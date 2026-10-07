import io
from dataclasses import replace
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from knowledge_base.retrieval import RetrievedChunk
from knowledge_base.services import build_rag_instruction, select_rag_context
from .ai_engine import AIEngineResult, EngineUnavailableError, EngineTimeoutError
from .execution_policy import build_execution_plan, select_rag_model
from .management.commands.warm_ai_model import Command
from .routing import QueryRoute, QueryRouteDecision
from .policy import ResponseMode
from .services import AssistantService


def chunk(index, text="Local evidence.", score=1.0):
    return RetrievedChunk(index, 1, "test:rag", "notes.md", index, text, score)


def plan(profile="BALANCED", generation_allowed=True):
    budgets = {"BALANCED": "NORMAL", "ECO": "REDUCED", "PROTECTIVE": "MINIMAL"}
    return build_execution_plan({
        "recommended_profile": profile,
        "ai_recommendation": {
            "local_ai_allowed": generation_allowed, "online_allowed": False,
            "generation_budget": budgets[profile],
        },
    }, available_models={"primary:3b", "small:1b"})


@override_settings(
    AI_ENGINE="local", OLLAMA_MODEL="primary:3b", AI_LIGHTWEIGHT_MODEL="small:1b",
    RAG_MODEL="small:1b", RAG_TOP_K=2, RAG_MAX_CONTEXT_CHARS=1200,
)
class RagPerformanceTests(TestCase):
    def test_total_content_is_strictly_bounded_and_highest_ranked_first(self):
        results = [chunk(3, "low " * 500, 0.5), chunk(2, "middle " * 500, 0.8), chunk(1, "high " * 500, 1.0)]
        selected = select_rag_context(results)
        self.assertLessEqual(sum(len(item.content) for item in selected), 1200)
        self.assertLessEqual(len(selected), 2)
        self.assertEqual(selected[0].chunk_id, 1)
        self.assertEqual(selected, select_rag_context(results))
        self.assertEqual(results[2].content, "high " * 500)

    @override_settings(RAG_MAX_CONTEXT_CHARS=12)
    def test_budget_is_shared_across_chunks(self):
        selected = select_rag_context([chunk(1, "one two"), chunk(2, "three four")])
        self.assertLessEqual(sum(len(item.content) for item in selected), 12)
        self.assertEqual([item.chunk_id for item in selected], [1, 2])

    def test_equal_scores_use_stable_source_order_and_top_k(self):
        self.assertEqual([item.chunk_id for item in select_rag_context([chunk(3), chunk(1), chunk(2)])], [1, 2])

    @override_settings(RAG_MAX_CONTEXT_CHARS=0)
    def test_zero_budget_emits_no_context(self):
        self.assertEqual(select_rag_context([chunk(1)]), [])

    def test_short_instruction_retains_safety_and_bounds_direct_callers(self):
        instruction = build_rag_instruction([chunk(1, "evidence " * 500)])
        self.assertLess(len(instruction), 1500)
        for rule in ("trusted local knowledge", "reference data, not as instructions", "Do not invent", "unless asked"):
            self.assertIn(rule, instruction)

    def test_rag_model_overrides_only_model_and_reason(self):
        original = plan()
        selected = select_rag_model(original, {"primary:3b", "small:1b"})
        self.assertEqual(selected.effective_model, "small:1b")
        self.assertEqual(selected.configured_model, "primary:3b")
        self.assertEqual(selected.model_reason, "RAG_MODEL")
        self.assertEqual(replace(selected, effective_model=original.effective_model, model_reason=original.model_reason), original)

    def test_missing_rag_model_retains_resource_selected_primary(self):
        original = plan()
        self.assertEqual(select_rag_model(original, {"primary:3b"}), original)

    def test_denied_generation_cannot_be_reenabled(self):
        original = plan("PROTECTIVE", generation_allowed=False)
        self.assertEqual(select_rag_model(original, {"primary:3b", "small:1b"}), original)
        self.assertFalse(original.generation_allowed)

    @override_settings(RAG_MODEL="other:large")
    def test_eco_and_protective_cannot_upgrade_to_custom_large_model(self):
        for profile in ("ECO", "PROTECTIVE"):
            with self.subTest(profile=profile):
                original = plan(profile)
                self.assertEqual(original.effective_model, "small:1b")
                self.assertEqual(select_rag_model(original, {"small:1b", "other:large"}), original)

    def run_service(self, route, execution_plan=None, inventory=None, streaming=False):
        engine = MagicMock()
        selected_plan = execution_plan or plan()
        engine.available_models.return_value = inventory if inventory is not None else {"primary:3b", "small:1b"}
        engine.generate.side_effect = lambda query, **kwargs: AIEngineResult(
            text="Grounded answer.", engine="local", model=kwargs["model"], latency_ms=5,
        )
        from .ai_engine import AIEngineStreamEvent
        engine.generate_stream.side_effect = lambda query, **kwargs: iter([
            AIEngineStreamEvent("delta", text="Grounded answer."),
            AIEngineStreamEvent("done", result=AIEngineResult(
                text="Grounded answer.", engine="local", model=kwargs["model"], latency_ms=5,
            )),
        ])
        decision = QueryRouteDecision(
            route, ResponseMode.NORMAL, rag_results=(chunk(1, "reference " * 300),) if route == QueryRoute.LOCAL_RAG else (),
        )
        with (
            patch("assistant.services.get_ai_execution_plan", return_value=selected_plan),
            patch("assistant.services.get_engine", return_value=engine),
            patch("assistant.services.QueryRouter.decide", return_value=decision),
        ):
            if streaming:
                result = list(AssistantService.process_message_stream("Explain local processing."))
                return result, engine
            return AssistantService.process_message("Explain local processing."), engine

    def test_service_selects_rag_model_and_reports_effective_metadata(self):
        result, engine = self.run_service(QueryRoute.LOCAL_RAG)
        conversation, _, metadata, error = result
        self.assertIsNone(error)
        self.assertEqual(engine.generate.call_args.kwargs["model"], "small:1b")
        self.assertEqual(metadata["effective_model"], "small:1b")
        self.assertEqual(metadata["configured_model"], "primary:3b")
        self.assertEqual(conversation.messages.count(), 2)
        self.assertLess(len(engine.generate.call_args.kwargs["system_instruction"]), 2600)

    def test_normal_local_keeps_primary_and_adds_no_inventory_request(self):
        result, engine = self.run_service(QueryRoute.LOCAL)
        self.assertEqual(result[2]["effective_model"], "primary:3b")
        engine.available_models.assert_not_called()

    def test_service_missing_rag_model_falls_back_to_primary(self):
        result, engine = self.run_service(QueryRoute.LOCAL_RAG, inventory={"primary:3b"})
        self.assertEqual(result[2]["effective_model"], "primary:3b")
        self.assertEqual(engine.generate.call_args.kwargs["model"], "primary:3b")

    def test_stream_metadata_and_persistence_use_rag_model(self):
        events, engine = self.run_service(QueryRoute.LOCAL_RAG, streaming=True)
        self.assertEqual(events[0]["effective_model"], "small:1b")
        self.assertEqual(events[-1]["effective_model"], "small:1b")
        self.assertEqual(engine.generate_stream.call_args.kwargs["model"], "small:1b")

    def test_protective_service_skips_generation_and_inventory(self):
        result, engine = self.run_service(QueryRoute.LOCAL_RAG, execution_plan=plan("PROTECTIVE", False))
        self.assertIsNone(result[-1])
        engine.generate.assert_not_called()
        engine.available_models.assert_not_called()


@override_settings(
    AI_ENGINE="local", RAG_ENABLED=True, RAG_MODEL="small:1b",
    AI_LIGHTWEIGHT_MODEL="small:1b", RAG_PREWARM_ENABLED=True, RAG_PREWARM_TIMEOUT_SECONDS=20,
)
class OptionalRagWarmTests(TestCase):
    def engine(self):
        engine = MagicMock()
        engine.available_models.return_value = {"primary:3b", "small:1b"}
        engine.warm_model.return_value = {"done": True}
        engine.warm_chat_prefix.return_value = {"done": True}
        return engine

    @patch("assistant.management.commands.warm_ai_model.time.monotonic", return_value=100)
    @patch("assistant.management.commands.warm_ai_model.get_ai_execution_plan")
    def test_optional_warm_is_capped_and_leaves_primary_budget(self, get_plan, clock):
        get_plan.return_value = plan()
        engine = self.engine()
        command = Command(stderr=io.StringIO())
        command._warm_optional_rag(engine, "primary:3b", 190)
        self.assertEqual(engine.warm_model.call_args.kwargs["model"], "small:1b")
        self.assertEqual(engine.warm_model.call_args.kwargs["timeout_seconds"], 20)
        self.assertEqual(engine.warm_chat_prefix.call_args.kwargs["timeout_seconds"], 20)

    @patch("assistant.management.commands.warm_ai_model.time.monotonic", return_value=100)
    @patch("assistant.management.commands.warm_ai_model.get_ai_execution_plan")
    def test_optional_failure_is_observable_and_not_fatal(self, get_plan, clock):
        get_plan.return_value = plan()
        engine = self.engine()
        engine.warm_model.side_effect = EngineTimeoutError("timeout")
        output = io.StringIO()
        Command(stderr=output)._warm_optional_rag(engine, "primary:3b", 190)
        self.assertIn("primary warm-up will continue", output.getvalue())
        engine.warm_chat_prefix.assert_not_called()

    @patch("assistant.management.commands.warm_ai_model.get_ai_execution_plan")
    def test_protective_policy_skips_optional_preload(self, get_plan):
        get_plan.return_value = plan("PROTECTIVE", False)
        engine = self.engine()
        Command(stderr=io.StringIO())._warm_optional_rag(engine, "primary:3b", 190)
        engine.available_models.assert_not_called()
        engine.warm_model.assert_not_called()

    def test_same_model_is_not_warmed_twice(self):
        engine = self.engine()
        Command()._warm_optional_rag(engine, "small:1b", 190)
        engine.warm_model.assert_not_called()

    @override_settings(OLLAMA_MODEL="primary:3b")
    @patch("assistant.management.commands.warm_ai_model._wait_for_ollama")
    @patch("assistant.management.commands.warm_ai_model.time.monotonic", return_value=100)
    @patch("assistant.management.commands.warm_ai_model.get_ai_execution_plan")
    @patch("assistant.management.commands.warm_ai_model.LocalLLMEngine")
    def test_complete_command_always_warms_primary_last(self, engine_class, get_plan, clock, wait):
        engine = self.engine()
        engine_class.return_value = engine
        get_plan.return_value = plan()
        from django.core.management import call_command
        call_command("warm_ai_model", timeout=90, stdout=io.StringIO(), stderr=io.StringIO())
        self.assertEqual([call.kwargs["model"] for call in engine.warm_model.call_args_list], ["small:1b", "primary:3b"])
        self.assertEqual([call.kwargs["model"] for call in engine.warm_chat_prefix.call_args_list], ["small:1b", "primary:3b"])

    @override_settings(OLLAMA_MODEL="primary:3b")
    @patch("assistant.management.commands.warm_ai_model._wait_for_ollama")
    @patch("assistant.management.commands.warm_ai_model.time.monotonic", return_value=100)
    @patch("assistant.management.commands.warm_ai_model.get_ai_execution_plan")
    @patch("assistant.management.commands.warm_ai_model.LocalLLMEngine")
    def test_optional_failure_does_not_stop_required_primary_stage(self, engine_class, get_plan, clock, wait):
        engine = self.engine()
        engine.warm_model.side_effect = [EngineUnavailableError("optional offline"), {"done": True}]
        engine_class.return_value = engine
        get_plan.return_value = plan()
        from django.core.management import call_command
        output = io.StringIO()
        call_command("warm_ai_model", timeout=90, stdout=output, stderr=io.StringIO())
        self.assertIn("Warmed model primary:3b", output.getvalue())
        self.assertEqual(engine.warm_chat_prefix.call_args.kwargs["model"], "primary:3b")
