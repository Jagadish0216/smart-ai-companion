from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import TestCase, override_settings

from assistant.ai_engine.base import AIEngineResult
from assistant.ai_engine.mock import MockAIEngine
from assistant.policy import (
    AssistantResponsePolicy,
    Capability,
    CapabilityRegistry,
    CapabilityStatus,
    ResponseMode,
)
from assistant.services import AssistantService


class AssistantResponsePolicyTests(TestCase):
    def assertClassification(self, query, expected_mode, expected_capability=None):
        mode, capability = AssistantResponsePolicy.classify(query)
        self.assertEqual(mode, expected_mode, query)
        self.assertEqual(capability, expected_capability, query)

    def test_brief_request_classification(self):
        mode, capability = AssistantResponsePolicy.classify(
            "Quickly tell me what RAM means."
        )
        self.assertEqual(mode, ResponseMode.BRIEF)
        self.assertIsNone(capability)

    def test_normal_request_classification(self):
        mode, capability = AssistantResponsePolicy.classify("What is RAM?")
        self.assertEqual(mode, ResponseMode.NORMAL)
        self.assertIsNone(capability)

    def test_detailed_request_classification(self):
        mode, capability = AssistantResponsePolicy.classify(
            "Explain machine learning step by step with examples."
        )
        self.assertEqual(mode, ResponseMode.DETAILED)
        self.assertIsNone(capability)

    def test_action_request_classification(self):
        mode, capability = AssistantResponsePolicy.classify("Turn on the light.")
        self.assertEqual(mode, ResponseMode.ACTION)
        self.assertEqual(capability, Capability.DEVICE_CONTROL)

    def test_ambiguous_request_prefers_clarification(self):
        mode, capability = AssistantResponsePolicy.classify("Do that again.")
        self.assertEqual(mode, ResponseMode.CLARIFICATION)
        self.assertIsNone(capability)

    def test_capability_education_is_not_classified_as_action(self):
        cases = (
            ("Explain how to control an ESP32 relay.", ResponseMode.NORMAL),
            ("How do I control an ESP32 from a Raspberry Pi?", ResponseMode.NORMAL),
            ("How does a temperature sensor work?", ResponseMode.NORMAL),
            ("Tell me about temperature sensors.", ResponseMode.NORMAL),
            (
                "Explain how to measure room temperature with an ESP32.",
                ResponseMode.NORMAL,
            ),
            ("How can I control a light using an ESP32?", ResponseMode.NORMAL),
            ("How can I control lights using an ESP32?", ResponseMode.NORMAL),
            ("Explain relay control.", ResponseMode.NORMAL),
            ("Tell me about smart lighting.", ResponseMode.NORMAL),
            ("How do reminders work?", ResponseMode.NORMAL),
            ("How can I build a reminder system in Python?", ResponseMode.NORMAL),
            ("Explain camera vision in detail.", ResponseMode.DETAILED),
            ("What is device control?", ResponseMode.NORMAL),
            ("Explain how a robot can navigate a room.", ResponseMode.NORMAL),
        )

        for query, expected_mode in cases:
            with self.subTest(query=query):
                self.assertClassification(query, expected_mode)

    def test_explicit_capability_execution_is_classified_as_action(self):
        cases = (
            ("Turn on the light.", Capability.DEVICE_CONTROL),
            ("Can you control my lights?", Capability.DEVICE_CONTROL),
            ("Turn on the lights.", Capability.DEVICE_CONTROL),
            ("Switch on the light.", Capability.DEVICE_CONTROL),
            ("Switch the lights off.", Capability.DEVICE_CONTROL),
            ("Turn the fan on.", Capability.DEVICE_CONTROL),
            ("Switch off the fan.", Capability.DEVICE_CONTROL),
            ("Switch the fan off.", Capability.DEVICE_CONTROL),
            ("Check the room temperature.", Capability.ENVIRONMENT_SENSING),
            ("Take the room temperature.", Capability.ENVIRONMENT_SENSING),
            ("Measure the room temperature.", Capability.ENVIRONMENT_SENSING),
            ("What's the room temperature?", Capability.ENVIRONMENT_SENSING),
            ("Check the humidity.", Capability.ENVIRONMENT_SENSING),
            ("Measure the humidity.", Capability.ENVIRONMENT_SENSING),
            ("Read the humidity.", Capability.ENVIRONMENT_SENSING),
            ("Remind me at 6 PM.", Capability.REMINDERS),
            ("Set an alarm for 7.", Capability.REMINDERS),
            ("Scan the room.", Capability.CAMERA_VISION),
            ("What can you see?", Capability.CAMERA_VISION),
            ("Move forward.", Capability.MOVEMENT),
            ("Search my documents for the report.", Capability.LOCAL_RAG),
            ("Search the web for today's AI news.", Capability.ONLINE_RETRIEVAL),
            ("Keep an eye on the room.", Capability.CAMERA_VISION),
        )

        for query, capability in cases:
            with self.subTest(query=query):
                self.assertClassification(query, ResponseMode.ACTION, capability)

    def test_mixed_information_and_execution_keeps_action(self):
        self.assertClassification(
            "Explain what's happening and turn on the light.",
            ResponseMode.ACTION,
            Capability.DEVICE_CONTROL,
        )

    def test_voice_instruction_requests_natural_spoken_delivery(self):
        normal = AssistantResponsePolicy.plan_voice_response("What is edge computing?")
        detailed = AssistantResponsePolicy.plan_voice_response(
            "Explain edge computing in detail."
        )

        self.assertIn("spoken personal-assistant response", normal.system_instruction)
        self.assertIn("Answer the user's actual request immediately", normal.system_instruction)
        self.assertIn("natural, connected spoken sentences", normal.system_instruction)
        self.assertIn("Don't repeat the question", normal.system_instruction)
        self.assertIn("Avoid article-style prose", normal.system_instruction)
        self.assertIn("do not impose a short response limit", detailed.system_instruction)
        self.assertEqual(detailed.mode, ResponseMode.DETAILED)

    @override_settings(
        VOICE_LLM_BRIEF_NUM_PREDICT="91",
        VOICE_LLM_NORMAL_NUM_PREDICT="161",
        VOICE_LLM_DETAILED_NUM_PREDICT="385",
    )
    def test_voice_modes_receive_request_scoped_budgets(self):
        brief = AssistantResponsePolicy.plan_voice_response("Answer briefly: what is RAM?")
        normal = AssistantResponsePolicy.plan_voice_response("What is RAM?")
        detailed = AssistantResponsePolicy.plan_voice_response(
            "Explain RAM in detail with examples."
        )

        self.assertEqual(brief.num_predict, 91)
        self.assertEqual(normal.num_predict, 161)
        self.assertEqual(detailed.num_predict, 385)
        self.assertEqual(brief.mode, ResponseMode.BRIEF)
        self.assertEqual(normal.mode, ResponseMode.NORMAL)
        self.assertEqual(detailed.mode, ResponseMode.DETAILED)

    @override_settings(
        AI_ENGINE="local",
        STT_ENGINE="whisper_cpp",
        TTS_ENGINE="piper",
    )
    def test_registry_reports_only_current_configured_capabilities(self):
        registry = CapabilityRegistry.from_settings()

        self.assertTrue(registry.is_available(Capability.LOCAL_CONVERSATION))
        self.assertTrue(registry.is_available(Capability.CONVERSATION_HISTORY))
        self.assertTrue(registry.is_available(Capability.LOCAL_LLM))
        self.assertTrue(registry.is_available(Capability.OFFLINE_STT))
        self.assertTrue(registry.is_available(Capability.OFFLINE_TTS))
        self.assertFalse(registry.is_available(Capability.LOCAL_RAG))
        self.assertFalse(registry.is_available(Capability.ONLINE_RETRIEVAL))
        self.assertFalse(registry.is_available(Capability.ENVIRONMENT_SENSING))
        self.assertFalse(registry.is_available(Capability.CAMERA_VISION))
        self.assertFalse(registry.is_available(Capability.DEVICE_CONTROL))
        self.assertFalse(registry.is_available(Capability.MOVEMENT))
        self.assertFalse(registry.is_available(Capability.REMINDERS))

    def test_grounding_instruction_is_built_from_registry_state(self):
        registry = CapabilityRegistry([
            CapabilityStatus(
                Capability.LOCAL_CONVERSATION,
                True,
                "Configured available capability",
            ),
            CapabilityStatus(
                Capability.REMINDERS,
                False,
                "Configured unavailable capability",
            ),
        ])

        instruction = registry.build_grounding_instruction()

        self.assertIn("Available now: Configured available capability", instruction)
        self.assertIn(
            "Unavailable now: Configured unavailable capability",
            instruction,
        )
        self.assertIn("Only claim capabilities listed as available", instruction)
        self.assertIn("may still explain", instruction)
        self.assertIn("never refer", instruction)

    def test_unavailable_sensor_request_is_grounded(self):
        plan = AssistantResponsePolicy.plan_voice_response(
            "Check the room temperature."
        )

        self.assertEqual(plan.mode, ResponseMode.ACTION)
        self.assertEqual(plan.required_capability, Capability.ENVIRONMENT_SENSING)
        self.assertIn("can't check", plan.direct_response)
        self.assertIn("sensor", plan.direct_response)
        self.assertNotIn("checking", plan.direct_response.lower())


class AssistantPolicyServiceTests(TestCase):
    @patch("assistant.services.get_engine")
    def test_normal_request_receives_capability_grounding(self, mock_get_engine):
        engine = MagicMock()
        engine.generate.return_value = AIEngineResult(
            text="Sure, talk to you later.",
            engine="local",
            latency_ms=1,
        )
        mock_get_engine.return_value = engine

        conversation, text, _metadata, error = AssistantService.process_message(
            "I'll talk to you later."
        )

        instruction = engine.generate.call_args.kwargs["system_instruction"]
        self.assertIsNone(error)
        self.assertEqual(text, "Sure, talk to you later.")
        self.assertNotIn("remind", text.lower())
        self.assertIn("Reminder, alarm, and timer scheduling", instruction)
        self.assertIn("Never say an unavailable capability was performed", instruction)
        self.assertEqual(conversation.messages.count(), 2)

    @patch("assistant.services.get_engine")
    def test_detailed_request_receives_voice_policy_and_grounding(
        self,
        mock_get_engine,
    ):
        engine = MagicMock()
        engine.generate.return_value = AIEngineResult(
            text="A detailed answer.",
            engine="local",
            latency_ms=1,
        )
        mock_get_engine.return_value = engine
        query = "Explain edge computing in detail."
        plan = AssistantResponsePolicy.plan_voice_response(query)

        AssistantService.process_message(query, response_plan=plan)

        instruction = engine.generate.call_args.kwargs["system_instruction"]
        self.assertIn(plan.system_instruction, instruction)
        self.assertIn("Use this runtime capability state", instruction)
        self.assertIn("do not impose a short response limit", instruction)

    @patch("assistant.services.get_engine")
    def test_educational_capability_request_reaches_engine(self, mock_get_engine):
        engine = MagicMock()
        engine.generate.return_value = AIEngineResult(
            text="An ESP32 can drive a relay through a suitable interface circuit.",
            engine="local",
            latency_ms=2,
        )
        mock_get_engine.return_value = engine
        query = "Explain how to control an ESP32 relay."
        plan = AssistantResponsePolicy.plan_voice_response(query)

        conversation, text, _metadata, error = AssistantService.process_message(
            query,
            response_plan=plan,
        )

        self.assertEqual(plan.mode, ResponseMode.NORMAL)
        self.assertIsNone(plan.required_capability)
        self.assertIsNone(plan.direct_response)
        self.assertIsNone(error)
        self.assertIn("ESP32", text)
        engine.generate.assert_called_once()
        self.assertEqual(conversation.messages.count(), 2)

    @patch("assistant.services.get_engine")
    def test_unavailable_action_never_calls_engine_or_claims_success(
        self,
        mock_get_engine,
    ):
        plan = AssistantResponsePolicy.plan_voice_response(
            "Check the room temperature."
        )

        conversation, text, metadata, error = AssistantService.process_message(
            "Check the room temperature.",
            response_plan=plan,
        )

        self.assertIsNone(error)
        self.assertEqual(text, plan.direct_response)
        self.assertEqual(metadata["engine"], "policy")
        mock_get_engine.assert_not_called()
        self.assertEqual(
            list(
                conversation.messages.order_by("id").values_list("sender", "text")
            ),
            [
                ("USER", "Check the room temperature."),
                ("AI", plan.direct_response),
            ],
        )

    @patch("assistant.services.get_engine")
    def test_request_scoped_metadata_is_forwarded_but_not_persisted(
        self,
        mock_get_engine,
    ):
        engine = MagicMock()
        engine.generate.return_value = AIEngineResult(
            text="A complete grounded answer.",
            engine="local",
            model="test-model",
            latency_ms=3,
        )
        mock_get_engine.return_value = engine
        plan = AssistantResponsePolicy.plan_voice_response(
            "Explain edge computing in detail."
        )

        conversation, text, _metadata, error = AssistantService.process_message(
            "Explain edge computing in detail.",
            response_plan=plan,
        )

        self.assertIsNone(error)
        self.assertEqual(text, "A complete grounded answer.")
        self.assertEqual(
            engine.generate.call_args.kwargs["num_predict"],
            plan.num_predict,
        )
        request_instruction = engine.generate.call_args.kwargs["system_instruction"]
        self.assertIn(plan.system_instruction, request_instruction)
        self.assertIn("Use this runtime capability state", request_instruction)
        persisted_text = list(
            conversation.messages.order_by("id").values_list("sender", "text")
        )
        self.assertEqual(
            persisted_text,
            [
                ("USER", "Explain edge computing in detail."),
                ("AI", "A complete grounded answer."),
            ],
        )
        self.assertNotIn(str(plan.num_predict), str(persisted_text))
        self.assertNotIn(plan.system_instruction, str(persisted_text))
        self.assertNotIn("Use this runtime capability state", str(persisted_text))

    @patch("assistant.services.get_engine")
    @override_settings(
        AI_ENGINE="local",
        STT_ENGINE="whisper_cpp",
        TTS_ENGINE="piper",
    )
    def test_request_grounding_does_not_mutate_settings(self, mock_get_engine):
        engine = MagicMock()
        engine.generate.return_value = AIEngineResult(
            text="Grounded response.",
            engine="local",
            latency_ms=1,
        )
        mock_get_engine.return_value = engine
        configured_engines = (
            settings.AI_ENGINE,
            settings.STT_ENGINE,
            settings.TTS_ENGINE,
        )

        AssistantService.process_message("What can you do?")

        self.assertEqual(
            (settings.AI_ENGINE, settings.STT_ENGINE, settings.TTS_ENGINE),
            configured_engines,
        )

    @patch("assistant.services.get_engine")
    def test_mock_capability_summary_does_not_claim_unavailable_features(
        self,
        mock_get_engine,
    ):
        engine = MockAIEngine()
        engine.SIMULATED_DELAY_S = 0
        mock_get_engine.return_value = engine

        _conversation, text, _metadata, error = AssistantService.process_message(
            "What can you do?"
        )

        self.assertIsNone(error)
        self.assertIn("answer general questions", text)
        self.assertIn("can't yet", text)
        self.assertNotIn("I can control", text)

    @patch("assistant.services.get_engine")
    def test_information_questions_reach_llm_instead_of_action_refusal(
        self,
        mock_get_engine,
    ):
        engine = MagicMock()
        engine.generate.return_value = AIEngineResult(
            text="An informational explanation.",
            engine="local",
            latency_ms=1,
        )
        mock_get_engine.return_value = engine

        for query in (
            "How can I control lights using an ESP32?",
            "How do reminders work?",
        ):
            with self.subTest(query=query):
                plan = AssistantResponsePolicy.plan_voice_response(query)
                _conversation, text, _metadata, error = (
                    AssistantService.process_message(query, response_plan=plan)
                )

                self.assertEqual(plan.mode, ResponseMode.NORMAL)
                self.assertIsNone(plan.direct_response)
                self.assertIsNone(error)
                self.assertEqual(text, "An informational explanation.")

        self.assertEqual(engine.generate.call_count, 2)

    @patch("assistant.services.get_engine")
    def test_monitoring_request_uses_grounded_direct_response(self, mock_get_engine):
        query = "Keep an eye on the room."
        plan = AssistantResponsePolicy.plan_voice_response(query)

        _conversation, text, metadata, error = AssistantService.process_message(
            query,
            response_plan=plan,
        )

        self.assertEqual(plan.mode, ResponseMode.ACTION)
        self.assertEqual(plan.required_capability, Capability.CAMERA_VISION)
        self.assertIsNone(error)
        self.assertEqual(text, plan.direct_response)
        self.assertEqual(metadata["engine"], "policy")
        self.assertIn("can't inspect", text.lower())
        mock_get_engine.assert_not_called()

    @patch("assistant.services.get_engine")
    def test_device_control_question_uses_grounded_direct_response(
        self,
        mock_get_engine,
    ):
        query = "Can you control my lights?"
        plan = AssistantResponsePolicy.plan_voice_response(query)

        _conversation, text, metadata, error = AssistantService.process_message(
            query,
            response_plan=plan,
        )

        self.assertEqual(plan.mode, ResponseMode.ACTION)
        self.assertEqual(plan.required_capability, Capability.DEVICE_CONTROL)
        self.assertIsNone(error)
        self.assertEqual(text, plan.direct_response)
        self.assertEqual(metadata["engine"], "policy")
        self.assertIn("can't control", text.lower())
        mock_get_engine.assert_not_called()
