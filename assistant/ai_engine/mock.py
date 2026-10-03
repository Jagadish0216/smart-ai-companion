"""
Mock AI Engine

Provides rule-based responses for development and testing.
This is the default engine when no local LLM is configured.
"""

import time

from .base import AIEngine, AIEngineResult


class MockAIEngine(AIEngine):
    """
    Rule-based mock engine for development without a real LLM.

    Matches keywords in the query and returns canned responses.
    Simulates a small processing delay to mimic real inference latency.
    """

    SIMULATED_DELAY_S = 0.3  # Reduced from 1.5s — fast enough for dev, still visible

    @property
    def engine_name(self) -> str:
        return "mock"

    def generate(
        self,
        query: str,
        conversation_history: list | None = None,
        system_instruction: str | None = None,
        num_predict: int | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AIEngineResult:
        start = time.perf_counter()

        # Simulate processing
        time.sleep(self.SIMULATED_DELAY_S)

        text = self._match_response(query)

        elapsed_ms = int((time.perf_counter() - start) * 1000)

        return AIEngineResult(
            text=text,
            engine=self.engine_name,
            model=None,
            mode="offline",
            latency_ms=elapsed_ms,
        )

    def _match_response(self, query: str) -> str:
        """Simple keyword matching for mock responses."""
        q = query.lower()

        if any(kw in q for kw in ("status", "health")):
            return (
                "I can respond, but I don't have live device, sensor, or "
                "knowledge-system status available."
            )
        if any(kw in q for kw in ("knowledge", "document")):
            return (
                "Local document retrieval isn't available yet, so I can't "
                "search uploaded documents or claim to have found results."
            )
        if any(kw in q for kw in ("hello", "hi", "hey")):
            return (
                "Hello! I am your Smart AI Companion. How can I help you today?"
            )
        if any(kw in q for kw in ("capability", "can you", "what do you")):
            return (
                "I can answer general questions and maintain our conversation. "
                "I can't yet search documents, retrieve live information, read "
                "sensors, or control devices."
            )
        if any(kw in q for kw in ("weather", "news", "internet")):
            return (
                "I can't retrieve live weather or news yet because online "
                "retrieval isn't available."
            )

        return (
            f"I understood your request: '{query}'. Please provide any additional "
            "context needed for a more specific answer."
        )
