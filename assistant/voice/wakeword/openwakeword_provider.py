"""Persistent openWakeWord inference, with capture scoped to each wait."""

from collections import deque
from dataclasses import replace
from statistics import median
import logging
import time

from .audio import AlsaWakeCapture, KWS_FRAME_SAMPLES, LeftChannelConverter
from .base import WakeWordDetection, WakeWordError, WakeWordProvider, WakeWordCleanupError
from .models import load_engine, validate_installed_assets
from ..hands_free import MAX_PREROLL_BYTES, normalized_left_rms
from ..onnx_logging import configure_cpu_onnx_logging

logger = logging.getLogger(__name__)


class OpenWakeWordProvider(WakeWordProvider):
    def __init__(self, config):
        config.validate()
        self.config = config
        self.model = None
        self.capture = None

    def warm(self):
        if self.model is not None:
            return
        try:
            if self.config.inference_framework == "onnx" or self.config.vad_threshold > 0:
                configure_cpu_onnx_logging()
            engine = load_engine()
            validate_installed_assets(self.config, engine)
            self.model = engine.Model(
                wakeword_models=[self.config.selected_model],
                inference_framework=self.config.inference_framework,
                vad_threshold=self.config.vad_threshold, ncpu=1,
            )
        except WakeWordError:
            raise
        except Exception as exc:
            raise WakeWordError(f"Wake model initialization failed: {exc}") from exc

    def wait_for_wake_word(self, device, *, cancel_event=None, guard=None, confirmations=1, threshold=None,
                           on_detection=None, diagnostics=None):
        if self.capture is not None:
            raise WakeWordError("Wake microphone is already owned by a listening attempt.")
        self.warm()
        started = time.monotonic()
        deadline = started + self.config.timeout_seconds if self.config.timeout_seconds else None
        self.capture = AlsaWakeCapture(device)
        try:
            converter = LeftChannelConverter()
            pending = bytearray()
            native_tail = bytearray()
            background = deque(maxlen=64)  # At most ~5 seconds of numeric RMS.
            self.capture.start()
            consecutive = {}
            chunks = self.capture.chunks(deadline, cancel_event=cancel_event) if cancel_event is not None else self.capture.chunks(deadline)
            for chunk in chunks:
                if diagnostics is not None:
                    diagnostics.record('monitor_ready', True) # First PCM proves more than Popen success.
                    if 'monitor_start_ms' not in diagnostics.values:
                        diagnostics.elapsed('monitor_start_ms', started)
                native_tail.extend(chunk)
                excess = len(native_tail) - MAX_PREROLL_BYTES
                if excess > 0:
                    del native_tail[:((excess + 7) // 8) * 8]
                pending.extend(converter.convert(chunk))
                while len(pending) >= KWS_FRAME_SAMPLES * 2:
                    frame = bytes(pending[:KWS_FRAME_SAMPLES * 2])
                    del pending[:KWS_FRAME_SAMPLES * 2]
                    scores = self.model.predict(converter.np.frombuffer(frame, dtype="<i2"))
                    if diagnostics is not None:
                        diagnostics.increment('frames')
                    logger.debug("Wake scores: %s", scores)
                    for model, score in scores.items():
                        above = float(score) >= max(self.config.threshold, threshold or 0)
                        permitted = above and (guard is None or guard(frame))
                        if diagnostics is not None:
                            diagnostics.record('max_score', float(score), maximum=True)
                            if above and not permitted: diagnostics.increment('echo_rejections')
                        consecutive[model] = consecutive.get(model,0)+1 if permitted else 0
                        if diagnostics is not None:
                            diagnostics.record('confirmations', consecutive[model], maximum=True)
                        if consecutive[model] >= confirmations:
                            ordered = sorted(background)
                            floor = median(ordered[:max(1,len(ordered)//2)]) if len(ordered) >= 5 else None
                            length = len(native_tail) - len(native_tail) % 8
                            result = WakeWordDetection(True, model, float(score), time.monotonic() - started,
                                                       bytes(native_tail[:length]), floor)
                            if on_detection is not None:
                                # Keep recording opening words while owned audio workers stop.
                                # Refuse rather than silently drop the beginning on slow cleanup.
                                on_detection(result)
                                retained = bytearray(result.audio_preroll)
                                for tail in chunks:
                                    retained.extend(tail)
                                    if len(retained) > 48000 * 8 * 2:
                                        raise WakeWordError("Interruption handoff exceeded two seconds; repeat after a fresh wake.")
                                result = replace(result, audio_preroll=bytes(retained))
                            return result
                if chunk and len(chunk) % 8 == 0:
                    background.append(normalized_left_rms(chunk))
                    if diagnostics is not None:
                        diagnostics.record('max_mic_rms', background[-1], maximum=True)
            return WakeWordDetection(False, self.config.selected_model, elapsed_seconds=time.monotonic() - started)
        except (WakeWordError, KeyboardInterrupt):
            raise
        except Exception as exc:
            raise WakeWordError(f"Wake listening failed: {exc}") from exc
        finally:
            self._release_capture()

    def _release_capture(self):
        capture, self.capture = self.capture, None
        if capture is not None:
            try:
                capture.close()
            except Exception as exc:
                raise WakeWordCleanupError("Wake microphone cleanup failed; stop before reusing capture.") from exc

    def reset(self):
        if self.model is not None:
            self.model.reset()
            # openWakeWord 0.6.0 Model.reset() omits Silero's recurrent state.
            if self.config.vad_threshold > 0:
                self.model.vad.reset_states()
                self.model.vad.prediction_buffer.clear()

    def close(self):
        try:
            self._release_capture()
        finally:
            self.model = None
