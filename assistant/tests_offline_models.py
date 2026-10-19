"""Offline comparison safety gates and real service fixture paths; no inference."""
from contextlib import nullcontext
import copy
import json
import signal
import subprocess
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from scripts import compare_offline_models as benchmark


def cool():
    return {"temperature_c": 45, "measured_arm_hz": 2400000000,
            "ram_available_bytes": 3 * 1024 ** 3,
            "throttled": {k: False for k in ("under_voltage", "frequency_capped", "currently_throttled", "soft_temperature_limit")}}


class OfflineModelSafetyTests(SimpleTestCase):
    def test_digest_pin_checks_confirmed_prefix_and_full_requested_digest(self):
        model = benchmark.MODELS[1]
        digest = "baf6a787fdff" + "0" * 52
        installed = {"usable": True, "digest": digest}
        self.assertEqual(benchmark.verify_identity(model, installed, digest), digest)
        for item, expected in (({"usable": False}, None),
                               ({"usable": True, "digest": "a" * 64}, None),
                               (installed, "baf6a787fdff" + "1" * 52)):
            with self.subTest(item=item), self.assertRaises(RuntimeError):
                benchmark.verify_identity(model, item, expected)

    def test_other_resident_or_wrong_digest_refuses_without_model_mutations(self):
        digest = "baf6a787fdff" + "0" * 52
        model = benchmark.MODELS[1]
        benchmark.verify_residents([], model, digest)
        matching = {"name": model, "digest": digest}
        benchmark.verify_residents([matching], model, digest)
        for resident in ([{"name": benchmark.MODELS[0], "digest": "d" * 64}],
                         [matching, matching], [{"name": model, "digest": "a" * 64}]):
            with self.subTest(resident=resident), self.assertRaises(RuntimeError):
                benchmark.verify_residents(resident, model, digest)

    def test_load_prefill_and_decode_are_separate_and_warm_requires_context_and_low_load(self):
        metrics = {"load_duration_ms": 2, "prompt_eval_duration_ms": 5000, "eval_duration_ms": 9000}
        self.assertEqual(benchmark.load_summary(metrics, [])["label"], "cold")
        summary = benchmark.load_summary(metrics, [{"context_length": 4096}])
        self.assertEqual(summary["label"], "warm")
        self.assertEqual((summary["load_duration_ms"], summary["prompt_eval_duration_ms"], summary["decode_duration_ms"]), (2, 5000, 9000))
        self.assertEqual(benchmark.load_summary(dict(metrics, load_duration_ms=500), [{}])["label"], "resident_reload_or_slow_load")
        self.assertEqual(benchmark.load_summary({}, [])["label"], "unknown")

    def test_deadline_cleanup_escalates_only_owned_session_and_reaps_child(self):
        process = MagicMock(pid=4242)
        process.wait.side_effect = [subprocess.TimeoutExpired("owned-child", 2), 0]
        with patch.object(benchmark.os, "killpg", create=True) as killpg, patch.object(signal, "SIGKILL", 9, create=True):
            benchmark.terminate(process)
        self.assertEqual([call.args for call in killpg.call_args_list],
                         [(4242, signal.SIGTERM), (4242, 9)])
        self.assertEqual(process.wait.call_count, 2)

    def test_remote_credentials_proxy_style_urls_and_redirects_rejected(self):
        self.assertEqual(benchmark.local_host("http://127.0.0.1:11434/"), "http://127.0.0.1:11434")
        for host in ("https://localhost", "http://cloud.example", "http://192.168.1.5:11434",
                     "http://user:password@localhost", "http://localhost/path", "http://localhost?remote=1"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                benchmark.local_host(host)
        with self.assertRaises(ValueError):
            benchmark.NoRedirect().redirect_request(None)

    def test_missing_telemetry_current_pressure_and_thermal_margin_fail_closed(self):
        sample = cool()
        sample["throttled"]["throttling_occurred"] = True
        self.assertIsNone(benchmark.pressure_reason(sample, starting=True))
        for key in ("temperature_c", "throttled", "measured_arm_hz"):
            invalid = dict(sample, **{key: None})
            self.assertEqual(benchmark.pressure_reason(invalid), "required_telemetry_unavailable")
        for invalid_hz in (0, -1, True, "2400000000"):
            self.assertEqual(benchmark.pressure_reason(dict(sample, measured_arm_hz=invalid_hz)), "required_telemetry_unavailable")
        self.assertEqual(benchmark.pressure_reason(dict(sample, ram_available_bytes=511 * 1024 ** 2)), "low_available_memory")
        self.assertEqual(benchmark.pressure_reason(dict(sample, temperature_c=55), starting=True), "cool_start_required")
        self.assertIsNone(benchmark.pressure_reason(dict(sample, temperature_c=69.9)))
        self.assertEqual(benchmark.pressure_reason(dict(sample, temperature_c=70)), "temperature_safety_margin")
        for key in ("under_voltage", "frequency_capped", "currently_throttled", "soft_temperature_limit"):
            invalid = dict(sample, throttled={**sample["throttled"], key: True})
            self.assertEqual(benchmark.pressure_reason(invalid), "current_power_or_throttle_pressure")

    @patch("scripts.compare_offline_models.metadata")
    def test_inventory_requires_exact_installed_local_tag_and_full_digest(self, metadata):
        model = benchmark.MODELS[0]
        valid = {"name": model, "digest": "a" * 64, "details": {"format": "gguf"}, "size": 123}
        for updates, usable in (({}, True), ({"remote_host": "cloud"}, False),
                                ({"digest": "short"}, False), ({"name": model + "-alias"}, False)):
            metadata.return_value = {"models": [{**valid, **updates}]}
            inventory = benchmark.inventory("http://localhost:11434")
            self.assertEqual(inventory[model]["usable"], usable)
            self.assertFalse(inventory[benchmark.MODELS[1]]["installed"])

    def report(self):
        return {"status": "sample_complete_quality_pending", "fixture": "fact", "model": benchmark.MODELS[0],
                "workload_sha256": "work", "source_sha256": "source", "head": "head", "tracked_diff_sha256": "diff",
                "output_mode": "text", "options": benchmark.request_options(), "ollama_version": "test",
                "telemetry": [cool()], "resident_before": [],
                "load": {"label": "cold"},
                "sample": {"request": {"message_sha256": "prompt", "options": benchmark.request_options(), "think": False, "keep_alive": "24h"},
                           "num_predict": 48, "route": "local", "resource_profile": "BALANCED", "rag_used": False,
                           "memory_used": False, "recent_history_used": False}}

    def test_comparison_refuses_aborts_changed_prompts_budgets_and_warmness(self):
        a = self.report()
        b = copy.deepcopy(a)
        b["model"] = benchmark.MODELS[1]
        result = benchmark.compare_reports(a, b)
        self.assertEqual(result["status"], "comparison_pending_quality")
        self.assertFalse(result["quality_accepted"])
        for mutate in (lambda r: r.update(status="aborted"),
                       lambda r: r["sample"]["request"].update(message_sha256="different"),
                       lambda r: r["sample"].update(num_predict=32),
                       lambda r: r.update(resident_before=[{}]),
                       lambda r: r.update(load={"label": "warm"}),
                       lambda r: r["telemetry"][0].update(temperature_c=60)):
            b = copy.deepcopy(a)
            mutate(b)
            self.assertEqual(benchmark.compare_reports(a, b)["status"], "invalid_pair")

    def test_thermal_abort_terminates_owned_child_and_preserves_invalid_report(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "new-report"
            args = ["compare", "--run", "--model", benchmark.MODELS[0], "--output", str(output)]
            hot = dict(cool(), temperature_c=70)
            with patch("sys.argv", args), patch.object(benchmark.platform, "system", return_value="Linux"), \
                 patch.object(benchmark, "inventory", return_value={benchmark.MODELS[0]: {"usable": True, "digest": "d8b0f5e9760c" + "0" * 52}}), \
                 patch.object(benchmark, "metadata", side_effect=[{"version": "test"}, {"models": []}]), \
                 patch.object(benchmark, "acquire_trial_lock"), \
                 patch("scripts.validate_pi_performance.isolated_database", return_value=nullcontext()), \
                 patch("scripts.benchmark_inference_ab.telemetry", side_effect=[cool(), hot]), \
                 patch.object(benchmark.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
                 patch.object(benchmark.subprocess, "check_output", side_effect=["test-head", b"test-diff"]), \
                 patch.object(benchmark.subprocess, "Popen") as popen, patch.object(benchmark, "terminate") as terminate:
                popen.return_value.poll.return_value = None
                with self.assertRaises(RuntimeError):
                    benchmark.main()
                terminate.assert_called_once_with(popen.return_value)
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(report["status"], "aborted")
            self.assertEqual(report["abort_reason"], "temperature_safety_margin")
            self.assertNotIn("sample", report)

    def test_qwen_residency_blocks_llama_before_schema_or_inference_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "new-report"
            digest = "baf6a787fdff" + "0" * 52
            args = ["compare", "--run", "--model", benchmark.MODELS[1], "--output", str(output)]
            with patch("sys.argv", args), patch.object(benchmark.platform, "system", return_value="Linux"), \
                 patch.object(benchmark, "inventory", return_value={benchmark.MODELS[1]: {"usable": True, "digest": digest}}), \
                 patch.object(benchmark, "metadata", side_effect=[{"version": "test"}, {"models": [{"name": benchmark.MODELS[0]}]}]), \
                 patch.object(benchmark, "acquire_trial_lock") as lock, \
                 patch.object(benchmark.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
                 patch.object(benchmark.subprocess, "check_output", side_effect=["test-head", b"test-diff"]), \
                 patch.object(benchmark.subprocess, "Popen") as popen, \
                 patch("scripts.validate_pi_performance.isolated_database") as database:
                with self.assertRaises(benchmark.TrialRefused):
                    benchmark.main()
                popen.assert_not_called()
                database.assert_not_called()
                lock.return_value.close.assert_called_once()
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(report["abort_reason"], "other_model_resident_no_unload_attempted")


class OfflineFixtureIntegrationTests(TestCase):
    def test_real_service_uses_rag_memory_followup_and_exact_generation_controls(self):
        from assistant.ai_engine import reset_engine
        from assistant.test_support import install_resource_report
        from conversations.models import Conversation
        from knowledge_base.models import Document
        install_resource_report(self)
        overrides = {name: getattr(settings, name, None) for name in (
            "AI_ENGINE", "OLLAMA_HOST", "OLLAMA_MODEL", "OLLAMA_NUM_THREAD", "OLLAMA_NUM_BATCH", "OLLAMA_TIMEOUT",
            "OLLAMA_THINK", "OLLAMA_KEEP_ALIVE", "VOICE_COMPACT_STYLE", "ONLINE_RETRIEVAL_ENABLED", "ONLINE_KNOWLEDGE_AUTO_PERSIST",
            "DEVICE_CONTROL_ENABLED", "COMPANION_DISPLAY_ENABLED", "MQTT_HOST", "SEARXNG_BASE_URL", "RAG_ENABLED")}
        self.addCleanup(reset_engine)
        with tempfile.TemporaryDirectory() as temp, override_settings(**overrides):
            for fixture in benchmark.FIXTURES:
                with self.subTest(fixture=fixture):
                    result = Path(temp) / "sample.json"
                    digest = "baf6a787fdff" + "0" * 52
                    args = SimpleNamespace(child_dir=temp, host="http://127.0.0.1:11434", model=benchmark.MODELS[1], verified_digest=digest,
                                           fixture=fixture, child_result=str(result))
                    response = MagicMock()
                    response.__enter__.return_value = response
                    response.__iter__.return_value = [json.dumps({"done": True, "done_reason": "stop",
                        "message": {"content": "A synthetic complete answer."}, "eval_count": 7,
                        "eval_duration": 1000000000, "prompt_eval_count": 50}).encode()]
                    with patch("scripts.validate_pi_performance.bootstrap"), patch("django.core.management.call_command"), \
                         patch("urllib.request.install_opener"), patch("urllib.request.urlopen", return_value=response) as urlopen, \
                         patch("django.db.connections.close_all"), \
                         patch.object(benchmark, "inventory", return_value={benchmark.MODELS[1]: {"usable": True, "digest": digest}}), \
                         patch.object(benchmark, "metadata", return_value={"models": []}):
                        benchmark.child(args)
                    sample = json.loads(result.read_text())
                    self.assertEqual(sample["request"]["options"], benchmark.request_options())
                    self.assertEqual(sample["request"]["model"], benchmark.MODELS[1])
                    self.assertEqual(sample["request"]["keep_alive"], "60s")
                    urlopen.assert_called_once()
                    self.assertEqual(sample["rag_used"], fixture == "rag")
                    self.assertEqual(sample["memory_used"], fixture == "memory")
                    if fixture == "followup":
                        self.assertTrue(sample["recent_history_used"])
                    Conversation.objects.all().delete()
                    Document.objects.all().delete()
