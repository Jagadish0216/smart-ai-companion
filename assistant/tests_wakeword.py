"""Hardware/network-free wake-word and hands-free lifecycle acceptance tests."""

from dataclasses import replace
import io
import math
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from assistant.voice.wakeword.audio import AlsaWakeCapture, CAPTURE_READ_BYTES, LeftChannelConverter
from assistant.voice.wakeword.base import (
    DisabledWakeWordProvider, WakeWordConfig, WakeWordDetection, WakeWordError,
)
from assistant.voice.wakeword.factory import get_wake_word_provider
from assistant.voice.wakeword.models import prepare_model, required_assets, validate_installed_assets
from assistant.voice.wakeword.openwakeword_provider import OpenWakeWordProvider
from assistant.tests_voice_stream import stream_events
from scripts import voice_loop


def native_pcm(left=0, right=0, frames=3840):
    return struct.pack("<ii", left, right) * frames


class WakeWordConfigurationTests(SimpleTestCase):
    def test_safe_defaults(self):
        config = WakeWordConfig.from_settings()
        self.assertEqual(config, WakeWordConfig())

    def test_disabled_factory_does_not_import_engine_or_open_microphone(self):
        with patch("assistant.voice.wakeword.audio.subprocess.Popen") as popen, patch(
            "assistant.voice.wakeword.models.importlib.import_module"
        ) as imports:
            provider = get_wake_word_provider(WakeWordConfig())
            self.assertIsInstance(provider, DisabledWakeWordProvider)
            provider.warm()
            self.assertFalse(provider.wait_for_wake_word("unused").detected)
            provider.reset()
            provider.close()
            imports.assert_not_called()
            popen.assert_not_called()

    def test_factory_selects_lazy_openwakeword(self):
        with patch("assistant.voice.wakeword.models.importlib.import_module") as imports:
            provider = get_wake_word_provider(WakeWordConfig(enabled=True))
            self.assertIsInstance(provider, OpenWakeWordProvider)
            imports.assert_not_called()

    def test_rejects_unknown_engine(self):
        with self.assertRaisesRegex(WakeWordError, "ENGINE"):
            get_wake_word_provider(WakeWordConfig(engine="other"))

    def test_rejects_invalid_model_configuration(self):
        for config in (WakeWordConfig(model=""), WakeWordConfig(model="unknown"), WakeWordConfig(model_path="/tmp/model.txt")):
            with self.subTest(config=config), self.assertRaises(WakeWordError):
                config.validate()

    def test_numeric_bounds(self):
        for field, values in {
            "threshold": (0, -1, 1.1, math.nan, math.inf),
            "vad_threshold": (-0.1, 1.1, math.nan, math.inf),
            "timeout_seconds": (-1, math.nan, math.inf),
            "cooldown_seconds": (-1, 5.1, math.nan, math.inf),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaisesRegex(WakeWordError, field.upper()):
                    replace(WakeWordConfig(), **{field: value}).validate()
        WakeWordConfig(threshold=1, vad_threshold=0, timeout_seconds=0, cooldown_seconds=5).validate()

    @override_settings(VOICE_WAKE_WORD_THRESHOLD="not-a-number")
    def test_invalid_numeric_setting_has_clean_error(self):
        with self.assertRaisesRegex(WakeWordError, "VOICE_WAKE_WORD_THRESHOLD"):
            WakeWordConfig.from_settings()

    @override_settings(VOICE_WAKE_WORD_ENABLED="true", VOICE_WAKE_WORD_MODEL="ignored",
                       VOICE_WAKE_WORD_MODEL_PATH=" /tmp/custom.onnx ", VOICE_WAKE_WORD_TIMEOUT_SECONDS="2.5")
    def test_custom_path_overrides_builtin(self):
        config = WakeWordConfig.from_settings()
        self.assertTrue(config.enabled)
        self.assertEqual(config.selected_model, str(Path("/tmp/custom.onnx")))
        self.assertEqual(config.inference_framework, "onnx")
        self.assertEqual(config.timeout_seconds, 2.5)
        self.assertEqual(WakeWordConfig(model_path="custom.tflite").inference_framework, "tflite")


class WakeAudioConversionTests(SimpleTestCase):
    def test_left_channel_only(self):
        self.assertEqual(LeftChannelConverter().convert(native_pcm(right=2147483647)), b"\0\0" * 1280)
        output = LeftChannelConverter().convert(native_pcm(left=65536000, right=-2147483648))
        self.assertEqual(struct.unpack("<1280h", output)[-100:], (1000,) * 100)

    def test_native_format_rate_and_signed_saturation(self):
        for amplitude, expected in ((2147483647, 32767), (-2147483648, -32768), (-65536000, -1000)):
            with self.subTest(amplitude=amplitude):
                output = LeftChannelConverter().convert(native_pcm(left=amplitude))
                self.assertEqual(len(output), 2560)
                self.assertEqual(struct.unpack("<1280h", output)[-100:], (expected,) * 100)

    def test_arbitrary_byte_boundaries_preserve_fir_history_and_decimation_phase(self):
        data = b"".join(struct.pack("<ii", (i % 17 - 8) * 12345678, 2147483647) for i in range(7777))
        expected = LeftChannelConverter().convert(data)
        converter = LeftChannelConverter()
        result = []
        offset = 0
        sizes = [1, 7, 19, 305, 1200, 30720]
        while offset < len(data):
            for size in sizes:
                result.append(converter.convert(data[offset:offset + size]))
                offset += size
        self.assertEqual(b"".join(result), expected)
        self.assertEqual(converter.partial, b"")

    def test_partial_frame_and_reset(self):
        converter = LeftChannelConverter()
        self.assertEqual(converter.convert(b"\x01"), b"")
        self.assertEqual(len(converter.convert(b"\0" * 7)), 2)
        converter.reset()
        self.assertEqual(converter.convert(native_pcm(left=1000000)), LeftChannelConverter().convert(native_pcm(left=1000000)))

    def test_low_pass_suppresses_aliasing(self):
        import numpy as np
        t = np.arange(48000) / 48000

        def amplitude(frequency):
            left = (np.sin(2 * np.pi * frequency * t) * (2 ** 29)).astype("<i4")
            stereo = np.column_stack((left, np.zeros_like(left))).astype("<i4").tobytes()
            samples = np.frombuffer(LeftChannelConverter().convert(stereo), "<i2")[100:].astype(float)
            return np.sqrt(np.mean(samples ** 2))

        self.assertLess(amplitude(12000), amplitude(1000) * 0.01)


class WakeWordProviderTests(SimpleTestCase):
    def setUp(self):
        self.engine = MagicMock()
        self.model = self.engine.Model.return_value
        self.provider = OpenWakeWordProvider(WakeWordConfig(enabled=True))
        for name, value in (("load_engine", self.engine), ("validate_installed_assets", None)):
            patcher = patch("assistant.voice.wakeword.openwakeword_provider." + name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        capture_patch = patch("assistant.voice.wakeword.openwakeword_provider.AlsaWakeCapture")
        self.capture_factory = capture_patch.start()
        self.addCleanup(capture_patch.stop)
        self.capture = self.capture_factory.return_value
        self.capture.chunks.return_value = iter([native_pcm()])
        self.model.predict.return_value = {"hey_jarvis": 0.5}

    def test_builtin_warm_is_persistent_and_has_no_capture(self):
        self.provider.warm()
        self.provider.warm()
        self.engine.Model.assert_called_once_with(wakeword_models=["hey_jarvis"], inference_framework="onnx", vad_threshold=0.5, ncpu=1)
        self.capture_factory.assert_not_called()

    def test_custom_model_warm_passes_path(self):
        self.provider.config = WakeWordConfig(enabled=True, model="ignored", model_path="custom.onnx")
        self.provider.warm()
        self.assertEqual(self.engine.Model.call_args.kwargs["wakeword_models"], ["custom.onnx"])

    def test_below_threshold_does_not_trigger(self):
        self.model.predict.return_value = {"hey_jarvis": 0.499}
        self.assertFalse(self.provider.wait_for_wake_word("mic").detected)
        self.capture.close.assert_called_once()

    def test_at_or_above_threshold_triggers_and_releases_before_return(self):
        for score in (0.5, 0.75):
            self.capture.chunks.return_value = iter([native_pcm()])
            self.model.predict.return_value = {"hey_jarvis": score}
            result = self.provider.wait_for_wake_word("mic")
            self.assertEqual((result.detected, result.model, result.score), (True, "hey_jarvis", score))
            self.assertGreaterEqual(result.elapsed_seconds, 0)
            self.assertIsNone(self.provider.capture)
        self.assertEqual(self.capture.close.call_count, 2)

    def test_prediction_frames_are_exact_int16_80ms_despite_short_chunks(self):
        import numpy as np
        data = native_pcm() * 2
        self.capture.chunks.return_value = iter([data[:13], data[13:2000], data[2000:-5], data[-5:]])
        self.model.predict.return_value = {"hey_jarvis": 0.1}
        self.assertFalse(self.provider.wait_for_wake_word("mic").detected)
        self.assertEqual(self.model.predict.call_count, 2)
        for prediction in self.model.predict.call_args_list:
            frame = prediction.args[0]
            self.assertEqual(frame.dtype, np.dtype("<i2"))
            self.assertEqual(frame.shape, (1280,))

    def test_finite_timeout_and_indefinite_deadline(self):
        with patch("assistant.voice.wakeword.openwakeword_provider.time.monotonic", return_value=100):
            self.provider.wait_for_wake_word("mic")
            self.capture.chunks.assert_called_once_with(None)
            self.provider.config = replace(self.provider.config, timeout_seconds=3)
            self.capture.chunks.return_value = iter([])
            result = self.provider.wait_for_wake_word("mic")
            self.capture.chunks.assert_called_with(103)
            self.assertFalse(result.detected)

    def test_close_and_reset_reuse_model_and_clear_vad(self):
        self.provider.warm()
        self.provider.reset()
        self.model.reset.assert_called_once()
        self.model.vad.reset_states.assert_called_once()
        self.model.vad.prediction_buffer.clear.assert_called_once()
        self.provider.capture = self.capture
        self.provider.close()
        self.provider.close()
        self.capture.close.assert_called_once()
        self.assertIsNone(self.provider.model)

    def test_interrupt_and_prediction_failure_release_capture(self):
        for error in (KeyboardInterrupt(), RuntimeError("engine error")):
            self.capture.chunks.return_value = iter([native_pcm()])
            self.model.predict.side_effect = error
            with self.subTest(error=error), self.assertRaises(KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else WakeWordError):
                self.provider.wait_for_wake_word("mic")
            self.assertIsNone(self.provider.capture)
        self.assertEqual(self.capture.close.call_count, 2)

    def test_capture_start_failure_releases(self):
        self.capture.start.side_effect = OSError("device busy")
        with self.assertRaisesRegex(WakeWordError, "device busy"):
            self.provider.wait_for_wake_word("mic")
        self.capture.close.assert_called_once()

    def test_reentrant_wait_rejected(self):
        self.provider.capture = self.capture
        with self.assertRaisesRegex(WakeWordError, "already owned"):
            self.provider.wait_for_wake_word("mic")

    def test_missing_dependency_and_model_fail_cleanly_without_capture(self):
        with patch("assistant.voice.wakeword.models.importlib.import_module", side_effect=ImportError("missing")):
            from assistant.voice.wakeword.models import load_engine
            with self.assertRaisesRegex(WakeWordError, "unavailable"):
                load_engine()
        with patch("assistant.voice.wakeword.openwakeword_provider.validate_installed_assets", side_effect=WakeWordError("missing assets")):
            with self.assertRaisesRegex(WakeWordError, "missing assets"):
                self.provider.warm()
        self.capture_factory.assert_not_called()


class WakeCaptureResourceTests(SimpleTestCase):
    def setUp(self):
        self.capture = AlsaWakeCapture("hw:test")
        self.process = MagicMock()
        self.process.poll.return_value = None
        self.process.stdout.fileno.return_value = 123
        self.popen = patch("assistant.voice.wakeword.audio.subprocess.Popen", return_value=self.process)
        self.popen_mock = self.popen.start()
        self.addCleanup(self.popen.stop)
        self.blocking = patch("assistant.voice.wakeword.audio.os.set_blocking", create=True)
        self.blocking.start()
        self.addCleanup(self.blocking.stop)
        self.select_patch = patch("assistant.voice.wakeword.audio.selectors.DefaultSelector")
        self.selector = self.select_patch.start().return_value
        self.addCleanup(self.select_patch.stop)

    def test_native_arecord_command_and_idempotent_close(self):
        self.capture.start()
        self.assertEqual(self.popen_mock.call_args.args[0], ["arecord", "-q", "-D", "hw:test", "-c", "2", "-r", "48000", "-f", "S32_LE", "-t", "raw"])
        self.assertEqual(self.popen_mock.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.capture.close()
        self.capture.close()
        self.process.terminate.assert_called_once()
        self.process.wait.assert_called_once_with(timeout=2)
        self.process.stdout.close.assert_called_once()
        self.selector.close.assert_called_once()

    def test_hung_process_is_killed_and_reaped(self):
        self.capture.start()
        self.process.wait.side_effect = [subprocess.TimeoutExpired("arecord", 2), 0]
        self.capture.close()
        self.process.kill.assert_called_once()
        self.assertEqual(self.process.wait.call_args_list, [call(timeout=2), call(timeout=5)])

    def test_child_exiting_during_kill_race_is_still_reaped(self):
        self.capture.start()
        self.process.wait.side_effect = [subprocess.TimeoutExpired("arecord", 2), 0]
        self.process.kill.side_effect = OSError("already exited")
        self.capture.close()
        self.assertEqual(self.process.wait.call_args_list, [call(timeout=2), call(timeout=5)])
        self.process.stdout.close.assert_called_once()

    def test_start_failure_reaps_child(self):
        with patch("assistant.voice.wakeword.audio.os.set_blocking", side_effect=OSError("failed")):
            with self.assertRaises(OSError):
                self.capture.start()
        self.process.wait.assert_called_once()
        self.process.stdout.close.assert_called_once()

    def test_deadline_uses_blocking_selector_and_bounded_reads(self):
        self.capture.start()
        self.selector.select.return_value = [True]
        with patch("assistant.voice.wakeword.audio.time.monotonic", side_effect=[0, 0, 0.1, 2]), patch(
            "assistant.voice.wakeword.audio.os.read", return_value=b"audio"
        ) as read:
            self.assertEqual(list(self.capture.chunks(deadline=1)), [b"audio"])
        self.selector.select.assert_called_once_with(0.25)
        read.assert_called_once_with(123, CAPTURE_READ_BYTES)
        self.capture.close()

    def test_stall_and_eof_fail_cleanly(self):
        self.capture.start()
        with patch("assistant.voice.wakeword.audio.time.monotonic", side_effect=[0, 5]):
            with self.assertRaisesRegex(WakeWordError, "stalled"):
                list(self.capture.chunks())
        self.selector.select.return_value = [True]
        with patch("assistant.voice.wakeword.audio.os.read", return_value=b""):
            with self.assertRaisesRegex(WakeWordError, "closed"):
                list(self.capture.chunks())
        self.capture.close()

    def test_ctrl_c_in_actual_capture_is_reaped_by_provider_before_propagating(self):
        provider = OpenWakeWordProvider(WakeWordConfig(enabled=True))
        provider.model = MagicMock()  # Avoid any third-party engine import.
        self.selector.select.side_effect = KeyboardInterrupt
        with patch("assistant.voice.wakeword.openwakeword_provider.AlsaWakeCapture", return_value=self.capture):
            with self.assertRaises(KeyboardInterrupt):
                provider.wait_for_wake_word("hw:test")
        self.process.terminate.assert_called_once()
        self.process.wait.assert_called_once_with(timeout=2)
        self.process.stdout.close.assert_called_once()
        self.selector.close.assert_called_once()
        self.assertIsNone(provider.capture)


class WakeModelSetupTests(SimpleTestCase):
    def engine_for(self, directory):
        def metadata(name):
            return {"model_path": str(Path(directory) / (name + ".tflite")), "download_url": "https://example.test/" + name + ".tflite"}
        return SimpleNamespace(FEATURE_MODELS={"feature": metadata("feature")},
                               VAD_MODELS={"vad": {"model_path": str(Path(directory) / "vad.onnx"), "download_url": "https://example.test/vad.onnx"}},
                               MODELS={"hey_jarvis": metadata("jarvis")})

    def test_selects_builtin_and_shared_onnx_assets(self):
        assets = required_assets(WakeWordConfig(), self.engine_for("models"))
        self.assertEqual([path.name for path, _ in assets], ["feature.onnx", "vad.onnx", "jarvis.onnx"])

    def test_custom_path_only_prepares_shared_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "custom.onnx"
            path.write_bytes(b"model")
            config = WakeWordConfig(model="ignored", model_path=str(path), vad_threshold=0)
            assets = required_assets(config, self.engine_for(directory))
            self.assertEqual([p.name for p, _ in assets], ["feature.onnx"])

    def test_missing_custom_path_and_builtin_assets_have_actionable_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = self.engine_for(directory)
            with self.assertRaisesRegex(WakeWordError, "prepare_wake_word_model"):
                validate_installed_assets(WakeWordConfig(), engine)
            with self.assertRaisesRegex(WakeWordError, "Custom wake model"):
                validate_installed_assets(WakeWordConfig(model_path=str(Path(directory) / "missing.onnx")), engine)

    def test_explicit_download_is_atomic_skips_installed_and_has_http_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = self.engine_for(directory)
            with patch("assistant.voice.wakeword.models.load_engine", return_value=engine), patch(
                "assistant.voice.wakeword.models.urllib.request.urlopen"
            ) as download:
                download.return_value.__enter__.side_effect = lambda: io.BytesIO(b"model")
                prepare_model(WakeWordConfig())
                prepare_model(WakeWordConfig())
                self.assertEqual(download.call_count, 3)
                self.assertTrue(all(item.kwargs["timeout"] == 30 for item in download.call_args_list))
            self.assertEqual(len(list(Path(directory).iterdir())), 3)

    def test_download_failure_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("assistant.voice.wakeword.models.load_engine", return_value=self.engine_for(directory)), patch(
                "assistant.voice.wakeword.models.urllib.request.urlopen"
            ) as download:
                download.return_value.__enter__.return_value.read.side_effect = [b"partial", OSError("offline")]
                with self.assertRaisesRegex(OSError, "offline"):
                    prepare_model(WakeWordConfig())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_management_command_reports_selected_model(self):
        output = io.StringIO()
        with patch("assistant.management.commands.prepare_wake_word_model.prepare_model") as prepare:
            call_command("prepare_wake_word_model", stdout=output)
        self.assertIn("model=hey_jarvis", output.getvalue())
        self.assertEqual(prepare.call_args.args[0].selected_model, "hey_jarvis")

    @override_settings(VOICE_WAKE_WORD_MODEL_PATH="custom.onnx")
    def test_command_override_does_not_mutate_settings(self):
        with patch("assistant.management.commands.prepare_wake_word_model.prepare_model") as prepare:
            call_command("prepare_wake_word_model", model="alexa", stdout=io.StringIO())
        self.assertEqual(prepare.call_args.args[0].selected_model, "alexa")
        self.assertEqual(WakeWordConfig.from_settings().model_path, "custom.onnx")

    def test_command_cleanly_reports_dependency_or_network_failure(self):
        for error in (ImportError("dependency missing"), OSError("network unavailable")):
            with patch("assistant.management.commands.prepare_wake_word_model.prepare_model", side_effect=error):
                with self.assertRaisesRegex(CommandError, str(error)):
                    call_command("prepare_wake_word_model", stdout=io.StringIO())


@override_settings(VOICE_WAKE_WORD_ENABLED=True)
class HandsFreeRuntimeTests(SimpleTestCase):
    def setUp(self):
        self.config = voice_loop.VoiceLoopConfig("", audio_backend="alsa", capture_vad_enabled=True)
        self.provider = MagicMock()
        self.display = MagicMock()
        self.tts = MagicMock()
        self.detection = WakeWordDetection(True, "hey_jarvis", 0.8, 1.2)
        self.provider.wait_for_wake_word.side_effect = [self.detection, KeyboardInterrupt()]
        self.mocks = {}
        for name, value in (("VoiceLoopConfig.from_settings", self.config), ("get_companion_display", self.display),
                            ("get_tts_provider", self.tts), ("get_wake_word_provider", self.provider), ("run_voice_cycle", 42),
                            ("time.sleep", None), ("builtins.print", None)):
            target = name if name.startswith("builtins") else "scripts.voice_loop." + name
            patcher = patch(target, return_value=value)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def test_success_resumes_after_drained_cycle_and_cooldown_with_conversation(self):
        events = []
        listening = [False]

        def wait(device):
            self.assertFalse(listening[0])
            events.append("wake")
            listening[0] = True
            listening[0] = False  # Provider's contract: release before return.
            if events.count("wake") == 3:
                raise KeyboardInterrupt
            return self.detection

        def cycle(config, conversation_id, display):
            self.assertFalse(listening[0])
            events.extend(["request_capture", "thinking", "tts", "playback", "drained"])
            return 42

        self.provider.wait_for_wake_word.side_effect = wait
        self.mocks["run_voice_cycle"].side_effect = cycle
        self.mocks["time.sleep"].side_effect = lambda seconds: events.append("cooldown")
        self.provider.reset.side_effect = lambda: events.append("reset")
        with patch("builtins.input", side_effect=AssertionError("Hands-free must never prompt")):
            self.assertEqual(voice_loop.main(["--hands-free"]), 130)
        self.assertEqual(events, ["wake", "request_capture", "thinking", "tts", "playback", "drained", "cooldown", "reset"] * 2 + ["wake"])
        self.assertEqual([c.args[1] for c in self.mocks["run_voice_cycle"].call_args_list], [None, 42])
        self.provider.warm.assert_called_once()
        self.tts.warm.assert_called_once()
        self.provider.close.assert_called_once()
        self.tts.close.assert_called_once()
        self.display.close.assert_called_once()

    def test_recoverable_cycle_failure_resumes_and_preserves_conversation(self):
        self.provider.wait_for_wake_word.side_effect = [self.detection] * 3 + [KeyboardInterrupt()]
        self.mocks["run_voice_cycle"].side_effect = [42, voice_loop.VoiceLoopError("no speech"), 43]
        with patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(voice_loop.main(["--hands-free"]), 130)
        self.assertEqual([c.args[1] for c in self.mocks["run_voice_cycle"].call_args_list], [None, 42, 42])
        states = [c.args[0] for c in self.display.set_state.call_args_list]
        self.assertIn("ERROR", states)
        self.assertIn(call(0.75), self.mocks["time.sleep"].call_args_list)
        self.assertEqual(self.provider.reset.call_count, 3)

    def test_capture_failure_and_timeout_resume_with_bounded_cooldown(self):
        self.provider.wait_for_wake_word.side_effect = [WakeWordError("mic busy"), WakeWordDetection(False), self.detection, KeyboardInterrupt()]
        with patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(voice_loop.main(["--hands-free"]), 130)
        self.mocks["run_voice_cycle"].assert_called_once()
        self.assertEqual(self.provider.reset.call_count, 3)

    def test_interrupt_during_cycle_cleans_all_providers(self):
        self.mocks["run_voice_cycle"].side_effect = KeyboardInterrupt
        self.assertEqual(voice_loop.main(["--hands-free"]), 130)
        self.provider.close.assert_called_once()
        self.tts.close.assert_called_once()
        self.display.close.assert_called_once()

    def test_startup_warm_order_and_wake_failure_closes_everything(self):
        events = []
        self.tts.warm.side_effect = lambda: events.append("tts")

        def fail():
            events.append("wake")
            raise WakeWordError("assets missing")

        self.provider.warm.side_effect = fail
        with patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(voice_loop.main(["--hands-free"]), 1)
        self.assertEqual(events, ["tts", "wake"])
        self.mocks["run_voice_cycle"].assert_not_called()
        self.provider.close.assert_called_once()
        self.tts.close.assert_called_once()
        self.display.close.assert_called_once()

    def test_wake_close_failure_does_not_prevent_other_cleanup(self):
        self.provider.close.side_effect = RuntimeError("cleanup")
        with self.assertLogs("scripts.voice_loop", level="WARNING"):
            self.assertEqual(voice_loop.main(["--hands-free"]), 130)
        self.tts.close.assert_called_once()
        self.display.close.assert_called_once()

    @override_settings(VOICE_WAKE_WORD_ENABLED=False)
    def test_disabled_handsfree_fails_before_hardware(self):
        with patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(voice_loop.main(["--hands-free"]), 1)
        self.mocks["get_companion_display"].assert_not_called()
        self.mocks["get_wake_word_provider"].assert_not_called()

    def test_handsfree_requires_native_alsa_and_existing_capture_vad(self):
        for config in (replace(self.config, audio_backend="pulse"), replace(self.config, capture_vad_enabled=False)):
            self.mocks["VoiceLoopConfig.from_settings"].return_value = config
            with patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(voice_loop.main(["--hands-free"]), 1)
        self.mocks["get_companion_display"].assert_not_called()

    def test_handsfree_cli_conflicts_and_help_do_not_initialize_hardware(self):
        for extra in (["--text", "hello"], ["--text-mode"], ["--audio-file", "test.wav"]):
            with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as error:
                voice_loop.main(["--hands-free"] + extra)
            self.assertEqual(error.exception.code, 2)
        with patch("sys.stdout", new_callable=io.StringIO) as help_text, self.assertRaises(SystemExit) as error:
            voice_loop.main(["--help"])
        self.assertEqual(error.exception.code, 0)
        self.assertIn("--hands-free", help_text.getvalue())
        self.mocks["get_companion_display"].assert_not_called()

    @override_settings(VOICE_WAKE_WORD_THRESHOLD="invalid", VOICE_WAKE_WORD_ENGINE="unsupported")
    def test_injection_and_interactive_modes_bypass_even_invalid_enabled_wake_settings(self):
        for argv in (["--text", "hello"], ["--audio-file", "test.wav"], ["--text-mode"], []):
            with self.subTest(argv=argv), patch("builtins.input", side_effect=["", "q"]):
                self.assertEqual(voice_loop.main(argv), 0)
        self.mocks["get_wake_word_provider"].assert_not_called()
        self.assertEqual(self.mocks["run_voice_cycle"].call_count, 3)


@override_settings(VOICE_WAKE_WORD_ENABLED=True)
class HandsFreeProductionCycleTests(SimpleTestCase):
    """Use the real provider, voice cycle and threaded stream with fake hardware."""

    def test_actual_cycle_never_overlaps_wake_capture_with_capture_tts_or_playback(self):
        events = []
        active = {"wake": False, "playback": False, "waits": 0}
        testcase = self

        def stage(name):
            testcase.assertFalse(active["wake"], f"wake capture overlapped {name}")
            events.append(name)

        class Capture:
            def __init__(self, device):
                testcase.assertFalse(active["wake"])
                testcase.assertFalse(active["playback"])
                self.started = False

            def start(self):
                active["waits"] += 1
                self.started = True
                active["wake"] = True
                events.append("wake_start")
                if active["waits"] == 3:
                    raise KeyboardInterrupt

            def chunks(self, deadline):
                yield native_pcm()

            def close(self):
                active["wake"] = False
                events.append("wake_released")

        class Player:
            def __init__(self, *args):
                pass

            def start(self):
                stage("playback_start")
                active["playback"] = True

            def check(self):
                testcase.assertFalse(active["wake"])

            def write_silence(self, frames):
                self.check()

            def write_file(self, path):
                stage("playback_write")

            def finish(self):
                stage("playback_drained")
                active["playback"] = False

            def abort(self):
                active["playback"] = False

        def record(path, *args, **kwargs):
            stage("request_capture")
            path.write_bytes(b"fake recording")

        def synthesize(text):
            stage("tts")
            return b"fake wav"

        def process(*args, **kwargs):
            stage("thinking")
            return stream_events("It is a microcontroller.", conversation_id=42,
                                 resource_profile="ECO")

        def prepare(source, destination, *args, **kwargs):
            stage("audio_prepare")
            destination.write_bytes(struct.pack("<ii", 1000000, 1000000) * 4800)

        def cooldown(seconds):
            self.assertFalse(active["wake"])
            self.assertFalse(active["playback"])
            events.append("cooldown")

        engine = MagicMock()
        engine.Model.return_value.predict.return_value = {"hey_jarvis": 0.8}
        engine.Model.return_value.reset.side_effect = lambda: events.append("reset")
        config = voice_loop.VoiceLoopConfig("", audio_backend="alsa", capture_vad_enabled=True)
        display = MagicMock()
        tts = MagicMock()
        tts.synthesize.side_effect = synthesize
        provider = OpenWakeWordProvider(WakeWordConfig(enabled=True))
        replacements = {
            "assistant.voice.wakeword.openwakeword_provider.load_engine": engine,
            "assistant.voice.wakeword.openwakeword_provider.validate_installed_assets": None,
            "scripts.voice_loop.VoiceLoopConfig.from_settings": config,
            "scripts.voice_loop.get_companion_display": display,
            "scripts.voice_loop.get_tts_provider": tts,
            "scripts.voice_loop.get_wake_word_provider": provider,
        }
        for name, value in replacements.items():
            patcher = patch(name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch("assistant.voice.wakeword.openwakeword_provider.AlsaWakeCapture", Capture))
            stack.enter_context(patch("scripts.voice_loop.PersistentAlsaPlayer", Player))
            stack.enter_context(patch("scripts.voice_loop.record_audio_with_capture_vad", side_effect=record))
            stack.enter_context(patch("scripts.voice_loop.normalize_audio"))
            stt = stack.enter_context(patch("scripts.voice_loop.get_stt_provider"))
            stt.return_value.transcribe.return_value = "What is an ESP32?"
            service = stack.enter_context(patch("scripts.voice_loop.AssistantService.process_message_stream", side_effect=process))
            stack.enter_context(patch("scripts.voice_loop.prepare_alsa_playback", side_effect=prepare))
            stack.enter_context(patch("scripts.voice_loop.time.sleep", side_effect=cooldown))
            stack.enter_context(patch("builtins.print"))
            self.assertEqual(voice_loop.main(["--hands-free"]), 130)
        self.assertEqual([c.kwargs["conversation_id"] for c in service.call_args_list], [None, 42])
        self.assertFalse(active["wake"])
        self.assertFalse(active["playback"])
        self.assertEqual(events, ["wake_start", "wake_released", "request_capture", "thinking", "tts", "audio_prepare",
                                  "playback_start", "playback_write", "playback_drained", "cooldown", "reset"] * 2
                         + ["wake_start", "wake_released"])
        states = [c.args[0].value for c in display.set_state.call_args_list]
        self.assertEqual(states, ["READY", "READY"] + ["LISTENING", "LISTENING", "THINKING", "SPEAKING", "ECO"] * 2)
        engine.Model.assert_called_once()
        self.assertIsNone(provider.model)
        tts.close.assert_called_once()
        display.close.assert_called_once()
