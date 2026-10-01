"""Typed device commands, results, and sensor readings."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class DeviceAction(str, Enum):
    SET_LED = "set_led"
    READ_TEMPERATURE = "read_temperature"


@dataclass(frozen=True)
class DeviceCommand:
    action: DeviceAction
    params: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def set_led(cls, state: bool) -> "DeviceCommand":
        return cls(DeviceAction.SET_LED, {"state": state})

    @classmethod
    def read_temperature(cls) -> "DeviceCommand":
        return cls(DeviceAction.READ_TEMPERATURE, {})


@dataclass(frozen=True)
class DeviceActionResult:
    request_id: str
    action: DeviceAction
    device_id: str
    success: bool
    latency_ms: int
    result: Mapping[str, object] = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class SensorReading:
    sensor_type: str
    value: float
    unit: str
