"""Explicitly load the configured Ollama model into memory."""

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from assistant.ai_engine import (
    EngineTimeoutError,
    EngineUnavailableError,
    ModelUnavailableError,
)
from assistant.ai_engine.local import LocalLLMEngine


class Command(BaseCommand):
    help = "Warm one local Ollama model; defaults to the configured OLLAMA_MODEL."

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            help="Explicit installed Ollama model name for diagnostics.",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=90.0,
            help="Bounded warm-up timeout in seconds (default: 90).",
        )

    def handle(self, *args, **options):
        model = options.get("model") or settings.OLLAMA_MODEL
        timeout = options["timeout"]
        if timeout <= 0 or timeout > 300:
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
        try:
            result = engine.warm_model(model=model, timeout_seconds=timeout)
        except ModelUnavailableError as exc:
            raise CommandError(f"Configured Ollama model is unavailable: {model}") from exc
        except EngineTimeoutError as exc:
            raise CommandError("Ollama model warm-up timed out.") from exc
        except EngineUnavailableError as exc:
            raise CommandError("Ollama is unavailable; model warm-up failed.") from exc
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        load_ns = result.get("load_duration")
        load_ms = round(load_ns / 1_000_000, 2) if isinstance(load_ns, (int, float)) else None
        suffix = f", Ollama load {load_ms} ms" if load_ms is not None else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"Warmed model {model} in {elapsed_ms} ms{suffix}; keep_alive is configured."
            )
        )
