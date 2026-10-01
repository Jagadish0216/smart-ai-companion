import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from assistant.ai_engine.base import AIEngineResult
from assistant.devices.base import (
    DeviceCommandTimeoutError,
    DeviceControllerError,
    DevicePublishError,
    DeviceUnavailableError,
)
from assistant.devices.factory import (
    is_device_control_available,
    is_environment_sensing_available,
    normalize_mqtt_host,
)
from assistant.devices.intents import map_device_command
from assistant.devices.mqtt import (
    MAX_MQTT_PAYLOAD_BYTES,
    MQTTDeviceController,
    validate_response_payload,
)
from assistant.devices.topics import MQTTTopics
from assistant.devices.types import DeviceAction, DeviceActionResult, DeviceCommand
from assistant.policy import (
    AssistantResponsePolicy,
    Capability,
    CapabilityRegistry,
    ResponseMode,
)
from assistant.routing import QueryRoute, QueryRouteDecision
from assistant.services import AssistantService
from knowledge_base.retrieval import RetrievedChunk


class _FakePublishInfo:
    def __init__(self, *, rc=0, published=True):
        self.rc = rc
        self._published = published

    def wait_for_publish(self, timeout=None):
        return None

    def is_published(self):
        return self._published


class _FakeMQTTClient:
    def __init__(
        self,
        response_factory=None,
        *,
        connect_error=None,
        connect_reason=0,
        subscribe_code=0,
        publish_code=0,
    ):
        self.response_factory = response_factory
        self.connect_error = connect_error
        self.connect_reason = connect_reason
        self.subscribe_code = subscribe_code
        self.publish_code = publish_code
        self.on_connect = None
        self.on_connect_fail = None
        self.on_subscribe = None
        self.on_message = None
        self.published = []
        self.disconnected = False
        self.loop_stopped = False

    def connect_async(self, host, port, keepalive):
        self.connection = (host, port, keepalive)
        if self.connect_error:
            raise self.connect_error
        return None

    def loop_start(self):
        self.on_connect(self, None, None, self.connect_reason, None)

    def subscribe(self, topic, qos):
        if self.subscribe_code == 0:
            self.on_subscribe(self, None, 1, [0], None)
        return self.subscribe_code, 1

    def publish(self, topic, payload, qos, retain):
        self.published.append((topic, payload, qos, retain))
        if self.publish_code == 0 and self.response_factory is not None:
            response = self.response_factory(json.loads(payload))
            if response is not None:
                response_payload = (
                    response
                    if isinstance(response, bytes)
                    else json.dumps(response).encode("utf-8")
                )
                self.on_message(self, None, SimpleNamespace(
                    topic=topic.replace("/command", "/response"),
                    payload=response_payload,
                ))
        return _FakePublishInfo(rc=self.publish_code)

    def disconnect(self):
        self.disconnected = True

    def loop_stop(self):
        self.loop_stopped = True


def _response_for(command_payload, *, result=None, ok=True, error=""):
    response = {
        "version": 1,
        "request_id": command_payload["request_id"],
        "ok": ok,
        "action": command_payload["action"],
    }
    if ok:
        response["result"] = result or {}
    else:
        response["error"] = error
    return response


def _controller(client):
    return MQTTDeviceController(
        host="127.0.0.1",
        port=1883,
        keepalive=30,
        timeout_seconds=0.01,
        device_id="companion-esp32-01",
        topic_prefix="smart-companion",
        client_factory=lambda _client_id: client,
        request_id_factory=lambda: "request-123",
    )


class MQTTProtocolTests(SimpleTestCase):
    def test_valid_led_on_acknowledgment(self):
        client = _FakeMQTTClient(
            lambda command: _response_for(command, result={"state": True})
        )

        result = _controller(client).execute(DeviceCommand.set_led(True))

        self.assertTrue(result.success)
        self.assertEqual(result.action, DeviceAction.SET_LED)
        self.assertEqual(result.result, {"state": True})
        topic, raw_payload, qos, retain = client.published[0]
        self.assertEqual(topic, "smart-companion/companion-esp32-01/command")
        self.assertEqual(qos, 1)
        self.assertFalse(retain)
        self.assertEqual(json.loads(raw_payload), {
            "version": 1,
            "request_id": "request-123",
            "action": "set_led",
            "params": {"state": True},
        })
        self.assertTrue(client.disconnected)
        self.assertTrue(client.loop_stopped)

    def test_valid_led_off_acknowledgment(self):
        client = _FakeMQTTClient(
            lambda command: _response_for(command, result={"state": False})
        )

        result = _controller(client).execute(DeviceCommand.set_led(False))

        self.assertTrue(result.success)
        self.assertEqual(result.result["state"], False)

    def test_valid_temperature_response(self):
        client = _FakeMQTTClient(
            lambda command: _response_for(
                command,
                result={"temperature_c": 28.4},
            )
        )

        result = _controller(client).execute(DeviceCommand.read_temperature())

        self.assertTrue(result.success)
        self.assertEqual(result.result, {"temperature_c": 28.4})

    def test_broker_unavailable_is_typed_failure_and_client_is_reaped(self):
        client = _FakeMQTTClient(connect_error=OSError("broker unavailable"))

        with self.assertRaises(DeviceUnavailableError):
            _controller(client).execute(DeviceCommand.set_led(True))

        self.assertTrue(client.disconnected)

    def test_timeout_never_reports_false_success(self):
        client = _FakeMQTTClient(response_factory=lambda _command: None)

        with self.assertRaises(DeviceCommandTimeoutError):
            _controller(client).execute(DeviceCommand.set_led(True))

        self.assertTrue(client.disconnected)
        self.assertTrue(client.loop_stopped)

    def test_publish_failure_is_typed(self):
        client = _FakeMQTTClient(publish_code=1)

        with self.assertRaises(DevicePublishError):
            _controller(client).execute(DeviceCommand.set_led(True))

    def test_invalid_messages_are_ignored_until_timeout(self):
        invalid_responses = (
            b"not-json",
            {
                "version": 1,
                "request_id": "wrong-request",
                "ok": True,
                "action": "set_led",
                "result": {"state": True},
            },
            {
                "version": 2,
                "request_id": "request-123",
                "ok": True,
                "action": "set_led",
                "result": {"state": True},
            },
        )
        for response in invalid_responses:
            with self.subTest(response=response):
                client = _FakeMQTTClient(lambda _command, value=response: value)
                with self.assertRaises(DeviceCommandTimeoutError):
                    _controller(client).execute(DeviceCommand.set_led(True))

    def test_ok_false_response_is_validated_failure(self):
        client = _FakeMQTTClient(
            lambda command: _response_for(
                command,
                ok=False,
                error="LED hardware unavailable",
            )
        )

        result = _controller(client).execute(DeviceCommand.set_led(True))

        self.assertFalse(result.success)
        self.assertEqual(result.error, "LED hardware unavailable")

    def test_invalid_temperature_types_and_ranges_are_rejected(self):
        invalid_values = (True, "28.4", float("nan"), -40.1, 80.1)
        command = DeviceCommand.read_temperature()
        for value in invalid_values:
            with self.subTest(value=value):
                payload = json.dumps({
                    "version": 1,
                    "request_id": "request-123",
                    "ok": True,
                    "action": "read_temperature",
                    "result": {"temperature_c": value},
                }).encode()
                self.assertIsNone(validate_response_payload(
                    payload,
                    expected_request_id="request-123",
                    command=command,
                ))

    def test_oversized_payload_is_rejected(self):
        self.assertIsNone(validate_response_payload(
            b"x" * (MAX_MQTT_PAYLOAD_BYTES + 1),
            expected_request_id="request-123",
            command=DeviceCommand.set_led(True),
        ))

    def test_topics_reject_unsafe_device_ids(self):
        for device_id in ("", "bad/device", "bad+device", "bad#device", "../bad"):
            with self.subTest(device_id=device_id):
                with self.assertRaises(DeviceControllerError):
                    MQTTTopics.for_device(device_id, "smart-companion")


class DeviceIntentAndCapabilityTests(SimpleTestCase):
    def test_led_phrases_map_only_to_requested_state(self):
        cases = (
            ("Turn the LED on", True),
            ("Switch the LED on", True),
            ("Turn on the LED", True),
            ("Turn the LED off", False),
            ("Switch the LED off", False),
        )
        for query, expected_state in cases:
            with self.subTest(query=query):
                command = map_device_command(query, Capability.DEVICE_CONTROL)
                self.assertEqual(command.action, DeviceAction.SET_LED)
                self.assertIs(command.params["state"], expected_state)

    def test_unsupported_device_never_maps_to_led(self):
        self.assertIsNone(map_device_command(
            "Turn on the fan",
            Capability.DEVICE_CONTROL,
        ))

    def test_temperature_intent_distinguishes_reading_from_education(self):
        actual_queries = (
            "What is the temperature?",
            "What's the room temperature?",
            "Read the temperature.",
        )
        for query in actual_queries:
            with self.subTest(query=query):
                mode, capability = AssistantResponsePolicy.classify(query)
                self.assertEqual(mode, ResponseMode.ACTION)
                self.assertEqual(capability, Capability.ENVIRONMENT_SENSING)

        for query in ("What is temperature?", "Explain temperature sensors."):
            with self.subTest(query=query):
                mode, capability = AssistantResponsePolicy.classify(query)
                self.assertEqual(mode, ResponseMode.NORMAL)
                self.assertIsNone(capability)

    @override_settings(
        DEVICE_CONTROL_ENABLED=True,
        DEVICE_TRANSPORT="mqtt",
        MQTT_HOST="127.0.0.1",
        MQTT_PORT=1883,
        MQTT_KEEPALIVE=30,
        MQTT_COMMAND_TIMEOUT_SECONDS=5,
        ESP32_DEVICE_ID="companion-esp32-01",
    )
    def test_capabilities_enabled_by_valid_configuration_without_network_probe(self):
        self.assertTrue(is_device_control_available())
        self.assertTrue(is_environment_sensing_available())
        registry = CapabilityRegistry.from_settings()
        self.assertTrue(registry.is_available(Capability.DEVICE_CONTROL))
        self.assertTrue(registry.is_available(Capability.ENVIRONMENT_SENSING))

    def test_disabled_and_invalid_configurations_are_unavailable(self):
        valid = {
            "DEVICE_CONTROL_ENABLED": True,
            "DEVICE_TRANSPORT": "mqtt",
            "MQTT_HOST": "127.0.0.1",
            "MQTT_PORT": 1883,
            "MQTT_KEEPALIVE": 30,
            "MQTT_COMMAND_TIMEOUT_SECONDS": 5,
            "MQTT_TOPIC_PREFIX": "smart-companion",
            "ESP32_DEVICE_ID": "companion-esp32-01",
        }
        invalid_cases = (
            {"DEVICE_CONTROL_ENABLED": False},
            {"DEVICE_TRANSPORT": "unsupported"},
            {"MQTT_HOST": "http://127.0.0.1"},
            {"MQTT_PORT": 0},
            {"MQTT_KEEPALIVE": 0},
            {"MQTT_COMMAND_TIMEOUT_SECONDS": 0},
            {"MQTT_COMMAND_TIMEOUT_SECONDS": float("inf")},
            {"MQTT_TOPIC_PREFIX": "unsafe/+"},
            {"ESP32_DEVICE_ID": "bad/device"},
        )
        for overrides in invalid_cases:
            with self.subTest(overrides=overrides):
                with self.settings(**(valid | overrides)):
                    self.assertFalse(is_device_control_available())
                    self.assertFalse(is_environment_sensing_available())

    def test_mqtt_host_validation_supports_local_and_lan_names(self):
        self.assertEqual(normalize_mqtt_host(" 127.0.0.1 "), "127.0.0.1")
        self.assertEqual(normalize_mqtt_host("PI.local"), "pi.local")


DEVICE_SETTINGS = {
    "AI_ENGINE": "local",
    "RAG_ENABLED": False,
    "ONLINE_RETRIEVAL_ENABLED": False,
    "DEVICE_CONTROL_ENABLED": True,
    "DEVICE_TRANSPORT": "mqtt",
    "MQTT_HOST": "127.0.0.1",
    "MQTT_PORT": 1883,
    "MQTT_KEEPALIVE": 30,
    "MQTT_COMMAND_TIMEOUT_SECONDS": 5,
    "MQTT_TOPIC_PREFIX": "smart-companion",
    "ESP32_DEVICE_ID": "companion-esp32-01",
}


@override_settings(**DEVICE_SETTINGS)
class DeviceServiceTests(TestCase):
    def controller_with_result(self, result):
        controller = MagicMock()
        controller.device_id = "companion-esp32-01"
        controller.execute.return_value = result
        return controller

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_device_controller")
    def test_led_on_success_is_deterministic_persisted_and_skips_llm(
        self,
        get_controller,
        get_engine,
    ):
        controller = self.controller_with_result(DeviceActionResult(
            request_id="request-123",
            action=DeviceAction.SET_LED,
            device_id="companion-esp32-01",
            success=True,
            latency_ms=17,
            result={"state": True},
        ))
        get_controller.return_value = controller

        conversation, text, metadata, error = AssistantService.process_message(
            "Turn the LED on"
        )

        self.assertIsNone(error)
        self.assertEqual(text, "The LED is on.")
        controller.execute.assert_called_once_with(DeviceCommand.set_led(True))
        get_engine.assert_not_called()
        self.assertEqual(metadata["action_name"], "set_led")
        self.assertTrue(metadata["action_used"])
        self.assertTrue(metadata["action_success"])
        self.assertEqual(metadata["action_latency_ms"], 17)
        self.assertEqual(metadata["device_id"], "companion-esp32-01")
        self.assertEqual(
            list(conversation.messages.order_by("id").values_list("sender", "text")),
            [("USER", "Turn the LED on"), ("AI", "The LED is on.")],
        )

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_device_controller")
    def test_led_off_success_calls_controller_once(self, get_controller, get_engine):
        controller = self.controller_with_result(DeviceActionResult(
            request_id="request-123",
            action=DeviceAction.SET_LED,
            device_id="companion-esp32-01",
            success=True,
            latency_ms=12,
            result={"state": False},
        ))
        get_controller.return_value = controller

        _conversation, text, metadata, error = AssistantService.process_message(
            "Switch the LED off"
        )

        self.assertIsNone(error)
        self.assertEqual(text, "The LED is off.")
        controller.execute.assert_called_once_with(DeviceCommand.set_led(False))
        self.assertTrue(metadata["action_success"])
        get_engine.assert_not_called()

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_device_controller")
    def test_temperature_success_returns_sensor_metadata(self, get_controller, get_engine):
        controller = self.controller_with_result(DeviceActionResult(
            request_id="request-123",
            action=DeviceAction.READ_TEMPERATURE,
            device_id="companion-esp32-01",
            success=True,
            latency_ms=23,
            result={"temperature_c": 28.4},
        ))
        get_controller.return_value = controller

        conversation, text, metadata, error = AssistantService.process_message(
            "What's the room temperature?"
        )

        self.assertIsNone(error)
        self.assertEqual(text, "The current temperature is 28.4 °C.")
        controller.execute.assert_called_once_with(DeviceCommand.read_temperature())
        get_engine.assert_not_called()
        self.assertTrue(metadata["sensor_used"])
        self.assertEqual(metadata["sensor_type"], "temperature")
        self.assertEqual(metadata["sensor_value"], 28.4)
        self.assertEqual(metadata["sensor_unit"], "°C")
        self.assertEqual(conversation.messages.count(), 2)

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_device_controller")
    def test_timeout_never_claims_led_success(self, get_controller, get_engine):
        controller = MagicMock()
        controller.device_id = "companion-esp32-01"
        controller.execute.side_effect = DeviceCommandTimeoutError("timeout")
        get_controller.return_value = controller

        _conversation, text, metadata, error = AssistantService.process_message(
            "Turn the LED on"
        )

        self.assertIsNone(error)
        self.assertEqual(text, "I sent the command, but the ESP32 didn't confirm it.")
        self.assertNotIn("LED is on", text)
        self.assertFalse(metadata["action_success"])
        get_engine.assert_not_called()

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_device_controller")
    def test_broker_failure_returns_grounded_failure(self, get_controller, get_engine):
        controller = MagicMock()
        controller.device_id = "companion-esp32-01"
        controller.execute.side_effect = DeviceUnavailableError("offline")
        get_controller.return_value = controller

        _conversation, text, metadata, error = AssistantService.process_message(
            "Turn the LED on"
        )

        self.assertIsNone(error)
        self.assertEqual(text, "I couldn't reach the ESP32 right now.")
        self.assertFalse(metadata["action_success"])
        get_engine.assert_not_called()

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_device_controller")
    def test_ok_false_returns_failure_not_confirmation(self, get_controller, get_engine):
        get_controller.return_value = self.controller_with_result(DeviceActionResult(
            request_id="request-123",
            action=DeviceAction.SET_LED,
            device_id="companion-esp32-01",
            success=False,
            latency_ms=15,
            error="LED unavailable",
        ))

        _conversation, text, metadata, error = AssistantService.process_message(
            "Turn the LED on"
        )

        self.assertIsNone(error)
        self.assertEqual(text, "The ESP32 couldn't complete the LED command.")
        self.assertFalse(metadata["action_success"])
        get_engine.assert_not_called()

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_device_controller")
    def test_unsupported_fan_never_controls_led(self, get_controller, get_engine):
        _conversation, text, metadata, error = AssistantService.process_message(
            "Turn on the fan"
        )

        self.assertIsNone(error)
        self.assertEqual(text, "I can only control the configured LED right now.")
        self.assertFalse(metadata["action_used"])
        self.assertIsNone(metadata["action_name"])
        get_controller.assert_not_called()
        get_engine.assert_not_called()

    @override_settings(DEVICE_CONTROL_ENABLED=False)
    @patch("assistant.services.get_device_controller")
    def test_disabled_capability_preserves_direct_unavailable_response(
        self,
        get_controller,
    ):
        _conversation, text, metadata, error = AssistantService.process_message(
            "Turn the LED on"
        )

        self.assertIsNone(error)
        self.assertIn("device control isn't connected", text)
        self.assertFalse(metadata["action_used"])
        get_controller.assert_not_called()

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_device_controller")
    def test_educational_temperature_query_remains_local(
        self,
        get_controller,
        get_engine,
    ):
        engine = MagicMock()
        engine.generate.return_value = AIEngineResult(
            text="Temperature measures thermal state.",
            engine="local",
            latency_ms=2,
        )
        get_engine.return_value = engine

        _conversation, _text, metadata, error = AssistantService.process_message(
            "What is temperature?"
        )

        self.assertIsNone(error)
        self.assertEqual(metadata["route"], "local")
        get_controller.assert_not_called()
        engine.generate.assert_called_once()

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_device_controller")
    def test_local_route_never_calls_device_controller(self, get_controller, get_engine):
        engine = MagicMock()
        engine.generate.return_value = AIEngineResult(
            text="Recursion is self-reference.",
            engine="local",
            latency_ms=2,
        )
        get_engine.return_value = engine

        _conversation, _text, metadata, error = AssistantService.process_message(
            "What is recursion?"
        )

        self.assertIsNone(error)
        self.assertEqual(metadata["route"], "local")
        get_controller.assert_not_called()

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_device_controller")
    @patch("assistant.services.QueryRouter.decide")
    def test_local_rag_route_never_calls_device_controller(
        self,
        decide,
        get_controller,
        get_engine,
    ):
        retrieved = RetrievedChunk(
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
            (retrieved,),
        )
        engine = MagicMock()
        engine.generate.return_value = AIEngineResult(
            text="Edge processing stays local.",
            engine="local",
            latency_ms=2,
        )
        get_engine.return_value = engine

        _conversation, _text, metadata, error = AssistantService.process_message(
            "Explain edge computing."
        )

        self.assertIsNone(error)
        self.assertEqual(metadata["route"], "local_rag")
        get_controller.assert_not_called()

    @patch("assistant.services.get_device_controller")
    def test_online_route_never_calls_device_controller(self, get_controller):
        _conversation, _text, metadata, error = AssistantService.process_message(
            "What's the latest AI news?"
        )

        self.assertIsNone(error)
        self.assertEqual(metadata["route"], "online")
        get_controller.assert_not_called()

    @patch("assistant.services.get_engine")
    @patch("assistant.services.get_device_controller")
    def test_public_api_exposes_action_metadata_without_topics(
        self,
        get_controller,
        get_engine,
    ):
        get_controller.return_value = self.controller_with_result(DeviceActionResult(
            request_id="request-123",
            action=DeviceAction.SET_LED,
            device_id="companion-esp32-01",
            success=True,
            latency_ms=17,
            result={"state": True},
        ))

        response = APIClient().post(
            "/api/assistant/chat/",
            {"query": "Turn the LED on"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["action_used"])
        self.assertEqual(response.data["action_name"], "set_led")
        self.assertEqual(response.data["device_id"], "companion-esp32-01")
        self.assertTrue(response.data["action_success"])
        self.assertEqual(response.data["action_latency_ms"], 17)
        self.assertNotIn("mqtt_topic", response.data)
        get_engine.assert_not_called()
