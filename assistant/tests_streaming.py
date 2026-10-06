import io
import json
import socket
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings

from conversations.models import Conversation, Message
from knowledge_base.retrieval import RetrievedChunk

from .ai_engine import (
    AIEngineResult,
    AIEngineStreamEvent,
    EngineTimeoutError,
    EngineUnavailableError,
    ModelUnavailableError,
)
from .ai_engine.local import LocalLLMEngine
from .execution_policy import build_execution_plan
from .online.base import OnlineRetrievalResult
from .policy import Capability, build_runtime_capability_registry
from .services import AssistantService, _build_generation_instruction


class IterableHTTPResponse:
    def __init__(self, events):
        self._lines = [json.dumps(event).encode("utf-8") + b"\n" for event in events]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(self._lines)


class LocalStreamingEngineTests(TestCase):
    def setUp(self):
        self.engine = LocalLLMEngine(
            host="http://localhost:11434",
            model="llama3.2:3b",
            timeout=30,
            keep_alive="24h",
            num_predict=128,
        )

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_chunks_reconstruct_final_text_and_metrics(self, urlopen):
        urlopen.return_value = IterableHTTPResponse([
            {"message": {"content": "Edge"}, "done": False},
            {"message": {"content": " computing"}, "done": False},
            {
                "message": {"content": ""},
                "done": True,
                "load_duration": 20_000_000,
                "prompt_eval_duration": 30_000_000,
                "eval_duration": 50_000_000,
                "prompt_eval_count": 8,
                "eval_count": 4,
            },
        ])

        events = list(self.engine.generate_stream("Explain edge AI."))

        self.assertEqual("".join(e.text for e in events if e.type == "delta"), "Edge computing")
        result = events[-1].result
        self.assertEqual(result.text, "Edge computing")
        self.assertIsNotNone(result.ttft_ms)
        self.assertIsNotNone(result.generation_ms)
        self.assertEqual(result.metrics["load_duration_ms"], 20.0)
        self.assertEqual(result.metrics["prompt_eval_count"], 8)
        self.assertEqual(result.metrics["eval_count"], 4)
        self.assertEqual(result.metrics["tokens_per_sec"], 80.0)
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["keep_alive"], "24h")

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_malformed_stream_event_is_rejected(self, urlopen):
        response = IterableHTTPResponse([])
        response._lines = [b"not-json\n"]
        urlopen.return_value = response
        with self.assertRaises(EngineUnavailableError):
            list(self.engine.generate_stream("Hello"))

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_eof_before_done_is_rejected_after_partial_output(self, urlopen):
        urlopen.return_value = IterableHTTPResponse([
            {"message": {"content": "partial"}, "done": False},
        ])
        iterator = self.engine.generate_stream("Hello")
        first = next(iterator)
        self.assertEqual(first.text, "partial")
        with self.assertRaises(EngineUnavailableError):
            next(iterator)

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_empty_generation_is_rejected(self, urlopen):
        urlopen.return_value = IterableHTTPResponse([
            {"message": {"content": ""}, "done": True},
        ])
        with self.assertRaises(EngineUnavailableError):
            list(self.engine.generate_stream("Hello"))

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_stream_http_model_error_is_mapped(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError(
            "http://localhost:11434/api/chat",
            404,
            "Not found",
            {},
            io.BytesIO(b'{"error":"model not found"}'),
        )
        with self.assertRaises(ModelUnavailableError):
            list(self.engine.generate_stream("Hello"))

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_stream_timeout_is_mapped(self, urlopen):
        urlopen.side_effect = urllib.error.URLError(socket.timeout("timed out"))
        with self.assertRaises(EngineTimeoutError):
            list(self.engine.generate_stream("Hello"))

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_stream_connection_failure_is_mapped(self, urlopen):
        urlopen.side_effect = urllib.error.URLError("connection refused")
        with self.assertRaises(EngineUnavailableError):
            list(self.engine.generate_stream("Hello"))

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_sync_generation_has_no_inventory_preflight(self, urlopen):
        response = MagicMock()
        response.read.return_value = json.dumps({
            "message": {"content": "ready"},
            "done": True,
        }).encode("utf-8")
        response.__enter__.return_value = response
        urlopen.return_value = response
        self.engine.generate("Hello")
        self.assertEqual(urlopen.call_count, 1)
        self.assertTrue(urlopen.call_args.args[0].full_url.endswith("/api/chat"))


    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_warm_chat_prefix_uses_production_chat_payload(self, urlopen):
        response = MagicMock()
        response.read.return_value = json.dumps({
            "message": {"role": "assistant", "content": "Ready"},
            "done": True,
            "prompt_eval_count": 300,
            "prompt_eval_duration": 1_000_000_000,
        }).encode("utf-8")
        response.__enter__.return_value = response
        urlopen.return_value = response

        self.engine.warm_chat_prefix(
            system_instruction="GROUND_STABLE",
            model="llama3.2:3b",
            timeout_seconds=90,
        )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))

        self.assertTrue(request.full_url.endswith("/api/chat"))
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["model"], "llama3.2:3b")
        self.assertEqual(payload["options"]["num_predict"], 1)
        self.assertEqual(payload["keep_alive"], "24h")
        self.assertIn("GROUND_STABLE", payload["messages"][0]["content"])
        self.assertEqual(payload["messages"][-1]["content"], "Ready.")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 90)

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_warm_chat_prefix_rejects_unconfirmed_or_error_payloads(self, urlopen):
        for payload, error_type in (
            ([], EngineUnavailableError),
            ({"done": False}, EngineUnavailableError),
            ({"error": "model not found"}, ModelUnavailableError),
            ({"error": "runner failed"}, EngineUnavailableError),
        ):
            with self.subTest(payload=payload):
                response = MagicMock()
                response.__enter__.return_value = response
                response.read.return_value = json.dumps(payload).encode("utf-8")
                urlopen.return_value = response
                with self.assertRaises(error_type):
                    self.engine.warm_chat_prefix()

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_warm_chat_prefix_maps_transport_and_decode_failures(self, urlopen):
        for failure, error_type in (
            (socket.timeout("timed out"), EngineTimeoutError),
            (urllib.error.URLError(socket.timeout("timed out")), EngineTimeoutError),
            (urllib.error.URLError("offline"), EngineUnavailableError),
            (ConnectionResetError("reset"), EngineUnavailableError),
            (urllib.error.HTTPError(
                "http://localhost:11434/api/chat", 404, "Not Found", {},
                io.BytesIO(b'{"error":"model not found"}'),
            ), ModelUnavailableError),
            (urllib.error.HTTPError(
                "http://localhost:11434/api/chat", 500, "Error", {},
                io.BytesIO(b'{"error":"runner failed"}'),
            ), EngineUnavailableError),
        ):
            with self.subTest(failure=type(failure).__name__):
                urlopen.side_effect = failure
                with self.assertRaises(error_type):
                    self.engine.warm_chat_prefix()
        urlopen.side_effect = None
        response = MagicMock()
        response.__enter__.return_value = response
        urlopen.return_value = response
        for body in (b"not json", b"\xff"):
            with self.subTest(body=body):
                response.read.return_value = body
                with self.assertRaises(EngineUnavailableError):
                    self.engine.warm_chat_prefix()


class GenerationInstructionOrderingTests(TestCase):
    @patch("assistant.services.build_rag_instruction", return_value="RAG_DYNAMIC")
    def test_stable_grounding_precedes_dynamic_context(self, build_rag):
        registry = MagicMock()
        registry.build_grounding_instruction.return_value = "GROUND_STABLE"

        response_plan = MagicMock()
        response_plan.rag_system_instruction = "RAG_POLICY"

        instruction = _build_generation_instruction(
            registry=registry,
            base_instruction="BASE_DYNAMIC",
            rag_results=[object()],
            response_plan=response_plan,
            online_instruction="ONLINE_DYNAMIC",
        )

        self.assertLess(
            instruction.index("GROUND_STABLE"),
            instruction.index("BASE_DYNAMIC"),
        )
        self.assertLess(
            instruction.index("BASE_DYNAMIC"),
            instruction.index("RAG_DYNAMIC"),
        )
        self.assertLess(
            instruction.index("RAG_DYNAMIC"),
            instruction.index("RAG_POLICY"),
        )
        self.assertLess(
            instruction.index("RAG_POLICY"),
            instruction.index("ONLINE_DYNAMIC"),
        )

    @override_settings(
        AI_ENGINE="local", RAG_ENABLED=True, ONLINE_RETRIEVAL_ENABLED=True,
        ONLINE_PROVIDER="searxng", SEARXNG_BASE_URL="http://127.0.0.1:8888",
        ONLINE_TIMEOUT_SECONDS=8, ONLINE_MAX_RESULTS=4, ONLINE_MAX_CONTEXT_CHARS=6000,
        DEVICE_CONTROL_ENABLED=False,
    )
    def test_command_prefix_matches_real_local_rag_and_online_preparation(self):
        plan = build_execution_plan({
            "recommended_profile": "BALANCED",
            "ai_recommendation": {"local_ai_allowed": True, "online_allowed": True},
        })
        engine = LocalLLMEngine(model=plan.configured_model)
        rag = RetrievedChunk(1, 1, "test:prefix", "prefix.md", 0, "Local reference.", 1.0)
        online = OnlineRetrievalResult(
            "Current reference", "https://example.com", "Current facts.", "searxng",
            datetime(2026, 10, 6, tzinfo=timezone.utc),
        )
        with (
            patch("knowledge_base.services.is_rag_available", return_value=True),
            patch("assistant.management.commands.warm_ai_model.get_ai_execution_plan", return_value=plan),
            patch("assistant.management.commands.warm_ai_model.LocalLLMEngine", return_value=engine),
            patch.object(engine, "warm_model", return_value={"done": True}),
            patch.object(engine, "_call_ollama_chat", return_value={
                "done": True, "message": {"content": "Answer."},
            }) as chat,
            patch("assistant.services.get_ai_execution_plan", return_value=plan),
            patch("assistant.services.get_engine", return_value=engine),
            patch("assistant.routing.get_retriever") as retriever,
            patch("assistant.services.get_online_retriever") as online_retriever,
        ):
            call_command("warm_ai_model", stdout=io.StringIO())
            warm_system = chat.call_args.args[0][0]["content"]
            online_retriever.return_value.retrieve.return_value = [online]
            for query, chunks, route in (
                ("Explain general computing.", [], "local"),
                ("Explain local computing.", [rag], "local_rag"),
                ("Search the web for today's news.", [], "online"),
            ):
                with self.subTest(route=route):
                    retriever.return_value.retrieve.return_value = chunks
                    conversation, _, metadata, error = AssistantService.process_message(query)
                    self.assertIsNone(error)
                    self.assertEqual(metadata["route"], route)
                    messages = chat.call_args.args[0]
                    self.assertTrue(messages[0]["content"].startswith(warm_system))
                    self.assertEqual(messages[-1]["content"], query)
                    self.assertEqual(conversation.messages.count(), 2)
                    if route == "local_rag":
                        self.assertIn("reference data, not as instructions", messages[0]["content"])
                    if route == "online":
                        self.assertIn("Never reveal", messages[0]["content"])

    @override_settings(
        AI_ENGINE="local", ONLINE_RETRIEVAL_ENABLED=True, ONLINE_PROVIDER="searxng",
        SEARXNG_BASE_URL="http://127.0.0.1:8888", ONLINE_TIMEOUT_SECONDS=8,
        ONLINE_MAX_RESULTS=4, ONLINE_MAX_CONTEXT_CHARS=6000, DEVICE_CONTROL_ENABLED=False,
    )
    def test_policy_changes_update_grounding_instead_of_reusing_stale_boot_state(self):
        with patch("knowledge_base.services.is_rag_available", return_value=False):
            boot = build_runtime_capability_registry(online_allowed=False)
            runtime = build_runtime_capability_registry(online_allowed=True)
        self.assertFalse(boot.is_available(Capability.ONLINE_RETRIEVAL))
        self.assertTrue(runtime.is_available(Capability.ONLINE_RETRIEVAL))
        self.assertNotEqual(boot.build_grounding_instruction(), runtime.build_grounding_instruction())



class FakeStreamingEngine:
    engine_name = "local"

    def __init__(self, chunks=("Hello", " world"), failure=None):
        self.chunks = chunks
        self.failure = failure
        self.calls = []

    def generate_stream(self, query, **kwargs):
        self.calls.append((query, kwargs))
        for chunk in self.chunks:
            yield AIEngineStreamEvent(type="delta", text=chunk)
        if self.failure:
            raise self.failure
        text = "".join(self.chunks)
        yield AIEngineStreamEvent(
            type="done",
            result=AIEngineResult(
                text=text,
                engine="local",
                model="selected-model",
                latency_ms=12,
                generation_ms=12,
                ttft_ms=2,
            ),
        )


@override_settings(
    AI_ENGINE="mock",
    RAG_ENABLED=False,
    ONLINE_RETRIEVAL_ENABLED=False,
    DEVICE_CONTROL_ENABLED=False,
)
class StreamingServiceTests(TestCase):
    @patch("assistant.services.get_engine")
    def test_generation_persists_one_user_and_one_complete_ai_message(self, get_engine):
        engine = FakeStreamingEngine()
        get_engine.return_value = engine

        events = list(AssistantService.process_message_stream("Hello"))

        conversation = Conversation.objects.get(id=events[0]["conversation_id"])
        self.assertEqual(
            list(conversation.messages.values_list("sender", "text")),
            [("USER", "Hello"), ("AI", "Hello world")],
        )
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["response"], "Hello world")
        self.assertEqual(events[-1]["effective_model"], "selected-model")
        self.assertEqual(events[-1]["ttft_ms"], 2)

    @patch("assistant.services.get_engine")
    def test_first_and_later_requests_pass_identity_correct_history(self, get_engine):
        engine = FakeStreamingEngine(chunks=("answer",))
        get_engine.return_value = engine
        first = list(AssistantService.process_message_stream("first question"))
        conversation_id = first[0]["conversation_id"]
        list(AssistantService.process_message_stream("second question", conversation_id))

        self.assertEqual(engine.calls[0][1]["conversation_history"], [])
        second_history = engine.calls[1][1]["conversation_history"]
        self.assertEqual(
            [(item["role"], item["content"]) for item in second_history],
            [("USER", "first question"), ("AI", "answer")],
        )
        messages = LocalLLMEngine()._build_messages("second question", second_history)
        self.assertEqual(
            sum(item["content"] == "second question" for item in messages),
            1,
        )

    @patch("assistant.services.get_engine")
    def test_repeated_identical_turns_preserve_the_previous_turn(self, get_engine):
        engine = FakeStreamingEngine(chunks=("answer",))
        get_engine.return_value = engine
        first = list(AssistantService.process_message_stream("same words"))
        list(AssistantService.process_message_stream("same words", first[0]["conversation_id"]))
        history = engine.calls[1][1]["conversation_history"]
        self.assertEqual(history[0], {"role": "USER", "content": "same words"})
        messages = LocalLLMEngine()._build_messages("same words", history)
        self.assertEqual(sum(m["content"] == "same words" for m in messages), 2)

    @patch("assistant.services.get_engine")
    def test_failure_before_output_keeps_only_user_message(self, get_engine):
        get_engine.return_value = FakeStreamingEngine(
            chunks=(),
            failure=EngineUnavailableError("offline"),
        )
        events = list(AssistantService.process_message_stream("Hello"))
        self.assertEqual(events[-1]["type"], "error")
        self.assertFalse(events[-1]["partial"])
        conversation = Conversation.objects.get(id=events[0]["conversation_id"])
        self.assertEqual(conversation.messages.count(), 1)

    @patch("assistant.services.get_engine")
    def test_failure_after_partial_output_does_not_persist_incomplete_ai(self, get_engine):
        get_engine.return_value = FakeStreamingEngine(
            chunks=("partial",),
            failure=EngineUnavailableError("disconnect"),
        )
        events = list(AssistantService.process_message_stream("Hello"))
        self.assertEqual(events[-1]["type"], "error")
        self.assertTrue(events[-1]["partial"])
        conversation = Conversation.objects.get(id=events[0]["conversation_id"])
        self.assertEqual(list(conversation.messages.values_list("sender", flat=True)), ["USER"])

    @patch("assistant.services.get_engine")
    def test_direct_response_does_not_call_llm(self, get_engine):
        events = list(AssistantService.process_message_stream("Turn it on"))
        self.assertEqual([event["type"] for event in events], ["start", "delta", "done"])
        get_engine.assert_not_called()


@override_settings(
    AI_ENGINE="mock",
    RAG_ENABLED=False,
    ONLINE_RETRIEVAL_ENABLED=False,
    DEVICE_CONTROL_ENABLED=False,
)
class StreamingAPITests(TestCase):
    def _events(self, response):
        return [json.loads(line) for line in b"".join(response.streaming_content).splitlines()]

    def test_direct_stream_is_valid_ndjson_with_streaming_headers(self):
        response = self.client.post(
            "/api/assistant/chat/stream/",
            {"query": "Turn it on"},
            content_type="application/json",
        )
        events = self._events(response)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("application/x-ndjson"))
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["X-Accel-Buffering"], "no")
        self.assertEqual([event["type"] for event in events], ["start", "delta", "done"])

    @patch("assistant.services.get_engine")
    def test_generated_stream_reconstructs_response(self, get_engine):
        get_engine.return_value = FakeStreamingEngine(chunks=("one", " two"))
        response = self.client.post(
            "/api/assistant/chat/stream/",
            {"query": "Explain recursion"},
            content_type="application/json",
        )
        events = self._events(response)
        self.assertEqual("".join(e.get("text", "") for e in events), "one two")
        self.assertEqual(events[-1]["response"], "one two")

    @patch("assistant.services.get_engine")
    def test_error_stream_is_structured_and_does_not_claim_completion(self, get_engine):
        get_engine.return_value = FakeStreamingEngine(
            chunks=("partial",),
            failure=EngineUnavailableError("disconnect"),
        )
        response = self.client.post(
            "/api/assistant/chat/stream/",
            {"query": "Explain recursion"},
            content_type="application/json",
        )
        events = self._events(response)
        self.assertEqual(events[-1]["type"], "error")
        self.assertTrue(events[-1]["partial"])
        self.assertNotIn("done", [event["type"] for event in events])

    def test_stream_endpoint_enforces_csrf(self):
        client = Client(enforce_csrf_checks=True)
        rejected = client.post(
            "/api/assistant/chat/stream/",
            data=json.dumps({"query": "Hello"}),
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 403)
        page = client.get("/assistant/")
        token = page.cookies["csrftoken"].value
        accepted = client.post(
            "/api/assistant/chat/stream/",
            data=json.dumps({"query": "Turn it on"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(accepted.status_code, 200)


class WarmModelCommandTests(TestCase):
    @override_settings(OLLAMA_MODEL="configured:3b")
    @patch("assistant.management.commands.warm_ai_model.build_runtime_capability_registry")
    @patch("assistant.management.commands.warm_ai_model.get_ai_execution_plan")
    @patch("assistant.management.commands.warm_ai_model.LocalLLMEngine.warm_chat_prefix")
    @patch("assistant.management.commands.warm_ai_model.LocalLLMEngine.warm_model")
    def test_defaults_to_configured_model(
        self,
        warm_model,
        warm_chat_prefix,
        get_execution_plan,
        build_registry,
    ):
        warm_model.return_value = {"done": True}
        warm_chat_prefix.return_value = {"done": True}

        plan = MagicMock()
        plan.online_allowed = True
        get_execution_plan.return_value = plan

        registry = MagicMock()
        registry.build_grounding_instruction.return_value = "GROUND_STABLE"
        build_registry.return_value = registry

        with patch(
            "assistant.management.commands.warm_ai_model.time.monotonic",
            side_effect=[100.0, 110.0],
        ):
            call_command("warm_ai_model", stdout=io.StringIO())

        warm_model.assert_called_once_with(
            model="configured:3b",
            timeout_seconds=90.0,
        )
        build_registry.assert_called_once_with(online_allowed=True)
        warm_chat_prefix.assert_called_once_with(
            system_instruction="GROUND_STABLE",
            model="configured:3b",
            timeout_seconds=80.0,
        )

    @patch("assistant.management.commands.warm_ai_model.build_runtime_capability_registry")
    @patch("assistant.management.commands.warm_ai_model.get_ai_execution_plan")
    @patch("assistant.management.commands.warm_ai_model.LocalLLMEngine.warm_chat_prefix")
    @patch("assistant.management.commands.warm_ai_model.LocalLLMEngine.warm_model")
    def test_explicit_model_override(
        self,
        warm_model,
        warm_chat_prefix,
        get_execution_plan,
        build_registry,
    ):
        warm_model.return_value = {"done": True}
        warm_chat_prefix.return_value = {"done": True}

        plan = MagicMock()
        plan.online_allowed = False
        get_execution_plan.return_value = plan

        registry = MagicMock()
        registry.build_grounding_instruction.return_value = "GROUND_STABLE"
        build_registry.return_value = registry

        with patch(
            "assistant.management.commands.warm_ai_model.time.monotonic",
            side_effect=[100.0, 105.0],
        ):
            call_command(
                "warm_ai_model",
                model="diagnostic:1b",
                stdout=io.StringIO(),
            )

        warm_model.assert_called_once_with(
            model="diagnostic:1b",
            timeout_seconds=90.0,
        )
        warm_chat_prefix.assert_called_once_with(
            system_instruction="GROUND_STABLE",
            model="diagnostic:1b",
            timeout_seconds=85.0,
        )

    @patch("assistant.management.commands.warm_ai_model.build_runtime_capability_registry")
    @patch("assistant.management.commands.warm_ai_model.get_ai_execution_plan")
    @patch("assistant.management.commands.warm_ai_model.LocalLLMEngine.warm_chat_prefix")
    @patch("assistant.management.commands.warm_ai_model.LocalLLMEngine.warm_model")
    def test_exhausted_shared_timeout_does_not_start_prefix(
        self, warm_model, warm_chat_prefix, get_plan, build_registry,
    ):
        warm_model.return_value = {"done": True}
        with (
            patch(
                "assistant.management.commands.warm_ai_model.time.monotonic",
                side_effect=[100.0, 190.0],
            ),
            self.assertRaisesRegex(CommandError, "warm-up timed out"),
        ):
            call_command("warm_ai_model", stdout=io.StringIO())
        warm_chat_prefix.assert_not_called()

    @patch("assistant.management.commands.warm_ai_model.build_runtime_capability_registry")
    @patch("assistant.management.commands.warm_ai_model.get_ai_execution_plan")
    @patch("assistant.management.commands.warm_ai_model.LocalLLMEngine.warm_chat_prefix")
    @patch("assistant.management.commands.warm_ai_model.LocalLLMEngine.warm_model")
    def test_prefix_failure_does_not_report_success(
        self, warm_model, warm_chat_prefix, get_plan, build_registry,
    ):
        warm_model.return_value = {"done": True}
        for failure in (
            EngineUnavailableError("token=private"),
            EngineTimeoutError("timed out"),
            ModelUnavailableError("not found"),
        ):
            with self.subTest(failure=type(failure).__name__):
                warm_chat_prefix.side_effect = failure
                output = io.StringIO()
                with self.assertRaises(CommandError) as context:
                    call_command("warm_ai_model", stdout=output)
                self.assertEqual(output.getvalue(), "")
                self.assertNotIn("private", str(context.exception))

    @patch("assistant.management.commands.warm_ai_model.LocalLLMEngine.warm_chat_prefix")
    @patch("assistant.management.commands.warm_ai_model.LocalLLMEngine.warm_model")
    def test_unavailable_ollama_fails_cleanly_without_secret_output(
        self,
        warm_model,
        warm_chat_prefix,
    ):
        warm_model.side_effect = EngineUnavailableError("token=private")
        output = io.StringIO()

        with self.assertRaises(CommandError) as context:
            call_command("warm_ai_model", stdout=output)

        self.assertNotIn("private", str(context.exception))
        self.assertEqual(output.getvalue(), "")
        warm_chat_prefix.assert_not_called()

    def test_timeout_validation_message_matches_strict_bounds(self):
        for timeout in (0, -1, 301, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(
                CommandError,
                "--timeout must be greater than 0 and at most 300 seconds",
            ):
                call_command("warm_ai_model", timeout=timeout, stdout=io.StringIO())


class StreamingFrontendStructureTests(TestCase):
    def test_dashboard_uses_safe_incremental_ndjson_rendering(self):
        source = (Path(settings.BASE_DIR) / "templates" / "dashboard" / "assistant.html").read_text(encoding="utf-8")
        self.assertIn("/api/assistant/chat/stream/", source)
        self.assertIn("response.body.getReader()", source)
        self.assertIn("buffer.split('\\n')", source)
        self.assertIn("aiBubble.textContent = accumulated", source)
        self.assertIn("CHAT_STREAM_IDLE_TIMEOUT_MS", source)
        self.assertIn("const markStreamInterrupted = (errorMessage) =>", source)
        self.assertIn("if (interruptionMarked) return;", source)
        self.assertEqual(source.count("[Response interrupted.]"), 1)
        self.assertIn("markStreamInterrupted(errorMessage);", source)
        finally_block = source.split("} finally {", 1)[1].split("}", 1)[0]
        self.assertNotIn("setVoiceState('IDLE')", finally_block)
        self.assertNotIn('<div class="msg-bubble">${text}</div>', source)

    def test_deployment_uses_one_threaded_worker_and_weak_warm_dependency(self):
        deployment = Path(settings.BASE_DIR) / "deployment" / "systemd"
        app_unit = (deployment / "smart-ai-companion.service").read_text(encoding="utf-8")
        warm_unit = (deployment / "smart-ai-companion-model-warm.service").read_text(encoding="utf-8")
        self.assertIn("--bind 0.0.0.0:8000", app_unit)
        self.assertIn("--worker-class gthread --workers 1 --threads 4", app_unit)
        self.assertIn(
            "Wants=smart-ai-companion-network-mode.service smart-ai-companion-model-warm.service",
            app_unit,
        )
        self.assertNotIn("Requires=smart-ai-companion-network-mode.service", app_unit)
        self.assertNotIn("Requires=smart-ai-companion-model-warm.service", app_unit)
        self.assertIn("Wants=ollama.service", warm_unit)
        self.assertIn(
            "After=ollama.service smart-ai-companion-network-mode.service network.target",
            warm_unit,
        )
        self.assertNotIn("After=smart-ai-companion.service", warm_unit)
        self.assertIn("manage.py warm_ai_model --timeout 90", warm_unit)
