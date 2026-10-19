#!/usr/bin/env python3
"""One synthetic text request per invocation; no downloads, audio or production DB writes."""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASELINE = "c4c5c442df0972ad177b038a4e9f7f4c540c5604"
MODELS = ("qwen3.5:4b-q4_K_M", "llama3.2:1b", "qwen3.5:2b-q4_K_M", "qwen3.5:0.8b-q8_0")
CONFIRMED_PREFIXES = {MODELS[0]: "d8b0f5e9760c", MODELS[1]: "baf6a787fdff"}


class TrialRefused(RuntimeError):
    """Fixed reason codes suitable for reports; no backend/configuration strings."""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def acquire_trial_lock():
    """Serialize this harness on Linux; never lock, stop or configure the app."""
    import fcntl
    path = Path(tempfile.gettempdir()) / "smart-companion-offline-model.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    handle = os.fdopen(descriptor, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        handle.close()
        raise TrialRefused("another_benchmark_or_lock_unavailable")
    return handle  # Closing releases the kernel lock, including after exceptions.
# Every output has the same 48-token ceiling; no long/detailed generation here.
FIXTURES = {
    "fact": ("In at most 25 words, name India's capital and say whether it is north or south of the equator.",
             "New Delhi; north of the equator. Both requested facts must be present."),
    "reasoning": ("In at most 25 words: a box has 3 red and 2 blue balls. Two red are removed. What fraction left is blue? Explain.",
                  "2/3: one red and two blue remain, three total."),
    "followup": ("In at most 25 words, which one keeps its data when power is off, and why?",
                 "Resolve supplied RAM/SSD context: SSD retains data; RAM is volatile."),
    "technical": ("In at most 25 words, distinguish a CPU clock frequency from instructions executed per second.",
                  "Cycles per second versus instructions per second; IPC/workload matters, they are not interchangeable."),
    "rag": ("According to my notes, what voltage does the Lumen board use?",
            "Use the retrieved synthetic note: 3.3 V; direct 5 V signal unsafe. No invented specifications."),
    "memory": ("What project codename did I mention earlier?",
               "Use retrieved conversation memory: Amber Finch. Do not invent additional project details."),
}


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def local_host(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Only a loopback HTTP Ollama endpoint is allowed")
    return value.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *unused, **kwargs):
        raise ValueError("Ollama redirects are not permitted")


def metadata(host, endpoint):
    # Ignore proxy environment: inference/metadata must stay on this computer.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(host + endpoint, timeout=3) as response:
        if urlsplit(response.geturl()).netloc != urlsplit(host).netloc:
            raise ValueError("Redirected Ollama endpoint")
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("Oversized metadata")
    return json.loads(raw)


def inventory(host):
    tags = metadata(host, "/api/tags").get("models", [])
    selected = {}
    for name in MODELS:
        matches = [m for m in tags if m.get("name") == name]
        item = matches[0] if len(matches) == 1 else {}
        digest = item.get("digest", "")
        confirmed = bool(re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", digest))
        local = (item.get("details", {}).get("format") == "gguf"
                 and not item.get("remote_model") and not item.get("remote_host"))
        selected[name] = {"installed": bool(item), "usable": confirmed and local,
                          "digest": digest if confirmed else None, "artifact_bytes": item.get("size"),
                          "details": {k: item.get("details", {}).get(k)
                                      for k in ("format", "family", "parameter_size", "quantization_level")}}
    return selected


def pressure_reason(sample, *, starting=False):
    flags = sample.get("throttled")
    temperature = sample.get("temperature_c")
    keys = ("under_voltage", "frequency_capped", "currently_throttled", "soft_temperature_limit")
    if (type(temperature) not in (int, float) or not 0 <= temperature < 100
            or not isinstance(flags, dict) or any(type(flags.get(k)) is not bool for k in keys)
            or type(sample.get("measured_arm_hz")) is not int or sample["measured_arm_hz"] <= 0
            or type(sample.get("ram_available_bytes")) is not int):
        return "required_telemetry_unavailable"
    if sample["ram_available_bytes"] < 512 * 1024 ** 2:
        return "low_available_memory"
    if any(flags[k] for k in keys):
        return "current_power_or_throttle_pressure"
    if temperature >= (55 if starting else 70):
        return "cool_start_required" if starting else "temperature_safety_margin"
    return None


def request_options():
    return {"seed": 42, "temperature": 0, "num_ctx": 4096, "num_predict": 48}


def verify_identity(model, installed, expected=None):
    if not installed.get("usable"):
        raise TrialRefused("exact_local_model_unavailable")
    digest = installed["digest"].removeprefix("sha256:")
    for pin in (CONFIRMED_PREFIXES.get(model), expected):
        if pin and not digest.startswith(pin.removeprefix("sha256:")):
            raise TrialRefused("expected_model_digest_mismatch")
    return digest


def verify_residents(resident, model, digest):
    if len(resident) > 1 or any(item.get("name") != model for item in resident):
        raise TrialRefused("other_model_resident_no_unload_attempted")
    if resident and resident[0].get("digest", "").removeprefix("sha256:") != digest:
        raise TrialRefused("resident_digest_mismatch")


def load_summary(metrics, resident):
    load = metrics.get("load_duration_ms")
    label = "unknown"
    if type(load) in (int, float) and load >= 0:
        if not resident:
            label = "cold"
        elif resident[0].get("context_length") == 4096 and load <= 100:
            label = "warm"
        else:
            label = "resident_reload_or_slow_load"
    return {"label": label, "resident_before_request": bool(resident),
            "load_duration_ms": load, "prompt_eval_duration_ms": metrics.get("prompt_eval_duration_ms"),
            "decode_duration_ms": metrics.get("eval_duration_ms"),
            "definition": "Warm is an operational label: matching resident context and load <=100 ms; not proof of prompt-cache reuse."}


def compare_reports(a, b):
    """Only matching, completed samples form a pair; quality still needs a human."""
    differences = {}
    for key in ("fixture", "workload_sha256", "source_sha256", "head", "tracked_diff_sha256",
                "output_mode", "options", "ollama_version"):
        if a.get(key) != b.get(key) or a.get(key) is None:
            differences[key] = [a.get(key), b.get(key)]
    sa, sb = a.get("sample", {}), b.get("sample", {})
    labels = [report.get("load", {}).get("label", "unknown") for report in (a, b)]
    if labels[0] != labels[1] or any(label not in ("cold", "warm") for label in labels):
        differences["load_label"] = labels
    for key in ("num_predict", "route", "resource_profile", "rag_used", "memory_used", "recent_history_used"):
        if sa.get(key) != sb.get(key) or key not in sa or key not in sb:
            differences[key] = [sa.get(key), sb.get(key)]
    for key in ("message_sha256", "options", "think", "keep_alive"):
        left, right = sa.get("request", {}).get(key), sb.get("request", {}).get(key)
        if left != right or left is None:
            differences["request_" + key] = [left, right]
    warm = [bool(report.get("resident_before")) for report in (a, b)]
    if warm[0] != warm[1]:
        differences["resident_before"] = warm
    temperatures = [(report.get("telemetry") or [{}])[0].get("temperature_c") for report in (a, b)]
    if any(type(t) not in (int, float) for t in temperatures) or abs(temperatures[0] - temperatures[1]) > 3:
        differences["starting_temperature_c"] = temperatures
    completed = all(report.get("status") == "sample_complete_quality_pending" for report in (a, b))
    return {"status": "comparison_pending_quality" if completed and not differences else "invalid_pair",
            "unequal_conditions": differences, "both_samples_completed": completed,
            "quality_accepted": False, "models": [a.get("model"), b.get("model")],
            "observed_conditions": [{"telemetry": report.get("telemetry"),
                                     "resident_before": report.get("resident_before"),
                                     "resident_after": report.get("resident_after"),
                                     "sample": report.get("sample")}
                                    for report in (a, b)],
            "note": "Different models/templates/tokenizers are intentional. Token counts and memory are measured, not held equal. Review reload/load durations, telemetry and every answer manually."}


def child(args):
    # Parent owns and removes this exact directory even after forced termination.
    from scripts.validate_pi_performance import bootstrap
    bootstrap(Path(args.child_dir))
    from django.conf import settings
    settings.AI_ENGINE = "local"
    settings.OLLAMA_HOST = args.host
    settings.OLLAMA_MODEL = args.model
    settings.OLLAMA_NUM_THREAD = settings.OLLAMA_NUM_BATCH = None
    settings.OLLAMA_TIMEOUT = 20
    settings.OLLAMA_KEEP_ALIVE = "60s"  # Request-local idle expiry; no explicit unload.
    settings.OLLAMA_THINK = False
    settings.VOICE_COMPACT_STYLE = False
    settings.ONLINE_RETRIEVAL_ENABLED = False
    settings.ONLINE_KNOWLEDGE_AUTO_PERSIST = False
    settings.DEVICE_CONTROL_ENABLED = settings.COMPANION_DISPLAY_ENABLED = False
    settings.MQTT_HOST = "127.0.0.1"  # Status probes cannot contact a configured remote broker.
    settings.SEARXNG_BASE_URL = "http://127.0.0.1:8888"
    settings.RAG_ENABLED = True
    from django.core.management import call_command
    from django.db import connections
    from assistant.ai_engine import get_engine, reset_engine
    from assistant.policy import AssistantResponsePolicy
    from assistant.services import AssistantService
    from conversations.models import Conversation, Message
    from knowledge_base.models import Document, KnowledgeChunk
    try:
        call_command("migrate", verbosity=0, interactive=False)
        doc = Document.objects.create(filename="synthetic-offline.md", status="INDEXED", source_identifier="audit:synthetic")
        KnowledgeChunk.objects.create(document=doc, chunk_index=0,
                                     content="The Lumen board uses 3.3 volts. Do not connect it directly to a 5 volt signal.")
        conversation = Conversation.objects.create(title="Synthetic offline comparison")
        if args.fixture == "followup":
            Message.objects.create(conversation=conversation, sender="USER", text="We are comparing RAM and an SSD.")
            Message.objects.create(conversation=conversation, sender="AI", text="RAM holds working data; an SSD stores files.")
        if args.fixture == "memory":
            Message.objects.create(conversation=conversation, sender="USER", text="Our project codename is Amber Finch.")
            Message.objects.bulk_create([Message(conversation=conversation, sender="USER", text="Synthetic unrelated context.") for _ in range(6)])
        reset_engine()
        engine = get_engine()
        original = engine._build_chat_request
        requests = []

        def build(*positional, **keyword):
            # Recheck immediately before inference, after migrations/policy work.
            installed = inventory(args.host)[args.model]
            digest = verify_identity(args.model, installed, args.verified_digest)
            verify_residents(metadata(args.host, "/api/ps").get("models", []), args.model, digest)
            request = original(*positional, **keyword)
            payload = json.loads(request.data)
            # Retain the service's protective effective generation cap.
            controls = request_options()
            controls["num_predict"] = min(controls["num_predict"], payload["options"]["num_predict"])
            payload["options"] = controls
            request.data = json.dumps(payload).encode()
            requests.append({"message_sha256": sha(payload["messages"]), "options": controls,
                             "model": payload["model"], "think": payload["think"], "keep_alive": payload["keep_alive"]})
            return request

        engine._build_chat_request = build
        # Disable HTTP proxies and redirects for inference, including localhost.
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()))
        query = FIXTURES[args.fixture][0]
        plan = replace(AssistantResponsePolicy.plan_voice_response(query), num_predict=48)
        plan = replace(plan, system_instruction=plan.system_instruction + " Answer in at most 25 words.")
        if args.fixture == "followup":
            plan = replace(plan, recent_history_limit=2)
        started = time.perf_counter()
        done = None
        for event in AssistantService.process_message_stream(query, conversation_id=conversation.pk, response_plan=plan):
            if event["type"] == "error":
                raise RuntimeError("Inference failed")
            if event["type"] == "done":
                done = event
        if (not done or done.get("engine") != "local" or len(requests) != 1
                or done.get("model") != args.model or done.get("online_used")):
            raise RuntimeError("Not one local inference result")
        if args.fixture == "rag" and not done.get("rag_used"):
            raise RuntimeError("RAG fixture did not use retrieval")
        if args.fixture == "memory" and not done.get("memory_used"):
            raise RuntimeError("Memory fixture did not use retrieval")
        metrics = done.get("ollama_metrics", {})
        if not metrics.get("eval_count") or not metrics.get("eval_duration_ms"):
            raise RuntimeError("Missing generated-token timing")
        result = {"answer": done["response"][:8192], "request": requests[0],
                  "first_text_token_ms": done.get("ttft_ms"), "total_answer_s": time.perf_counter() - started,
                  "metrics": metrics, "num_predict": done.get("num_predict"),
                  "route": done.get("route"), "rag_used": done.get("rag_used"),
                  "memory_used": done.get("memory_used"), "recent_history_used": done.get("recent_history_used"),
                  "resource_profile": done.get("resource_profile"), "manual_quality": None}
        Path(args.child_result).write_text(json.dumps(result, indent=2))
    finally:
        connections.close_all()


def terminate(process):
    # Only the fresh session owned by this invocation; never stop Ollama/services.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inventory", action="store_true")
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--compare", type=Path, nargs=2, metavar=("A_REPORT", "B_REPORT"))
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--model", choices=MODELS)
    parser.add_argument("--expected-digest", help="Optional full digest or at least 12 hexadecimal prefix characters")
    parser.add_argument("--fixture", choices=FIXTURES, default="fact")
    parser.add_argument("--output", type=Path, required=True, help="New directory only")
    parser.add_argument("--child-dir", help=argparse.SUPPRESS)
    parser.add_argument("--child-result", help=argparse.SUPPRESS)
    parser.add_argument("--verified-digest", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.host = local_host(args.host)
    if args.expected_digest and not re.fullmatch(r"(?:sha256:)?[a-f0-9]{12,64}", args.expected_digest):
        parser.error("Expected digest must be a full digest or a 12-64 character hex prefix")
    if args.child_dir:
        child(args)
        return
    if args.run and (not args.model or platform.system() != "Linux"):
        parser.error("One explicit installed model and Linux are required")
    if args.run and subprocess.run(["git", "merge-base", "--is-ancestor", BASELINE, "HEAD"], cwd=ROOT).returncode:
        parser.error("Accepted performance baseline must be an ancestor")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"status": "incomplete", "baseline": BASELINE, "model": args.model, "fixture": args.fixture,
              "workload_sha256": sha(FIXTURES), "output_mode": "text only; no STT/TTS/playback",
              "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "tracked_diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "HEAD"], cwd=ROOT)).hexdigest(),
              "options": request_options(), "quality_accepted": False, "telemetry": []}
    output = args.output / "report.json"
    lock = None
    try:
        if args.run:
            lock = acquire_trial_lock()
        if args.compare:
            report = compare_reports(*(json.loads(path.read_text()) for path in args.compare))
            return
        if args.plan_only:
            report.update(status="plan_only", fixtures=FIXTURES, installed_availability="unverified")
            return
        report["inventory"] = inventory(args.host)
        version = metadata(args.host, "/api/version").get("version")
        report["ollama_version"] = version if isinstance(version, str) and re.fullmatch(r"[a-zA-Z0-9.+_-]{1,80}", version) else None
        if args.inventory:
            report["status"] = "inventory_only"
            return
        digest = verify_identity(args.model, report["inventory"][args.model], args.expected_digest)
        report["verified_digest"] = digest
        report["expected_digest"] = args.expected_digest or CONFIRMED_PREFIXES.get(args.model)
        resident = metadata(args.host, "/api/ps").get("models", [])
        # No second resident LLM dependency. Do not evict another app's runner.
        verify_residents(resident, args.model, digest)
        report["resident_before"] = [{k: item.get(k) for k in ("name", "digest", "size", "size_vram", "context_length")} for item in resident]
        from scripts.validate_pi_performance import isolated_database
        from scripts.benchmark_inference_ab import telemetry
        with isolated_database("offline-monitor-"):
            first = telemetry()
            report["telemetry"].append(first)
            reason = pressure_reason(first, starting=True)
            if reason:
                report["abort_reason"] = reason
                raise RuntimeError("Cool healthy start required")
            with tempfile.TemporaryDirectory(prefix="offline-single-") as temporary:
                target = Path(temporary) / "result.json"
                command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:],
                           "--child-dir", temporary, "--child-result", str(target)]
                command += ["--verified-digest", digest]
                process = subprocess.Popen(command, cwd=ROOT, start_new_session=True,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                start = time.monotonic()
                try:
                    while process.poll() is None:
                        sample = telemetry()
                        report["telemetry"].append(sample)
                        reason = pressure_reason(sample)
                        if reason or time.monotonic() - start >= 45:
                            report["abort_reason"] = reason or "45_second_wall_deadline"
                            raise RuntimeError("Safety stop")
                        verify_residents(metadata(args.host, "/api/ps").get("models", []), args.model, digest)
                        time.sleep(0.5)
                    if process.returncode or not target.is_file():
                        report["child_exit_code"] = process.returncode
                        raise RuntimeError("Isolated request failed; not a valid timing sample")
                    final = telemetry()
                    report["telemetry"].append(final)
                    if pressure_reason(final):
                        report["abort_reason"] = pressure_reason(final)
                        raise RuntimeError("Final safety check failed")
                    report["sample"] = json.loads(target.read_text())
                    report["load"] = load_summary(report["sample"]["metrics"], report["resident_before"])
                finally:
                    terminate(process)
        after = inventory(args.host)
        if after != report["inventory"]:
            raise RuntimeError("Installed inventory changed during sample")
        resident = metadata(args.host, "/api/ps").get("models", [])
        report["resident_after"] = [{k: item.get(k) for k in ("name", "digest", "size", "size_vram", "context_length")} for item in resident]
        verify_residents(resident, args.model, digest)
        if len(resident) != 1:
            raise RuntimeError("Resident model identity not confirmed")
        report["status"] = "sample_complete_quality_pending"
    except BaseException as error:
        report["status"] = "aborted"
        report["error_type"] = type(error).__name__
        if isinstance(error, TrialRefused):
            report["abort_reason"] = error.reason
        raise
    finally:
        if lock is not None:
            lock.close()
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(f"Offline comparison stopped: {type(error).__name__}; inspect report.json", file=sys.stderr)
        raise SystemExit(1)
