import io
from pathlib import Path
import subprocess
import tempfile
from threading import Event
from types import SimpleNamespace
from unittest.mock import call, MagicMock, patch
import wave

from django.test import SimpleTestCase, override_settings

from assistant.policy import AssistantResponsePolicy, ResponseMode
from assistant.tests_voice_stream import stream_events
from scripts.voice_loop import (
    VoiceLoopConfig,
    VoiceLoopError,
    _timed_call,
    chunk_text_for_speech,
    is_no_speech_transcription,
    limit_text_for_speech,
    normalize_audio,
    play_audio,
    prepare_speech_chunks,
    prepend_leading_silence,
    record_audio,
    record_audio_with_capture_vad,
    run_voice_cycle,
    sanitize_text_for_speech,
    speak_speech_chunks,
)


def _make_wav(frame_count=160, sample_rate=16000):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x01\x00" * frame_count)
    return output.getvalue()


def _pcm_chunk(amplitude=0, frame_count=320):
    sample = int(amplitude).to_bytes(2, "little", signed=True)
    return sample * frame_count


@override_settings(
    RAG_ENABLED=False,
    ONLINE_RETRIEVAL_ENABLED=False,
    DEVICE_CONTROL_ENABLED=False,
)
class VoiceLoopTests(SimpleTestCase):
    @override_settings(
        VOICE_INPUT_SOURCE="bluez_input.realme_buds",
        VOICE_RECORD_SECONDS="6.5",
        VOICE_INPUT_WARMUP_SECONDS="1.25",
        VOICE_LEADING_SILENCE_SECONDS="0.9",
        VOICE_TTS_CHUNK_CHARS="275",
        VOICE_TTS_MAX_TOTAL_CHARS="1800",
        VOICE_CAPTURE_VAD_ENABLED="true",
        VOICE_CAPTURE_VAD_START_THRESHOLD="0.03",
        VOICE_CAPTURE_VAD_SILENCE_SECONDS="1.1",
        VOICE_CAPTURE_VAD_MAX_SECONDS="12",
        VOICE_CAPTURE_VAD_START_TIMEOUT_SECONDS="6",
    )
    def test_config_reads_voice_loop_settings(self):
        config = VoiceLoopConfig.from_settings()

        self.assertEqual(config.input_source, "bluez_input.realme_buds")
        self.assertEqual(config.record_seconds, 6.5)
        self.assertEqual(config.input_warmup_seconds, 1.25)
        self.assertEqual(config.leading_silence_seconds, 0.9)
        self.assertEqual(config.tts_chunk_chars, 275)
        self.assertEqual(config.tts_max_total_chars, 1800)
        self.assertTrue(config.capture_vad_enabled)
        self.assertEqual(config.capture_vad_start_threshold, 0.03)
        self.assertEqual(config.capture_vad_silence_seconds, 1.1)
        self.assertEqual(config.capture_vad_max_seconds, 12)
        self.assertEqual(config.capture_vad_start_timeout_seconds, 6)

    def test_markdown_is_sanitized_for_speech(self):
        text = "## **Local AI** uses `edge inference` and __private data__."

        result = sanitize_text_for_speech(text)

        self.assertEqual(
            result,
            "Local AI uses edge inference and private data.",
        )

    def test_short_answer_remains_one_tts_chunk(self):
        chunks = prepare_speech_chunks("A short spoken answer.", chunk_chars=300)

        self.assertEqual(chunks, ["A short spoken answer."])

    def test_long_answer_splits_at_sentence_boundaries(self):
        text = (
            "The first sentence explains the premise. "
            "The second sentence adds useful context. "
            "The third sentence gives the conclusion."
        )

        chunks = chunk_text_for_speech(text, target_chars=55)

        self.assertEqual(
            chunks,
            [
                "The first sentence explains the premise.",
                "The second sentence adds useful context.",
                "The third sentence gives the conclusion.",
            ],
        )

    def test_all_sanitized_content_is_preserved_across_chunks(self):
        original = (
            "## **Overview**\n"
            "- The first section has `important details`.\n"
            "- The second section completes the answer."
        )
        sanitized = sanitize_text_for_speech(original)

        chunks = prepare_speech_chunks(original, chunk_chars=45, max_total_chars=0)

        self.assertEqual(" ".join(chunks), sanitized)
        spoken = " ".join(chunks)
        self.assertNotIn("**", spoken)
        self.assertNotIn("`", spoken)
        self.assertNotIn("- ", spoken)

    def test_long_sentence_chunks_do_not_split_words(self):
        text = "alpha extraordinaryword beta gamma delta"

        chunks = chunk_text_for_speech(text, target_chars=12)

        self.assertEqual(" ".join(chunks), text)
        self.assertIn("extraordinaryword", chunks)

    def test_unlimited_speech_mode_does_not_truncate(self):
        text = "First complete sentence. Second complete sentence. Third one."

        chunks = prepare_speech_chunks(text, chunk_chars=25, max_total_chars=0)

        self.assertEqual(" ".join(chunks), text)

    def test_explicit_safety_limit_uses_clean_sentence_boundary(self):
        text = (
            "This is the first complete sentence. "
            "This additional sentence is beyond the configured safety limit."
        )

        result = limit_text_for_speech(text, 50)

        self.assertEqual(result, "This is the first complete sentence.")
        self.assertLessEqual(len(result), 50)

    def test_timing_accumulates_across_repeated_tts_chunks(self):
        timings = {}

        with patch(
            "scripts.voice_loop.time.perf_counter",
            side_effect=[0.0, 1.5, 2.0, 4.25],
        ):
            _timed_call(timings, "tts", lambda: None)
            _timed_call(timings, "tts", lambda: None)

        self.assertEqual(timings["tts"], 3.75)

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
            with patch("scripts.voice_loop.time.sleep") as mock_sleep, patch(
                "builtins.print"
            ) as mock_print:
                record_audio(output_path, "bluez_input.test_source", 5, 1.25)

        command = mock_popen.call_args.args[0]
        self.assertIn("--device=bluez_input.test_source", command)
        mock_sleep.assert_called_once_with(1.25)
        mock_print.assert_called_once_with("Speak now...")
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
            record_audio(output_path, "bluez_input.test_source", 5, 0)

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
                record_audio(output_path, "bluez_input.test_source", 5, 0)

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
                record_audio(output_path, "bluez_input.test_source", 5, 0)

        process.terminate.assert_called_once_with()
        process.kill.assert_not_called()
        self.assertEqual(process.communicate.call_args_list[-1], call(timeout=5))

    @patch("scripts.voice_loop.subprocess.Popen")
    def test_record_audio_reaps_process_if_warmup_is_interrupted(self, mock_popen):
        process = MagicMock()
        process.communicate.return_value = (b"", b"")
        mock_popen.return_value = process

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "scripts.voice_loop.time.sleep",
            side_effect=KeyboardInterrupt,
        ):
            output_path = Path(temp_dir) / "recorded.wav"
            with self.assertRaises(KeyboardInterrupt):
                record_audio(output_path, "bluez_input.test_source", 5, 1)

        process.terminate.assert_called_once_with()
        process.kill.assert_not_called()
        process.communicate.assert_called_once_with(timeout=5)

    @patch("scripts.voice_loop.subprocess.Popen")
    def test_capture_vad_detects_speech_and_stops_after_silence(self, mock_popen):
        silence = _pcm_chunk()
        speech = _pcm_chunk(2000)
        process = MagicMock()
        process.stdout.read.side_effect = [
            silence,
            silence,
            speech,
            speech,
            silence,
            silence,
        ]
        process.communicate.return_value = (b"", b"")
        mock_popen.return_value = process

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "scripts.voice_loop.time.sleep"
        ), patch("builtins.print") as mock_print:
            output_path = Path(temp_dir) / "recorded.wav"
            record_audio_with_capture_vad(
                output_path,
                "bluez_input.test_source",
                warmup_seconds=1,
                start_threshold=0.02,
                silence_seconds=0.04,
                max_seconds=10,
                start_timeout_seconds=5,
            )
            with wave.open(str(output_path), "rb") as wav_file:
                self.assertEqual(wav_file.getnframes(), 6 * 320)

        command = mock_popen.call_args.args[0]
        self.assertIn("--raw", command)
        self.assertIn("--format=s16le", command)
        self.assertIn("--rate=16000", command)
        self.assertIn("--device=bluez_input.test_source", command)
        process.terminate.assert_called_once_with()
        mock_print.assert_any_call("Speak now...")
        mock_print.assert_any_call("Capture complete: 0.1s audio.")

    @patch("scripts.voice_loop.subprocess.Popen")
    def test_capture_vad_stops_at_maximum_recording_time(self, mock_popen):
        speech = _pcm_chunk(2000)
        process = MagicMock()
        process.stdout.read.side_effect = [speech, speech, speech, speech]
        process.communicate.return_value = (b"", b"")
        mock_popen.return_value = process

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "scripts.voice_loop.time.sleep"
        ), patch("builtins.print"):
            output_path = Path(temp_dir) / "recorded.wav"
            record_audio_with_capture_vad(
                output_path,
                "source",
                warmup_seconds=0,
                start_threshold=0.02,
                silence_seconds=1,
                max_seconds=0.06,
                start_timeout_seconds=1,
            )
            with wave.open(str(output_path), "rb") as wav_file:
                self.assertEqual(wav_file.getnframes(), 3 * 320)

        self.assertEqual(process.stdout.read.call_count, 3)
        process.terminate.assert_called_once_with()

    @patch("scripts.voice_loop.subprocess.Popen")
    def test_capture_vad_no_speech_timeout_cleans_up(self, mock_popen):
        process = MagicMock()
        process.stdout.read.side_effect = [_pcm_chunk(), _pcm_chunk(), _pcm_chunk()]
        process.communicate.return_value = (b"", b"")
        mock_popen.return_value = process

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "scripts.voice_loop.time.sleep"
        ):
            output_path = Path(temp_dir) / "recorded.wav"
            with self.assertRaisesRegex(VoiceLoopError, "no speech detected"):
                record_audio_with_capture_vad(
                    output_path,
                    "source",
                    warmup_seconds=0,
                    start_threshold=0.02,
                    silence_seconds=0.8,
                    max_seconds=10,
                    start_timeout_seconds=0.06,
                )

        self.assertFalse(output_path.exists())
        process.terminate.assert_called_once_with()
        process.communicate.assert_called_once_with(timeout=5)

    @patch("scripts.voice_loop.subprocess.Popen")
    def test_capture_vad_keyboard_interrupt_cleans_up(self, mock_popen):
        process = MagicMock()
        process.stdout.read.side_effect = KeyboardInterrupt
        process.communicate.return_value = (b"", b"")
        mock_popen.return_value = process

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "scripts.voice_loop.time.sleep"
        ):
            with self.assertRaises(KeyboardInterrupt):
                record_audio_with_capture_vad(
                    Path(temp_dir) / "recorded.wav",
                    "source",
                    warmup_seconds=0,
                    start_threshold=0.02,
                    silence_seconds=0.8,
                    max_seconds=10,
                    start_timeout_seconds=5,
                )

        process.terminate.assert_called_once_with()
        process.communicate.assert_called_once_with(timeout=5)

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

    @patch("scripts.voice_loop.prepend_leading_silence")
    @patch("scripts.voice_loop.play_audio")
    def test_multi_chunk_tts_synthesizes_ahead_without_playback_overlap(
        self,
        mock_play,
        mock_prepend,
    ):
        second_synthesis_started = Event()
        allow_second_synthesis = Event()
        playback_active = False
        played_audio = []
        provider = MagicMock()

        def synthesize(text):
            if text == "Second chunk.":
                second_synthesis_started.set()
                if not allow_second_synthesis.wait(timeout=1):
                    raise RuntimeError("test synchronization timed out")
            return text.encode()

        def play(path):
            nonlocal playback_active
            self.assertFalse(playback_active)
            playback_active = True
            try:
                if not played_audio:
                    self.assertTrue(second_synthesis_started.wait(timeout=1))
                    allow_second_synthesis.set()
                played_audio.append(path.read_bytes())
            finally:
                playback_active = False

        provider.synthesize.side_effect = synthesize
        mock_prepend.return_value = b"first-with-leading-silence"
        mock_play.side_effect = play

        with tempfile.TemporaryDirectory() as temp_dir:
            speak_speech_chunks(
                ["First chunk.", "Second chunk."],
                provider,
                Path(temp_dir),
                0.7,
                {},
            )

        self.assertEqual(
            provider.synthesize.call_args_list,
            [call("First chunk."), call("Second chunk.")],
        )
        self.assertEqual(
            played_audio,
            [b"first-with-leading-silence", b"Second chunk."],
        )
        mock_prepend.assert_called_once_with(b"First chunk.", 0.7)
        self.assertEqual(mock_play.call_count, 2)

    @patch("scripts.voice_loop.prepend_leading_silence", return_value=b"first")
    @patch("scripts.voice_loop.play_audio")
    def test_tts_pipeline_waits_for_worker_cleanup_on_playback_failure(
        self,
        mock_play,
        _mock_prepend,
    ):
        worker_finished = Event()
        provider = MagicMock()

        def synthesize(text):
            if text == "Second chunk.":
                worker_finished.set()
            return text.encode()

        provider.synthesize.side_effect = synthesize
        mock_play.side_effect = VoiceLoopError("sink failed")

        with tempfile.TemporaryDirectory() as temp_dir, self.assertRaisesRegex(
            VoiceLoopError,
            "Playback failed on chunk 1/2",
        ):
            speak_speech_chunks(
                ["First chunk.", "Second chunk."],
                provider,
                Path(temp_dir),
                0.7,
                {},
            )

        self.assertTrue(worker_finished.is_set())

    def test_no_speech_placeholders_are_normalized_and_rejected(self):
        for transcript in (
            "[BLANK_AUDIO]",
            "(blank_audio)",
            " blank   audio ",
            "[blank audio]",
            "[NO_SPEECH]",
            "[silence]",
        ):
            with self.subTest(transcript=transcript):
                self.assertTrue(is_no_speech_transcription(transcript))

        self.assertFalse(is_no_speech_transcription("Explain blank audio detection."))
        self.assertFalse(is_no_speech_transcription("Hello, can you hear me?"))

    @patch("scripts.voice_loop.AssistantService.process_message_stream")
    @patch("scripts.voice_loop.get_stt_provider")
    @patch("scripts.voice_loop.normalize_audio")
    @patch("scripts.voice_loop.record_audio")
    def test_blank_audio_placeholder_does_not_invoke_or_persist_through_service(
        self,
        _mock_record,
        _mock_normalize,
        mock_get_stt,
        mock_process_message,
    ):
        mock_get_stt.return_value.transcribe.return_value = "[BLANK_AUDIO]"

        with self.assertRaisesRegex(VoiceLoopError, "no speech was recognized"):
            run_voice_cycle(VoiceLoopConfig("source"))

        mock_process_message.assert_not_called()

    @patch("scripts.voice_loop.play_audio")
    @patch("scripts.voice_loop.prepend_leading_silence", return_value=b"final wav")
    @patch("scripts.voice_loop.normalize_audio")
    @patch("scripts.voice_loop.record_audio")
    @patch("scripts.voice_loop.record_audio_with_capture_vad")
    @patch("scripts.voice_loop.AssistantService.process_message_stream")
    @patch("scripts.voice_loop.get_tts_provider")
    @patch("scripts.voice_loop.get_stt_provider")
    def test_voice_cycle_reuses_existing_providers_and_service(
        self,
        mock_get_stt,
        mock_get_tts,
        mock_process_message,
        mock_capture_vad,
        mock_record,
        mock_normalize,
        mock_prepend,
        mock_play,
    ):
        mock_get_stt.return_value.transcribe.return_value = "What time is it?"
        mock_get_tts.return_value.synthesize.return_value = _make_wav()
        original_response = "**It is test time.**\n- Details are `ready`."
        mock_process_message.return_value = stream_events(original_response, conversation_id=42, engine="mock")
        config = VoiceLoopConfig("bluez_input.test", 5, 0.7, 500)
        self.assertFalse(config.capture_vad_enabled)

        with patch("builtins.print") as mock_print:
            conversation_id = run_voice_cycle(config, conversation_id=12)

        self.assertEqual(conversation_id, 42)
        mock_record.assert_called_once()
        mock_capture_vad.assert_not_called()
        self.assertEqual(mock_record.call_args.args[2:], (5, 1.0))
        mock_normalize.assert_called_once()
        mock_get_stt.return_value.transcribe.assert_called_once()
        mock_process_message.assert_called_once_with(
            "What time is it?",
            conversation_id=12,
            response_plan=AssistantResponsePolicy.plan_voice_response(
                "What time is it?"
            ),
        )
        response_plan = mock_process_message.call_args.kwargs["response_plan"]
        self.assertEqual(response_plan.mode, ResponseMode.NORMAL)
        self.assertIn("concise but sufficient", response_plan.system_instruction)
        self.assertIn("Do not use Markdown", response_plan.system_instruction)
        self.assertEqual(mock_get_tts.return_value.synthesize.call_args_list,
                         [call("It is test time."), call("Details are ready.")])
        self.assertEqual(mock_process_message.return_value[-1]["response"], original_response)
        mock_print.assert_any_call(f"Assistant: {original_response}")
        summary = next(args[0] for args, _ in mock_print.call_args_list if args and str(args[0]).startswith("Timing:"))
        for metric in ("record", "normalize", "stt", "ai", "ai_ttft", "first_speech", "ai_total", "tts", "playback", "total"):
            self.assertIn(metric + "=", summary)
        mock_prepend.assert_called_once_with(_make_wav(), 0.7)
        self.assertEqual(mock_play.call_count, 2)
        self.assertFalse(Path(mock_play.call_args.args[0]).exists())

    @patch("scripts.voice_loop.play_audio")
    @patch("scripts.voice_loop.prepend_leading_silence")
    @patch("scripts.voice_loop.normalize_audio")
    @patch("scripts.voice_loop.record_audio")
    @patch("scripts.voice_loop.AssistantService.process_message_stream")
    @patch("scripts.voice_loop.get_tts_provider")
    @patch("scripts.voice_loop.get_stt_provider")
    def test_voice_cycle_synthesizes_and_plays_all_chunks_sequentially(
        self,
        mock_get_stt,
        mock_get_tts,
        mock_process_message,
        _mock_record,
        _mock_normalize,
        mock_prepend,
        mock_play,
    ):
        mock_get_stt.return_value.transcribe.return_value = "Explain fully."
        original_response = (
            "**First sentence contains useful details.** "
            "Second sentence provides more context. "
            "Third sentence completes the explanation."
        )
        mock_process_message.return_value = stream_events(original_response, conversation_id=7)
        wav_bytes = _make_wav()
        mock_get_tts.return_value.synthesize.return_value = wav_bytes
        mock_prepend.return_value = b"first chunk with silence"
        config = VoiceLoopConfig(
            input_source="source",
            tts_chunk_chars=50,
            tts_max_total_chars=0,
        )

        with patch("builtins.print"):
            result = run_voice_cycle(config)

        self.assertEqual(result, 7)
        self.assertEqual(
            mock_get_tts.return_value.synthesize.call_args_list,
            [
                call("First sentence contains useful details."),
                call("Second sentence provides more context."),
                call("Third sentence completes the explanation."),
            ],
        )
        self.assertEqual(mock_play.call_count, 3)
        mock_prepend.assert_called_once_with(wav_bytes, 0.7)
        self.assertEqual(mock_process_message.return_value[-1]["response"], original_response)

    @patch("scripts.voice_loop.play_audio")
    @patch("scripts.voice_loop.prepend_leading_silence", return_value=b"first")
    @patch("scripts.voice_loop.normalize_audio")
    @patch("scripts.voice_loop.record_audio")
    @patch("scripts.voice_loop.AssistantService.process_message_stream")
    @patch("scripts.voice_loop.get_tts_provider")
    @patch("scripts.voice_loop.get_stt_provider")
    def test_voice_cycle_reports_later_chunk_failure(
        self,
        mock_get_stt,
        mock_get_tts,
        mock_process_message,
        _mock_record,
        _mock_normalize,
        _mock_prepend,
        mock_play,
    ):
        mock_get_stt.return_value.transcribe.return_value = "Explain fully."
        mock_process_message.return_value = stream_events(
            "First complete sentence. Second complete sentence.", conversation_id=7)
        mock_get_tts.return_value.synthesize.side_effect = [
            _make_wav(),
            RuntimeError("Piper stopped"),
        ]
        config = VoiceLoopConfig(input_source="source", tts_chunk_chars=26)

        with patch("builtins.print"), self.assertRaisesRegex(
            VoiceLoopError,
            "TTS failed on streaming chunk 2",
        ):
            run_voice_cycle(config)

        mock_play.assert_called_once()

    @patch("scripts.voice_loop.get_stt_provider")
    @patch("scripts.voice_loop.normalize_audio")
    @patch("scripts.voice_loop.record_audio")
    @patch("scripts.voice_loop.record_audio_with_capture_vad")
    def test_voice_cycle_uses_capture_vad_when_enabled(
        self,
        mock_capture_vad,
        mock_record,
        _mock_normalize,
        mock_get_stt,
    ):
        mock_get_stt.return_value.transcribe.return_value = ""
        config = VoiceLoopConfig(
            input_source="bluez_input.test",
            input_warmup_seconds=1.25,
            capture_vad_enabled=True,
            capture_vad_start_threshold=0.03,
            capture_vad_silence_seconds=0.9,
            capture_vad_max_seconds=12,
            capture_vad_start_timeout_seconds=6,
        )

        with self.assertRaisesRegex(VoiceLoopError, "no speech was recognized"):
            run_voice_cycle(config)

        mock_record.assert_not_called()
        self.assertEqual(
            mock_capture_vad.call_args.args[1:],
            ("bluez_input.test", 1.25, 0.03, 0.9, 12, 6),
        )

    @patch("scripts.voice_loop.AssistantService.process_message_stream")
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
        mock_process_message.return_value = [{"type": "error", "detail": "AI engine unavailable."}]

        with self.assertRaisesRegex(VoiceLoopError, "AI response failed"):
            run_voice_cycle(VoiceLoopConfig("bluez_input.test"))

    def test_voice_loop_config_requires_input_source(self):
        with self.assertRaisesRegex(VoiceLoopError, "VOICE_INPUT_SOURCE"):
            VoiceLoopConfig("", 5, 0.7).validate()
