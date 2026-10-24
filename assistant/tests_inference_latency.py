"""Inference trials preserve defaults, grounding and streaming boundaries."""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from assistant.ai_engine import get_engine, reset_engine
from assistant.ai_engine.local import LocalLLMEngine
from assistant.policy import AssistantResponsePolicy, VOICE_SAFETY_INSTRUCTION
from scripts.voice_loop import StreamingSpeechBuffer, VoiceLoopConfig, VoiceLoopError


class CpuOptionTests(SimpleTestCase):
    def test_unset_cpu_options_preserve_backend_defaults(self):
        engine = LocalLLMEngine(num_ctx=4096, num_predict=64)
        options = json.loads(engine._build_chat_request([], stream=True).data)["options"]
        self.assertEqual(options, {"num_ctx": 4096, "num_predict": 64})

    @override_settings(AI_ENGINE="local", OLLAMA_NUM_THREAD=3, OLLAMA_NUM_BATCH=128)
    def test_factory_batch_stream_and_warm_use_same_explicit_cpu_options(self):
        reset_engine()
        self.addCleanup(reset_engine)
        engine = get_engine()
        for stream in (False, True):
            options = json.loads(engine._build_chat_request([], stream=stream).data)["options"]
            self.assertEqual((options["num_thread"], options["num_batch"]), (3, 128))
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"done":true}'
        with patch("assistant.ai_engine.local.urllib.request.urlopen", return_value=response) as request:
            engine.warm_model()
        self.assertEqual(json.loads(request.call_args.args[0].data)["options"], {
            "num_ctx": engine._num_ctx, "num_thread": 3, "num_batch": 128,
        })

    def test_invalid_cpu_options_fail_without_network(self):
        for name, values in (("num_thread", [0, -1, 65, True, 2.5, "4"]),
                             ("num_batch", [0, -1, 2049, True, "128"])):
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    LocalLLMEngine(**{name: value})


class VoicePolicyLatencyTests(SimpleTestCase):
    def test_nonaction_planning_has_no_capability_database_probe(self):
        with patch("assistant.policy.CapabilityRegistry.from_settings", side_effect=AssertionError("redundant probe")):
            for query in ("What is RAM?", "Briefly define a CPU.", "Explain a CPU in detail.", "Do it again."):
                AssistantResponsePolicy.plan_voice_response(query)

    def test_action_planning_still_uses_availability_and_refuses(self):
        with patch("assistant.policy.CapabilityRegistry.from_settings") as factory:
            factory.return_value.is_available.return_value = False
            factory.return_value.status.return_value.unavailable_response = "Unavailable."
            result = AssistantResponsePolicy.plan_voice_response("Turn the LED on")
        factory.assert_called_once_with()
        self.assertEqual(result.direct_response, "Unavailable.")

    @override_settings(VOICE_COMPACT_STYLE=True)
    def test_compact_style_retains_format_priority_and_does_not_edit_safety(self):
        result = AssistantResponsePolicy.plan_voice_response("What is RAM?")
        self.assertIn("Explicit format requests take priority", result.system_instruction)
        self.assertLess(len(result.system_instruction), 200)
        self.assertIn("explicit results supplied for this request", VOICE_SAFETY_INSTRUCTION)
        self.assertEqual(result.recent_history_limit, 0)
        self.assertFalse(result.allow_implicit_rag)


class FirstSentenceLatencyTests(SimpleTestCase):
    def test_accepted_default_holds_short_sentence_but_trial_releases_it(self):
        sentence = "RAM holds data used by running programs."
        self.assertEqual(StreamingSpeechBuffer().feed(sentence + " "), [])
        trial = StreamingSpeechBuffer(first_sentence_min_chars=30)
        self.assertEqual(trial.feed(sentence + " "), [sentence])
        tail = "Stored files remain on the disk."
        self.assertEqual(trial.feed(tail), [])
        self.assertEqual(trial.feed("", final=True), [tail])

    def test_trial_never_lowers_clause_or_partial_word_minimum(self):
        trial = StreamingSpeechBuffer(first_sentence_min_chars=30)
        self.assertEqual(trial.feed("RAM stores useful data, "), [])
        self.assertEqual(trial.feed("which programs use"), [])
        self.assertEqual(trial.feed("", final=True), ["RAM stores useful data, which programs use"])

    def test_abbreviations_numbers_and_quotes_remain_protected(self):
        trial = StreamingSpeechBuffer(first_sentence_min_chars=30)
        text = 'Dr. Smith measured a voltage of 3.3 volts in "the device'
        self.assertEqual(trial.feed(text), [])
        self.assertEqual(trial.feed(' during operation." '), [text + ' during operation."'])

    def test_sentence_override_validation_and_disabled_default(self):
        self.assertIsNone(VoiceLoopConfig("source").stream_first_sentence_min_chars)
        for value in (0, 19, 61, True):
            with self.subTest(value=value), self.assertRaises(VoiceLoopError):
                VoiceLoopConfig("source", stream_first_sentence_min_chars=value).validate()
