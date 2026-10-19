"""Capture RMS sequences and native PCM fixtures; no microphone or binaries."""

from pathlib import Path
import struct
import tempfile
from unittest.mock import MagicMock, patch
import wave

from django.test import SimpleTestCase, override_settings

from assistant.voice.capture_vad import AdaptiveCaptureVAD
from scripts.voice_loop import VoiceLoopConfig, VoiceLoopError, record_audio_with_capture_vad


class AdaptiveCaptureVADTests(SimpleTestCase):
    def setUp(self):
        self.vad = AdaptiveCaptureVAD()

    def feed(self, rms, count, vad=None):
        vad = vad or self.vad
        return [vad.feed(rms, 0.02) for _ in range(count)]

    def calibrate(self, rms=0.01):
        self.feed(rms, 25)

    def speak(self):
        self.calibrate()
        self.feed(0.06, 20)

    def test_quiet_room_robust_median_floor(self):
        for rms in [0.005, 0.008, 0.01, 0.012, 0.015] * 5:
            self.vad.feed(rms, 0.02)
        self.assertTrue(self.vad.calibrated)
        self.assertAlmostEqual(self.vad.noise_floor, 0.01)
        self.assertFalse(self.vad.speech_started)

    def test_background_noise_raises_start_threshold(self):
        self.calibrate(0.015)
        self.assertAlmostEqual(self.vad.start_threshold, 0.0395)
        self.feed(0.015, 50)
        self.assertFalse(self.vad.speech_started)

    def test_configured_start_is_a_floor(self):
        self.calibrate(0.001)
        self.assertEqual(self.vad.start_threshold, 0.02)

    def test_isolated_spike_does_not_start_or_poison_noise(self):
        self.calibrate()
        self.feed(0.08, 1)
        self.feed(0.01, 5)
        self.assertFalse(self.vad.speech_started)
        self.assertAlmostEqual(self.vad.noise_floor, 0.01)

    def test_calibration_median_rejects_isolated_spike(self):
        self.feed(0.01, 12)
        self.feed(0.2, 1)
        self.feed(0.01, 12)
        self.assertAlmostEqual(self.vad.noise_floor, 0.01)

    def test_start_requires_three_consecutive_20_ms_chunks(self):
        self.calibrate()
        self.assertEqual(self.feed(0.06, 3), [None, None, "start"])
        self.assertAlmostEqual(self.vad.speech_duration, 0.06)

    def test_gap_resets_start_confirmation(self):
        self.calibrate()
        self.feed(0.06, 2)
        self.feed(0.01, 1)
        self.feed(0.06, 2)
        self.assertFalse(self.vad.speech_started)
        self.assertEqual(self.feed(0.06, 1), ["start"])

    def test_active_speech_never_updates_noise_floor(self):
        self.speak()
        threshold = self.vad.start_threshold
        self.feed(0.08, 50)
        self.assertAlmostEqual(self.vad.noise_floor, 0.01)
        self.assertEqual(self.vad.start_threshold, threshold)

    def test_short_pauses_do_not_accumulate_across_words(self):
        self.speak()
        for _ in range(5):
            self.feed(0.005, 15)
            self.feed(0.04, 10)
        self.assertIsNone(self.vad.stop_reason)
        self.assertEqual(self.vad.silence_duration, 0)

    def test_quieter_words_between_thresholds_hold_speech(self):
        self.speak()
        self.assertLess(self.vad.continue_threshold, 0.018)
        self.assertLess(0.018, self.vad.start_threshold)
        self.feed(0.018, 75)
        self.assertIsNone(self.vad.stop_reason)
        self.assertEqual(self.vad.silence_duration, 0)

    def test_brief_dips_below_continue_do_not_stop(self):
        self.speak()
        self.feed(0.005, 39)
        self.assertIsNone(self.vad.stop_reason)

    def test_resume_resets_candidate_silence(self):
        self.speak()
        self.feed(0.005, 39)
        self.feed(0.018, 3)
        self.feed(0.005, 39)
        self.assertIsNone(self.vad.stop_reason)
        self.assertEqual(self.feed(0.005, 1), ["stop"])

    def test_calibrated_baseline_and_samples_freeze_while_waiting(self):
        self.calibrate()
        baseline = (self.vad.noise_floor, self.vad.start_threshold, self.vad.continue_threshold)
        samples = list(self.vad.noise_samples)
        self.feed(0.02, 60)
        self.assertEqual(self.vad.calibrated_noise_floor, baseline[0])
        self.assertEqual((self.vad.noise_floor, self.vad.start_threshold, self.vad.continue_threshold), baseline)
        self.assertEqual(list(self.vad.noise_samples), samples)
        self.assertFalse(self.vad.speech_started)

    def test_soft_to_loud_opening_triggers_without_threshold_ratchet(self):
        self.calibrate()
        baseline = self.vad.start_threshold
        self.feed(0.02, 10)
        self.assertEqual(self.vad.start_threshold, baseline)
        self.assertEqual(self.feed(0.035, 3), [None, None, "start"])
        self.assertEqual(self.vad.start_threshold, baseline)

    def test_one_ambiguous_spike_preserves_end_silence_progress(self):
        self.speak()
        voiced = self.vad.speech_duration
        self.feed(0.005, 20)
        self.feed(0.018, 1)
        self.assertGreaterEqual(self.vad.silence_duration, 0.4)
        self.assertEqual(self.vad.speech_duration, voiced)
        self.feed(0.005, 19)
        self.assertEqual(self.vad.stop_reason, "silence")

    def test_definite_resumed_speech_resets_silence_immediately(self):
        self.speak()
        self.feed(0.005, 39)
        self.feed(0.04, 1)
        self.assertEqual(self.vad.silence_duration, 0)
        self.assertIsNone(self.vad.stop_reason)

    def test_final_noisy_silence_ends_despite_intermittent_spikes(self):
        self.speak()
        for _ in range(8):
            self.feed(0.005, 4)
            self.feed(0.018, 1)
        self.feed(0.005, 1)
        self.assertEqual(self.vad.stop_reason, "silence")
        self.assertLess(self.vad.capture_duration, self.vad.max_seconds)

    def test_two_ambiguous_chunks_do_not_confirm_resume(self):
        self.speak()
        self.feed(0.005, 20)
        self.feed(0.018, 2)
        self.assertGreaterEqual(self.vad.silence_duration, 0.4)
        self.feed(0.005, 1)
        self.assertEqual(self.vad.resume_duration, 0)

    def test_isolated_boundary_spike_only_defers_stop_one_chunk(self):
        self.speak()
        self.feed(0.005, 39)
        self.feed(0.018, 1)
        self.assertIsNone(self.vad.stop_reason)
        self.feed(0.005, 1)
        self.assertEqual(self.vad.stop_reason, "silence")

    def test_continuous_continuation_activity_still_hits_maximum(self):
        self.vad = AdaptiveCaptureVAD(max_seconds=0.5)
        self.calibrate()
        self.feed(0.06, 3)
        self.feed(0.018, 22)
        self.assertEqual(self.vad.stop_reason, "max_duration")
        self.assertAlmostEqual(self.vad.capture_duration, 0.5)

    def test_pending_resume_cannot_extend_maximum_duration(self):
        self.vad = AdaptiveCaptureVAD(max_seconds=0.5)
        self.calibrate()
        self.feed(0.06, 20)
        self.feed(0.005, 4)
        self.feed(0.018, 1)
        self.assertEqual(self.vad.stop_reason, "max_duration")

    def test_ambiguous_spikes_do_not_bypass_minimum_voiced_guard(self):
        self.calibrate()
        self.feed(0.06, 3)
        for _ in range(8):
            self.feed(0.005, 4)
            self.feed(0.018, 1)
        self.feed(0.005, 1)
        self.assertFalse(self.vad.accepted)
        self.assertFalse(self.vad.speech_started)

    def test_final_continuous_silence_ends_capture(self):
        self.speak()
        self.feed(0.005, 40)
        self.assertEqual(self.vad.stop_reason, "silence")
        self.assertTrue(self.vad.accepted)

    def test_max_duration_ends_continuous_speech(self):
        self.vad = AdaptiveCaptureVAD(max_seconds=0.5)
        self.calibrate()
        self.feed(0.06, 25)
        self.assertEqual(self.vad.stop_reason, "max_duration")
        self.assertAlmostEqual(self.vad.capture_duration, 0.5)

    def test_start_timeout_includes_calibration(self):
        self.vad = AdaptiveCaptureVAD(start_timeout_seconds=0.4)
        self.feed(0.01, 20)
        self.assertEqual(self.vad.stop_reason, "start_timeout")
        self.assertFalse(self.vad.accepted)

    def test_minimum_voiced_guard_rejects_burst_without_renewing_timeout(self):
        self.calibrate()
        self.feed(0.06, 3)
        self.assertEqual(self.feed(0.005, 40)[-1], "reset")
        self.assertFalse(self.vad.accepted)
        self.feed(0.005, 182)
        self.assertEqual(self.vad.stop_reason, "start_timeout")
        self.assertAlmostEqual(self.vad.elapsed, 5.0)

    def test_real_speech_after_rejected_burst_is_accepted(self):
        self.calibrate()
        self.feed(0.06, 3)
        self.feed(0.005, 40)
        self.feed(0.06, 20)
        self.feed(0.005, 40)
        self.assertEqual(self.vad.stop_reason, "silence")
        self.assertTrue(self.vad.accepted)

    def test_minimum_guard_cannot_extend_maximum(self):
        self.vad = AdaptiveCaptureVAD(max_seconds=0.1)
        self.calibrate()
        self.feed(0.06, 3)
        self.feed(0.005, 2)
        self.assertEqual(self.vad.stop_reason, "max_duration")
        self.assertFalse(self.vad.accepted)

    def test_confirmation_cannot_extend_short_maximum(self):
        self.vad = AdaptiveCaptureVAD(max_seconds=0.04)
        self.calibrate()
        self.feed(0.06, 2)
        self.assertEqual(self.vad.stop_reason, "max_duration")
        self.assertAlmostEqual(self.vad.capture_duration, 0.04)
        self.assertFalse(self.vad.accepted)

    def test_hysteresis_is_strict_even_at_saturation(self):
        for noise in (0, 0.001, 0.015, 0.1, 0.5, 1):
            for configured in (0.02, 0.2, 1):
                vad = AdaptiveCaptureVAD(start_threshold=configured)
                self.feed(noise, 25, vad)
                self.assertLess(vad.continue_threshold, vad.start_threshold)
                self.assertGreaterEqual(vad.start_threshold, configured)
                self.assertLessEqual(vad.start_threshold, 1)

    def test_disabled_adaptive_preserves_legacy_single_chunk_gate(self):
        vad = AdaptiveCaptureVAD(adaptive=False, silence_seconds=0.04)
        self.assertEqual(vad.feed(0.03, 0.02), "start")
        self.assertEqual(vad.continue_threshold, vad.start_threshold)
        self.assertIsNone(vad.feed(0.015, 0.02))
        self.assertEqual(vad.feed(0.015, 0.02), "stop")
        self.assertTrue(vad.accepted)

    def test_legacy_speech_at_wait_boundary_still_starts(self):
        vad = AdaptiveCaptureVAD(adaptive=False, start_timeout_seconds=0.04)
        self.assertIsNone(vad.feed(0.005, 0.02))
        self.assertEqual(vad.feed(0.03, 0.02), "start")

    def test_hands_free_ceiling_handles_observed_noisy_calibration(self):
        for noise in (0.0497, 0.0638):
            with self.subTest(noise=noise):
                ordinary = AdaptiveCaptureVAD()
                capped = AdaptiveCaptureVAD(max_start_threshold=0.10)
                self.feed(noise, 25, ordinary)
                self.feed(noise, 25, capped)
                self.assertAlmostEqual(ordinary.start_threshold, noise * 2.5 + 0.002)
                self.assertEqual(capped.start_threshold, 0.10)
                self.assertEqual(capped.noise_floor, ordinary.noise_floor)
                floor = min(noise, capped.start_threshold * 0.95)
                self.assertAlmostEqual(capped.continue_threshold, floor + 0.25 * (0.10 - floor))
                self.assertLess(capped.continue_threshold, capped.start_threshold)
                self.assertEqual(self.feed(0.11, 3, capped), [None, None, "start"])
                self.assertEqual(self.feed(0.11, 3, ordinary), [None, None, None])

    def test_ceiling_preserves_lower_normal_threshold_and_frozen_floor(self):
        vad = AdaptiveCaptureVAD(max_start_threshold=0.10)
        self.feed(0.0304, 25, vad)
        self.assertAlmostEqual(vad.start_threshold, 0.078)
        baseline = (vad.noise_floor, vad.start_threshold, vad.continue_threshold)
        self.feed(0.04, 30, vad)
        self.assertEqual((vad.noise_floor, vad.start_threshold, vad.continue_threshold), baseline)

    def test_capped_hysteresis_remains_strict_at_saturation(self):
        for noise in (0, 0.01, 0.1, 0.5, 1):
            for ceiling in (0.001, 0.10, 1):
                with self.subTest(noise=noise, ceiling=ceiling):
                    vad = AdaptiveCaptureVAD(max_start_threshold=ceiling)
                    self.feed(noise, 25, vad)
                    self.assertEqual(vad.start_threshold, min(max(0.02, min(1, noise * 2.5 + 0.002)), ceiling))
                    self.assertLess(vad.continue_threshold, vad.start_threshold)

    def test_ceiling_validates_bounds_and_does_not_change_nonadaptive_gate(self):
        for ceiling in (0, -1, 1.01, float("nan"), float("inf")):
            with self.subTest(ceiling=ceiling), self.assertRaisesRegex(ValueError, "max start threshold"):
                AdaptiveCaptureVAD(max_start_threshold=ceiling)
        vad = AdaptiveCaptureVAD(adaptive=False, start_threshold=0.2, max_start_threshold=0.10)
        self.assertEqual(vad.start_threshold, 0.2)
        self.assertIsNone(vad.feed(0.15, 0.02))
        self.assertEqual(vad.feed(0.3, 0.02), "start")

    def test_max_wait_rms_excludes_calibration_and_active_speech(self):
        self.calibrate(0.05)
        self.assertEqual(self.vad.max_wait_rms, 0)
        self.feed(0.07, 2)
        self.feed(0.13, 3)
        self.assertAlmostEqual(self.vad.max_wait_rms, 0.13)
        self.feed(0.9, 20)
        self.assertAlmostEqual(self.vad.max_wait_rms, 0.13)

    def test_max_wait_rms_includes_timeout_chunk_and_rejected_burst_waits(self):
        vad = AdaptiveCaptureVAD(start_timeout_seconds=0.54)
        self.feed(0.01, 25, vad)
        self.feed(0.015, 1, vad)
        self.feed(0.019, 1, vad)
        self.assertEqual(vad.stop_reason, "start_timeout")
        self.assertEqual(vad.max_wait_rms, 0.019)
        self.calibrate()
        self.feed(0.06, 3)
        self.feed(0.005, 40)
        self.feed(0.08, 1)
        self.assertEqual(self.vad.max_wait_rms, 0.08)


class AdaptiveCaptureIntegrationTests(SimpleTestCase):
    @override_settings(
        VOICE_INPUT_SOURCE="source", VOICE_CAPTURE_VAD_ADAPTIVE="false",
        VOICE_CAPTURE_VAD_NOISE_CALIBRATION_SECONDS="0.6",
        VOICE_CAPTURE_VAD_START_CONFIRM_MS="80", VOICE_CAPTURE_VAD_MIN_SPEECH_MS="400",
    )
    def test_new_settings_are_loaded(self):
        config = VoiceLoopConfig.from_settings()
        self.assertFalse(config.capture_vad_adaptive)
        self.assertEqual(config.capture_vad_noise_calibration_seconds, 0.6)
        self.assertEqual(config.capture_vad_start_confirm_ms, 80)
        self.assertEqual(config.capture_vad_min_speech_ms, 400)

    def test_invalid_new_settings_rejected(self):
        for options in (
            {"capture_vad_noise_calibration_seconds": float("nan")},
            {"capture_vad_noise_calibration_seconds": 0},
            {"capture_vad_start_confirm_ms": 20},
            {"capture_vad_start_confirm_ms": 120},
            {"capture_vad_min_speech_ms": 0},
        ):
            with self.subTest(options=options), self.assertRaises(VoiceLoopError):
                VoiceLoopConfig("source", **options).validate()

    @patch("scripts.voice_loop.Timer")
    @patch("scripts.voice_loop.time.sleep")
    @patch("scripts.voice_loop.subprocess.Popen")
    def test_adaptive_native_capture_preserves_preroll_format_and_cleanup(self, popen, _sleep, timer):
        def pcm(rms):
            return struct.pack("<ii", int(rms * 2147483648), 0) * 960

        chunks = [pcm(0.01)] * 25 + [pcm(0.06)] * 20 + [pcm(0.018)] * 10 + [pcm(0.005)] * 40
        process = popen.return_value
        process.stdout.read.side_effect = chunks
        process.communicate.return_value = (b"", b"")
        with tempfile.TemporaryDirectory() as directory, patch("builtins.print") as output:
            path = Path(directory) / "capture.wav"
            record_audio_with_capture_vad(path, "hw:test", 0, 0.02, 0.8, 10, 5, backend="alsa")
            with wave.open(str(path), "rb") as wav:
                self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (48000, 2, 4))
                # 15 chunks through start confirmation + 17 remaining speech + 10 quiet words + 40 silence.
                self.assertEqual(wav.readframes(wav.getnframes()), b"".join(chunks[13:]))
            diagnostic = [call.args[0] for call in output.call_args_list if str(call.args[0]).startswith("Capture VAD:")]
            self.assertEqual(len(diagnostic), 1)
            self.assertIn("stop_reason=silence", diagnostic[0])
            self.assertIn("continue_threshold=", diagnostic[0])
        process.terminate.assert_called_once()
        process.communicate.assert_called_once_with(timeout=5)
        timer.return_value.cancel.assert_called_once()

    @patch("scripts.voice_loop.time.sleep")
    @patch("scripts.voice_loop.subprocess.Popen")
    def test_rejected_burst_does_not_save_false_capture(self, popen, _sleep):
        def pcm(rms):
            return struct.pack("<h", int(rms * 32768)) * 320

        popen.return_value.stdout.read.side_effect = [pcm(0.01)] * 25 + [pcm(0.06)] * 3 + [pcm(0.005)] * 72
        popen.return_value.communicate.return_value = (b"", b"")
        with tempfile.TemporaryDirectory() as directory, patch("builtins.print"):
            path = Path(directory) / "capture.wav"
            with self.assertRaisesRegex(VoiceLoopError, "start timeout"):
                record_audio_with_capture_vad(path, "source", 0, 0.02, 0.8, 10, 2)
            self.assertFalse(path.exists())
        popen.return_value.terminate.assert_called_once()
        popen.return_value.communicate.assert_called_once_with(timeout=5)


class CaptureReadyCallbackTests(SimpleTestCase):
    def test_amplitude_uses_existing_rms_after_calibration_and_failure_keeps_audio(self):
        chunks = [self.pcm(0.01)] * 25 + [self.pcm(0.06)] * 20 + [self.pcm(0.001)] * 2
        for fail in (False, True):
            self.process.stdout.read.side_effect = chunks
            levels = MagicMock(side_effect=RuntimeError() if fail else None)
            with patch("scripts.voice_loop._normalized_alsa_left_rms",
                       wraps=self.voice_loop._normalized_alsa_left_rms) as rms:
                self.record(on_amplitude=levels)
            self.assertEqual(rms.call_count, len(chunks))
            self.assertEqual(levels.call_count, 22)
            self.assertAlmostEqual(levels.call_args_list[0].args[0], 0.06, places=6)
            with wave.open(str(self.output_path), "rb") as wav:
                self.assertEqual(wav.readframes(wav.getnframes()), b"".join(chunks[13:]))

    def setUp(self):
        from scripts import voice_loop
        self.voice_loop = voice_loop
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output_path = Path(directory.name) / "recorded.wav"
        self.process = MagicMock()
        self.process.communicate.return_value = (b"", b"")
        self.vads = []

        def create_vad(**kwargs):
            vad = AdaptiveCaptureVAD(**kwargs)
            self.vads.append(vad)
            return vad

        self.mocks = {}
        for name, kwargs in (
            ("subprocess.Popen", {"return_value": self.process}), ("Timer", {}),
            ("time.sleep", {}), ("AdaptiveCaptureVAD", {"side_effect": create_vad}),
        ):
            patcher = patch("scripts.voice_loop." + name, **kwargs)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        print_patch = patch("builtins.print")
        self.printed = print_patch.start()
        self.addCleanup(print_patch.stop)

    def pcm(self, rms):
        return struct.pack("<ii", int(rms * 2147483648), 0) * 960

    def record(self, **kwargs):
        record_audio_with_capture_vad(self.output_path, "hw:test", 1, 0.02, 0.04, 10, 2, backend="alsa", **kwargs)

    def test_ready_once_after_adaptive_thresholds_are_frozen(self):
        self.process.stdout.read.side_effect = [self.pcm(0.0638)] * 25 + [self.pcm(0.11)] * 20 + [self.pcm(0.001)] * 2

        def ready():
            self.assertEqual(self.process.stdout.read.call_count, 25)
            vad = self.vads[0]
            self.assertTrue(vad.calibrated)
            self.assertFalse(vad.speech_started)
            self.assertAlmostEqual(vad.noise_floor, 0.0638, places=6)
            self.assertEqual(vad.start_threshold, 0.10)
            self.assertLess(vad.continue_threshold, vad.start_threshold)
            self.assertEqual(self.printed.call_args.args, ("Speak now...",))

        callback = MagicMock(side_effect=ready)
        self.record(on_capture_ready=callback, max_start_threshold=0.10)
        callback.assert_called_once_with()
        self.process.terminate.assert_called_once()
        self.process.communicate.assert_called_once_with(timeout=5)
        diagnostic = next(c.args[0] for c in self.printed.call_args_list if c.args[0].startswith("Capture VAD:"))
        self.assertIn("max_wait_rms=0.1100", diagnostic)

    def test_nonadaptive_ready_after_warmup_before_first_speech_chunk(self):
        self.process.stdout.read.side_effect = [self.pcm(0.06), self.pcm(0), self.pcm(0)]

        def ready():
            self.mocks["time.sleep"].assert_called_once_with(1)
            self.process.stdout.read.assert_not_called()
            self.assertEqual(self.printed.call_args.args, ("Speak now...",))

        callback = MagicMock(side_effect=ready)
        self.record(adaptive=False, on_capture_ready=callback)
        callback.assert_called_once_with()

    def test_rejected_burst_does_not_fire_ready_callback_again(self):
        self.process.stdout.read.side_effect = ([self.pcm(0.01)] * 25 + [self.pcm(0.06)] * 3
                                               + [self.pcm(0)] * 2 + [self.pcm(0.06)] * 20 + [self.pcm(0)] * 2)
        callback = MagicMock()
        self.record(on_capture_ready=callback)
        callback.assert_called_once_with()
        self.assertTrue(self.vads[-1].accepted)

    def test_omitted_callback_preserves_identical_audio_and_thresholds(self):
        chunks = [self.pcm(0.01)] * 25 + [self.pcm(0.06)] * 20 + [self.pcm(0)] * 2
        self.process.stdout.read.side_effect = chunks
        self.record()
        original_audio = self.output_path.read_bytes()
        original_thresholds = (self.vads[-1].start_threshold, self.vads[-1].continue_threshold)
        self.process.stdout.read.side_effect = chunks
        self.record(on_capture_ready=MagicMock())
        self.assertEqual(self.output_path.read_bytes(), original_audio)
        self.assertEqual((self.vads[-1].start_threshold, self.vads[-1].continue_threshold), original_thresholds)

    def test_callback_failure_and_interrupt_reap_capture_and_cancel_watchdog(self):
        for adaptive in (True, False):
            for error in (RuntimeError("ready failed"), KeyboardInterrupt()):
                with self.subTest(adaptive=adaptive, error=error):
                    self.process.reset_mock()
                    self.process.stdout.read.side_effect = [self.pcm(0.01)] * 25
                    self.mocks["Timer"].reset_mock()
                    callback = MagicMock(side_effect=error)
                    expected = KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else VoiceLoopError
                    with self.assertRaises(expected):
                        self.record(adaptive=adaptive, on_capture_ready=callback)
                    callback.assert_called_once()
                    self.process.terminate.assert_called_once()
                    self.process.communicate.assert_called_once_with(timeout=5)
                    self.mocks["Timer"].return_value.cancel.assert_called_once()
                    self.assertFalse(self.output_path.exists())

    def test_calibration_timeout_does_not_signal_ready(self):
        self.process.stdout.read.side_effect = [self.pcm(0.01)] * 25
        callback = MagicMock()
        with self.assertRaisesRegex(VoiceLoopError, "start timeout"):
            record_audio_with_capture_vad(self.output_path, "hw:test", 0, 0.02, 0.8, 10, 0.5,
                                          backend="alsa", on_capture_ready=callback)
        callback.assert_not_called()
        self.process.terminate.assert_called_once()

    @override_settings(RAG_ENABLED=True, AI_ENGINE="local")
    @patch("knowledge_base.services.is_rag_available", return_value=False)
    def test_real_interactive_voice_cycle_preserves_accepted_vad_timing(self, rag_available):
        display = MagicMock()
        states = ["READY"]
        display.set_state.side_effect = lambda state: states.append(state.value)
        chunks = iter([self.pcm(0.01)] * 25 + [self.pcm(0.06)] * 20 + [self.pcm(0)] * 2)

        def warmup(seconds):
            self.assertEqual(states, ["READY", "LISTENING"])
            self.process.stdout.read.assert_not_called()

        def read(size):
            self.assertEqual(states[-1], "LISTENING")
            return next(chunks)

        self.mocks["time.sleep"].side_effect = warmup
        self.process.stdout.read.side_effect = read
        config = VoiceLoopConfig("", audio_backend="alsa", capture_vad_enabled=True, capture_vad_silence_seconds=0.04)
        with patch("scripts.voice_loop.normalize_audio"), patch("scripts.voice_loop.get_stt_provider") as stt, patch(
            "scripts.voice_loop.get_tts_provider"
        ), patch("scripts.voice_loop.AssistantService.process_message_stream", return_value=[{"type": "error", "detail": "unavailable"}]), patch(
            "scripts.voice_loop.close_tts_provider"
        ):
            stt.return_value.transcribe.return_value = "What is an ESP32?"
            with self.assertRaisesRegex(VoiceLoopError, "unavailable"):
                self.voice_loop.run_voice_cycle(config, display=display)
        self.assertEqual(states, ["READY", "LISTENING", "THINKING", "ERROR"])
        # Plain voice planning no longer probes RAG; the mocked service would
        # otherwise build the sole authoritative capability registry.
        rag_available.assert_not_called()
        self.assertAlmostEqual(self.vads[-1].start_threshold, 0.027, places=6)
        self.process.terminate.assert_called_once()
