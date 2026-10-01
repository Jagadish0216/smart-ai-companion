"""Transport-independent controller contract and expected failure types."""

from abc import ABC, abstractmethod

from .types import DeviceActionResult, DeviceCommand


class DeviceController(ABC):
    @property
    @abstractmethod
    def device_id(self) -> str:
        ...

    @abstractmethod
    def execute(self, command: DeviceCommand) -> DeviceActionResult:
        """Execute one bounded command and return a validated device result."""
        ...


class DeviceControllerError(Exception):
    """Base class for expected device-control failures."""


class DeviceUnavailableError(DeviceControllerError):
    """The MQTT broker or device transport could not be reached."""


class DevicePublishError(DeviceControllerError):
    """The command could not be delivered to the MQTT broker."""


class DeviceCommandTimeoutError(DeviceControllerError):
    """No valid correlated device acknowledgment arrived in time."""
