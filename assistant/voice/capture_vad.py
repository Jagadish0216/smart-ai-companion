"""Deterministic capture-only RMS gate; unrelated to Whisper/Silero STT VAD."""

from collections import deque
import math
from statistics import median


class AdaptiveCaptureVAD:
    """Feed normalized RMS and audio duration; return start/reset/stop events.

    Calibrate in quiet before speaking. Median rejects isolated spikes. After
    calibration the ambient floor and both thresholds remain frozen, including
    while waiting for speech. Internal constants deliberately remain bounded:
    start = max(configured, min(1, 2.5 * noise + .002)); continue uses 25% of
    the floor-to-start gap. Clip the continuation floor to 95% of start so
    hysteresis remains strict even with saturated/clipped ambient input.
    Continuation-level activity needs 60 ms consecutively to cancel end silence;
    definite above-start speech cancels it immediately.
    """

    def __init__(self, *, start_threshold=0.02, silence_seconds=0.8,
                 max_seconds=10.0, start_timeout_seconds=5.0, adaptive=True,
                 noise_calibration_seconds=0.5, start_confirm_ms=60,
                 min_speech_ms=300, chunk_seconds=0.02):
        values = (start_threshold, silence_seconds, max_seconds,
                  start_timeout_seconds, noise_calibration_seconds,
                  start_confirm_ms, min_speech_ms, chunk_seconds)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("Capture VAD settings must be finite and positive.")
        if start_threshold > 1:
            raise ValueError("Capture VAD start threshold must be at most 1.")
        self.configured_start = start_threshold
        self.silence_seconds = silence_seconds
        self.max_seconds = max_seconds
        self.start_timeout_seconds = start_timeout_seconds
        self.adaptive = adaptive
        self.calibration_seconds = noise_calibration_seconds
        self.confirm_seconds = start_confirm_ms / 1000
        self.min_speech_seconds = min_speech_ms / 1000
        self.noise_samples = deque(maxlen=max(1, math.ceil(noise_calibration_seconds / chunk_seconds)))
        self.noise_floor = 0.0
        self.calibrated_noise_floor = 0.0
        self.start_threshold = start_threshold
        self.continue_threshold = start_threshold
        self.elapsed = 0.0
        self.capture_duration = 0.0
        self.speech_duration = 0.0  # Voiced time, not elapsed time or pre-roll.
        self.candidate_duration = 0.0
        self.silence_duration = 0.0
        self.resume_duration = 0.0
        self.speech_started = False
        self.stop_reason = None
        if adaptive:
            self._thresholds()

    @property
    def calibrated(self):
        return not self.adaptive or self.elapsed + 1e-9 >= self.calibration_seconds

    @property
    def accepted(self):
        return self.speech_started and (
            not self.adaptive or self.speech_duration + 1e-9 >= self.min_speech_seconds
        )

    def _thresholds(self):
        self.noise_floor = median(self.noise_samples) if self.noise_samples else 0.0
        self.calibrated_noise_floor = self.noise_floor
        self.start_threshold = max(self.configured_start, min(1.0, self.noise_floor * 2.5 + 0.002))
        floor = min(self.noise_floor, self.start_threshold * 0.95)
        self.continue_threshold = floor + 0.25 * (self.start_threshold - floor)

    def feed(self, rms, seconds):
        if not math.isfinite(rms) or not 0 <= rms <= 1 or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Capture VAD input must be normalized RMS and positive duration.")
        if self.stop_reason is not None:
            return "stop"
        was_calibrated = self.calibrated
        self.elapsed += seconds
        if not self.speech_started:
            # Includes calibration and rejected bursts: no fresh waiting budget.
            if self.elapsed + 1e-9 >= self.start_timeout_seconds and (
                self.adaptive or rms < self.start_threshold
            ):
                self.stop_reason = "start_timeout"
                return "stop"
            if self.adaptive and not was_calibrated:
                self.noise_samples.append(rms)
                if self.calibrated:
                    self._thresholds()
                return None
            if rms >= self.start_threshold:
                self.candidate_duration += seconds
                if (
                    self.adaptive
                    and self.candidate_duration + 1e-9 >= self.max_seconds
                    and self.candidate_duration + 1e-9 < self.confirm_seconds
                ):
                    self.capture_duration = self.speech_duration = self.candidate_duration
                    self.stop_reason = "max_duration"
                    return "stop"
                if not self.adaptive or self.candidate_duration + 1e-9 >= self.confirm_seconds:
                    self.speech_started = True
                    self.capture_duration = self.candidate_duration
                    self.speech_duration = self.candidate_duration
                    if self.capture_duration + 1e-9 >= self.max_seconds:
                        self.stop_reason = "max_duration"
                    return "start"
            else:
                self.candidate_duration = 0.0
            return None

        self.capture_duration += seconds
        resume_pending = False
        if rms >= self.start_threshold:
            self.speech_duration += seconds
            self.silence_duration = 0.0
            self.resume_duration = 0.0
        elif self.adaptive and rms >= self.continue_threshold:
            previous_resume = self.resume_duration
            self.resume_duration += seconds
            if self.resume_duration + 1e-9 >= 0.06:
                # Count the confirmation run once, not isolated room-noise spikes.
                self.speech_duration += (
                    seconds if previous_resume + 1e-9 >= 0.06 else self.resume_duration
                )
                self.silence_duration = 0.0
            else:
                self.silence_duration += seconds
                resume_pending = True
        else:
            self.silence_duration += seconds
            self.resume_duration = 0.0
        # A minimum speech guard can never extend the maximum duration.
        if self.capture_duration + 1e-9 >= self.max_seconds:
            self.stop_reason = "max_duration"
            return "stop"
        # Allow an in-flight resume confirmation at the silence boundary, at
        # most 60 ms. Maximum duration above still wins without any extension.
        if self.silence_duration + 1e-9 >= self.silence_seconds and not resume_pending:
            if self.accepted:
                self.stop_reason = "silence"
                return "stop"
            # Reject a short burst and return to waiting within the original bound.
            self.speech_started = False
            self.capture_duration = self.speech_duration = 0.0
            self.candidate_duration = self.silence_duration = 0.0
            self.resume_duration = 0.0
            return "reset"
        return None
