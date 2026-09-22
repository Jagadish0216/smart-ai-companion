"""
Local LLM Engine — Ollama Backend

Connects to a locally running Ollama instance for real LLM inference.
Uses Ollama's /api/chat endpoint which automatically applies the correct
chat template for the loaded model (e.g., Llama 3.2's chat format).

When AI_ENGINE=local but Ollama is unavailable, this engine raises
EngineUnavailableError — it never produces fake AI output.

Architecture:
    Django (LocalLLMEngine)
        --HTTP--> Ollama server (localhost:11434)
            --> llama.cpp (quantized GGUF model)
                --> Response

Why Ollama:
    - HTTP API = zero Python ML dependencies in Django
    - Manages model downloads and memory automatically
    - Runs well on Raspberry Pi 5 (ARM64, 4/8GB)
    - Can be tested on any dev machine
    - Clean separation: Django handles web, Ollama handles inference
"""

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Optional

from .base import AIEngine, AIEngineResult, EngineUnavailableError

logger = logging.getLogger(__name__)

# System prompt establishing the companion persona
SYSTEM_PROMPT = (
    "You are a Smart AI Companion running on a Raspberry Pi 5 edge device. "
    "You assist users with IoT monitoring, edge automation, knowledge retrieval, "
    "and general questions. Keep responses concise, accurate, and helpful. "
    "You are running in offline mode with local inference. "
    "Do not mention being a large language model or AI assistant by OpenAI or any other company. "
    "You are the Smart AI Companion."
)


class LocalLLMEngine(AIEngine):
    """
    AI engine backed by a local Ollama instance.

    Configuration (environment variables):
        OLLAMA_HOST    — Ollama server URL (default: http://localhost:11434)
        OLLAMA_MODEL   — Model to use (default: llama3.2:1b)
        OLLAMA_TIMEOUT — Request timeout in seconds (default: 120)
    """

    def __init__(
        self,
        host: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        import os

        self._host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self._model = model or os.environ.get("OLLAMA_MODEL", "llama3.2:1b")
        self._timeout = timeout or int(os.environ.get("OLLAMA_TIMEOUT", "120"))

    @property
    def engine_name(self) -> str:
        return "local"

    def health_check(self) -> bool:
        """Check if the Ollama server is reachable."""
        try:
            req = urllib.request.Request(f"{self._host}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def generate(self, query: str, conversation_history: list | None = None) -> AIEngineResult:
        """
        Send a chat request to Ollama and return the structured result.

        Uses /api/chat which automatically applies the correct chat template
        for the loaded model (e.g., Llama 3.2 instruct format).
        """
        if not self.health_check():
            raise EngineUnavailableError(
                f"Cannot reach Ollama at {self._host}. "
                "Ensure Ollama is installed and running: https://ollama.com"
            )

        messages = self._build_messages(query, conversation_history)

        start = time.perf_counter()

        try:
            response_data = self._call_ollama_chat(messages)
        except EngineUnavailableError:
            raise
        except Exception as exc:
            logger.exception("Ollama inference failed")
            raise EngineUnavailableError(f"Inference failed: {exc}") from exc

        elapsed_ms = int((time.perf_counter() - start) * 1000)

        # Extract response text
        message = response_data.get("message", {})
        text = message.get("content", "").strip()

        if not text:
            raise EngineUnavailableError(
                "Ollama returned an empty response. The model may not be loaded correctly."
            )

        # Extract performance metadata from Ollama response
        eval_count = response_data.get("eval_count", 0)
        eval_duration_ns = response_data.get("eval_duration", 0)
        tokens_per_sec = 0.0
        if eval_duration_ns > 0 and eval_count > 0:
            tokens_per_sec = round(eval_count / (eval_duration_ns / 1e9), 1)

        return AIEngineResult(
            text=text,
            engine=self.engine_name,
            model=self._model,
            mode="offline",
            latency_ms=elapsed_ms,
        )

    # ── Internal helpers ───────────────────────────────────────

    def _build_messages(self, query: str, history: list | None) -> list[dict]:
        """
        Build the messages array for Ollama /api/chat.

        Format:
            [
                {"role": "system", "content": "..."},
                {"role": "user", "content": "..."},
                {"role": "assistant", "content": "..."},
                ...
                {"role": "user", "content": "<current query>"}
            ]

        Ollama applies the correct chat template for the model automatically.
        """
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        if history:
            for msg in history[-10:]:  # Last 10 messages for context window
                role = "user" if msg.get("role") == "USER" else "assistant"
                content = msg.get("content", "")
                if content:
                    messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": query})

        return messages

    def _call_ollama_chat(self, messages: list[dict]) -> dict:
        """
        Make a synchronous HTTP request to Ollama's /api/chat endpoint.

        Uses Python stdlib urllib — no third-party HTTP library needed.

        Returns the full JSON response dict containing:
            - message.content: the response text
            - eval_count: number of tokens generated
            - eval_duration: time spent generating (nanoseconds)
            - total_duration: total request time (nanoseconds)
        """
        url = f"{self._host}/api/chat"
        payload = json.dumps({
            "model": self._model,
            "messages": messages,
            "stream": False,
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body
        except urllib.error.HTTPError as exc:
            # Parse Ollama error responses
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="replace")
                error_data = json.loads(error_body)
                error_msg = error_data.get("error", error_body)
            except (json.JSONDecodeError, Exception):
                error_msg = error_body or str(exc)

            if exc.code == 404 or "not found" in error_msg.lower():
                raise EngineUnavailableError(
                    f"Model '{self._model}' not found in Ollama. "
                    f"Pull it with: ollama pull {self._model}"
                ) from exc

            raise EngineUnavailableError(
                f"Ollama error (HTTP {exc.code}): {error_msg}"
            ) from exc
        except urllib.error.URLError as exc:
            raise EngineUnavailableError(
                f"Could not connect to Ollama at {self._host}: {exc.reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise EngineUnavailableError(
                f"Invalid JSON response from Ollama: {exc}"
            ) from exc

    # Legacy method kept for backwards compatibility with existing tests
    def _build_prompt(self, query: str, history: list | None) -> str:
        """Build a plain-text prompt. Kept for test compatibility."""
        parts = []
        if history:
            for msg in history[-10:]:
                role = "User" if msg.get("role") == "USER" else "Assistant"
                parts.append(f"{role}: {msg.get('content', '')}")
        parts.append(f"User: {query}")
        parts.append("Assistant:")
        return "\n".join(parts)
