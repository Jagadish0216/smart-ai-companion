"""Pi I2S audio coverage without sound hardware or external audio binaries."""

import io
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch
import wave

from django.test import SimpleTestCase, override_settings

from scripts import voice_loop


DEVICE = "hw:CARD=sndrpigooglevoi,DEV=0"


def pcm_chunk(left=0, right=0):
    return struct.pack("<ii", left, right) * 960  # 20 ms, native stereo S32_LE


def piper_wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\x01\x00" * 2205)
    return output.getvalue()


class AlsaConfigTests(SimpleTestCase):
    @override_settings(
        VOICE_AUDIO_BACKEND="alsa", VOICE_INPUT_SOURCE="",
        VOICE_ALSA_CAPTURE_DEVICE=DEVICE, VOICE_ALSA_PLAYBACK_DEVICE=DEVICE,
        VOICE_PLAYBACK_GAIN="4.0", VOICE_LEADING_SILENCE_SECONDS="0.5",
    )
    def test_pi_config_uses_named_devices_without_pulse_source(self):
        config = voice_loop.VoiceLoopConfig.from_settings()
        self.assertEqual(config.audio_backend, "alsa")
        self.assertEqual(config.alsa_capture_device, DEVICE)
        self.assertEqual(config.alsa_playback_device, DEVICE)
        self.assertEqual(config.playback_gain, 4.0)
        self.assertEqual(config.leading_silence_seconds, 0.5)

    def test_default_config_preserves_pulse_and_existing_silence(self):
        config = voice_loop.VoiceLoopConfig("pulse-source")
        config.validate()
        self.assertEqual(config.audio_backend, "pulse")
        self.assertEqual(config.leading_silence_seconds, 0.7)

    def test_invalid_backend_gain_devices_and_unbounded_duration_are_rejected(self):
        for options in (
            {"audio_backend": "unknown"}, {"playback_gain": 0},
            {"playback_gain": float("nan")}, {"playback_gain": float("inf")},
            {"audio_backend": "alsa", "alsa_capture_device": ""},
            {"audio_backend": "alsa", "alsa_playback_device": ""},
            {"capture_vad_max_seconds": float("inf")},
        ):
            with self.subTest(options=options), self.assertRaises(voice_loop.VoiceLoopError):
                voice_loop.VoiceLoopConfig("source", **options).validate()


class AlsaCaptureTests(SimpleTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name) / "recorded.wav"
        self.process = MagicMock()
        self.process.returncode = 0
        self.process.communicate.return_value = (b"", b"")
        for name in ("subprocess.Popen", "time.sleep", "Timer"):
            patcher = patch("scripts.voice_loop." + name)
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            setattr(self, name.rsplit(".", 1)[-1], mock)
        self.Popen.return_value = self.process

    def capture_vad(self, **options):
        kwargs = dict(warmup_seconds=0.25, start_threshold=0.02,
                      silence_seconds=0.04, max_seconds=1, start_timeout_seconds=0.1)
        kwargs.update(options)
        voice_loop.record_audio_with_capture_vad(self.output, DEVICE, backend="alsa", **kwargs)

    def test_fixed_capture_command_and_bounded_warmup_plus_speech_window(self):
        self.output.write_bytes(b"mock audio")
        self.process.communicate.side_effect = [subprocess.TimeoutExpired("arecord", 5), (b"", b"")]
        voice_loop.record_audio_alsa(self.output, DEVICE, 5, 1)
        self.Popen.assert_called_once_with(
            ["arecord", "-D", DEVICE, "-c", "2", "-r", "48000", "-f", "S32_LE",
             "-t", "wav", str(self.output)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.sleep.assert_called_once_with(1)
        self.process.terminate.assert_called_once()
        self.assertEqual(self.process.communicate.call_args_list, [call(timeout=5), call(timeout=5)])

    def test_fixed_capture_kills_and_reaps_if_terminate_is_insufficient(self):
        self.output.write_bytes(b"mock audio")
        self.process.communicate.side_effect = [
            subprocess.TimeoutExpired("arecord", 5), subprocess.TimeoutExpired("arecord", 5), (b"", b"")]
        voice_loop.record_audio_alsa(self.output, DEVICE, 5, 0)
        self.process.kill.assert_called_once()
        self.assertEqual(self.process.communicate.call_args_list[-1], call())

    def test_fixed_capture_interrupt_cleans_up(self):
        self.sleep.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            voice_loop.record_audio_alsa(self.output, DEVICE, 5)
        self.process.terminate.assert_called_once()
        self.process.communicate.assert_called_once_with(timeout=5)

    def test_fixed_capture_missing_binary_is_useful_error(self):
        self.Popen.side_effect = FileNotFoundError
        with self.assertRaisesRegex(voice_loop.VoiceLoopError, "arecord was not found"):
            voice_loop.record_audio_alsa(self.output, DEVICE, 5)

    def test_fixed_capture_nonzero_exit_is_not_success(self):
        self.process.returncode = 1
        self.process.communicate.return_value = (b"", b"device unavailable")
        with self.assertRaisesRegex(voice_loop.VoiceLoopError, "arecord exit 1.*device unavailable"):
            voice_loop.record_audio_alsa(self.output, DEVICE, 5)

    def test_fixed_capture_missing_output_is_error(self):
        with self.assertRaisesRegex(voice_loop.VoiceLoopError, "arecord produced no audio"):
            voice_loop.record_audio_alsa(self.output, DEVICE, 5)

    def test_rms_uses_only_signed_32_bit_left_channel(self):
        self.assertAlmostEqual(voice_loop._normalized_alsa_left_rms(pcm_chunk(2**29, 0)), 0.25)
        self.assertAlmostEqual(voice_loop._normalized_alsa_left_rms(pcm_chunk(-2**29, 2**30)), 0.25)
        self.assertEqual(voice_loop._normalized_alsa_left_rms(pcm_chunk(0, 2**30)), 0)
        self.assertEqual(voice_loop._normalized_alsa_left_rms(b""), 0)

    def test_vad_native_command_preroll_silence_stop_and_wav_format(self):
        chunks = [pcm_chunk(), pcm_chunk(), pcm_chunk(2**28), pcm_chunk(2**28), pcm_chunk(), pcm_chunk()]
        self.process.stdout.read.side_effect = chunks
        with patch("scripts.voice_loop.subprocess.run") as external_conversion:
            self.capture_vad()
        external_conversion.assert_not_called()
        self.Popen.assert_called_once_with(
            ["arecord", "-D", DEVICE, "-c", "2", "-r", "48000", "-f", "S32_LE", "-t", "raw"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.process.stdout.read.assert_called_with(7680)
        self.process.terminate.assert_called_once()
        self.process.communicate.assert_called_once_with(timeout=5)
        with wave.open(str(self.output), "rb") as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (48000, 2, 4))
            self.assertEqual(wav.getnframes(), 6 * 960)
            self.assertEqual(wav.readframes(wav.getnframes()), b"".join(chunks))
        self.Timer.return_value.start.assert_called_once()
        self.Timer.return_value.cancel.assert_called_once()

    def test_vad_preroll_is_bounded_to_300_ms(self):
        self.process.stdout.read.side_effect = [pcm_chunk()] * 20 + [pcm_chunk(2**28)] + [pcm_chunk()] * 2
        self.capture_vad(start_timeout_seconds=1)
        with wave.open(str(self.output), "rb") as wav:
            self.assertEqual(wav.getnframes(), (15 + 2) * 960)

    def test_vad_ignores_right_channel_and_cleans_up_on_start_timeout(self):
        self.process.stdout.read.side_effect = [pcm_chunk(0, 2**30)] * 5
        with self.assertRaisesRegex(voice_loop.VoiceLoopError, "no speech.*start timeout"):
            self.capture_vad()
        self.assertFalse(self.output.exists())
        self.process.terminate.assert_called_once()
        self.process.communicate.assert_called_once_with(timeout=5)
        self.Timer.return_value.cancel.assert_called_once()

    def test_vad_stops_at_max_capture_duration(self):
        self.process.stdout.read.side_effect = [pcm_chunk(2**28)] * 3
        self.capture_vad(max_seconds=0.06)
        self.assertEqual(self.process.stdout.read.call_count, 3)
        with wave.open(str(self.output), "rb") as wav:
            self.assertEqual(wav.getnframes(), 3 * 960)

    def test_vad_stalled_pipe_watchdog_kills_then_main_thread_reaps(self):
        def stalled_read(_size):
            self.Timer.call_args.args[1]()
            return b""
        self.process.stdout.read.side_effect = stalled_read
        with self.assertRaisesRegex(voice_loop.VoiceLoopError, "arecord.*timed out"):
            self.capture_vad()
        self.assertAlmostEqual(self.Timer.call_args.args[0], 0.25 + 0.1 + 1 + 1)
        self.process.kill.assert_called_once()
        self.process.communicate.assert_called_once_with(timeout=5)
        self.Timer.return_value.cancel.assert_called_once()

    def test_vad_interrupt_and_read_error_both_clean_up(self):
        for error in (KeyboardInterrupt(), OSError("pipe failed")):
            with self.subTest(error=type(error).__name__):
                self.process.reset_mock()
                self.process.stdout.read.side_effect = error
                expected = KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else voice_loop.VoiceLoopError
                with self.assertRaises(expected):
                    self.capture_vad()
                self.process.terminate.assert_called_once()
                self.process.communicate.assert_called_once_with(timeout=5)


class AlsaConversionTests(SimpleTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.input = Path(directory.name) / "input.wav"
        self.output = Path(directory.name) / "output.wav"
        self.output.write_bytes(b"mock converted WAV")
        patcher = patch("scripts.voice_loop.subprocess.run")
        self.run = patcher.start()
        self.addCleanup(patcher.stop)
        self.run.return_value = SimpleNamespace(returncode=0, stderr=b"")

    def test_normalization_selects_left_then_whisper_format(self):
        voice_loop.normalize_audio(self.input, self.output, left_channel=True)
        self.run.assert_called_once_with(
            ["ffmpeg", "-y", "-i", str(self.input), "-af", "pan=mono|c0=FL",
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(self.output)],
            capture_output=True, timeout=60)

    def test_pulse_normalization_command_is_unchanged(self):
        voice_loop.normalize_audio(self.input, self.output)
        self.run.assert_called_once_with(
            ["ffmpeg", "-y", "-i", str(self.input), "-ar", "16000", "-ac", "1",
             "-c:a", "pcm_s16le", str(self.output)], capture_output=True, timeout=60)

    def test_playback_conversion_uses_gain_limiter_and_native_format(self):
        voice_loop.prepare_alsa_playback(self.input, self.output, 4.0)
        self.run.assert_called_once_with(
            ["ffmpeg", "-y", "-i", str(self.input), "-af", "volume=4.0,alimiter=limit=0.98:level=false",
             "-ar", "48000", "-ac", "2", "-c:a", "pcm_s32le", str(self.output)],
            capture_output=True, timeout=60)

    def test_playback_gain_is_configurable(self):
        voice_loop.prepare_alsa_playback(self.input, self.output, 2.5)
        self.assertIn("volume=2.5,alimiter=limit=0.98:level=false", self.run.call_args.args[0])

    def test_aplay_uses_named_device(self):
        voice_loop.play_audio_alsa(self.input, DEVICE)
        self.run.assert_called_once_with(["aplay", "-D", DEVICE, str(self.input)],
                                         capture_output=True, timeout=300)

    def test_subprocess_failures_become_voice_errors(self):
        for operation in (
            lambda: voice_loop.normalize_audio(self.input, self.output, left_channel=True),
            lambda: voice_loop.prepare_alsa_playback(self.input, self.output, 4),
            lambda: voice_loop.play_audio_alsa(self.input, DEVICE),
        ):
            for error in (FileNotFoundError(), subprocess.TimeoutExpired("audio", 60), OSError("audio device lost")):
                with self.subTest(operation=operation, error=type(error).__name__):
                    self.run.side_effect = error
                    with self.assertRaises(voice_loop.VoiceLoopError):
                        operation()
            self.run.side_effect = None
            self.run.return_value = SimpleNamespace(returncode=1, stderr=b"invalid audio format")
            with self.assertRaisesRegex(voice_loop.VoiceLoopError, "exit 1.*invalid audio format"):
                operation()

    def test_conversion_missing_output_is_not_success(self):
        self.output.unlink()
        with self.assertRaisesRegex(voice_loop.VoiceLoopError, "produced no WAV"):
            voice_loop.prepare_alsa_playback(self.input, self.output, 4)


@override_settings(RAG_ENABLED=False, ONLINE_RETRIEVAL_ENABLED=False, DEVICE_CONTROL_ENABLED=False)
class AlsaLifecycleTests(SimpleTestCase):
    def setUp(self):
        self.events = []
        self.display = MagicMock()
        self.display.set_state.side_effect = lambda state: self.events.append(state.value)
        self.mocks = {}
        for name in ("record_audio", "record_audio_alsa", "record_audio_with_capture_vad",
                     "normalize_audio", "get_stt_provider", "get_tts_provider",
                     "AssistantService.process_message_stream", "prepare_alsa_playback", "play_audio_alsa", "play_audio"):
            patcher = patch("scripts.voice_loop." + name)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.mocks["get_stt_provider"].return_value.transcribe.return_value = "Explain local AI."
        self.mocks["get_tts_provider"].return_value.synthesize.return_value = piper_wav()
        text = "First sentence. Second sentence."
        self.mocks["AssistantService.process_message_stream"].return_value = [
            {"type": "start", "conversation_id": 42, "direct": False}, {"type": "delta", "text": text},
            {"type": "done", "conversation_id": 42, "response": text, "resource_profile": "BALANCED"}]
        for operation, event in (("record_audio_alsa", "capture"), ("normalize_audio", "normalize"),
                                 ("prepare_alsa_playback", "convert"), ("play_audio_alsa", "play")):
            self.mocks[operation].side_effect = lambda *args, _event=event, **kwargs: self.events.append(_event)
        self.config = voice_loop.VoiceLoopConfig("", audio_backend="alsa", leading_silence_seconds=0.5,
                                                tts_chunk_chars=17)

    def cycle(self, config=None):
        return voice_loop.run_voice_cycle(config or self.config, conversation_id=12, display=self.display)

    def test_cycle_selects_alsa_and_speaking_once_after_conversion_before_playback(self):
        self.assertEqual(self.cycle(), 42)
        self.assertEqual(self.events[:4], ["LISTENING", "capture", "normalize", "THINKING"])
        self.assertEqual([event for event in self.events if event not in {"convert", "play"}],
                         ["LISTENING", "capture", "normalize", "THINKING", "SPEAKING", "READY"])
        self.assertLess(self.events.index("convert"), self.events.index("SPEAKING"))
        without_conversion = [event for event in self.events if event != "convert"]
        self.assertEqual(without_conversion[without_conversion.index("SPEAKING") + 1], "play")
        self.assertEqual(self.events.count("play"), 2)
        self.assertEqual(self.events[-1], "READY")
        self.mocks["record_audio"].assert_not_called()
        self.mocks["play_audio"].assert_not_called()
        self.assertEqual(self.mocks["record_audio_alsa"].call_args.args[1:], (DEVICE, 5, 1))
        self.assertEqual(self.mocks["normalize_audio"].call_args.kwargs, {"left_channel": True})
        for conversion, playback in zip(self.mocks["prepare_alsa_playback"].call_args_list,
                                         self.mocks["play_audio_alsa"].call_args_list):
            self.assertEqual(conversion.args[2], 4)
            self.assertEqual(playback.args, (conversion.args[1], DEVICE))
        self.assertEqual(self.mocks["AssistantService.process_message_stream"].call_args.kwargs["conversation_id"], 12)

    def test_vad_cycle_selects_native_left_channel_backend(self):
        config = voice_loop.VoiceLoopConfig("", audio_backend="alsa", capture_vad_enabled=True)
        self.cycle(config)
        self.mocks["record_audio_alsa"].assert_not_called()
        capture = self.mocks["record_audio_with_capture_vad"].call_args
        self.assertEqual(capture.args[1], DEVICE)
        self.assertEqual(capture.kwargs, {"backend": "alsa"})

    def test_first_chunk_has_half_second_digital_silence_only(self):
        source = piper_wav()
        chunks = []
        events = []
        def convert(input_path, output_path, gain):
            chunks.append(input_path.read_bytes())
            events.append("convert")
        self.mocks["prepare_alsa_playback"].side_effect = convert
        self.mocks["play_audio_alsa"].side_effect = lambda *args: events.append("play")
        with tempfile.TemporaryDirectory() as directory, patch("scripts.voice_loop.time.sleep") as sleep:
            voice_loop.speak_speech_chunks(
                ["First.", "Second."], self.mocks["get_tts_provider"].return_value, Path(directory), 0.5, {},
                on_playback_start=lambda: events.append("SPEAKING"), audio_config=self.config)
        sleep.assert_not_called()
        self.assertEqual(events, ["convert", "SPEAKING", "play", "convert", "play"])
        self.assertEqual(chunks[1], source)
        with wave.open(io.BytesIO(chunks[0]), "rb") as wav:
            silence_frames = round(22050 * 0.5)
            self.assertEqual(wav.getnframes(), silence_frames + 2205)
            self.assertEqual(wav.readframes(silence_frames), b"\x00" * (silence_frames * 2))
            self.assertEqual(wav.readframes(2205), b"\x01\x00" * 2205)

    def test_alsa_failures_set_error_and_next_interaction_listens_again(self):
        for name in ("record_audio_alsa", "normalize_audio", "prepare_alsa_playback", "play_audio_alsa"):
            with self.subTest(operation=name):
                operation = self.mocks[name]
                original = operation.side_effect
                self.events.clear()
                operation.side_effect = voice_loop.VoiceLoopError("audio unavailable")
                with self.assertRaises(voice_loop.VoiceLoopError):
                    self.cycle()
                self.assertEqual(self.events[-1], "ERROR")
                if name == "prepare_alsa_playback":
                    self.assertNotIn("SPEAKING", self.events)
                operation.side_effect = original
                self.events.clear()
                self.cycle()
                self.assertEqual(self.events[0], "LISTENING")
                self.assertEqual(self.events[-1], "READY")

    def test_resource_idle_states_are_preserved(self):
        for profile in ("ECO", "PROTECTIVE"):
            with self.subTest(profile=profile):
                self.mocks["AssistantService.process_message_stream"].return_value = [
                    {"type": "start", "conversation_id": 42, "direct": False},
                    {"type": "delta", "text": "A short answer."},
                    {"type": "done", "conversation_id": 42, "response": "A short answer.", "resource_profile": profile}]
                self.cycle()
                self.assertEqual(self.events[-1], profile)
