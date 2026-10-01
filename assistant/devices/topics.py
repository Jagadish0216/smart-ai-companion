"""Central MQTT topic construction with strict device-ID validation."""

import re
from dataclasses import dataclass

from .base import DeviceControllerError


_DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def is_valid_device_id(device_id: str) -> bool:
    return bool(
        isinstance(device_id, str)
        and _DEVICE_ID_PATTERN.fullmatch(device_id.strip())
    )


def normalize_topic_prefix(prefix: str) -> str:
    if not isinstance(prefix, str):
        raise DeviceControllerError("The MQTT topic prefix is invalid.")
    normalized = prefix.strip().strip("/")
    segments = normalized.split("/")
    if (
        not normalized
        or len(normalized) > 128
        or any(not _DEVICE_ID_PATTERN.fullmatch(segment) for segment in segments)
    ):
        raise DeviceControllerError("The MQTT topic prefix is invalid.")
    return normalized


@dataclass(frozen=True)
class MQTTTopics:
    command: str
    response: str
    telemetry: str

    @classmethod
    def for_device(
        cls,
        device_id: str,
        topic_prefix: str,
    ) -> "MQTTTopics":
        normalized = device_id.strip() if isinstance(device_id, str) else ""
        if not is_valid_device_id(normalized):
            raise DeviceControllerError("The ESP32 device ID is invalid.")
        prefix = f"{normalize_topic_prefix(topic_prefix)}/{normalized}"
        return cls(
            command=f"{prefix}/command",
            response=f"{prefix}/response",
            telemetry=f"{prefix}/telemetry",
        )
