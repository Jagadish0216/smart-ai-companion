"""Optional companion-face transport, independent of assistant/device routing."""

from .base import CompanionDisplay, DisplayState, NoOpDisplay
from .factory import get_companion_display

__all__ = ["CompanionDisplay", "DisplayState", "NoOpDisplay", "get_companion_display"]
