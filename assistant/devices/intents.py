"""Narrow deterministic mapping from existing ACTION intents to device commands."""

import re

from assistant.policy import Capability

from .types import DeviceCommand


_LED_ON_PATTERNS = (
    r"\b(?:turn|switch)\s+on\s+(?:the\s+)?led\b",
    r"\b(?:turn|switch)\s+(?:the\s+)?led\s+on\b",
)
_LED_OFF_PATTERNS = (
    r"\b(?:turn|switch)\s+off\s+(?:the\s+)?led\b",
    r"\b(?:turn|switch)\s+(?:the\s+)?led\s+off\b",
)


def map_device_command(
    query: str,
    required_capability: Capability | None,
) -> DeviceCommand | None:
    normalized = " ".join((query or "").lower().split())
    if required_capability == Capability.DEVICE_CONTROL:
        if any(re.search(pattern, normalized) for pattern in _LED_ON_PATTERNS):
            return DeviceCommand.set_led(True)
        if any(re.search(pattern, normalized) for pattern in _LED_OFF_PATTERNS):
            return DeviceCommand.set_led(False)
        return None
    if required_capability == Capability.ENVIRONMENT_SENSING:
        return DeviceCommand.read_temperature()
    return None
