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
    return set(re.findall(r"[a-z0-9]+", query.lower())) - stop


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
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise OnlineRetrievalConfigurationError(
                "Online retrieval timeout must be greater than zero."
            )
        if not 1 <= self.max_results <= 20:
            raise OnlineRetrievalConfigurationError(
                "Online maximum results must be between 1 and 20."
            )

    def retrieve(self, query: str) -> list[OnlineRetrievalResult]:
        bounded_query = _bounded_query(query)
        if not bounded_query:
            return []

        key = (self.base_url, self.max_results, hashlib.sha256(bounded_query.casefold().encode()).digest())
        now = time.monotonic()
        with _LOCK:
            health = _HEALTH.get(self.base_url)
            if health and health[0] > now and not health[1]:
                raise OnlineRetrievalProviderError("Online search is temporarily unavailable.")
            cached = _CACHE.get(key)
            if cached and cached[0] > now:
                return list(cached[1])
        if not _SLOTS.acquire(blocking=False):
            raise OnlineRetrievalProviderError("Online search is busy.")
        deadline = now + min(self.timeout_seconds, 8.0)
        try:
            results = []
            # Two search attempts total, shared deadline. No retries for auth,
            # malformed JSON or portal redirects. A second empty-query attempt
            # only removes conversational filler, never adds invented entities.
            candidate = bounded_query
            for attempt in range(2):
                try:
                    results = self._search(candidate, deadline)
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
                    probe = self._search("IANA example domains", deadline)
                    if not any(parse.urlsplit(r.url).hostname in {"iana.org", "www.iana.org"} and "example" in r.content.lower() for r in probe):
                        raise OnlineRetrievalProviderError("External search health could not be verified.")
            with _LOCK:
                _remember(_HEALTH, self.base_url, (time.monotonic() + 30, True), 8)
                _remember(_CACHE, key, (time.monotonic() + (freshness_seconds(query) if results else 5), tuple(results)))
            return results
        except (OnlineRetrievalProviderError, OnlineRetrievalTimeoutError, OnlineRetrievalResponseError):
            with _LOCK:
                _remember(_HEALTH, self.base_url, (time.monotonic() + 10, False), 8)
            raise
        finally:
            _SLOTS.release()

    def _search(self, bounded_query, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OnlineRetrievalTimeoutError("Online search budget exhausted.")

        query_string = parse.urlencode({
            "q": bounded_query,
            "format": "json",
        })
        search_request = request.Request(
            f"{self.base_url}/search?{query_string}",
            headers={
                "Accept": "application/json",
                "User-Agent": "smart-ai-companion/1.0",
            },
            method="GET",
        )

        try:
            with request.urlopen(
                search_request,
                timeout=min(3.0, remaining),
            ) as response:
                # urllib follows redirects by default. Reject portal/login pages
                # before parsing; never submit authentication or follow HTML.
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
            results.append(OnlineRetrievalResult(
                title=title[:180],
                url=url[:2048],
                content=content[:900],
                provider=self.provider_name,
                retrieved_at=retrieved_at,
                published_at=_published_time(raw_result.get("publishedDate") or raw_result.get("published_date")),
            ))
        terms = query_terms(bounded_query)
        # Rank a bounded candidate pool before applying the result limit, so a
        # generic landing-page hit cannot crowd out a relevant dated source.
        results.sort(key=lambda item: (
            len(terms & set(re.findall(r"[a-z0-9]+", (item.title + " " + item.content).lower()))),
            item.published_at.timestamp() if item.published_at else 0,
        ), reverse=True)
        return results[:self.max_results]


class _TransientProviderError(OnlineRetrievalProviderError):
    pass


def _published_time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


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
