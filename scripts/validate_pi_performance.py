#!/usr/bin/env python3
"""Non-destructive acceptance harness. No model pulls, service changes or real DB writes."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASELINE = "f59da97d270ebe1157dac65e9862ec8ed77116cd"


def command(args, timeout=10):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          timeout=timeout, check=False)


def run_regressions(args, output):
    """Own the Linux test process group, including synthetic spawned workers."""
    isolated_group = sys.platform != "win32"
    with (output / "tests.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([
            sys.executable, str(Path(__file__).resolve()), "--tests-child",
            "--expected-commit", args.expected_commit, "--output", str(output),
        ], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=isolated_group)
        try:
            return process.wait(timeout=600)
        except BaseException:
            # Only our fresh session is targeted, never a service or the caller.
            if isolated_group:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            finally:
                # A child may ignore SIGTERM even after the leader exits.
                if isolated_group:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            raise


def bootstrap(directory):
    # Import settings before django.setup so no connection can target personal data.
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    import config.settings as config
    config.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3",
                                   "NAME": str(directory / "isolated.sqlite3")}}
    config.MEDIA_ROOT = str(directory / "media")
    config.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    config.DEBUG = False
    import django
    django.setup()


@contextmanager
def isolated_database(prefix):
    with tempfile.TemporaryDirectory(prefix=prefix) as temp:
        try:
            bootstrap(Path(temp))
            yield Path(temp)
        finally:
            # Windows cannot remove an open SQLite file. Close before cleanup
            # on every success/failure/interrupt path, including migration errors.
            from django.db import connections
            connections.close_all()


def distribution_versions():
    result = {}
    for name in ("Django", "djangorestframework", "piper-tts", "onnxruntime",
                 "numpy", "paho-mqtt", "pyserial", "openwakeword", "pypdf"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def resources():
    from system.control_plane.resources import get_resource_metrics
    measured = get_resource_metrics()
    result = {key: measured.get(key) for key in (
        "cpu_percent", "cpu_count", "ram_percent", "ram_available_bytes",
        "ram_total_bytes", "temperature_c", "throttled", "load_1m", "load_5m",
    )}
    try:
        fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        result["swap_total_kib"] = int(fields["SwapTotal"].split()[0])
        result["swap_free_kib"] = int(fields["SwapFree"].split()[0])
    except (OSError, KeyError, ValueError):
        result["swap_total_kib"] = result["swap_free_kib"] = None
    result["at_utc"] = datetime.now(timezone.utc).isoformat()
    return result


def median_call(operation, repeats=7):
    samples = []
    value = None
    for _ in range(repeats):
        started = time.perf_counter()
        value = operation()
        samples.append((time.perf_counter() - started) * 1000)
    return {"median_ms": round(statistics.median(samples), 3),
            "samples_ms": [round(value, 3) for value in samples]}, value


def retrieval_benchmarks():
    """Synthetic, disposable corpus; baseline comparison isolates row materialization."""
    from conversations.memory import LexicalConversationMemoryRetriever
    from conversations.models import Conversation, Message
    from knowledge_base.models import Document, KnowledgeChunk
    from knowledge_base.retrieval import LexicalRetriever, usable_knowledge_filter
    document = Document.objects.create(filename="audit-fixture.md", status="INDEXED",
                                       source_identifier="audit:synthetic")
    content = "Alpha beta processors use local memory. " * 16
    KnowledgeChunk.objects.bulk_create([
        KnowledgeChunk(document=document, chunk_index=n, content=content) for n in range(1000)
    ])

    def candidates():
        return (KnowledgeChunk.objects.filter(document__status="INDEXED")
                .filter(usable_knowledge_filter()).filter(content__icontains="alpha")
                .order_by("document_id", "chunk_index"))

    def materialize(projected):
        query = candidates()
        if projected:
            rows = query.values_list("id", "document_id", "document__source_identifier",
                                     "document__filename", "chunk_index", "content").iterator(chunk_size=200)
        else:
            rows = ((row.id, row.document_id, row.document.source_identifier,
                     row.document.filename, row.chunk_index, row.content)
                    for row in query.select_related("document").iterator(chunk_size=200))
        digest = hashlib.sha256()
        for row in rows:
            digest.update(json.dumps(row).encode())
        return digest.hexdigest()

    # Prime both paths, alternate measurement order to reduce warm-cache/order bias.
    if materialize(False) != materialize(True):
        raise RuntimeError("Candidate projections differ")
    baseline, optimized = [], []
    for index in range(9):
        for projected in ((False, True) if index % 2 == 0 else (True, False)):
            started = time.perf_counter()
            materialize(projected)
            (optimized if projected else baseline).append((time.perf_counter() - started) * 1000)
    projection = {
        "rows": 1000, "equal_row_digest": True, "repeats": 9,
        "baseline_median_ms": round(statistics.median(baseline), 3),
        "optimized_median_ms": round(statistics.median(optimized), 3),
        "baseline_samples_ms": baseline, "optimized_samples_ms": optimized,
        "scope": "candidate projection only, not full RAG or voice latency",
    }
    rag, matches = median_call(lambda: LexicalRetriever().retrieve("alpha beta processors"))
    rag["matches"] = len(matches)
    conversation = Conversation.objects.create(title="Synthetic audit memory")
    Message.objects.bulk_create([
        Message(conversation=conversation, sender="USER", text=f"Audit project alpha beta identifier {n}.")
        for n in range(600)
    ])
    current = Message.objects.create(conversation=conversation, sender="USER", text="Recall alpha beta")
    memory, matches = median_call(lambda: LexicalConversationMemoryRetriever().retrieve(
        "Recall alpha beta", conversation_id=conversation.pk, before_id=current.pk, recent_ids=[]))
    memory["matches"] = len(matches)
    return {"candidate_projection": projection, "synthetic_rag": rag, "synthetic_memory": memory}


class PiperTimings(logging.Handler):
    def __init__(self):
        super().__init__()
        self.samples = []

    def emit(self, record):
        metrics = getattr(record, "piper_timings", None)
        if isinstance(metrics, dict) and len(self.samples) < 512:
            self.samples.append(dict(metrics))


def benchmark_voice(report, args, directory, save):
    from django.conf import settings
    from assistant.ai_engine import get_engine
    from assistant.policy import AssistantResponsePolicy
    from assistant.services import AssistantService
    from assistant.voice.factory import get_tts_provider, reset_voice_providers
    from scripts.voice_loop import VoiceLoopConfig, speak_stream_response
    # These changes exist in this disposable process only. Never publish actions.
    settings.AI_ENGINE = "local"
    settings.DEVICE_CONTROL_ENABLED = False
    settings.COMPANION_DISPLAY_ENABLED = False
    settings.RAG_ENABLED = False  # LOCAL voice comparison independent of synthetic corpus.
    settings.ONLINE_KNOWLEDGE_AUTO_PERSIST = False
    if settings.TTS_ENGINE != "piper":
        raise RuntimeError("Real voice benchmark requires configured TTS_ENGINE=piper")
    engine = get_engine()
    if settings.OLLAMA_MODEL not in engine.available_models(cache_seconds=0):
        raise RuntimeError("Configured model is not installed; no download attempted")
    config = VoiceLoopConfig.from_settings(require_capture=False)
    if args.play_audio and config.audio_backend != "alsa":
        raise RuntimeError("Physical playback requires the configured ALSA backend")
    if not args.play_audio:
        config = replace(config, audio_backend="alsa", alsa_playback_device="null")
    report["voice_mode"] = "configured physical ALSA" if args.play_audio else "ALSA null sink; no physical audio"
    report["model_config"] = {key: getattr(settings, key) for key in (
        "OLLAMA_MODEL", "OLLAMA_NUM_CTX", "OLLAMA_KEEP_ALIVE", "OLLAMA_THINK",
        "VOICE_LLM_BRIEF_NUM_PREDICT", "VOICE_LLM_NORMAL_NUM_PREDICT", "VOICE_LLM_DETAILED_NUM_PREDICT",
    )}
    # Observe residency; never evict another process's model to manufacture a cold run.
    import urllib.request
    with urllib.request.urlopen(settings.OLLAMA_HOST.rstrip("/") + "/api/ps", timeout=5) as response:
        running = json.loads(response.read(1_000_001))
    report["initial_model_resident"] = any(
        model.get("name") == settings.OLLAMA_MODEL for model in running.get("models", [])
    )
    piper = PiperTimings()
    logger = logging.getLogger("assistant.voice.tts")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(piper)
    questions = [
        ("brief", "Briefly, what is a CPU?"),
        ("normal", "What is the difference between RAM and storage?"),
        ("normal", "How does a solar panel produce electricity?"),
        ("detailed", "Explain in detail how a computer executes a program, with examples."),
    ]
    report["voice_samples"] = []
    try:
        # Initial request is separate; all reported warm rounds follow it.
        runs = [("initial", "normal", "What is a computer processor?")]
        runs += [(f"warm-{number + 1}", mode, query)
                 for number in range(args.rounds) for mode, query in questions]
        for index, (phase, mode, query) in enumerate(runs):
            report.setdefault("resources", []).append(resources())
            plan = AssistantResponsePolicy.plan_voice_response(query)
            timings = {}
            offset = len(piper.samples)
            started = time.perf_counter()
            with tempfile.TemporaryDirectory(dir=directory) as audio_dir, open(os.devnull, "w") as quiet:
                with redirect_stdout(quiet):
                    _, done = speak_stream_response(
                        AssistantService.process_message_stream(query, response_plan=plan),
                        get_tts_provider(), Path(audio_dir), config, timings, started,
                    )
            if done.get("route") != "local" or done.get("engine") != "local":
                raise RuntimeError("Benchmark did not exercise LOCAL model generation")
            timings["total"] = time.perf_counter() - started
            if "first_tts_text_ready" in timings and "ai_ttft" in timings:
                timings["token_to_first_tts_unit"] = timings["first_tts_text_ready"] - timings["ai_ttft"]
            sample = {"phase": phase, "mode": mode, "timings": timings,
                      "piper": piper.samples[offset:], "ollama": done.get("ollama_metrics", {})}
            sample["stages"] = {key: done.get(key) for key in (
                "policy_ms", "history_ms", "routing_ms", "rag_ms", "memory_ms", "execution_ms",
                "num_predict", "output_limit_reached", "resource_profile",
            )}
            report["voice_samples"].append(sample)
            save()
            print(f"Completed {phase} {mode} ({index + 1}/{len(runs)})", flush=True)
    finally:
        try:
            report.setdefault("resources", []).append(resources())
        finally:
            try:
                reset_voice_providers()
            finally:
                logger.removeHandler(piper)
                logger.setLevel(previous_level)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-commit", required=True, help="Approved full Git SHA; compared exactly")
    parser.add_argument("--output", type=Path, required=True, help="New report directory (must not exist)")
    parser.add_argument("--rounds", type=int, choices=range(1, 6), default=2)
    parser.add_argument("--play-audio", action="store_true", help="Use configured speaker; otherwise ALSA null sink")
    parser.add_argument("--online", action="store_true", help="Measure configured SearXNG separately; sends one public query")
    parser.add_argument("--offline-only", action="store_true", help="Only synthetic DB benchmarks; no inference/audio/network")
    parser.add_argument("--skip-tests", action="store_true", help="For repeated benchmarks after a passing complete suite")
    parser.add_argument("--tests-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.tests_child:
        with isolated_database("companion-audit-tests-"):
            from django.test.runner import DiscoverRunner
            return int(bool(DiscoverRunner(verbosity=1, interactive=False).run_tests([])))
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_commit):
        parser.error("--expected-commit must be a full lowercase SHA")
    actual = command(["git", "rev-parse", "HEAD"])
    if actual.returncode or actual.stdout.strip() != args.expected_commit:
        parser.error("Checkout does not match expected commit")
    if not args.offline_only:
        if platform.system() != "Linux":
            parser.error("Real acceptance requires Linux; use --offline-only for Windows harness checks")
        if command(["git", "diff", "HEAD", "--exit-code", "--quiet"]).returncode:
            parser.error("Tracked checkout differs from expected commit")
        if command(["git", "merge-base", "--is-ancestor", BASELINE, "HEAD"]).returncode:
            parser.error("Accepted baseline is not an ancestor (fetch its history first)")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"commit": args.expected_commit, "baseline_commit": BASELINE,
              "platform": platform.platform(), "python": platform.python_version(),
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "mode": "offline synthetic" if args.offline_only else "Linux acceptance",
              "dependencies": distribution_versions(), "errors": []}
    report["tracked_changes"] = command(["git", "diff", "HEAD", "--name-only"]).stdout.splitlines()

    def save():
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        lines = ["# Companion acceptance report", "", f"Commit: `{report['commit']}`",
                 f"Platform: {report['platform']}; Python {report['python']}",
                 f"Mode: {report['mode']}", "", "No physical microphone or TFT validation is claimed.",
                 "Raw scalar measurements: report.json. Regression output: tests.log.", ""]
        projection = report.get("retrieval", {}).get("candidate_projection")
        if projection:
            lines += [f"Synthetic candidate projection median: {projection['baseline_median_ms']} ms baseline, "
                      f"{projection['optimized_median_ms']} ms optimized (1,000 rows, nine alternating repetitions).", ""]
        if report.get("voice_samples"):
            lines += [f"Voice mode: {report['voice_mode']}", "",
                      "| Phase | Policy | First token s | First speech submission s | Total s |",
                      "|---|---|---:|---:|---:|"]
            for sample in report["voice_samples"]:
                timing = sample["timings"]
                lines.append(f"| {sample['phase']} | {sample['mode']} | {timing.get('ai_ttft')} | "
                             f"{timing.get('first_speech')} | {timing.get('total')} |")
        lines += ["", f"Tests: {report.get('tests', 'not run')}", f"Errors: {report['errors']}",
                  "", "Compare identical settings, sink mode, corpus and questions across approved commits.",
                  "Queue starvation and first speech submission are software estimates, not DAC timestamps."]
        (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    save()
    try:
        report["stage"] = "regression tests"
        if not args.skip_tests:
            test_exit_code = run_regressions(args, output)
            report["tests"] = {"exit_code": test_exit_code}
            summary = re.search(r"Ran (\d+) tests in ([\d.]+)s", (output / "tests.log").read_text(encoding="utf-8"))
            if summary:
                report["tests"].update(count=int(summary[1]), elapsed_seconds=float(summary[2]))
            save()
            if test_exit_code:
                raise RuntimeError("Regression suite failed; inspect tests.log")
        with isolated_database("companion-audit-") as directory:
            from django.core.management import call_command
            report["stage"] = "temporary database migration"
            call_command("migrate", verbosity=0, interactive=False)
            report["stage"] = "synthetic retrieval"
            report["retrieval"] = retrieval_benchmarks()
            save()
            if not args.offline_only:
                report["stage"] = "service and dependency checks"
                report["services"] = {}
                for name in ("smart-ai-companion", "smart-ai-companion-model-warm", "ollama",
                             "mosquitto", "searxng", "smart-ai-companion-knowledge-sync.timer"):
                    result = command(["systemctl", "is-active", name])
                    report["services"][name] = result.stdout.strip()
                from django.conf import settings
                report["executables"] = {name: bool(shutil.which(name)) for name in ("ffmpeg", "aplay", "arecord", "vcgencmd")}
                report["stt_assets"] = {"binary_exists": Path(settings.STT_WHISPER_BIN).is_file(),
                                        "model_exists": Path(settings.STT_WHISPER_MODEL).is_file()}
                report["dependency_checks"] = {
                    "audio_tools_ready": all(report["executables"][name] for name in ("ffmpeg", "aplay")),
                    "piper_installed": report["dependencies"]["piper-tts"] is not None,
                    "stt_assets_ready": all(report["stt_assets"].values()),
                }
                # Unit names vary (e.g. containerized SearXNG); record both unit
                # state and application probes instead of assuming every unit
                # must be active. A completed oneshot warm service is inactive.
                from system.control_plane.health import get_service_health, _http_check
                report["service_probes"] = {
                    name: {key: item.get(key) for key in ("status", "latency_ms")}
                    for name, item in get_service_health().items()
                }
                # get_service_health's Django entry describes this Python
                # process. Probe the unit's documented localhost:8000 listener
                # independently; retain no response body or endpoint detail.
                dashboard = _http_check("http://127.0.0.1:8000", "", "Dashboard")
                report["service_probes"]["dashboard_http"] = {
                    key: dashboard.get(key) for key in ("status", "latency_ms")
                }
                if not report["dependency_checks"]["audio_tools_ready"] or not report["dependency_checks"]["piper_installed"]:
                    raise RuntimeError("Required real audio dependencies are missing")
                report["stage"] = "LOCAL voice benchmark"
                save()
                benchmark_voice(report, args, directory, save)
                if args.online:
                    report["stage"] = "online retrieval"
                    if not settings.ONLINE_RETRIEVAL_ENABLED:
                        report["online"] = {"status": "not configured"}
                    else:
                        from assistant.online.factory import get_online_retriever
                        started = time.perf_counter()
                        results = get_online_retriever().retrieve("latest Raspberry Pi software release")
                        report["online"] = {"latency_ms": round((time.perf_counter() - started) * 1000, 2),
                                            "results": len(results), "content_saved": False}
        report["stage"] = "complete"
    except (Exception, KeyboardInterrupt) as exc:
        # Exception strings/tracebacks can carry URLs/configuration; report type only.
        report["errors"].append(type(exc).__name__)
        print(f"Validation stopped ({type(exc).__name__}); report retained.", file=sys.stderr)
    finally:
        save()
    print(f"Report: {output / 'report.md'}")
    return int(bool(report["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
