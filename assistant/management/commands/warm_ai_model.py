"""Explicitly load the configured Ollama model into memory."""

import math
import time

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
            help="Shared timeout for model and chat-prefix warm-up (default: 90 seconds).",
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
            model_result = engine.warm_model(
                model=model,
                timeout_seconds=timeout,
            )

            execution_plan = get_ai_execution_plan()
            registry = build_runtime_capability_registry(
                online_allowed=execution_plan.online_allowed,
            )
            remaining_timeout = deadline - time.monotonic()
            if remaining_timeout <= 0:
                raise EngineTimeoutError("The shared warm-up timeout was exhausted.")
            prefix_result = engine.warm_chat_prefix(
                system_instruction=registry.build_grounding_instruction(),
                model=model,
                timeout_seconds=remaining_timeout,
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
