import io
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import call, MagicMock, patch
import wave

from django.test import SimpleTestCase, override_settings

from scripts.voice_loop import (
    VOICE_SYSTEM_INSTRUCTION,
    VoiceLoopConfig,
    VoiceLoopError,
    normalize_audio,
    play_audio,
    prepare_text_for_speech,
    prepend_leading_silence,
    record_audio,
    run_voice_cycle,
    sanitize_text_for_speech,
    truncate_text_for_speech,
)


def _make_wav(frame_count=160, sample_rate=16000):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x01\x00" * frame_count)
    return output.getvalue()


class VoiceLoopTests(SimpleTestCase):
    @override_settings(
        VOICE_INPUT_SOURCE="bluez_input.realme_buds",
        VOICE_RECORD_SECONDS="6.5",
        VOICE_LEADING_SILENCE_SECONDS="0.9",
        VOICE_MAX_SPEECH_CHARS="450",
    )
    def test_config_reads_voice_loop_settings(self):
        config = VoiceLoopConfig.from_settings()

        self.assertEqual(config.input_source, "bluez_input.realme_buds")
        self.assertEqual(config.record_seconds, 6.5)
        self.assertEqual(config.leading_silence_seconds, 0.9)
        self.assertEqual(config.max_speech_chars, 450)

    def test_markdown_is_sanitized_for_speech(self):
        text = "## **Local AI** uses `edge inference` and __private data__."

        result = sanitize_text_for_speech(text)

        self.assertEqual(
            result,
            "Local AI uses edge inference and private data.",
        )

    def test_markdown_bullets_are_removed_for_speech(self):
        text = "* First item\n- Second item\n• Third item"

        result = prepare_text_for_speech(text)

        self.assertEqual(result, "First item Second item Third item")

    def test_speech_text_truncates_at_sentence_boundary(self):
        text = (
            "This is the first complete sentence. "
            "This additional sentence extends beyond the speech limit."
        )

        result = truncate_text_for_speech(text, 50)

        self.assertEqual(result, "This is the first complete sentence.")
        self.assertLessEqual(len(result), 50)

    def test_prepend_leading_silence(self):
        result = prepend_leading_silence(_make_wav(), 0.7)

        with wave.open(io.BytesIO(result), "rb") as wav_file:
            self.assertEqual(wav_file.getframerate(), 16000)
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getnframes(), 160 + 11200)
            leading_sample = wav_file.readframes(1)
        self.assertEqual(leading_sample, b"\x00\x00")

    @patch("scripts.voice_loop.subprocess.Popen")
    def test_record_audio_uses_configured_source_and_duration(self, mock_popen):
        process = MagicMock()
        process.communicate.side_effect = [
            subprocess.TimeoutExpired("parecord", 5),
            (b"", b""),
        ]
        mock_popen.return_value = process

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "recorded.wav"
            output_path.write_bytes(b"recorded audio")
            record_audio(output_path, "bluez_input.test_source", 5)

        command = mock_popen.call_args.args[0]
        self.assertIn("--device=bluez_input.test_source", command)
        self.assertEqual(
            process.communicate.call_args_list,
            [call(timeout=5), call(timeout=5)],
        )
        process.terminate.assert_called_once_with()
        process.kill.assert_not_called()

    @patch("scripts.voice_loop.subprocess.Popen")
    def test_record_audio_kills_process_if_terminate_does_not_stop_it(self, mock_popen):
        process = MagicMock()
        process.communicate.side_effect = [
            subprocess.TimeoutExpired("parecord", 5),
            subprocess.TimeoutExpired("parecord", 5),
            (b"", b""),
        ]
        mock_popen.return_value = process

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "recorded.wav"
            output_path.write_bytes(b"recorded audio")
            record_audio(output_path, "bluez_input.test_source", 5)

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.communicate.call_args_list[-1], call())

    @patch("scripts.voice_loop.subprocess.Popen")
    def test_record_audio_reaps_process_on_keyboard_interrupt(self, mock_popen):
        process = MagicMock()
        process.communicate.side_effect = [
            KeyboardInterrupt,
            subprocess.TimeoutExpired("parecord", 5),
            (b"", b""),
        ]
        mock_popen.return_value = process

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "recorded.wav"
            with self.assertRaises(KeyboardInterrupt):
                record_audio(output_path, "bluez_input.test_source", 5)

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.communicate.call_args_list[-1], call())

    @patch("scripts.voice_loop.subprocess.Popen")
    def test_record_audio_reaps_process_on_communication_failure(self, mock_popen):
        process = MagicMock()
        process.communicate.side_effect = [
            OSError("pipe failure"),
            (b"", b""),
        ]
        mock_popen.return_value = process

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "recorded.wav"
            with self.assertRaisesRegex(VoiceLoopError, "Recording failed"):
                record_audio(output_path, "bluez_input.test_source", 5)

        process.terminate.assert_called_once_with()
        process.kill.assert_not_called()
        self.assertEqual(process.communicate.call_args_list[-1], call(timeout=5))

    @patch("scripts.voice_loop.subprocess.run")
    def test_normalize_audio_runs_ffmpeg_for_mono_16khz_pcm(self, mock_run):
        def complete_conversion(command, **_kwargs):
            Path(command[-1]).write_bytes(b"normalized wav")
            return SimpleNamespace(returncode=0, stderr=b"")

        mock_run.side_effect = complete_conversion
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "recorded.wav"
            output_path = Path(temp_dir) / "normalized.wav"
            input_path.write_bytes(b"recorded wav")

            normalize_audio(input_path, output_path)

        command = mock_run.call_args.args[0]
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("16000", command)
        self.assertIn("pcm_s16le", command)

    @patch("scripts.voice_loop.subprocess.run")
    def test_playback_failure_is_reported(self, mock_run):
        mock_run.return_value = SimpleNamespace(
            returncode=1,
            stderr=b"No default sink",
        )

        with self.assertRaisesRegex(VoiceLoopError, "Playback failed"):
            play_audio(Path("playback.wav"))

    @patch("scripts.voice_loop.play_audio")
    @patch("scripts.voice_loop.prepend_leading_silence", return_value=b"final wav")
    @patch("scripts.voice_loop.normalize_audio")
    @patch("scripts.voice_loop.record_audio")
    @patch("scripts.voice_loop.AssistantService.process_message")
    @patch("scripts.voice_loop.get_tts_provider")
    @patch("scripts.voice_loop.get_stt_provider")
    def test_voice_cycle_reuses_existing_providers_and_service(
        self,
        mock_get_stt,
        mock_get_tts,
        mock_process_message,
        mock_record,
        mock_normalize,
        mock_prepend,
        mock_play,
    ):
        mock_get_stt.return_value.transcribe.return_value = "What time is it?"
        mock_get_tts.return_value.synthesize.return_value = _make_wav()
        conversation = SimpleNamespace(id=42)
        original_response = "**It is test time.**\n- Details are `ready`."
        mock_process_message.return_value = (
            conversation,
            original_response,
            {"engine": "mock"},
            None,
        )
        config = VoiceLoopConfig("bluez_input.test", 5, 0.7, 500)

        clock_values = [
            0.0,
            0.0, 5.0,
            5.0, 6.0,
            6.0, 9.4,
            9.4, 17.5,
            17.5, 19.7,
            19.7, 23.7,
            23.9,
        ]
        with patch(
            "scripts.voice_loop.time.perf_counter",
            side_effect=clock_values,
        ), patch("builtins.print") as mock_print:
            conversation_id = run_voice_cycle(config, conversation_id=12)

        self.assertEqual(conversation_id, 42)
        mock_record.assert_called_once()
        mock_normalize.assert_called_once()
        mock_get_stt.return_value.transcribe.assert_called_once()
        mock_process_message.assert_called_once_with(
            "What time is it?",
            conversation_id=12,
            system_instruction=VOICE_SYSTEM_INSTRUCTION,
        )
        self.assertIn("2–3 short sentences", VOICE_SYSTEM_INSTRUCTION)
        self.assertIn("Do not use Markdown", VOICE_SYSTEM_INSTRUCTION)
        self.assertIn("bullet lists", VOICE_SYSTEM_INSTRUCTION)
        self.assertIn("headings", VOICE_SYSTEM_INSTRUCTION)
        self.assertIn("conversational spoken language", VOICE_SYSTEM_INSTRUCTION)
        self.assertIn("explicitly asks for detail", VOICE_SYSTEM_INSTRUCTION)
        mock_get_tts.return_value.synthesize.assert_called_once_with(
            "It is test time. Details are ready."
        )
        self.assertEqual(mock_process_message.return_value[1], original_response)
        mock_print.assert_any_call(f"Assistant: {original_response}")
        mock_print.assert_any_call(
            "Timing: record=5.0s normalize=1.0s stt=3.4s ai=8.1s "
            "tts=2.2s playback=4.0s total=23.9s"
        )
        mock_prepend.assert_called_once_with(_make_wav(), 0.7)
        mock_play.assert_called_once()
        self.assertFalse(Path(mock_play.call_args.args[0]).exists())

    @patch("scripts.voice_loop.AssistantService.process_message")
    @patch("scripts.voice_loop.get_stt_provider")
    @patch("scripts.voice_loop.normalize_audio")
    @patch("scripts.voice_loop.record_audio")
    def test_voice_cycle_reports_ai_failure(
        self,
        mock_record,
        mock_normalize,
        mock_get_stt,
        mock_process_message,
    ):
        mock_get_stt.return_value.transcribe.return_value = "Hello"
        mock_process_message.return_value = (
            SimpleNamespace(id=1),
            None,
            None,
            "AI engine unavailable.",
        )

        with self.assertRaisesRegex(VoiceLoopError, "AI response failed"):
            run_voice_cycle(VoiceLoopConfig("bluez_input.test"))

    def test_voice_loop_config_requires_input_source(self):
        with self.assertRaisesRegex(VoiceLoopError, "VOICE_INPUT_SOURCE"):
            VoiceLoopConfig("", 5, 0.7).validate()
