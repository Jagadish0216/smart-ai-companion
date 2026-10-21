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
    the floor-to-start gap. An optional adaptive start ceiling caps only that
    calculated start; continuation is then derived from the same frozen floor.
    Clip the continuation floor to 95% of start so
    hysteresis remains strict even with saturated/clipped ambient input.
    Continuation-level activity needs 60 ms consecutively to cancel end silence;
    definite above-start speech cancels it immediately.
    """

    def __init__(self, *, start_threshold=0.02, silence_seconds=0.8,
                 max_seconds=10.0, start_timeout_seconds=5.0, adaptive=True,
                 noise_calibration_seconds=0.5, start_confirm_ms=60,
                 min_speech_ms=300, chunk_seconds=0.02, max_start_threshold=None):
        values = (start_threshold, silence_seconds, max_seconds,
                  start_timeout_seconds, noise_calibration_seconds,
                  start_confirm_ms, min_speech_ms, chunk_seconds)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("Capture VAD settings must be finite and positive.")
        if start_threshold > 1:
            raise ValueError("Capture VAD start threshold must be at most 1.")
        if max_start_threshold is not None and (
            not math.isfinite(max_start_threshold) or not 0 < max_start_threshold <= 1
        ):
            raise ValueError("Capture VAD max start threshold must be finite, greater than 0 and at most 1.")
        self.configured_start = start_threshold
        self.max_start_threshold = max_start_threshold
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
        self.max_wait_rms = 0.0
        self.stop_reason = None
        self.start_window_origin = 0.0
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
        if self.max_start_threshold is not None:
            self.start_threshold = min(self.start_threshold, self.max_start_threshold)
        floor = min(self.noise_floor, self.start_threshold * 0.95)
        self.continue_threshold = floor + 0.25 * (self.start_threshold - floor)

    def open_start_window(self):
        """Opt-in lifecycle: preserve calibration, start a fresh bounded wait after cue."""
        if not self.calibrated or self.speech_started or self.stop_reason:
            raise ValueError("Cannot open speech window before valid calibration.")
        self.start_window_origin = self.elapsed

    def feed(self, rms, seconds):
        if not math.isfinite(rms) or not 0 <= rms <= 1 or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Capture VAD input must be normalized RMS and positive duration.")
        if self.stop_reason is not None:
            return "stop"
        was_calibrated = self.calibrated
        self.elapsed += seconds
        if not self.speech_started:
            if was_calibrated:
                self.max_wait_rms = max(self.max_wait_rms, rms)
            # Includes calibration and rejected bursts: no fresh waiting budget.
            if self.elapsed - self.start_window_origin + 1e-9 >= self.start_timeout_seconds and (
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


class QuietCapturePreparation:
    """Optional bounded preparation, shared by live capture and file replay.

    Drain real warm-up frames, never insert silence. Sustained activity during
    warm-up or a calibration median at/above the configured onset floor refuses
    readiness; this is a conservative non-quiet gate, not a speech classifier.
    No recalibration/retry, threshold lowering or suppression of a valid request.
    """
    ABSOLUTE_ACTIVITY_REFUSAL = True

    def __init__(self, vad, warmup_seconds):
        if not vad.adaptive or not math.isfinite(warmup_seconds) or warmup_seconds < 0:
            raise ValueError("Calibrated readiness requires adaptive VAD and bounded warm-up.")
        self.vad = vad
        self.warmup_seconds = warmup_seconds
        self.warmup_elapsed = 0.0
        self.activity_seconds = 0.0
        # Scalars plus at most 4096 frame RMS values; no PCM or per-frame export.
        self.frame_count = self.above_count = 0
        self.above_seconds = self.longest_activity_seconds = 0.0
        self.rms_min = self.rms_max = None
        self._rms = []
        self.first_pcm_monotonic = self.refusal_monotonic = None
        self.calibration_started = False
        self.interruption = None

    def diagnostics(self):
        values = sorted(self._rms)
        complete = self.frame_count == len(values)
        return dict(
            warmup_frames=self.frame_count,
            warmup_rms_min=self.rms_min, warmup_rms_max=self.rms_max,
            warmup_rms_median=median(values) if values and complete else None,
            warmup_rms_p90=values[math.ceil(.9 * len(values)) - 1] if values and complete else None,
            rms_statistics_complete=complete, rms_statistics_capacity=4096,
            above_threshold_frames=self.above_count,
            above_threshold_audio_seconds=self.above_seconds,
            longest_above_threshold_audio_seconds=self.longest_activity_seconds,
            activity_threshold=self.vad.configured_start,
            first_pcm_to_activity_refusal_ms=(
                (self.refusal_monotonic - self.first_pcm_monotonic) * 1000
                if self.refusal_monotonic is not None and self.first_pcm_monotonic is not None else None),
            startup_frames_before_activity_decision=(self.frame_count - 1
                if self.vad.stop_reason == "preparation_activity" else None),
            calibration_started=self.calibration_started,
            refusal_reason=(self.vad.stop_reason if self.vad.stop_reason in
                ("preparation_activity", "calibration_unreliable") else self.interruption),
        )

    def warming_up(self):
        return self.warmup_elapsed + 1e-9 < self.warmup_seconds

    def consume_warmup(self, rms, seconds):
        self.frame_count += 1
        self.rms_min = rms if self.rms_min is None else min(self.rms_min, rms)
        self.rms_max = rms if self.rms_max is None else max(self.rms_max, rms)
        if len(self._rms) < 4096:
            self._rms.append(rms)
        if rms >= self.vad.configured_start:
            self.above_count += 1
            self.above_seconds += seconds
        self.warmup_elapsed += seconds
        self.activity_seconds = self.activity_seconds + seconds if rms >= self.vad.configured_start else 0.0
        self.longest_activity_seconds = max(self.longest_activity_seconds, self.activity_seconds)
        if self.ABSOLUTE_ACTIVITY_REFUSAL and self.activity_seconds + 1e-9 >= self.vad.min_speech_seconds:
            self.vad.stop_reason = "preparation_activity"

    def calibration_usable(self):
        if not self.vad.calibrated or self.vad.stop_reason:
            return False
        if self.vad.noise_floor >= self.vad.configured_start:
            self.vad.stop_reason = "calibration_unreliable"
            return False
        return True


class SettledFanPreparation(QuietCapturePreparation):
    """Opt-in startup drain followed by stable fan-background calibration.

    Energy alone cannot identify steady speech. Require operator silence before
    ready; reject observable modulation/drift instead of pretending to classify it.
    Reuse the existing adaptive thresholds, onset confirmation and hysteresis.
    """
    ABSOLUTE_ACTIVITY_REFUSAL = False
    MIN_DRAIN_SECONDS = 1.5
    WINDOW_SECONDS = 1.0
    MAX_PREPARATION_SECONDS = 6.0
    MAX_SPREAD_RATIO = 1.8
    MAX_DRIFT_RATIO = 1.35

    def __init__(self, vad, warmup_seconds):
        super().__init__(vad, warmup_seconds)
        if warmup_seconds > self.MAX_PREPARATION_SECONDS - self.WINDOW_SECONDS:
            raise ValueError("Settled fan warm-up exceeds bounded preparation budget.")
        self._window = deque(maxlen=50)
        self._settled = False
        self.settled_floor = None

    def warming_up(self):
        return not self._settled and self.vad.stop_reason is None

    def consume_warmup(self, rms, seconds):
        # Retain the shared numeric diagnostics but replace only this opt-in
        # policy's absolute sustained-activity refusal. No PCM is retained here.
        super().consume_warmup(rms, seconds)
        if self.warmup_elapsed - seconds + 1e-9 >= max(self.MIN_DRAIN_SECONDS, self.warmup_seconds):
            self._window.append((rms, seconds))
        duration = sum(dt for _, dt in self._window)
        if duration + 1e-9 >= self.WINDOW_SECONDS:
            levels = [level for level, _ in self._window]
            if self.stable(levels):
                self.settled_floor = median(levels)
                self._settled = True
        if not self._settled and self.warmup_elapsed + 1e-9 >= self.MAX_PREPARATION_SECONDS:
            self.vad.stop_reason = "preparation_unstable"

    def stable(self, levels):
        if len(levels) < 2:
            return False
        ordered = sorted(levels)
        floor = median(levels)
        # Existing configured onset floor is an auditable calibration ceiling,
        # not a requirement for silence or an altered speech-onset threshold.
        if floor >= self.vad.configured_start:
            return False
        low = max(ordered[int(.1 * (len(ordered)-1))], .0001)
        high = ordered[math.ceil(.9 * len(ordered))-1]
        half = len(levels)//2
        left, right = median(levels[:half]), median(levels[half:])
        return (high <= low * self.MAX_SPREAD_RATIO and
                max(left, right) <= max(min(left, right), .0001) * self.MAX_DRIFT_RATIO)

    def calibration_usable(self):
        if not self.vad.calibrated or self.vad.stop_reason:
            return False
        levels = list(self.vad.noise_samples)
        floor = self.vad.noise_floor
        if (not self.stable(levels) or
            max(floor, self.settled_floor) > max(min(floor, self.settled_floor), .0001) * self.MAX_DRIFT_RATIO):
            self.vad.stop_reason = "calibration_unreliable"
            return False
        return True

    def diagnostics(self):
        result = super().diagnostics()
        result.update(policy="settled_fan_v1", settled_background_rms=self.settled_floor,
                      startup_drain_seconds=max(self.MIN_DRAIN_SECONDS, self.warmup_seconds),
                      stable_window_seconds=self.WINDOW_SECONDS,
                      preparation_limit_seconds=self.MAX_PREPARATION_SECONDS,
                      max_spread_ratio=self.MAX_SPREAD_RATIO, max_drift_ratio=self.MAX_DRIFT_RATIO)
        if self.vad.stop_reason == "preparation_unstable":
            result["refusal_reason"] = self.vad.stop_reason
        return result
