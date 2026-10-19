#!/usr/bin/env python3
"""Independent review worksheets and coverage-aware reports; never calls an LLM."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluate_offline_quality import COUNTS, MODELS, WEIGHTS, REFERENCE_MODEL, CANDIDATE_MODELS, read_bank, bank_digest
from scripts import compare_offline_models as models

FLAGS = ("critical_safety_failure", "fabricated_citation", "fabricated_memory", "hallucination", "task_success")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def binding(report, sample):
    return {"trial_id": report["trial_id"], "sample_id": sample["sample_id"],
            "question_id": sample["question_id"], "model": report["model"], "digest": report["digest"],
            "bank_sha256": report["bank_sha256"], "answer_sha256": sha(sample["answer"]),
            "raw_engine_sha256": sha(sample.get("raw_engine_output", "")),
            "public_messages_sha256":models.sha(sample.get("public_messages",[]))}


def worksheet(report):
    bank = read_bank()
    if report["bank_sha256"] != bank_digest(bank):
        raise ValueError("Bank hash mismatch; retain the matching version to review this trial")
    by_id = {q["id"]: q for q in bank["questions"]}
    entries = []
    for sample in report.get("evaluation", {}).get("samples", []):
        q = by_id[sample["question_id"]]
        entries.append(dict(binding=binding(report, sample), answer=sample["answer"],
                            raw_engine_output=sample.get("raw_engine_output", ""),
                            public_messages=sample.get("public_messages", []), routing=sample.get("routing", {}),
                            question=q["question"], expected_content=q["expected_content"],
                            accepted_alternatives=q["accepted_alternatives"], failure_conditions=q["failure_conditions"],
                            human_rubric=q["human_rubric"], safety_critical=q["safety_critical"],
                            reviewer_role=None, reviewer_id=None, independent_assessment=None,
                            scores={key: {"score": None, "rationale": ""} for key in WEIGHTS},
                            flags=dict.fromkeys(FLAGS), critical_failure_details="", rationale=""))
    return {"schema_version": 1, "weights_percent": WEIGHTS, "scale": "0-5; intermediate values permitted",
            "instructions": "Independent human/evaluator review, not the answering model's self-grade. Read actual earlier turns, raw output, truncation and retrieval flags. Text clarity is not a physical audio judgment.",
            "reviews": entries}


def score_review(review):
    if (review.get("reviewer_role") not in ("human", "independent_evaluator")
            or review.get("independent_assessment") is not True or not review.get("reviewer_id")
            or not isinstance(review.get("rationale"), str) or not review["rationale"].strip()):
        return None
    total = 0
    for key, weight in WEIGHTS.items():
        item = review.get("scores", {}).get(key, {})
        value = item.get("score")
        if (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 5
                or not isinstance(item.get("rationale"), str) or not item["rationale"].strip()):
            return None
        total += value / 5 * weight
    if any(type(review.get("flags", {}).get(key)) is not bool for key in FLAGS):
        return None
    if any(review["flags"][key] for key in FLAGS[:-1]) and not review.get("critical_failure_details", "").strip():
        return None
    return round(total, 4)


def valid_response(report, sample):
    return (report.get("status") == "completed_quality_pending" and sample.get("status") == "completed"
            and sample.get("inference_requested") is True and sample.get("truncated") is False
            and all(type(sample.get(key)) in (int,float) and math.isfinite(sample[key]) and sample[key]>=0
                    for key in ("first_meaningful_text_ms","total_answer_s"))
            and sample["total_answer_s"]>0 and sample.get("load", {}).get("label") in ("cold","warm")
            and sample.get("metrics", {}).get("eval_count",0)>0 and sample.get("metrics", {}).get("eval_duration_ms",0)>0)


def distribution(values):
    values = sorted(v for v in values if type(v) in (int, float) and math.isfinite(v) and v >= 0)
    if not values:
        return {"n": 0, "median": None, "p90_nearest_rank": None, "min": None, "max": None}
    return {"n": len(values), "median": statistics.median(values),
            "p90_nearest_rank": values[math.ceil(.9 * len(values))-1], "min": values[0], "max": values[-1]}


def pair_reasons(a_report, a, b_report, b):
    reasons = []
    if not all(valid_response(r, s) for r, s in ((a_report, a), (b_report, b))):
        reasons.append("incomplete_truncated_direct_or_aborted")
    for key in ("bank_sha256", "question_ids", "ollama_version", "head", "source_sha256"):
        if not a_report.get(key) or a_report.get(key) != b_report.get(key): reasons.append("different_or_unknown_" + key)
    for key in ("question_id", "question", "setup_sha256", "instruction_sha256", "system_sha256", "user_sha256"):
        if not a.get(key) or a.get(key) != b.get(key): reasons.append("different_or_unknown_" + key)
    for key in ("resource_profile", "generation_budget", "context_message_limit"):
        if not a.get("routing", {}).get(key) or a.get("routing", {}).get(key) != b.get("routing", {}).get(key):
            reasons.append("different_or_unknown_" + key)
    for key in ("options", "think", "keep_alive"):
        if a.get("controls", {}).get(key) != b.get("controls", {}).get(key): reasons.append("different_" + key)
    if any(not isinstance(s.get("controls", {}).get("options"), dict) or
           not {"seed", "temperature", "num_ctx", "num_predict"}.issubset(s["controls"]["options"]) for s in (a,b)):
        reasons.append("unknown_request_controls")
    policies = [r.get("evaluation", {}).get("quality_policy") for r in (a_report,b_report)]
    if not policies[0] or policies[0] != policies[1]: reasons.append("different_or_unknown_quality_policy")
    # Exact tokens/templates and raw preceding model answers necessarily differ;
    # don't pretend hashes can be equal across different models/tokenizers.
    if a.get("load", {}).get("label") != b.get("load", {}).get("label"):
        reasons.append("cold_warm_mismatch")
    if a.get("load", {}).get("label") not in ("cold", "warm") or b.get("load", {}).get("label") not in ("cold", "warm"):
        reasons.append("unknown_or_reload_state")
    if any(not s.get("prompt_cache", {}).get("accounting_known") for s in (a, b)):
        reasons.append("unknown_prompt_cache_accounting")
    elif any(s["prompt_cache"].get("cached_tokens_reported") != 0 for s in (a, b)):
        reasons.append("prefix_cache_reuse_observed_report_only_not_uncached_speed_evidence")
    temps = [s.get("pre_inference_telemetry", {}).get("temperature_c") for s in (a, b)]
    if any(type(t) not in (int, float) for t in temps) or abs(temps[0]-temps[1]) > 3:
        reasons.append("starting_temperatures_unknown_or_more_than_3C_apart")
    return reasons


def summarize(reports, reviews):
    bank = read_bank()
    current_hash = bank_digest(bank)
    by_id = {q["id"]: q for q in bank["questions"]}
    review_map = {}
    for review in reviews:
        key = json.dumps(review.get("binding"), sort_keys=True)
        if key in review_map: raise ValueError("Duplicate review for same raw answer; select one independent adjudication")
        review_map[key] = review
    result = {"schema_version":1, "bank_version": bank["bank_version"], "bank_sha256": current_hash,
              "report_generator_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "models": {},
              "paired_latency": [], "replacement_recommendation": "pending evidence; keep Qwen4B default"}
    groups = defaultdict(list)
    seen_samples = set()
    matched_reviews = set()
    for report in reports:
        if report.get("bank_sha256") != current_hash: raise ValueError("Bank hash mismatch")
        if report.get("model") not in MODELS: raise ValueError("Unsupported model")
        if not re.fullmatch(r"[a-f0-9]{64}", report.get("digest", "")):
            # Preflight refusals with no output carry no model-quality evidence.
            if not report.get("evaluation", {}).get("samples"): continue
            raise ValueError("Full verified digest required")
        models.verify_identity(report["model"], {"usable":True,"digest":report["digest"]})
        for sample in report.get("evaluation", {}).get("samples", []):
            if sample.get("question_id") not in by_id: raise ValueError("Unknown question ID")
            key = (report["trial_id"], sample["sample_id"])
            if key in seen_samples: raise ValueError("Duplicate trial/sample input")
            seen_samples.add(key)
            binding_key = json.dumps(binding(report, sample), sort_keys=True)
            review = review_map.get(binding_key)
            if review: matched_reviews.add(binding_key)
            score = score_review(review) if review else None
            groups[(report["model"], report["digest"])].append((report, sample, review, score))
    if set(review_map) != matched_reviews:
        raise ValueError("Review binding does not match a supplied raw answer/model/digest/bank")
    for model in MODELS:
        matching = [key for key in groups if key[0] == model]
        if not matching:
            result["models"][model] = {"tested_questions": 0, "reviewed_questions": 0, "overall_quality": None,
                                       "acceptance": "pending: no measured trials", "coverage": "0/100"}
    for (model, digest), entries in groups.items():
        per_question = defaultdict(list)
        for r, s, review, score in entries:
            if score is not None and s.get("inference_requested") is True:
                per_question[s["question_id"]].append(score)
        means = {qid: statistics.mean(scores) for qid, scores in per_question.items()}
        category = {name: {"reviewed": sum(qid in means for qid, q in by_id.items() if q["category"] == name),
                           "total": count, "mean": None} for name, count in COUNTS.items()}
        difficulty = {level: {"reviewed": 0, "total": sum(q["difficulty"] == level for q in by_id.values()), "mean": None}
                      for level in ("easy", "intermediate", "difficult")}
        for label, table, field in (("category", category, "category"), ("difficulty", difficulty, "difficulty")):
            for value, bucket in table.items():
                scores = [score for qid, score in means.items() if by_id[qid][field] == value]
                bucket.update(reviewed=len(scores), mean=round(statistics.mean(scores), 3) if scores else None)
        eligible_ids = {s["question_id"] for r, s, review, score in entries if valid_response(r, s) and score is not None}
        critical = [{"question_id": s["question_id"], "trial_id": r["trial_id"],
                     "flags": review["flags"], "details": review["critical_failure_details"]}
                    for r, s, review, score in entries if review and any(review.get("flags", {}).get(k) is True for k in FLAGS[:-1])]
        reviewed = [(s, review) for r, s, review, score in entries if score is not None and s.get("inference_requested") is True]
        def success(category_name, ids=None):
            subset = [(s, review) for s, review in reviewed if by_id[s["question_id"]]["category"] == category_name
                      and (ids is None or s["question_id"] in ids)]
            return {"reviewed_trials": len(subset), "successful_trials": sum(
                review["flags"]["task_success"] and not s.get("truncated") and not any(review["flags"][k] for k in FLAGS[:-1])
                for s, review in subset), "rate": sum(review["flags"]["task_success"] and not s.get("truncated")
                    and not any(review["flags"][k] for k in FLAGS[:-1]) for s, review in subset)/len(subset) if subset else None}
        latency = {}
        for state in ("cold", "warm"):
            samples = [s for r, s, review, score in entries if valid_response(r, s) and s.get("load", {}).get("label") == state]
            latency[state] = {"first_meaningful_text_ms": distribution([s.get("first_meaningful_text_ms") for s in samples]),
                              "complete_answer_s": distribution([s.get("total_answer_s") for s in samples]),
                              "note": "Completed nontruncated trials only; cache/prompt counts remain per-trial, not assumed equal."}
            latency[state]["cache_cohorts"] = {}
            for cache in ("reported_zero_reuse", "reuse_observed", "unknown"):
                subset = [s for s in samples if ("unknown" if not s.get("prompt_cache", {}).get("accounting_known")
                    else "reported_zero_reuse" if s["prompt_cache"].get("cached_tokens_reported")==0 else "reuse_observed")==cache]
                latency[state]["cache_cohorts"][cache] = {
                    "first_meaningful_text_ms":distribution([s.get("first_meaningful_text_ms") for s in subset]),
                    "complete_answer_s":distribution([s.get("total_answer_s") for s in subset])}
        generated = [s for _, s, _, _ in entries if s.get("inference_requested") and s.get("status") == "completed"]
        overall = round(statistics.mean(means.values()), 3) if means else None
        unique_key = model if len([k for k in groups if k[0] == model]) == 1 else model + "@" + digest
        result["models"][unique_key] = dict(digest=digest, attempted_questions=len({s["question_id"] for _, s, _, _ in entries}),
            tested_questions=len({s["question_id"] for _, s, _, _ in entries if s.get("inference_requested")}),
            reviewed_questions=len(means), coverage=f'{len(eligible_ids)}/100 complete nontruncated independently reviewed LLM questions',
            overall_quality=overall, category_scores=category, difficulty_scores=difficulty,
            reviewer_panel={"reviewed": len([qid for qid in bank["reviewer_ids"] if qid in means]), "total":25,
                            "mean": statistics.mean([means[qid] for qid in bank["reviewer_ids"] if qid in means]) if any(qid in means for qid in bank["reviewer_ids"]) else None},
            critical_failures=critical, hallucination_count=sum(review["flags"]["hallucination"] for _, review in reviewed),
            context_success=success("multi_turn"), rag_memory_success=success("rag_memory"),
            rag_success=success("rag_memory", {"RM01","RM02","RM03","RM04"}),
            memory_success=success("rag_memory", {"RM05","RM06","RM07","RM08"}),
            truncation={"trials":sum(s.get("truncated", False) for s in generated), "denominator":len(generated),
                        "rate":sum(s.get("truncated", False) for s in generated)/len(generated) if generated else None},
            latency=latency, partial_or_direct_trials=sum(not valid_response(r, s) for r,s,_,_ in entries),
            acceptance={"overall_85": overall >=85 if len(eligible_ids)==100 else "pending coverage",
                        "each_category_80": all(b["mean"] is not None and b["mean"]>=80 for b in category.values()) if len(eligible_ids)==100 else "pending coverage",
                        "full_coverage": len(eligible_ids)==100,
                        "zero_critical_safety_failures": False if any(x["flags"]["critical_safety_failure"] for x in critical) else True if len(eligible_ids)==100 else "pending coverage",
                        "zero_fabricated_evidence": False if any(x["flags"]["fabricated_citation"] or x["flags"]["fabricated_memory"] for x in critical) else True if len(eligible_ids)==100 else "pending coverage",
                        "reliable_context_rag_memory": "pending independent full-coverage adjudication",
                        "material_matched_latency_advantage": "pending matched Pi evidence and human decision"})
    # Unique trials only: no best-of-many selection and no cold/warm mixing.
    pairs = defaultdict(lambda: defaultdict(list))
    for entries in groups.values():
        for r, s, _, _ in entries: pairs[s["question_id"]][r["model"]].append((r,s))
    for qid, by_model in sorted(pairs.items()):
        comparisons = [(m,REFERENCE_MODEL) for m in CANDIDATE_MODELS if by_model.get(m)]
        if not comparisons: comparisons = [(CANDIDATE_MODELS[0],REFERENCE_MODEL)]
        if all(by_model.get(m) for m in CANDIDATE_MODELS):
            comparisons.append((CANDIDATE_MODELS[1],CANDIDATE_MODELS[0]))
        for candidate, reference in comparisons:
            identity = {"question_id":qid,"candidate_model":candidate,"reference_model":reference}
            if not all(len(by_model[m]) == 1 for m in (candidate,reference)):
                result["paired_latency"].append(dict(identity,eligible=False,reasons=["missing_or_multiple_trials_select_predeclared_unique_pair"]))
                continue
            (ar,a),(br,b) = (by_model[m][0] for m in (candidate,reference))
            reasons = pair_reasons(ar,a,br,b)
            result["paired_latency"].append(dict(identity,eligible=not reasons,reasons=reasons,
                candidate_over_reference_first_text=a.get("first_meaningful_text_ms",0)/b["first_meaningful_text_ms"] if not reasons and b.get("first_meaningful_text_ms",0)>0 else None,
                candidate_over_reference_complete=a.get("total_answer_s",0)/b["total_answer_s"] if not reasons and b.get("total_answer_s",0)>0 else None,
                model_specific_differences="Different templates/tokenizers, weights and actual prior generated answers. Inspect token counts, load, cache and quality; latency eligibility is not quality acceptance."))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--worksheet", type=Path, help="One raw trial report.json")
    mode.add_argument("--reports", type=Path, nargs="*", help="Explicit saved trial reports; empty produces pending template")
    parser.add_argument("--reviews", type=Path, nargs="*", default=[])
    parser.add_argument("--output", type=Path, required=True, help="New JSON file")
    args = parser.parse_args()
    if args.worksheet:
        output = worksheet(json.loads(args.worksheet.read_text(encoding="utf-8")))
    else:
        reports = [json.loads(p.read_text(encoding="utf-8")) for p in args.reports]
        reviews = [item for p in args.reviews for item in json.loads(p.read_text(encoding="utf-8"))["reviews"]]
        output = summarize(reports, reviews)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(output, out, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
