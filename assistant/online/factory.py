"""Configuration-driven online retriever factory and availability check."""

from django.conf import settings

from .base import OnlineRetrievalConfigurationError, OnlineRetriever
from .searxng import SearXNGOnlineRetriever, normalize_searxng_base_url


def get_online_retriever() -> OnlineRetriever:
    if not getattr(settings, "ONLINE_RETRIEVAL_ENABLED", False):
        raise OnlineRetrievalConfigurationError("Online retrieval is disabled.")

    provider = str(getattr(settings, "ONLINE_PROVIDER", "searxng")).strip().lower()
    if provider == "searxng":
        return SearXNGOnlineRetriever(
            base_url=getattr(
                settings,
                "SEARXNG_BASE_URL",
                "http://127.0.0.1:8888",
            ),
            timeout_seconds=getattr(settings, "ONLINE_TIMEOUT_SECONDS", 8),
            max_results=getattr(settings, "ONLINE_MAX_RESULTS", 4),
        )
    raise OnlineRetrievalConfigurationError(
        f"Unsupported online retrieval provider: {provider or 'none'}."
    )


def is_online_retrieval_available() -> bool:
    """Conservatively report whether the configured pipeline is usable."""
    if not getattr(settings, "ONLINE_RETRIEVAL_ENABLED", False):
        return False
    if getattr(settings, "AI_ENGINE", "mock") != "local":
        return False
    provider = str(getattr(settings, "ONLINE_PROVIDER", "searxng")).strip().lower()
    if provider != "searxng":
        return False
    try:
        normalize_searxng_base_url(getattr(
            settings,
            "SEARXNG_BASE_URL",
            "http://127.0.0.1:8888",
        ))
        timeout = float(getattr(settings, "ONLINE_TIMEOUT_SECONDS", 8))
        max_results = int(getattr(settings, "ONLINE_MAX_RESULTS", 4))
        max_context = int(getattr(settings, "ONLINE_MAX_CONTEXT_CHARS", 6000))
    except (OnlineRetrievalConfigurationError, TypeError, ValueError):
        return False
    return timeout > 0 and 1 <= max_results <= 20 and max_context >= 500
