"""Streaming speech, backpressure and cancellation without audio hardware."""

import io
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Full, Queue
import subprocess
import sys
import tempfile
from threading import Event, Timer, enumerate as enumerate_threads, get_ident
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch
import wave

from django.test import SimpleTestCase, TestCase, override_settings

from assistant.execution_policy import CONSTRAINED_RESOURCE_STATUS, REDUCED_RESOURCE_STATUS
from assistant.ai_engine import AIEngineResult, AIEngineStreamEvent
from assistant.services import AssistantService
from assistant.voice.processes import AudioCancelledError, run_cancellable
from assistant.voice.tts import MockTTSProvider, PiperProvider, TextToSpeechProvider, TTSError
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
        self.assertIn("audio_prepare", self.timings)
        self.assertIn("playback", self.timings)
        self.assertLess(self.timings["ai_to_first_speech"], self.timings["ai_total"])

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
                output.assert_any_call(f"Status: {prefix}", flush=True)
                output.assert_any_call("Assistant: ", end="", flush=True)
                output.assert_any_call("The actual answer.", end="", flush=True)
                self.assertNotIn(call("Assistant: " + text), output.call_args_list)

    def test_direct_resource_unavailability_answer_is_not_hidden(self):
        text = "Local AI is temporarily unavailable because the device is protecting itself."
        self.speak(stream_events(text, direct=True))
        self.provider.synthesize.assert_called_once_with(text)

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
        self.assertEqual(self.provider.synthesize.call_args_list[0], call("First sentence."))
        self.assertLessEqual(self.provider.synthesize.call_count, 2)
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
        self.assertEqual([queue.maxsize for queue in queues], [2, 2])
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
        self.assertEqual(order.count("SPEAKING"), 1)
        self.assertEqual(order.count("play"), 2)
        self.assertLess(order.index("convert"), order.index("SPEAKING"))
        self.assertEqual([event for event in order if event != "convert"], ["SPEAKING", "play", "play"])
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

    def test_prepare_next_audio_during_playback_with_unique_files_and_no_overlap(self):
        playing_first = Event()
        second_ready = Event()
        prep_threads, play_threads, played_paths = [], [], []
        active_synthesis = active_playback = 0
        queues = []
        class ObservedQueue(Queue):
            def put(queue, item, *args, **kwargs):
                super().put(item, *args, **kwargs)
                if isinstance(item, voice_loop.PreparedAudio) and item.number == 2:
                    second_ready.set()
        def queue_factory(*args, **kwargs):
            queues.append(ObservedQueue(*args, **kwargs))
            return queues[-1]
        def synthesize(text):
            nonlocal active_synthesis
            self.assertEqual(active_synthesis, 0)
            active_synthesis += 1
            prep_threads.append(get_ident())
            try:
                if text == "Second sentence.":
                    self.assertTrue(playing_first.wait(2))
                return self.audio
            finally:
                active_synthesis -= 1
        def convert(source, destination, gain, **kwargs):
            self.assertEqual(gain, 4)
            destination.write_bytes(source.read_bytes())
        def play(path, device, **kwargs):
            nonlocal active_playback
            self.assertEqual(active_playback, 0)
            active_playback += 1
            play_threads.append(get_ident())
            played_paths.append(path.name)
            try:
                if len(played_paths) == 1:
                    original = path.read_bytes()
                    playing_first.set()
                    self.assertTrue(second_ready.wait(2))  # Prep/enqueue 2 while playback 1 is active.
                    self.assertEqual(path.read_bytes(), original)  # Prep did not overwrite playing WAV.
                if len(played_paths) == 2:
                    self.assertTrue(second_ready.is_set())
                    self.assertGreaterEqual(self.provider.synthesize.call_count, 2)
            finally:
                active_playback -= 1
        self.provider.synthesize.side_effect = synthesize
        self.prepare_alsa_playback.side_effect = convert
        self.play_audio_alsa.side_effect = play
        config = voice_loop.VoiceLoopConfig("", audio_backend="alsa", leading_silence_seconds=0.5)
        with patch("scripts.voice_loop.Queue", side_effect=queue_factory), patch("scripts.voice_loop.time.sleep") as sleep:
            self.speak(stream_events("First sentence. Second sentence. Third sentence."), config)
        sleep.assert_not_called()
        self.assertEqual(played_paths, ["stream-speaker-001.wav", "stream-speaker-002.wav", "stream-speaker-003.wav"])
        self.assertEqual([item.args[0].name for item in self.prepare_alsa_playback.call_args_list],
                         ["stream-playback-001.wav", "stream-playback-002.wav", "stream-playback-003.wav"])
        self.assertEqual(len(set(prep_threads)), 1)
        self.assertEqual(len(set(play_threads)), 1)
        self.assertNotEqual(prep_threads[0], play_threads[0])
        self.assertEqual([queue.maxsize for queue in queues], [2, 2])
        self.assertEqual(self.states, ["SPEAKING"])

    def test_tts_failure_cancels_when_text_queue_is_full(self):
        full = Event()
        queues = []
        class ObservedQueue(Queue):
            def put(queue, item, *args, **kwargs):
                try:
                    super().put(item, *args, **kwargs)
                except Full:
                    if queue is queues[0]:
                        full.set()
                    raise
        def queue_factory(*args, **kwargs):
            queues.append(ObservedQueue(*args, **kwargs))
            return queues[-1]
        def synthesize(text):
            self.assertTrue(full.wait(2))
            raise TTSError("Piper failed while text queue full")
        self.provider.synthesize.side_effect = synthesize
        with patch("scripts.voice_loop.Queue", side_effect=queue_factory):
            with self.assertRaisesRegex(voice_loop.VoiceLoopError, "Piper failed while text queue full"):
                self.speak(stream_events("A completed sentence. " * 30))
        self.assertTrue(full.is_set())
        self.play_audio.assert_not_called()
        self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in enumerate_threads()))

    def test_playback_failure_cancels_both_full_queues(self):
        full_text, full_audio = Event(), Event()
        queues = []
        class ObservedQueue(Queue):
            def put(queue, item, *args, **kwargs):
                try:
                    super().put(item, *args, **kwargs)
                except Full:
                    (full_text if queue is queues[0] else full_audio).set()
                    raise
        def queue_factory(*args, **kwargs):
            queues.append(ObservedQueue(*args, **kwargs))
            return queues[-1]
        def play(*args, **kwargs):
            self.assertTrue(full_audio.wait(2))
            self.assertTrue(full_text.wait(2))
            raise voice_loop.VoiceLoopError("speaker failed while both queues full")
        self.play_audio.side_effect = play
        with patch("scripts.voice_loop.Queue", side_effect=queue_factory):
            with self.assertRaisesRegex(voice_loop.VoiceLoopError, "speaker failed while both queues full"):
                self.speak(stream_events("A completed sentence. " * 30))
        self.assertTrue(full_audio.is_set())
        self.assertTrue(full_text.is_set())
        self.play_audio.assert_called_once()
        self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in enumerate_threads()))

    def test_preparation_or_tts_failure_stops_active_playback_and_keeps_root_error(self):
        for stage in ("tts", "prepare"):
            with self.subTest(stage=stage):
                self.provider.reset_mock()
                self.play_audio_alsa.reset_mock()
                self.prepare_alsa_playback.reset_mock()
                active = Event()
                cancelled = Event()
                def fail_after_playback():
                    self.assertTrue(active.wait(2))
                    raise voice_loop.VoiceLoopError("original " + stage + " failure")
                def synthesize(text):
                    if stage == "tts" and text == "Second sentence.":
                        fail_after_playback()
                    return self.audio
                def convert(source, destination, gain, **kwargs):
                    if stage == "prepare" and source.name.endswith("002.wav"):
                        fail_after_playback()
                def play(*args, cancel_event):
                    active.set()
                    self.assertTrue(cancel_event.wait(2))
                    cancelled.set()
                    raise AudioCancelledError("cancelled secondary error")
                self.provider.synthesize.side_effect = synthesize
                self.prepare_alsa_playback.side_effect = convert
                self.play_audio_alsa.side_effect = play
                config = voice_loop.VoiceLoopConfig("", audio_backend="alsa")
                with self.assertRaisesRegex(voice_loop.VoiceLoopError, "original " + stage + " failure"):
                    self.speak(stream_events("First sentence. Second sentence."), config)
                self.assertTrue(cancelled.is_set())
                self.play_audio_alsa.assert_called_once()
                self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in enumerate_threads()))

    def test_live_terminal_deltas_before_done_without_final_answer_replay(self):
        output = io.StringIO()
        fragments = ["A solar panel ", "converts sunlight.\n", "It produces electricity."]
        def events():
            yield {"type": "start", "conversation_id": 9, "direct": False}
            for index, fragment in enumerate(fragments):
                yield {"type": "delta", "text": fragment}
                self.assertEqual(output.getvalue(), "Assistant: " + "".join(fragments[:index + 1]))
            yield {"type": "done", "conversation_id": 9, "response": "".join(fragments)}
        with redirect_stdout(output), patch("builtins.print", wraps=print) as print_calls:
            self.assertEqual(self.speak(events())[0], 9)
        self.assertEqual(output.getvalue(), "Assistant: " + "".join(fragments) + "\nTTS chunks: 2\n")
        for fragment in fragments:
            self.assertIn(call(fragment, end="", flush=True), print_calls.call_args_list)
        self.assertEqual(output.getvalue().count("Assistant: "), 1)

    def test_direct_terminal_output_preserves_newlines_and_is_not_duplicated(self):
        text = "Please clarify.\nWhich device?"
        output = io.StringIO()
        with redirect_stdout(output):
            self.speak(stream_events(text, direct=True))
        self.assertEqual(output.getvalue(), "Assistant: " + text + "\nTTS chunks: 2\n")

    def test_terminal_error_finishes_partial_line(self):
        output = io.StringIO()
        events = [{"type": "start", "direct": False}, {"type": "delta", "text": "Partial answer"},
                  {"type": "error", "detail": "offline"}]
        with redirect_stdout(output), self.assertRaisesRegex(voice_loop.VoiceLoopError, "offline"):
            self.speak(events)
        self.assertEqual(output.getvalue(), "Assistant: Partial answer\n")
        self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in enumerate_threads()))

    def test_repeated_model_notice_across_deltas_is_presentation_only(self):
        prefix = REDUCED_RESOURCE_STATUS + "\n\n"
        text = prefix * 2 + "The actual answer."
        events = stream_events(text)
        fragments = [prefix, "Running in ", "reduced-resource mode.", "\n\nThe actual answer."]
        events[1:2] = [{"type": "delta", "text": part} for part in fragments]
        output = io.StringIO()
        with redirect_stdout(output):
            done = self.speak(events)[1]
        self.assertEqual(output.getvalue(), f"Status: {REDUCED_RESOURCE_STATUS}\nAssistant: The actual answer.\nTTS chunks: 1\n")
        self.provider.synthesize.assert_called_once_with("The actual answer.")
        self.assertEqual(done["response"], text)  # Never rewrite canonical service text.

    def test_timing_separates_preparation_and_physical_playback(self):
        # First prep runs alone; then playback starts. A shared monotonic fake
        # clock makes stage ownership explicit without sleeping or hardware.
        clock = [10]
        timings = {}
        def synthesize(text):
            clock[0] += 2
            return self.audio
        def convert(*args, **kwargs):
            clock[0] += 3
        def play(*args, **kwargs):
            clock[0] += 7
        self.provider.synthesize.side_effect = synthesize
        self.prepare_alsa_playback.side_effect = convert
        self.play_audio_alsa.side_effect = play
        config = voice_loop.VoiceLoopConfig("", audio_backend="alsa")
        with tempfile.TemporaryDirectory() as directory, patch("scripts.voice_loop.time.perf_counter", side_effect=lambda: clock[0]):
            voice_loop.speak_stream_response(stream_events("An answer."), self.provider, Path(directory),
                                            config, timings, cycle_started=1)
        self.assertEqual(timings["tts"], 2)
        self.assertEqual(timings["audio_prepare"], 3)
        self.assertEqual(timings["playback"], 7)
        self.assertEqual(timings["ai_to_first_speech"], 5)
        self.assertEqual(timings["first_speech"], 14)

    def test_ai_error_cancels_active_preparation_and_playback_together(self):
        active_play, active_tts = Event(), Event()
        cancelled_play, cancelled_tts = Event(), Event()
        audio = self.audio
        class Provider(TextToSpeechProvider):
            def synthesize_cancellable(provider, text, cancel_event):
                if text == "Second sentence.":
                    if not active_play.wait(2):
                        raise TTSError("test playback did not start")
                    active_tts.set()
                    if not cancel_event.wait(2):
                        raise TTSError("test cancellation timed out")
                    cancelled_tts.set()
                    raise AudioCancelledError("preparation cancelled")
                return audio
        self.provider = Provider()
        def play(*args, cancel_event):
            active_play.set()
            self.assertTrue(cancel_event.wait(2))
            cancelled_play.set()
            raise AudioCancelledError("playback cancelled")
        self.play_audio.side_effect = play
        def events():
            yield {"type": "start", "conversation_id": 1, "direct": False}
            yield {"type": "delta", "text": "First sentence. Second sentence. "}
            self.assertTrue(active_tts.wait(2))
            yield {"type": "error", "detail": "AI connection interrupted"}
        with self.assertRaisesRegex(voice_loop.VoiceLoopError, "AI connection interrupted"):
            self.speak(events())
        self.assertTrue(cancelled_play.is_set())
        self.assertTrue(cancelled_tts.is_set())
        self.play_audio.assert_called_once()
        self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in enumerate_threads()))

    def test_pending_audio_continues_while_terminal_write_is_blocked(self):
        second_finished = Event()
        played = []
        def play(path, **kwargs):
            played.append(path.name)
            if len(played) == 2:
                second_finished.set()
        self.play_audio.side_effect = play
        def terminal_print(*args, **kwargs):
            if args == ("Third sentence. ",):
                self.assertTrue(second_finished.wait(2))
        text = "First sentence. Second sentence. Third sentence. "
        events = stream_events(text)
        events[1:2] = [{"type": "delta", "text": "First sentence. Second sentence. "},
                      {"type": "delta", "text": "Third sentence. "}]
        with patch("builtins.print", side_effect=terminal_print):
            self.speak(events)
        self.assertEqual(played, ["stream-playback-001.wav", "stream-playback-002.wav", "stream-playback-003.wav"])

    def test_second_worker_start_failure_still_cancels_and_joins_first_worker(self):
        executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="voice-stream-audio")
        submissions = 0
        def submit(operation):
            nonlocal submissions
            submissions += 1
            if submissions == 2:
                raise RuntimeError("cannot start playback worker")
            return executor.submit(operation)
        wrapper = MagicMock()
        wrapper.submit.side_effect = submit
        wrapper.shutdown.side_effect = executor.shutdown
        with patch("scripts.voice_loop.ThreadPoolExecutor", return_value=wrapper):
            with self.assertRaisesRegex(RuntimeError, "cannot start playback worker"):
                self.speak(stream_events("An answer."))
        wrapper.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
        self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in enumerate_threads()))


class ResourceNoticeTests(SimpleTestCase):
    def test_filter_only_removes_initial_exact_notices_at_any_delta_boundary(self):
        prefixes = REDUCED_RESOURCE_STATUS + "\n\n" + CONSTRAINED_RESOURCE_STATUS + "\n\n"
        answer = "A real answer.\n\n" + REDUCED_RESOURCE_STATUS + " This is quoted background."
        text = prefixes + answer
        for width in range(1, len(text) + 1):
            with self.subTest(width=width):
                filter = voice_loop.LeadingResourceNoticeFilter()
                output, notices = [], []
                for offset in range(0, len(text), width):
                    part, removed = filter.feed(text[offset:offset + width])
                    output.append(part)
                    notices.extend(removed)
                part, removed = filter.feed("", final=True)
                output.append(part)
                notices.extend(removed)
                self.assertEqual("".join(output), answer)
                self.assertEqual(notices, [REDUCED_RESOURCE_STATUS, CONSTRAINED_RESOURCE_STATUS])

    def test_non_notice_text_and_whitespace_are_preserved(self):
        for text in ("\n Actual answer.", "Running in reduced-resource mode.X", "Running a program.", "Running"):
            with self.subTest(text=text):
                filter = voice_loop.LeadingResourceNoticeFilter()
                parts = [filter.feed(character)[0] for character in text]
                parts.append(filter.feed("", final=True)[0])
                self.assertEqual("".join(parts), text)

    @patch("assistant.services._log_request_performance")
    @patch("assistant.services._generation_metadata", return_value={})
    @patch("assistant.services._persist_generated_response")
    @patch("assistant.services._prepare_message")
    def test_service_adds_one_notice_and_second_canonical_copy_is_engine_generated(self, prepare, persist, metadata, log):
        prefix = REDUCED_RESOURCE_STATUS + "\n\n"
        model_text = prefix + "The actual answer."
        engine = MagicMock()
        result = AIEngineResult(text=model_text, engine="local", model="model", mode="offline", latency_ms=1)
        engine.generate_stream.return_value = [AIEngineStreamEvent(type="delta", text=model_text),
                                               AIEngineStreamEvent(type="done", result=result)]
        prepare.return_value = SimpleNamespace(
            conversation=SimpleNamespace(id=42), num_predict=64, engine=engine, history=[], system_instruction="",
            route_decision=SimpleNamespace(route=SimpleNamespace(value="local")),
            execution_plan=SimpleNamespace(resource_profile="ECO", status_message=REDUCED_RESOURCE_STATUS,
                                           effective_model="model", timeout_seconds=120))
        events = list(AssistantService.process_message_stream("question"))
        self.assertEqual([event["text"] for event in events if event["type"] == "delta"], [prefix, model_text])
        self.assertEqual(events[-1]["response"], prefix + model_text)
        self.assertEqual(persist.call_args.args[1], prefix + model_text)


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
