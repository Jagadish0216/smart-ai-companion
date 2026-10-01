"""Settings-driven device-controller factory and capability checks."""

from __future__ import annotations

import ipaddress
import math
import re

from django.conf import settings

from .base import DeviceController, DeviceControllerError
from .mqtt import MQTTDeviceController
from .topics import is_valid_device_id, normalize_topic_prefix


_HOST_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.I)


def get_device_controller() -> DeviceController:
    if not is_device_configuration_available():
        raise DeviceControllerError("Device control is disabled or misconfigured.")
    return MQTTDeviceController(
        host=normalize_mqtt_host(settings.MQTT_HOST),
        port=int(settings.MQTT_PORT),
        keepalive=int(settings.MQTT_KEEPALIVE),
        timeout_seconds=float(settings.MQTT_COMMAND_TIMEOUT_SECONDS),
        device_id=str(settings.ESP32_DEVICE_ID).strip(),
        topic_prefix=str(settings.MQTT_TOPIC_PREFIX).strip(),
    )


def is_device_control_available() -> bool:
    return is_device_configuration_available()


def is_environment_sensing_available() -> bool:
    return is_device_configuration_available()


def is_device_configuration_available() -> bool:
    if not getattr(settings, "DEVICE_CONTROL_ENABLED", False):
        return False
    if str(getattr(settings, "DEVICE_TRANSPORT", "mqtt")).strip().lower() != "mqtt":
        return False
    try:
        normalize_mqtt_host(getattr(settings, "MQTT_HOST", "127.0.0.1"))
        port = int(getattr(settings, "MQTT_PORT", 1883))
        keepalive = int(getattr(settings, "MQTT_KEEPALIVE", 30))
        timeout = float(getattr(settings, "MQTT_COMMAND_TIMEOUT_SECONDS", 5))
        normalize_topic_prefix(getattr(settings, "MQTT_TOPIC_PREFIX", "smart-companion"))
    except (DeviceControllerError, TypeError, ValueError):
        return False
    device_id = getattr(settings, "ESP32_DEVICE_ID", "")
    return (
        1 <= port <= 65535
        and keepalive > 0
        and math.isfinite(timeout)
        and timeout > 0
        and is_valid_device_id(device_id)
    )


def normalize_mqtt_host(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("MQTT host must be a hostname or IP address.")
    host = value.strip()
    if (
        not host
        or len(host) > 253
        or "://" in host
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in host
        )
    ):
        raise ValueError("MQTT host must be a hostname or IP address.")
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        try:
            ascii_host = host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("MQTT host is invalid.") from exc
        labels = ascii_host.split(".")
        if any(not _HOST_LABEL_PATTERN.fullmatch(label) for label in labels):
            raise ValueError("MQTT host is invalid.")
        return ascii_host
