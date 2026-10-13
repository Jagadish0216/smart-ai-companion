"""Create one optional display controller for a caller-owned lifetime."""

import logging
import math

from django.conf import settings

from .base import NoOpDisplay

logger = logging.getLogger(__name__)


def get_companion_display():
    if not getattr(settings, "COMPANION_DISPLAY_ENABLED", False):
        return NoOpDisplay()
    try:
        port = str(getattr(settings, "COMPANION_DISPLAY_PORT", "")).strip()
        baud = int(getattr(settings, "COMPANION_DISPLAY_BAUD", 115200))
        delay = float(getattr(settings, "COMPANION_DISPLAY_STARTUP_DELAY_SECONDS", 1.5))
        if not port or any(ord(char) < 32 for char in port) or baud <= 0 or not math.isfinite(delay) or not 0 <= delay <= 10:
            raise ValueError("invalid display settings")
        from .serial_display import SerialCompanionDisplay
        return SerialCompanionDisplay(port=port, baudrate=baud, startup_delay_seconds=delay)
    except Exception:
        logger.warning("Companion display configuration unavailable; continuing without display")
        return NoOpDisplay(reason="misconfigured or unavailable")
