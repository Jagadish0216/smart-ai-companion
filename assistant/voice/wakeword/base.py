"""Engine-independent wake-word contract and validated runtime configuration."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import math
from pathlib import Path

from django.conf import settings


class WakeWordError(Exception):
    """Configuration, model or capture failure in the wake-word subsystem."""


@dataclass(frozen=True)
class WakeWordDetection:
    detected: bool
    model: str = ""
    score: float = 0.0
    elapsed_seconds: float = 0.0
    # Native request handoff only; never include microphone bytes in repr/logs.
    audio_preroll: bytes = field(default=b"", repr=False, compare=False)
    background_rms: float | None = None


@dataclass(frozen=True)
class WakeWordConfig:
    enabled: bool = False
    engine: str = "openwakeword"
    model: str = "hey_jarvis"
    model_path: str = ""
    threshold: float = 0.5
    vad_threshold: float = 0.5
    timeout_seconds: float = 0.0
    cooldown_seconds: float = 0.75

    @classmethod
    def from_settings(cls):
        values = {}
        for field in cls.__dataclass_fields__.values():
            name = "VOICE_WAKE_WORD_" + field.name.upper()
            value = getattr(settings, name, field.default)
            if field.name == "enabled":
                value = str(value).strip().lower() in {"true", "1", "yes", "on"}
            elif isinstance(field.default, float):
                try:
                    value = float(value)
                except (ValueError, TypeError) as exc:
                    raise WakeWordError(f"{name} must be a finite number.") from exc
            else:
                value = str(value).strip()
            values[field.name] = value
        config = cls(**values)
        config.validate()
        return config

    @property
    def selected_model(self):
        return str(Path(self.model_path).expanduser()) if self.model_path else self.model

    @property
    def inference_framework(self):
        return "tflite" if self.model_path and Path(self.model_path).suffix.lower() == ".tflite" else "onnx"

    def validate(self):
        if self.engine != "openwakeword":
            raise WakeWordError("VOICE_WAKE_WORD_ENGINE must be openwakeword.")
        if not self.model_path and self.model not in {
            "alexa", "hey_mycroft", "hey_jarvis", "hey_rhasspy", "timer", "weather",
        }:
            raise WakeWordError("VOICE_WAKE_WORD_MODEL must select a built-in model or set VOICE_WAKE_WORD_MODEL_PATH.")
        if self.model_path and Path(self.model_path).suffix.lower() not in {".onnx", ".tflite"}:
            raise WakeWordError("VOICE_WAKE_WORD_MODEL_PATH must be an ONNX or TFLite model file.")
        for field in ("threshold", "vad_threshold", "timeout_seconds", "cooldown_seconds"):
            value = getattr(self, field)
            if not math.isfinite(value):
                raise WakeWordError(f"VOICE_WAKE_WORD_{field.upper()} must be finite.")
        if not 0 < self.threshold <= 1:
            raise WakeWordError("VOICE_WAKE_WORD_THRESHOLD must be greater than zero and at most 1.")
        if not 0 <= self.vad_threshold <= 1:
            raise WakeWordError("VOICE_WAKE_WORD_VAD_THRESHOLD must be between 0 and 1 (0 disables wake VAD).")
        if self.timeout_seconds < 0:
            raise WakeWordError("VOICE_WAKE_WORD_TIMEOUT_SECONDS must be nonnegative (0 waits indefinitely).")
        if not 0 <= self.cooldown_seconds <= 5:
            raise WakeWordError("VOICE_WAKE_WORD_COOLDOWN_SECONDS must be between 0 and 5.")


class WakeWordProvider(ABC):
    @abstractmethod
    def warm(self):
        """Load persistent inference resources without opening the microphone."""

    @abstractmethod
    def wait_for_wake_word(self, device: str) -> WakeWordDetection:
        """Own capture for this wait only; release it before returning or raising."""

    @abstractmethod
    def reset(self):
        """Discard detection history between listening attempts."""

    @abstractmethod
    def close(self):
        """Release all owned resources; safe to call repeatedly."""


class DisabledWakeWordProvider(WakeWordProvider):
    def warm(self):
        pass

    def wait_for_wake_word(self, device):
        return WakeWordDetection(False)

    def reset(self):
        pass

    def close(self):
        pass
