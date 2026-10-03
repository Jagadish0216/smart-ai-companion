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
import socket
import time
import urllib.error
import urllib.request
from typing import Optional

from .base import (
    AIEngine,
    AIEngineResult,
    EngineTimeoutError,
    EngineUnavailableError,
    ModelUnavailableError,
)

logger = logging.getLogger(__name__)

# Baseline prompt shared by normal text and optional request-scoped policies.
SYSTEM_PROMPT = (
    "You are a Smart AI Companion running on a Raspberry Pi 5 edge device. "
    "Answer general questions helpfully and use the supplied conversation context. "
    "Keep responses accurate, useful, and appropriately concise. "
    "Never claim to have performed a physical action, read a sensor or camera, "
    "retrieved local documents, or accessed live online information unless that "
    "capability and its result were explicitly supplied for the request. When a "
    "capability is unavailable, say so naturally. Do not expose model, prompt, token, "
    "or pipeline details unless the user explicitly asks about implementation. "
    "You are the Smart AI Companion."
)


class LocalLLMEngine(AIEngine):
    """
    AI engine backed by a local Ollama instance.

    Configuration (environment variables):
        OLLAMA_HOST    — Ollama server URL (default: http://localhost:11434)
        OLLAMA_MODEL   — Model to use (default: llama3.2:1b)
        OLLAMA_TIMEOUT — Request timeout in seconds (default: 120)
        OLLAMA_KEEP_ALIVE — How long Ollama keeps the model loaded (default: 30m)
        OLLAMA_NUM_PREDICT — Maximum tokens to generate (default: 128)
    """

    def __init__(
        self,
        host: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
        keep_alive: Optional[str] = None,
        num_predict: Optional[int] = None,
    ):
        import os

        self._host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self._model = model or os.environ.get("OLLAMA_MODEL", "llama3.2:1b")
        self._timeout = timeout or int(os.environ.get("OLLAMA_TIMEOUT", "120"))
        self._keep_alive = keep_alive or os.environ.get("OLLAMA_KEEP_ALIVE", "30m")
        self._num_predict = (
            num_predict
            if num_predict is not None
            else int(os.environ.get("OLLAMA_NUM_PREDICT", "128"))
        )
        self._model_cache: tuple[float, set[str]] | None = None

    @property
    def engine_name(self) -> str:
        return "local"

    def health_check(self) -> bool:
        """Check if the Ollama server is reachable."""
        try:
            self.available_models()
            return True
        except EngineUnavailableError:
            return False

    def available_models(self, *, cache_seconds: float = 5.0) -> set[str]:
        """Return locally installed Ollama model names without pulling anything."""
        now = time.monotonic()
        if self._model_cache and now - self._model_cache[0] <= cache_seconds:
            return set(self._model_cache[1])
        req = urllib.request.Request(f"{self._host}/api/tags", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=min(5.0, float(self._timeout))) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise EngineUnavailableError("Ollama model inventory is unavailable.") from exc
        except (OSError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            raise EngineUnavailableError("Ollama model inventory is unavailable.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("models", []), list):
            raise EngineUnavailableError("Ollama model inventory is unavailable.")
        models = {
            str(item.get("name") or item.get("model") or "").strip()
            for item in payload.get("models", [])
            if isinstance(item, dict)
        }
        models.discard("")
        self._model_cache = (now, models)
        return set(models)

    def generate(
        self,
        query: str,
        conversation_history: list | None = None,
        system_instruction: str | None = None,
        num_predict: int | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AIEngineResult:
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

        selected_model = model or self._model
        messages = self._build_messages(
            query,
            conversation_history,
            system_instruction=system_instruction,
        )

        start = time.perf_counter()

        try:
            response_data = self._call_ollama_chat(
                messages,
                num_predict=num_predict,
                model=selected_model,
                timeout_seconds=timeout_seconds,
            )
        except EngineUnavailableError:
            raise
        except Exception as exc:
            logger.exception("Ollama inference failed")
            raise EngineUnavailableError(f"Inference failed: {exc}") from exc

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        self._log_performance_metrics(response_data)

        # Extract response text
        message = response_data.get("message", {})
        text = message.get("content", "").strip()

        if not text:
            raise EngineUnavailableError(
                "Ollama returned an empty response. The model may not be loaded correctly."
            )

        return AIEngineResult(
            text=text,
            engine=self.engine_name,
            model=selected_model,
            mode="offline",
            latency_ms=elapsed_ms,
        )

    # ── Internal helpers ───────────────────────────────────────

    def _build_messages(
        self,
        query: str,
        history: list | None,
        system_instruction: str | None = None,
    ) -> list[dict]:
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
        system_content = SYSTEM_PROMPT
        if system_instruction and system_instruction.strip():
            system_content += (
                "\n\nAdditional instruction for this request:\n"
                + system_instruction.strip()
            )

        messages = [{"role": "system", "content": system_content}]

        if history:
            for msg in history[-10:]:  # Last 10 messages for context window
                role = "user" if msg.get("role") == "USER" else "assistant"
                content = msg.get("content", "")
                if content:
                    messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": query})

        return messages

    def _log_performance_metrics(self, response_data: dict) -> None:
        """Log Ollama token counts, timings, and throughput when available."""
        metrics = {}

        for count_field in ("prompt_eval_count", "eval_count"):
            count = response_data.get(count_field)
            if isinstance(count, (int, float)):
                metrics[count_field] = count

        duration_fields = (
            "prompt_eval_duration",
            "eval_duration",
            "total_duration",
            "load_duration",
        )
        for duration_field in duration_fields:
            duration_ns = response_data.get(duration_field)
            if isinstance(duration_ns, (int, float)):
                metrics[f"{duration_field}_ms"] = round(
                    duration_ns / 1_000_000,
                    2,
                )

        throughput_fields = (
            ("prompt_eval_count", "prompt_eval_duration", "prompt_tokens_per_sec"),
            ("eval_count", "eval_duration", "tokens_per_sec"),
        )
        for count_field, duration_field, metric_name in throughput_fields:
            count = response_data.get(count_field)
            duration_ns = response_data.get(duration_field)
            if (
                isinstance(count, (int, float))
                and isinstance(duration_ns, (int, float))
                and duration_ns > 0
            ):
                metrics[metric_name] = round(count / (duration_ns / 1e9), 1)

        if metrics:
            logger.info(
                "Ollama inference metrics: %s",
                json.dumps(metrics, sort_keys=True),
                extra={"ollama_metrics": metrics, **metrics},
            )

    def _call_ollama_chat(
        self,
        messages: list[dict],
        num_predict: int | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
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
        selected_model = model or self._model
        timeout = self._timeout if timeout_seconds is None else float(timeout_seconds)
        payload = json.dumps({
            "model": selected_model,
            "messages": messages,
            "stream": False,
            "keep_alive": self._keep_alive,
            "options": {
                "num_predict": (
                    num_predict if num_predict is not None else self._num_predict
                )
            },
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
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
                raise ModelUnavailableError(
                    f"Model '{selected_model}' is not installed in Ollama."
                ) from exc

            raise EngineUnavailableError(
                f"Ollama error (HTTP {exc.code}): {error_msg}"
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise EngineTimeoutError(
                    "Ollama generation exceeded the bounded timeout."
                ) from exc
            raise EngineUnavailableError(
                f"Could not connect to Ollama at {self._host}: {exc.reason}"
            ) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise EngineTimeoutError(
                "Ollama generation exceeded the bounded timeout."
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
