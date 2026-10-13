import io
from pathlib import Path
import subprocess
import tempfile
from threading import Event, enumerate as enumerate_threads
from types import SimpleNamespace
from unittest.mock import call, MagicMock, patch
import wave

from django.test import SimpleTestCase, override_settings

from assistant.policy import AssistantResponsePolicy, ResponseMode
from assistant.tests_voice_stream import FIRST_SENTENCE, SECOND_SENTENCE, THIRD_SENTENCE, stream_events
from scripts import make_voice_test_audio, voice_loop
from assistant.voice.tts import PiperProvider, TTSError
from assistant.tests_piper_support import fake_piper_worker
import multiprocessing
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
                adaptive=False,  # Legacy single-threshold/pre-roll compatibility.
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
                adaptive=False,
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
        self.assertIn("1-2 short sentences", response_plan.system_instruction)
        self.assertIn("no filler or headings", response_plan.system_instruction)
        self.assertEqual(mock_get_tts.return_value.synthesize.call_args_list,
                         [call("It is test time. Details are ready.")])
        self.assertEqual(mock_process_message.return_value[-1]["response"], original_response)
        mock_print.assert_any_call("Assistant: ", end="", flush=True)
        mock_print.assert_any_call(original_response, end="", flush=True)
        summary = next(args[0] for args, _ in mock_print.call_args_list if args and str(args[0]).startswith("Timing:"))
        for metric in ("record", "normalize", "stt", "ai", "ai_ttft", "first_tts_chars", "first_tts_text_ready", "first_tts_unit_ready", "first_audio_ready",
                       "ai_to_first_speech", "first_speech",
                       "ai_total", "tts", "audio_prepare", "playback", "total"):
            self.assertIn(metric + "=", summary)
        mock_prepend.assert_called_once_with(_make_wav(), 0.7)
        self.assertEqual(mock_play.call_count, 1)
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
            "**" + FIRST_SENTENCE + "** " + SECOND_SENTENCE + " " + THIRD_SENTENCE
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
                call(FIRST_SENTENCE),
                call(SECOND_SENTENCE),
                call(THIRD_SENTENCE),
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
            FIRST_SENTENCE + " " + SECOND_SENTENCE, conversation_id=7)
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

        # A preparation failure can cancel the first prepared item before it
        # launches; it must never play the failed second chunk.
        self.assertLessEqual(mock_play.call_count, 1)

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


@override_settings(RAG_ENABLED=False, ONLINE_RETRIEVAL_ENABLED=False, DEVICE_CONTROL_ENABLED=False)
class VoiceInputInjectionTests(SimpleTestCase):
    """CLI inputs share real streaming workers/player; no hardware subprocesses."""

    def setUp(self):
        self.config = VoiceLoopConfig("", audio_backend="alsa", capture_vad_enabled=True)
        self.children = []
        self.mocks = {}
        targets = (
            "record_audio", "record_audio_alsa", "record_audio_with_capture_vad", "normalize_audio",
            "get_stt_provider", "get_tts_provider", "get_companion_display", "prepare_alsa_playback",
            "VoiceLoopConfig.from_settings", "AssistantService.process_message_stream",
            "AdaptiveCaptureVAD",
        )
        for name in targets:
            patcher = patch("scripts.voice_loop." + name)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("record_audio", "record_audio_alsa", "record_audio_with_capture_vad"):
            self.mocks[name].side_effect = AssertionError("Injected input must not capture audio")
        self.mocks["normalize_audio"].side_effect = lambda source, output, **kwargs: output.write_bytes(_make_wav())
        self.mocks["get_stt_provider"].return_value.transcribe.return_value = "What is an ESP32?"
        self.mocks["get_tts_provider"].return_value.synthesize.return_value = _make_wav()
        self.mocks["AssistantService.process_message_stream"].side_effect = lambda query, **kwargs: stream_events(
            "It is a microcontroller.", conversation_id=kwargs["conversation_id"] or 42)
        self.mocks["VoiceLoopConfig.from_settings"].return_value = self.config
        self.display = self.mocks["get_companion_display"].return_value
        self.states = []
        self.display.set_state.side_effect = lambda state: self.states.append(state.value)
        self.mocks["prepare_alsa_playback"].side_effect = lambda source, output, *args, **kwargs: output.write_bytes(
            b"\x00\x00\x00\x02" * 2 * 4800)

        def launch(*args, **kwargs):
            child = MagicMock()
            child.poll.return_value = None
            child.stdin.fileno.return_value = 123

            def wait(**kwargs):
                child.poll.return_value = 0
                return 0

            child.wait.side_effect = wait
            self.children.append(child)
            return child

        for target, effect in (
            ("assistant.voice.alsa_stream.subprocess.Popen", launch),
            ("assistant.voice.alsa_stream.os.write", lambda fd, data: len(data)),
        ):
            patcher = patch(target, side_effect=effect)
            self.mocks[target] = patcher.start()
            self.addCleanup(patcher.stop)
        for target in ("assistant.voice.alsa_stream.os.set_blocking", "builtins.print"):
            patcher = patch(target)
            self.mocks[target] = patcher.start()
            self.addCleanup(patcher.stop)

    def assert_no_capture(self):
        for name in ("record_audio", "record_audio_alsa", "record_audio_with_capture_vad", "AdaptiveCaptureVAD"):
            self.mocks[name].assert_not_called()

    def assert_reaped(self):
        self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in enumerate_threads()))
        for child in self.children:
            self.assertEqual(child.poll(), 0)
            child.wait.assert_called()
            child.stdin.close.assert_called()

    def test_audio_file_cli_bypasses_capture_but_normalizes_transcribes_and_speaks(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "test.wav"
            source.write_bytes(_make_wav(sample_rate=22050))
            with patch("builtins.input", side_effect=AssertionError("Single input must not prompt")):
                self.assertEqual(voice_loop.main(["--audio-file", str(source)]), 0)
            self.assertTrue(source.exists())  # User input is never removed/rewritten.
        self.assert_no_capture()
        normalize = self.mocks["normalize_audio"]
        normalize.assert_called_once()
        self.assertEqual(normalize.call_args.args[0], source)
        self.assertEqual(normalize.call_args.kwargs, {})  # Not the microphone's ALSA left-channel filter.
        normalized = normalize.call_args.args[1]
        self.mocks["get_stt_provider"].return_value.transcribe.assert_called_once_with(str(normalized))
        self.assertFalse(normalized.exists())
        service = self.mocks["AssistantService.process_message_stream"]
        self.assertEqual(service.call_args.args, ("What is an ESP32?",))
        self.assertEqual(service.call_args.kwargs["response_plan"], AssistantResponsePolicy.plan_voice_response("What is an ESP32?"))
        self.mocks["get_tts_provider"].return_value.synthesize.assert_called_once_with("It is a microcontroller.")
        self.mocks["builtins.print"].assert_any_call(f"Input audio: {source}")
        self.mocks["builtins.print"].assert_any_call("You: What is an ESP32?")
        self.assertEqual(self.states, ["READY", "THINKING", "SPEAKING", "READY"])
        self.display.close.assert_called_once()
        self.assertEqual(len(self.children), 1)
        self.assert_reaped()

    def test_text_cli_bypasses_all_audio_input_stages_but_uses_service_and_speaker(self):
        with patch("builtins.input", side_effect=AssertionError("Single input must not prompt")):
            self.assertEqual(voice_loop.main(["--text", "What is an ESP32?"]), 0)
        self.assert_no_capture()
        self.mocks["normalize_audio"].assert_not_called()
        self.mocks["get_stt_provider"].assert_not_called()
        self.mocks["VoiceLoopConfig.from_settings"].assert_called_once_with(require_capture=False)
        self.mocks["AssistantService.process_message_stream"].assert_called_once_with(
            "What is an ESP32?", conversation_id=None,
            response_plan=AssistantResponsePolicy.plan_voice_response("What is an ESP32?"))
        self.mocks["get_tts_provider"].return_value.synthesize.assert_called_once_with("It is a microcontroller.")
        self.mocks["builtins.print"].assert_any_call("Injected text: What is an ESP32?")
        summary = next(args[0] for args, _ in self.mocks["builtins.print"].call_args_list
                       if args and str(args[0]).startswith("Timing:"))
        for stage in ("record=", "normalize=", "stt="):
            self.assertNotIn(stage, summary)
        self.assertEqual(self.states, ["READY", "THINKING", "SPEAKING", "READY"])
        self.display.close.assert_called_once()
        self.assertEqual(len(self.children), 1)
        self.assert_reaped()

    def test_interactive_text_preserves_query_case_and_conversation_for_follow_up(self):
        with patch("builtins.input", side_effect=["", "Explain how Raspberry Pi works.", "Tell me about it.", "Q"]) as inputs:
            self.assertEqual(voice_loop.main(["--text-mode"]), 0)
        self.assertTrue(all(item == call("[text, q=quit] > ") for item in inputs.call_args_list))
        calls = self.mocks["AssistantService.process_message_stream"].call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[0], "Explain how Raspberry Pi works.")
        self.assertIsNone(calls[0].kwargs["conversation_id"])
        self.assertEqual(calls[1].args[0], "Tell me about it.")
        self.assertEqual(calls[1].kwargs["conversation_id"], 42)
        self.assertEqual(calls[1].kwargs["response_plan"].recent_history_limit, 2)
        self.assert_no_capture()
        self.mocks["get_stt_provider"].assert_not_called()
        self.display.close.assert_called_once()
        self.assertEqual(len(self.children), 2)
        self.assert_reaped()

    def test_default_cli_still_records_with_existing_vad_and_alsa_left_channel(self):
        self.mocks["record_audio_with_capture_vad"].side_effect = None
        with patch("builtins.input", side_effect=["", "q"]):
            self.assertEqual(voice_loop.main([]), 0)
        self.mocks["VoiceLoopConfig.from_settings"].assert_called_once_with()
        self.mocks["record_audio_with_capture_vad"].assert_called_once()
        self.assertEqual(self.mocks["record_audio_with_capture_vad"].call_args.kwargs, {
            "backend": "alsa", "adaptive": True, "noise_calibration_seconds": 0.5,
            "start_confirm_ms": 60, "min_speech_ms": 300,
        })
        self.assertEqual(self.mocks["normalize_audio"].call_args.kwargs, {"left_channel": True})
        self.mocks["get_stt_provider"].return_value.transcribe.assert_called_once()
        self.assertEqual(self.states, ["READY", "LISTENING", "THINKING", "SPEAKING", "READY"])
        self.assert_reaped()

    def test_interactive_failure_keeps_previous_conversation_and_retries_next_input(self):
        service = self.mocks["AssistantService.process_message_stream"]
        service.side_effect = [stream_events("It is a microcontroller.", conversation_id=42),
                               [{"type": "error", "detail": "Unavailable."}],
                               stream_events("It runs programs.", conversation_id=42)]
        with patch("builtins.input", side_effect=["What is an ESP32?", "Tell me more.", "Tell me about it.", "q"]), patch(
            "sys.stderr", new_callable=io.StringIO):
            self.assertEqual(voice_loop.main(["--text-mode"]), 0)
        self.assertEqual([item.kwargs["conversation_id"] for item in service.call_args_list], [None, 42, 42])
        self.assertIn("ERROR", self.states)
        self.assertEqual(self.states[-1], "READY")
        self.display.close.assert_called_once()
        self.assertEqual(len(self.children), 2)
        self.assert_reaped()

    def test_single_input_interrupt_closes_display_and_reports_cancelled_exit(self):
        self.mocks["AssistantService.process_message_stream"].side_effect = KeyboardInterrupt
        with patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(voice_loop.main(["--text", "What is an ESP32?"]), 130)
        self.assertEqual(self.states[-1], "ERROR")
        self.display.close.assert_called_once()
        self.assert_reaped()

    def test_missing_file_fails_once_without_capture_stt_service_or_playback(self):
        with tempfile.TemporaryDirectory() as directory, patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(voice_loop.main(["--audio-file", str(Path(directory) / "missing.wav")]), 1)
        self.assert_no_capture()
        self.mocks["get_stt_provider"].assert_not_called()
        self.mocks["AssistantService.process_message_stream"].assert_not_called()
        self.assertEqual(self.children, [])
        self.assertEqual(self.states, ["READY", "ERROR"])
        self.display.close.assert_called_once()

    def test_audio_file_no_speech_does_not_invoke_ai(self):
        self.mocks["get_stt_provider"].return_value.transcribe.return_value = "[BLANK_AUDIO]"
        with tempfile.TemporaryDirectory() as directory, patch("sys.stderr", new_callable=io.StringIO):
            source = Path(directory) / "silence.wav"
            source.write_bytes(_make_wav())
            self.assertEqual(voice_loop.main(["--audio-file", str(source)]), 1)
        self.assert_no_capture()
        self.mocks["normalize_audio"].assert_called_once()
        self.mocks["AssistantService.process_message_stream"].assert_not_called()
        self.assertEqual(self.children, [])
        self.display.close.assert_called_once()

    def test_injected_playback_failure_reaps_process_and_workers_and_closes_display(self):
        self.mocks["assistant.voice.alsa_stream.os.write"].side_effect = BrokenPipeError("speaker disconnected")
        with patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(voice_loop.main(["--text", "What is an ESP32?"]), 1)
        self.assertEqual(len(self.children), 1)
        self.children[0].terminate.assert_called()
        self.assert_reaped()
        self.assertEqual(self.states[-1], "ERROR")
        self.display.close.assert_called_once()

    def test_cli_rejects_conflicting_modes_and_blank_text_before_hardware(self):
        for argv in (["--text", "hello", "--audio-file", "test.wav"],
                     ["--text", "hello", "--text-mode"], ["--audio-file", "test.wav", "--text-mode"],
                     ["--text", "   "]):
            with self.subTest(argv=argv), patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as error:
                voice_loop.main(argv)
            self.assertEqual(error.exception.code, 2)
        self.mocks["get_companion_display"].assert_not_called()
        self.mocks["VoiceLoopConfig.from_settings"].assert_not_called()

    def test_injection_configuration_does_not_require_microphone_but_still_requires_speaker(self):
        VoiceLoopConfig("").validate(require_capture=False)
        VoiceLoopConfig("", audio_backend="alsa", alsa_capture_device="").validate(require_capture=False)
        with self.assertRaises(VoiceLoopError):
            VoiceLoopConfig("").validate()
        with self.assertRaises(VoiceLoopError):
            VoiceLoopConfig("", audio_backend="alsa", alsa_playback_device="").validate(require_capture=False)

    def test_main_warms_once_before_inputs_and_excludes_warmup_from_cycle_timings(self):
        clock = [0.0]
        provider = self.mocks["get_tts_provider"].return_value
        provider.warm.side_effect = lambda: clock.__setitem__(0, 7.0)
        queries = iter(("What is an ESP32?", "Tell me about it.", "q"))
        def input_text(prompt):
            provider.warm.assert_called_once()
            self.assertEqual(clock[0], 7.0)
            return next(queries)
        with patch("builtins.input", side_effect=input_text), patch("scripts.voice_loop.time.perf_counter", side_effect=lambda: clock[0]):
            self.assertEqual(voice_loop.main(["--text-mode"]), 0)
        summaries = [args[0] for args, _ in self.mocks["builtins.print"].call_args_list
                     if args and str(args[0]).startswith("Timing:")]
        self.assertEqual(len(summaries), 2)
        self.assertTrue(all("total=0.000s" in summary and "first_audio_ready=0.000s" in summary for summary in summaries))
        provider.close.assert_called_once()

    def test_help_does_not_initialize_or_warm_speech_provider(self):
        with self.assertRaises(SystemExit) as exit:
            voice_loop.main(["--help"])
        self.assertEqual(exit.exception.code, 0)
        self.mocks["get_tts_provider"].assert_not_called()

    def test_warm_failure_prevents_interaction_and_still_closes_display_and_provider(self):
        provider = self.mocks["get_tts_provider"].return_value
        provider.warm.side_effect = TTSError("Unable to load configured voice")
        with patch("builtins.input") as inputs, patch("sys.stderr", new_callable=io.StringIO) as error:
            self.assertEqual(voice_loop.main(["--text-mode"]), 1)
        inputs.assert_not_called()
        self.mocks["builtins.print"].assert_any_call("TTS warm-up failed: Unable to load configured voice", file=error)
        provider.close.assert_called_once()
        self.display.close.assert_called_once()
        self.assertEqual(self.states[-1], "ERROR")

    def test_main_reaps_real_synthetic_worker_on_normal_and_exceptional_exit(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                provider = PiperProvider("legacy", "fake-voice", timeout=5)
                self.addCleanup(provider.close)
                self.mocks["get_tts_provider"].return_value = provider
                service = self.mocks["AssistantService.process_message_stream"]
                service.side_effect = None
                service.return_value = ([{"type": "error", "detail": "AI failed"}] if failed
                                        else stream_events("It is a microcontroller.", conversation_id=42))
                pid = []
                real_warm = provider.warm
                def warm():
                    real_warm()
                    pid.append(provider._worker.pid)
                with patch("assistant.voice.tts.worker_main", fake_piper_worker), patch(
                    "assistant.voice.tts.os.path.exists", return_value=True), patch.object(provider, "warm", side_effect=warm), patch(
                    "sys.stderr", new_callable=io.StringIO):
                    self.assertEqual(voice_loop.main(["--text", "What is an ESP32?"]), 1 if failed else 0)
                self.assertIsNone(provider._worker)
                self.assertNotIn(pid[0], [child.pid for child in multiprocessing.active_children()])
                self.assert_reaped()

    def test_speech_cleanup_failure_does_not_prevent_display_cleanup(self):
        provider = self.mocks["get_tts_provider"].return_value
        provider.close.side_effect = RuntimeError("cleanup failed")
        with patch("builtins.input", return_value="q"), self.assertLogs("scripts.voice_loop", level="WARNING"):
            self.assertEqual(voice_loop.main(["--text-mode"]), 0)
        self.display.close.assert_called_once()


class VoiceTestAudioFixtureTests(SimpleTestCase):
    @override_settings(TTS_ENGINE="piper")
    @patch("scripts.make_voice_test_audio.get_tts_provider")
    def test_fixture_cli_writes_configured_provider_wav_without_playback(self, provider):
        audio = _make_wav(sample_rate=22050)
        provider.return_value.synthesize.return_value = audio
        with tempfile.TemporaryDirectory() as directory, patch("builtins.print") as output:
            destination = Path(directory) / "fixture.wav"
            self.assertEqual(make_voice_test_audio.main(["--text", "What is an ESP32?", "--output", str(destination)]), 0)
            self.assertEqual(destination.read_bytes(), audio)
            output.assert_any_call(f"Test audio: {destination} (configured TTS: piper)")
        provider.return_value.synthesize.assert_called_once_with("What is an ESP32?")

    @patch("scripts.make_voice_test_audio.get_tts_provider")
    def test_failed_synthesis_or_invalid_wav_does_not_replace_existing_fixture(self, provider):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "fixture.wav"
            destination.write_bytes(_make_wav())
            original = destination.read_bytes()
            for result, failure in ((b"not a wav", None), (b"", RuntimeError("TTS unavailable"))):
                with self.subTest(failure=failure), patch("sys.stderr", new_callable=io.StringIO):
                    provider.return_value.synthesize.return_value = result
                    provider.return_value.synthesize.side_effect = failure
                    self.assertEqual(make_voice_test_audio.main(["--text", "Hello", "--output", str(destination)]), 1)
                    self.assertEqual(destination.read_bytes(), original)

    @patch("scripts.make_voice_test_audio.get_tts_provider")
    def test_blank_fixture_text_is_rejected_without_calling_tts(self, provider):
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as error:
            make_voice_test_audio.main(["--text", " ", "--output", "unused.wav"])
        self.assertEqual(error.exception.code, 2)
        provider.assert_not_called()
