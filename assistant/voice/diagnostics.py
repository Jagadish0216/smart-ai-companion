"""Opt-in numeric summaries only: bounded storage, no audio or text fields."""
import json
import math
from threading import Lock
import time


class VoiceDiagnostics:
    def __init__(self, enabled=False, *, clock=time.monotonic):
        self.enabled, self.clock = enabled, clock
        self.origin = clock() if enabled else 0
        self.values = {}
        self.lock = Lock()

    def record(self, key, value=1, *, maximum=False):
        if not self.enabled or key not in FIELDS or type(value) not in (int, float, bool):
            return
        if not math.isfinite(value):
            return
        with self.lock:
            if maximum:
                self.values[key] = max(self.values.get(key, 0), value)
            else:
                self.values[key] = value

    def increment(self, key):
        if self.enabled and key in FIELDS:
            with self.lock:
                self.values[key] = min(1000000, self.values.get(key, 0) + 1)

    def elapsed(self, key, start):
        if self.enabled:
            self.record(key, max(0, (self.clock() - start) * 1000))

    def report(self):
        if self.enabled:
            with self.lock:
                values = dict(self.values)
            try:
                print('Voice diagnostics: ' + json.dumps(values, sort_keys=True))
            except OSError:
                pass # Closed terminals must never break owned-device cleanup.


FIELDS = frozenset('monitor_started monitor_ready monitor_failed monitor_ended frames max_score confirmations echo_rejections reference_ready reference_silent reference_missing reference_text_blocked mic_silent max_correlation monitor_start_ms cancel_requested cancel_ack_ms output_reaped output_exit_code reference_age_ms max_mic_rms'.split())
