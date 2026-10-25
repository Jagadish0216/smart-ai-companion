"""Lightweight SearXNG retriever using only the Python standard library."""

from __future__ import annotations

import html
import ipaddress
import json
import math
import re
import socket
import hashlib
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib import error, parse, request
from .dates import parse_publication_date, consistent_date

from .base import (
    OnlineRetrievalConfigurationError,
    OnlineRetrievalProviderError,
    OnlineRetrievalResponseError,
    OnlineRetrievalResult,
    OnlineRetrievalTimeoutError,
    OnlineRetriever,
)


_MAX_RESPONSE_BYTES = 1_000_000
_MAX_QUERY_CHARS = 600
_MAX_QUERY_WORDS = 75
_HOST_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.I)
_CACHE = OrderedDict()
_HEALTH = OrderedDict()
_LOCK = threading.Lock()
_SLOTS = threading.BoundedSemaphore(2)


class _NoProviderRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_search(search_request, *, timeout):
    # A configured JSON endpoint must respond directly. Do not let urllib
    # multiply per-attempt timeouts by following portal/provider redirects.
    return request.build_opener(_NoProviderRedirect()).open(search_request, timeout=timeout)


def clear_online_cache():
    """Clear process-local, bounded search/health caches (also used by tests)."""
    with _LOCK:
        _CACHE.clear()
        _HEALTH.clear()


def _remember(cache, key, value, limit=64):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > limit:
        cache.popitem(last=False)


def freshness_seconds(query):
    if re.search(r"\b(price|prices|score|scores|status|traffic|stock|stocks|live)\b", query, re.I):
        return 15
    if re.search(r"\b(weather|forecast|news|today|now)\b", query, re.I):
        return 30
    return 120


def query_terms(query):
    stop = set("what whats s who is are the a an of in on at to for me tell please latest current currently today now recent version release news weather price score status and how much does it cost search browse look up web internet online this".split())
    stop.update('january february march april may june july august september october november december'.split())
    stop.update('actor actress movie movies film films working project projects upcoming next releases updates week month year'.split())
    return {term for term in re.findall(r"[a-z0-9]+", query.lower()) if not re.fullmatch(r'20\d{2}', term)} - stop


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "template"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


class SearXNGOnlineRetriever(OnlineRetriever):
    """Retrieve bounded snippets from a configured SearXNG Search API."""

    provider_name = "searxng"

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        max_results: int,
    ):
        self.base_url = normalize_searxng_base_url(base_url)
        self.timeout_seconds = float(timeout_seconds)
        self.max_results = int(max_results)
        self.diagnostics = {}
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise OnlineRetrievalConfigurationError(
                "Online retrieval timeout must be greater than zero."
            )
        if not 1 <= self.max_results <= 20:
            raise OnlineRetrievalConfigurationError(
                "Online maximum results must be between 1 and 20."
            )

    def retrieve(self, query: str) -> list[OnlineRetrievalResult]:
        self.diagnostics = {}
        bounded_query = _bounded_query(query)
        if not bounded_query:
            return []

        key = (self.base_url, self.max_results, hashlib.sha256(bounded_query.casefold().encode()).digest())
        now = time.monotonic()
        timings = {'search_ms': 0, 'enrichment_ms': 0, 'evidence_selection_ms': 0,
                   'cache_ms': 0, 'cache_hit': False}
        with _LOCK:
            health = _HEALTH.get(self.base_url)
            if health and health[0] > now and not health[1]:
                raise OnlineRetrievalProviderError("Online search is temporarily unavailable.")
            cached = _CACHE.get(key)
            if cached and cached[0] > now:
                self.diagnostics = dict(cached[2]) if len(cached) > 2 else {}
                self.diagnostics.update(timings, cache_hit=True, cache_ms=round((time.monotonic()-now)*1000))
                return list(cached[1])
        if not _SLOTS.acquire(blocking=False):
            raise OnlineRetrievalProviderError("Online search is busy.")
        deadline = now + min(self.timeout_seconds, 8.0)
        def search(candidate):
            started = time.monotonic()
            try:
                return self._search(candidate, deadline)
            finally:
                timings['search_ms'] += round((time.monotonic()-started)*1000)
        try:
            results = []
            # Two search attempts total, shared deadline. No retries for auth,
            # malformed JSON or portal redirects. A second empty-query attempt
            # only removes conversational filler, never adds invented entities.
            candidate = bounded_query
            for attempt in range(2):
                try:
                    results = search(candidate)
                except (OnlineRetrievalTimeoutError, _TransientProviderError):
                    if attempt or deadline - time.monotonic() < .1:
                        raise
                    continue
                if results:
                    break
                alternative = re.sub(r"^(?:please\s+)?(?:(?:can|could) you\s+)?(?:tell me\s+|what(?:'s| is| are)\s+)", "", candidate, flags=re.I).strip(" ?.")
                if alternative == candidate or not alternative:
                    break
                candidate = alternative
            if not results:
                with _LOCK:
                    health = _HEALTH.get(self.base_url)
                if not health or health[0] <= time.monotonic() or not health[1]:
                    # A running local SearXNG with no working external engines
                    # is NOT an online success. Validate real search, not TCP.
                    probe = search("IANA example domains")
                    if not any(parse.urlsplit(r.url).hostname in {"iana.org", "www.iana.org"} and "example" in r.content.lower() for r in probe):
                        raise OnlineRetrievalProviderError("External search health could not be verified.")
            from .enrichment import enrich_results
            from .evidence_policy import evidence_category, official_catalog
            retrieved_count = len(results)
            from .current_sources import current_topic
            topic = current_topic(query)
            official_url = (
                'https://www.telangana.gov.in/government/chief-minister/'
                if topic == 'officeholder' and re.search(r'\btelangana\b', query, re.I) else
                'https://www.pmindia.gov.in/en/pms-profile/'
                if topic == 'officeholder' and re.search(r'\bindia\b', query, re.I) else ''
            )
            if official_url:
                # A known roster URL is a candidate, never a source until fetched.
                candidate_page = OnlineRetrievalResult('Official officeholder profile', official_url,
                    'Official officeholder profile; current role requires page verification.', self.provider_name, datetime.now(timezone.utc))
                results = [candidate_page] + [r for r in results if r.url.rstrip('/') != official_url.rstrip('/')][:19]
            if results and evidence_category(query)=='release' and re.search(r'\braspberry\s+pi\s+os\b',query,re.I):
                catalog=next((r for r in results if official_catalog(r.url)),None)
                if catalog is None:
                    catalog=OnlineRetrievalResult('Official Raspberry Pi OS catalog','https://www.raspberrypi.com/software/operating-systems/',
                        'Official Raspberry Pi OS download catalog. Release details require page verification.', 'official_catalog',datetime.now(timezone.utc))
                results=[catalog]+[r for r in results if not official_catalog(r.url)][:19]
            # Four fetch attempts and discovery share this two-second page budget.
            enrichment_deadline = min(deadline, time.monotonic() + 2.0)
            attempt_budget = [4]
            enrichment_started = time.monotonic()
            results = enrich_results(results, query, enrichment_deadline, min(4, self.max_results), attempt_budget)
            timings['enrichment_ms'] += round((time.monotonic()-enrichment_started)*1000)
            from .selection import rank_candidates, diverse_selection
            from .grounding import select_current_evidence
            fresh = select_current_evidence(results, query, datetime.now(timezone.utc))
            category = evidence_category(query)
            if retrieved_count and not fresh and category in {'current', 'release', 'officeholder', 'entertainment'} and deadline-time.monotonic() > .15:
                # One additional discovery call only, sharing the original 8s
                # deadline. Never retry a denied page or bypass its restriction.
                alternative = (
                    bounded_query + ' release notes site:raspberrypi.com/news/'
                    if category == 'release' and re.search(r'\braspberry\s+pi\s+os\b', query, re.I)
                    else re.sub(r'\bnews\b', 'headlines', bounded_query, flags=re.I)
                    if re.search(r'\bnews\b', bounded_query, re.I)
                    else bounded_query + ' release notes' if category == 'release'
                    else bounded_query + ' site:telangana.gov.in/government/chief-minister' if category == 'officeholder' and re.search(r'\btelangana\b', query, re.I)
                    else bounded_query + ' site:pmindia.gov.in/en/pms-profile' if category == 'officeholder' and re.search(r'\bindia\b', query, re.I)
                    else bounded_query + ' official announcement' if category == 'entertainment' else ''
                )
                if alternative and alternative != candidate:
                    try:
                        discovered = search(alternative)
                        retrieved_count += len(discovered)
                        known = {r.url for r in results}
                        discovered = [r for r in discovered if r.url not in known]
                        enrichment_started = time.monotonic()
                        results += enrich_results(discovered, query, enrichment_deadline, min(4, self.max_results), attempt_budget)
                        timings['enrichment_ms'] += round((time.monotonic()-enrichment_started)*1000)
                    except (OnlineRetrievalProviderError, OnlineRetrievalTimeoutError, OnlineRetrievalResponseError):
                        pass  # Preserve the original evidence and honest abstention.
            verified_count = sum(r.accessibility == 'available' for r in results)
            unavailable_count = sum(r.accessibility == 'unavailable' for r in results)
            denied_count = sum(r.http_status in (401, 403) for r in results)
            selection_started = time.monotonic()
            results = rank_candidates(results, query)
            fresh = select_current_evidence(results, query, datetime.now(timezone.utc))
            self.diagnostics = {
                'provider_reachable': True, 'results_retrieved': retrieved_count,
                'sources_verified': verified_count,
                'sources_unavailable': unavailable_count,
                'sources_access_denied': denied_count,
                'fresh_evidence_accepted': len(fresh),
                **timings,
            }
            remainder = [item for item in results if item not in fresh and item.accessibility != 'unavailable']
            selected = diverse_selection(fresh, self.max_results)
            results = selected + diverse_selection(remainder, self.max_results - len(selected)) if len(selected) < self.max_results else selected
            self.diagnostics['evidence_selection_ms'] += round((time.monotonic()-selection_started)*1000)
            with _LOCK:
                _remember(_HEALTH, self.base_url, (time.monotonic() + 30, True), 8)
                _remember(_CACHE, key, (time.monotonic() + (freshness_seconds(query) if results else 5), tuple(results), dict(self.diagnostics)))
            return results
        except (OnlineRetrievalProviderError, OnlineRetrievalTimeoutError, OnlineRetrievalResponseError):
            self.diagnostics = dict(timings)
            with _LOCK:
                _remember(_HEALTH, self.base_url, (time.monotonic() + 10, False), 8)
            raise
        finally:
            _SLOTS.release()

    def _search(self, bounded_query, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OnlineRetrievalTimeoutError("Online search budget exhausted.")

        parameters = {
            "q": bounded_query,
            "format": "json",
        }
        if re.search(r'\b(news|headlines?)\b', bounded_query, re.I):
            parameters.update(categories='news', time_range='day')
        query_string = parse.urlencode(parameters)
        search_request = request.Request(
            f"{self.base_url}/search?{query_string}",
            headers={
                "Accept": "application/json",
                "User-Agent": "smart-ai-companion/1.0",
            },
            method="GET",
        )

        try:
            with _open_search(
                search_request,
                timeout=min(3.0, remaining),
            ) as response:
                # Defense in depth for alternate transports/test doubles.
                geturl = getattr(response, "geturl", None)
                if callable(geturl) and parse.urlsplit(geturl())[:2] != parse.urlsplit(search_request.full_url)[:2]:
                    raise OnlineRetrievalResponseError("Online provider redirected to another origin.")
                payload_bytes = _read_bounded(response, deadline)
                if time.monotonic() > deadline:
                    raise OnlineRetrievalTimeoutError("Online search budget exhausted.")
        except error.HTTPError as exc:
            failure = _TransientProviderError if exc.code in (502, 503, 504) else OnlineRetrievalProviderError
            raise failure(
                f"The online provider returned HTTP status {exc.code}."
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise OnlineRetrievalTimeoutError(
                "The online provider request timed out."
            ) from exc
        except error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise OnlineRetrievalTimeoutError(
                    "The online provider request timed out."
                ) from exc
            raise _TransientProviderError(
                "The online provider could not be reached."
            ) from exc
        except OSError as exc:
            raise _TransientProviderError(
                "The online provider connection failed."
            ) from exc

        if len(payload_bytes) > _MAX_RESPONSE_BYTES:
            raise OnlineRetrievalResponseError(
                "The online provider response was unexpectedly large."
            )

        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
            raw_results = payload["results"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, RecursionError) as exc:
            raise OnlineRetrievalResponseError(
                "The online provider returned a malformed response."
            ) from exc
        if not isinstance(raw_results, list):
            raise OnlineRetrievalResponseError(
                "The online provider returned a malformed result list."
            )
        if not raw_results and payload.get("unresponsive_engines"):
            raise _TransientProviderError("External search engines are unavailable.")

        retrieved_at = datetime.now(timezone.utc)
        results: list[OnlineRetrievalResult] = []
        seen_urls: set[str] = set()
        seen_content: set[str] = set()
        for raw_result in raw_results[:100]:
            if not isinstance(raw_result, dict):
                continue
            title = _plain_text(raw_result.get("title"))
            content = _plain_text(
                raw_result.get("content") or raw_result.get("snippet")
            )
            url = _safe_public_url(raw_result.get("url"))
            identity = re.sub(r"\W+", "", content.casefold())
            if not title or len(content) < 12 or not url or url in seen_urls or identity in seen_content:
                continue
            seen_urls.add(url)
            seen_content.add(identity)
            date_values = [raw_result.get(key) for key in ('publishedDate', 'pubdate', 'published_date', 'datePublished', 'date_of_publication')]
            parsed_dates = [parse_publication_date(value) for value in date_values]
            publication = consistent_date(date_values)
            conflict = len({d.date() for d in parsed_dates if d}) > 1
            results.append(OnlineRetrievalResult(
                title=title[:180],
                url=url[:2048],
                content=content[:900],
                provider=self.provider_name,
                retrieved_at=retrieved_at,
                published_at=publication,
                publication_source='searxng' if publication else '',
                date_conflict=conflict,
                modified_at=consistent_date([raw_result.get('dateModified'), raw_result.get('modifiedDate')]),
            ))
        from .selection import rank_candidates
        return rank_candidates(results, bounded_query)


class _TransientProviderError(OnlineRetrievalProviderError):
    pass


def _published_time(value):
    return parse_publication_date(value)


def _read_bounded(response, deadline):
    chunks = []
    size = 0
    read = getattr(response, "read1", None)
    if not callable(read):
        return response.read(_MAX_RESPONSE_BYTES + 1)
    while size <= _MAX_RESPONSE_BYTES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OnlineRetrievalTimeoutError("Online search budget exhausted.")
        # HTTPResponse exposes the underlying socket through its buffered reader.
        # Reset the read timeout so a slow-drip response cannot renew the budget.
        sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        if sock is not None:
            sock.settimeout(min(3.0, remaining))
        chunk = read(min(16384, _MAX_RESPONSE_BYTES + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def normalize_searxng_base_url(value: str) -> str:
    """Validate and normalize an HTTP(S) SearXNG instance base URL."""
    if not isinstance(value, str):
        raise OnlineRetrievalConfigurationError(
            "SearXNG base URL must be an HTTP or HTTPS URL."
        )
    raw_url = value.strip()
    if not raw_url or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in raw_url
    ):
        raise OnlineRetrievalConfigurationError(
            "SearXNG base URL must be an HTTP or HTTPS URL."
        )
    try:
        parsed = parse.urlsplit(raw_url)
        if (
            parsed.scheme.lower() not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        port = parsed.port  # Validate that an optional port is numeric and in range.
        hostname = _normalize_hostname(parsed.hostname)
    except (ValueError, UnicodeError) as exc:
        raise OnlineRetrievalConfigurationError(
            "SearXNG base URL must be a valid HTTP or HTTPS URL without credentials."
        ) from exc

    normalized_path = parsed.path.rstrip("/")
    normalized_netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        normalized_netloc = f"{normalized_netloc}:{port}"
    return parse.urlunsplit((
        parsed.scheme.lower(),
        normalized_netloc,
        normalized_path,
        "",
        "",
    ))


def _normalize_hostname(hostname: str) -> str:
    try:
        return ipaddress.ip_address(hostname).compressed
    except ValueError:
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError from exc
        labels = ascii_hostname.split(".")
        if (
            len(ascii_hostname) > 253
            or all(label.isdigit() for label in labels)
            or any(not _HOST_LABEL_PATTERN.fullmatch(label) for label in labels)
        ):
            raise ValueError
        return ascii_hostname


def _bounded_query(query: str) -> str:
    words = " ".join((query or "").split()).split()[:_MAX_QUERY_WORDS]
    bounded = " ".join(words)
    if len(bounded) <= _MAX_QUERY_CHARS:
        return bounded
    shortened = bounded[:_MAX_QUERY_CHARS].rsplit(" ", 1)[0]
    return shortened or bounded[:_MAX_QUERY_CHARS]


def _plain_text(value) -> str:
    if not isinstance(value, str):
        return ""
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(value)
        extractor.close()
    except Exception:
        return ""
    text = html.unescape(" ".join(extractor.parts))
    text = re.sub(r"[\x00-\x1f\x7f]|---\s*SOURCE", " ", text, flags=re.I)
    return " ".join(text.split())


def _safe_public_url(value) -> str:
    if not isinstance(value, str):
        return ""
    if len(value) > 2048 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        return ""
    try:
        parsed = parse.urlsplit(value.strip())
        if (
            parsed.scheme.lower() not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return ""
        parsed.port
        host = _normalize_hostname(parsed.hostname)
        if host == "localhost" or host.endswith((".localhost", ".local")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            pass
        return parse.urlunsplit((
            parsed.scheme.lower(),
            parsed.netloc,
            parsed.path,
            parsed.query,
            "",
        ))
    except (ValueError, UnicodeError):
        return ""
