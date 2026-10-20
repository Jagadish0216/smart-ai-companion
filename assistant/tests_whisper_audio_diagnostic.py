"""Approved synthetic PCM only; no real audio, FFmpeg, STT or Pi execution."""
from contextlib import redirect_stderr, redirect_stdout
import io
import math
from pathlib import Path
import struct
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import wave

from django.test import SimpleTestCase
from scripts import whisper_audio_diagnostic as audio, compare_offline_stt as stt


def wav(path, frames, *, native=True):
    with wave.open(str(path),"wb") as target:
        target.setparams((2 if native else 1,4 if native else 2,48000 if native else 16000,0,"NONE",""))
        target.writeframes(frames)


class AudioDiagnosticTests(SimpleTestCase):
    def test_left_channel_measurement_not_stereo_average(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"public.wav"
            wav(path,struct.pack("<ii",2**29,0)*960)
            result=audio.analyze(path)
            self.assertEqual(result["channels"],2)
            self.assertEqual(result["sample_rate"],48000)
            self.assertEqual(result["peak"],.25)
            self.assertEqual(result["rms"],.25)
            self.assertEqual(result["peak_to_rms"],1)
            self.assertIsNone(result["signal_to_noise_db"])
            self.assertEqual(result["noise_reduction"]["status"],"skipped")
            wav(path,struct.pack("<ii",0,2**30)*960)
            self.assertEqual(audio.analyze(path)["rms"],0)

    def test_frame_statistics_active_proxy_and_clipping_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"public.wav"
            wav(path,struct.pack("<h",0)*320*20+struct.pack("<h",8192)*320*20,native=False)
            result=audio.analyze(path)
            self.assertEqual(result["frame_rms"]["p10"],0)
            self.assertEqual(result["estimated_active_rms"],.25)
            self.assertEqual(result["clipping_percent"],0)
            wav(path,struct.pack("<hh",32767,-32768)*320,native=False)
            self.assertEqual(audio.analyze(path)["clipping_percent"],100)

    def test_exact_chains_gain_cap_and_no_speaker_loudness(self):
        self.assertEqual(audio.filter_chain("baseline",.3232,2),"pan=mono|c0=c0,aresample=16000")
        self.assertEqual(audio.filter_chain("highpass",.3232,2),"pan=mono|c0=c0,aresample=16000,highpass=f=80:p=2,alimiter=limit=0.95:level=false")
        chain=audio.filter_chain("normalize",.3232,2)
        self.assertIn("volume=1.547029703",chain)
        self.assertIn("limit=0.95:level=false",chain)
        self.assertIn("volume=4.000000000",audio.filter_chain("normalize",.001,1))
        self.assertIn("volume=1.000000000",audio.filter_chain("normalize",0,1))
        self.assertNotIn("loudnorm",chain)
        self.assertNotIn("pan=",audio.filter_chain("baseline",.25,1))
        with self.assertRaises(ValueError): audio.filter_chain("denoise",.25,2)

    def test_prepare_preserves_source_and_validates_output_format(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/"original.wav"
            wav(source,struct.pack("<ii",2**29,0)*960)
            original=source.read_bytes()
            def ffmpeg(command,**kwargs):
                self.assertIn("-n",command)
                self.assertEqual(kwargs["timeout"],25)
                wav(Path(command[-1]),struct.pack("<h",8192)*320,native=False)
                return SimpleNamespace(returncode=0)
            with patch.object(audio.subprocess,"run",side_effect=ffmpeg):
                output,report=audio.prepare_variant(source,root,"normalize")
            self.assertEqual(source.read_bytes(),original)
            self.assertEqual(len(stt.read_audio(output)),320)
            self.assertEqual(report["source"]["fingerprint"],stt.fingerprint(source))
            with self.assertRaises(ValueError): audio.prepare_variant(source,root,"normalize")

    def test_filter_failure_and_peak_violation_remove_partial_not_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/"original.wav"
            wav(source,struct.pack("<h",8192)*320,native=False)
            original=source.read_bytes()
            for code in (1,0):
                def ffmpeg(command,**kwargs):
                    wav(Path(command[-1]),struct.pack("<h",32767)*320,native=False)
                    return SimpleNamespace(returncode=code)
                with patch.object(audio.subprocess,"run",side_effect=ffmpeg):
                    with self.assertRaises(ValueError): audio.prepare_variant(source,root,"normalize")
                self.assertFalse((root/"normalize.wav").exists())
                self.assertEqual(source.read_bytes(),original)

    def test_replay_is_actual_vad_and_rms_consistent_with_production(self):
        from scripts import voice_loop
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"public.wav"
            chunks=[struct.pack("<ii",int(level*2**31),0)*960 for level in [.001]*25+[.06]*20+[.001]*40]
            wav(path,b"".join(chunks))
            result=audio.replay(path)
            actual=audio.AdaptiveCaptureVAD(**result["options"])
            for chunk in chunks:
                actual.feed(voice_loop._normalized_alsa_left_rms(chunk),.02)
                if actual.stop_reason: break
            self.assertEqual(result["stop_reason"],actual.stop_reason)
            self.assertEqual(result["accepted"],actual.accepted)
            self.assertAlmostEqual(result["noise_floor"],actual.noise_floor)
            self.assertTrue(result["simulated_replay"])

    def test_single_variant_required_and_readonly_analysis_never_infers(self):
        for flags in (["--run"],["--run","--variant","all"],["--run","--variant","baseline","--variant","normalize"]):
            # Repeated selections must be rejected, not silently run a different trial.
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                audio.main(["--audio","public.wav","--operator-approved-public-audio"]+flags)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"public.wav";wav(path,struct.pack("<h",0)*16000,native=False)
            with patch.object(stt,"run") as run, patch.object(audio,"prepare_variant") as prepare,redirect_stdout(io.StringIO()):
                self.assertEqual(audio.main(["--audio",str(path),"--operator-approved-public-audio"]),0)
            run.assert_not_called(); prepare.assert_not_called()

    def test_cli_routes_one_guarded_whisper_variant(self):
        with patch.object(stt,"run",return_value=0) as run:
            self.assertEqual(audio.main(["--audio","public.wav","--operator-approved-public-audio","--run",
                "--variant","baseline","--whisper-bin","whisper","--whisper-model","ggml-base.en.bin",
                "--output","uncreated-step65-report.json"]),0)
        self.assertEqual(run.call_args.kwargs,{"whisper_variant":"baseline"})


class GuardedVariantTests(SimpleTestCase):
    def execute(self,**kwargs):
        from assistant.tests_moonshine_only import MoonshineOnlyTests
        case=MoonshineOnlyTests("test_default_runs_both_in_original_order_and_status")
        original=stt.run
        with patch.object(audio,"load_left"),patch.object(stt,"run",side_effect=lambda args:original(args,whisper_variant="baseline")):
            return case.execute(engine=None,**kwargs)

    def test_exactly_one_whisper_and_no_moonshine_with_report_metrics(self):
        code,report,commands,checks=self.execute()
        self.assertEqual(code,0)
        self.assertEqual(len(commands),1)
        self.assertEqual(checks,3)
        self.assertEqual(commands[0][commands[0].index("--worker-engine")+1],"whisper")
        self.assertEqual(commands[0][commands[0].index("--audio-variant")+1],"baseline")
        self.assertNotIn("--moonshine-model",commands[0])
        self.assertEqual(report["status"],"whisper_variant_complete_quality_pending")
        self.assertEqual(report["requested_engines"],["whisper"])
        self.assertIsNone(report["moonshine_controls"])
        self.assertIn("historical_whisper_reference",report)

    def test_temperature_memory_power_residency_and_no_retry(self):
        from assistant.tests_offline_voice_validation import point
        pressure=point(); pressure["throttled"]["under_voltage"]=True
        for telemetry in (point(temperature_c=50),point(ram_available_bytes=1),pressure):
            code,report,commands,_=self.execute(telemetry=telemetry)
            self.assertEqual(code,2); self.assertFalse(commands)
            self.assertIn("reason",report)
        code,report,commands,_=self.execute(residency_failure=True)
        self.assertEqual(code,2);self.assertFalse(commands)
        code,report,commands,_=self.execute(failure=True)
        self.assertEqual(code,2);self.assertEqual(len(commands),1)

    def test_child_prepares_then_reports_transcript_wer_and_processing_failure(self):
        import json
        resource=SimpleNamespace(RUSAGE_SELF=0,RUSAGE_CHILDREN=1,
            getrusage=lambda _:SimpleNamespace(ru_utime=0,ru_stime=0,ru_maxrss=1))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/"source.wav";model=root/"ggml-base.en.bin"
            wav(source,struct.pack("<h",8192)*320,native=False); model.write_bytes(b"fake model")
            args=SimpleNamespace(worker_engine="whisper",audio=source,audio_variant="normalize",
                child_dir=root,whisper_model=model,whisper_bin=root/"whisper",reference=stt.REFERENCE)
            for fail in (False,True):
                def ffmpeg(command,**kwargs):
                    wav(Path(command[-1]),struct.pack("<h",8192)*320,native=False)
                    return SimpleNamespace(returncode=1 if fail else 0)
                measured=dict(raw_transcription=stt.REFERENCE,final_ready_at=time.monotonic()+1,
                              model_load_seconds=None,transcription_seconds=.1)
                with patch.dict(stt.sys.modules,{"resource":resource}), \
                     patch.object(audio.subprocess,"run",side_effect=ffmpeg), \
                     patch.object(stt,"whisper_transcribe",return_value=measured) as whisper:
                    self.assertEqual(stt.child(args),2 if fail else 0)
                result=json.loads((root/"result.json").read_text())
                self.assertIn("filter_chain",result["audio_processing"])
                if fail:
                    self.assertEqual(result["reason"],"audio_preparation_failed")
                    self.assertNotIn("raw_transcription",result);whisper.assert_not_called()
                else:
                    self.assertEqual(result["accuracy"]["wer"],0)
                    self.assertEqual(result["raw_transcription"],stt.REFERENCE)
                    (root/"normalize.wav").unlink()
