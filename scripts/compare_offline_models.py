#!/usr/bin/env python3
"""Short single or explicit cold/warm trials; no downloads, audio or production DB writes."""
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


def distinct_fixture(fixture, phase):
    """Exactly two controlled questions; context comes only from the actual cold turn."""
    if phase == "cold":
        return {
            "followup": ("In at most 25 words, explain what an SSD stores.", "An SSD stores persistent files/data."),
            "memory": ("Remember our project codename is Amber Finch. Briefly confirm.", "Acknowledge Amber Finch without inventing project details."),
        }.get(fixture, FIXTURES["fact"])
    return {
        "fact": ("In at most 25 words, which planet is called the Red Planet?", "Mars; no invented explanation."),
        "followup": ("What about its data when power is off?", "Resolve SSD from the real preceding turn; its data persists without power."),
        "memory": ("Recall project codename.", "Retrieve Amber Finch from the actual cold user statement, with no fabricated extra history."),
    }.get(fixture, FIXTURES[fixture])


def prompt_accounting(metrics):
    total, cached = metrics.get("prompt_eval_count"), metrics.get("prompt_eval_cached_count")
    known = type(total) is int and total > 0 and type(cached) is int and 0 <= cached <= total
    return {"total_tokens_reported": total, "cached_tokens_reported": cached,
            "uncached_tokens_derived": total - cached if known else None,
            "cache_fraction": round(cached / total, 6) if known else None,
            "accounting_known": known,
            "definition": "Uncached is total minus cached, assuming the installed backend reports prompt_eval_count as total prompt tokens; verify runtime counter semantics."}


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


def verify_request_residency(resident, model, digest, phase=None):
    verify_residents(resident, model, digest)
    if phase == "cold" and resident:
        raise TrialRefused("cold_requires_no_resident_model")
    if phase == "warm":
        if not resident:
            raise TrialRefused("premature_model_expiry")
        if resident[0].get("context_length") != 4096:
            raise TrialRefused("warm_context_changed")


def validate_cold_warm(samples, *, distinct=False):
    if len(samples) != 2 or any(s.get("status") != "sample_complete_quality_pending" for s in samples):
        return "incomplete_cold_warm_pair"
    cold, warm = samples
    if cold.get("phase") != "cold" or warm.get("phase") != "warm":
        return "invalid_phase_order"
    if cold.get("load", {}).get("label") != "cold" or warm.get("load", {}).get("label") != "warm":
        return "model_reloaded_or_load_unverified"
    a, b = cold.get("sample", {}), warm.get("sample", {})
    if distinct:
        if (not a.get("question") or not b.get("question") or a["question"] == b["question"]
                or a.get("request", {}).get("message_sha256") == b.get("request", {}).get("message_sha256")):
            return "warm_question_not_distinct"
        if any(s.get("request", {}).get("user_sha256") != sha(s["question"]) for s in (a, b)):
            return "question_payload_mismatch"
        for key in ("options", "model", "think", "keep_alive"):
            if key not in a.get("request", {}) or a["request"][key] != b.get("request", {}).get(key):
                return "unequal_distinct_request_controls"
    elif a.get("request") != b.get("request") or not a.get("request"):
        return "unequal_cold_warm_request"
    policy_keys = ("num_predict", "resource_profile") if distinct else ("num_predict", "route", "resource_profile", "rag_used", "memory_used", "recent_history_used")
    for key in policy_keys:
        if key not in a or key not in b or a[key] != b[key]:
            return "unequal_cold_warm_policy"
    for sample in (a, b):
        metrics = sample.get("metrics", {})
        for key in ("load_duration_ms", "prompt_eval_duration_ms", "eval_duration_ms", "eval_count", "prompt_eval_count", "tokens_per_sec"):
            value = metrics.get(key)
            if type(value) not in (int, float) or not 0 <= value < float("inf"):
                return "incomplete_backend_metrics"
        meaningful = sample.get("first_meaningful_text_ms")
        if type(meaningful) not in (int, float) or not 0 <= meaningful < float("inf"):
            return "meaningful_text_timing_missing"
        if (not sample.get("answer") or type(sample.get("total_answer_s")) not in (int, float)
                or not 0 < sample["total_answer_s"] < float("inf")
                or any(metrics[key] <= 0 for key in ("eval_count", "prompt_eval_count", "eval_duration_ms", "tokens_per_sec"))):
            return "incomplete_backend_metrics"
        if metrics.get("done_reason") != "stop" or metrics["eval_count"] >= sample["num_predict"]:
            return "truncated_or_unfinished_answer"
        cached = metrics.get("prompt_eval_cached_count")
        if type(cached) is not int or cached < 0:
            return "prompt_cache_unverified"
        if distinct and not prompt_accounting(metrics)["accounting_known"]:
            return "invalid_prompt_cache_accounting"
        if cached and not distinct:
            return "prompt_cache_reuse"
    if distinct:
        if prompt_accounting(b["metrics"])["uncached_tokens_derived"] <= 0:
            return "no_new_prompt_work"
        app_first = b.get("application_first_meaningful_text_ms")
        if type(app_first) not in (int, float) or not 0 <= app_first < float("inf"):
            return "application_timing_missing"
    elif a["metrics"]["prompt_eval_count"] != b["metrics"]["prompt_eval_count"]:
        return "evaluated_prompt_work_changed"
    return None


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
    if getattr(args, "required_residency", None):
        # The service passes its policy timeout explicitly, overriding the engine.
        settings.AI_LOCAL_GENERATION_TIMEOUT_SECONDS = 20
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
        distinct = getattr(args, "warm_distinct", False)
        previous_turn = getattr(args, "previous_turn", None)
        if distinct and previous_turn:
            turn = json.loads(Path(previous_turn).read_text(encoding="utf-8"))
            if not all(isinstance(turn.get(k), str) and 0 < len(turn[k]) <= 8192 for k in ("question", "answer")):
                raise TrialRefused("invalid_observed_history")
            Message.objects.create(conversation=conversation, sender="USER", text=turn["question"])
            Message.objects.create(conversation=conversation, sender="AI", text=turn["answer"])
        if args.fixture == "followup" and not distinct:
            Message.objects.create(conversation=conversation, sender="USER", text="We are comparing RAM and an SSD.")
            Message.objects.create(conversation=conversation, sender="AI", text="RAM holds working data; an SSD stores files.")
        if args.fixture == "memory" and not distinct:
            Message.objects.create(conversation=conversation, sender="USER", text="Our project codename is Amber Finch.")
            Message.objects.bulk_create([Message(conversation=conversation, sender="USER", text="Synthetic unrelated context.") for _ in range(6)])
        reset_engine()
        engine = get_engine()
        original = engine._build_chat_request
        requests = []
        measured = {}

        def build(*positional, **keyword):
            # Recheck immediately before inference, after migrations/policy work.
            probe_started = time.perf_counter()
            installed = inventory(args.host)[args.model]
            digest = verify_identity(args.model, installed, args.verified_digest)
            verify_request_residency(metadata(args.host, "/api/ps").get("models", []), args.model, digest,
                                     getattr(args, "required_residency", None))
            measured["identity_probe_ms"] = round((time.perf_counter() - probe_started) * 1000, 3)
            request = original(*positional, **keyword)
            payload = json.loads(request.data)
            # Retain the service's protective effective generation cap.
            controls = request_options()
            controls["num_predict"] = min(controls["num_predict"], payload["options"]["num_predict"])
            payload["options"] = controls
            request.data = json.dumps(payload).encode()
            requests.append({"message_sha256": sha(payload["messages"]), "options": controls,
                             "system_sha256": sha([m["content"] for m in payload["messages"] if m["role"] == "system"]),
                             "user_sha256": sha(payload["messages"][-1]["content"]),
                             "model": payload["model"], "think": payload["think"], "keep_alive": payload["keep_alive"]})
            measured["request_started"] = time.perf_counter()
            return request

        engine._build_chat_request = build
        original_stream = engine.generate_stream

        def stream(*positional, **keyword):
            for event in original_stream(*positional, **keyword):
                if event.type == "delta" and any(char.isalnum() for char in event.text):
                    measured.setdefault("first_meaningful_text_ms", round((time.perf_counter() - measured["request_started"]) * 1000, 3))
                    measured.setdefault("application_first_meaningful_text_ms", round((time.perf_counter() - measured["service_started"]) * 1000, 3))
                yield event

        engine.generate_stream = stream
        # Disable HTTP proxies and redirects for inference, including localhost.
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()))
        query, reference = distinct_fixture(args.fixture, args.required_residency) if distinct else FIXTURES[args.fixture]
        plan = replace(AssistantResponsePolicy.plan_voice_response(query), num_predict=48)
        plan = replace(plan, system_instruction=plan.system_instruction + " Answer in at most 25 words.")
        if args.fixture == "followup" and not distinct:
            plan = replace(plan, recent_history_limit=2)
        started = time.perf_counter()
        measured["service_started"] = started
        done = None
        for event in AssistantService.process_message_stream(query, conversation_id=conversation.pk, response_plan=plan):
            if event["type"] == "error":
                raise RuntimeError("Inference failed")
            if event["type"] == "done":
                done = event
        if (not done or done.get("engine") != "local" or len(requests) != 1
                or done.get("model") != args.model or done.get("online_used")):
            raise RuntimeError("Not one local inference result")
        warm_distinct = distinct and args.required_residency == "warm"
        if args.fixture == "rag" and (not distinct or warm_distinct) and not done.get("rag_used"):
            raise RuntimeError("RAG fixture did not use retrieval")
        if args.fixture == "memory" and (not distinct or warm_distinct) and not done.get("memory_used"):
            raise RuntimeError("Memory fixture did not use retrieval")
        if warm_distinct and args.fixture == "followup" and not done.get("recent_history_used"):
            raise TrialRefused("followup_history_not_used")
        metrics = done.get("ollama_metrics", {})
        if not metrics.get("eval_count") or not metrics.get("eval_duration_ms"):
            raise RuntimeError("Missing generated-token timing")
        total_s = time.perf_counter() - started
        result = {"answer": done["response"] if distinct else done["response"][:8192], "question": query, "request": requests[0],
                  "first_meaningful_text_ms": measured.get("first_meaningful_text_ms"),
                  "application_first_meaningful_text_ms": measured.get("application_first_meaningful_text_ms"),
                  "first_text_token_ms": done.get("ttft_ms"), "total_answer_s": total_s,
                  "engine_stream_ms": done.get("generation_ms"),
                  "benchmark_identity_probe_ms": measured.get("identity_probe_ms"),
                  "application_outside_engine_ms": round(total_s * 1000 - done["generation_ms"], 3) if type(done.get("generation_ms")) in (int, float) else None,
                  "prompt_cache": prompt_accounting(metrics),
                  "metrics": metrics, "num_predict": done.get("num_predict"),
                  "route": done.get("route"), "rag_used": done.get("rag_used"),
                  "memory_used": done.get("memory_used"), "recent_history_used": done.get("recent_history_used"),
                  "resource_profile": done.get("resource_profile"), "manual_quality": None,
                  "correctness_review": {"reference": reference, "correct": None, "complete": None,
                                         "note": "Human review required; no automatic factual-quality acceptance."}}
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


def run_request(args, digest, report, phase=None):
    from scripts.validate_pi_performance import isolated_database
    from scripts.benchmark_inference_ab import telemetry
    with isolated_database("offline-monitor-"):
        first = telemetry()
        report["telemetry"].append(first)
        reason = pressure_reason(first, starting=phase != "warm")
        if reason:
            report["abort_reason"] = reason
            raise RuntimeError("Cool healthy start required")
        with tempfile.TemporaryDirectory(prefix="offline-single-") as temporary:
            target = Path(temporary) / "result.json"
            command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:],
                       "--child-dir", temporary, "--child-result", str(target)]
            command += ["--verified-digest", digest]
            if phase:
                command += ["--required-residency", phase]
            if report.get("previous_turn"):
                history_path = Path(temporary) / "observed-turn.json"
                history_path.write_text(json.dumps(report["previous_turn"]), encoding="utf-8")
                command += ["--previous-turn", str(history_path)]
            process = subprocess.Popen(command, cwd=ROOT, start_new_session=True,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                start = time.monotonic()
                while process.poll() is None:
                    sample = telemetry()
                    report["telemetry"].append(sample)
                    reason = pressure_reason(sample)
                    if reason or time.monotonic() - start >= 45:
                        report["abort_reason"] = reason or "45_second_wall_deadline"
                        raise RuntimeError("Safety stop")
                    verify_request_residency(metadata(args.host, "/api/ps").get("models", []), args.model, digest,
                                             "warm" if phase == "warm" else None)
                    time.sleep(0.5)
                if time.monotonic() - start >= 45:
                    report["abort_reason"] = "45_second_wall_deadline"
                    raise RuntimeError("Request exceeded its wall deadline")
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
    verify_request_residency(resident, args.model, digest, "warm" if phase else None)
    if len(resident) != 1:
        raise RuntimeError("Resident model identity not confirmed")
    report["status"] = "sample_complete_quality_pending"


def run_cold_warm(args, digest, report, save):
    distinct = getattr(args, "warm_distinct", False)
    kind = "warm_distinct" if distinct else "cold_warm"
    report.update(benchmark=kind, valid=False, samples=[], status="incomplete_" + kind,
                  uncached_warm_under_two_seconds=None, quality_accepted=False)
    if distinct:
        report["warm_distinct_under_two_seconds"] = None
        report["unseen_scope"] = "Warm question differs from this invocation's cold question; no claim that the device has never encountered it before."
    for phase in ("cold", "warm"):
        phase_report = {"phase": phase, "status": "incomplete", "telemetry": [], "inventory": report["inventory"]}
        report["samples"].append(phase_report)
        if distinct and phase == "warm" and args.fixture in ("followup", "memory"):
            cold_sample = report["samples"][0]["sample"]
            phase_report["previous_turn"] = {"question": cold_sample["question"], "answer": cold_sample["answer"]}
            phase_report["history_provenance"] = "Only the actual cold question and generated answer; no invented user messages."
        save()  # A partial pair is never reported as a successful benchmark.
        try:
            resident = metadata(args.host, "/api/ps").get("models", [])
            verify_request_residency(resident, args.model, digest, phase)
            phase_report["resident_before"] = [{k: item.get(k) for k in ("name", "digest", "size", "size_vram", "context_length")} for item in resident]
            run_request(args, digest, phase_report, phase)
            if phase == "cold":
                sample = phase_report.get("sample", {})
                metrics = sample.get("metrics", {})
                if (phase_report.get("status") != "sample_complete_quality_pending"
                        or phase_report.get("load", {}).get("label") != "cold"
                        or not sample.get("answer") or metrics.get("done_reason") != "stop"
                        or metrics.get("eval_count", 48) >= sample.get("num_predict", 48)):
                    raise TrialRefused("cold_request_failed_validation")
        except BaseException:
            phase_report["status"] = "aborted"
            save()
            raise
        save()
    reason = validate_cold_warm(report["samples"], distinct=distinct)
    if reason:
        report["invalid_reason"] = reason
        report["cache_status"] = reason if reason.startswith("prompt_cache") else "not_accepted"
        raise TrialRefused(reason)
    warm = report["samples"][1]["sample"]
    if distinct:
        report.update(status="warm_distinct_complete_quality_pending", valid=True,
                      cache_status="quantified_prefix_reuse_not_assumed_uncached",
                      warm_distinct_under_two_seconds=warm["application_first_meaningful_text_ms"] < 2000,
                      interpretation="One different question with quantified prefix caching; not proof of general conversation quality, zero-cache speed or physical speech latency.")
        save()
        return
    report.update(status="cold_warm_complete_quality_pending", valid=True,
                  cache_status="reported_zero_hits_and_equal_prompt_work",
                  uncached_warm_under_two_seconds=warm["first_meaningful_text_ms"] < 2000,
                  interpretation="Independent repeated-question warm measurement; human correctness and broader conversational/physical voice validation still required.")
    save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inventory", action="store_true")
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--cold-warm", action="store_true", help="Exactly two factual requests: empty-residency cold, then independently timed warm")
    mode.add_argument("--warm-distinct", action="store_true", help="One cold initializer and one different warm question; quantify prefix-cache reuse")
    mode.add_argument("--compare", type=Path, nargs=2, metavar=("A_REPORT", "B_REPORT"))
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--model", choices=MODELS)
    parser.add_argument("--expected-digest", help="Optional full digest or at least 12 hexadecimal prefix characters")
    parser.add_argument("--fixture", choices=FIXTURES, default="fact")
    parser.add_argument("--output", type=Path, required=True, help="New directory only")
    parser.add_argument("--child-dir", help=argparse.SUPPRESS)
    parser.add_argument("--child-result", help=argparse.SUPPRESS)
    parser.add_argument("--verified-digest", help=argparse.SUPPRESS)
    parser.add_argument("--required-residency", choices=("cold", "warm"), help=argparse.SUPPRESS)
    parser.add_argument("--previous-turn", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.host = local_host(args.host)
    if args.expected_digest and not re.fullmatch(r"(?:sha256:)?[a-f0-9]{12,64}", args.expected_digest):
        parser.error("Expected digest must be a full digest or a 12-64 character hex prefix")
    if args.child_dir:
        child(args)
        return
    paired_mode = args.cold_warm or args.warm_distinct
    inference_mode = args.run or paired_mode
    if args.cold_warm and args.fixture != "fact":
        parser.error("Cold/warm mode uses only the fixed factual question")
    if inference_mode and (not args.model or platform.system() != "Linux"):
        parser.error("One explicit installed model and Linux are required")
    if inference_mode and subprocess.run(["git", "merge-base", "--is-ancestor", BASELINE, "HEAD"], cwd=ROOT).returncode:
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
    if paired_mode:
        report.update(benchmark="warm_distinct" if args.warm_distinct else "cold_warm", valid=False, samples=[], uncached_warm_under_two_seconds=None)
        if args.warm_distinct:
            report["warm_distinct_under_two_seconds"] = None
    lock = None
    try:
        if inference_mode:
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
        verify_request_residency(resident, args.model, digest, "cold" if paired_mode else None)
        report["resident_before"] = [{k: item.get(k) for k in ("name", "digest", "size", "size_vram", "context_length")} for item in resident]
        if paired_mode:
            run_cold_warm(args, digest, report, lambda: output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8"))
        else:
            run_request(args, digest, report)
    except BaseException as error:
        report["status"] = "incomplete_warm_distinct" if args.warm_distinct else ("invalid_cold_warm" if args.cold_warm else "aborted")
        if paired_mode:
            report["valid"] = False
            report["uncached_warm_under_two_seconds"] = None
            if args.warm_distinct:
                report["warm_distinct_under_two_seconds"] = None
                if report.get("invalid_reason"):
                    report["status"] = "invalid_warm_distinct"
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
