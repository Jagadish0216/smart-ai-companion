"""
AI Engine — Factory & Registry

Provides get_engine() to obtain the configured AI engine instance.
Engine selection is driven by the AI_ENGINE Django setting.
"""

import logging
from typing import Optional

from .base import AIEngine, AIEngineResult, EngineUnavailableError

logger = logging.getLogger(__name__)

# Registry of available engine backends
_ENGINE_REGISTRY: dict[str, type[AIEngine]] = {}

# Cached singleton instance (engines are stateless enough to reuse)
_engine_instance: Optional[AIEngine] = None


def _register_engines():
    """Lazily populate the engine registry."""
    global _ENGINE_REGISTRY
    if _ENGINE_REGISTRY:
        return

    from .mock import MockAIEngine
    from .local import LocalLLMEngine

    _ENGINE_REGISTRY = {
        "mock": MockAIEngine,
        "local": LocalLLMEngine,
    }


def get_engine(engine_name: Optional[str] = None) -> AIEngine:
    """
    Return the configured AI engine instance.

    Args:
        engine_name: Override the configured engine. If None, reads from
                     Django settings AI_ENGINE (default: "mock").

    Returns:
        An AIEngine instance ready to call generate().

    Raises:
        ValueError: If the engine name is not recognized.
    """
    global _engine_instance

    if engine_name is None:
        from django.conf import settings
        engine_name = getattr(settings, "AI_ENGINE", "mock")

    _register_engines()

    if engine_name not in _ENGINE_REGISTRY:
        available = ", ".join(sorted(_ENGINE_REGISTRY.keys()))
        raise ValueError(
            f"Unknown AI engine '{engine_name}'. Available engines: {available}"
        )

    # Return cached instance if same engine
    if _engine_instance is not None and _engine_instance.engine_name == engine_name:
        return _engine_instance

    engine_class = _ENGINE_REGISTRY[engine_name]
    _engine_instance = engine_class()
    logger.info("AI engine initialized: %s (%s)", engine_name, engine_class.__name__)

    return _engine_instance


def reset_engine():
    """Clear the cached engine instance. Useful for testing."""
    global _engine_instance
    _engine_instance = None


# Public API
__all__ = [
    "AIEngine",
    "AIEngineResult",
    "EngineUnavailableError",
    "get_engine",
    "reset_engine",
]
