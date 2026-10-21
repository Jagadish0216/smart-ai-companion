"""Bounded spectral corroboration for native ALSA LEFT, never exported audio.

This is a lightweight signal heuristic, not a trained speech classifier. Frozen
background shape rejects uniform fan gain changes; multiple novel speech bands
corroborate modest RMS rise. Existing VAD owns confirmation and capture bounds.
"""
from collections import deque


class SpectralOnset:
    SAMPLE_RATE = 48000
    FRAME_SAMPLES = 960
    BANDS = (200, 400, 800, 1400, 2200, 3200, 4800)
    EXCESS_POWER_RATIO = 2.0
    SHAPE_RATIO = 1.8
    REQUIRED_BANDS = 2

    def __init__(self):
        import numpy as np  # Existing wake-word dependency; no model/assets needed.
        self.np = np
        self.window = np.hanning(self.FRAME_SAMPLES)
        frequencies = np.fft.rfftfreq(self.FRAME_SAMPLES, 1/self.SAMPLE_RATE)
        self.masks = [(frequencies >= lo) & (frequencies < hi)
                      for lo, hi in zip(self.BANDS, self.BANDS[1:])]
        self.total_mask = (frequencies >= 50) & (frequencies < 8000)
        self.normalizer = self.FRAME_SAMPLES * (self.window**2).sum()
        self.samples = deque(maxlen=128)  # Only 7 aggregate powers per frame.
        self.recent = deque(maxlen=3)
        self.baseline = None
        self.frozen = False
        self.evidence_frames = 0

    def features(self, pcm):
        if len(pcm) != self.FRAME_SAMPLES * 8:
            return None  # Partial reads never fabricate spectral evidence.
        np = self.np
        left = np.frombuffer(pcm, dtype="<i4").reshape(-1, 2)[:, 0].astype(float) / 2147483648.0
        left -= left.mean()
        power = abs(np.fft.rfft(left * self.window))**2 / self.normalizer
        return np.array([power[mask].sum() for mask in self.masks] + [power[self.total_mask].sum()])

    def observe_calibration(self, features, rms=None):
        if features is not None and not self.frozen:
            self.samples.append((features, rms))

    def freeze(self, background_rms=None):
        self.frozen = True
        # Use the validated robust RMS floor to exclude loud calibration frames
        # from the spectrum too; otherwise the two calibration paths disagree.
        selected = [features for features, rms in self.samples
                    if background_rms is None or rms is None or
                    rms <= max(2 * background_rms, background_rms + .002)]
        if len(selected) >= 10:
            baseline = self.np.median(self.np.array(selected), axis=0)
            if self.np.isfinite(baseline).all() and baseline[-1] > 1e-12:
                self.baseline = baseline
        self.samples.clear()
        return self.baseline is not None

    def _changed_bands(self, features):
        baseline = self.np.maximum(self.baseline[:-1], self.baseline[-1] * .005)
        excess = features[:-1] >= baseline * self.EXCESS_POWER_RATIO
        changed_shape = features[:-1] / features[-1] >= baseline / self.baseline[-1] * self.SHAPE_RATIO
        return int((excess & changed_shape).sum()) >= self.REQUIRED_BANDS

    def evidence(self, features):
        if self.baseline is None or features is None or features[-1] <= 1e-12:
            self.recent.clear()
            return False
        self.recent.append(features)
        # Smooth spectral variance without retaining waveform samples. Require
        # the current frame too, so averaging cannot extend speech into silence.
        smoothed = self.np.mean(self.recent, axis=0)
        result = self._changed_bands(features) and self._changed_bands(smoothed)
        self.evidence_frames += int(result)
        return result

    def diagnostics(self):
        return dict(policy="relative_power_multiband_v1", available=self.baseline is not None,
                    evidence_frames=self.evidence_frames, band_edges_hz=list(self.BANDS),
                    excess_power_ratio=self.EXCESS_POWER_RATIO, shape_ratio=self.SHAPE_RATIO,
                    required_bands=self.REQUIRED_BANDS, smoothing_frames=3, calibration_capacity=128)
