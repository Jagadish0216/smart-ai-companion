"""Optional companion-face transport, independent of assistant/device routing."""

from .base import CompanionDisplay, DisplayMode, DisplayState, NoOpDisplay
from .factory import get_companion_display

__all__ = ["CompanionDisplay", "DisplayMode", "DisplayState", "NoOpDisplay", "get_companion_display"]
