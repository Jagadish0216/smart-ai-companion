"""Lightweight RMS meter. No audio ownership, threads or device dependencies."""

import math
import time


def observe(callback, value):
    """Audio observers are optional and must never fail the audio operation."""
    if callback is not None:
        try:
            callback(value)
        except Exception:
            pass


class AudioLevelMeter:
    def __init__(self, callback, *, gain=4.0, attack=0.04, release=0.18,
                 interval=0.08, clock=time.monotonic):
        for value in (gain, attack, release, interval):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Meter parameters must be finite and positive.")
        self.callback = callback
        self.gain, self.attack, self.release = gain, attack, release
        self.interval, self.clock = interval, clock
        self._value = 0.0
        self._updated = None
        self._sent_at = None
        self._sent = None

    def reset(self, *, send=True):
        self._value = 0.0
        self._updated = None
        self._sent_at = self.clock()
        self._sent = 0
        if send:
            observe(self.callback, 0)

    def feed(self, rms):
        now = self.clock()
        try:
            value = float(rms)
        except (ValueError, TypeError, OverflowError):
            value = 0.0
        if not math.isfinite(value):
            value = 0.0
        target = min(1.0, max(0.0, value) * self.gain) * 100
        elapsed = self.interval if self._updated is None else max(0.0, now - self._updated)
        self._updated = now
        tau = self.attack if target > self._value else self.release
        self._value += (target - self._value) * (1 - math.exp(-elapsed / tau))
        level = min(100, max(0, round(self._value)))
        if self._sent_at is not None and now - self._sent_at < self.interval:
            return
        # Firmware falls back to decorative motion after 1.2 s: even an
        # unchanged zero needs a bounded keepalive while observing live audio.
        if level == self._sent and now - self._sent_at < 0.8:
            return
        self._sent, self._sent_at = level, now
        observe(self.callback, level)
