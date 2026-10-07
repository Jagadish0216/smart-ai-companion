"""Explicitly load the configured Ollama model into memory."""

import json
import math
import time
import urllib.error
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from assistant.ai_engine import (
    EngineTimeoutError,
    EngineUnavailableError,
    ModelUnavailableError,
)
from assistant.ai_engine.local import LocalLLMEngine
from assistant.execution_policy import get_ai_execution_plan
from assistant.policy import build_runtime_capability_registry


def _remaining_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise EngineTimeoutError("The shared warm-up timeout was exhausted.")
    return remaining


def _wait_for_ollama(host: str, deadline: float) -> None:
    """Wait for the HTTP API, retrying only transient availability failures."""
    request = urllib.request.Request(
        f"{host.rstrip('/')}/api/version", method="GET",
    )
    while True:
        remaining = _remaining_timeout(deadline)
        try:
            with urllib.request.urlopen(request, timeout=min(1.0, remaining)) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("version"), str):
                raise EngineUnavailableError("Ollama returned invalid readiness data.")
            return
        except urllib.error.HTTPError as exc:
            if exc.code != 503:
                raise EngineUnavailableError(
                    f"Ollama readiness check failed (HTTP {exc.code})."
                ) from exc
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EngineUnavailableError("Ollama returned invalid readiness data.") from exc
        time.sleep(min(0.2, _remaining_timeout(deadline)))


class Command(BaseCommand):
    help = (
        "Warm one local Ollama model and its reusable Smart Companion chat prefix; "
        "defaults to the configured OLLAMA_MODEL."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            help="Explicit installed Ollama model name for diagnostics.",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=90.0,
            help="Shared timeout for API readiness, model and chat-prefix warm-up (default: 90 seconds).",
        )

    def handle(self, *args, **options):
        model = options.get("model") or settings.OLLAMA_MODEL
        timeout = options["timeout"]
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 300:
            raise CommandError(
                "--timeout must be greater than 0 and at most 300 seconds."
            )
        engine = LocalLLMEngine(
            host=settings.OLLAMA_HOST,
            model=model,
            timeout=settings.OLLAMA_TIMEOUT,
            keep_alive=getattr(settings, "OLLAMA_KEEP_ALIVE", "30m"),
            num_predict=settings.OLLAMA_NUM_PREDICT,
        )
        started = time.perf_counter()
        deadline = time.monotonic() + timeout
        try:
            _wait_for_ollama(settings.OLLAMA_HOST, deadline)
            self._warm_optional_rag(engine, model, deadline)
            model_result = engine.warm_model(
                model=model,
                timeout_seconds=_remaining_timeout(deadline),
            )

            execution_plan = get_ai_execution_plan()
            registry = build_runtime_capability_registry(
                online_allowed=execution_plan.online_allowed,
            )
            prefix_result = engine.warm_chat_prefix(
                system_instruction=registry.build_grounding_instruction(),
                model=model,
                timeout_seconds=_remaining_timeout(deadline),
            )
        except ModelUnavailableError as exc:
            raise CommandError(
                f"Configured Ollama model is unavailable: {model}"
            ) from exc
        except EngineTimeoutError as exc:
            raise CommandError("Ollama model or chat-prefix warm-up timed out.") from exc
        except EngineUnavailableError as exc:
            raise CommandError(
                "Ollama is unavailable; model or chat-prefix warm-up failed."
            ) from exc

        elapsed_ms = round((time.perf_counter() - started) * 1000)

        load_ns = model_result.get("load_duration")
        load_ms = (
            round(load_ns / 1_000_000, 2)
            if isinstance(load_ns, (int, float))
            else None
        )

        prompt_ns = prefix_result.get("prompt_eval_duration")
        prompt_ms = (
            round(prompt_ns / 1_000_000, 2)
            if isinstance(prompt_ns, (int, float))
            else None
        )
        prompt_tokens = prefix_result.get("prompt_eval_count")

        details = []
        if load_ms is not None:
            details.append(f"Ollama load {load_ms} ms")
        if prompt_ms is not None:
            details.append(f"chat-prefix prefill {prompt_ms} ms")
        if isinstance(prompt_tokens, (int, float)):
            details.append(f"{int(prompt_tokens)} prompt tokens")

        suffix = f", {', '.join(details)}" if details else ""

        self.stdout.write(
            self.style.SUCCESS(
                f"Warmed model {model} and production chat prefix "
                f"in {elapsed_ms} ms{suffix}; keep_alive is configured."
            )
        )

    def _warm_optional_rag(self, engine, primary_model, deadline):
        """Best effort only, with at most a third of the remaining boot budget."""
        if not (
            getattr(settings, "RAG_PREWARM_ENABLED", False)
            and getattr(settings, "RAG_ENABLED", False)
            and settings.AI_ENGINE == "local"
        ):
            return
        rag_model = getattr(settings, "RAG_MODEL", settings.AI_LIGHTWEIGHT_MODEL)
        if rag_model == primary_model:
            return
        try:
            optional_seconds = float(getattr(settings, "RAG_PREWARM_TIMEOUT_SECONDS", 20))
            if not math.isfinite(optional_seconds) or optional_seconds <= 0:
                return
            plan = get_ai_execution_plan()
            if not plan.generation_allowed or not plan.local_ai_allowed:
                return
            # ECO/PROTECTIVE never preloads a larger configured RAG override.
            if plan.resource_profile in {"ECO", "PROTECTIVE"} and rag_model != settings.AI_LIGHTWEIGHT_MODEL:
                return
            if rag_model not in engine.available_models():
                self.stderr.write("Optional RAG model is not installed; primary warm-up will continue.")
                return
            seconds = min(optional_seconds, _remaining_timeout(deadline) / 3)
            optional_deadline = time.monotonic() + seconds
            engine.warm_model(model=rag_model, timeout_seconds=_remaining_timeout(optional_deadline))
            registry = build_runtime_capability_registry(online_allowed=plan.online_allowed)
            engine.warm_chat_prefix(
                system_instruction=registry.build_grounding_instruction(),
                model=rag_model,
                timeout_seconds=_remaining_timeout(optional_deadline),
            )
            self.stderr.write("Optional RAG model and prefix warmed; warming the primary model last.")
        except EngineUnavailableError:
            self.stderr.write("Optional RAG warm-up failed or timed out; primary warm-up will continue.")
