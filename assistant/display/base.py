from abc import ABC, abstractmethod
from enum import Enum


class DisplayState(str, Enum):
    BOOTING = "BOOTING"
    SETUP = "SETUP"
    CONNECTING = "CONNECTING"
    READY = "READY"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    OFFLINE = "OFFLINE"
    ECO = "ECO"
    PROTECTIVE = "PROTECTIVE"
    ERROR = "ERROR"


class CompanionDisplay(ABC):
    enabled = True

    @abstractmethod
    def set_state(self, state: DisplayState) -> bool:
        """Best-effort send; True means bytes sent, not firmware acknowledgment."""

    @abstractmethod
    def set_text(self, text: str) -> bool:
        """Set a short status label."""

    @abstractmethod
    def clear_text(self) -> bool:
        """Clear the status label."""

    @abstractmethod
    def close(self) -> None:
        """Release transport resources."""


class NoOpDisplay(CompanionDisplay):
    enabled = False

    def __init__(self, reason="disabled"):
        self.reason = reason

    def set_state(self, state):
        DisplayState(state)  # Reject unsupported programming inputs consistently.
        return False

    def set_text(self, text):
        return False

    def clear_text(self):
        return False

    def close(self):
        pass
