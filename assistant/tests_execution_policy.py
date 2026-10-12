"""Deterministic coverage for resource-aware AI execution V2."""

from datetime import datetime, timezone
import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from knowledge_base.retrieval import RetrievedChunk
from conversations.models import Message

from .ai_engine import (
    AIEngineResult,
    AIEngineStreamEvent,
    EngineTimeoutError,
    EngineUnavailableError,
)
from .ai_engine.local import LocalLLMEngine
from .execution_policy import (
    AIExecutionPlan,
    build_execution_plan,
    get_ai_execution_plan,
    select_voice_budget,
)
from .online.base import OnlineRetrievalResult
from .policy import AssistantResponsePolicy, Capability, ResponseMode
from .routing import QueryRoute, QueryRouteDecision
from .services import AssistantService


POLICY_SETTINGS = {
    "AI_ENGINE": "local",
    "OLLAMA_MODEL": "llama3.2:3b",
    "AI_LIGHTWEIGHT_MODEL": "llama3.2:1b",
    "AI_GENERATION_NORMAL_NUM_PREDICT": 128,
    "AI_GENERATION_REDUCED_NUM_PREDICT": 96,
    "AI_GENERATION_MINIMAL_NUM_PREDICT": 48,
    "AI_CONTEXT_NORMAL_MESSAGES": 10,
    "AI_CONTEXT_REDUCED_MESSAGES": 6,
    "AI_CONTEXT_MINIMAL_MESSAGES": 3,
    "AI_LOCAL_GENERATION_TIMEOUT_SECONDS": 120,
}


def _report(
    profile,
    *,
    local_ai_allowed=True,
    online_allowed=False,
    generation_budget=None,
    reason="RESOURCE_PRESSURE",
):
    if generation_budget is None:
        generation_budget = {
            "PERFORMANCE": "NORMAL",
            "BALANCED": "NORMAL",
            "ECO": "REDUCED",
            "PROTECTIVE": "MINIMAL",
        }[profile]
    return {
        "recommended_profile": profile,
        "reasons": [{"code": reason}],
        "ai_recommendation": {
            "local_ai_allowed": local_ai_allowed,
            "online_allowed": online_allowed,
            "generation_budget": generation_budget,
        },
    }


def _plan(
    *,
    profile="BALANCED",
    effective_model="llama3.2:3b",
    budget="NORMAL",
    generation_allowed=True,
    local_ai_allowed=True,
    online_allowed=False,
    status_message=None,
):
    values = {
        "NORMAL": (128, 10),
        "REDUCED": (96, 6),
        "MINIMAL": (48, 3),
    }
    max_output_tokens, context_message_limit = values[budget]
    return AIExecutionPlan(
        resource_profile=profile,
        configured_model="llama3.2:3b",
        effective_model=effective_model,
        model_reason="RESOURCE_PRESSURE",
        generation_budget=budget,
        max_output_tokens=max_output_tokens,
        context_message_limit=context_message_limit,
        local_ai_allowed=local_ai_allowed,
        generation_allowed=generation_allowed,
        online_allowed=online_allowed,
        timeout_seconds=120,
        policy_reason="MEMORY_PRESSURE",
        resource_policy_available=True,
        status_message=status_message,
    )


@override_settings(**POLICY_SETTINGS)
class ExecutionPolicyMappingTests(SimpleTestCase):
    def test_voice_ceiling_changes_only_healthy_normal_execution(self):
        for profile in ("BALANCED", "PERFORMANCE"):
            original = build_execution_plan(_report(profile))
            voice = select_voice_budget(original)
            self.assertEqual(voice.max_output_tokens, 384)
            self.assertEqual(original.max_output_tokens, 128)
            self.assertEqual(voice.effective_model, original.effective_model)
            self.assertEqual(voice.context_message_limit, original.context_message_limit)
        for profile, budget, allowed, available in (
            ("ECO", "REDUCED", True, True), ("PROTECTIVE", "MINIMAL", True, True),
            ("BALANCED", "REDUCED", True, True), ("BALANCED", "NORMAL", False, True),
            ("BALANCED", "NORMAL", True, False),
        ):
            original = build_execution_plan(_report(profile, generation_budget=budget, local_ai_allowed=allowed),
                                            resource_policy_available=available)
            self.assertIs(select_voice_budget(original), original)

    @override_settings(AI_GENERATION_NORMAL_VOICE_NUM_PREDICT=99999)
    def test_voice_ceiling_is_hard_bounded_and_configurable(self):
        plan = build_execution_plan(_report("BALANCED"))
        self.assertEqual(select_voice_budget(plan).max_output_tokens, 512)
        with self.settings(AI_GENERATION_NORMAL_VOICE_NUM_PREDICT=200):
            self.assertEqual(select_voice_budget(plan).max_output_tokens, 200)

    def test_historical_diagnostic_does_not_become_current_pressure_reason(self):
        report = _report("ECO")
        report["reasons"] = [{"code": "THROTTLING_HISTORY"}, {"code": "MEMORY_PRESSURE"}]
        plan = build_execution_plan(report, available_models={"llama3.2:1b"})
        self.assertEqual(plan.policy_reason, "MEMORY_PRESSURE")
        self.assertEqual(plan.model_reason, "MEMORY_PRESSURE")

    def test_performance_selects_configured_standard_model(self):
        plan = build_execution_plan(
            _report("PERFORMANCE"),
            available_models={"llama3.2:1b", "llama3.2:3b"},
        )

        self.assertEqual(plan.effective_model, "llama3.2:3b")
        self.assertEqual(plan.generation_budget, "NORMAL")
        self.assertEqual(plan.max_output_tokens, 128)

    def test_balanced_never_forces_lightweight_downgrade(self):
        plan = build_execution_plan(
            _report("BALANCED"),
            available_models={"llama3.2:1b", "llama3.2:3b"},
        )

        self.assertEqual(plan.effective_model, "llama3.2:3b")
        self.assertEqual(plan.model_reason, "CONFIGURED_MODEL")

    def test_eco_selects_approved_lightweight_model_when_installed(self):
        plan = build_execution_plan(
            _report("ECO", reason="MEMORY_PRESSURE"),
            available_models={"llama3.2:1b", "llama3.2:3b"},
        )

        self.assertEqual(plan.configured_model, "llama3.2:3b")
        self.assertEqual(plan.effective_model, "llama3.2:1b")
        self.assertEqual(plan.model_reason, "MEMORY_PRESSURE")
        self.assertEqual(plan.generation_budget, "REDUCED")
        self.assertEqual(plan.max_output_tokens, 96)

    def test_eco_uses_reduced_standard_model_when_lightweight_is_absent(self):
        plan = build_execution_plan(
            _report("ECO"),
            available_models={"llama3.2:3b"},
        )

        self.assertTrue(plan.generation_allowed)
        self.assertEqual(plan.effective_model, "llama3.2:3b")
        self.assertEqual(plan.model_reason, "LIGHTWEIGHT_UNAVAILABLE")
        self.assertEqual(plan.max_output_tokens, 96)

    def test_protective_never_escalates_to_standard_when_lightweight_absent(self):
        plan = build_execution_plan(
            _report("PROTECTIVE"),
            available_models={"llama3.2:3b"},
        )

        self.assertFalse(plan.generation_allowed)
        self.assertIsNone(plan.effective_model)
        self.assertEqual(plan.model_reason, "LIGHTWEIGHT_UNAVAILABLE")
        self.assertEqual(plan.generation_budget, "MINIMAL")
        self.assertEqual(plan.max_output_tokens, 48)

    def test_protective_uses_lightweight_only_when_allowed_and_installed(self):
        plan = build_execution_plan(
            _report("PROTECTIVE", local_ai_allowed=True),
            available_models={"llama3.2:1b", "llama3.2:3b"},
        )

        self.assertTrue(plan.generation_allowed)
        self.assertEqual(plan.effective_model, "llama3.2:1b")
        self.assertEqual(plan.max_output_tokens, 48)

    def test_protective_local_ai_denial_blocks_generation(self):
        plan = build_execution_plan(
            _report("PROTECTIVE", local_ai_allowed=False),
            available_models={"llama3.2:1b", "llama3.2:3b"},
        )

        self.assertFalse(plan.generation_allowed)
        self.assertIsNone(plan.effective_model)
        self.assertEqual(plan.model_reason, "RESOURCE_POLICY_BLOCK")
        self.assertIn("protecting itself", plan.status_message)

    @patch("assistant.execution_policy.get_internet_status")
    @patch("assistant.execution_policy.get_resource_manager_report")
    def test_resource_manager_failure_uses_conservative_fallback(
        self,
        resource_report,
        internet_status,
    ):
        resource_report.side_effect = RuntimeError("telemetry unavailable")
        internet_status.return_value = {"state": "FULL"}
        inventory = MagicMock()

        with self.settings(ONLINE_RETRIEVAL_ENABLED=True):
            plan = get_ai_execution_plan(model_inventory_provider=inventory)

        self.assertEqual(plan.resource_profile, "UNKNOWN")
        self.assertFalse(plan.resource_policy_available)
        self.assertEqual(plan.effective_model, "llama3.2:3b")
        self.assertEqual(plan.generation_budget, "REDUCED")
        self.assertEqual(plan.max_output_tokens, 96)
        self.assertTrue(plan.online_allowed)
        self.assertEqual(plan.policy_reason, "RESOURCE_POLICY_UNAVAILABLE")
        inventory.assert_not_called()


class LocalModelInventoryTests(SimpleTestCase):
    @staticmethod
    def _response(payload):
        response = MagicMock()
        response.read.return_value = json.dumps(payload).encode("utf-8")
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        return response

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_inventory_uses_tags_get_only_and_never_downloads(self, urlopen):
        urlopen.return_value = self._response({
            "models": [
                {"name": "llama3.2:3b"},
                {"model": "llama3.2:1b"},
            ]
        })
        engine = LocalLLMEngine(host="http://localhost:11434")

        models = engine.available_models()

        self.assertEqual(models, {"llama3.2:1b", "llama3.2:3b"})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:11434/api/tags")
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        urlopen.assert_called_once()


SERVICE_SETTINGS = POLICY_SETTINGS | {
    "RAG_ENABLED": False,
    "ONLINE_RETRIEVAL_ENABLED": True,
    "ONLINE_PROVIDER": "searxng",
    "SEARXNG_BASE_URL": "http://127.0.0.1:8888",
    "ONLINE_TIMEOUT_SECONDS": 8,
    "ONLINE_MAX_RESULTS": 4,
    "ONLINE_MAX_CONTEXT_CHARS": 6000,
    "DEVICE_CONTROL_ENABLED": False,
}


@override_settings(**SERVICE_SETTINGS)
class ResourceAwareAssistantServiceTests(TestCase):
    def _engine(self, *, text="Local answer.", model="llama3.2:3b"):
        engine = MagicMock()
        engine.generate.return_value = AIEngineResult(
            text=text,
            engine="local",
            model=model,
            mode="offline",
            latency_ms=5,
        )
        return engine

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_online_retriever")
    @patch("assistant.services.get_ai_execution_plan")
    def test_offline_policy_skips_online_retrieval_even_when_searxng_is_ready(
        self,
        execution_policy,
        online_retriever,
        get_engine,
    ):
        execution_policy.return_value = _plan(online_allowed=False)

        _conversation, text, metadata, error = AssistantService.process_message(
            "Search the web for today's AI news."
        )

        self.assertIsNone(error)
        self.assertIn("does not allow Internet access", text)
        self.assertFalse(metadata["online_allowed"])
        self.assertFalse(metadata["online_used"])
        online_retriever.assert_not_called()
        get_engine.assert_not_called()

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_online_retriever")
    @patch("assistant.services.get_ai_execution_plan")
    def test_offline_policy_preserves_local_ollama_generation(
        self,
        execution_policy,
        online_retriever,
        get_engine,
    ):
        execution_policy.return_value = _plan(online_allowed=False)
        engine = self._engine()
        get_engine.return_value = engine

        _conversation, text, metadata, error = AssistantService.process_message(
            "Explain recursion."
        )

        self.assertIsNone(error)
        self.assertEqual(text, "Local answer.")
        self.assertEqual(metadata["route"], "local")
        engine.generate.assert_called_once()
        online_retriever.assert_not_called()

    @patch("assistant.services.QueryRouter.decide")
    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_online_retriever")
    @patch("assistant.services.get_ai_execution_plan")
    def test_offline_policy_preserves_local_rag(
        self,
        execution_policy,
        online_retriever,
        get_engine,
        decide,
    ):
        execution_policy.return_value = _plan(online_allowed=False)
        chunk = RetrievedChunk(
            chunk_id=1,
            document_id=1,
            source_identifier="test:edge",
            title="edge.md",
            chunk_index=0,
            content="Edge computing processes data near its source.",
            score=1.0,
        )
        decide.return_value = QueryRouteDecision(
            QueryRoute.LOCAL_RAG,
            ResponseMode.NORMAL,
            Capability.LOCAL_RAG,
            (chunk,),
        )
        engine = self._engine(text="Grounded local answer.")
        get_engine.return_value = engine

        _conversation, text, metadata, error = AssistantService.process_message(
            "Explain edge computing."
        )

        self.assertIsNone(error)
        self.assertEqual(text, "Grounded local answer.")
        self.assertTrue(metadata["rag_used"])
        self.assertFalse(metadata["online_used"])
        self.assertIn(
            "Edge computing processes data near its source.",
            engine.generate.call_args.kwargs["system_instruction"],
        )
        online_retriever.assert_not_called()

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_online_retriever")
    @patch("assistant.services.get_ai_execution_plan")
    def test_full_internet_and_allowed_policy_keep_online_path_available(
        self,
        execution_policy,
        online_retriever,
        get_engine,
    ):
        execution_policy.return_value = _plan(online_allowed=True)
        retriever = MagicMock()
        retriever.retrieve.return_value = [
            OnlineRetrievalResult(
                title="Current release",
                url="https://example.com/release",
                content="The current release is available.",
                provider="searxng",
                retrieved_at=datetime(2026, 10, 3, tzinfo=timezone.utc),
            )
        ]
        online_retriever.return_value = retriever
        get_engine.return_value = self._engine()

        _conversation, _text, metadata, error = AssistantService.process_message(
            "What's the latest Python release?"
        )

        self.assertIsNone(error)
        retriever.retrieve.assert_called_once()
        self.assertTrue(metadata["online_allowed"])
        self.assertTrue(metadata["online_used"])

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_ai_execution_plan")
    def test_protective_block_performs_no_ollama_generation(
        self,
        execution_policy,
        get_engine,
    ):
        execution_policy.return_value = _plan(
            profile="PROTECTIVE",
            effective_model=None,
            budget="MINIMAL",
            generation_allowed=False,
            local_ai_allowed=False,
            status_message=(
                "Local AI is temporarily unavailable because the device is "
                "protecting itself from a critical resource condition."
            ),
        )

        _conversation, text, metadata, error = AssistantService.process_message(
            "Explain recursion."
        )

        self.assertIsNone(error)
        self.assertIn("protecting itself", text)
        self.assertFalse(metadata["generation_allowed"])
        self.assertEqual(metadata["generation_budget"], "MINIMAL")
        get_engine.assert_not_called()

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_ai_execution_plan")
    def test_execution_metadata_reports_configured_and_effective_models(
        self,
        execution_policy,
        get_engine,
    ):
        execution_policy.return_value = _plan(
            profile="ECO",
            effective_model="llama3.2:1b",
            budget="REDUCED",
            status_message="Running in reduced-resource mode.",
        )
        engine = self._engine(model="llama3.2:1b")
        get_engine.return_value = engine

        _conversation, text, metadata, error = AssistantService.process_message(
            "Explain recursion."
        )

        self.assertIsNone(error)
        self.assertTrue(text.startswith("Running in reduced-resource mode."))
        self.assertEqual(metadata["configured_model"], "llama3.2:3b")
        self.assertEqual(metadata["effective_model"], "llama3.2:1b")
        self.assertEqual(metadata["generation_budget"], "REDUCED")
        self.assertIn("execution_ms", metadata)
        self.assertEqual(engine.generate.call_args.kwargs["num_predict"], 96)
        self.assertEqual(
            engine.generate.call_args.kwargs["model"],
            "llama3.2:1b",
        )

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_ai_execution_plan")
    def test_execution_plan_is_evaluated_once_per_request(
        self,
        execution_policy,
        get_engine,
    ):
        execution_policy.return_value = _plan()
        get_engine.return_value = self._engine()

        AssistantService.process_message("Explain recursion.")

        execution_policy.assert_called_once()


@override_settings(**SERVICE_SETTINGS, AI_GENERATION_NORMAL_VOICE_NUM_PREDICT=384,
                   VOICE_LLM_BRIEF_NUM_PREDICT=96, VOICE_LLM_NORMAL_NUM_PREDICT=160,
                   VOICE_LLM_DETAILED_NUM_PREDICT=384)
class VoiceResourceBudgetTests(TestCase):
    def setUp(self):
        self.plan = _plan()
        self.result = AIEngineResult(text="Complete answer.", engine="local", model="llama3.2:3b", latency_ms=1)
        self.engine = MagicMock()
        self.engine.available_models.return_value = {"llama3.2:1b", "llama3.2:3b"}
        self.engine.generate.side_effect = lambda *args, **kwargs: self.result
        self.engine.generate_stream.side_effect = lambda *args, **kwargs: iter([
            AIEngineStreamEvent("delta", text=self.result.text), AIEngineStreamEvent("done", result=self.result)])
        for patcher in (patch("assistant.services.get_engine", return_value=self.engine),
                        patch("assistant.services.get_ai_execution_plan", side_effect=lambda **kwargs: self.plan)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_healthy_voice_modes_and_four_sentences_receive_useful_bounded_budgets(self):
        for profile in ("BALANCED", "PERFORMANCE"):
            self.plan = _plan(profile=profile)
            for query, expected in (("Briefly explain RAM.", 96), ("Explain how Raspberry Pi works.", 160),
                                    ("Explain RAM in four short sentences.", 160),
                                    ("Explain RAM in detail with examples.", 384)):
                with self.subTest(profile=profile, query=query):
                    _, _, metadata, error = AssistantService.process_message(
                        query, response_plan=AssistantResponsePolicy.plan_voice_response(query))
                    self.assertIsNone(error)
                    self.assertEqual(self.engine.generate.call_args.kwargs["num_predict"], expected)
                    self.assertEqual(metadata["num_predict"], expected)
                    self.assertEqual(metadata["max_output_tokens"], 384)
                    self.assertEqual(metadata["effective_model"], "llama3.2:3b")
        _, _, metadata, error = AssistantService.process_message("Explain RAM in detail.")
        self.assertIsNone(error)
        self.assertEqual(metadata["num_predict"], 128)  # Text callers remain unchanged.
        self.assertEqual(metadata["max_output_tokens"], 128)

    def test_eco_and_protective_caps_still_win_for_every_voice_mode(self):
        for profile, budget, cap in (("ECO", "REDUCED", 96), ("PROTECTIVE", "MINIMAL", 48)):
            self.plan = _plan(profile=profile, budget=budget, effective_model="llama3.2:1b")
            for query in ("Briefly explain RAM.", "Explain RAM.", "Explain RAM in detail."):
                with self.subTest(profile=profile, query=query):
                    _, _, metadata, error = AssistantService.process_message(
                        query, response_plan=AssistantResponsePolicy.plan_voice_response(query))
                    self.assertIsNone(error)
                    self.assertEqual(metadata["num_predict"], cap)
                    self.assertEqual(metadata["max_output_tokens"], cap)
        self.plan = _plan(profile="PROTECTIVE", budget="MINIMAL", generation_allowed=False, local_ai_allowed=False)
        self.engine.reset_mock()
        AssistantService.process_message("Explain RAM in detail.",
                                         response_plan=AssistantResponsePolicy.plan_voice_response("Explain RAM in detail."))
        self.engine.generate.assert_not_called()

    def test_limit_metadata_is_truthful_and_done_event_contains_selected_voice_budget(self):
        query = "Explain RAM in detail."
        for count in (383, 384, 385, None, "384", True):
            with self.subTest(count=count):
                self.result.metrics = {} if count is None else {"eval_count": count}
                events = list(AssistantService.process_message_stream(
                    query, response_plan=AssistantResponsePolicy.plan_voice_response(query)))
                self.assertEqual(events[0]["num_predict"], 384)
                done = events[-1]
                self.assertEqual(done["type"], "done")
                self.assertEqual(done["num_predict"], 384)
                if type(count) is int:
                    self.assertEqual(done["eval_count"], count)
                    self.assertEqual(done["output_limit_reached"], count >= 384)
                else:
                    self.assertNotIn("output_limit_reached", done)
                    self.assertNotIn("eval_count", done)
        self.assertEqual(self.engine.generate_stream.call_count, 6)
        self.engine.generate.assert_not_called()

    def test_observed_eco_limit_exhaustion_keeps_canonical_fragment_truthful(self):
        self.plan = _plan(profile="ECO", budget="REDUCED", effective_model="llama3.2:1b",
                          status_message="Running in reduced-resource mode.")
        self.result.text = "Solar panels are a renewable energy source that provides a"
        self.result.metrics = {"eval_count": 96}
        query = "Explain solar panels in detail."
        events = list(AssistantService.process_message_stream(
            query, response_plan=AssistantResponsePolicy.plan_voice_response(query)))
        done = events[-1]
        self.assertEqual(done["num_predict"], 96)
        self.assertTrue(done["output_limit_reached"])
        self.assertEqual(done["response"], "Running in reduced-resource mode.\n\n" + self.result.text)
        self.assertEqual(Message.objects.get(sender="AI").text, done["response"])


@override_settings(**SERVICE_SETTINGS)
class ResourceAwareAssistantAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/assistant/chat/"

    @staticmethod
    def _engine(side_effect=None):
        engine = MagicMock()
        if side_effect is None:
            engine.generate.return_value = AIEngineResult(
                text="Policy-selected answer.",
                engine="local",
                model="llama3.2:1b",
                latency_ms=5,
            )
        else:
            engine.generate.side_effect = side_effect
        return engine

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_ai_execution_plan")
    def test_client_cannot_override_server_selected_model(
        self,
        execution_policy,
        get_engine,
    ):
        execution_policy.return_value = _plan(
            profile="ECO",
            effective_model="llama3.2:1b",
            budget="REDUCED",
        )
        engine = self._engine()
        get_engine.return_value = engine

        response = self.client.post(
            self.url,
            {"query": "Explain recursion.", "model": "attacker/model:latest"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            engine.generate.call_args.kwargs["model"],
            "llama3.2:1b",
        )
        self.assertEqual(response.data["configured_model"], "llama3.2:3b")
        self.assertEqual(response.data["effective_model"], "llama3.2:1b")
        self.assertNotIn("attacker/model", str(response.data))

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_ai_execution_plan")
    def test_generation_timeout_is_distinct_from_unreachable_ollama(
        self,
        execution_policy,
        get_engine,
    ):
        execution_policy.return_value = _plan()
        get_engine.return_value = self._engine(
            EngineTimeoutError("bounded timeout")
        )

        response = self.client.post(
            self.url,
            {"query": "Explain recursion."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_504_GATEWAY_TIMEOUT)
        self.assertEqual(response.data["error"], "Generation timeout")
        self.assertNotIn("bounded timeout", str(response.data))

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_ai_execution_plan")
    def test_unreachable_ollama_remains_service_unavailable(
        self,
        execution_policy,
        get_engine,
    ):
        execution_policy.return_value = _plan()
        get_engine.return_value = self._engine(
            EngineUnavailableError("connection refused at private host")
        )

        response = self.client.post(
            self.url,
            {"query": "Explain recursion."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["error"], "AI unavailable")
        self.assertNotIn("private host", str(response.data))
