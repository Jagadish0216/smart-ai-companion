#!/usr/bin/env python3
"""One direct local Ollama text smoke test; no application DB, audio or deployment."""
import argparse
import json
from pathlib import Path
import re
import signal
import socket
import urllib.error
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import compare_offline_models as models
from scripts import evaluate_offline_quality as quality
from scripts.validate_offline_voice import monitor

MODEL = "llama3.2:3b"
QUESTION = "What is the capital of India? Answer in one short sentence."
OPTIONS = dict(seed=42, temperature=0, num_ctx=4096, num_predict=32)
RUNTIME_SECONDS = 30


def save(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def preflight(point, installed, residents):
    quality.memory_floor(point, installed, bool(residents))
    if point["temperature_c"] >= 50:
        raise models.TrialRefused("below_50_cool_start_required")


def classify(sample):
    metrics = sample.get("metrics", {})
    count = metrics.get("eval_count")
    truncated = metrics.get("done_reason") == "length" or (type(count) is int and count >= 32)
    sample["truncated"] = truncated
    sample["expected_answer_present"] = bool(re.search(r"\bnew\s+delhi\b", sample.get("answer", ""), re.I))
    timings = ("load_duration", "prompt_eval_duration", "eval_duration", "eval_count", "prompt_eval_count")
    complete = (sample.get("stream_done") is True and metrics.get("done_reason") == "stop"
                and not truncated and bool(sample.get("answer", "").strip())
                and all(type(metrics.get(k)) in (int, float) and metrics[k] >= 0 for k in timings)
                and metrics.get("eval_count", 0) > 0 and metrics.get("eval_duration", 0) > 0
                and sample.get("first_meaningful_text_ms") is not None)
    sample["complete"] = complete
    sample["metrics_ms"] = {k + "_ms": metrics.get(k, 0) / 1e6 if type(metrics.get(k)) in (int, float) else None
                            for k in ("load_duration", "prompt_eval_duration", "eval_duration")}
    sample["tokens_per_second"] = count / (metrics["eval_duration"] / 1e9) if type(count) is int and metrics.get("eval_duration", 0) > 0 else None
    return complete and sample["expected_answer_present"]


def infer(host, digest, target, *, opener=None, clock=time.perf_counter, question=QUESTION, gate=None, http_timeout=20, phase=None):
    """Exactly one request; checkpoint every text event, including before cancellation."""
    if not 0 < http_timeout < RUNTIME_SECONDS:
        raise ValueError("HTTP timeout must be positive and below the monitored request limit")
    quality.require_pi()
    models.verify_identity(MODEL, models.inventory(host)[MODEL], digest)
    resident = quality.resident(host, MODEL, digest)
    from scripts.benchmark_inference_ab import telemetry
    (gate or preflight)(telemetry(), models.inventory(host)[MODEL], resident)
    payload = dict(model=MODEL, messages=[dict(role="user", content=question)],
                   stream=True, options=dict(OPTIONS), think=False, keep_alive="60s")
    sample = dict(answer="", metrics={}, stream_done=False, request=payload,
                  http_timeout_seconds=http_timeout, phase=phase)
    started = clock()
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), models.NoRedirect())
    save(target, sample)
    try:
        request = urllib.request.Request(host + "/api/chat", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with opener.open(request, timeout=http_timeout) as response:
            for raw in response:
                if len(raw) > 1_000_000:
                    raise models.TrialRefused("oversized_stream_event")
                event = json.loads(raw)
                if event.get("error"):
                    raise models.TrialRefused("ollama_stream_error")
                text = event.get("message", {}).get("content", "")
                sample["answer"] += text
                if len(sample["answer"]) > 8192:
                    raise models.TrialRefused("oversized_answer")
                if any(c.isalnum() for c in text):
                    sample.setdefault("first_meaningful_text_ms", (clock() - started) * 1000)
                sample["metrics"].update({k: event[k] for k in ("done_reason", "load_duration", "prompt_eval_duration", "eval_duration", "eval_count", "prompt_eval_count", "prompt_eval_cached_count") if k in event})
                sample["stream_done"] = event.get("done") is True
                save(target, sample)
                if sample["stream_done"]:
                    break
    except BaseException as exc:
        cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        if isinstance(cause, (TimeoutError, socket.timeout)):
            # A socket timeout during a cold request does not prove loading was the cause.
            sample["abort_reason"] = "cold_load_timeout" if phase == "cold" else "http_socket_timeout"
            sample["timeout_scope"] = "HTTP socket inactivity; underlying backend stage unproven"
        elif isinstance(exc, models.TrialRefused):
            sample["abort_reason"] = exc.reason
        else:
            sample["abort_reason"] = "worker_cancelled" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "worker_failure"
        sample["error_type"] = type(exc).__name__
        sample["error_detail"] = str(exc)
        raise
    finally:
        sample["total_response_seconds"] = clock() - started
        classify(sample)
        save(target, sample)
    return 0 if sample["complete"] else 2



def read_sample(target):
    """Missing/malformed checkpoints cannot turn a failed request into expiry."""
    try:
        sample = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(sample, dict):
            raise ValueError("Expected checkpoint object")
        return sample
    except (OSError, ValueError) as exc:
        return dict(abort_reason="worker_failure", error_type=type(exc).__name__,
                    error_detail="Missing or unreadable worker checkpoint")


def request_failure(sample, returncode):
    """Inspect worker evidence before considering post-completion residency."""
    if sample.get("abort_reason"):
        return sample["abort_reason"]
    classify(sample)
    if returncode not in (0, 2):
        return "worker_failure"
    if not sample["complete"]:
        return "incomplete_answer"
    if returncode:
        return "worker_failure"
    return None

def run(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = dict(status="aborted", model=MODEL, question=QUESTION, options=OPTIONS,
                  telemetry=[], success=False, manual_quality_review_required=True,
                  runtime_limit_seconds=RUNTIME_SECONDS, output_mode="direct text only; no voice or application pipeline")
    lock = None
    try:
        quality.require_pi()
        lock = models.acquire_trial_lock()
        # Telemetry bootstrap always targets a disposable DB, never deployed data.
        from scripts.validate_pi_performance import isolated_database
        with isolated_database("short-thermal-monitor-"):
            from scripts.benchmark_inference_ab import telemetry
            installed = models.inventory(args.host)[MODEL]
            digest = models.verify_identity(MODEL, installed)
            report.update(verified_digest=digest, installed=installed)
            resident = quality.resident(args.host, MODEL, digest)
            report["resident_before"] = resident
            first = telemetry()
            report["telemetry"].append(first)
            preflight(first, installed, resident)
            with tempfile.TemporaryDirectory(prefix="short-thermal-child-") as directory:
                target = Path(directory) / "sample.json"
                command = [sys.executable, str(Path(__file__).resolve()), "--host", args.host,
                           "--child-result", str(target), "--digest", digest]
                process = subprocess.Popen(command, cwd=ROOT, start_new_session=True,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    monitor(process, telemetry, lambda: quality.resident(args.host, MODEL, digest),
                            report, timeout=RUNTIME_SECONDS)
                    outcome = read_sample(target)
                    reason = request_failure(outcome, process.returncode)
                    if reason:
                        raise models.TrialRefused(reason)
                    final = telemetry()
                    report["telemetry"].append(final)
                    reason = models.pressure_reason(final)
                    if reason:
                        raise models.TrialRefused(reason)
                    models.verify_identity(MODEL, models.inventory(args.host)[MODEL], digest)
                    quality.resident(args.host, MODEL, digest, "warm")
                    report["status"] = "finished"
                finally:
                    models.terminate(process)
                    if target.is_file():
                        report["sample"] = read_sample(target)
    except BaseException as exc:
        report.update(status="aborted", abort_reason=exc.reason if isinstance(exc, models.TrialRefused) else "failed_or_cancelled")
    finally:
        if lock:
            lock.close()
        sample = report.get("sample", {})
        passed = classify(sample)
        report["success"] = report["status"] == "finished" and passed
        if report["status"] == "finished":
            report["status"] = "short_answer_pass_manual_review_pending" if passed else "incomplete_or_factual_check_failed"
        report["sample"] = sample
        report["load"] = models.load_summary(sample.get("metrics_ms", {}), report.get("resident_before", []))
        temperatures = [p["temperature_c"] for p in report["telemetry"] if type(p.get("temperature_c")) in (int, float)]
        report["initial_temperature_c"] = temperatures[0] if temperatures else None
        report["maximum_observed_temperature_c"] = max(temperatures) if temperatures else None
        save(output / "report.json", report)
    return 0 if report["success"] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="New output directory only")
    parser.add_argument("--host", type=models.local_host, default="http://127.0.0.1:11434")
    parser.add_argument("--child-result", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--digest", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child_result:
        signal.signal(signal.SIGTERM, lambda *unused: (_ for _ in ()).throw(KeyboardInterrupt()))
        quality.require_pi()
        from scripts.validate_pi_performance import isolated_database
        with isolated_database("short-thermal-worker-"):
            return infer(args.host, args.digest, args.child_result)
    if not args.output:
        parser.error("--output is required")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
