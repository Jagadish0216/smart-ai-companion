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


def stream_events(text, conversation_id=42, direct=False, **metadata):
    return [{"type": "start", "conversation_id": conversation_id, "direct": direct},
            {"type": "delta", "text": text},
            {"type": "done", "conversation_id": conversation_id, "response": text, **metadata}]


FIRST_CLAUSE = "Solar panels convert sunlight into electricity by using photovoltaic cells,"
# Hardware pipeline fixtures deliberately exceed the packing minimums so
# multi-unit worker/backpressure assertions still exercise real boundaries.
FIRST_SENTENCE = "The first complete sentence contains useful information and is long enough to start natural speech."
SECOND_SENTENCE = "The second complete sentence contains more useful information and enough connected words to form a natural post-first speech unit."
THIRD_SENTENCE = SECOND_SENTENCE.replace("second", "third")


class FirstClauseBufferTests(SimpleTestCase):
    def test_short_full_sentence_is_held_until_response_completion(self):
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed("The answer is 4. "), [])
        self.assertEqual(buffer.feed("", final=True), ["The answer is 4."])

    def test_tiny_first_sentence_combines_with_following_content(self):
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed("It's a mixed bag. "), [])
        following = "A small computer runs Linux and connects to hardware using GPIO pins."
        self.assertEqual(buffer.feed(following + " "), ["It's a mixed bag. " + following])

    def test_short_adjacent_sentences_keep_punctuation_in_one_final_unit(self):
        text = "It's a small computer. It runs Linux. It has GPIO pins."
        buffer = voice_loop.StreamingSpeechBuffer()
        for sentence in ("It's a small computer. ", "It runs Linux. ", "It has GPIO pins."):
            self.assertEqual(buffer.feed(sentence), [])
        self.assertEqual(buffer.feed("", final=True), [text])

    def test_available_adjacent_sentences_are_packed_before_first_release(self):
        text = "It's a small computer. It runs Linux. It has GPIO pins. It supports programming."
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed(text + " "), [text])
        self.assertEqual(buffer.feed("", final=True), [])

    def test_later_short_sentences_pack_into_normal_sized_units(self):
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed(FIRST_CLAUSE + " "), [FIRST_CLAUSE])
        self.assertEqual(buffer.feed("It runs Linux. It has GPIO pins. "), [])
        more = "It connects to a monitor and keyboard, supports programming, and fits many embedded projects. "
        expected = "It runs Linux. It has GPIO pins. " + more.strip()
        self.assertEqual(buffer.feed(more), [expected])
        self.assertTrue(120 <= len(expected) <= 220)

    def test_completed_unit_in_target_grace_window_is_not_split_into_tiny_jobs(self):
        buffer = voice_loop.StreamingSpeechBuffer()
        buffer.feed(FIRST_CLAUSE + " ")
        text = ("The computer uses a processor and memory to run programs and process incoming data. "
                "GPIO pins let it communicate with connected hardware such as sensors, switches and small displays.")
        self.assertTrue(180 < len(text) <= 220)
        self.assertEqual(buffer.feed(text + " "), [text])

    def test_obviously_incomplete_tail_requires_a_complete_sentence_and_playback_permission(self):
        for ending in ("The Raspberry Pi runs on a Linux-based", "It connects to the", "It provides power and"):
            with self.subTest(ending=ending):
                buffer = voice_loop.StreamingSpeechBuffer()
                self.assertEqual(buffer.feed(FIRST_SENTENCE + " "), [FIRST_SENTENCE])
                self.assertEqual(buffer.feed(ending), [])
                self.assertEqual(buffer.feed("", final=True, omit_incomplete_tail=True), [])
                buffer = voice_loop.StreamingSpeechBuffer()
                buffer.feed(FIRST_SENTENCE + " ")
                buffer.feed(ending)
                self.assertEqual(buffer.feed("", final=True), [ending])
                buffer = voice_loop.StreamingSpeechBuffer()
                buffer.feed(ending)
                self.assertEqual(buffer.feed("", final=True, omit_incomplete_tail=True), [ending])
        buffer = voice_loop.StreamingSpeechBuffer()
        buffer.feed(FIRST_CLAUSE + " ")  # A clause alone is not a complete useful sentence.
        buffer.feed("which are")
        self.assertEqual(buffer.feed("", final=True, omit_incomplete_tail=True), ["which are"])

    def test_useful_unpunctuated_tail_is_not_discarded(self):
        for text in ("The name is Blue Falcon", "The system is Linux-based", "That is what it is for",
                     "The indicator is switched on", "Yes, you can"):
            with self.subTest(text=text):
                buffer = voice_loop.StreamingSpeechBuffer()
                buffer.feed(FIRST_SENTENCE + " ")
                buffer.feed(text)
                self.assertEqual(buffer.feed("", final=True, omit_incomplete_tail=True), [text])

    def test_first_long_sentence_emits_a_useful_comma_clause_before_completion(self):
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed(FIRST_CLAUSE[:-1]), [])
        self.assertEqual(buffer.feed(", "), [FIRST_CLAUSE])
        self.assertTrue(buffer.first_unit_emitted)
        self.assertGreaterEqual(len(FIRST_CLAUSE), 60)

    def test_semicolon_and_colon_are_supported(self):
        for punctuation in (";", ":"):
            with self.subTest(punctuation=punctuation):
                clause = FIRST_CLAUSE[:-1] + punctuation
                buffer = voice_loop.StreamingSpeechBuffer()
                self.assertEqual(buffer.feed(clause + " "), [clause])

    def test_punctuation_before_minimum_does_not_emit_a_tiny_fragment(self):
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed("In short, "), [])
        self.assertEqual(buffer.feed(FIRST_CLAUSE + " "), ["In short, " + FIRST_CLAUSE])

    def test_latest_available_clause_in_target_grace_window_is_preferred(self):
        text = "This explanation begins with enough meaningful words to describe the process, with useful details; and context:"
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(len(text), 111)
        self.assertEqual(buffer.feed(text + " a much more distant clause with several words, "), [text])

    def test_complete_sentence_before_target_wins_over_clause(self):
        sentence = FIRST_CLAUSE + " safely."
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertLess(len(sentence), 100)
        self.assertEqual(buffer.feed(sentence + " "), [sentence])

    def test_long_completed_sentence_can_use_shorter_first_clause(self):
        remainder = "which are made of semiconductor materials that respond to incoming sunlight."
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed(FIRST_CLAUSE + " " + remainder + " "), [FIRST_CLAUSE])
        self.assertEqual(buffer.feed("", final=True), [remainder])

    def test_boundary_waits_for_lookahead_and_never_cuts_words_at_target(self):
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed(FIRST_CLAUSE), [])
        self.assertEqual(buffer.feed(" which"), [FIRST_CLAUSE])
        buffer = voice_loop.StreamingSpeechBuffer()
        words = "usefulword " * 10
        self.assertEqual(buffer.feed(words), [("usefulword " * 9).strip()])
        self.assertEqual(buffer.feed("", final=True), ["usefulword"])

    def test_later_sentences_do_not_release_small_clauses(self):
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed(FIRST_CLAUSE + " "), [FIRST_CLAUSE])
        continuation = "which use semiconductor materials to transfer absorbed energy into current,"
        self.assertEqual(buffer.feed(continuation + " "), [])
        self.assertEqual(buffer.feed("rather than heat alone. "), [])
        self.assertEqual(buffer.feed("", final=True), [continuation + " rather than heat alone."])

    def test_link_labels_urls_and_markdown_length_are_protected(self):
        buffer = voice_loop.StreamingSpeechBuffer()
        prefix = "Solar panels convert sunlight into energy as described in "
        link = "[the local guide, section one](https://guide.test/ref)"
        self.assertEqual(buffer.feed(prefix + link), [])
        expected = prefix + "the local guide, section one,"
        self.assertEqual(buffer.feed(", "), [expected])
        buffer = voice_loop.StreamingSpeechBuffer()
        tiny = "A guide says [short](https://example.test/" + "x" * 80 + "), "
        self.assertLess(len(tiny), 180)
        self.assertEqual(buffer.feed(tiny), [])  # A long URL is not useful spoken content.
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed("**" + FIRST_CLAUSE + "** "), [FIRST_CLAUSE])

    def test_parentheses_and_quotes_avoid_inside_clause_boundaries(self):
        for group in ("(which are described, in the local guide)", '"which are described, in the local guide"',
                      "“which are described, in the local guide”"):
            with self.subTest(group=group):
                buffer = voice_loop.StreamingSpeechBuffer(first_target_chars=160)
                prefix = FIRST_CLAUSE[:-1] + " "
                inside = group.index(",") + 2
                self.assertEqual(buffer.feed(prefix + group[:inside]), [])
                self.assertEqual(buffer.feed(group[inside:] + ", "), [prefix + group + ","])

    def test_apostrophes_do_not_disable_a_safe_clause(self):
        clause = "The panel's specialised cells convert absorbed sunlight into electrical energy,"
        self.assertEqual(voice_loop.StreamingSpeechBuffer().feed(clause + " "), [clause])

    def test_decimal_time_and_numeric_separators_are_not_split(self):
        clause = "Solar panels convert sunlight into energy at a measured output of 3.14 volts,"
        self.assertEqual(voice_loop.StreamingSpeechBuffer().feed(clause + " "), [clause])
        buffer = voice_loop.StreamingSpeechBuffer()
        numeric = "The measured electrical result has a ratio expressed in units as 12: 30"
        self.assertEqual(buffer.feed(numeric), [])

    def test_titles_and_dotted_abbreviations_do_not_create_first_fragments(self):
        for abbreviation in ("Dr.", "Prof.", "U.S.", "e.g."):
            with self.subTest(abbreviation=abbreviation):
                buffer = voice_loop.StreamingSpeechBuffer()
                self.assertEqual(buffer.feed(abbreviation + " "), [])
                rest = "Smith explains how photovoltaic cells make electricity from absorbed sunlight,"
                self.assertEqual(buffer.feed(rest + " "), [abbreviation + " " + rest])

    def test_unpunctuated_fallback_and_text_order_are_preserved(self):
        text = " ".join("word" + str(index) for index in range(80))
        buffer = voice_loop.StreamingSpeechBuffer()
        units = buffer.feed(text)
        self.assertTrue(units)
        units.extend(buffer.feed("", final=True))
        self.assertEqual(" ".join(units), text)
        for width in (1, 3, 17, 150):
            buffer = voice_loop.StreamingSpeechBuffer()
            text = FIRST_CLAUSE + " which become useful components in renewable power systems. Next, more detail follows."
            units = []
            for index in range(0, len(text), width):
                units.extend(buffer.feed(text[index:index + width]))
            units.extend(buffer.feed("", final=True))
            self.assertEqual(" ".join(units), text)

    @override_settings(VOICE_AUDIO_BACKEND="pulse", VOICE_INPUT_SOURCE="source",
                       VOICE_STREAM_FIRST_CHUNK_MIN_CHARS="65", VOICE_STREAM_FIRST_CHUNK_TARGET_CHARS="110")
    def test_first_chunk_settings_and_validation(self):
        config = voice_loop.VoiceLoopConfig.from_settings()
        self.assertEqual(config.stream_first_chunk_min_chars, 65)
        self.assertEqual(config.stream_first_chunk_target_chars, 110)
        for minimum, target in ((0, 100), (-1, 100), (60, 59)):
            with self.subTest(minimum=minimum, target=target), self.assertRaises(voice_loop.VoiceLoopError):
                voice_loop.VoiceLoopConfig("source", stream_first_chunk_min_chars=minimum,
                                          stream_first_chunk_target_chars=target).validate()


class FirstPhraseFallbackTests(SimpleTestCase):
    def test_punctuation_free_first_unit_releases_near_target_before_sentence_completes(self):
        text = ("An ESP32 is a programmable microcontroller offering wireless connectivity for embedded projects "
                "across many environments")
        buffer = voice_loop.StreamingSpeechBuffer()
        units = []
        released_at = None
        for index, character in enumerate(text):
            part = buffer.feed(character)
            if part and released_at is None:
                released_at = index + 1
            units.extend(part)
        self.assertIsNotNone(released_at)
        self.assertLessEqual(released_at, 120)
        self.assertTrue(60 <= len(units[0]) <= 120)
        self.assertLess(len(units[0]), len(text))
        units.extend(buffer.feed("", final=True))
        self.assertEqual(" ".join(units), text)

    def test_completed_natural_connectors_precede_whitespace_fallback(self):
        prefix = "An ESP32 is a compact microcontroller with integrated wireless connectivity"
        for connector in ("because", "due to", "while", "but", "so", "which", "that"):
            with self.subTest(connector=connector):
                buffer = voice_loop.StreamingSpeechBuffer()
                self.assertEqual(buffer.feed(prefix + " " + connector + " useful details follow later"), [prefix])
                self.assertEqual(buffer.feed("", final=True), [connector + " useful details follow later"])

    def test_partial_connector_is_not_mistaken_for_a_complete_word(self):
        prefix = "An ESP32 is a compact microcontroller with integrated wireless connectivity"
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed(prefix + " becau"), [])
        self.assertEqual(buffer.feed("se"), [])
        self.assertEqual(buffer.feed(" it supports wireless projects"), [prefix])

    def test_punctuation_wins_over_phrase_and_completed_sentence_wins_over_clause(self):
        for punctuation in (",", ";", ":"):
            with self.subTest(punctuation=punctuation):
                clause = FIRST_CLAUSE[:-1] + punctuation
                text = clause + " because it supports useful applications and more details"
                self.assertEqual(voice_loop.StreamingSpeechBuffer().feed(text), [clause])
        sentence = FIRST_CLAUSE + " safely."
        self.assertEqual(voice_loop.StreamingSpeechBuffer().feed(sentence + " because further detail follows"), [sentence])

    def test_fallback_does_not_split_a_word_at_the_target(self):
        prefix = "An ESP32 is a compact microcontroller with integrated wireless connectivity"
        long_word = "extraordinarymicrocontrollerapplications"
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed(prefix + " " + long_word + " later"), [prefix])
        self.assertEqual(buffer.feed("", final=True), [long_word + " later"])

    def test_whitespace_fallback_backs_up_before_dangling_tokens(self):
        prefix = "The controller supports wireless networking and reliable operation in embedded systems"
        for token in ("a", "an", "the", "and", "or", "to", "of", "for", "in", "on", "with", "across"):
            with self.subTest(token=token):
                buffer = voice_loop.StreamingSpeechBuffer()
                self.assertTrue(60 <= len(prefix) < 100)
                text = prefix + " " + token + " extraordinarymicrocontrollerapplications continue"
                self.assertEqual(buffer.feed(text), [prefix])
                self.assertEqual(" ".join([prefix] + buffer.feed("", final=True)), text)

    def test_protected_groups_cannot_trigger_an_internal_phrase_or_word_split(self):
        prefix = "The controller provides useful functions "
        inside = "wireless networking because useful devices communicate " * 5
        for opening, closing in (("(", ")"), ("[", "]"), ("{", "}"), ('"', '"'), ("“", "”")):
            with self.subTest(opening=opening):
                buffer = voice_loop.StreamingSpeechBuffer()
                text = prefix + opening + inside.strip() + closing
                self.assertEqual(buffer.feed(text[:-1]), [])
                self.assertEqual(buffer.feed(text[-1]), [])
                self.assertEqual(buffer.feed("", final=True), [text])

    def test_markdown_link_label_and_url_remain_one_protected_first_unit(self):
        prefix = "The controller provides useful functions "
        label = "wireless networking because useful devices communicate " * 4
        link = "[" + label.strip() + "](https://example.test/guide)"
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed(prefix + link[:-4]), [])
        self.assertEqual(buffer.feed(link[-4:]), [])
        self.assertEqual(buffer.feed("", final=True), [prefix + label.strip()])

    def test_bare_url_and_formatted_number_cannot_be_split_in_the_first_unit(self):
        prefix = "The controller provides useful functions "
        url = "https://example.test/" + "long-path-" * 14
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed(prefix + url + " "), [])
        self.assertEqual(buffer.feed("", final=True), [prefix + url])
        prefix = "The controller reports measurements for connected embedded devices"
        for number in ("1 000 000 000 000 000 000 000", "12: 30", "1, 234, 567", "3. 14159"):
            with self.subTest(number=number):
                text = prefix + " " + number + " measurements are available for review"
                buffer = voice_loop.StreamingSpeechBuffer()
                units = buffer.feed(text)
                self.assertTrue(units)
                # A whole number may fit, but no first unit may end partway through it.
                self.assertTrue(units[0] == prefix or number in units[0])
                units.extend(buffer.feed("", final=True))
                self.assertEqual(" ".join(units), text)

    def test_new_fallback_never_releases_a_tiny_prefix_to_escape_a_long_word(self):
        text = "A tiny start " + "unbrokenword" * 24
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed(text), [])
        self.assertEqual(buffer.feed("", final=True), [text])

    def test_closed_markdown_label_waits_for_url_in_next_delta(self):
        prefix = "The controller provides useful functions "
        label = "a detailed reference for wireless networking applications " * 3 + "."
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed(prefix + "[" + label + "]"), [])
        self.assertEqual(buffer.feed("(https://example.test/guide)"), [])
        self.assertEqual(buffer.feed("", final=True), [prefix + label])

    def test_later_units_keep_normal_target_after_first_phrase_fallback(self):
        prefix = "An ESP32 is a compact microcontroller with integrated wireless connectivity"
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed(prefix + " because "), [prefix])
        continuation = "the application uses programmable devices to provide reliable functions for connected systems"
        self.assertEqual(buffer.feed(continuation + " "), [])
        self.assertEqual(buffer.feed("", final=True), ["because " + continuation])


class StreamingBufferTests(SimpleTestCase):
    def test_multiple_deltas_wait_for_one_completed_sentence(self):
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed("A natural"), [])
        self.assertEqual(buffer.feed(" completed sentence"), [])
        self.assertEqual(buffer.feed(". "), [])
        self.assertEqual(buffer.feed("", final=True), ["A natural completed sentence."])

    def test_split_closing_quote_bracket_and_markdown_stay_with_sentence(self):
        buffer = voice_loop.StreamingSpeechBuffer()
        self.assertEqual(buffer.feed('He said "**Hello.'), [])
        self.assertEqual(buffer.feed('**") '), [])
        self.assertEqual(buffer.feed("Another fact! "), [])
        self.assertEqual(buffer.feed("", final=True), ['He said "Hello.") Another fact!'])

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
        # Mock consumption of a raw file, not a per-chunk aplay invocation.
        self.stream_write = MagicMock()
        self.player = MagicMock()
        patcher = patch("scripts.voice_loop.PersistentAlsaPlayer")
        self.player_factory = patcher.start()
        self.addCleanup(patcher.stop)
        def factory(device, stopped, temp_path):
            self.player.write_file.side_effect = lambda path: self.stream_write(path, device, cancel_event=stopped)
            return self.player
        self.player_factory.side_effect = factory
        def trim(source, destination, **kwargs):
            destination.write_bytes(source.read_bytes() if source.exists() else self.audio)
            return 0, 0
        patcher = patch("scripts.voice_loop.trim_pcm_edges", side_effect=trim)
        patcher.start()
        self.addCleanup(patcher.stop)

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
            yield {"type": "delta", "text": FIRST_SENTENCE[:10]}
            yield {"type": "delta", "text": FIRST_SENTENCE[10:] + " "}
            self.assertTrue(playback_started.wait(2))
            generation_continued.set()
            yield {"type": "delta", "text": "Second sentence. Last unfinished thought"}
            yield {"type": "done", "conversation_id": 72,
                   "response": FIRST_SENTENCE + " Second sentence. Last unfinished thought",
                   "resource_profile": "ECO", "num_predict": 64}
        conversation_id, metadata = self.speak(events())
        self.assertEqual(conversation_id, 72)
        self.assertEqual(metadata["num_predict"], 64)
        self.assertEqual(self.timings["num_predict"], 64)
        self.assertEqual(self.provider.synthesize.call_args_list,
                         [call(FIRST_SENTENCE), call("Second sentence."), call("Last unfinished thought")])
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

    def test_first_clause_metrics_exclude_notice_and_precede_done_without_delaying_terminal(self):
        clock = [0.0]
        first_started, allow_synthesis = Event(), Event()
        output = io.StringIO()
        prefix = REDUCED_RESOURCE_STATUS + "\n\n"
        clause = "**" + FIRST_CLAUSE + "** "
        remainder = "which use semiconducting materials."
        def synthesize(text):
            if text == FIRST_CLAUSE:
                first_started.set()
                if not allow_synthesis.wait(2):
                    raise RuntimeError("test synthesis was not released")
            return self.audio
        self.provider.synthesize.side_effect = synthesize
        def events():
            yield {"type": "start", "direct": False, "conversation_id": 42}
            clock[0] = 0.1
            yield {"type": "delta", "text": prefix}
            self.assertNotIn("first_tts_unit_ready", self.timings)
            self.assertNotIn("first_tts_text_ready", self.timings)
            self.assertNotIn("first_audio_ready", self.timings)
            self.assertNotIn("first_tts_chars", self.timings)
            clock[0] = 0.2
            yield {"type": "delta", "text": clause[:-5]}
            self.assertIn("Assistant: " + clause[:-5], output.getvalue())
            self.assertFalse(self.provider.synthesize.called)
            clock[0] = 0.5
            yield {"type": "delta", "text": clause[-5:]}
            self.assertTrue(first_started.wait(2))
            self.assertEqual(self.timings["first_tts_unit_ready"], 0.5)
            self.assertEqual(self.timings["first_tts_text_ready"], 0.5)
            self.assertNotIn("first_audio_ready", self.timings)  # Piper is still blocked.
            self.assertEqual(self.timings["first_tts_chars"], len(FIRST_CLAUSE))
            self.assertIn("Assistant: " + clause, output.getvalue())
            clock[0] = 3.0
            allow_synthesis.set()
            yield {"type": "delta", "text": remainder}
            yield {"type": "done", "conversation_id": 42, "response": prefix + clause + remainder}
        try:
            with redirect_stdout(output), patch("scripts.voice_loop.time.perf_counter", side_effect=lambda: clock[0]):
                metadata = self.speak(events())[1]
        finally:
            allow_synthesis.set()
        self.assertEqual(self.provider.synthesize.call_args_list, [call(FIRST_CLAUSE), call(remainder)])
        self.assertEqual(self.timings["ai_ttft"], 0.2)
        self.assertEqual(self.timings["ai_total"], 3.0)
        self.assertLess(self.timings["first_tts_unit_ready"], self.timings["ai_total"])
        self.assertEqual(self.timings["first_audio_ready"], 3.0)
        self.assertLess(self.timings["first_tts_text_ready"], self.timings["first_audio_ready"])
        self.assertEqual(metadata["response"], prefix + clause + remainder)
        self.assertEqual(output.getvalue(), f"Status: {REDUCED_RESOURCE_STATUS}\nAssistant: " + clause + remainder + "\nTTS chunks: 2\n")

    def test_first_audio_readiness_waits_for_alsa_preparation_not_just_synthesis(self):
        clock = [0.0]
        prep_started, allow_preparation = Event(), Event()

        def prepare(*args, **kwargs):
            self.assertEqual(self.timings["first_tts_text_ready"], 0.5)
            self.assertNotIn("first_audio_ready", self.timings)
            self.provider.synthesize.assert_called_once()
            prep_started.set()
            if not allow_preparation.wait(2):
                raise RuntimeError("test preparation was not released")

        self.prepare_alsa_playback.side_effect = prepare

        def events():
            yield {"type": "start", "conversation_id": 42}
            clock[0] = 0.5
            yield {"type": "delta", "text": FIRST_CLAUSE + " "}
            self.assertTrue(prep_started.wait(2))
            self.assertNotIn("first_audio_ready", self.timings)
            clock[0] = 2.0
            allow_preparation.set()
            yield {"type": "done", "conversation_id": 42, "response": FIRST_CLAUSE + " "}

        try:
            with patch("scripts.voice_loop.time.perf_counter", side_effect=lambda: clock[0]):
                self.speak(events(), config=voice_loop.VoiceLoopConfig("", audio_backend="alsa"))
        finally:
            allow_preparation.set()
        self.assertEqual(self.timings["first_tts_text_ready"], 0.5)
        self.assertEqual(self.timings["first_tts_unit_ready"], 0.5)
        self.assertEqual(self.timings["first_audio_ready"], 2.0)
        self.assertGreaterEqual(self.timings["ai_to_first_speech"], self.timings["first_audio_ready"])

    def test_pipeline_uses_configured_first_minimum_and_target(self):
        first = "This explanation begins with enough meaningful words to describe the process, with useful details;"
        config = voice_loop.VoiceLoopConfig("source", stream_first_chunk_min_chars=90, stream_first_chunk_target_chars=110)
        self.speak(stream_events(first + " and continues with the final thought."), config)
        self.assertEqual(self.provider.synthesize.call_args_list[0], call(first))
        self.assertEqual(self.timings["first_tts_chars"], len(first))

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
        self.speak(stream_events(FIRST_SENTENCE + " " + SECOND_SENTENCE), config)
        self.assertEqual(self.provider.synthesize.call_args_list, [call(FIRST_SENTENCE), call(SECOND_SENTENCE)])

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
                yield {"type": "delta", "text": FIRST_SENTENCE + " "}
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
        self.assertEqual(self.provider.synthesize.call_args_list[0], call(FIRST_SENTENCE))
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
        self.assertLess(self.provider.synthesize.call_count, 30)
        self.assertEqual(" ".join(item.args[0] for item in self.provider.synthesize.call_args_list),
                         ("A completed sentence. " * 30).strip())
        self.assertEqual(len(set(threads)), 1)

    def test_alsa_first_silence_conversion_and_speaking_order(self):
        inputs, order = [], []
        def convert(path, output, gain, **kwargs):
            inputs.append(path.read_bytes())
            self.assertEqual(gain, 4.0)
            order.append("convert")
        self.prepare_alsa_playback.side_effect = convert
        self.stream_write.side_effect = lambda *args, **kwargs: order.append("play")
        config = voice_loop.VoiceLoopConfig("", audio_backend="alsa", leading_silence_seconds=0.5)
        with tempfile.TemporaryDirectory() as directory, patch("scripts.voice_loop.time.sleep") as sleep:
            voice_loop.speak_stream_response(stream_events(FIRST_SENTENCE + " " + SECOND_SENTENCE), self.provider,
                                            Path(directory), config, {}, voice_loop.time.perf_counter(),
                                            on_playback_start=lambda: order.append("SPEAKING"))
        self.assertEqual(order.count("SPEAKING"), 1)
        self.assertEqual(order.count("play"), 2)
        self.assertLess(order.index("convert"), order.index("SPEAKING"))
        self.assertEqual([event for event in order if event != "convert"], ["SPEAKING", "play", "play"])
        sleep.assert_not_called()
        self.assertEqual(inputs[0], self.audio)  # Startup zeros now belong to the persistent player.
        self.assertEqual(self.player.write_silence.call_args_list, [call(24000), call(5760)])
        self.assertEqual(inputs[1], self.audio)
        self.play_audio.assert_not_called()
        for playback in self.stream_write.call_args_list:
            self.assertEqual(playback.args[1], "hw:CARD=sndrpigooglevoi,DEV=0")

    def test_actual_alsa_commands_use_cancellable_subprocess_path(self):
        config = voice_loop.VoiceLoopConfig("", audio_backend="alsa")
        self.prepare_alsa_playback.side_effect = REAL_PREPARE_ALSA
        def execute(command, **kwargs):
            if command[0] == "ffmpeg":
                Path(command[-1]).write_bytes(self.audio)
            return subprocess.CompletedProcess(command, 0, b"", b"")
        with patch("scripts.voice_loop._run_audio_subprocess") as run:
            run.side_effect = execute
            self.speak(stream_events("An answer."), config)
        conversion, = run.call_args_list
        command = conversion.args[0]
        self.assertEqual(command[:3], ["ffmpeg", "-y", "-i"])
        self.assertEqual(command[4:-1], ["-af", "volume=4.0,alimiter=limit=0.98:level=false",
                                        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s32le", "-f", "s32le"])
        self.player_factory.assert_called_once()
        conversion_event = self.prepare_alsa_playback.call_args.kwargs["cancel_event"]
        self.assertIs(self.stream_write.call_args.kwargs["cancel_event"], conversion_event)
        self.assertIs(conversion.kwargs["cancel_event"], conversion_event)

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
                if text == SECOND_SENTENCE:
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
        self.stream_write.side_effect = play
        config = voice_loop.VoiceLoopConfig("", audio_backend="alsa", leading_silence_seconds=0.5)
        with patch("scripts.voice_loop.Queue", side_effect=queue_factory), patch("scripts.voice_loop.time.sleep") as sleep:
            self.speak(stream_events(FIRST_SENTENCE + " " + SECOND_SENTENCE + " " + THIRD_SENTENCE), config)
        sleep.assert_not_called()
        self.assertEqual(played_paths, ["stream-speaker-001.pcm", "stream-speaker-002.pcm", "stream-speaker-003.pcm"])
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
                self.speak(stream_events("A completed sentence. " * 100))
        self.assertTrue(full_audio.is_set())
        self.assertTrue(full_text.is_set())
        self.play_audio.assert_called_once()
        self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in enumerate_threads()))

    def test_preparation_or_tts_failure_stops_active_playback_and_keeps_root_error(self):
        for stage in ("tts", "prepare"):
            with self.subTest(stage=stage):
                self.provider.reset_mock()
                self.stream_write.reset_mock()
                self.prepare_alsa_playback.reset_mock()
                active = Event()
                cancelled = Event()
                def fail_after_playback():
                    self.assertTrue(active.wait(2))
                    raise voice_loop.VoiceLoopError("original " + stage + " failure")
                def synthesize(text):
                    if stage == "tts" and text == SECOND_SENTENCE:
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
                self.stream_write.side_effect = play
                config = voice_loop.VoiceLoopConfig("", audio_backend="alsa")
                with self.assertRaisesRegex(voice_loop.VoiceLoopError, "original " + stage + " failure"):
                    self.speak(stream_events(FIRST_SENTENCE + " " + SECOND_SENTENCE), config)
                self.assertTrue(cancelled.is_set())
                self.stream_write.assert_called_once()
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
        self.assertEqual(output.getvalue(), "Assistant: " + "".join(fragments) + "\nTTS chunks: 1\n")
        for fragment in fragments:
            self.assertIn(call(fragment, end="", flush=True), print_calls.call_args_list)
        self.assertEqual(output.getvalue().count("Assistant: "), 1)

    def test_direct_terminal_output_preserves_newlines_and_is_not_duplicated(self):
        text = "Please clarify.\nWhich device?"
        output = io.StringIO()
        with redirect_stdout(output):
            self.speak(stream_events(text, direct=True))
        self.assertEqual(output.getvalue(), "Assistant: " + text + "\nTTS chunks: 1\n")

    def test_incomplete_tail_is_speech_only_after_complete_sentence_playback(self):
        consumed = Event()
        class CompletionTimings(dict):
            def __setitem__(timings, key, value):
                super().__setitem__(key, value)
                if key == "playback_chunks" and value == 1:
                    consumed.set()
        self.timings = CompletionTimings()
        tail = "The Raspberry Pi runs on a Linux-based"
        canonical = FIRST_SENTENCE + " " + tail
        def events():
            yield {"type": "start", "conversation_id": 42, "direct": False}
            yield {"type": "delta", "text": FIRST_SENTENCE + " "}
            self.assertTrue(consumed.wait(2))
            yield {"type": "delta", "text": tail}
            yield {"type": "done", "conversation_id": 42, "response": canonical}
        output = io.StringIO()
        with redirect_stdout(output):
            done = self.speak(events())[1]
        self.provider.synthesize.assert_called_once_with(FIRST_SENTENCE)
        self.assertEqual(done["response"], canonical)
        self.assertEqual(output.getvalue(), "Assistant: " + canonical + "\nTTS chunks: 1\n")

    def test_short_multi_sentence_answer_uses_one_piper_call(self):
        text = "It's a small computer. It runs Linux. It has GPIO pins."
        self.speak(stream_events(text))
        self.provider.synthesize.assert_called_once_with(text)

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
        self.stream_write.side_effect = play
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
                if text == SECOND_SENTENCE:
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
            yield {"type": "delta", "text": FIRST_SENTENCE + " " + SECOND_SENTENCE + " "}
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
            if args == (THIRD_SENTENCE + " ",):
                self.assertTrue(second_finished.wait(2))
        text = FIRST_SENTENCE + " " + SECOND_SENTENCE + " " + THIRD_SENTENCE + " "
        events = stream_events(text)
        events[1:2] = [{"type": "delta", "text": FIRST_SENTENCE + " " + SECOND_SENTENCE + " "},
                      {"type": "delta", "text": THIRD_SENTENCE + " "}]
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
            conversation=SimpleNamespace(id=42), num_predict=64, engine=engine, history=[], system_instruction="", is_voice=False,
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
    @patch("assistant.services.get_engine")
    def test_first_phrase_fallback_never_changes_canonical_generated_or_persisted_text(self, engine, play, tts):
        canonical = ("An ESP32 is a programmable microcontroller offering wireless connectivity for embedded projects "
                     "across many environments.")
        first_synthesis = Event()
        provider = tts.return_value
        original_synthesize = provider.synthesize

        def synthesize(text):
            first_synthesis.set()
            return original_synthesize(text)

        def generate(query, **kwargs):
            yield AIEngineStreamEvent("delta", text=canonical[:110])
            self.assertTrue(first_synthesis.wait(2))  # Speakable text precedes model completion.
            yield AIEngineStreamEvent("delta", text=canonical[110:])
            yield AIEngineStreamEvent("done", result=AIEngineResult(text=canonical, engine="mock", latency_ms=1))

        engine.return_value.generate_stream.side_effect = generate
        with patch.object(provider, "synthesize", side_effect=synthesize) as synthesis, redirect_stdout(io.StringIO()) as terminal:
            conversation_id = voice_loop.run_voice_cycle(voice_loop.VoiceLoopConfig("source"), text="What is an ESP32?")
        spoken = [item.args[0] for item in synthesis.call_args_list]
        self.assertTrue(60 <= len(spoken[0]) <= 120)
        self.assertGreater(len(spoken), 1)
        self.assertEqual(" ".join(spoken), canonical)
        self.assertIn("Assistant: " + canonical, terminal.getvalue())
        self.assertEqual(Message.objects.get(conversation_id=conversation_id, sender="AI").text, canonical)
        self.assertEqual(Message.objects.get(conversation_id=conversation_id, sender="USER").text, "What is an ESP32?")
        engine.return_value.generate_stream.assert_called_once()

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
