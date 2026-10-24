#!/usr/bin/env python3
"""Operator initiated, two request Pi 5 residency comparison. Never retries."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import test_short_answer_thermal as short
models, quality = short.models, short.quality
QUESTIONS = (short.QUESTION, "What is RAM in a computer? Answer in one short sentence.")
PREFIX = "a80c4f17acd5"
SESSION_SECONDS = 60
HTTP_TIMEOUTS = {"cold": 25, "warm": 20}


def gate(point, installed, residents, phase):
    if phase == "cold":
        short.preflight(point, installed, residents)
        if residents:
            raise models.TrialRefused("cold_requires_no_resident_model")
    else:
        if type(point.get("temperature_c")) in (int, float) and point["temperature_c"] >= 55:
            raise models.TrialRefused("warm_deferred_thermal")
        quality.memory_floor(point, installed, True)
        if not residents:
            raise models.TrialRefused("premature_model_expiry")


def summarize(sample, phase):
    short.classify(sample)
    sample["correctness_review"] = {"manual_review_required": True, "correct": None,
        "expected_concept": "New Delhi" if phase == "cold" else "RAM is temporary working memory for active data/programs"}
    sample["factual_screen_passed"] = bool(re.search(r"\bnew\s+delhi\b", sample.get("answer", ""), re.I)) if phase == "cold" else bool(re.search(r"\b(memory|temporary|volatile)\b", sample.get("answer", ""), re.I))
    sample["prompt_cache"] = models.prompt_accounting(sample.get("metrics", {}))
    sample["question_sha256"] = hashlib.sha256(QUESTIONS[phase == "warm"].encode()).hexdigest()
    messages = sample.get("request", {}).get("messages")
    sample["messages_sha256"] = hashlib.sha256(json.dumps(messages, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    sample["backend_rendered_prompt_sha256"] = None
    sample["template_prefix_disclosure"] = "No explicit system message or conversation history. Ollama model template may share prefixes; rendered prompt unavailable. Missing cache counters mean unknown, never zero."
    return sample.get("complete") is True


def worker(args):
    quality.require_pi()
    models.verify_identity(short.MODEL, models.inventory(args.host)[short.MODEL], PREFIX)
    phase = args.phase
    def check(point, installed, residents):
        quality.resident(args.host, short.MODEL, args.digest, phase)
        gate(point, installed, residents, phase)
        # Each subprocess is a worker start: retain the stricter <50 C gate.
        if point["temperature_c"] >= 50:
            raise models.TrialRefused("warm_deferred_thermal")
    return short.infer(args.host, args.digest, args.child_result,
                       question=QUESTIONS[phase == "warm"], gate=check,
                       http_timeout=HTTP_TIMEOUTS[phase], phase=phase)


def run(args, *, clock=time.monotonic):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = dict(schema_version=1, status="aborted", phases=[], telemetry=[],
                  manual_quality_review_required=True, session_limit_seconds=SESSION_SECONDS,
                  request_limit_seconds=short.RUNTIME_SECONDS, http_timeout_seconds=HTTP_TIMEOUTS, ratios=None)
    lock = None
    deadline = clock() + SESSION_SECONDS
    try:
        quality.require_pi()
        lock = models.acquire_trial_lock()
        from scripts.validate_pi_performance import isolated_database
        with isolated_database("warm-comparison-monitor-"):
            from scripts.benchmark_inference_ab import telemetry
            installed = models.inventory(args.host)[short.MODEL]
            digest = models.verify_identity(short.MODEL, installed, PREFIX)
            report.update(installed=installed, verified_digest=digest)
            for phase in ("cold", "warm"):
                entry = dict(phase=phase, telemetry=[], sample={}, status="aborted")
                report["phases"].append(entry)
                target = output / (phase + ".json")
                residents = quality.resident(args.host, short.MODEL, digest, phase)
                point = telemetry()
                entry.update(resident_before=residents, initial_temperature_c=point.get("temperature_c"))
                entry["telemetry"].append(point)
                gate(point, installed, residents, phase)
                models.verify_identity(short.MODEL, models.inventory(args.host)[short.MODEL], digest)
                remaining = min(short.RUNTIME_SECONDS, deadline - clock())
                if remaining <= 0:
                    raise models.TrialRefused("session_deadline")
                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--host", args.host,
                    "--child-result", str(target), "--digest", digest, "--phase", phase],
                    cwd=ROOT, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    try:
                        short.monitor(process, telemetry, lambda: quality.resident(args.host, short.MODEL, digest,
                            "warm" if phase == "warm" else None), entry, timeout=remaining)
                    except models.TrialRefused as exc:
                        if exc.reason == "bounded_cycle_timeout":
                            raise models.TrialRefused("session_deadline" if clock() >= deadline else "request_deadline") from exc
                        raise
                    # Load the actual child outcome before any post-request warm assertion.
                    entry["sample"] = short.read_sample(target)
                    entry["worker_returncode"] = process.returncode
                    failure = short.request_failure(entry["sample"], process.returncode)
                    try:
                        final = telemetry()
                    except Exception as exc:
                        entry["final_telemetry_error"] = type(exc).__name__
                        if failure:
                            raise models.TrialRefused(failure) from exc
                        raise
                    entry["telemetry"].append(final)
                    reason = models.pressure_reason(final)
                    if reason:
                        entry["final_pressure_reason"] = reason
                    if failure:
                        raise models.TrialRefused(failure)
                    if reason:
                        raise models.TrialRefused(reason)
                    if clock() >= deadline:
                        raise models.TrialRefused("session_deadline")
                    models.verify_identity(short.MODEL, models.inventory(args.host)[short.MODEL], digest)
                    entry["resident_after"] = quality.resident(args.host, short.MODEL, digest, "warm")
                    entry["status"] = "finished"
                finally:
                    models.terminate(process)
                    if target.is_file():
                        entry["sample"] = short.read_sample(target)
                    summarize(entry["sample"], phase)
                if not entry["sample"]["complete"] or not entry["sample"]["factual_screen_passed"]:
                    raise models.TrialRefused("factual_screen_failed")
            report["status"] = "complete_manual_review_pending"
    except BaseException as exc:
        report["abort_reason"] = exc.reason if isinstance(exc, models.TrialRefused) else "failed_or_cancelled"
        if report["phases"]:
            report["phases"][-1].update(status="aborted", abort_reason=report["abort_reason"])
        if report["abort_reason"] == "warm_deferred_thermal":
            report["status"] = "warm_deferred_thermal"
    finally:
        for entry in report["phases"]:
            temperatures = [p["temperature_c"] for p in entry["telemetry"] if type(p.get("temperature_c")) in (int, float)]
            entry["maximum_observed_temperature_c"] = max(temperatures) if temperatures else None
            entry["load"] = models.load_summary(entry["sample"].get("metrics_ms", {}), entry.get("resident_before", []))
        if report["status"] == "complete_manual_review_pending":
            cold, warm = report["phases"]
            if (cold["load"]["label"] == "cold" and warm["load"]["label"] == "warm"
                    and all(e["sample"]["prompt_cache"]["accounting_known"] for e in (cold, warm))):
                report["ratios"] = {key: cold["sample"][key] / warm["sample"][key]
                    for key in ("first_meaningful_text_ms", "total_response_seconds")
                    if type(warm["sample"].get(key)) in (int, float) and warm["sample"][key] > 0}
        report["interpretation"] = "Different short questions and token workloads; shared template/cache may contribute. Ratios are conditional observations pending factual review. No voice latency or sustained thermal stability claim."
        short.save(output / "report.json", report)
        if lock:
            lock.close()
    return 0 if report["status"] == "complete_manual_review_pending" else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--host", type=models.local_host, default="http://127.0.0.1:11434")
    parser.add_argument("--child-result", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--digest", help=argparse.SUPPRESS)
    parser.add_argument("--phase", choices=("cold", "warm"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child_result:
        if not args.digest or not args.phase:
            parser.error("Worker requires digest and phase")
        signal.signal(signal.SIGTERM, lambda *unused: (_ for _ in ()).throw(KeyboardInterrupt()))
        from scripts.validate_pi_performance import isolated_database
        with isolated_database("warm-comparison-worker-"):
            try:
                return worker(args)
            except BaseException as exc:
                sample = short.read_sample(args.child_result) if args.child_result.is_file() else {}
                sample.setdefault("abort_reason", exc.reason if isinstance(exc, models.TrialRefused) else
                                  "worker_cancelled" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "worker_failure")
                sample.setdefault("error_type", type(exc).__name__)
                sample.setdefault("error_detail", str(exc))
                short.save(args.child_result, sample)
                return 2
    if not args.output:
        parser.error("--output required")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
