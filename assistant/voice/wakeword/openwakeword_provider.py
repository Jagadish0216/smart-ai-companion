"""Persistent openWakeWord inference, with capture scoped to each wait."""

import logging
import time

from .audio import AlsaWakeCapture, KWS_FRAME_SAMPLES, LeftChannelConverter
from .base import WakeWordDetection, WakeWordError, WakeWordProvider
from .models import load_engine, validate_installed_assets

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

    def wait_for_wake_word(self, device):
        if self.capture is not None:
            raise WakeWordError("Wake microphone is already owned by a listening attempt.")
        self.warm()
        started = time.monotonic()
        deadline = started + self.config.timeout_seconds if self.config.timeout_seconds else None
        self.capture = AlsaWakeCapture(device)
        try:
            converter = LeftChannelConverter()
            pending = bytearray()
            self.capture.start()
            for chunk in self.capture.chunks(deadline):
                pending.extend(converter.convert(chunk))
                while len(pending) >= KWS_FRAME_SAMPLES * 2:
                    frame = bytes(pending[:KWS_FRAME_SAMPLES * 2])
                    del pending[:KWS_FRAME_SAMPLES * 2]
                    scores = self.model.predict(converter.np.frombuffer(frame, dtype="<i2"))
                    logger.debug("Wake scores: %s", scores)
                    for model, score in scores.items():
                        if float(score) >= self.config.threshold:
                            return WakeWordDetection(True, model, float(score), time.monotonic() - started)
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
            capture.close()

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
