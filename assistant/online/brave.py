"""Lightweight Brave Search API retriever using only the standard library."""

from __future__ import annotations

import html
import json
import socket
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib import error, parse, request

from .base import (
    OnlineRetrievalAuthenticationError,
    OnlineRetrievalConfigurationError,
    OnlineRetrievalProviderError,
    OnlineRetrievalResponseError,
    OnlineRetrievalResult,
    OnlineRetrievalTimeoutError,
    OnlineRetriever,
)


BRAVE_WEB_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_QUERY_CHARS = 600
_MAX_QUERY_WORDS = 75


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


class BraveSearchRetriever(OnlineRetriever):
    """Retrieve small web-result snippets without downloading result pages."""

    provider_name = "brave"

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float,
        max_results: int,
        endpoint: str = BRAVE_WEB_SEARCH_ENDPOINT,
    ):
        self.api_key = (api_key or "").strip()
        self.timeout_seconds = float(timeout_seconds)
        self.max_results = int(max_results)
        self.endpoint = endpoint
        if not self.api_key:
            raise OnlineRetrievalConfigurationError(
                "Brave Search credentials are not configured."
            )
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
            "count": self.max_results,
            "search_lang": "en",
        })
        search_request = request.Request(
            f"{self.endpoint}?{query_string}",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
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
            if exc.code in (401, 403):
                raise OnlineRetrievalAuthenticationError(
                    "The online provider rejected its credentials."
                ) from exc
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
            raw_results = payload["web"]["results"]
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
                raw_result.get("description") or raw_result.get("snippet")
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
        return parse.urlunsplit((
            parsed.scheme.lower(),
            parsed.netloc,
            parsed.path,
            parsed.query,
            "",
        ))
    except (ValueError, UnicodeError):
        return ""
