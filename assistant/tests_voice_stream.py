"""Streaming speech, backpressure and cancellation without audio hardware."""

import io
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event, Timer, enumerate as enumerate_threads, get_ident
from unittest.mock import MagicMock, call, patch
import wave

from django.test import SimpleTestCase, TestCase, override_settings

from assistant.execution_policy import CONSTRAINED_RESOURCE_STATUS, REDUCED_RESOURCE_STATUS
from assistant.voice.processes import AudioCancelledError, run_cancellable
from assistant.voice.tts import MockTTSProvider, PiperProvider, TTSError
from conversations.models import Conversation, Message
from scripts import voice_loop

REAL_PREPARE_ALSA = voice_loop.prepare_alsa_playback
REAL_PLAY_ALSA = voice_loop.play_audio_alsa


def stream_events(text, conversation_id=42, direct=False, **metadata):
    return [{"type": "start", "conversation_id": conversation_id, "direct": direct},
            {"type": "delta", "text": text},
            {"type": "done", "conversation_id": conversation_id, "response": text, **metadata}]


class StreamingBufferTests(SimpleTestCase):
    def test_multiple_deltas_wait_for_one_completed_sentence(self):
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed("A natural"), [])
        self.assertEqual(buffer.feed(" completed sentence"), [])
        self.assertEqual(buffer.feed(". "), ["A natural completed sentence."])

    def test_split_closing_quote_bracket_and_markdown_stay_with_sentence(self):
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed('He said "**Hello.'), [])
        self.assertEqual(buffer.feed('**") '), ['He said "Hello.")'])
        self.assertEqual(buffer.feed("Another fact! "), ["Another fact!"])

    def test_final_incomplete_sentence_flushes(self):
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed("This answer has no final punctuation"), [])
        self.assertEqual(buffer.feed("", final=True), ["This answer has no final punctuation"])
        self.assertEqual(buffer.feed("", final=True), [])

    def test_long_unpunctuated_text_splits_only_at_word_boundaries(self):
        text = " ".join("word" + str(index) for index in range(80))
        buffer = voice_loop.StreamingSpeechBuffer(180)
        spoken = []
        for character in text:
            spoken.extend(buffer.feed(character))
        self.assertGreater(len(spoken), 1)  # Already released before done.
        spoken.extend(buffer.feed("", final=True))
        self.assertEqual(" ".join(spoken), text)
        self.assertTrue(all(len(unit) <= 180 for unit in spoken))

    def test_arbitrary_delta_boundaries_do_not_lose_duplicate_or_reorder_words(self):
        text = '**First answer.** Second fact? He said "Yes!" Final incomplete thought'
        for width in range(1, len(text) + 1):
            with self.subTest(width=width):
                buffer = voice_loop.StreamingSpeechBuffer()
                spoken = []
                for start in range(0, len(text), width):
                    spoken.extend(buffer.feed(text[start:start + width]))
                spoken.extend(buffer.feed("", final=True))
                self.assertEqual(" ".join(spoken), voice_loop.sanitize_text_for_speech(text))

    def test_markdown_links_are_not_split_or_spoken_as_urls(self):
        text = "Read [the helpful local guide](https://example.test/long/path). Then continue."
        buffer = voice_loop.StreamingSpeechBuffer(35)
        spoken = []
        for character in text:
            spoken.extend(buffer.feed(character))
        spoken.extend(buffer.feed("", final=True))
        self.assertEqual(" ".join(spoken), voice_loop.sanitize_text_for_speech(text))

    @override_settings(VOICE_STREAM_TTS_TARGET_CHARS="220", VOICE_AUDIO_BACKEND="pulse", VOICE_INPUT_SOURCE="source")
    def test_target_setting_and_validation(self):
        self.assertEqual(voice_loop.VoiceLoopConfig.from_settings().stream_tts_target_chars, 220)
        with self.assertRaisesRegex(voice_loop.VoiceLoopError, "VOICE_STREAM_TTS_TARGET_CHARS"):
            voice_loop.VoiceLoopConfig("source", stream_tts_target_chars=0).validate()


class StreamingAudioTests(SimpleTestCase):
    def setUp(self):
        self.provider = MagicMock()
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x01\x00" * 160)
        self.audio = output.getvalue()
        self.provider.synthesize.return_value = self.audio
        self.config = voice_loop.VoiceLoopConfig("source", leading_silence_seconds=0.5)
        self.timings = {}
        self.states = []
        for name in ("play_audio", "play_audio_alsa", "prepare_alsa_playback"):
            patcher = patch("scripts.voice_loop." + name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.main_thread = get_ident()

    def speak(self, events, config=None):
        with tempfile.TemporaryDirectory() as directory:
            result = voice_loop.speak_stream_response(
                events, self.provider, Path(directory), config or self.config, self.timings,
                voice_loop.time.perf_counter(), on_playback_start=lambda: self.states.append("SPEAKING"))
        self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in enumerate_threads()))
        return result

    def test_sentence_plays_before_done_and_generation_continues_during_playback(self):
        playback_started = Event()
        generation_continued = Event()
        thread_ids = []
        def play(*args, **kwargs):
            thread_ids.append(get_ident())
            if not playback_started.is_set():
                playback_started.set()
                self.assertTrue(generation_continued.wait(2))
        self.play_audio.side_effect = play
        def events():
            self.assertEqual(get_ident(), self.main_thread)
            yield {"type": "start", "conversation_id": 71, "direct": False}
            yield {"type": "delta", "text": "First"}
            yield {"type": "delta", "text": " completed sentence. "}
            self.assertTrue(playback_started.wait(2))
            generation_continued.set()
            yield {"type": "delta", "text": "Second sentence. Last unfinished thought"}
            yield {"type": "done", "conversation_id": 72,
                   "response": "First completed sentence. Second sentence. Last unfinished thought",
                   "resource_profile": "ECO", "num_predict": 64}
        conversation_id, metadata = self.speak(events())
        self.assertEqual(conversation_id, 72)
        self.assertEqual(metadata["num_predict"], 64)
        self.assertEqual(self.provider.synthesize.call_args_list,
                         [call("First completed sentence."), call("Second sentence."), call("Last unfinished thought")])
        self.assertEqual(len(set(thread_ids)), 1)
        self.assertNotEqual(thread_ids[0], self.main_thread)
        self.assertEqual(self.states, ["SPEAKING"])
        self.assertGreaterEqual(self.timings["first_speech"], self.timings["ai_ttft"])
        self.assertEqual(self.timings["ai"], self.timings["ai_total"])
        self.assertIn("tts", self.timings)
        self.assertIn("playback", self.timings)

    def test_direct_response_is_spoken_and_start_id_is_fallback(self):
        events = stream_events("Please clarify your request.", conversation_id=19, direct=True)
        events[-1].pop("conversation_id")
        self.assertEqual(self.speak(events)[0], 19)
        self.provider.synthesize.assert_called_once_with("Please clarify your request.")

    def test_resource_prefix_is_filtered_only_from_voice_copy_not_canonical_text(self):
        for prefix in (REDUCED_RESOURCE_STATUS, CONSTRAINED_RESOURCE_STATUS):
            with self.subTest(prefix=prefix):
                self.provider.reset_mock()
                text = prefix + "\n\n" + "The actual answer."
                events = stream_events(text)
                events[1:2] = [{"type": "delta", "text": prefix + "\n\n"},
                               {"type": "delta", "text": "The actual answer."}]
                with patch("builtins.print") as output:
                    metadata = self.speak(events)[1]
                self.provider.synthesize.assert_called_once_with("The actual answer.")
                self.assertEqual(metadata["response"], text)
                output.assert_any_call("Assistant: " + text)

    def test_direct_resource_unavailability_answer_is_not_hidden(self):
        self.speak(stream_events(REDUCED_RESOURCE_STATUS + "\n\n", direct=True))
        self.provider.synthesize.assert_called_once_with(REDUCED_RESOURCE_STATUS)

    def test_error_before_speech_and_unexpected_eof(self):
        for events in ([{"type": "error", "detail": "offline"}],
                       [{"type": "delta", "text": "Incomplete text"}]):
            with self.subTest(events=events), self.assertRaises(voice_loop.VoiceLoopError):
                self.speak(events)
        self.provider.synthesize.assert_not_called()
        self.play_audio.assert_not_called()
        self.assertEqual(self.states, [])
        self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in enumerate_threads()))

    def test_malformed_mismatched_and_empty_streams_fail_without_hanging(self):
        cases = [["not an event"], [{"type": "unexpected"}], stream_events(""),
                 [{"type": "delta", "text": 123}],
                 [{"type": "delta", "text": "Unfinished text"}, {"type": "done", "response": "different"}]]
        for events in cases:
            with self.subTest(events=events), self.assertRaises(voice_loop.VoiceLoopError):
                self.speak(events)
        self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in enumerate_threads()))

    def test_producer_interrupt_cancels_worker_and_closes_stream(self):
        closed = Event()
        def events():
            try:
                yield {"type": "start", "conversation_id": 1, "direct": False}
                raise KeyboardInterrupt
            finally:
                closed.set()
        with self.assertRaises(KeyboardInterrupt):
            self.speak(events())
        self.assertTrue(closed.is_set())
        self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in enumerate_threads()))

    def test_streaming_does_not_apply_legacy_batch_truncation_limit(self):
        config = voice_loop.VoiceLoopConfig("source", tts_max_total_chars=5)
        self.speak(stream_events("First sentence. Second sentence."), config)
        self.assertEqual(self.provider.synthesize.call_args_list, [call("First sentence."), call("Second sentence.")])

    def test_first_speech_metric_includes_display_callback_time(self):
        clock = [0]
        timings = {}
        with tempfile.TemporaryDirectory() as directory, patch("scripts.voice_loop.time.perf_counter", side_effect=lambda: clock[0]):
            voice_loop.speak_stream_response(
                stream_events("An answer."), self.provider, Path(directory), self.config, timings, 0,
                on_playback_start=lambda: clock.__setitem__(0, 3))
        self.assertEqual(timings["first_speech"], 3)

    def test_error_after_first_playback_cancels_active_audio_and_discards_queue(self):
        playback_started = Event()
        playback_cancelled = Event()
        stream_closed = Event()
        def play(*args, cancel_event):
            playback_started.set()
            self.assertTrue(cancel_event.wait(2))
            playback_cancelled.set()
            raise AudioCancelledError("cancelled")
        self.play_audio.side_effect = play
        def events():
            try:
                yield {"type": "start", "conversation_id": 19, "direct": False}
                yield {"type": "delta", "text": "First sentence. "}
                self.assertTrue(playback_started.wait(2))
                yield {"type": "delta", "text": "Queued sentence. "}
                yield {"type": "error", "detail": "connection lost"}
            finally:
                stream_closed.set()
        with self.assertRaisesRegex(voice_loop.VoiceLoopError, "connection lost"):
            self.speak(events())
        self.assertTrue(playback_cancelled.is_set())
        self.assertTrue(stream_closed.is_set())
        self.play_audio.assert_called_once()
        self.provider.synthesize.assert_called_once_with("First sentence.")
        self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in enumerate_threads()))

    def test_audio_failure_unblocks_full_queue_and_closes_producer(self):
        closed = Event()
        def events():
            try:
                yield {"type": "start", "conversation_id": 1, "direct": False}
                for _ in range(40):
                    yield {"type": "delta", "text": "Another completed sentence. "}
            finally:
                closed.set()
        self.provider.synthesize.side_effect = TTSError("Piper stopped")
        with self.assertRaisesRegex(voice_loop.VoiceLoopError, "Piper stopped"):
            self.speak(events())
        self.assertTrue(closed.is_set())
        self.assertEqual(self.provider.synthesize.call_count, 1)
        self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in enumerate_threads()))

    def test_queue_capacity_and_piper_concurrency_are_bounded(self):
        from queue import Queue
        queues = []
        def queue_factory(*args, **kwargs):
            queues.append(Queue(*args, **kwargs))
            return queues[-1]
        threads = []
        self.provider.synthesize.side_effect = lambda text: threads.append(get_ident()) or self.audio
        with patch("scripts.voice_loop.Queue", side_effect=queue_factory):
            self.speak(stream_events("A completed sentence. " * 30))
        self.assertEqual(queues[0].maxsize, 2)
        self.assertEqual(self.provider.synthesize.call_count, 30)
        self.assertEqual(len(set(threads)), 1)

    def test_alsa_first_silence_conversion_and_speaking_order(self):
        inputs, order = [], []
        def convert(path, output, gain, **kwargs):
            inputs.append(path.read_bytes())
            self.assertEqual(gain, 4.0)
            order.append("convert")
        self.prepare_alsa_playback.side_effect = convert
        self.play_audio_alsa.side_effect = lambda *args, **kwargs: order.append("play")
        config = voice_loop.VoiceLoopConfig("", audio_backend="alsa", leading_silence_seconds=0.5)
        with tempfile.TemporaryDirectory() as directory, patch("scripts.voice_loop.time.sleep") as sleep:
            voice_loop.speak_stream_response(stream_events("First sentence. Second sentence."), self.provider,
                                            Path(directory), config, {}, voice_loop.time.perf_counter(),
                                            on_playback_start=lambda: order.append("SPEAKING"))
        self.assertEqual(order, ["convert", "SPEAKING", "play", "convert", "play"])
        sleep.assert_not_called()
        with wave.open(io.BytesIO(inputs[0]), "rb") as wav:
            self.assertEqual(wav.getnframes(), 8160)
            self.assertEqual(wav.readframes(8000), b"\x00" * 16000)
        self.assertEqual(inputs[1], self.audio)
        self.play_audio.assert_not_called()
        for playback in self.play_audio_alsa.call_args_list:
            self.assertEqual(playback.args[1], "hw:CARD=sndrpigooglevoi,DEV=0")

    def test_actual_alsa_commands_use_cancellable_subprocess_path(self):
        config = voice_loop.VoiceLoopConfig("", audio_backend="alsa")
        self.prepare_alsa_playback.side_effect = REAL_PREPARE_ALSA
        self.play_audio_alsa.side_effect = REAL_PLAY_ALSA
        def execute(command, **kwargs):
            if command[0] == "ffmpeg":
                Path(command[-1]).write_bytes(self.audio)
            return subprocess.CompletedProcess(command, 0, b"", b"")
        with patch("scripts.voice_loop._run_audio_subprocess") as run:
            run.side_effect = execute
            self.speak(stream_events("An answer."), config)
        conversion, playback = run.call_args_list
        command = conversion.args[0]
        self.assertEqual(command[:3], ["ffmpeg", "-y", "-i"])
        self.assertEqual(command[4:-1], ["-af", "volume=4.0,alimiter=limit=0.98:level=false",
                                        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s32le"])
        self.assertEqual(playback.args[0], ["aplay", "-D", "hw:CARD=sndrpigooglevoi,DEV=0", command[-1]])
        conversion_event = self.prepare_alsa_playback.call_args.kwargs["cancel_event"]
        self.assertIs(self.play_audio_alsa.call_args.kwargs["cancel_event"], conversion_event)
        self.assertIs(conversion.kwargs["cancel_event"], conversion_event)
        self.assertIs(playback.kwargs["cancel_event"], conversion_event)


class CancellableProcessTests(SimpleTestCase):
    def test_real_child_is_cancelled_and_reaped_without_hardware(self):
        stopped = Event()
        children = []
        original_popen = subprocess.Popen
        def launch(*args, **kwargs):
            child = original_popen(*args, **kwargs)
            children.append(child)
            return child
        timer = Timer(0.1, stopped.set)
        timer.start()
        try:
            with patch("assistant.voice.processes.subprocess.Popen", side_effect=launch):
                with self.assertRaises(AudioCancelledError):
                    run_cancellable([sys.executable, "-c", "import time; time.sleep(30)"],
                                    cancel_event=stopped, timeout=5)
        finally:
            timer.cancel()
            timer.join()
        self.assertIsNotNone(children[0].poll())
        self.assertIsNotNone(children[0].returncode)

    @patch("assistant.voice.processes.subprocess.Popen")
    def test_cancel_kills_and_reaps_stubborn_child(self, popen):
        stopped = Event()
        child = popen.return_value
        child.poll.return_value = None
        def communicate(**kwargs):
            stopped.set()
            raise subprocess.TimeoutExpired("audio", 0.1)
        calls = 0
        def communication(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return communicate(**kwargs)
            if calls == 2:
                raise subprocess.TimeoutExpired("audio", 2)
            return b"", b""
        child.communicate.side_effect = communication
        with self.assertRaises(AudioCancelledError):
            run_cancellable(["aplay", "file.wav"], cancel_event=stopped, timeout=10)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(child.communicate.call_args_list[-1], call(timeout=5))

    @patch("assistant.voice.processes.subprocess.Popen")
    def test_input_sent_once_and_completed_process_reaped(self, popen):
        child = popen.return_value
        child.returncode = 0
        child.poll.return_value = 0
        child.communicate.side_effect = [subprocess.TimeoutExpired("piper", 0.1), (b"wav", b""), (b"wav", b"")]
        result = run_cancellable(["piper"], cancel_event=Event(), timeout=10, input=b"A sentence.")
        self.assertEqual(result.stdout, b"wav")
        self.assertEqual(child.communicate.call_args_list[0].kwargs["input"], b"A sentence.")
        self.assertIsNone(child.communicate.call_args_list[1].kwargs["input"])
        child.terminate.assert_not_called()

    @patch("assistant.voice.processes.subprocess.Popen")
    def test_cancelled_job_never_starts_child(self, popen):
        stopped = Event()
        stopped.set()
        with self.assertRaises(AudioCancelledError):
            run_cancellable(["piper"], cancel_event=stopped, timeout=10)
        popen.assert_not_called()

    @patch("assistant.voice.processes.time.monotonic", side_effect=[0, 11])
    @patch("assistant.voice.processes.subprocess.Popen")
    def test_timeout_is_bounded_and_reaped(self, popen, clock):
        popen.return_value.poll.return_value = None
        with self.assertRaises(subprocess.TimeoutExpired):
            run_cancellable(["piper"], cancel_event=Event(), timeout=10)
        popen.return_value.terminate.assert_called_once()
        popen.return_value.communicate.assert_called_once_with(timeout=2)

    @patch("assistant.voice.tts.os.path.exists", return_value=True)
    @patch("assistant.voice.tts.run_cancellable")
    def test_piper_cancellation_uses_same_provider_and_error_contract(self, run, exists):
        provider = PiperProvider("piper", "voice.onnx")
        stopped = Event()
        run.return_value = subprocess.CompletedProcess([], 0, b"wav", b"")
        self.assertEqual(provider.synthesize_cancellable("A sentence.", stopped), b"wav")
        run.assert_called_once_with(["piper", "-m", "voice.onnx", "-f", "-"],
                                    input=b"A sentence.", timeout=30, cancel_event=stopped)
        run.side_effect = AudioCancelledError()
        with self.assertRaisesRegex(TTSError, "cancelled"):
            provider.synthesize_cancellable("A sentence.", stopped)


@override_settings(AI_ENGINE="mock", RAG_ENABLED=False, ONLINE_RETRIEVAL_ENABLED=False,
                   DEVICE_CONTROL_ENABLED=False, CONVERSATION_MEMORY_ENABLED=False)
class StreamingVoicePersistenceTests(TestCase):
    @patch("scripts.voice_loop.get_tts_provider", return_value=MockTTSProvider())
    @patch("scripts.voice_loop.play_audio")
    @patch("scripts.voice_loop.get_stt_provider")
    @patch("scripts.voice_loop.normalize_audio")
    @patch("scripts.voice_loop.record_audio")
    def test_real_service_preserves_one_conversation_and_messages(self, record, normalize, stt, play, tts):
        stt.return_value.transcribe.return_value = "Explain local AI."
        # No DB use from the audio worker; the real service runs on this thread.
        with patch("scripts.voice_loop.AssistantService.process_message") as blocking_service:
            first_id = voice_loop.run_voice_cycle(voice_loop.VoiceLoopConfig("source"))
            second_id = voice_loop.run_voice_cycle(voice_loop.VoiceLoopConfig("source"), first_id)
        blocking_service.assert_not_called()
        self.assertEqual(first_id, second_id)
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(Message.objects.filter(conversation_id=first_id, sender="USER").count(), 2)
        self.assertEqual(Message.objects.filter(conversation_id=first_id, sender="AI").count(), 2)
