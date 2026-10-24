"""Cold/warm orchestration and invalidation tests; synthetic timings, no inference."""
from contextlib import nullcontext
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from scripts import compare_offline_models as benchmark
from assistant.tests_offline_models import cool


MODEL = benchmark.MODELS[1]
DIGEST = "baf6a787fdff" + "0" * 52  # Synthetic full digest for testing only.
RESIDENT = {"name": MODEL, "digest": DIGEST, "context_length": 4096}


def sample(phase):
    return {"phase": phase, "status": "sample_complete_quality_pending",
            "load": {"label": phase}, "sample": {
                "answer": "The capital is New Delhi, north of the equator.",
                "num_predict": 48, "route": "local", "resource_profile": "BALANCED",
                "rag_used": False, "memory_used": False, "recent_history_used": False,
                "request": {"message_sha256": "same-prompt", "model": MODEL, "options": benchmark.request_options(), "think": False, "keep_alive": "60s"},
                "first_meaningful_text_ms": 19844 if phase == "cold" else 1500,
                "total_answer_s": 21.919 if phase == "cold" else 3.2,
                "metrics": {"load_duration_ms": 17433.33 if phase == "cold" else 2,
                            "prompt_eval_duration_ms": 2407.88 if phase == "cold" else 1400,
                            "eval_duration_ms": 1860.19, "eval_count": 17,
                            "prompt_eval_count": 142, "prompt_eval_cached_count": 0,
                            "tokens_per_sec": 9.1, "done_reason": "stop"}}}


class ColdWarmBenchmarkTests(SimpleTestCase):
    def args(self):
        return SimpleNamespace(host="http://127.0.0.1:11434", model=MODEL)

    def run_sample(self, args, digest, report, phase):
        report.update(copy.deepcopy(sample(phase)))

    def test_success_uses_two_independently_measured_requests_and_no_load_subtraction(self):
        report = {"inventory": {}}
        snapshots = []
        with patch.object(benchmark, "metadata", side_effect=[{"models": []}, {"models": [RESIDENT]}]), \
             patch.object(benchmark, "run_request", side_effect=self.run_sample) as run:
            benchmark.run_cold_warm(self.args(), DIGEST, report, lambda: snapshots.append(copy.deepcopy(report)))
        self.assertEqual([call.args[3] for call in run.call_args_list], ["cold", "warm"])
        self.assertEqual(report["samples"][1]["sample"]["first_meaningful_text_ms"], 1500)
        self.assertNotEqual(19844 - 17433.33, 1500)
        self.assertTrue(report["valid"])
        self.assertTrue(report["uncached_warm_under_two_seconds"])
        self.assertFalse(report["quality_accepted"])
        self.assertTrue(all(not s["valid"] for s in snapshots[:-1]))

    def test_premature_expiry_skips_warm_inference_and_keeps_cold_sample(self):
        report = {"inventory": {}}
        with patch.object(benchmark, "metadata", return_value={"models": []}), \
             patch.object(benchmark, "run_request", side_effect=self.run_sample) as run:
            with self.assertRaisesRegex(benchmark.TrialRefused, "premature_model_expiry"):
                benchmark.run_cold_warm(self.args(), DIGEST, report, lambda: None)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(report["samples"][0]["sample"]["answer"], sample("cold")["sample"]["answer"])
        self.assertEqual(report["samples"][1]["status"], "aborted")
        self.assertFalse(report["valid"])

    def test_immediate_residency_checks_refuse_wrong_model_digest_context_and_nonempty_cold(self):
        for resident, phase in (([RESIDENT], "cold"), ([], "warm"),
                                ([dict(RESIDENT, name=benchmark.MODELS[0])], "warm"),
                                ([dict(RESIDENT, digest="a" * 64)], "warm"),
                                ([dict(RESIDENT, context_length=2048)], "warm")):
            with self.subTest(resident=resident, phase=phase), self.assertRaises(benchmark.TrialRefused):
                benchmark.verify_request_residency(resident, MODEL, DIGEST, phase)

    def test_failed_cold_initialization_never_attempts_warm_request(self):
        report = {"inventory": {}}
        with patch.object(benchmark, "metadata", return_value={"models": []}), \
             patch.object(benchmark, "run_request", side_effect=RuntimeError("synthetic failure")) as run:
            with self.assertRaises(RuntimeError):
                benchmark.run_cold_warm(self.args(), DIGEST, report, lambda: None)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(len(report["samples"]), 1)
        self.assertFalse(report["valid"])

    def test_truncated_cold_initialization_never_attempts_warm_request(self):
        def truncated(args, digest, report, phase):
            self.run_sample(args, digest, report, phase)
            report["sample"]["metrics"]["done_reason"] = "length"
        report = {"inventory": {}}
        with patch.object(benchmark, "metadata", return_value={"models": []}), \
             patch.object(benchmark, "run_request", side_effect=truncated) as run:
            with self.assertRaisesRegex(benchmark.TrialRefused, "cold_request_failed_validation"):
                benchmark.run_cold_warm(self.args(), DIGEST, report, lambda: None)
        self.assertEqual(run.call_count, 1)

    def test_cache_hits_unknown_cache_and_changed_evaluated_work_are_invalid(self):
        for value, reason in ((None, "prompt_cache_unverified"), (100, "prompt_cache_reuse")):
            samples = [sample("cold"), sample("warm")]
            samples[1]["sample"]["metrics"]["prompt_eval_cached_count"] = value
            self.assertEqual(benchmark.validate_cold_warm(samples), reason)
        samples = [sample("cold"), sample("warm")]
        samples[1]["sample"]["metrics"]["prompt_eval_count"] = 1
        self.assertEqual(benchmark.validate_cold_warm(samples), "evaluated_prompt_work_changed")

    def test_reload_changed_parameters_and_missing_reports_cannot_form_valid_pair(self):
        self.assertEqual(benchmark.validate_cold_warm([sample("cold")]), "incomplete_cold_warm_pair")
        samples = [sample("cold"), sample("warm")]
        samples[1]["load"]["label"] = "resident_reload_or_slow_load"
        self.assertEqual(benchmark.validate_cold_warm(samples), "model_reloaded_or_load_unverified")
        samples = [sample("cold"), sample("warm")]
        samples[1]["sample"]["request"]["options"]["num_ctx"] = 2048
        self.assertEqual(benchmark.validate_cold_warm(samples), "unequal_cold_warm_request")

    def test_warm_safety_abort_does_not_spawn_child(self):
        report = {"telemetry": [], "resident_before": [RESIDENT]}
        with patch("scripts.validate_pi_performance.isolated_database", return_value=nullcontext()), \
             patch("scripts.benchmark_inference_ab.telemetry", return_value=dict(cool(), temperature_c=70)), \
             patch.object(benchmark.subprocess, "Popen") as popen:
            with self.assertRaises(RuntimeError):
                benchmark.run_request(self.args(), DIGEST, report, "warm")
        popen.assert_not_called()
        self.assertEqual(report["abort_reason"], "temperature_safety_margin")

    def test_request_deadline_terminates_owned_child_and_cleans_temporary_directory(self):
        report = {"telemetry": [], "resident_before": [RESIDENT]}
        with patch("scripts.validate_pi_performance.isolated_database", return_value=nullcontext()), \
             patch("scripts.benchmark_inference_ab.telemetry", return_value=cool()), \
             patch.object(benchmark.subprocess, "Popen") as popen, \
             patch.object(benchmark.time, "monotonic", side_effect=[0, 46]), \
             patch.object(benchmark, "terminate") as terminate:
            popen.return_value.poll.return_value = None
            with self.assertRaises(RuntimeError):
                benchmark.run_request(self.args(), DIGEST, report, "warm")
            terminate.assert_called_once_with(popen.return_value)
            command = popen.call_args.args[0]
            directory = Path(command[command.index("--child-dir") + 1])
            self.assertFalse(directory.exists())
        self.assertEqual(report["abort_reason"], "45_second_wall_deadline")

    def test_incomplete_warm_failure_is_written_as_invalid_with_cold_evidence(self):
        def fail_warm(args, digest, report, phase):
            if phase == "warm":
                raise RuntimeError("synthetic failure")
            self.run_sample(args, digest, report, phase)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report"
            argv = ["compare", "--cold-warm", "--model", MODEL, "--output", str(output)]
            with patch("sys.argv", argv), patch.object(benchmark.platform, "system", return_value="Linux"), \
                 patch.object(benchmark, "acquire_trial_lock"), \
                 patch.object(benchmark, "inventory", return_value={MODEL: {"usable": True, "digest": DIGEST}}), \
                 patch.object(benchmark, "metadata", side_effect=[{"version": "test"}, {"models": []}, {"models": []}, {"models": [RESIDENT]}]), \
                 patch.object(benchmark.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
                 patch.object(benchmark.subprocess, "check_output", side_effect=["test-head", b"test-diff"]), \
                 patch.object(benchmark, "run_request", side_effect=fail_warm):
                with self.assertRaises(RuntimeError):
                    benchmark.main()
            report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["status"], "invalid_cold_warm")
        self.assertFalse(report["valid"])
        self.assertIsNone(report["uncached_warm_under_two_seconds"])
        self.assertEqual(report["samples"][0]["status"], "sample_complete_quality_pending")
        self.assertEqual(report["samples"][1]["status"], "aborted")
