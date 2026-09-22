"""
AI Engine — Core Abstractions

Defines the contract for all AI inference backends (mock, local LLM, etc.)
and the structured result type returned by every engine.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AIEngineResult:
    """
    Structured result returned by every AI engine implementation.

    This is a pure Python dataclass — no Django dependency.
    The AssistantService translates this into API responses and DB records.
    """
    text: str
    engine: str                         # e.g. "mock", "local", "online"
    model: Optional[str] = None         # e.g. "tinyllama:1.1b", None for mock
    mode: str = "offline"               # "offline", "online", "hybrid"
    latency_ms: int = 0                 # wall-clock processing time
    sources: list = field(default_factory=list)  # RAG source metadata (future)
    error: Optional[str] = None         # non-None means partial/failed response

    @property
    def success(self) -> bool:
        return self.error is None


class AIEngine(ABC):
    """
    Abstract base class for AI inference backends.

    Every backend must implement generate().
    The rest of Django interacts with engines only through this interface.
    """

    @abstractmethod
    def generate(self, query: str, conversation_history: list | None = None) -> AIEngineResult:
        """
        Generate a response for the given query.

        Args:
            query: The user's input text.
            conversation_history: Optional list of prior messages as dicts
                                  [{"role": "USER"|"AI", "content": "..."}]
                                  for context-aware responses.

        Returns:
            AIEngineResult with the response text and metadata.
            On failure, the result should have error set and text as a
            user-friendly fallback message.
        """
        ...

    @property
    @abstractmethod
    def engine_name(self) -> str:
        """Short identifier for this engine (e.g. 'mock', 'local')."""
        ...

    def health_check(self) -> bool:
        """
        Returns True if the engine is ready to accept queries.
        Override in backends that depend on external services.
        """
        return True


class EngineUnavailableError(Exception):
    """Raised when an engine cannot be initialized or is unreachable."""
    pass
