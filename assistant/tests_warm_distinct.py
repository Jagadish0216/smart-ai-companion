"""Distinct-question timing, cache accounting and observed-history integration."""
from contextlib import nullcontext
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from scripts import compare_offline_models as benchmark
from assistant.tests_offline_models import cool
from assistant.tests_warm_benchmark import sample, MODEL, DIGEST, RESIDENT


def distinct_sample(phase):
    result = copy.deepcopy(sample(phase))
    data = result["sample"]
    data["question"] = benchmark.distinct_fixture("technical", phase)[0]
    data["request"]["message_sha256"] = benchmark.sha(data["question"])
    data["request"]["user_sha256"] = benchmark.sha(data["question"])
    data["application_first_meaningful_text_ms"] = 6400 if phase == "cold" else 1800
    data["metrics"]["prompt_eval_count"] = 142 if phase == "cold" else 155
    data["metrics"]["prompt_eval_cached_count"] = 0 if phase == "cold" else 110
    return result


class WarmDistinctTests(SimpleTestCase):
    def test_all_six_fixtures_have_deterministic_different_questions(self):
        for fixture in benchmark.FIXTURES:
            cold, warm = (benchmark.distinct_fixture(fixture, phase) for phase in ("cold", "warm"))
            self.assertNotEqual(cold[0], warm[0])
            self.assertEqual(warm, benchmark.distinct_fixture(fixture, "warm"))

    def test_real_repeat_result_is_cache_accounted_without_calling_it_uncached(self):
        accounting = benchmark.prompt_accounting({"prompt_eval_count": 142, "prompt_eval_cached_count": 141})
        self.assertEqual(accounting["cached_tokens_reported"], 141)
        self.assertEqual(accounting["uncached_tokens_derived"], 1)
        self.assertAlmostEqual(accounting["cache_fraction"], 141 / 142, places=5)

    def test_distinct_pair_accepts_quantified_prefix_reuse_but_rejects_unknown_or_impossible_counts(self):
        samples = [distinct_sample("cold"), distinct_sample("warm")]
        self.assertIsNone(benchmark.validate_cold_warm(samples, distinct=True))
        for cached in (None, -1, 156, True):
            samples[1]["sample"]["metrics"]["prompt_eval_cached_count"] = cached
            self.assertIsNotNone(benchmark.validate_cold_warm(samples, distinct=True))
        samples[1]["sample"]["metrics"]["prompt_eval_cached_count"] = 155
        self.assertEqual(benchmark.validate_cold_warm(samples, distinct=True), "no_new_prompt_work")

    def test_replay_or_changed_controls_reload_and_policy_are_invalid(self):
        for mutate in (lambda s: s[1]["sample"].update(question=s[0]["sample"]["question"]),
                       lambda s: s[1]["sample"]["request"]["options"].update(num_ctx=2048),
                       lambda s: s[1]["load"].update(label="resident_reload_or_slow_load"),
                       lambda s: s[1]["sample"].update(resource_profile="ECO")):
            samples = [distinct_sample("cold"), distinct_sample("warm")]
            mutate(samples)
            self.assertIsNotNone(benchmark.validate_cold_warm(samples, distinct=True))

    def test_orchestration_records_partial_cache_and_uses_application_latency(self):
        def run(args, digest, report, phase):
            report.update(distinct_sample(phase))
        args = SimpleNamespace(model=MODEL, host="http://127.0.0.1:11434", warm_distinct=True, fixture="technical")
        report = {"inventory": {}}
        with patch.object(benchmark, "metadata", side_effect=[{"models": []}, {"models": [RESIDENT]}]), \
             patch.object(benchmark, "run_request", side_effect=run) as execute:
            benchmark.run_cold_warm(args, DIGEST, report, lambda: None)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(report["status"], "warm_distinct_complete_quality_pending")
        self.assertTrue(report["warm_distinct_under_two_seconds"])
        self.assertIsNone(report["uncached_warm_under_two_seconds"])
        self.assertFalse(report["quality_accepted"])

    def test_expiry_prevents_second_inference(self):
        args = SimpleNamespace(model=MODEL, host="http://127.0.0.1:11434", warm_distinct=True, fixture="technical")
        report = {"inventory": {}}
        with patch.object(benchmark, "metadata", return_value={"models": []}), \
             patch.object(benchmark, "run_request", side_effect=lambda a, d, r, p: r.update(distinct_sample(p))) as execute:
            with self.assertRaisesRegex(benchmark.TrialRefused, "premature_model_expiry"):
                benchmark.run_cold_warm(args, DIGEST, report, lambda: None)
        self.assertEqual(execute.call_count, 1)
        self.assertFalse(report["valid"])

    def test_context_file_contains_only_observed_turn_and_is_cleaned_on_safety_abort(self):
        report = {"telemetry": [], "previous_turn": {"question": "Remember Amber Finch.", "answer": "Noted."}}
        args = SimpleNamespace(model=MODEL, host="http://127.0.0.1:11434")
        seen = {}
        def spawn(command, **kwargs):
            path = Path(command[command.index("--previous-turn") + 1])
            seen["path"], seen["turn"] = path, json.loads(path.read_text())
            process = MagicMock()
            process.poll.return_value = None
            return process
        with patch("scripts.validate_pi_performance.isolated_database", return_value=nullcontext()), \
             patch("scripts.benchmark_inference_ab.telemetry", side_effect=[cool(), dict(cool(), temperature_c=70)]), \
             patch.object(benchmark.subprocess, "Popen", side_effect=spawn), patch.object(benchmark, "terminate") as terminate:
            with self.assertRaises(RuntimeError):
                benchmark.run_request(args, DIGEST, report, "warm")
        self.assertEqual(seen["turn"], report["previous_turn"])
        self.assertFalse(seen["path"].exists())
        terminate.assert_called_once()
        self.assertEqual(report["abort_reason"], "temperature_safety_margin")


class WarmDistinctHistoryIntegrationTests(TestCase):
    def test_real_service_all_fixtures_use_only_observed_history_and_keep_routes(self):
        from assistant.ai_engine import reset_engine
        from assistant.test_support import install_resource_report
        from conversations.models import Conversation, Message
        from knowledge_base.models import Document
        install_resource_report(self)
        names = ("AI_ENGINE", "OLLAMA_HOST", "OLLAMA_MODEL", "OLLAMA_NUM_THREAD", "OLLAMA_NUM_BATCH", "OLLAMA_TIMEOUT",
                 "OLLAMA_THINK", "OLLAMA_KEEP_ALIVE", "AI_LOCAL_GENERATION_TIMEOUT_SECONDS", "VOICE_COMPACT_STYLE",
                 "ONLINE_RETRIEVAL_ENABLED", "ONLINE_KNOWLEDGE_AUTO_PERSIST", "DEVICE_CONTROL_ENABLED",
                 "COMPANION_DISPLAY_ENABLED", "MQTT_HOST", "SEARXNG_BASE_URL", "RAG_ENABLED")
        self.addCleanup(reset_engine)
        with tempfile.TemporaryDirectory() as temporary, override_settings(**{name: getattr(settings, name, None) for name in names}):
            for fixture in benchmark.FIXTURES:
                cold = None
                for phase in ("cold", "warm"):
                    with self.subTest(fixture=fixture, phase=phase):
                        result = Path(temporary) / "result.json"
                        previous = Path(temporary) / "turn.json"
                        use_history = phase == "warm" and fixture in ("followup", "memory")
                        if use_history:
                            previous.write_text(json.dumps({k: cold[k] for k in ("question", "answer")}))
                        args = SimpleNamespace(child_dir=temporary, host="http://127.0.0.1:11434", model=MODEL,
                            verified_digest=DIGEST, fixture=fixture, child_result=str(result), required_residency=phase,
                            warm_distinct=True, previous_turn=str(previous) if use_history else None)
                        response = MagicMock()
                        response.__enter__.return_value = response
                        response.__iter__.return_value = [json.dumps({"done": True, "done_reason": "stop",
                            "message": {"content": "An SSD stores persistent data. Amber Finch is noted."}, "eval_count": 12,
                            "eval_duration": 1000000000, "prompt_eval_count": 142, "prompt_eval_cached_count": 0}).encode()]
                        with patch("scripts.validate_pi_performance.bootstrap"), patch("django.core.management.call_command"), \
                             patch("urllib.request.install_opener"), patch("urllib.request.urlopen", return_value=response), \
                             patch("django.db.connections.close_all"), \
                             patch.object(benchmark, "inventory", return_value={MODEL: {"usable": True, "digest": DIGEST}}), \
                             patch.object(benchmark, "metadata", return_value={"models": [] if phase == "cold" else [RESIDENT]}):
                            benchmark.child(args)
                        data = json.loads(result.read_text())
                        self.assertEqual(data["question"], benchmark.distinct_fixture(fixture, phase)[0])
                        self.assertEqual(data["request"]["options"], benchmark.request_options())
                        self.assertEqual(data["request"]["user_sha256"], benchmark.sha(data["question"]))
                        self.assertGreaterEqual(data["application_outside_engine_ms"], 0)
                        self.assertIsNone(data["correctness_review"]["correct"])
                        if phase == "cold":
                            cold = data
                        else:
                            self.assertNotEqual(data["question"], cold["question"])
                            self.assertEqual(data["rag_used"], fixture == "rag")
                            self.assertEqual(data["memory_used"], fixture == "memory")
                            if fixture == "followup":
                                self.assertTrue(data["recent_history_used"])
                            if use_history:
                                self.assertEqual(list(Message.objects.filter(sender="USER").order_by("id").values_list("text", flat=True)), [cold["question"], data["question"]])
                            if fixture == "technical":
                                # Subject-specific reference instructions legitimately differ between technical questions.
                                self.assertEqual(len(data["request"]["system_sha256"]), 64)
                        Conversation.objects.all().delete()
                        Document.objects.all().delete()
