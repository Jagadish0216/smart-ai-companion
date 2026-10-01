import tempfile
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from assistant.ai_engine.base import AIEngineResult
from assistant.policy import CapabilityRegistry
from assistant.routing import QueryRoute, QueryRouter
from assistant.services import AssistantService
from knowledge_base.retrieval import RetrievedChunk
from knowledge_base.services import KnowledgeIngestionService


EDGE_KNOWLEDGE = (
    "Edge computing processes data close to the device or data source to reduce "
    "latency and dependence on cloud connectivity. IoT devices use edge computing "
    "for low-latency local decisions."
)
RELAY_KNOWLEDGE = (
    "An ESP32 can control a relay through a suitable transistor driver. The relay "
    "module and ESP32 should share a common ground, with protection for inductive loads."
)


class RouterTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self._media_directory = tempfile.TemporaryDirectory()
        self._settings_override = override_settings(
            MEDIA_ROOT=self._media_directory.name,
            AI_ENGINE="local",
            RAG_ENABLED=True,
            RAG_RETRIEVER="lexical",
            RAG_CHUNK_CHARS=500,
            RAG_CHUNK_OVERLAP_CHARS=50,
            RAG_TOP_K=4,
            RAG_MIN_RELEVANCE=0.5,
        )
        self._settings_override.enable()
        ingestion = KnowledgeIngestionService()
        ingestion.ingest_text(
            EDGE_KNOWLEDGE,
            filename="edge.md",
            file_type="text/markdown",
            source_identifier="test:edge",
        )
        ingestion.ingest_text(
            RELAY_KNOWLEDGE,
            filename="relay.md",
            file_type="text/markdown",
            source_identifier="test:relay",
        )

    def tearDown(self):
        self._settings_override.disable()
        self._media_directory.cleanup()
        super().tearDown()

    def decide(self, query):
        return QueryRouter().decide(
            query,
            registry=CapabilityRegistry.from_settings(),
        )


class QueryRouterTests(RouterTestCase):
    def test_general_knowledge_routes_local(self):
        for query in (
            "What is recursion?",
            "Who wrote Hamlet?",
            "Explain how a temperature sensor works.",
        ):
            with self.subTest(query=query):
                decision = self.decide(query)
                self.assertEqual(decision.route, QueryRoute.LOCAL)
                self.assertEqual(decision.rag_results, ())

    def test_relevant_local_knowledge_routes_rag(self):
        cases = (
            ("Explain how to control an ESP32 relay.", "relay.md"),
            ("Explain edge computing with an IoT example.", "edge.md"),
        )

        for query, title in cases:
            with self.subTest(query=query):
                decision = self.decide(query)
                self.assertEqual(decision.route, QueryRoute.LOCAL_RAG)
                self.assertTrue(decision.rag_results)
                self.assertEqual(decision.rag_results[0].title, title)

    def test_time_sensitive_queries_route_online(self):
        queries = (
            "What is today's weather?",
            "What's the latest AI news?",
            "What is the current Bitcoin price?",
            "What was the score of today's match?",
            "Who won the match today?",
            "What is the latest Python version?",
            "Search the web for Raspberry Pi 5 updates.",
            "Look this up online.",
        )

        for query in queries:
            with self.subTest(query=query):
                self.assertEqual(self.decide(query).route, QueryRoute.ONLINE)

    def test_online_words_do_not_create_false_positives(self):
        queries = (
            "How does weather forecasting work?",
            "Explain live variables.",
            "What is electrical current?",
            "What does latest mean?",
        )

        for query in queries:
            with self.subTest(query=query):
                self.assertEqual(self.decide(query).route, QueryRoute.LOCAL)

    def test_action_and_clarification_routes_preserve_policy(self):
        self.assertEqual(
            self.decide("Turn on the lights.").route,
            QueryRoute.ACTION,
        )
        self.assertEqual(
            self.decide("Remind me at 6 PM.").route,
            QueryRoute.ACTION,
        )
        self.assertEqual(
            self.decide("Do that again.").route,
            QueryRoute.CLARIFICATION,
        )

    def test_decisions_are_deterministic_immutable_request_state(self):
        first = self.decide("Explain edge computing with an IoT example.")
        second = self.decide("Explain edge computing with an IoT example.")

        self.assertEqual(first, second)
        self.assertIsInstance(first.rag_results, tuple)
        with self.assertRaises(FrozenInstanceError):
            first.route = QueryRoute.LOCAL

    def test_online_route_skips_retrieval(self):
        retriever = MagicMock()
        decision = QueryRouter(retriever=retriever).decide(
            "What is today's weather?",
            registry=CapabilityRegistry.from_settings(),
        )

        self.assertEqual(decision.route, QueryRoute.ONLINE)
        retriever.retrieve.assert_not_called()


class QueryRouterServiceTests(RouterTestCase):
    @patch("assistant.services.get_engine")
    @patch("assistant.routing.get_retriever")
    def test_rag_results_are_retrieved_once_and_reused(
        self,
        mock_get_retriever,
        mock_get_engine,
    ):
        retrieved = RetrievedChunk(
            chunk_id=100,
            document_id=10,
            source_identifier="test:edge",
            title="edge.md",
            chunk_index=0,
            content=EDGE_KNOWLEDGE,
            score=1.0,
        )
        retriever = MagicMock()
        retriever.retrieve.return_value = [retrieved]
        mock_get_retriever.return_value = retriever
        engine = MagicMock()
        engine.generate.return_value = AIEngineResult(
            text="Edge computing keeps processing close to the source.",
            engine="local",
            latency_ms=3,
        )
        mock_get_engine.return_value = engine

        conversation, _text, metadata, error = AssistantService.process_message(
            "What is edge computing?"
        )

        self.assertIsNone(error)
        retriever.retrieve.assert_called_once_with("What is edge computing?")
        instruction = engine.generate.call_args.kwargs["system_instruction"]
        self.assertEqual(instruction.count(EDGE_KNOWLEDGE), 1)
        self.assertEqual(metadata["route"], "local_rag")
        self.assertTrue(metadata["rag_used"])
        self.assertEqual(metadata["rag_chunks"], 1)
        self.assertFalse(metadata["online_used"])
        self.assertEqual(conversation.messages.count(), 2)

    @patch("assistant.services.get_engine")
    def test_unavailable_online_route_returns_without_llm(self, mock_get_engine):
        conversation, text, metadata, error = AssistantService.process_message(
            "What is today's weather?"
        )

        self.assertIsNone(error)
        self.assertIn("can't retrieve live online information", text.lower())
        self.assertEqual(metadata["route"], "online")
        self.assertFalse(metadata["online_used"])
        self.assertEqual(metadata["latency_ms"], 0)
        self.assertFalse(metadata["rag_used"])
        mock_get_engine.assert_not_called()
        self.assertEqual(
            list(conversation.messages.order_by("id").values_list("sender", "text")),
            [
                ("USER", "What is today's weather?"),
                ("AI", text),
            ],
        )

    @patch("assistant.services.get_engine")
    def test_unavailable_action_still_uses_capability_grounding(self, mock_get_engine):
        _conversation, text, metadata, error = AssistantService.process_message(
            "Turn on the lights."
        )

        self.assertIsNone(error)
        self.assertIn("can't control", text.lower())
        self.assertEqual(metadata["route"], "action")
        self.assertFalse(metadata["online_used"])
        mock_get_engine.assert_not_called()

    @override_settings(AI_ENGINE="mock")
    def test_browser_api_contract_remains_compatible(self):
        response = APIClient().post(
            "/api/assistant/chat/",
            {"query": "What is recursion?"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("conversation_id", response.data)
        self.assertIn("response", response.data)
        self.assertIn("engine", response.data)
        self.assertIn("mode", response.data)
        self.assertEqual(response.data["route"], "local")
        self.assertFalse(response.data["rag_used"])
        self.assertEqual(response.data["rag_chunks"], 0)
        self.assertEqual(response.data["rag_sources"], [])
        self.assertFalse(response.data["online_used"])

    def test_online_route_metadata_is_exposed_additively(self):
        response = APIClient().post(
            "/api/assistant/chat/",
            {"query": "What is today's weather?"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["route"], "online")
        self.assertEqual(response.data["engine"], "policy")
        self.assertFalse(response.data["online_used"])
        self.assertFalse(response.data["rag_used"])
