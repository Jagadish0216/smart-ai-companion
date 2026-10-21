"""Synthetic fan-on release candidate: no hardware, packages or inference."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import wave

from django.test import SimpleTestCase, override_settings
from assistant.voice.capture_vad import AdaptiveCaptureVAD, SettledFanPreparation
from scripts import voice_loop as voice, validate_offline_voice as validation


class SettledFanTests(SimpleTestCase):
    def prepare(self, levels, vad=None):
        vad = vad or AdaptiveCaptureVAD()
        prep = SettledFanPreparation(vad, 1)
        for level in levels:
            prep.consume_warmup(level, .02)
            if not prep.warming_up():
                break
        return vad, prep

    def calibrated(self):
        vad, prep = self.prepare([.008]*300)
        self.assertTrue(prep._settled)
        for _ in range(25): vad.feed(.008, .02)
        self.assertTrue(prep.calibration_usable())
        vad.open_start_window()
        return vad, prep

    def test_high_startup_energy_settles_against_fan(self):
        levels=[.78684]+[.08038]*24+[.03093]*25+[.020]*25+[.01102]*25+[.009]*75
        vad, prep=self.prepare(levels)
        self.assertIsNone(vad.stop_reason)
        self.assertTrue(prep._settled)
        self.assertGreaterEqual(prep.warmup_elapsed,2.5-1e-9)
        self.assertLessEqual(prep.warmup_elapsed,6)
        self.assertLess(prep.settled_floor,.012)
        for _ in range(25): vad.feed(.009,.02)
        self.assertTrue(prep.calibration_usable())
        self.assertLess(vad.start_threshold,.03134)
        self.assertEqual(prep.diagnostics()["policy"],"settled_fan_v1")

    def test_continuous_fan_and_moderate_variation_never_trigger_speech(self):
        vad,prep=self.calibrated()
        for level in ([.008,.010,.012,.009]*62+[.008]*2): vad.feed(level,.02)
        self.assertEqual(vad.stop_reason,"start_timeout")
        self.assertFalse(vad.accepted)
        self.assertEqual(vad.noise_floor,.008)
        self.assertGreater(vad.start_threshold,0)

    def test_speech_after_ready_short_pauses_and_endpoint(self):
        vad,prep=self.calibrated()
        events=[]
        for level in [.03134]*25+[.008]*15+[.03134]*25+[.008]*40:
            event=vad.feed(level,.02)
            if event: events.append(event)
        self.assertIn("start",events)
        self.assertTrue(vad.accepted)
        self.assertEqual(vad.stop_reason,"silence")
        self.assertAlmostEqual(vad.speech_duration,1)
        self.assertAlmostEqual(vad.capture_duration,2.1)

    def test_short_interrupted_speech_not_accepted_and_wait_stays_bounded(self):
        vad,prep=self.calibrated()
        for level in ([.04]*2+[.008])*20+[.008]*190:
            vad.feed(level,.02)
            if vad.stop_reason: break
        self.assertFalse(vad.accepted)
        self.assertEqual(vad.stop_reason,"start_timeout")

    def test_excessive_noise_modulated_early_speech_and_deadline(self):
        for levels in ([.08]*300, [.008]*75+([.008]*5+[.04]*5)*23):
            vad,prep=self.prepare(levels)
            self.assertFalse(prep._settled)
            self.assertEqual(vad.stop_reason,"preparation_unstable")
            self.assertAlmostEqual(prep.warmup_elapsed,6)
            self.assertEqual(prep.diagnostics()["refusal_reason"],"preparation_unstable")
            self.assertEqual(validation.cycle_failure_reason(voice.VoiceLoopError("safe"),
                type("Trace",(),{"data":{"capture_stop_reason":"preparation_unstable"}})()),
                "capture_preparation_unstable")

    def test_early_activity_can_finish_before_background_calibration(self):
        vad,prep=self.prepare([.08]*75+[.04]*50+[.008]*175)
        self.assertTrue(prep._settled)
        self.assertGreaterEqual(prep.warmup_elapsed,3.4)
        self.assertFalse(vad.speech_started)

    def test_calibration_change_refuses_and_budget_configuration_fails_closed(self):
        vad,prep=self.prepare([.008]*300)
        for _ in range(25): vad.feed(.04,.02)
        self.assertFalse(prep.calibration_usable())
        self.assertEqual(vad.stop_reason,"calibration_unreliable")
        with self.assertRaises(ValueError): SettledFanPreparation(AdaptiveCaptureVAD(),6)

    @override_settings(VOICE_CAPTURE_PREPARATION_POLICY="settled_fan",VOICE_CAPTURE_READY_LIFECYCLE=False,
                       VOICE_AUDIO_BACKEND="alsa",VOICE_CAPTURE_VAD_ENABLED=True,VOICE_CAPTURE_VAD_ADAPTIVE=True)
    def test_process_optin_implies_ready_lifecycle_and_has_reversible_default(self):
        config=voice.VoiceLoopConfig.from_settings()
        self.assertEqual(config.capture_preparation_policy,"settled_fan")
        self.assertTrue(config.capture_ready_lifecycle)
        with override_settings(VOICE_CAPTURE_PREPARATION_POLICY="legacy"):
            self.assertFalse(voice.VoiceLoopConfig.from_settings().capture_ready_lifecycle)
        with self.assertRaises(voice.VoiceLoopError):
            replace(config,capture_preparation_policy="unknown").validate()


class NativeSettledFanTests(SimpleTestCase):
    def case(self):
        from assistant.tests_capture_readiness import NativeCaptureReadinessTests
        helper=NativeCaptureReadinessTests()
        case=helper.case()
        self.addCleanup(helper.doCleanups)
        return case

    def test_drained_startup_ready_and_exact_native_speech_boundaries(self):
        case=self.case();trace=validation.Trace();ready=[]
        # Earliest stable window follows 1.5 seconds of discarded startup PCM.
        chunks=[case.pcm(.08)]*75+[case.pcm(.008)]*75+[case.pcm(.04)]*20+[case.pcm(.008)]*2
        case.process.stdout.read.side_effect=chunks
        def cue(): ready.append(case.process.stdout.read.call_count)
        import os
        read_fd,write_fd=os.pipe()
        try:
            with validation.observe_pipeline(voice,trace,"public",True,ready_fd=write_fd):
                voice._timed_call({},"record",voice.record_audio_with_capture_vad,
                    case.output_path,"hw:test",1,.02,.04,10,2,backend="alsa",ready_lifecycle=True,
                    preparation_policy="settled_fan",on_capture_ready=cue)
            self.assertEqual(ready,[150])
            self.assertIn(b"MICROPHONE READY",os.read(read_fd,200))
        finally: os.close(read_fd)
        with wave.open(str(case.output_path),"rb") as wav:
            self.assertEqual((wav.getnchannels(),wav.getframerate(),wav.getsampwidth()),(2,48000,4))
            self.assertEqual(wav.readframes(wav.getnframes()),b"".join(chunks[138:]))
        self.assertEqual(trace.data["capture_vad_summary"]["preparation"]["warmup_frames"],125)
        self.assertIn("endpoint_detected",trace.data["events_ms"])
        self.assertLessEqual(trace.data["events_ms"]["microphone_ready_emitted"],trace.data["events_ms"]["speech_start_window_open"])
        self.assertAlmostEqual(case.mocks["Timer"].call_args.args[0],19.5)
        case.process.terminate.assert_called_once()

    def test_cancel_and_unstable_preparation_never_emit_ready_or_endpoint(self):
        for levels,error in (([.08]*300,voice.VoiceLoopError),([.008]*2+[KeyboardInterrupt()],KeyboardInterrupt)):
            case=self.case();trace=validation.Trace()
            case.process.stdout.read.side_effect=[level if isinstance(level,BaseException) else case.pcm(level) for level in levels]
            with validation.observe_pipeline(voice,trace,"public",True):
                with self.assertRaises(error):
                    voice._timed_call({},"record",voice.record_audio_with_capture_vad,
                        case.output_path,"hw:test",1,.02,.04,10,2,backend="alsa",ready_lifecycle=True,
                        preparation_policy="settled_fan")
            self.assertNotIn("capture_ready",trace.data["events_ms"])
            self.assertNotIn("endpoint_detected",trace.data["events_ms"])
            self.assertIn(trace.data["capture_vad_summary"]["preparation"]["refusal_reason"],("preparation_unstable","cancelled"))
            self.assertFalse(case.output_path.exists())
            case.process.terminate.assert_called_once()
            case.doCleanups()


class IntegratedReleaseTests(SimpleTestCase):
    def test_normal_arbitrary_question_dispatch_playback_profile_and_display_progression(self):
        from assistant.tests_voice_loop import VoiceInputInjectionTests, _make_wav
        helper=VoiceInputInjectionTests("test_default_cli_still_records_with_existing_vad_and_alsa_left_channel")
        helper.setUp();self.addCleanup(helper.doCleanups)
        helper.config=replace(helper.config,capture_ready_lifecycle=True,capture_preparation_policy="settled_fan",
                              playback_profile="approved_loudness")
        helper.mocks["VoiceLoopConfig.from_settings"].return_value=helper.config
        def captured(path,*args,**kwargs):
            self.assertEqual(kwargs["preparation_policy"],"settled_fan")
            self.assertNotIn("LISTENING",helper.states)
            helper.display.set_text.assert_any_call("PREPARING - keep quiet")
            kwargs["on_capture_ready"]()
            self.assertEqual(helper.states[-1],"LISTENING")
            path.write_bytes(_make_wav())
        helper.mocks["record_audio_with_capture_vad"].side_effect=captured
        def transcribe(path):
            self.assertEqual(helper.states[-1],"THINKING")
            return "How does an ESP32 work?"
        helper.mocks["get_stt_provider"].return_value.transcribe.side_effect=transcribe
        with patch("builtins.input",side_effect=["","q"]):
            self.assertEqual(voice.main([]),0)
        self.assertEqual(helper.mocks["AssistantService.process_message_stream"].call_args.args,("How does an ESP32 work?",))
        self.assertEqual(helper.mocks["normalize_audio"].call_args.kwargs,{"left_channel":True})
        self.assertTrue(helper.mocks["prepare_alsa_playback"].called)
        for call in helper.mocks["prepare_alsa_playback"].call_args_list:
            self.assertEqual(call.kwargs["profile"],"approved_loudness")
        self.assertEqual(helper.states,["READY","READY","LISTENING","THINKING","SPEAKING","READY"])
        helper.display.set_text.assert_any_call("SPEAKING")
        helper.display.set_text.assert_any_call("READY")
        helper.assert_reaped()


class ReleaseGuardTests(SimpleTestCase):
    def test_release_command_enables_direct_ready_and_refuses_synthetic(self):
        with patch.object(validation.sys,"argv",["validator","--operator-live-trial","--production-qwen","--settled-fan"]), \
             patch.object(validation,"run",return_value=0) as run, \
             patch.object(validation.Path,"exists",return_value=False):
            self.assertEqual(validation.main(),0)
        self.assertTrue(run.call_args.args[0].settled_fan)
        self.assertTrue(run.call_args.args[0].ready_lifecycle)
        with patch.object(validation.sys,"argv",["validator","--synthetic","--settled-fan"]), \
             patch.object(validation,"run") as run, patch.object(validation.sys,"stderr"):
            with self.assertRaises(SystemExit): validation.main()
        run.assert_not_called()

    def test_completion_requires_capture_profile_and_display_software_evidence(self):
        trace=validation.Trace()
        trace.update(capture_vad_summary={"accepted":True},ready_emitted_before_speech_window=True)
        trace.mark("endpoint_detected")
        trace.data["display_calls"]=[{"state":s} for s in ("READY","LISTENING","THINKING","SPEAKING","READY")]
        config=voice.VoiceLoopConfig("",playback_profile="approved_loudness")
        self.assertTrue(all(validation.release_completion_checks(trace,config).values()))
        trace.data["events_ms"].clear()
        trace.data["display_calls"].pop()
        checks=validation.release_completion_checks(trace,replace(config,playback_profile="legacy"))
        self.assertFalse(checks["accepted_endpoint"])
        self.assertFalse(checks["display_callback_progression"])
        self.assertFalse(checks["approved_playback_profile"])
