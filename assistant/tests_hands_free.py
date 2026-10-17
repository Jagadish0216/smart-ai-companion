"""Deterministic post-wake retention, endpoint, pipe and no-speech acceptance."""

from dataclasses import replace
import io
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch
import wave

from django.test import SimpleTestCase, override_settings

from assistant.voice.hands_free import (
    FRAME_BYTES, HandsFreeCaptureConfig, HandsFreeCaptureError, HandsFreeEndpointDetector,
    NativeRequestReader, normalized_left_rms, record_audio_hands_free,
)
from assistant.voice.onnx_logging import configure_cpu_onnx_logging
from scripts import voice_loop


def pcm(rms, frames=960, right=0):
    return struct.pack("<ii", int(rms * 2147483647), right) * frames


class HandsFreeConfigTests(SimpleTestCase):
    def test_production_defaults(self):
        self.assertEqual(HandsFreeCaptureConfig.from_settings(), HandsFreeCaptureConfig(0.25, 0.25, 6, 0.9))

    def test_all_durations_have_finite_positive_bounds(self):
        for field, low, high in (("warmup_seconds", 0.02, 2), ("calibration_seconds", 0.02, 2),
                                 ("max_seconds", 0.1, 30), ("end_silence_seconds", 0.1, 5)):
            for value in (0, -1, low / 2, high + 1, float("nan"), float("inf")):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, field.upper()):
                    replace(HandsFreeCaptureConfig(), **{field: value}).validate()
            replace(HandsFreeCaptureConfig(), **{field: low}).validate()
            replace(HandsFreeCaptureConfig(), **{field: high}).validate()

    @override_settings(VOICE_HANDS_FREE_MAX_SECONDS="invalid")
    def test_bad_environment_value_has_actionable_message(self):
        with self.assertRaisesRegex(ValueError, "VOICE_HANDS_FREE_MAX_SECONDS"):
            HandsFreeCaptureConfig.from_settings()

    @override_settings(VOICE_HANDS_FREE_VAD_MAX_START_THRESHOLD="invalid",
                       VOICE_HANDS_FREE_WARMUP_SECONDS="0.12", VOICE_HANDS_FREE_CALIBRATION_SECONDS="0.16",
                       VOICE_HANDS_FREE_MAX_SECONDS="4", VOICE_HANDS_FREE_END_SILENCE_SECONDS="0.8")
    def test_new_settings_are_independent_of_deprecated_ceiling(self):
        self.assertEqual(HandsFreeCaptureConfig.from_settings(), HandsFreeCaptureConfig(0.12, 0.16, 4, 0.8))


class HandsFreeEndpointTests(SimpleTestCase):
    def test_native_rms_uses_left_only_and_checks_frame_integrity(self):
        self.assertEqual(normalized_left_rms(pcm(0, right=2147483647)), 0)
        self.assertAlmostEqual(normalized_left_rms(pcm(0.062, right=-2147483648)), 0.062, places=6)
        with self.assertRaises(HandsFreeCaptureError):
            normalized_left_rms(b"partial")

    def test_physical_floor_and_nonconsecutive_peaks_establish_speech(self):
        detector = HandsFreeEndpointDetector(0.062, 0.9)
        self.assertAlmostEqual(detector.speech_threshold, 0.0743)
        for value, expected in ((0.10, False), (0.062, False), (0.10, True)):
            self.assertFalse(detector.feed(value, 0.02))
            self.assertEqual(detector.speech_seen, expected)

    def test_physical_strong_single_peak_counts_as_evidence(self):
        detector = HandsFreeEndpointDetector(0.0619, 0.9)
        self.assertFalse(detector.feed(0.1171, 0.02))
        self.assertTrue(detector.speech_seen)

    def test_no_evidence_never_ends_even_with_long_silence(self):
        detector = HandsFreeEndpointDetector(0.0628, 0.9)
        for _ in range(500):
            self.assertFalse(detector.feed(0.0668, 0.02))
        self.assertFalse(detector.speech_seen)

    def test_sustained_quiet_after_evidence_ends_and_activity_resets_quiet(self):
        detector = HandsFreeEndpointDetector(0.062, 0.9)
        detector.feed(0.12, 0.02)
        for _ in range(44):
            self.assertFalse(detector.feed(0.062, 0.02))
        self.assertFalse(detector.feed(0.07, 0.02))  # Quieter word above continuation.
        self.assertEqual(detector.quiet_seconds, 0)
        for _ in range(44):
            self.assertFalse(detector.feed(0.062, 0.02))
        self.assertTrue(detector.feed(0.062, 0.02))

    def test_spaced_isolated_peaks_outside_rolling_window_do_not_accumulate(self):
        detector = HandsFreeEndpointDetector(0.062, 0.9)
        for _ in range(10):
            detector.feed(0.10, 0.02)
            for _ in range(5):
                detector.feed(0.062, 0.02)
        self.assertFalse(detector.speech_seen)

    def test_thresholds_are_bounded_and_continuation_is_strict(self):
        for floor in (0, 0.001, 0.062, 0.1, 0.5, 1):
            detector = HandsFreeEndpointDetector(floor, 0.9)
            self.assertLess(detector.continue_threshold, detector.speech_threshold)
            self.assertTrue(0.035 <= detector.speech_threshold <= 0.08)
        for value in (float("nan"), -0.1, 1.1):
            with self.assertRaises(ValueError):
                HandsFreeEndpointDetector(value, 0.9)


class HandsFreeRecordingTests(SimpleTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "request.wav"
        self.config = HandsFreeCaptureConfig(0.04, 0.04, 0.2, 0.1)
        reader_patch = patch("assistant.voice.hands_free.NativeRequestReader")
        self.reader_factory = reader_patch.start()
        self.addCleanup(reader_patch.stop)
        self.reader = self.reader_factory.return_value
        sleep_patch = patch("assistant.voice.hands_free.time.sleep")
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        print_patch = patch("builtins.print")
        self.printed = print_patch.start()
        self.addCleanup(print_patch.stop)

    def feed(self, post, noise=0.062):
        self.warmup = [pcm(0.9, right=123), pcm(0.8, right=456)]
        self.calibration = [pcm(noise, right=789)] * 2
        self.reader.read_frames.side_effect = self.warmup + self.calibration + post

    def audio(self):
        with wave.open(str(self.path), "rb") as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (48000, 2, 4))
            return wav.readframes(wav.getnframes())

    def diagnostic(self):
        lines = [c.args[0] for c in self.printed.call_args_list if c.args and c.args[0].startswith("Hands-free capture:")]
        self.assertEqual(len(lines), 1)
        self.assertNotIn("start_timeout", lines[0])
        return lines[0]

    def test_active_drain_excludes_warmup_and_calibration_and_signals_ready_once(self):
        post = [pcm(0.0668, right=i) for i in range(10)]
        self.feed(post)

        def ready():
            self.assertEqual(self.reader.read_frames.call_args_list, [call(960)] * 4)
            self.assertEqual(self.printed.call_args.args, ("Speak now...",))
            self.assertFalse(self.path.exists())

        callback = MagicMock(side_effect=ready)
        record_audio_hands_free(self.path, "hw:test", self.config, on_capture_ready=callback)
        callback.assert_called_once_with()
        self.sleep.assert_not_called()
        self.assertEqual(self.audio(), b"".join(post))
        self.reader.start.assert_called_once()
        self.reader.close.assert_called_once()
        self.assertIn("speech_seen=false", self.diagnostic())
        self.assertIn("stop_reason=max_duration", self.diagnostic())
        self.assertIn("noise_floor=0.0620", self.diagnostic())

    def test_weak_fluctuating_request_is_retained_without_any_speech_classification(self):
        post = [pcm(value, right=i) for i, value in enumerate([0.06, 0.065, 0.0668, 0.073, 0.063] * 2)]
        self.feed(post)
        record_audio_hands_free(self.path, "hw:test", self.config)
        self.assertEqual(self.audio(), b"".join(post))
        self.assertIn("speech_seen=false", self.diagnostic())
        self.assertIn("capture_duration=0.20s", self.diagnostic())

    def test_nonconsecutive_speech_then_quiet_ends_early_and_retains_every_chunk(self):
        post = [pcm(value) for value in [0.10, 0.062, 0.10] + [0.062] * 10]
        self.feed(post)
        record_audio_hands_free(self.path, "hw:test", self.config)
        self.assertEqual(self.audio(), b"".join(post[:8]))
        self.assertIn("speech_seen=true", self.diagnostic())
        self.assertIn("stop_reason=silence", self.diagnostic())
        self.assertIn("capture_duration=0.16s", self.diagnostic())

    def test_uninterrupted_speech_also_has_a_maximum_duration(self):
        post = [pcm(0.12)] * 10
        self.feed(post)
        record_audio_hands_free(self.path, "hw:test", self.config)
        self.assertEqual(self.audio(), b"".join(post))
        self.assertIn("speech_seen=true", self.diagnostic())
        self.assertIn("stop_reason=max_duration", self.diagnostic())

    def test_exact_non_chunk_aligned_phase_lengths_do_not_leak_audio(self):
        config = HandsFreeCaptureConfig(0.025, 0.025, 0.125, 0.9)
        data = pcm(0.9, frames=1200) + pcm(0.062, frames=1200) + pcm(0.0668, frames=6000)
        offset = 0

        def read(frames):
            nonlocal offset
            result = data[offset:offset + frames * FRAME_BYTES]
            offset += len(result)
            return result

        self.reader.read_frames.side_effect = read
        record_audio_hands_free(self.path, "hw:test", config)
        self.assertEqual(offset, len(data))
        self.assertEqual(self.audio(), data[2400 * FRAME_BYTES:])
        self.assertEqual(self.reader.read_frames.call_args_list[:4], [call(960), call(240), call(960), call(240)])

    def test_callback_exception_and_interrupt_close_capture(self):
        for error in (RuntimeError("ready failed"), KeyboardInterrupt()):
            self.reader.reset_mock()
            self.feed([pcm(0)] * 10)
            with self.subTest(error=error), self.assertRaises(HandsFreeCaptureError if isinstance(error, Exception) else KeyboardInterrupt):
                record_audio_hands_free(self.path, "hw:test", self.config, on_capture_ready=MagicMock(side_effect=error))
            self.reader.close.assert_called_once()
            self.assertFalse(self.path.exists())

    def test_ctrl_c_during_each_phase_closes_capture(self):
        for index in (0, 2, 4):
            self.reader.reset_mock()
            chunks = [pcm(0.062)] * index + [KeyboardInterrupt()]
            self.reader.read_frames.side_effect = chunks
            with self.subTest(index=index), self.assertRaises(KeyboardInterrupt):
                record_audio_hands_free(self.path, "hw:test", self.config)
            self.reader.close.assert_called_once()

    def test_arecord_start_and_pipe_failures_are_errors_and_close(self):
        for starting in (True, False):
            self.reader.reset_mock()
            self.reader.start.side_effect = OSError("arecord failed") if starting else None
            self.reader.read_frames.side_effect = OSError("pipe failed")
            with self.subTest(starting=starting), self.assertRaises(HandsFreeCaptureError):
                record_audio_hands_free(self.path, "hw:test", self.config)
            self.reader.close.assert_called_once()

    def test_invalid_config_does_not_open_hardware(self):
        with self.assertRaises(ValueError):
            record_audio_hands_free(self.path, "hw:test", replace(self.config, max_seconds=0))
        self.reader.start.assert_not_called()


class NativeRequestReaderTests(SimpleTestCase):
    def setUp(self):
        self.reader = NativeRequestReader("hw:test", 2)
        self.process = MagicMock()
        self.process.poll.return_value = None
        self.process.stdout.fileno.return_value = 123
        self.mocks = {}
        for name, kwargs in (("subprocess.Popen", {"return_value": self.process}),
                             ("os.set_blocking", {"create": True}), ("selectors.DefaultSelector", {}),
                             ("os.read", {}), ("time.monotonic", {"return_value": 0})):
            patcher = patch("assistant.voice.hands_free." + name, **kwargs)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.selector = self.mocks["selectors.DefaultSelector"].return_value
        self.selector.select.return_value = [True]

    def test_exact_native_command_short_reads_and_cleanup(self):
        self.reader.start()
        self.assertEqual(self.mocks["subprocess.Popen"].call_args.args[0],
                         ["arecord", "-D", "hw:test", "-c", "2", "-r", "48000", "-f", "S32_LE", "-t", "raw"])
        self.mocks["os.read"].side_effect = [b"abc", b"defgh"]
        self.assertEqual(self.reader.read_frames(1), b"abcdefgh")
        self.assertEqual(self.mocks["os.read"].call_args_list, [call(123, 8), call(123, 5)])
        self.reader.close()
        self.reader.close()
        self.process.terminate.assert_called_once()
        self.process.wait.assert_called_once_with(timeout=2)
        self.process.stdout.close.assert_called_once()
        self.selector.close.assert_called_once()

    def test_stall_uses_selector_and_deadline_not_busy_polling(self):
        self.reader.start()
        self.selector.select.return_value = []
        self.mocks["time.monotonic"].side_effect = [0, 3]
        with self.assertRaisesRegex(HandsFreeCaptureError, "deadline"):
            self.reader.read_frames(960)
        self.selector.select.assert_called_once_with(0.25)
        self.reader.close()

    def test_eof_and_early_process_exit_are_hardware_errors(self):
        self.reader.start()
        self.mocks["os.read"].return_value = b""
        with self.assertRaisesRegex(HandsFreeCaptureError, "closed"):
            self.reader.read_frames(960)
        self.selector.select.return_value = []
        self.process.poll.return_value = 7
        self.process.returncode = 7
        with self.assertRaisesRegex(HandsFreeCaptureError, "exit 7"):
            self.reader.read_frames(960)
        self.reader.close()
        self.process.wait.assert_called_once()

    def test_capture_cleanup_kills_and_reaps_hung_arecord(self):
        self.reader.start()
        self.process.wait.side_effect = [subprocess.TimeoutExpired("arecord", 2), 0]
        self.reader.close()
        self.process.kill.assert_called_once()
        self.assertEqual(self.process.wait.call_args_list, [call(timeout=2), call(timeout=5)])
        self.process.stdout.close.assert_called_once()

    def test_start_failure_reaps_created_child(self):
        self.mocks["os.set_blocking"].side_effect = OSError("setup failed")
        with self.assertRaises(OSError):
            self.reader.start()
        self.process.terminate.assert_called_once()
        self.process.wait.assert_called_once()

    def test_ctrl_c_in_actual_recorder_reaps_arecord(self):
        self.mocks["os.read"].side_effect = KeyboardInterrupt
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(KeyboardInterrupt):
            record_audio_hands_free(Path(directory) / "request.wav", "hw:test", HandsFreeCaptureConfig())
        self.process.terminate.assert_called_once()
        self.process.wait.assert_called_once()
        self.process.stdout.close.assert_called_once()


class HandsFreeVoiceCycleTests(SimpleTestCase):
    def setUp(self):
        self.config = voice_loop.VoiceLoopConfig("", audio_backend="alsa", capture_vad_enabled=False)
        self.capture = HandsFreeCaptureConfig(0.04, 0.04, 0.2, 0.1)
        self.display = MagicMock()
        self.states = ["READY"]
        self.display.set_state.side_effect = lambda state: self.states.append(state.value)
        self.mocks = {}
        for name in ("record_audio_hands_free", "record_audio_with_capture_vad", "record_audio_alsa",
                     "normalize_audio", "get_stt_provider", "get_tts_provider", "close_tts_provider",
                     "AssistantService.process_message_stream", "speak_stream_response"):
            patcher = patch("scripts.voice_loop." + name)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

        def record(path, device, config, *, on_capture_ready):
            self.assertEqual(self.states[-1], "READY")
            on_capture_ready()
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(2)
                wav.setsampwidth(4)
                wav.setframerate(48000)
                wav.writeframes(pcm(0.0668) * 10)

        self.mocks["record_audio_hands_free"].side_effect = record
        self.mocks["get_stt_provider"].return_value.transcribe.return_value = "What is an ESP32?"

        def speak(*args, on_playback_start, on_caption,
                  on_stream_start=None, on_stream_metadata=None):
            self.assertEqual(self.states[-1], "THINKING")
            self.display.set_query.assert_called_once_with("What is an ESP32?")
            if on_stream_start is not None:
                on_stream_start({"type": "start", "route": "local"})
            on_playback_start("An ESP32 is a microcontroller.")
            on_caption("An ESP32 is a microcontroller.")
            if on_stream_metadata is not None:
                on_stream_metadata({"type": "done", "route": "local"})
            return 42, {"resource_profile": "BALANCED"}

        self.mocks["speak_stream_response"].side_effect = speak
        print_patch = patch("builtins.print")
        self.printed = print_patch.start()
        self.addCleanup(print_patch.stop)

    def cycle(self):
        return voice_loop.run_voice_cycle(self.config, 12, display=self.display, hands_free_capture=self.capture)

    def test_successful_display_lifecycle_and_thinking_during_normalization_and_stt(self):
        self.mocks["normalize_audio"].side_effect = lambda *args, **kwargs: self.assertEqual(self.states[-1], "THINKING")
        self.mocks["get_stt_provider"].return_value.transcribe.side_effect = lambda path: (
            self.assertEqual(self.states[-1], "THINKING") or "What is an ESP32?"
        )
        self.assertEqual(self.cycle(), 42)
        self.assertEqual(self.states, ["READY", "LISTENING", "THINKING", "SPEAKING", "READY"])
        self.mocks["record_audio_with_capture_vad"].assert_not_called()
        self.mocks["record_audio_alsa"].assert_not_called()

    def test_unclassified_max_duration_recording_reaches_stt_before_nonfatal_no_speech(self):
        self.mocks["record_audio_hands_free"].side_effect = record_audio_hands_free
        self.mocks["get_stt_provider"].return_value.transcribe.return_value = "[BLANK_AUDIO]"
        post = [pcm(0.0668)] * 10

        def normalize(source, destination, **kwargs):
            with wave.open(str(source), "rb") as wav:
                self.assertEqual(wav.readframes(wav.getnframes()), b"".join(post))
            self.assertEqual(kwargs, {"left_channel": True})
            self.assertEqual(self.states[-1], "THINKING")

        self.mocks["normalize_audio"].side_effect = normalize
        with patch("assistant.voice.hands_free.NativeRequestReader") as factory:
            factory.return_value.read_frames.side_effect = [pcm(0.9)] * 2 + [pcm(0.062)] * 2 + post
            with self.assertRaises(voice_loop.NoSpeechDetected):
                self.cycle()
            factory.return_value.close.assert_called_once()
        self.mocks["get_stt_provider"].return_value.transcribe.assert_called_once()
        self.assertEqual(self.states, ["READY", "LISTENING", "THINKING", "READY"])
        self.mocks["AssistantService.process_message_stream"].assert_not_called()
        self.mocks["get_tts_provider"].assert_not_called()
        self.mocks["close_tts_provider"].assert_not_called()

    def test_all_empty_and_no_speech_transcripts_return_ready_without_error(self):
        for transcript in ("", "  ", "[BLANK_AUDIO]", "[no speech]", "silence", "inaudible"):
            self.states[:] = ["READY"]
            self.mocks["get_stt_provider"].return_value.transcribe.return_value = transcript
            with self.subTest(transcript=transcript), self.assertRaisesRegex(voice_loop.NoSpeechDetected, "No request speech recognized"):
                self.cycle()
            self.assertEqual(self.states, ["READY", "LISTENING", "THINKING", "READY"])
        self.mocks["speak_stream_response"].assert_not_called()
        self.mocks["close_tts_provider"].assert_not_called()

    def test_actual_arecord_failure_becomes_error(self):
        self.mocks["record_audio_hands_free"].side_effect = record_audio_hands_free
        with patch("assistant.voice.hands_free.NativeRequestReader") as factory:
            factory.return_value.start.side_effect = FileNotFoundError("arecord unavailable")
            with self.assertRaisesRegex(HandsFreeCaptureError, "arecord unavailable"):
                self.cycle()
            factory.return_value.close.assert_called_once()
        self.assertEqual(self.states, ["READY", "ERROR"])
        self.mocks["get_stt_provider"].assert_not_called()
        self.mocks["close_tts_provider"].assert_called_once()


class CpuOnnxLoggingTests(SimpleTestCase):
    def test_supported_api_sets_error_severity(self):
        runtime = MagicMock()
        with patch("assistant.voice.onnx_logging.importlib.import_module", return_value=runtime):
            self.assertTrue(configure_cpu_onnx_logging())
        runtime.set_default_logger_severity.assert_called_once_with(3)

    def test_dependency_missing_api_missing_or_setter_failure_is_optional(self):
        failing = SimpleNamespace(set_default_logger_severity=MagicMock(side_effect=RuntimeError("unsupported")))
        for runtime in (SimpleNamespace(), failing):
            with patch("assistant.voice.onnx_logging.importlib.import_module", return_value=runtime):
                self.assertFalse(configure_cpu_onnx_logging())
        with patch("assistant.voice.onnx_logging.importlib.import_module", side_effect=ImportError):
            self.assertFalse(configure_cpu_onnx_logging())

    def test_python_interrupt_is_not_suppressed(self):
        with patch("assistant.voice.onnx_logging.importlib.import_module", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                configure_cpu_onnx_logging()


@override_settings(VOICE_WAKE_WORD_ENABLED=True)
class HandsFreeShutdownTests(SimpleTestCase):
    def test_parent_ctrl_c_during_real_request_capture_reaps_and_closes_every_provider(self):
        config = voice_loop.VoiceLoopConfig("", audio_backend="alsa", capture_vad_enabled=False)
        display, tts, wake, process = MagicMock(), MagicMock(), MagicMock(), MagicMock()
        from assistant.voice.wakeword.base import WakeWordDetection
        wake.wait_for_wake_word.return_value = WakeWordDetection(True, "hey_jarvis", 0.9)
        process.poll.return_value = None
        process.stdout.fileno.return_value = 123
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch("scripts.voice_loop.VoiceLoopConfig.from_settings", return_value=config))
            stack.enter_context(patch("scripts.voice_loop.get_companion_display", return_value=display))
            stack.enter_context(patch("scripts.voice_loop.get_tts_provider", return_value=tts))
            stack.enter_context(patch("scripts.voice_loop.get_wake_word_provider", return_value=wake))
            stack.enter_context(patch("scripts.voice_loop.close_tts_provider"))
            stack.enter_context(patch("assistant.voice.hands_free.subprocess.Popen", return_value=process))
            stack.enter_context(patch("assistant.voice.hands_free.os.set_blocking", create=True))
            selector = stack.enter_context(patch("assistant.voice.hands_free.selectors.DefaultSelector")).return_value
            selector.select.return_value = [True]
            stack.enter_context(patch("assistant.voice.hands_free.os.read", side_effect=KeyboardInterrupt))
            stack.enter_context(patch("builtins.print"))
            self.assertEqual(voice_loop.main(["--hands-free"]), 130)
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=2)
        process.stdout.close.assert_called_once()
        selector.close.assert_called_once()
        wake.wait_for_wake_word.assert_called_once()
        wake.close.assert_called_once()
        tts.close.assert_called_once()
        display.close.assert_called_once()
