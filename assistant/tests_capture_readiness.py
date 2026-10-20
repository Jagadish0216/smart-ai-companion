"""Step 66 simulated RMS/PCM only: no microphone, inference or visible-pixel claims."""
import io
import os
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch
import wave

from django.test import SimpleTestCase
from assistant.voice.capture_vad import AdaptiveCaptureVAD, QuietCapturePreparation
from scripts import voice_loop as voice, validate_offline_voice as validation


class ReadinessStateTests(SimpleTestCase):
    def feed(self,vad,levels):
        events=[]
        for level in levels:
            event=vad.feed(level,.02)
            if event: events.append((event,vad.elapsed))
            if vad.stop_reason: break
        return events

    def test_observed_original_and_delayed_aggregates_with_synthetic_envelope(self):
        # Contrived RMS envelope fits supplied aggregates; not the actual waveform.
        original=[.2596807409]+[.0457428885]*24+[.0392295279]*227
        original+=([.001]*35+[.021])*4+[.001]*4
        self.assertEqual(len(original),400)
        vad=AdaptiveCaptureVAD(); self.feed(vad,original)
        self.assertAlmostEqual(vad.noise_floor,.0457428885)
        self.assertAlmostEqual(vad.start_threshold,.1163572212)
        self.assertAlmostEqual(vad.continue_threshold,.0633964717)
        self.assertAlmostEqual(vad.max_wait_rms,.0392295279)
        self.assertEqual(vad.stop_reason,"start_timeout")
        vad=AdaptiveCaptureVAD();events=self.feed(vad,[0]*50+original)
        self.assertAlmostEqual(vad.noise_floor,0)
        self.assertAlmostEqual(vad.max_wait_rms,.2596807409)
        self.assertAlmostEqual(events[0][1],1.06)
        self.assertAlmostEqual(vad.speech_duration,5.12)
        self.assertAlmostEqual(vad.capture_duration,8)
        self.assertTrue(vad.accepted);self.assertIsNone(vad.stop_reason)

    def test_five_second_window_excludes_calibration_and_preserves_threshold(self):
        vad=AdaptiveCaptureVAD();self.feed(vad,[.001]*25)
        threshold=vad.start_threshold
        vad.open_start_window()
        self.feed(vad,[.001]*249)
        self.assertIsNone(vad.stop_reason)
        self.feed(vad,[.001])
        self.assertEqual(vad.stop_reason,"start_timeout")
        self.assertAlmostEqual(vad.elapsed,5.5)
        self.assertEqual(vad.start_threshold,threshold)
        with self.assertRaises(ValueError): AdaptiveCaptureVAD().open_start_window()

    def test_contaminated_calibration_refuses_instead_of_false_ready(self):
        vad=AdaptiveCaptureVAD();self.feed(vad,[.0457428885]*25)
        prep=QuietCapturePreparation(vad,1)
        self.assertFalse(prep.calibration_usable())
        self.assertEqual(vad.stop_reason,"calibration_unreliable")
        self.assertGreater(vad.start_threshold,.11)

    def test_transient_noise_and_interrupted_confirmation_remain_rejected(self):
        vad=AdaptiveCaptureVAD();self.feed(vad,[.001]*12+[.9]+[.001]*12)
        self.assertTrue(QuietCapturePreparation(vad,0).calibration_usable())
        vad.open_start_window()
        self.feed(vad,[.0392295279]*2+[.001]+[.0392295279]*2)
        self.assertFalse(vad.speech_started)
        self.feed(vad,[.0392295279])
        self.assertTrue(vad.speech_started)
        self.feed(vad,[.0392295279]*20+[.001]*15+[.0392295279]*20+[.001]*40)
        self.assertTrue(vad.accepted);self.assertEqual(vad.stop_reason,"silence")

    def test_warmup_activity_and_short_burst_never_authorize_request(self):
        vad=AdaptiveCaptureVAD();prep=QuietCapturePreparation(vad,1)
        for _ in range(15): prep.consume_warmup(.06,.02)
        self.assertEqual(vad.stop_reason,"preparation_activity")
        vad=AdaptiveCaptureVAD();self.feed(vad,[.001]*25);vad.open_start_window()
        self.feed(vad,[.06]*3+[.001]*40)
        self.assertFalse(vad.accepted)


class NativeCaptureReadinessTests(SimpleTestCase):
    def test_lifecycle_refuses_unbounded_backend_before_opening_capture(self):
        with patch.object(voice.subprocess,"Popen") as capture:
            with self.assertRaises(voice.VoiceLoopError):
                voice.record_audio_with_capture_vad(Path("unused.wav"),"source",1,.02,.8,10,5,ready_lifecycle=True)
        capture.assert_not_called()

    def case(self):
        from assistant.tests_capture_vad import CaptureReadyCallbackTests
        case=CaptureReadyCallbackTests("test_ready_once_after_adaptive_thresholds_are_frozen")
        case.setUp();self.addCleanup(case.doCleanups)
        return case

    def test_warmup_drained_ready_after_calibration_then_valid_capture(self):
        case=self.case();observed=[];summary=MagicMock()
        chunks=[case.pcm(.001)]*75+[case.pcm(.0392295279)]*20+[case.pcm(.001)]*2
        case.process.stdout.read.side_effect=chunks
        def ready():
            self.assertEqual(case.process.stdout.read.call_count,75)
            self.assertAlmostEqual(case.vads[0].elapsed,.5)
            self.assertEqual(case.vads[0].start_window_origin,0)
            observed.append("ready")
        case.record(ready_lifecycle=True,on_capture_ready=ready,on_vad_summary=summary)
        case.mocks["time.sleep"].assert_not_called()
        self.assertEqual(observed,["ready"])
        self.assertEqual(summary.call_args.args[0]["accepted"],True)
        self.assertAlmostEqual(summary.call_args.args[0]["warmup_audio_seconds"],1)
        self.assertAlmostEqual(case.vads[0].start_window_origin,.5)
        with wave.open(str(case.output_path),"rb") as wav:
            data=wav.readframes(wav.getnframes())
            self.assertEqual((wav.getnchannels(),wav.getframerate(),wav.getsampwidth()),(2,48000,4))
            self.assertEqual(data,b"".join(chunks[63:])) # Original pre-roll and speech, no artificial samples.
        case.process.terminate.assert_called_once()

    def test_preparation_activity_and_nonquiet_calibration_no_ready_or_wav(self):
        for levels,reason in (([.06]*50,"preparation_activity"),
                              ([.001]*50+[.0457428885]*25,"calibration_unreliable")):
            case=self.case();ready=MagicMock();summary=MagicMock()
            case.process.stdout.read.side_effect=[case.pcm(v) for v in levels]
            with self.assertRaises(voice.VoiceLoopError):
                case.record(ready_lifecycle=True,on_capture_ready=ready,on_vad_summary=summary)
            ready.assert_not_called();self.assertFalse(case.output_path.exists())
            self.assertEqual(summary.call_args.args[0]["stop_reason"],reason)
            case.process.terminate.assert_called_once()
            case.doCleanups()

    def test_timeout_callback_failure_cancellation_and_missing_pcm_are_bounded(self):
        for failure in ("timeout","callback","cancel","eof"):
            case=self.case();ready=MagicMock()
            chunks=[case.pcm(.001)]*75
            if failure=="timeout": chunks += [case.pcm(.001)]*100 # case's configured 2 s wait.
            if failure=="callback": ready.side_effect=RuntimeError("callback failed")
            if failure=="cancel": chunks += [KeyboardInterrupt()]
            if failure=="eof": chunks += [b""]
            case.process.stdout.read.side_effect=chunks
            with self.assertRaises(KeyboardInterrupt if failure=="cancel" else voice.VoiceLoopError):
                case.record(ready_lifecycle=True,on_capture_ready=ready)
            case.process.terminate.assert_called_once()
            self.assertFalse(case.output_path.exists())
            case.mocks["Timer"].return_value.start.assert_called_once()
            case.mocks["Timer"].return_value.cancel.assert_called_once()
            case.doCleanups()

    def test_direct_ready_channel_is_constant_and_before_window_open(self):
        case=self.case();trace=validation.Trace()
        case.process.stdout.read.side_effect=[case.pcm(.001)]*75+[case.pcm(.06)]*20+[case.pcm(.001)]*2
        read_fd,write_fd=os.pipe()
        try:
            with validation.observe_pipeline(voice,trace,"approved public fixture",True,ready_fd=write_fd):
                voice._timed_call({},"record",voice.record_audio_with_capture_vad,
                    case.output_path,"hw:test",1,.02,.04,10,2,backend="alsa",ready_lifecycle=True,
                    on_capture_ready=lambda:None)
            self.assertEqual(os.read(read_fd,200),b"MICROPHONE READY - speak the displayed public fixture now.\n")
        finally: os.close(read_fd)
        events=trace.data["events_ms"]
        for a,b in zip(("capture_open_start","capture_open_end","capture_first_frame","capture_warmup_end",
                        "calibration_start","calibration_end","capture_ready","microphone_ready_emitted","speech_start_window_open"),
                       ("capture_open_end","capture_first_frame","capture_warmup_end","calibration_start",
                        "calibration_end","capture_ready","microphone_ready_emitted","speech_start_window_open","speech_detected")):
            self.assertLessEqual(events[a],events[b])
        self.assertTrue(trace.data["ready_emitted_before_speech_window"])
        self.assertIn("minimum_speech_accepted",events)

    def test_unavailable_cue_never_opens_window(self):
        case=self.case();trace=validation.Trace()
        case.process.stdout.read.side_effect=[case.pcm(.001)]*75
        with validation.observe_pipeline(voice,trace,"public",True,ready_fd=None):
            with self.assertRaises(voice.VoiceLoopError):
                voice._timed_call({},"record",voice.record_audio_with_capture_vad,
                    case.output_path,"hw:test",1,.02,.04,10,2,backend="alsa",ready_lifecycle=True)
        self.assertNotIn("speech_start_window_open",trace.data["events_ms"])
        self.assertNotIn("microphone_ready_emitted",trace.data["events_ms"])
        self.assertFalse(case.output_path.exists())

    def test_parent_inherits_only_fixed_cue_descriptor_and_disables_polling_cue(self):
        from contextlib import nullcontext, redirect_stdout
        import json
        from types import SimpleNamespace
        from assistant.tests_offline_voice_validation import point
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile(mode="w+") as terminal:
            root=Path(directory);output=root/"report.json"
            args=SimpleNamespace(production_qwen=True,synthetic=False,operator_live_trial=True,
                ready_lifecycle=True,fixture="fact",host="http://127.0.0.1:11434",output=output)
            process=MagicMock(returncode=2)
            def spawn(command,**kwargs):
                self.assertIn("--ready-lifecycle",command)
                descriptor=int(command[command.index("--ready-fd")+1])
                self.assertEqual(kwargs["pass_fds"],(descriptor,))
                os.fstat(descriptor)
                child_dir=Path(command[command.index("--child-dir")+1])
                (child_dir/"trace.json").write_text(json.dumps({"status":"aborted","reason":"capture_calibration_unreliable"}))
                return process
            with patch("scripts.validate_pi_performance.isolated_database",return_value=nullcontext(root)), \
                 patch.object(validation.sys,"platform","linux"), \
                 patch.object(validation.subprocess,"check_output",return_value="baseline"), \
                 patch.object(validation.subprocess,"Popen",side_effect=spawn) as popen, \
                 patch.object(validation.models,"acquire_trial_lock"), \
                 patch.object(validation.models,"inventory",return_value={validation.QWEN:{}}), \
                 patch.object(validation.models,"verify_identity",return_value="d8b0f5e9760c"+"0"*52), \
                 patch.object(validation,"require_resident"), \
                 patch.object(validation,"ready_cue") as old_cue, \
                 patch.object(validation,"monitor"), \
                 patch.object(validation.models,"terminate") as terminate, \
                 patch("scripts.benchmark_inference_ab.telemetry",return_value=point()),redirect_stdout(terminal):
                self.assertEqual(validation.run(args),2)
            popen.assert_called_once();terminate.assert_called_once_with(process)
            old_cue.assert_not_called()
            self.assertEqual(json.loads(output.read_text())["reason"],"capture_calibration_unreliable")
            terminal.seek(0)
            self.assertIn("Capture refused:",terminal.read())
