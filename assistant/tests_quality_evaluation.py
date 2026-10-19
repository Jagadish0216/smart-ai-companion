"""Quality evaluation infrastructure only: fake answers cannot certify model quality."""
from copy import deepcopy
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from scripts import evaluate_offline_quality as quality
from scripts import report_offline_quality as reviews

DIGEST = "baf6a787fdff" + "0" * 52
MODEL = "llama3.2:1b"
RESIDENT = dict(name=MODEL, digest=DIGEST, context_length=4096, size=1300000000)


def point(**kw):
    result = dict(temperature_c=44.0, ram_available_bytes=3*1024**3, measured_arm_hz=2400000000,
                  throttled=dict.fromkeys(("under_voltage","frequency_capped","currently_throttled","soft_temperature_limit"),False))
    result.update(kw)
    return result


def trial(model=MODEL):
    digest = DIGEST if model == MODEL else "d8b0f5e9760c" + "0"*52
    sample = dict(question_id="CS01",question=quality.read_bank()["questions"][20]["question"],sample_id="sample-"+model,
                  status="completed",inference_requested=True,truncated=False,answer="Observed public test answer.",
                  model=model,digest=digest,setup_sha256="setup",instruction_sha256="instruction",system_sha256="system",user_sha256="user",category="computer_science",
                  controls={"options":{"seed":42,"temperature":0,"num_ctx":4096,"num_predict":160},"think":False,"keep_alive":"60s"},
                  routing={"resource_profile":"BALANCED","generation_budget":"NORMAL","context_message_limit":10},
                  load={"label":"warm"},prompt_cache=quality.models.prompt_accounting({"prompt_eval_count":100,"prompt_eval_cached_count":0}),
                  pre_inference_telemetry=point(),first_meaningful_text_ms=500,total_answer_s=5,metrics={"eval_count":40,"eval_duration_ms":2000})
    return dict(trial_id="trial-"+model,model=model,digest=digest,bank_sha256=quality.bank_digest(),head="a"*40,source_sha256="runner-source-hash",
                question_ids=["CS01"],ollama_version="test-version",status="completed_quality_pending",evaluation={"samples":[sample],"quality_policy":{"normal_cap":192}})


def completed_review(report, score=5):
    review=reviews.worksheet(report)["reviews"][0]
    review.update(reviewer_role="human",reviewer_id="independent-test-reviewer",independent_assessment=True,
                  rationale="Synthetic evaluator fixture; this is not a model-quality result.")
    review["scores"]={key:{"score":score,"rationale":"Inspected criterion against reference and observed output."} for key in quality.WEIGHTS}
    review["flags"]={key:key=="task_success" for key in reviews.FLAGS}
    return review


class ManifestTests(SimpleTestCase):
    def test_bank_hash_independent_of_json_format_and_windows_line_endings(self):
        bank=quality.read_bank()
        windows=json.dumps(bank,indent=4,ensure_ascii=False).replace("\n","\r\n")
        self.assertEqual(quality.bank_digest(bank),quality.bank_digest(json.loads(windows)))
        changed=deepcopy(bank);changed["questions"][0]["question"]+=" Changed."
        self.assertNotEqual(quality.bank_digest(bank),quality.bank_digest(changed))

    def test_exact_counts_and_dedicated_panel(self):
        bank=quality.read_bank()
        self.assertEqual(len(bank["questions"]),100)
        self.assertEqual(len(bank["reviewer_ids"]),25)
        self.assertEqual(sum(bank["category_counts"].values()),100)
        self.assertTrue(all(q["human_rubric"] and q["accepted_alternatives"] and q["failure_conditions"] for q in bank["questions"]))

    def test_bad_count_duplicates_prerequisites_and_missing_references_refused(self):
        bank=quality.read_bank()
        for change in (lambda b:b["questions"].pop(),
                       lambda b:b["questions"][0].update(id="GK02"),
                       lambda b:b["questions"][0].update(expected_content=[]),
                       lambda b:b["questions"][71]["sequence"].update(preceding_ids=["GK01"])):
            broken=deepcopy(bank)
            change(broken)
            with self.assertRaises((ValueError,TypeError)):
                quality.validate_bank(broken)

    def test_only_selected_question_or_actual_prefix_not_a_batch(self):
        bank=quality.read_bank()
        self.assertEqual([q["id"] for q in quality.workload(bank,"CS01")],["CS01"])
        self.assertEqual([q["id"] for q in quality.workload(bank,"MT06")],["MT05","MT06"])
        self.assertEqual([q["id"] for q in quality.workload(bank,"MT05")],["MT05"])
        with self.assertRaises(ValueError):quality.workload(bank,"ALL")

    def test_no_reference_metadata_canary_enters_instruction(self):
        q=deepcopy(quality.read_bank()["questions"][0])
        for key in ("expected_content","accepted_alternatives","failure_conditions","human_rubric","completeness","context_prerequisites"):
            q[key]="SECRET_EVALUATOR_CANARY"
        self.assertNotIn("SECRET_EVALUATOR_CANARY",quality.instruction(q))
        self.assertIn(q["answer_style"],quality.instruction(q))

    def test_missing_fixtures_do_not_preload_correct_answers(self):
        setups=quality.read_bank()["setups"]
        self.assertFalse(any("Amber Finch" in s or "Copper Robin" in s for s in setups["memory_absent"]["user_history"]))
        self.assertNotIn("serial",setups["lumen_note"]["documents"][0]["content"].lower())
        self.assertTrue(all("user_history" in setup for setup in setups.values()))
        self.assertFalse(any("assistant_history" in setup for setup in setups.values()))

    def test_varied_generation_limits_and_no_unfair_word_cap(self):
        bank=quality.read_bank()
        self.assertEqual({q["num_predict"] for q in bank["questions"]},{96,128,160,192})
        self.assertFalse(any("at most 25 words" in quality.instruction(q) for q in bank["questions"]))


class SafetyTests(SimpleTestCase):
    def test_exact_digest_conflicting_residency_context_and_expiry(self):
        installed={"usable":True,"digest":DIGEST}
        self.assertEqual(quality.models.verify_identity(MODEL,installed),DIGEST)
        with self.assertRaises(quality.models.TrialRefused):quality.models.verify_identity(MODEL,dict(installed,digest="a"*64))
        for entries in ([dict(RESIDENT,name="other")],[RESIDENT,RESIDENT],[dict(RESIDENT,context_length=2048)],[]):
            with patch.object(quality.models,"metadata",return_value={"models":entries}):
                with self.assertRaises(quality.models.TrialRefused):quality.resident("http://127.0.0.1:11434",MODEL,DIGEST,"warm")

    def test_cold_memory_margin_and_current_thermal_guard(self):
        quality.memory_floor(point(),{"artifact_bytes":1024**3},False)
        for sample, installed in ((point(temperature_c=55),{"artifact_bytes":1024**3}),
                                  (point(ram_available_bytes=1024**3),{"artifact_bytes":1024**3}),
                                  (point(),{"artifact_bytes":None})):
            with self.assertRaises(quality.models.TrialRefused):quality.memory_floor(sample,installed,False)
        quality.memory_floor(point(),{},True)
        history=point()
        history["throttled"]["throttling_occurred"]=True
        self.assertIsNone(quality.models.pressure_reason(history))
        self.assertIsNotNone(quality.models.pressure_reason(point(temperature_c=70)))

    def test_monitor_abort_cancellation_and_bounded_clock(self):
        process=MagicMock()
        process.poll.return_value=None
        with self.assertRaises(quality.models.TrialRefused):
            quality.monitor(process,lambda:point(temperature_c=70),lambda:None,{"telemetry":[]},sleep=lambda _:None)
        with self.assertRaises(KeyboardInterrupt):
            quality.monitor(process,MagicMock(side_effect=KeyboardInterrupt),lambda:None,{"telemetry":[]})
        ticks=iter((0,61))
        with self.assertRaises(quality.models.TrialRefused):
            quality.monitor(process,point,lambda:None,{"telemetry":[]},clock=lambda:next(ticks),sleep=lambda _:None)

    def test_resource_summary_current_history_and_missing_samples(self):
        history=point();history["throttled"]["throttling_occurred"]=True
        summary=quality.resource_summary([history,point(temperature_c=50)])
        self.assertEqual(summary["max_observed_temperature_c"],50)
        self.assertFalse(summary["current_power_throttle_pressure_observed"])
        self.assertFalse(summary["memory_pressure_observed"])
        self.assertIsNone(quality.resource_summary([])["max_observed_temperature_c"])
        self.assertIsNone(quality.resource_summary([point(throttled=None)])["current_power_throttle_pressure_observed"])

    def test_parent_preserves_partial_report_and_reaps_owned_child_on_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/"report"
            child_directories=[]
            def spawn(command,**kw):
                child=Path(command[command.index("--child-dir")+1])
                child_directories.append(child)
                partial={"status":"incomplete","samples":[{"question_id":"CS01","sample_id":"partial",
                    "answer":"","raw_engine_output":"Observed partial output.","started_s":0,"status":"incomplete"}]}
                quality.save(child/"result.json",partial)
                return MagicMock()
            args=SimpleNamespace(question="CS01",model=MODEL,host="http://127.0.0.1:11434",output=str(output))
            with patch.object(quality,"require_pi"), \
                 patch.object(quality.subprocess,"check_output",return_value="a"*40), \
                 patch.object(quality.subprocess,"Popen",side_effect=spawn), \
                 patch.object(quality.models,"acquire_trial_lock",return_value=MagicMock()), \
                 patch.object(quality.models,"inventory",return_value={MODEL:{"usable":True,"digest":DIGEST}}), \
                 patch.object(quality.models,"metadata",return_value={"version":"mock"}), \
                 patch.object(quality,"resident",return_value=[RESIDENT]), \
                 patch.object(quality,"monitor",side_effect=KeyboardInterrupt), \
                 patch.object(quality.models,"terminate") as terminate, \
                 patch("scripts.benchmark_inference_ab.telemetry",return_value=point()), \
                 patch("scripts.validate_pi_performance.isolated_database",return_value=nullcontext(Path(directory))):
                self.assertEqual(quality.run(args),2)
            saved=json.loads((output/"report.json").read_text())
            self.assertEqual(saved["status"],"aborted")
            self.assertEqual(saved["evaluation"]["samples"][0]["raw_engine_output"],"Observed partial output.")
            terminate.assert_called_once()
            self.assertTrue(all(not child.exists() for child in child_directories))

    def test_refuse_windows_and_non_pi_including_hidden_child(self):
        with patch.object(quality.sys,"platform","win32"):
            with self.assertRaises(quality.models.TrialRefused):quality.require_pi()
            with self.assertRaises(quality.models.TrialRefused):quality.child(SimpleNamespace())
        with patch.object(quality.sys,"platform","linux"),patch.object(Path,"read_text",return_value="Raspberry Pi 4"):
            with self.assertRaises(quality.models.TrialRefused):quality.require_pi()


class ReviewTests(SimpleTestCase):
    def test_weights_exact_and_worksheet_never_self_certifies(self):
        report=trial()
        template=reviews.worksheet(report)["reviews"][0]
        self.assertIsNone(reviews.score_review(template))
        review=completed_review(report)
        self.assertEqual(reviews.score_review(review),100)
        review["scores"]["factual_correctness"]["score"]=0
        self.assertEqual(reviews.score_review(review),65)
        self.assertEqual(reviews.score_review(completed_review(report,0)),0)

    def test_invalid_scores_missing_rationale_and_self_review_remain_pending(self):
        for value in (True,-1,6,float("nan"),float("inf"),None):
            r=completed_review(trial())
            r["scores"]["completeness"]["score"]=value
            self.assertIsNone(reviews.score_review(r))
        for change in (lambda r:r.update(reviewer_role="answering_model"),
                       lambda r:r.update(independent_assessment=False),
                       lambda r:r["scores"]["grounding"].update(rationale="")):
            r=completed_review(trial());change(r)
            self.assertIsNone(reviews.score_review(r))

    def test_no_untested_accuracy_empty_and_partial_coverage(self):
        empty=reviews.summarize([],[])
        self.assertIsNone(empty["models"][MODEL]["overall_quality"])
        report=trial()
        result=reviews.summarize([report],[completed_review(report)])
        model=result["models"][MODEL]
        self.assertEqual(model["reviewed_questions"],1)
        self.assertTrue(model["coverage"].startswith("1/100"))
        self.assertIsNone(model["category_scores"]["general_knowledge"]["mean"])
        self.assertEqual(model["acceptance"]["each_category_80"],"pending coverage")
        self.assertIn("pending",result["replacement_recommendation"])

    def test_review_bound_to_raw_answer_digest_model_and_bank(self):
        report=trial();review=completed_review(report)
        for key,value in (("digest","a"*64),("model","qwen3.5:4b-q4_K_M"),("answer_sha256","x")):
            broken=deepcopy(review);broken["binding"][key]=value
            with self.assertRaises(ValueError):reviews.summarize([report],[broken])
        with self.assertRaises(ValueError):reviews.summarize([report],[review,review])

    def test_truncation_partial_and_direct_not_successful_model_completion(self):
        for change in (lambda r:r["evaluation"]["samples"][0].update(truncated=True),
                       lambda r:r.update(status="aborted"),
                       lambda r:r["evaluation"]["samples"][0].update(inference_requested=False)):
            report=trial();change(report)
            result=reviews.summarize([report],[completed_review(report)])["models"][MODEL]
            self.assertTrue(result["coverage"].startswith("0/100"))
            self.assertEqual(result["latency"]["warm"]["complete_answer_s"]["n"],0)

    def test_critical_safety_fabricated_citations_memory_explicit(self):
        report=trial();review=completed_review(report)
        review["flags"].update(critical_safety_failure=True,fabricated_citation=True,fabricated_memory=True,hallucination=True,task_success=False)
        self.assertIsNone(reviews.score_review(review))
        review["critical_failure_details"]="Synthetic failure example for reporting tests."
        result=reviews.summarize([report],[review])["models"][MODEL]
        self.assertEqual(len(result["critical_failures"]),1)
        self.assertTrue(result["critical_failures"][0]["flags"]["fabricated_memory"])
        self.assertEqual(result["hallucination_count"],1)
        self.assertFalse(result["acceptance"]["zero_critical_safety_failures"])
        self.assertFalse(result["acceptance"]["zero_fabricated_evidence"])

    def test_latency_distributions_and_cold_warm_cache_conditions(self):
        a,b=trial(),trial(quality.MODELS[1])
        self.assertFalse(reviews.pair_reasons(a,a["evaluation"]["samples"][0],b,b["evaluation"]["samples"][0]))
        for change in (lambda r:r["evaluation"]["samples"][0]["load"].update(label="cold"),
                       lambda r:r["evaluation"]["samples"][0]["prompt_cache"].update(cached_tokens_reported=50),
                       lambda r:r["evaluation"]["samples"][0]["controls"]["options"].update(num_predict=96),
                       lambda r:r["evaluation"]["samples"][0]["pre_inference_telemetry"].update(temperature_c=50)):
            changed=deepcopy(b);change(changed)
            self.assertTrue(reviews.pair_reasons(a,a["evaluation"]["samples"][0],changed,changed["evaluation"]["samples"][0]))
        self.assertEqual(reviews.distribution([1,2,3,4,5])["p90_nearest_rank"],5)
        self.assertIsNone(reviews.distribution([])["median"])


@override_settings(AI_ENGINE="local",OLLAMA_MODEL=MODEL,OLLAMA_HOST="http://127.0.0.1:11434",
                   ONLINE_RETRIEVAL_ENABLED=False,DEVICE_CONTROL_ENABLED=False,
                   AI_GENERATION_NORMAL_NUM_PREDICT=192,AI_LOCAL_GENERATION_TIMEOUT_SECONDS=40,RAG_ENABLED=True,RAG_NUM_PREDICT=192)
class ScenarioTests(TestCase):
    def exercise(self, question="MT06", truncate=False, interrupted=False, canary=False):
        from assistant.ai_engine import AIEngineResult,AIEngineStreamEvent
        from assistant.ai_engine.local import LocalLLMEngine
        from assistant.execution_policy import AIExecutionPlan
        execution=AIExecutionPlan(resource_profile="BALANCED", configured_model=MODEL, effective_model=MODEL,
            model_reason="single_model",generation_budget="NORMAL",max_output_tokens=192,context_message_limit=10,
            local_ai_allowed=True,generation_allowed=True,online_allowed=False,timeout_seconds=40,
            policy_reason="test",resource_policy_available=True)
        engine=LocalLLMEngine(model=MODEL,host="http://127.0.0.1:11434")
        captured=[]
        original_builder=engine._build_chat_request
        def builder(*a,**kw):
            request=original_builder(*a,**kw)
            captured.append(json.loads(request.data))
            return request
        engine._build_chat_request=builder
        def stream(query,conversation_history=None,system_instruction=None,**kw):
            messages=engine._build_messages(query,conversation_history,system_instruction)
            engine._build_chat_request(messages,stream=True,num_predict=kw.get("num_predict"),model=MODEL)
            text="Actual generated first response, including its original mistakes." if len(captured)==1 else "Actual generated follow-up response."
            yield AIEngineStreamEvent(type="delta",text=text)
            if interrupted:raise KeyboardInterrupt()
            yield AIEngineStreamEvent(type="done",result=AIEngineResult(text=text,engine="local",model=MODEL,
                metrics={"eval_count":128 if truncate else 20,"eval_duration_ms":2000,"load_duration_ms":1,
                         "prompt_eval_count":150,"prompt_eval_cached_count":0,"done_reason":"length" if truncate else "stop"}))
        engine.generate_stream=stream
        args=SimpleNamespace(question=question,model=MODEL,host="http://127.0.0.1:11434",digest=DIGEST,phase="warm")
        report={"status":"incomplete","samples":[]}
        snapshots=[]
        reference_bank=quality.read_bank()
        if canary:
            for q in reference_bank["questions"]:
                for key in ("expected_content","accepted_alternatives","failure_conditions"):
                    q[key]=["EVALUATOR_ONLY_CANARY"]
                for key in ("completeness","context_prerequisites"):
                    q[key]="EVALUATOR_ONLY_CANARY"
                q["human_rubric"]={key:{"0":"EVALUATOR_ONLY_CANARY","3":"EVALUATOR_ONLY_CANARY","5":"EVALUATOR_ONLY_CANARY"} for key in quality.WEIGHTS}
        with patch("assistant.ai_engine.get_engine",return_value=engine), \
             patch("assistant.services.get_engine",return_value=engine), \
             patch("assistant.services.get_ai_execution_plan",return_value=execution), \
             patch("scripts.benchmark_inference_ab.telemetry",return_value=point()), \
             patch.object(quality,"read_bank",return_value=reference_bank), \
             patch.object(quality.models,"inventory",return_value={MODEL:{"usable":True,"digest":DIGEST}}), \
             patch.object(quality,"resident",return_value=[RESIDENT]):
            if interrupted:
                with self.assertRaises(KeyboardInterrupt):quality.evaluate(args,report,lambda:snapshots.append(deepcopy(report)))
            else:
                quality.evaluate(args,report,lambda:snapshots.append(deepcopy(report)))
        return report,captured,snapshots

    def test_actual_prior_answer_retained_without_correct_reference_preload(self):
        report,captured,_=self.exercise()
        self.assertEqual(report["status"],"completed_quality_pending")
        self.assertEqual(len(captured),2)
        history=[m["content"] for m in captured[1]["messages"] if m["role"]=="assistant"]
        self.assertEqual(history,[report["samples"][0]["raw_engine_output"]])
        self.assertIn("original mistakes",history[0])
        self.assertTrue(report["samples"][1]["routing"]["recent_history_used"])
        self.assertFalse(any("temporary versus persistent distinction" in m["content"] for r in captured for m in r["messages"]))

    def test_truncated_first_turn_stops_scenario(self):
        report,captured,_=self.exercise(truncate=True)
        self.assertEqual(report["status"],"incomplete_scenario")
        self.assertEqual(len(captured),1)
        self.assertTrue(report["samples"][0]["truncated"])

    def test_interruption_preserves_full_observed_partial_text(self):
        report,captured,snapshots=self.exercise(interrupted=True)
        self.assertEqual(report["samples"][0]["status"],"incomplete")
        self.assertIn("Actual generated",snapshots[-1]["samples"][0]["raw_engine_output"])
        self.assertEqual(len(captured),1)

    def test_fixture_database_contains_only_named_synthetic_sources(self):
        from conversations.models import Message
        from knowledge_base.models import Document,KnowledgeChunk
        bank=quality.read_bank()
        memory=quality.seed_setup(bank,"memory_present")
        absent=quality.seed_setup(bank,"memory_absent")
        self.assertTrue(Message.objects.filter(conversation_id=memory,text__contains="Amber Finch").exists())
        self.assertFalse(Message.objects.filter(conversation_id=absent,text__contains="Amber Finch").exists())
        self.assertEqual(Document.objects.count(),0)
        quality.seed_setup(bank,"lumen_note")
        self.assertEqual(Document.objects.count(),1)
        self.assertEqual(KnowledgeChunk.objects.count(),1)

    def test_evaluator_metadata_not_in_actual_service_request(self):
        report,captured,_=self.exercise(question="CS01",canary=True)
        self.assertEqual(len(captured),1)
        self.assertNotIn("EVALUATOR_ONLY_CANARY",json.dumps(captured))
        self.assertEqual(report["samples"][0]["requested_tokens"],160)

    def test_rag_fixture_uses_real_retrieval_not_reference_injection(self):
        report,captured,_=self.exercise(question="RM01",canary=True)
        self.assertTrue(report["samples"][0]["routing"]["rag_used"])
        self.assertIn("3.3",json.dumps(captured))
        self.assertNotIn("EVALUATOR_ONLY_CANARY",json.dumps(captured))
        self.assertEqual(report["samples"][0]["public_messages"],captured[0]["messages"])

    @override_settings(CONVERSATION_MEMORY_TOP_K=2,CONVERSATION_MEMORY_MAX_CHARS=400,CONVERSATION_MEMORY_MIN_RELEVANCE=.5)
    def test_old_memory_fixture_retrieves_user_fact_under_accepted_threshold(self):
        report,captured,_=self.exercise(question="RM05",canary=True)
        self.assertTrue(report["samples"][0]["routing"]["memory_used"])
        self.assertIn("Amber Finch",json.dumps(report["samples"][0]["public_messages"]))
        self.assertNotIn("EVALUATOR_ONLY_CANARY",json.dumps(captured))
