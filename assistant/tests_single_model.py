"""Pi Qwen single-model payload, voice context and explicit RAG contracts."""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from conversations.models import Conversation, Message
from knowledge_base.services import KnowledgeIngestionService
from .ai_engine import AIEngineResult, AIEngineStreamEvent, EngineUnavailableError, get_engine, reset_engine
from .ai_engine.local import LocalLLMEngine
from .execution_policy import build_execution_plan, get_ai_execution_plan
from .policy import AssistantResponsePolicy, build_runtime_capability_registry
from .routing import normalize_rag_query
from .services import AssistantService
from .test_support import install_resource_report

MODEL = "qwen3.5:4b-q4_K_M"
SETTINGS = dict(AI_ENGINE="local", OLLAMA_MODEL=MODEL, OLLAMA_THINK=False,
                OLLAMA_NUM_CTX=4096, OLLAMA_KEEP_ALIVE="24h", RAG_ENABLED=False,
                ONLINE_RETRIEVAL_ENABLED=False, DEVICE_CONTROL_ENABLED=False,
                VOICE_RECENT_MESSAGES=2, CONVERSATION_RECENT_MESSAGES=4,
                VOICE_LLM_BRIEF_NUM_PREDICT=48, VOICE_LLM_NORMAL_NUM_PREDICT=96,
                VOICE_LLM_DETAILED_NUM_PREDICT=192)


@override_settings(**SETTINGS, AI_LIGHTWEIGHT_MODEL="legacy:1b", RAG_MODEL="legacy:rag")
class SingleModelPayloadTests(SimpleTestCase):
    def test_factory_uses_django_settings_not_stale_environment_model(self):
        reset_engine()
        self.addCleanup(reset_engine)
        with patch.dict("os.environ", {"OLLAMA_MODEL": "legacy:1b", "OLLAMA_THINK": "true", "OLLAMA_NUM_CTX": "99999"}):
            engine = get_engine()
        for stream in (False, True):
            payload = json.loads(engine._build_chat_request(engine._build_messages("Hello", []), stream=stream).data)
            self.assertEqual(payload["model"], MODEL)
            self.assertIs(payload["think"], False)
            self.assertEqual(payload["options"]["num_ctx"], 4096)
            self.assertEqual(payload["keep_alive"], "24h")

    def test_context_and_thinking_are_configurable_without_changing_predict_budget(self):
        engine = LocalLLMEngine(model=MODEL, think=True, num_ctx=2048)
        payload = json.loads(engine._build_chat_request([], stream=True, num_predict=48).data)
        self.assertIs(payload["think"], True)
        self.assertEqual(payload["options"], {"num_ctx": 2048, "num_predict": 48})
        with self.assertRaises(ValueError):
            LocalLLMEngine(num_ctx=0)

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_streaming_and_batch_never_expose_backend_thinking(self, urlopen):
        response = MagicMock()
        response.__enter__.return_value = response
        payload = {"done": True, "message": {"thinking": "PRIVATE_REASONING", "content": "New Delhi."},
                   "eval_count": 9, "prompt_eval_count": 48, "prompt_eval_cached_count": 44}
        response.__iter__.return_value = [json.dumps(payload).encode()]
        response.read.return_value = json.dumps(payload).encode()
        urlopen.return_value = response
        engine = LocalLLMEngine(model=MODEL, think=False, num_ctx=4096)
        stream = list(engine.generate_stream("What is the capital of India?", num_predict=96))
        self.assertEqual(stream[-1].result.text, "New Delhi.")
        self.assertEqual(stream[-1].result.metrics["prompt_eval_cached_count"], 44)
        self.assertNotIn("PRIVATE_REASONING", str(stream))
        result = engine.generate("What is the capital of India?", num_predict=96)
        self.assertEqual(result.text, "New Delhi.")
        self.assertNotIn("PRIVATE_REASONING", str(result))
        for call in urlopen.call_args_list:
            request = json.loads(call.args[0].data)
            self.assertIs(request["think"], False)
            self.assertEqual(request["options"], {"num_predict": 96, "num_ctx": 4096})

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_both_warm_requests_use_same_context_and_thinking_controls(self, urlopen):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"done":true,"message":{"content":"Ready"}}'
        urlopen.return_value = response
        engine = LocalLLMEngine(model=MODEL, num_ctx=4096, think=False, keep_alive="24h")
        grounding = build_runtime_capability_registry(online_allowed=False).build_grounding_instruction()
        engine.warm_model()
        engine.warm_chat_prefix(system_instruction=grounding)
        requests = [json.loads(item.args[0].data) for item in urlopen.call_args_list]
        self.assertEqual(len(requests), 2)
        for payload in requests:
            self.assertEqual(payload["model"], MODEL)
            self.assertEqual(payload["options"]["num_ctx"], 4096)
            self.assertIs(payload["think"], False)
        stable = engine._build_messages("A different question", [], system_instruction=grounding)[0]["content"]
        self.assertEqual(requests[1]["messages"][0]["content"], stable)
        self.assertLess(len(stable), 900)

    @patch("assistant.execution_policy.get_resource_manager_report")
    def test_all_profiles_use_configured_model_without_inventory_or_switching(self, report):
        for profile, budget, allowed, cap in (("PERFORMANCE", "NORMAL", True, 128),
                                             ("BALANCED", "NORMAL", True, 128),
                                             ("ECO", "REDUCED", True, 96),
                                             ("PROTECTIVE", "MINIMAL", False, 48)):
            with self.subTest(profile=profile):
                report.return_value = {"recommended_profile": profile, "ai_recommendation": {
                    "local_ai_allowed": allowed, "generation_budget": budget}}
                inventory = MagicMock(side_effect=AssertionError("No model selection inventory needed"))
                plan = get_ai_execution_plan(model_inventory_provider=inventory)
                inventory.assert_not_called()
                self.assertEqual(plan.effective_model, MODEL if allowed else None)
                self.assertEqual(plan.max_output_tokens, cap)
                self.assertEqual(plan.generation_allowed, allowed)


@override_settings(**SETTINGS)
class VoiceContextTests(TestCase):
    def setUp(self):
        install_resource_report(self)
        self.conversation = Conversation.objects.create()
        self.engine = MagicMock()
        self.engine.generate.side_effect = lambda query, **kwargs: AIEngineResult(
            text="A factual answer.", engine="local", model=kwargs["model"], latency_ms=1)
        self.engine.generate_stream.side_effect = lambda query, **kwargs: iter([
            AIEngineStreamEvent("delta", text="A factual answer."),
            AIEngineStreamEvent("done", result=AIEngineResult(
                text="A factual answer.", engine="local", model=kwargs["model"], latency_ms=1))])
        patcher = patch("assistant.services.get_engine", return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.old = Message.objects.create(conversation=self.conversation, sender="USER", text="Tell me about the old ESP32 assistant project.")
        for sender, text in (("USER", "What is MQTT?"), ("AI", "It is a messaging protocol."),
                             ("USER", "Explain Raspberry Pi."), ("AI", "It is a small computer.")):
            Message.objects.create(conversation=self.conversation, sender=sender, text=text)

    def request(self, query, voice=True):
        return list(AssistantService.process_message_stream(
            query, self.conversation.id,
            response_plan=AssistantResponsePolicy.plan_voice_response(query) if voice else None))[-1]

    def test_standalone_voice_topics_omit_recent_and_unrelated_old_memory(self):
        for query in ("What is ESP32?", "What is MQTT?", "Explain how Raspberry Pi works.",
                      "What is the capital of India?", "What is an ESP32 and how does it work?"):
            with self.subTest(query=query):
                done = self.request(query)
                self.assertEqual(self.engine.generate_stream.call_args.kwargs["conversation_history"], [])
                self.assertFalse(done["recent_history_used"])
                self.assertEqual(done["recent_history_messages"], 0)
                self.assertEqual(done["recent_history_chars"], 0)
                self.assertFalse(done["memory_used"])
                self.assertEqual(done["effective_model"], MODEL)

    def test_deictic_and_explicit_references_receive_only_two_recent_messages(self):
        for query in ("Tell me about it.", "How does that work?", "Why?", "What about its GPIO pins?",
                      "Can you explain that again?", "What did you mean by that?",
                      "Earlier you said it was small.", "As we discussed, explain MQTT.",
                      "What did I just tell you?"):
            with self.subTest(query=query):
                done = self.request(query)
                self.assertTrue(done["recent_history_used"])
                self.assertEqual(done["recent_history_messages"], 2)
                history = self.engine.generate_stream.call_args.kwargs["conversation_history"]
                self.assertNotIn(self.old.text, str(history))
                self.assertGreater(done["recent_history_chars"], 0)

    def test_web_history_stays_enabled_for_standalone_topic(self):
        done = self.request("What is MQTT?", voice=False)
        self.assertEqual(done["recent_history_messages"], 4)
        self.assertEqual(len(self.engine.generate_stream.call_args.kwargs["conversation_history"]),
                         4 + done["memory_messages"])

    def test_meaningful_voice_recall_retrieves_older_user_facts(self):
        self.old.text = "I use GPIO18 for the relay, and I like the ESP32 microcontroller."
        self.old.save()
        for query in ("What GPIO pin did I tell you I use?", "What microcontroller did I say I like?"):
            with self.subTest(query=query):
                done = self.request(query)
                self.assertTrue(done["memory_used"])
                self.assertIn(self.old.text, str(self.engine.generate_stream.call_args.kwargs["conversation_history"]))

    def test_voice_budgets_and_explicit_four_sentences_preserve_completion_headroom(self):
        for query, expected in (("Briefly explain MQTT.", 48), ("Explain MQTT.", 96),
                                ("Explain MQTT in detail.", 192), ("Explain MQTT in four short sentences.", 192)):
            with self.subTest(query=query):
                done = self.request(query)
                self.assertEqual(done["num_predict"], expected)
                self.assertEqual(self.engine.generate_stream.call_args.kwargs["num_predict"], expected)
        self.assertEqual(self.engine.generate_stream.call_count, 4)


class RagNormalizationTests(SimpleTestCase):
    def test_known_wrappers_preserve_the_topic_and_implicit_queries(self):
        for query, expected in (
            ("What does our knowledge base say about edge computing?", "edge computing"),
            ("According to my notes, how is ESP32 relay wiring connected?", "ESP32 relay wiring connected"),
            ("Search my documents for MQTT broker setup.", "MQTT broker setup"),
            ("Search the knowledge base for MQTT broker setup.", "MQTT broker setup"),
            ("Look in my files for GPIO18 wiring.", "GPIO18 wiring"),
            ("In our project, how did we connect ESP32?", "connect ESP32"),
            ("Explain edge computing.", "Explain edge computing."),
        ):
            with self.subTest(query=query):
                self.assertEqual(normalize_rag_query(query), expected)


@override_settings(**{**SETTINGS, "RAG_ENABLED": True}, RAG_MIN_RELEVANCE=0.5,
                   RAG_MODEL="legacy:1b", RAG_TOP_K=1, RAG_MAX_CONTEXT_CHARS=700)
class ExplicitRagIntegrationTests(VoiceContextTests):
    def test_normalized_user_topic_is_not_logged_on_generation_failure(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory, self.settings(MEDIA_ROOT=directory):
            KnowledgeIngestionService().ingest_text(
                "PrivateWidget99 has a documented setting.", filename="private.md",
                file_type="text/markdown", source_identifier="test:private")
            query = "What does our knowledge base say about PrivateWidget99?"
            self.engine.generate_stream.side_effect = EngineUnavailableError("offline")
            with self.assertLogs("assistant.services", level="INFO") as logs:
                self.assertEqual(self.request(query)["type"], "error")
            self.assertNotIn("PrivateWidget99", str(logs.output))

    def test_normalized_topic_retrieves_lexical_match_and_uses_qwen_once(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory, self.settings(MEDIA_ROOT=directory):
            KnowledgeIngestionService().ingest_text(
                "Edge computing processes data near the device to reduce latency.",
                filename="edge.md", file_type="text/markdown", source_identifier="test:edge")
            query = "What does our knowledge base say about edge computing?"
            done = self.request(query)
            kwargs = self.engine.generate_stream.call_args.kwargs
            self.assertEqual(done["route"], "local_rag")
            self.assertEqual(done["retrieval_query"], "edge computing")
            self.assertTrue(done["rag_query_normalized"])
            self.assertEqual(kwargs["model"], MODEL)
            self.assertIn("Edge computing processes", kwargs["system_instruction"])
            self.assertEqual(self.engine.generate_stream.call_args.args[0], query)
            self.assertEqual(done["num_predict"], 64)
            self.engine.available_models.assert_not_called()
            self.engine.generate_stream.assert_called_once()
            self.engine.reset_mock()
            generic = self.request("What is edge computing?")
            self.assertEqual(generic["route"], "local")
            self.assertFalse(generic["rag_used"])
