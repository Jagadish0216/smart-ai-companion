#!/usr/bin/env python3
"""Exact-fixture inference A/B. Temporary baseline snapshot; no deploy or model pull."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import signal
import statistics
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "3bb84a9ca6987f6fd11df366be97aabbdef929b8"
FIXTURES = (
    ("brief", "Briefly, what is the capital of India?", ("delhi",)),
    ("normal", "What is the difference between RAM and storage?", ("ram", "storage")),
    ("normal", "How does a solar panel produce electricity?", ("light",)),
    ("detailed", "Explain in detail the fetch, decode and execute stages, with one example.", ("fetch", "decode", "execute")),
    ("rag", "According to my notes, what voltage does the Lumen board use?", ("3.3",)),
    ("memory", "What project codename did I mention earlier?", ("amber finch",)),
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def snapshot(destination):
    paths = ["assistant", "conversations", "knowledge_base", "system", "config",
             "scripts", "dashboard", "manage.py"]
    archive = subprocess.run(["git", "archive", "--format=zip", BASELINE, "--", *paths],
                             cwd=ROOT, capture_output=True, check=True, timeout=30)
    with zipfile.ZipFile(io.BytesIO(archive.stdout)) as zipped:
        for entry in zipped.infolist():
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(destination.resolve()) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Unsafe archive entry")
        zipped.extractall(destination)


def telemetry():
    from system.control_plane.resources import get_resource_metrics
    measured = get_resource_metrics()
    sample = {key: measured.get(key) for key in (
        "cpu_percent", "temperature_c", "throttled", "ram_available_bytes", "load_1m",
    )}
    try:
        sample["requested_cpu_khz"] = int(Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq").read_text())
    except (OSError, ValueError):
        sample["requested_cpu_khz"] = None
    try:
        result = subprocess.run(["vcgencmd", "measure_clock", "arm"], capture_output=True,
                                text=True, timeout=1, check=False)
        sample["measured_arm_hz"] = int(result.stdout.strip().split("=", 1)[1]) if result.returncode == 0 else None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        sample["measured_arm_hz"] = None
    try:
        fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        sample["swap_free_kib"] = int(fields["SwapFree"].split()[0])
    except (OSError, KeyError, ValueError):
        sample["swap_free_kib"] = None
    sample["monotonic_s"] = time.monotonic()
    return sample


def stop_for_pressure(sample):
    flags = sample.get("throttled") or {}
    active = any(flags.get(key) for key in (
        "under_voltage", "frequency_capped", "currently_throttled", "soft_temperature_limit",
    ))
    return active or (sample.get("temperature_c") is not None and sample["temperature_c"] >= 75)


def options_for(args, arm):
    options = {"seed": 42, "temperature": 0, "num_ctx": args.context}
    if args.threads:
        options["num_thread"] = args.threads
    if args.batch:
        options["num_batch"] = args.batch
    if arm == "B" and args.profile == "cpu":
        if args.candidate_threads:
            options["num_thread"] = args.candidate_threads
        if args.candidate_batch:
            options["num_batch"] = args.candidate_batch
    return options


def validate_local_model(installed):
    if (installed.get("remote_model") or installed.get("remote_host")
            or installed.get("details", {}).get("format") != "gguf"):
        raise RuntimeError("Comparison requires installed local GGUF models, not remote/cloud aliases")


def child(args):
    sys.path.insert(0, str(Path(args.source_root).resolve()))
    from scripts.validate_pi_performance import isolated_database
    with isolated_database("companion-inference-ab-") as temporary:
        from django.conf import settings
        from django.core.management import call_command
        settings.BASE_DIR = ROOT  # Same storage filesystem for resource policy in both arms.
        settings.AI_ENGINE = "local"
        settings.DEVICE_CONTROL_ENABLED = False
        settings.COMPANION_DISPLAY_ENABLED = False
        settings.ONLINE_RETRIEVAL_ENABLED = False
        settings.ONLINE_KNOWLEDGE_AUTO_PERSIST = False
        settings.RAG_ENABLED = True
        settings.OLLAMA_NUM_THREAD = None
        settings.OLLAMA_NUM_BATCH = None
        settings.VOICE_COMPACT_STYLE = args.arm == "B" and args.profile == "compact"
        settings.VOICE_STREAM_FIRST_SENTENCE_MIN_CHARS = "30" if args.arm == "B" and args.profile == "first-sentence" else ""
        if args.arm == "B" and args.profile == "detail-budget":
            settings.VOICE_LLM_DETAILED_NUM_PREDICT = args.detail_budget
        if args.arm == "B" and args.candidate_model:
            settings.OLLAMA_MODEL = args.candidate_model
        from assistant.ai_engine import get_engine, reset_engine
        from assistant.policy import AssistantResponsePolicy, build_runtime_capability_registry
        from assistant.services import AssistantService, _build_generation_instruction
        from assistant.voice.factory import get_tts_provider, reset_voice_providers
        from scripts import voice_loop
        reset_engine()
        engine = get_engine()
        original_builder = engine._build_chat_request
        requests = []

        def build(*positional, **keyword):
            request = original_builder(*positional, **keyword)
            payload = json.loads(request.data)
            payload["options"].update(options_for(args, args.arm))
            request.data = json.dumps(payload).encode()
            # Fixed/synthetic inputs only; record hashes, not prompt text.
            requests.append({"message_sha256": digest(payload["messages"]),
                             "options": payload["options"], "think": payload.get("think"),
                             "keep_alive": payload.get("keep_alive"),
                             "model": payload["model"], "input_chars": sum(len(m["content"]) for m in payload["messages"])})
            return request

        engine._build_chat_request = build
        report = {"arm": args.arm, "round": args.block_round, "profile": args.profile,
                  "fixture_sha256": digest(FIXTURES), "samples": [], "plan_only": args.plan_only}
        output = Path(args.child_output)

        def save():
            output.write_text(json.dumps(report, indent=2), encoding="utf-8")

        report["stage"] = "plan" if args.plan_only else "model-preflight"
        save()
        if args.plan_only:
            # No schema, model inventory, providers or service/resource probes.
            settings.RAG_ENABLED = False
            registry = build_runtime_capability_registry(online_allowed=False)
            for mode, query, _ in FIXTURES:
                plan = AssistantResponsePolicy.plan_voice_response(query, registry=registry)
                instruction = _build_generation_instruction(
                    registry=registry, base_instruction=plan.system_instruction, rag_results=[],
                    response_plan=plan, online_instruction=None,
                )
                engine._build_chat_request(engine._build_messages(query, [], system_instruction=instruction,
                                                                 system_prompt=""), stream=True, num_predict=plan.num_predict)
            report["requests"] = requests
            report["stage"] = "complete"
            save()
            return 0
        if settings.TTS_ENGINE != "piper":
            raise RuntimeError("Real comparison requires configured Piper")
        if settings.OLLAMA_MODEL not in engine.available_models(cache_seconds=0):
            raise RuntimeError("Requested model is not installed; no download attempted")
        import urllib.request

        def installed_model_info(endpoint):
            with urllib.request.urlopen(settings.OLLAMA_HOST.rstrip("/") + endpoint, timeout=5) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise RuntimeError("Oversized Ollama metadata")
            payload = json.loads(raw)
            return next((item for item in payload.get("models", [])
                         if item.get("name") == settings.OLLAMA_MODEL), {})

        installed = installed_model_info("/api/tags")
        validate_local_model(installed)
        report["installed_model"] = {key: installed.get(key) for key in ("digest", "size")}
        report["model_details"] = {key: installed.get("details", {}).get(key)
                                   for key in ("family", "parameter_size", "quantization_level")}
        with urllib.request.urlopen(settings.OLLAMA_HOST.rstrip("/") + "/api/version", timeout=5) as response:
            version = json.loads(response.read(4096)).get("version")
        report["ollama_version"] = version if isinstance(version, str) and re.fullmatch(r"[a-zA-Z0-9.+_-]{1,80}", version) else None
        show_request = urllib.request.Request(
            settings.OLLAMA_HOST.rstrip("/") + "/api/show",
            data=json.dumps({"model": settings.OLLAMA_MODEL, "verbose": False}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(show_request, timeout=5) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise RuntimeError("Oversized model description")
        shown = json.loads(raw)
        report["model_metadata"] = {
            "template_sha256": digest(shown.get("template", "")),
            "parameters_sha256": digest(shown.get("parameters", "")),
            "architecture": shown.get("model_info", {}).get("general.architecture"),
            "context_lengths": {key: value for key, value in shown.get("model_info", {}).items()
                                if key.endswith(".context_length") and type(value) is int},
        }
        report["stage"] = "isolated-schema"
        save()
        call_command("migrate", verbosity=0, interactive=False)
        from knowledge_base.models import Document, KnowledgeChunk
        from conversations.models import Conversation, Message
        document = Document.objects.create(filename="synthetic-ab.md", status="INDEXED", source_identifier="audit:synthetic")
        KnowledgeChunk.objects.create(document=document, chunk_index=0,
                                     content="The Lumen board uses 3.3 volts. Do not connect it directly to a 5 volt signal.")
        config = voice_loop.VoiceLoopConfig.from_settings(require_capture=False)
        if args.play_audio and config.audio_backend != "alsa":
            raise RuntimeError("Physical comparison requires configured ALSA")
        if not args.play_audio:
            config = replace(config, audio_backend="alsa", alsa_playback_device="null")
        report["sink"] = "configured ALSA speaker" if args.play_audio else "ALSA null; no physical sound"
        report["voice_config"] = {key: value for key, value in vars(config).items()
                                  if key not in ("input_source", "alsa_capture_device", "alsa_playback_device")}
        report["model"] = settings.OLLAMA_MODEL
        provider = get_tts_provider()
        try:
            report["stage"] = "voice-warmup"
            save()
            provider.warm()  # No inference; persistent voice readiness outside timed requests.
            # One separately labelled real warm-up, then the same six fixtures.
            for index in [-1, *range(len(FIXTURES))]:
                report["stage"] = f"fixture-{index}"
                save()
                mode, query, checks = FIXTURES[max(0, index)]
                before = telemetry()
                if stop_for_pressure(before):
                    report["stopped_for_pressure"] = before
                    save()
                    raise RuntimeError("Current thermal/power pressure; comparison stopped")
                conversation_id = None
                if mode == "memory":
                    conversation = Conversation.objects.create(title="Synthetic A/B recall")
                    Message.objects.create(conversation=conversation, sender="USER", text="Our project codename is Amber Finch.")
                    Message.objects.bulk_create([Message(conversation=conversation, sender="USER", text="Synthetic unrelated context.") for _ in range(6)])
                    conversation_id = conversation.pk
                plan = AssistantResponsePolicy.plan_voice_response(query)
                timings, samples = {}, [before]
                stopped = Event()
                pressure = Event()

                def monitor():
                    while not stopped.wait(2):
                        try:
                            sample = telemetry()
                        except Exception:
                            pressure.set()  # Missing safety telemetry invalidates the block.
                            return
                        if len(samples) < 180:
                            samples.append(sample)
                        if stop_for_pressure(sample):
                            pressure.set()

                thread = Thread(target=monitor, name="ab-telemetry", daemon=True)
                print(f"START {args.arm} round {args.block_round} fixture {index}", flush=True)
                started = time.perf_counter()
                real_player = voice_loop.PersistentAlsaPlayer

                class MeasuredPlayer(real_player):
                    def write_file(self, path, *, on_progress=None):
                        def progress(written, total):
                            timings.setdefault("first_pcm_submitted", time.perf_counter() - started)
                            if on_progress is not None:
                                on_progress(written, total)
                        return super().write_file(path, on_progress=progress)

                def events():
                    iterator = AssistantService.process_message_stream(query, conversation_id=conversation_id, response_plan=plan)
                    try:
                        for event in iterator:
                            if pressure.is_set():
                                raise RuntimeError("Thermal/power pressure detected")
                            yield event
                    finally:
                        iterator.close()

                thread.start()
                voice_loop.PersistentAlsaPlayer = MeasuredPlayer
                try:
                    with tempfile.TemporaryDirectory(dir=temporary) as audio, open(os.devnull, "w") as quiet:
                        with redirect_stdout(quiet):
                            _, done = voice_loop.speak_stream_response(events(), provider, Path(audio), config, timings, started)
                    timings["total"] = time.perf_counter() - started
                except BaseException:
                    report["aborted_sample"] = {"fixture": index, "timings_s": timings, "telemetry": samples}
                    save()
                    raise
                finally:
                    stopped.set()
                    thread.join(timeout=5)
                    voice_loop.PersistentAlsaPlayer = real_player
                if pressure.is_set():
                    raise RuntimeError("Thermal/power pressure detected; do not accept this sample")
                if done.get("engine") != "local":
                    raise RuntimeError("Resource/policy response is not a model benchmark sample")
                if mode == "rag" and not done.get("rag_used") or mode == "memory" and not done.get("memory_used"):
                    raise RuntimeError("Synthetic grounding/recall fixture did not execute")
                if index < 0:
                    resident = installed_model_info("/api/ps")
                    report["resident_model"] = {key: resident.get(key) for key in ("size", "size_vram", "context_length")}
                sample = {"fixture": index, "mode": mode, "warmup": index < 0, "timings_s": timings,
                          "engine_ttft_ms": done.get("ttft_ms"), "generation_ms": done.get("generation_ms"),
                          "request": requests[-1], "metrics": done.get("ollama_metrics", {}),
                          "route": done.get("route"), "resource_profile": done.get("resource_profile"),
                          "num_predict": done.get("num_predict"), "memory_used": done.get("memory_used"),
                          "rag_used": done.get("rag_used"), "answer": done.get("response", "")[:8192],
                          "rubric_keywords_present": {term: term in done.get("response", "").lower() for term in checks},
                          "manual_quality_accepted": None, "observed_audible_onset_s": None,
                          "telemetry": samples}
                report["samples"].append(sample)
                save()
                print(f"Completed {args.arm} round {args.block_round} fixture {index}", flush=True)
        finally:
            reset_voice_providers()
        report["stage"] = "complete"
        save()
        return 0


def condition_differences(a, b, sample_a, sample_b):
    """Report all unequal controls, including intentional experimental variables."""
    controls = {
        "request": (sample_a.get("request"), sample_b.get("request")),
        "route": (sample_a.get("route"), sample_b.get("route")),
        "num_predict": (sample_a.get("num_predict"), sample_b.get("num_predict")),
        "resource_profile": (sample_a.get("resource_profile"), sample_b.get("resource_profile")),
    }
    for key in ("sink", "voice_config", "installed_model", "model_metadata", "ollama_version"):
        controls[key] = (a.get(key), b.get(key))
    for key in ("temperature_c", "requested_cpu_khz", "measured_arm_hz", "throttled"):
        controls["starting_" + key] = (
            (sample_a.get("telemetry") or [{}])[0].get(key),
            (sample_b.get("telemetry") or [{}])[0].get(key),
        )
    return {key: {"A": left, "B": right} for key, (left, right) in controls.items() if left != right}


def summarize(output, comparison):
    grouped = {}
    paired = {}
    for filename in comparison["blocks"]:
        block = json.loads((output / filename).read_text())
        for sample in block["samples"]:
            if not sample["warmup"]:
                grouped.setdefault((block["arm"], sample["fixture"], sample["mode"]), []).append(sample)
                paired.setdefault((block["round"], sample["fixture"]), {})[block["arm"]] = (block, sample)
    comparison["condition_differences"] = []
    for (round_number, fixture), arms in sorted(paired.items()):
        if "A" not in arms or "B" not in arms:
            comparison["condition_differences"].append({"round": round_number, "fixture": fixture, "unpaired": True})
            continue
        a, sa = arms["A"]
        b, sb = arms["B"]
        differences = condition_differences(a, b, sa, sb)
        if differences:
            comparison["condition_differences"].append({"round": round_number, "fixture": fixture, "differences": differences})
    lines = ["# Inference A/B", "", f"Baseline: `{BASELINE}`; candidate HEAD: `{comparison['candidate_head']}`.",
             f"Profile: {comparison['profile']}. Synthetic answers are in the per-block JSON reports.",
             "", "Quality and audible onset require human review. No automatic factual-quality acceptance.", "",
             "| Arm | Fixture | N | Engine TTFT ms | First PCM s | Total s | Decode tok/s | Length stops |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    summary = []
    for (arm, fixture, mode), samples in sorted(grouped.items()):
        def median(values):
            values = [v for v in values if type(v) in (int, float)]
            return round(statistics.median(values), 3) if values else None
        row = {"arm": arm, "fixture": fixture, "mode": mode, "n": len(samples),
               "ttft_ms": median([s.get("engine_ttft_ms") for s in samples]),
               "first_pcm_s": median([s["timings_s"].get("first_pcm_submitted") for s in samples]),
               "total_s": median([s["timings_s"].get("total") for s in samples]),
               "decode_tokens_s": median([s["metrics"].get("tokens_per_sec") for s in samples]),
               "length_stops": sum(s["metrics"].get("done_reason") == "length" for s in samples),
               "effective_budgets": sorted({s["num_predict"] for s in samples}),
               "resource_profiles": sorted({s["resource_profile"] for s in samples})}
        summary.append(row)
        lines.append(f"| {arm} | {fixture} {mode} | {row['n']} | {row['ttft_ms']} | {row['first_pcm_s']} | {row['total_s']} | {row['decode_tokens_s']} | {row['length_stops']} |")
    comparison["summary"] = summary
    lines += ["", "## Unequal conditions (including intentional profile changes)", ""]
    for mismatch in comparison["condition_differences"]:
        fields = ", ".join(mismatch.get("differences", {})) or "missing paired arm"
        lines.append(f"- Round {mismatch['round']}, fixture {mismatch['fixture']}: {fields}. See comparison.json for A/B values.")
    if not comparison["condition_differences"]:
        lines.append("No unequal recorded controls in paired samples. Missing telemetry still requires review; plan-only has no measured samples.")
    lines += ["", "First PCM is pipe acceptance, not acoustic onset; null sink has no audible speech.",
              "Compare effective budgets and resource profiles before attributing differences.",
              "Telemetry retains current and latched flags separately; aborted blocks are not accepted measurements."]
    (output / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_child(command):
    process = subprocess.Popen(command, cwd=ROOT, start_new_session=os.name == "posix")
    try:
        result = process.wait(timeout=1800)
        if result:
            raise RuntimeError("A/B block failed; inspect partial report")
    except BaseException:
        if os.name == "posix":
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
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=("defaults", "first-sentence", "compact", "cpu", "detail-budget"), default="defaults")
    parser.add_argument("--rounds", type=int, choices=range(1, 4), default=2)
    parser.add_argument("--cooldown-seconds", type=int, choices=range(0, 61), default=10)
    parser.add_argument("--context", type=int, choices=(2048, 4096), default=4096)
    parser.add_argument("--threads", type=int, choices=(2, 3, 4))
    parser.add_argument("--batch", type=int, choices=(64, 128, 256, 512))
    parser.add_argument("--candidate-threads", type=int, choices=(2, 3, 4))
    parser.add_argument("--candidate-batch", type=int, choices=(64, 128, 256, 512))
    parser.add_argument("--candidate-model", help="Explicit installed alternative only; never pulled")
    parser.add_argument("--detail-budget", type=int, choices=(256, 384), default=256)
    parser.add_argument("--play-audio", action="store_true")
    parser.add_argument("--plan-only", action="store_true", help="No inference, audio or network; validate snapshots/payloads")
    for name in ("source-root", "arm", "child-output", "block-round"):
        parser.add_argument("--" + name, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.source_root:
        return child(args)
    if args.candidate_model and args.profile != "defaults":
        parser.error("Model comparison must use defaults profile to isolate the model")
    if args.profile != "cpu" and (args.candidate_threads or args.candidate_batch):
        parser.error("Candidate CPU options require cpu profile")
    if args.profile == "cpu" and bool(args.candidate_threads) == bool(args.candidate_batch):
        parser.error("Change exactly one candidate CPU option per comparison")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if not re.fullmatch(r"[a-f0-9]{40}", args.expected_commit) or actual != args.expected_commit:
        parser.error("Expected full feature commit does not match HEAD")
    if not args.plan_only and (platform.system() != "Linux" or subprocess.run(
            ["git", "diff", "HEAD", "--exit-code", "--quiet"], cwd=ROOT).returncode):
        parser.error("Real benchmark requires Linux and clean tracked feature files")
    if subprocess.run(["git", "merge-base", "--is-ancestor", BASELINE, "HEAD"], cwd=ROOT).returncode:
        parser.error("Validated baseline must be an ancestor")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    # Loading config reads .env into this process's inherited environment only.
    # It does not initialize Django or access the production database.
    sys.path.insert(0, str(ROOT))
    import config.settings  # noqa: F401
    with tempfile.TemporaryDirectory(prefix="companion-ab-baseline-") as temporary:
        baseline_root = Path(temporary)
        snapshot(baseline_root)
        plan = {"baseline_commit": BASELINE, "candidate_head": actual, "profile": args.profile,
                "fixture_sha256": digest(FIXTURES), "candidate_dirty": bool(subprocess.check_output(
                    ["git", "diff", "HEAD", "--name-only"], cwd=ROOT, text=True).strip()),
                "blocks": [], "plan_only": args.plan_only, "status": "incomplete"}
        plan["source_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                  for name in ("assistant/ai_engine/local.py", "assistant/policy.py",
                                               "config/settings.py", "scripts/voice_loop.py", "scripts/benchmark_inference_ab.py")}
        try:
            for number in range(1 if args.plan_only else args.rounds):
                for arm in (("A", "B") if number % 2 == 0 else ("B", "A")):
                    if not args.plan_only:
                        time.sleep(args.cooldown_seconds)
                    target = args.output / f"{number + 1}-{arm}.json"
                    plan["active_block"] = target.name
                    child_command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:],
                                     "--source-root", str(baseline_root if arm == "A" else ROOT),
                                     "--arm", arm, "--child-output", str(target), "--block-round", str(number + 1)]
                    run_child(child_command)
                    plan["blocks"].append(target.name)
            plan["status"] = "complete"
            plan.pop("active_block", None)
        finally:
            summarize(args.output, plan)
            (args.output / "comparison.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"Comparison reports: {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as failure:
        # Never dump exception strings that might include configured URLs/paths.
        print(f"A/B stopped: {type(failure).__name__}", file=sys.stderr)
        raise SystemExit(1)
