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


class DisplayMode(str, Enum):
    LOCAL = "LOCAL"
    KNOWLEDGE = "KNOWLEDGE"
    WEB = "WEB"


def validate_level(level):
    if type(level) is not int or not 0 <= level <= 100:
        raise ValueError("Display level must be an integer from 0 through 100.")
    return level


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

    def set_query(self, text: str) -> bool:
        """Set recognized input, if supported by this display provider."""
        return False

    def clear_query(self) -> bool:
        return False

    def set_mode(self, mode: DisplayMode) -> bool:
        return False

    def clear_mode(self) -> bool:
        return False

    def set_level(self, level: int) -> bool:
        """Optional amplitude command; older providers remain compatible."""
        validate_level(level)
        return False

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

    def set_query(self, text):
        return False

    def clear_query(self):
        return False

    def set_mode(self, mode):
        return False

    def clear_mode(self):
        return False

    def close(self):
        pass
