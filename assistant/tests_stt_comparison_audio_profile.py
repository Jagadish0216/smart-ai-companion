"""No model, microphone, native package, FFmpeg or speaker is invoked."""
from contextlib import nullcontext, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import wave

from django.test import SimpleTestCase, override_settings
from scripts import compare_offline_stt as compare, voice_loop as voice


class STTComparisonTests(SimpleTestCase):
    def test_word_errors_and_digit_normalization(self):
        self.assertEqual(compare.word_errors("One two three", "1, 2, 3!")["wer"], 0)
        for hypothesis, counts in (("one wrong three", (1,0,0)), ("one three", (0,1,0)),
                                   ("one two extra three", (0,0,1)), ("", (0,3,0))):
            result = compare.word_errors("one two three", hypothesis)
            self.assertEqual(tuple(result[k] for k in ("substitutions","deletions","insertions")), counts)
            self.assertEqual(result["wer"], sum(counts)/3)
        with self.assertRaises(ValueError):
            compare.word_errors("", "text")
        with self.assertRaises(ValueError):
            compare.word_errors("a "*257, "text")

    def test_wav_validation_and_no_silent_channel_averaging(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"audio.wav"
            for channels in (1,2):
                with wave.open(str(path),"wb") as wav:
                    wav.setparams((channels,2,16000,0,"NONE",""))
                    wav.writeframes(struct.pack("<h",16384)*channels*1600)
                if channels == 1:
                    self.assertEqual(compare.read_audio(path),[.5]*1600)
                else:
                    with self.assertRaises(ValueError):
                        compare.read_audio(path)

    def test_moonshine_exact_arch_local_files_and_final_only(self):
        transcriber = MagicMock()
        final = SimpleNamespace(lines=[SimpleNamespace(text="final text",is_complete=True)])
        transcriber.stop.return_value = final
        module = SimpleNamespace(Transcriber=MagicMock(return_value=transcriber),
                                 ModelArch=SimpleNamespace(SMALL_STREAMING=4))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for name in compare.FILES: (path/name).write_bytes(b"fixture")
            with patch.dict(sys.modules,{"moonshine_voice":module}), \
                 patch.object(compare,"verify_moonshine_files"), \
                 patch.object(compare.importlib.metadata,"version",return_value="0.1.5"):
                result = compare.moonshine_transcribe(path,[0.0]*3201)
                self.assertEqual(result["raw_transcription"],"final text")
                module.Transcriber.assert_called_once_with(model_path=str(path),model_arch=4)
                self.assertEqual(transcriber.add_audio.call_count,3)
                transcriber.stop.assert_called_once()
                transcriber.close.assert_called_once()
                transcriber.add_listener.assert_not_called()
                final.lines[0].is_complete=False
                with self.assertRaises(ValueError): compare.moonshine_transcribe(path,[])
                transcriber.stop.return_value=None
                with self.assertRaises(ValueError): compare.moonshine_transcribe(path,[])

    def test_missing_local_models_refuse_without_import_or_download(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                compare.moonshine_transcribe(Path(directory),[])

    def test_model_manifest_size_and_checksum_fail_closed(self):
        checksum=MagicMock()
        checksum.digest.return_value=b"\x00\x00\x00\x00"
        module=SimpleNamespace(Checksum=MagicMock(return_value=checksum))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/"test.ort").write_bytes(b"fixture")
            with patch.object(compare,"MODEL_MANIFEST",{"test.ort":(7,"AAAAAA==")}), \
                 patch.dict(sys.modules,{"google_crc32c":module}):
                compare.verify_moonshine_files(path)
                checksum.update.assert_called_with(b"fixture")
                checksum.digest.return_value=b"\x01\x00\x00\x00"
                with self.assertRaisesRegex(ValueError,"integrity"):
                    compare.verify_moonshine_files(path)
                (path/"test.ort").write_bytes(b"short")
                with self.assertRaisesRegex(ValueError,"english_files"):
                    compare.verify_moonshine_files(path)

    def test_readonly_compatibility_does_not_import_native_package(self):
        with patch.object(compare.importlib.metadata,"version",side_effect=compare.importlib.metadata.PackageNotFoundError):
            self.assertIsNone(compare.compatibility()["moonshine_installed"])
        self.assertFalse(compare.compatibility()["inference_run"])

    def test_python_network_is_refused(self):
        with compare.no_network(), compare.socket.socket() as sock:
            with self.assertRaisesRegex(RuntimeError,"network_forbidden"):
                sock.connect(("127.0.0.1",11434))

    def test_whisper_native_load_counter_is_optional_not_inferred(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            binary, model = path/"whisper", path/"ggml-base.en.bin"
            binary.touch();model.touch()
            for diagnostic, load in ((b"load time = 100.0 ms",.1),(b"",None)):
                def run(*a,**kw):
                    kw["stdout"].write(b"Hello public fixture")
                    kw["stderr"].write(diagnostic)
                    return SimpleNamespace(returncode=0)
                with patch.object(compare.subprocess,"run",side_effect=run):
                    result=compare.whisper_transcribe(binary,model,path/"audio.wav",path)
                self.assertEqual(result["model_load_seconds"],load)
                self.assertTrue(result["transcription_includes_model_load"])

    def test_parent_hot_start_never_spawns_worker(self):
        from assistant.tests_offline_voice_validation import point
        from scripts import compare_offline_models as models
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            args=SimpleNamespace(reference=compare.REFERENCE,operator_approved_public_audio=True,
                python=path/"isolated/python",audio=path/"audio.wav",host="http://127.0.0.1:11434",output=path/"report.json")
            args.audio.write_bytes(b"public fixture")
            with patch.object(compare.sys,"platform","linux"), patch.object(compare,"read_audio"), \
                 patch.object(models,"acquire_trial_lock") as lock, \
                 patch.object(models,"inventory",return_value={"qwen3.5:4b-q4_K_M":{}}), \
                 patch.object(models,"verify_identity",return_value="d8b0f5e9760c"+"0"*52), \
                 patch("scripts.validate_pi_performance.isolated_database",return_value=nullcontext(path)), \
                 patch("scripts.benchmark_inference_ab.telemetry",return_value=point(temperature_c=50)), \
                 patch.object(compare.subprocess,"Popen") as spawn:
                self.assertEqual(compare.run(args),2)
            spawn.assert_not_called()
            lock.return_value.close.assert_called_once()
            self.assertEqual(json.loads(args.output.read_text())["reason"],"below_50_start_required")

    def test_failed_baseline_preserves_partial_and_never_launches_candidate(self):
        from assistant.tests_offline_voice_validation import point
        from scripts import compare_offline_models as models
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            args=SimpleNamespace(reference=compare.REFERENCE,operator_approved_public_audio=True,
                python=root/"isolated/python",audio=root/"audio.wav",host="http://127.0.0.1:11434",
                output=root/"report.json",whisper_bin=root/"whisper",whisper_model=root/"ggml-base.en.bin",
                moonshine_model=root/"small-streaming-en")
            args.audio.write_bytes(b"public")
            process=MagicMock(returncode=2)
            def spawn(command,**kwargs):
                destination=Path(command[command.index("--child-dir")+1])
                (destination/"result.json").write_text('{"status":"aborted","reason":"stt_execution_failed"}')
                self.assertIn("whisper",command)
                self.assertTrue(kwargs["start_new_session"])
                return process
            with patch.object(compare.sys,"platform","linux"), patch.object(compare,"read_audio"), \
                 patch.object(models,"acquire_trial_lock"), \
                 patch.object(models,"inventory",return_value={"qwen3.5:4b-q4_K_M":{}}), \
                 patch.object(models,"verify_identity",return_value="d8b0f5e9760c"+"0"*52), \
                 patch.object(models,"terminate") as terminate, \
                 patch("scripts.validate_pi_performance.isolated_database",return_value=nullcontext(root)), \
                 patch("scripts.validate_offline_voice.require_resident"), \
                 patch("scripts.validate_offline_voice.monitor"), \
                 patch("scripts.benchmark_inference_ab.telemetry",return_value=point()), \
                 patch.object(compare.subprocess,"Popen",side_effect=spawn) as popen:
                self.assertEqual(compare.run(args),2)
            popen.assert_called_once()
            terminate.assert_called_once_with(process)
            report=json.loads(args.output.read_text())
            self.assertEqual(len(report["samples"]),1)
            self.assertEqual(report["samples"][0]["result"]["reason"],"stt_execution_failed")
            self.assertEqual(report["llm_requests"],0)



class SpeakerProfileTests(SimpleTestCase):
    def test_filters_replace_fourfold_gain_preserve_format_and_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            for profile in ("legacy","approved_loudness","bounded_gain"):
                for raw in (False,True):
                    cancel=object()
                    def execute(command,**kw):
                        Path(command[-1]).write_bytes(b"fake pcm")
                        return subprocess.CompletedProcess(command,0,b"",b"")
                    with patch.object(voice,"_run_audio_subprocess",side_effect=execute) as run:
                        voice.prepare_alsa_playback(path/"piper.wav",path/"speaker.wav",4.0,
                            profile=profile,raw_pcm=raw,cancel_event=cancel)
                    command=run.call_args.args[0]
                    chain=command[command.index("-af")+1]
                    self.assertNotIn("pan=",chain)
                    self.assertIs(run.call_args.kwargs["cancel_event"],cancel)
                    self.assertEqual(run.call_args.kwargs["timeout"],60)
                    if profile=="legacy": self.assertIn("volume=4.0",chain)
                    else:
                        self.assertNotIn("volume=4.0",chain)
                        self.assertIn("volume=1.10,alimiter=limit=0.95:level=false",chain)
                    self.assertEqual("loudnorm=" in chain,profile=="approved_loudness")
                    self.assertEqual(command[command.index("-ar")+1],"48000")
                    self.assertEqual(command[command.index("-ac")+1],"2")

    @override_settings(VOICE_AUDIO_BACKEND="alsa",VOICE_PLAYBACK_PROFILE="approved_loudness")
    def test_process_setting_and_invalid_or_unsupported_profiles(self):
        self.assertEqual(voice.VoiceLoopConfig.from_settings().playback_profile,"approved_loudness")
        for profile, backend in (("unknown","alsa"),("approved_loudness","pulse")):
            with self.assertRaises(voice.VoiceLoopError):
                voice.VoiceLoopConfig("source",playback_profile=profile,audio_backend=backend).validate()

    def test_optin_preserves_streaming_first_sentence_and_overlap(self):
        from threading import Event
        from assistant.tests_voice_stream import StreamingAudioTests, FIRST_SENTENCE
        for profile in ("approved_loudness","bounded_gain"):
            case=StreamingAudioTests("test_sentence_plays_before_done_and_generation_continues_during_playback")
            case.setUp()
            try:
                case.config=replace(case.config,playback_profile=profile,audio_backend="alsa")
                playing, continuing = Event(), Event()
                def play(*args,**kwargs):
                    playing.set()
                    self.assertTrue(continuing.wait(2))
                case.stream_write.side_effect=play
                def events():
                    yield {"type":"start","conversation_id":42}
                    yield {"type":"delta","text":FIRST_SENTENCE+" "}
                    self.assertTrue(playing.wait(2))
                    continuing.set()
                    yield {"type":"done","conversation_id":42,"response":FIRST_SENTENCE+" "}
                with redirect_stdout(io.StringIO()): case.speak(events())
                self.assertTrue(case.prepare_alsa_playback.called)
                self.assertTrue(all(c.kwargs["profile"]==profile for c in case.prepare_alsa_playback.call_args_list))
            finally: case.doCleanups()


class VADSummaryTests(SimpleTestCase):
    def test_numeric_summary_on_timeout_and_observer_failure_is_noncritical(self):
        from assistant.tests_capture_vad import CaptureReadyCallbackTests
        for broken in (False,True):
            case=CaptureReadyCallbackTests("test_ready_once_after_adaptive_thresholds_are_frozen")
            case.setUp()
            try:
                case.process.stdout.read.side_effect=[case.pcm(.05)]*25+[case.pcm(.07)]*75
                summary=MagicMock(side_effect=RuntimeError("observer") if broken else None)
                with self.assertRaisesRegex(voice.VoiceLoopError,"start timeout"):
                    case.record(on_vad_summary=summary)
                summary.assert_called_once()
                result=summary.call_args.args[0]
                self.assertEqual(result["stop_reason"],"start_timeout")
                self.assertAlmostEqual(result["noise_floor"],.05,places=5)
                self.assertAlmostEqual(result["start_threshold"],.127,places=5)
                self.assertAlmostEqual(result["max_wait_rms"],.07,places=5)
                self.assertTrue(all(type(v) in (float,int,str) for v in result.values()))
                for name in ("candidates_started","candidates_rejected","candidates_accepted"):
                    self.assertEqual(result[name],0)
                    self.assertIs(type(result[name]),int)
                case.process.terminate.assert_called_once()
            finally: case.doCleanups()
