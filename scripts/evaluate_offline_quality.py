#!/usr/bin/env python3
"""Versioned quality bank; exactly one selected question/scenario, never batch inference."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import compare_offline_models as models
from scripts.validate_offline_voice import configure, loopback_only, monitor

BANK = ROOT / "benchmarks/offline_quality_v1.json"
MODELS = ("llama3.2:1b", "qwen3.5:4b-q4_K_M")
COUNTS = dict(general_knowledge=10, science_mathematics=10, computer_science=10,
              iot_embedded=10, pi_linux_networking=10, ai_edge=10, reasoning=10,
              multi_turn=8, rag_memory=8, uncertainty=6, everyday=4, device_safety=4)
WEIGHTS = dict(factual_correctness=35, completeness=20, reasoning_precision=15,
               grounding=15, relevance=10, spoken_clarity=5)


def read_bank():
    bank = json.loads(BANK.read_text(encoding="utf-8"))
    validate_bank(bank)
    return bank


def bank_digest(bank=None):
    """Semantic content hash, independent of Windows/Pi JSON line endings."""
    data = read_bank() if bank is None else bank
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def require(condition):
    if not condition:
        raise ValueError("Invalid quality benchmark manifest")


def validate_bank(bank):
    questions = bank["questions"]
    require(bank["schema_version"] == 1 and bank["bank_version"] == "1.0.0")
    require(bank["weights"] == WEIGHTS and bank["category_counts"] == COUNTS)
    require(len(questions) == 100 and len({q["id"] for q in questions}) == 100)
    by_id = {q["id"]: q for q in questions}
    require(len(bank["reviewer_ids"]) == len(set(bank["reviewer_ids"])) == 25)
    require(set(bank["reviewer_ids"]) == {q["id"] for q in questions if q["reviewer_panel"]})
    for category, count in COUNTS.items():
        require(sum(q["category"] == category for q in questions) == count)
    require({q["difficulty"] for q in questions} == {"easy", "intermediate", "difficult"})
    for q in questions:
        for key in ("id", "question", "context_prerequisites", "answer_style", "completeness"):
            require(isinstance(q[key], str) and q[key].strip())
        for key in ("expected_content", "accepted_alternatives", "failure_conditions"):
            require(q[key] and all(isinstance(v, str) and v.strip() for v in q[key]))
        require(q["setup"] in bank["setups"] and type(q["safety_critical"]) is bool)
        require(type(q["num_predict"]) is int and 64 <= q["num_predict"] <= 192)
        require(set(q["human_rubric"]) == set(WEIGHTS))
        require(all(set(r) == {"0", "3", "5"} for r in q["human_rubric"].values()))
        seq = q["sequence"]
        if seq:
            require(seq["turn"] in (1, 2) and q["category"] == "multi_turn")
            require(len(seq["preceding_ids"]) == seq["turn"] - 1)
            for earlier in seq["preceding_ids"]:
                require(by_id[earlier]["sequence"]["id"] == seq["id"])
                require(by_id[earlier]["sequence"]["turn"] < seq["turn"])
    return bank


def workload(bank, question_id):
    by_id = {q["id"]: q for q in bank["questions"]}
    if question_id not in by_id:
        raise ValueError("Unknown question ID")
    q = by_id[question_id]
    return [by_id[earlier] for earlier in (q["sequence"] or {}).get("preceding_ids", [])] + [q]


def instruction(q):
    # Strict allowlist. Evaluation references, failure criteria and rubrics NEVER
    # enter an inference message. Setup facts are provided through DB fixtures.
    return ("Answer accurately and explain the necessary steps. Admit missing evidence. "
            "Do not invent citations, memories, sensor readings or executed actions. " + q["answer_style"])


def save(path, data):
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    pending.replace(path)


def seed_setup(bank, setup_id):
    from conversations.models import Conversation, Message
    from knowledge_base.models import Document, KnowledgeChunk
    conversation = Conversation.objects.create(title="Isolated public quality evaluation")
    setup = bank["setups"][setup_id]
    for doc in setup["documents"]:
        record = Document.objects.create(filename=doc["name"], status="INDEXED", source_identifier="audit:quality-synthetic")
        KnowledgeChunk.objects.create(document=record, chunk_index=0, content=doc["content"])
    for text in setup["user_history"]:
        Message.objects.create(conversation=conversation, sender="USER", text=text)
    return conversation.pk


def resident(host, model, digest, phase=None):
    entries = models.metadata(host, "/api/ps").get("models", [])
    models.verify_request_residency(entries, model, digest, phase)
    if entries and entries[0].get("context_length") != 4096:
        raise models.TrialRefused("resident_context_must_be_4096")
    return entries


def memory_floor(sample, installed, already_resident):
    reason = models.pressure_reason(sample, starting=True)
    if reason:
        raise models.TrialRefused(reason)
    if not already_resident:
        size = installed.get("artifact_bytes")
        if type(size) is not int or size <= 0:
            raise models.TrialRefused("cold_model_memory_requirement_unknown")
        # Conservative load allowance, not a promise of actual model RAM use.
        if sample["ram_available_bytes"] < int(size * 1.5) + 512 * 1024**2:
            raise models.TrialRefused("insufficient_cold_load_memory_margin")


def require_pi():
    if sys.platform != "linux":
        raise models.TrialRefused("linux_pi_only_no_laptop_inference")
    if not Path("/proc/device-tree/model").read_text().rstrip("\x00").startswith("Raspberry Pi 5"):
        raise models.TrialRefused("raspberry_pi_5_required")


def child(args):
    require_pi()
    directory = Path(args.child_dir)
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    os.environ["TMPDIR"] = os.environ["TEMP"] = os.environ["TMP"] = str(directory)
    tempfile.tempdir = str(directory)
    import config.settings as config
    config.LOGGING_CONFIG = None
    from scripts.validate_pi_performance import bootstrap
    bootstrap(directory)
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connections
    configure(settings, args.host)
    settings.OLLAMA_MODEL = args.model
    settings.COMPANION_DISPLAY_ENABLED = False
    settings.OLLAMA_TIMEOUT = settings.AI_LOCAL_GENERATION_TIMEOUT_SECONDS = 40
    settings.AI_GENERATION_NORMAL_NUM_PREDICT = 192  # Quality only; ECO/protective caps unchanged.
    settings.RAG_NUM_PREDICT = 192  # Remove the normal 64-token RAG style cap only in this quality process.
    settings.VOICE_COMPACT_STYLE = False
    signal.signal(signal.SIGTERM, lambda *unused: (_ for _ in ()).throw(KeyboardInterrupt()))
    target = directory / "result.json"
    report = {"status": "incomplete", "samples": [],
              "quality_policy": {key: getattr(settings, key) for key in (
                  "AI_GENERATION_NORMAL_NUM_PREDICT", "AI_GENERATION_REDUCED_NUM_PREDICT",
                  "AI_GENERATION_MINIMAL_NUM_PREDICT", "AI_LOCAL_GENERATION_TIMEOUT_SECONDS",
                  "RAG_NUM_PREDICT", "RAG_TOP_K", "RAG_MIN_RELEVANCE", "RAG_MAX_CONTEXT_CHARS",
                  "CONVERSATION_MEMORY_TOP_K", "CONVERSATION_MEMORY_MAX_CHARS",
                  "CONVERSATION_MEMORY_MIN_RELEVANCE", "CONVERSATION_RECENT_MESSAGES")}}
    try:
        with loopback_only():
            call_command("migrate", verbosity=0, interactive=False)
            evaluate(args, report, lambda: save(target, report))
        return 0 if report["status"] == "completed_quality_pending" else 2
    except BaseException as exc:
        report.update(status="aborted", reason=exc.reason if isinstance(exc, models.TrialRefused) else "inference_failed_or_cancelled")
        save(target, report)
        return 2
    finally:
        connections.close_all()


def evaluate(args, report, persist):
    """Actual service persists real answers; next turn reads them, never reference text."""
    from assistant.ai_engine import get_engine, reset_engine
    from assistant.policy import ResponsePlan, ResponseMode
    from assistant.services import AssistantService
    from scripts.benchmark_inference_ab import telemetry
    bank = read_bank()
    questions = workload(bank, args.question)
    conversation_id = seed_setup(bank, questions[0]["setup"])
    reset_engine()
    engine = get_engine()
    original_builder, original_stream = engine._build_chat_request, engine.generate_stream
    active, requests = {}, []
    def build(*a, **kw):
        point = telemetry()
        reason = models.pressure_reason(point, starting=len(report["samples"]) == 1)
        if reason:
            raise models.TrialRefused(reason)
        identity = models.verify_identity(args.model, models.inventory(args.host)[args.model], args.digest)
        before = resident(args.host, args.model, identity, "warm" if len(report["samples"]) > 1 else args.phase)
        if requests:
            raise models.TrialRefused("more_than_one_inference_for_question")
        request = original_builder(*a, **kw)
        payload = json.loads(request.data)
        if payload["model"] != args.model:
            raise models.TrialRefused("effective_model_changed")
        payload["options"].update(seed=42, temperature=0, num_ctx=4096)
        # The service's effective cap may be reduced by current resource policy.
        payload["options"]["num_predict"] = min(active["requested_tokens"], payload["options"]["num_predict"])
        request.data = json.dumps(payload).encode()
        requests.append(True)
        active.update(controls={k: payload.get(k) for k in ("model", "options", "think", "keep_alive")},
                      inference_requested=True,
                      message_sha256=models.sha(payload["messages"]),
                      public_messages=payload["messages"],  # Only isolated public fixtures/actual trial turns.
                      system_sha256=models.sha([m["content"] for m in payload["messages"] if m["role"] == "system"]),
                      user_sha256=models.sha(payload["messages"][-1]["content"]),
                      resident_before=[{k: item.get(k) for k in ("name", "digest", "size", "size_vram", "context_length")} for item in before],
                      pre_inference_telemetry=point, request_started_s=time.monotonic())
        persist()
        return request
    def stream(*a, **kw):
        for event in original_stream(*a, **kw):
            if event.type == "delta":
                active["raw_engine_output"] += event.text
                if any(c.isalnum() for c in event.text):
                    active.setdefault("first_meaningful_text_ms", round((time.monotonic() - active["request_started_s"]) * 1000, 3))
                    active.setdefault("application_first_meaningful_text_ms", round((time.monotonic() - active["started_s"]) * 1000, 3))
                persist()  # Partial raw output survives interruption.
            yield event
    engine._build_chat_request, engine.generate_stream = build, stream
    try:
        for q in questions:
            active = dict(question_id=q["id"], question=q["question"], category=q["category"], difficulty=q["difficulty"],
                          sample_id=str(uuid.uuid4()), model=args.model, digest=args.digest,
                          status="incomplete", requested_tokens=q["num_predict"], raw_engine_output="", answer="",
                          metrics={}, prompt_cache=models.prompt_accounting({}), truncated=None,
                          started_s=time.monotonic(), human_review=None, sequence=q["sequence"],
                          setup_sha256=models.sha(bank["setups"][q["setup"]]))
            report["samples"].append(active)
            persist()
            requests.clear()
            plan = ResponsePlan(mode=ResponseMode.DETAILED if q["difficulty"] == "difficult" else ResponseMode.NORMAL,
                                system_instruction=instruction(q), num_predict=q["num_predict"],
                                allow_implicit_rag=q["setup"].startswith("lumen"),
                                recent_history_limit=10 if q["sequence"] else 0,
                                allow_long_term_memory=q["setup"].startswith("memory"), is_voice=False)
            done = None
            for event in AssistantService.process_message_stream(q["question"], conversation_id=conversation_id, response_plan=plan):
                if event["type"] == "delta":
                    active["answer"] += event["text"]
                elif event["type"] == "done":
                    done = event
                elif event["type"] == "error":
                    raise models.TrialRefused("service_error")
            if not done or done.get("online_used") or done.get("effective_model") not in (None, args.model):
                raise models.TrialRefused("invalid_local_service_result")
            metrics = done.get("ollama_metrics", {})
            count, cap = metrics.get("eval_count"), (active.get("controls", {}).get("options") or {}).get("num_predict")
            truncated = metrics.get("done_reason") == "length" or type(count) is int and type(cap) is int and count >= cap
            after = resident(args.host, args.model, args.digest, "warm" if requests else None)
            active.update(status="completed", answer=done["response"], inference_requested=bool(requests),
                          completed_s=time.monotonic(), total_answer_s=round(time.monotonic()-active["started_s"], 6),
                          metrics=metrics, prompt_cache=models.prompt_accounting(metrics),
                          load=models.load_summary(metrics, active.get("resident_before", [])), truncated=bool(truncated),
                          effective_tokens=cap, instruction_sha256=models.sha(instruction(q)),
                          routing={k: done.get(k) for k in ("route", "rag_used", "memory_used", "recent_history_used", "resource_profile", "generation_budget", "num_predict", "context_message_limit")},
                          resident_after=[{k: item.get(k) for k in ("name", "digest", "size", "size_vram", "context_length")} for item in after])
            if requests and (not metrics.get("eval_count") or not metrics.get("eval_duration_ms") or active.get("first_meaningful_text_ms") is None):
                active["status"] = "invalid_missing_metrics"
            if active.get("resident_before") and active["load"]["label"] != "warm":
                active["status"] = "invalid_resident_reload"
            persist()
            if truncated or active["status"] != "completed":
                report["status"] = "incomplete_scenario"
                persist()
                return
        report["status"] = "completed_quality_pending"
        persist()
    finally:
        engine._build_chat_request, engine.generate_stream = original_builder, original_stream
        reset_engine()


def resource_summary(points):
    temperatures = [p["temperature_c"] for p in points if type(p.get("temperature_c")) in (int, float) and math.isfinite(p["temperature_c"])]
    memory = [p["ram_available_bytes"] for p in points if type(p.get("ram_available_bytes")) is int]
    clocks = [p["measured_arm_hz"] for p in points if type(p.get("measured_arm_hz")) is int]
    keys = ("under_voltage", "frequency_capped", "currently_throttled", "soft_temperature_limit")
    flags = [p["throttled"] for p in points if isinstance(p.get("throttled"), dict)]
    active = any(f.get(k) is True for f in flags for k in keys)
    known = len(flags)==len(points) and all(type(f.get(k)) is bool for f in flags for k in keys)
    return {"sample_count": len(points), "max_observed_temperature_c": max(temperatures, default=None),
            "min_available_ram_bytes": min(memory, default=None),
            "memory_pressure_observed": min(memory) < 512 * 1024**2 if memory else None,
            "min_measured_arm_hz": min(clocks, default=None), "max_measured_arm_hz": max(clocks, default=None),
            "current_power_throttle_pressure_observed": True if active else False if points and known else None}


def run(args):
    bank = read_bank()
    selected = workload(bank, args.question)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = dict(schema_version=1, bank_version=bank["bank_version"], bank_sha256=bank_digest(bank),
                  source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), trial_id=str(uuid.uuid4()),
                  head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  created_utc=datetime.now(timezone.utc).isoformat(), model=args.model, question_ids=[q["id"] for q in selected],
                  status="incomplete", telemetry=[], output_mode="text only; spoken clarity judged from text, not measured audio",
                  quality_accepted=False, target="Raspberry Pi 5 only", inference_process_started=False)
    def persist(): save(output / "report.json", report)
    persist()
    lock = None
    try:
        require_pi()
        lock = models.acquire_trial_lock()
        from scripts.validate_pi_performance import isolated_database
        with isolated_database("quality-monitor-"):
            from scripts.benchmark_inference_ab import telemetry
            inventory = models.inventory(args.host)
            args.digest = models.verify_identity(args.model, inventory[args.model])
            before = resident(args.host, args.model, args.digest)
            args.phase = "warm" if before else "cold"
            first = telemetry()
            memory_floor(first, inventory[args.model], before)
            report.update(digest=args.digest, resident_at_start=bool(before), telemetry=[first],
                          installed=inventory[args.model], ollama_version=models.metadata(args.host, "/api/version").get("version"))
            persist()
            with tempfile.TemporaryDirectory(prefix="quality-question-") as directory:
                command = [sys.executable, str(Path(__file__).resolve()), "--child-dir", directory, "--question", args.question,
                           "--model", args.model, "--digest", args.digest, "--phase", args.phase, "--host", args.host]
                process = subprocess.Popen(command, cwd=ROOT, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                report["inference_process_started"] = True
                try:
                    monitor(process, telemetry, lambda: resident(args.host, args.model, args.digest, "warm" if before else None), report)
                    final = telemetry()
                    report["telemetry"].append(final)
                    reason = models.pressure_reason(final)
                    if reason: raise models.TrialRefused(reason)
                    if models.inventory(args.host) != inventory:
                        raise models.TrialRefused("inventory_changed")
                    resident(args.host, args.model, args.digest)
                    if process.returncode:
                        raise models.TrialRefused("child_incomplete_or_failed")
                finally:
                    models.terminate(process)
                    result = Path(directory) / "result.json"
                    if result.is_file(): report["evaluation"] = json.loads(result.read_text(encoding="utf-8"))
                report["status"] = report.get("evaluation", {}).get("status", "incomplete")
    except BaseException as exc:
        report.update(status="aborted", reason=exc.reason if isinstance(exc, models.TrialRefused) else "evaluation_failed_or_cancelled")
    finally:
        if lock: lock.close()
        points = report["telemetry"]
        report["resource_summary"] = resource_summary(points)
        report["resource_summary"]["scope"] = "Whole invocation; sampled observations are not continuous hardware measurements."
        for sample in report.get("evaluation", {}).get("samples", []):
            inside = [p for p in points if sample["started_s"] <= p.get("monotonic_s", -1) <= sample.get("completed_s", time.monotonic())]
            if sample.get("pre_inference_telemetry"): inside.append(sample["pre_inference_telemetry"])
            sample["resource_summary"] = resource_summary(inside)
        persist()
    return 0 if report["status"] == "completed_quality_pending" else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-bank", action="store_true")
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--question", help="One stable question ID; second turns run the actual preceding turn first")
    parser.add_argument("--model", choices=MODELS)
    parser.add_argument("--host", type=models.local_host, default="http://127.0.0.1:11434")
    parser.add_argument("--output", help="New report directory; never reused")
    parser.add_argument("--child-dir", help=argparse.SUPPRESS)
    parser.add_argument("--digest", help=argparse.SUPPRESS)
    parser.add_argument("--phase", choices=("cold", "warm"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    bank = read_bank()
    if args.validate_bank:
        print("Valid bank 1.0.0: 100 questions, 12 categories, 25 reviewer questions; no inference")
        return 0
    if args.list:
        print("\n".join(f'{q["id"]} {q["difficulty"]} {q["question"]}' for q in bank["questions"]))
        return 0
    workload(bank, args.question)
    if not args.model: parser.error("Select one installed model")
    if args.child_dir: return child(args)
    if not args.output: parser.error("Choose a new output directory")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
