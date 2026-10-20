"""Step 64 orchestration only: no native inference, packages, audio or Pi access."""
from contextlib import nullcontext, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from scripts import compare_offline_stt as stt, compare_offline_models as models
from assistant.tests_offline_voice_validation import point


class MoonshineOnlyTests(SimpleTestCase):
    def execute(self, engine="moonshine", telemetry=None, failure=False, residency_failure=False):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            args=SimpleNamespace(engine=engine, reference=stt.REFERENCE,
                operator_approved_public_audio=True, python=root/"isolated/python",
                audio=root/"public.wav", host="http://127.0.0.1:11434", output=root/"report.json",
                moonshine_model=root/"small-streaming-en", whisper_bin=None, whisper_model=None)
            if engine is None:
                args.whisper_bin=root/"whisper"
                args.whisper_model=root/"ggml-base.en.bin"
            args.audio.write_bytes(b"public fixture")
            processes, commands = [], []
            def spawn(command,**kwargs):
                commands.append(command)
                self.assertTrue(kwargs["start_new_session"])
                self.assertNotIn("--engine",command)
                self.assertEqual(command[command.index("--baseline-prefix")+1],stt.sys.prefix)
                result={"status":"aborted" if failure else "sample_complete",
                        "reason":"stt_execution_failed" if failure else None}
                path=Path(command[command.index("--child-dir")+1]) / "result.json"
                path.write_text(json.dumps(result))
                process=MagicMock(returncode=2 if failure else 0)
                processes.append(process)
                return process
            def monitor(process,sample,check,report,**kwargs):
                self.assertEqual(kwargs["timeout"],120)
                report["telemetry"].append(point())
                check()
            with patch.object(stt.sys,"platform","linux"), patch.object(stt,"read_audio"), \
                 patch.object(models,"acquire_trial_lock") as lock, \
                 patch.object(models,"inventory",return_value={"qwen3.5:4b-q4_K_M":{}}), \
                 patch.object(models,"verify_identity",return_value="d8b0f5e9760c"+"0"*52), \
                 patch("scripts.validate_pi_performance.isolated_database",return_value=nullcontext(root)), \
                 patch("scripts.benchmark_inference_ab.telemetry",return_value=telemetry or point()), \
                 patch("scripts.validate_offline_voice.require_resident",
                       side_effect=models.TrialRefused("resident_digest_mismatch") if residency_failure else None) as resident, \
                 patch("scripts.validate_offline_voice.monitor",side_effect=monitor), \
                 patch.object(stt.subprocess,"Popen",side_effect=spawn), \
                 patch.object(models,"terminate") as terminate, \
                 patch("assistant.voice.factory.get_stt_provider") as factory, \
                 patch.object(stt,"whisper_transcribe") as whisper:
                status=stt.run(args)
            whisper.assert_not_called()
            factory.assert_not_called()
            self.assertEqual(terminate.call_count,len(processes))
            for call, process in zip(terminate.call_args_list,processes):
                self.assertIs(call.args[0],process)
            lock.return_value.close.assert_called_once()
            return status,json.loads(args.output.read_text()),commands,resident.call_count

    def test_moonshine_only_never_launches_or_requires_whisper(self):
        status,report,commands,checks=self.execute()
        self.assertEqual(status,0)
        self.assertEqual(len(commands),1)
        self.assertEqual(commands[0][commands[0].index("--worker-engine")+1],"moonshine")
        self.assertNotIn("--whisper-bin",commands[0])
        self.assertNotIn("--whisper-model",commands[0])
        self.assertEqual(checks,3)  # Before, monitored during, after completion.
        self.assertEqual(report["status"],"moonshine_complete_quality_pending")
        self.assertEqual(report["run_mode"],"moonshine_only")
        self.assertEqual(report["requested_engines"],["moonshine"])
        self.assertIsNone(report["whisper_controls"])
        self.assertIn("non_contemporaneous",report["historical_whisper_reference"]["source"])
        self.assertFalse(report["historical_whisper_reference"]["normalization_and_audio_fingerprint_verified"])

    def test_default_runs_both_in_original_order_and_status(self):
        status,report,commands,checks=self.execute(engine=None)
        self.assertEqual(status,0)
        self.assertEqual([c[c.index("--worker-engine")+1] for c in commands],["whisper","moonshine"])
        self.assertIn("--whisper-model",commands[0])
        self.assertNotIn("--whisper-model",commands[1])
        self.assertEqual(checks,6)
        self.assertEqual(report["status"],"comparison_complete_quality_pending")
        self.assertEqual(report["run_mode"],"two_engine_comparison")
        self.assertNotIn("historical_whisper_reference",report)

    @override_settings(STT_ENGINE="whisper_cpp",STT_WHISPER_MODEL="/models/ggml-base.en.bin",
                       OLLAMA_MODEL="qwen3.5:4b-q4_K_M",OLLAMA_KEEP_ALIVE="24h",OLLAMA_NUM_CTX=4096)
    def test_production_model_context_and_whisper_selection_unchanged(self):
        names=("STT_ENGINE","STT_WHISPER_MODEL","OLLAMA_MODEL","OLLAMA_KEEP_ALIVE","OLLAMA_NUM_CTX")
        before={name:getattr(settings,name) for name in names}
        self.execute()
        self.assertEqual({name:getattr(settings,name) for name in names},before)

    def test_start_temperature_ram_and_missing_telemetry_refuse(self):
        for sample in (point(temperature_c=50),point(ram_available_bytes=512*1024**2-1),
                       point(temperature_c=None)):
            status,report,commands,_=self.execute(telemetry=sample)
            self.assertEqual(status,2)
            self.assertFalse(commands)
            self.assertEqual(report["status"],"aborted")
            self.assertIn("reason",report)

    def test_residency_mismatch_refuses_without_worker(self):
        status,report,commands,_=self.execute(residency_failure=True)
        self.assertEqual(status,2)
        self.assertEqual(report["reason"],"resident_digest_mismatch")
        self.assertFalse(commands)

    def test_failed_worker_report_has_no_retry(self):
        for engine in ("moonshine",None):
            status,report,commands,_=self.execute(engine=engine,failure=True)
            self.assertEqual(status,2)
            self.assertEqual(len(commands),1)
            self.assertEqual(report["reason"],"stt_worker_failed_no_follow_on_trial")
            self.assertEqual(report["samples"][0]["result"]["status"],"aborted")

    def test_integrity_failure_precedes_native_constructor(self):
        module=SimpleNamespace(Transcriber=MagicMock(),ModelArch=SimpleNamespace(SMALL_STREAMING=4))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in stt.FILES: (root/name).touch()
            with patch.dict(stt.sys.modules,{"moonshine_voice":module}), \
                 patch.object(stt.importlib.metadata,"version",return_value="0.1.5"), \
                 patch.object(stt,"verify_moonshine_files",side_effect=ValueError("moonshine_model_integrity_mismatch")):
                with self.assertRaises(ValueError): stt.moonshine_transcribe(root,[])
            module.Transcriber.assert_not_called()


class EngineCLItests(SimpleTestCase):
    def test_operator_moonshine_needs_no_whisper_arguments(self):
        with patch.object(stt,"run",return_value=0) as run:
            self.assertEqual(stt.main(["--run","--engine","moonshine","--python","isolated/python",
                "--moonshine-model","models","--output","never-created-step64.json"]),0)
        self.assertIsNone(run.call_args.args[0].whisper_model)

    def test_invalid_operator_selection_and_ambiguous_child_flags_reject(self):
        for options in (["--run","--engine","whisper"],["--engine","invalid"],
                        ["--worker-engine","moonshine"],["--child-dir","tmp"],
                        ["--child-dir","tmp","--worker-engine","moonshine","--engine","moonshine"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                stt.main(options)
            self.assertEqual(caught.exception.code,2)

    def test_internal_worker_routes_both_engines_explicitly(self):
        for engine in ("moonshine","whisper"):
            with patch.object(stt,"child",return_value=0) as child:
                self.assertEqual(stt.main(["--child-dir","tmp","--worker-engine",engine]),0)
            self.assertEqual(child.call_args.args[0].worker_engine,engine)
            self.assertIsNone(child.call_args.args[0].engine)
