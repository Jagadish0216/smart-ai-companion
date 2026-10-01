"""Lightweight SearXNG retriever using only the Python standard library."""

from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
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


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
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
        if self.timeout_seconds <= 0:
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
                timeout=self.timeout_seconds,
            ) as response:
                payload_bytes = response.read(_MAX_RESPONSE_BYTES + 1)
        except error.HTTPError as exc:
            raise OnlineRetrievalProviderError(
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
            raise OnlineRetrievalProviderError(
                "The online provider could not be reached."
            ) from exc
        except OSError as exc:
            raise OnlineRetrievalProviderError(
                "The online provider connection failed."
            ) from exc

        if len(payload_bytes) > _MAX_RESPONSE_BYTES:
            raise OnlineRetrievalResponseError(
                "The online provider response was unexpectedly large."
            )

        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
            raw_results = payload["results"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise OnlineRetrievalResponseError(
                "The online provider returned a malformed response."
            ) from exc
        if not isinstance(raw_results, list):
            raise OnlineRetrievalResponseError(
                "The online provider returned a malformed result list."
            )

        retrieved_at = datetime.now(timezone.utc)
        results: list[OnlineRetrievalResult] = []
        seen_urls: set[str] = set()
        for raw_result in raw_results:
            if not isinstance(raw_result, dict):
                continue
            title = _plain_text(raw_result.get("title"))
            content = _plain_text(
                raw_result.get("content") or raw_result.get("snippet")
            )
            url = _safe_public_url(raw_result.get("url"))
            if not title or not content or not url or url in seen_urls:
                continue
            seen_urls.add(url)
            results.append(OnlineRetrievalResult(
                title=title,
                url=url,
                content=content,
                provider=self.provider_name,
                retrieved_at=retrieved_at,
            ))
            if len(results) >= self.max_results:
                break
        return results


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
    return " ".join(html.unescape(" ".join(extractor.parts)).split())


def _safe_public_url(value) -> str:
    if not isinstance(value, str):
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
        return parse.urlunsplit((
            parsed.scheme.lower(),
            parsed.netloc,
            parsed.path,
            parsed.query,
            "",
        ))
    except (ValueError, UnicodeError):
        return ""
