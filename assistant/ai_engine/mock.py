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

    def generate(self, query: str, conversation_history: list | None = None) -> AIEngineResult:
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
                "All systems are currently online. CPU is at nominal levels, "
                "and the knowledge base is synchronized."
            )
        if any(kw in q for kw in ("knowledge", "document")):
            return (
                "I can access your uploaded documents. I've found 2 relevant "
                "snippets regarding edge deployment."
            )
        if any(kw in q for kw in ("hello", "hi", "hey")):
            return (
                "Hello! I am your Smart AI Companion. How can I assist you "
                "with your IoT or edge automation tasks today?"
            )
        if any(kw in q for kw in ("capability", "can you", "what do you")):
            return (
                "I can help with system monitoring, knowledge retrieval from "
                "uploaded documents, and general Q&A — all processed locally "
                "on the edge device."
            )
        if any(kw in q for kw in ("weather", "news", "internet")):
            return (
                "I'm currently running in offline mode. Online retrieval is "
                "planned for a future update."
            )

        return (
            f"I processed your query locally: '{query}'. "
            "This is a simulated response from the mock AI engine."
        )
