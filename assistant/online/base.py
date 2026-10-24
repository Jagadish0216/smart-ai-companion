"""Core abstractions and safe error types for online retrieval."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class OnlineRetrievalResult:
    """One small online result suitable for grounding the local LLM."""

    title: str
    url: str
    content: str
    provider: str
    retrieved_at: datetime
    published_at: datetime | None = None
    modified_at: datetime | None = None
    publication_source: str = ""
    date_conflict: bool = False
    accessibility: str = "unchecked"

    def source_metadata(self) -> dict:
        """Return public-safe attribution data without copied web content."""
        return {
            "title": self.title,
            "url": self.url,
            "provider": self.provider,
            "retrieved_at": self.retrieved_at.isoformat(),
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "modified_at": self.modified_at.isoformat() if self.modified_at else None,
            "publication_source": self.publication_source,
            "accessibility": self.accessibility,
        }


class OnlineRetriever(ABC):
    """Replaceable interface implemented by online search providers."""

    @abstractmethod
    def retrieve(self, query: str) -> list[OnlineRetrievalResult]:
        """Return a bounded list of current online search-result snippets."""
        ...


class OnlineRetrievalError(Exception):
    """Base class for expected online retrieval failures."""


class OnlineRetrievalConfigurationError(OnlineRetrievalError):
    """The selected provider is disabled or not fully configured."""


class OnlineRetrievalTimeoutError(OnlineRetrievalError):
    """The provider did not respond before the configured timeout."""


class OnlineRetrievalAuthenticationError(OnlineRetrievalError):
    """The provider rejected its configured credentials."""


class OnlineRetrievalProviderError(OnlineRetrievalError):
    """The provider could not complete the request."""


class OnlineRetrievalResponseError(OnlineRetrievalError):
    """The provider returned an unusable or malformed response."""
