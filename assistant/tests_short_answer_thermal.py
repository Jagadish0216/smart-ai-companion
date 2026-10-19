"""Mock-only single request and safety tests; never infer on Windows."""
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from scripts import test_short_answer_thermal as smoke
from assistant.tests_quality_evaluation import point

DIGEST = "a80c4f17acd5" + "0" * 52
INSTALLED = dict(usable=True, digest=DIGEST, artifact_bytes=1024**3)


class ShortThermalTests(SimpleTestCase):
    def test_exact_identity(self):
        self.assertEqual(smoke.models.verify_identity(smoke.MODEL, INSTALLED), DIGEST)
        with self.assertRaises(smoke.models.TrialRefused):
            smoke.models.verify_identity(smoke.MODEL, dict(INSTALLED, digest="a"*64))

    def test_start_strictly_below_fifty(self):
        smoke.preflight(point(temperature_c=49.9), INSTALLED, [])
        for temperature in (50, 54.9, 55, 70):
            with self.assertRaises(smoke.models.TrialRefused):
                smoke.preflight(point(temperature_c=temperature), INSTALLED, [])

    def test_active_flags_and_low_memory(self):
        for flag in ("under_voltage", "frequency_capped", "currently_throttled", "soft_temperature_limit"):
            sample = point(); sample["throttled"][flag] = True
            with self.assertRaises(smoke.models.TrialRefused):smoke.preflight(sample, INSTALLED, [])
        for ram, resident in ((511*1024**2, [{}]), (1024**3, [])):
            with self.assertRaises(smoke.models.TrialRefused):smoke.preflight(point(ram_available_bytes=ram), INSTALLED, resident)

    def test_conflicting_residency(self):
        with patch.object(smoke.models, "metadata", return_value={"models":[{"name":"llama3.2:1b"}]}):
            with self.assertRaises(smoke.models.TrialRefused):smoke.quality.resident("host", smoke.MODEL, DIGEST)

    def test_runtime_seventy_and_timeout(self):
        process = Mock(); process.poll.return_value = None
        report = dict(telemetry=[])
        with self.assertRaisesRegex(smoke.models.TrialRefused,"temperature_safety_margin"):
            smoke.monitor(process, lambda: point(temperature_c=70), lambda: None, report, timeout=30)
        with self.assertRaisesRegex(smoke.models.TrialRefused,"bounded_cycle_timeout"):
            smoke.monitor(process, point, lambda: None, report, clock=Mock(side_effect=[0,30]), timeout=30)

    def stream(self, partial=False):
        events = [dict(message=dict(content="New Delhi is the capital of India."))]
        if not partial:
            events.append(dict(done=True,done_reason="stop",load_duration=1_000_000,
                prompt_eval_duration=2_000_000,eval_duration=1_000_000_000,eval_count=9,prompt_eval_count=20))
        return io.BytesIO(b"\n".join(json.dumps(e).encode() for e in events))

    def infer(self, partial=False):
        opener = Mock(); opener.open.return_value = self.stream(partial)
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/"sample.json"
            with patch.object(smoke.quality,"require_pi"), patch.object(smoke.models,"inventory",return_value={smoke.MODEL:INSTALLED}), patch.object(smoke.quality,"resident",return_value=[]), patch("scripts.benchmark_inference_ab.telemetry",side_effect=point):
                status=smoke.infer("http://127.0.0.1:11434",DIGEST,target,opener=opener)
            sample=json.loads(target.read_text())
        self.assertEqual(opener.open.call_count,1)
        payload=json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload["options"],smoke.OPTIONS)
        self.assertEqual(payload["messages"],[dict(role="user",content=smoke.QUESTION)])
        self.assertNotIn("New Delhi",str(payload))
        return status,sample

    def test_complete_answer_one_request(self):
        status,sample=self.infer()
        self.assertEqual(status,0)
        self.assertTrue(sample["complete"])
        self.assertTrue(sample["expected_answer_present"])
        self.assertFalse(sample["truncated"])

    def test_partial_preserved_no_retry(self):
        status,sample=self.infer(True)
        self.assertEqual(status,2)
        self.assertIn("New Delhi",sample["answer"])
        self.assertFalse(sample["complete"])

    def test_token_limit_correct_answer_still_truncated(self):
        _,sample=self.infer()
        sample["metrics"]["eval_count"]=32
        self.assertFalse(smoke.classify(sample))
        self.assertTrue(sample["expected_answer_present"])
        self.assertTrue(sample["truncated"])

    def test_parent_abort_retains_child_and_never_passes(self):
        process=Mock(); process.returncode=0
        def start(command, **kwargs):
            target=Path(command[command.index("--child-result")+1])
            target.write_text(json.dumps(dict(answer="New Delhi",metrics={},stream_done=False)))
            return process
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as directory:
            args=SimpleNamespace(output=str(Path(directory)/"result"),host="http://127.0.0.1:11434")
            with patch.object(smoke.quality,"require_pi"), patch.object(smoke.models,"acquire_trial_lock",return_value=Mock()), patch("scripts.validate_pi_performance.isolated_database",return_value=nullcontext()), patch("scripts.benchmark_inference_ab.telemetry",side_effect=point), patch.object(smoke.models,"inventory",return_value={smoke.MODEL:INSTALLED}), patch.object(smoke.quality,"resident",return_value=[]), patch.object(smoke.subprocess,"Popen",side_effect=start) as launch, patch.object(smoke,"monitor",side_effect=smoke.models.TrialRefused("temperature_safety_margin")), patch.object(smoke.models,"terminate") as terminate:
                self.assertEqual(smoke.run(args),2)
            report=json.loads((Path(args.output)/"report.json").read_text())
            self.assertEqual(report["sample"]["answer"],"New Delhi")
            self.assertFalse(report["success"])
            self.assertEqual(report["status"],"aborted")
            launch.assert_called_once(); terminate.assert_called_once_with(process)
