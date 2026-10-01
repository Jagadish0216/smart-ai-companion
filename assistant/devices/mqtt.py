"""Synchronous request/response device controller over MQTT."""

from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from .base import (
    DeviceCommandTimeoutError,
    DeviceController,
    DeviceControllerError,
    DevicePublishError,
    DeviceUnavailableError,
)
from .topics import MQTTTopics
from .types import DeviceAction, DeviceActionResult, DeviceCommand


PROTOCOL_VERSION = 1
MAX_MQTT_PAYLOAD_BYTES = 4096
MIN_TEMPERATURE_C = -40.0
MAX_TEMPERATURE_C = 80.0
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class _ValidatedResponse:
    success: bool
    result: dict
    error: str = ""


class MQTTDeviceController(DeviceController):
    def __init__(
        self,
        *,
        host: str,
        port: int,
        keepalive: int,
        timeout_seconds: float,
        device_id: str,
        topic_prefix: str,
        client_factory: Callable[[str], object] | None = None,
        request_id_factory: Callable[[], str] | None = None,
    ):
        self.host = host
        self.port = int(port)
        self.keepalive = int(keepalive)
        self.timeout_seconds = float(timeout_seconds)
        self._device_id = device_id
        self.topics = MQTTTopics.for_device(device_id, topic_prefix)
        self._client_factory = client_factory or _create_paho_client
        self._request_id_factory = request_id_factory or (lambda: uuid.uuid4().hex)

    @property
    def device_id(self) -> str:
        return self._device_id

    def execute(self, command: DeviceCommand) -> DeviceActionResult:
        if command.action not in (DeviceAction.SET_LED, DeviceAction.READ_TEMPERATURE):
            raise DeviceControllerError("Unsupported device action.")
        if (
            command.action == DeviceAction.SET_LED
            and type(command.params.get("state")) is not bool
        ):
            raise DeviceControllerError("LED state must be boolean.")
        if command.action == DeviceAction.READ_TEMPERATURE and command.params:
            raise DeviceControllerError("Temperature requests do not accept parameters.")
        request_id = self._request_id_factory()
        if (
            not isinstance(request_id, str)
            or not _REQUEST_ID_PATTERN.fullmatch(request_id)
        ):
            raise DeviceControllerError("Generated request ID is invalid.")

        started = time.perf_counter()
        deadline = started + self.timeout_seconds
        connected = threading.Event()
        subscribed = threading.Event()
        response_received = threading.Event()
        state: dict[str, object] = {
            "connection_failed": False,
            "subscription_failed": False,
            "response": None,
        }

        client = self._client_factory(f"smart-ai-{request_id[:12]}")

        def on_connect(_client, _userdata, _flags, reason_code, _properties):
            state["connection_failed"] = _reason_code_failed(reason_code)
            connected.set()

        def on_connect_fail(_client, _userdata):
            state["connection_failed"] = True
            connected.set()

        def on_subscribe(_client, _userdata, _mid, reason_codes, _properties):
            state["subscription_failed"] = any(
                _reason_code_failed(code) for code in (reason_codes or ())
            )
            subscribed.set()

        def on_message(_client, _userdata, message):
            if getattr(message, "topic", "") != self.topics.response:
                return
            parsed = validate_response_payload(
                getattr(message, "payload", b""),
                expected_request_id=request_id,
                command=command,
            )
            if parsed is not None:
                state["response"] = parsed
                response_received.set()

        client.on_connect = on_connect
        client.on_connect_fail = on_connect_fail
        client.on_subscribe = on_subscribe
        client.on_message = on_message

        loop_started = False
        try:
            connect_result = client.connect_async(
                self.host,
                self.port,
                self.keepalive,
            )
            if (
                connect_result is not None
                and not _return_code_succeeded(connect_result)
            ):
                raise DeviceUnavailableError("MQTT connection could not be started.")
            client.loop_start()
            loop_started = True

            if not connected.wait(_remaining(deadline)):
                raise DeviceUnavailableError("MQTT broker connection timed out.")
            if state["connection_failed"]:
                raise DeviceUnavailableError("MQTT broker rejected the connection.")

            subscribe_result, _mid = client.subscribe(self.topics.response, qos=1)
            if not _return_code_succeeded(subscribe_result):
                raise DeviceUnavailableError("MQTT response subscription failed.")
            if not subscribed.wait(_remaining(deadline)):
                raise DeviceUnavailableError("MQTT response subscription timed out.")
            if state["subscription_failed"]:
                raise DeviceUnavailableError("MQTT broker rejected the subscription.")

            payload = _command_payload(request_id, command)
            publish_info = client.publish(
                self.topics.command,
                payload=payload,
                qos=1,
                retain=False,
            )
            if not _return_code_succeeded(getattr(publish_info, "rc", 1)):
                raise DevicePublishError("MQTT command publish failed.")
            try:
                publish_info.wait_for_publish(timeout=_remaining(deadline))
            except Exception as exc:
                raise DevicePublishError("MQTT command publish failed.") from exc
            if not publish_info.is_published():
                raise DevicePublishError("MQTT command was not confirmed as published.")

            if not response_received.wait(_remaining(deadline)):
                raise DeviceCommandTimeoutError(
                    "No valid correlated ESP32 acknowledgment was received."
                )
            response = state["response"]
            if not isinstance(response, _ValidatedResponse):
                raise DeviceCommandTimeoutError(
                    "No valid correlated ESP32 acknowledgment was received."
                )
            return DeviceActionResult(
                request_id=request_id,
                action=command.action,
                device_id=self.device_id,
                success=response.success,
                latency_ms=round((time.perf_counter() - started) * 1000),
                result=response.result,
                error=response.error,
            )
        except DeviceControllerError:
            raise
        except OSError as exc:
            raise DeviceUnavailableError("MQTT broker connection failed.") from exc
        except Exception as exc:
            raise DeviceUnavailableError("MQTT transport failed.") from exc
        finally:
            try:
                client.disconnect()
            except Exception:
                pass
            if loop_started:
                try:
                    client.loop_stop()
                except Exception:
                    pass


def validate_response_payload(
    payload: bytes,
    *,
    expected_request_id: str,
    command: DeviceCommand,
) -> _ValidatedResponse | None:
    if not isinstance(payload, (bytes, bytearray)) or len(payload) > MAX_MQTT_PAYLOAD_BYTES:
        return None
    try:
        message = json.loads(bytes(payload).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(message, dict):
        return None
    if type(message.get("version")) is not int or message["version"] != PROTOCOL_VERSION:
        return None
    if message.get("request_id") != expected_request_id:
        return None
    if message.get("action") != command.action.value:
        return None
    if type(message.get("ok")) is not bool:
        return None
    if not message["ok"]:
        error_message = message.get("error")
        return _ValidatedResponse(
            success=False,
            result={},
            error=(
                error_message[:200]
                if isinstance(error_message, str)
                else "The ESP32 rejected the command."
            ),
        )

    result = message.get("result")
    if not isinstance(result, dict):
        return None
    if command.action == DeviceAction.SET_LED:
        state = result.get("state")
        expected_state = command.params.get("state")
        if type(state) is not bool or state is not expected_state:
            return None
        return _ValidatedResponse(success=True, result={"state": state})

    temperature = result.get("temperature_c")
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not math.isfinite(float(temperature))
        or not MIN_TEMPERATURE_C <= float(temperature) <= MAX_TEMPERATURE_C
    ):
        return None
    return _ValidatedResponse(
        success=True,
        result={"temperature_c": float(temperature)},
    )


def _command_payload(request_id: str, command: DeviceCommand) -> str:
    payload = json.dumps({
        "version": PROTOCOL_VERSION,
        "request_id": request_id,
        "action": command.action.value,
        "params": dict(command.params),
    }, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_MQTT_PAYLOAD_BYTES:
        raise DeviceControllerError("MQTT command payload is too large.")
    return payload


def _create_paho_client(client_id: str):
    try:
        from paho.mqtt import client as mqtt
    except ImportError as exc:
        raise DeviceUnavailableError(
            "The configured MQTT client dependency is unavailable."
        ) from exc
    return mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv311,
        reconnect_on_failure=False,
    )


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.perf_counter())


def _reason_code_failed(reason_code) -> bool:
    if getattr(reason_code, "is_failure", False):
        return True
    try:
        return int(reason_code) != 0
    except (TypeError, ValueError):
        return True


def _return_code_succeeded(return_code) -> bool:
    try:
        return int(return_code) == 0
    except (TypeError, ValueError):
        return False
