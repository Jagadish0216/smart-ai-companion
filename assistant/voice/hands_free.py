"""Post-wake request recording: retain audio unconditionally, endpoint optionally."""

from collections import deque
from dataclasses import dataclass
import math
import os
from pathlib import Path
import selectors
from statistics import median
import struct
import subprocess
import time
import wave

from assistant.display.audio_level import observe

SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 4
FRAME_BYTES = CHANNELS * SAMPLE_WIDTH
CHUNK_FRAMES = 960  # 20 ms of native stereo S32_LE.


class HandsFreeCaptureError(Exception):
    """A hardware, pipe or recording-file failure, rather than absence of speech."""


@dataclass(frozen=True)
class HandsFreeCaptureConfig:
    warmup_seconds: float = 0.25
    calibration_seconds: float = 0.25
    max_seconds: float = 6.0
    end_silence_seconds: float = 0.9

    @classmethod
    def from_settings(cls):
        from django.conf import settings
        values = {}
        for field in cls.__dataclass_fields__.values():
            name = "VOICE_HANDS_FREE_" + field.name.upper()
            try:
                values[field.name] = float(getattr(settings, name, field.default))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be a finite number.") from exc
        config = cls(**values)
        config.validate()
        return config

    def validate(self):
        for name, low, high in (
            ("warmup_seconds", 0.02, 2.0), ("calibration_seconds", 0.02, 2.0),
            ("max_seconds", 0.1, 30.0), ("end_silence_seconds", 0.1, 5.0),
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"VOICE_HANDS_FREE_{name.upper()} must be finite and between {low:g} and {high:g}.")


def normalized_left_rms(chunk):
    """Measure native LEFT audio; request WAVs retain both native channels."""
    if not chunk or len(chunk) % FRAME_BYTES:
        raise HandsFreeCaptureError("Request PCM contains an incomplete stereo frame.")
    squares = sum(left * left for left, _right in struct.iter_unpack("<ii", chunk))
    return math.sqrt(squares / (len(chunk) // FRAME_BYTES)) / 2147483648.0


class HandsFreeEndpointDetector:
    """Conservative endpoint hints, never an audio admission/start gate.

    Two above-threshold chunks in a rolling 100 ms window, or one strong
    chunk, establish speech evidence. Only sustained below-continuation quiet
    after that evidence can stop capture early. No reset or start timeout.
    """

    def __init__(self, noise_floor, end_silence_seconds):
        if not math.isfinite(noise_floor) or not 0 <= noise_floor <= 1:
            raise ValueError("Endpoint noise floor must be normalized RMS.")
        if not math.isfinite(end_silence_seconds) or end_silence_seconds <= 0:
            raise ValueError("Endpoint silence must be finite and positive.")
        self.noise_floor = noise_floor
        self.speech_threshold = min(0.08, max(0.035, noise_floor * 1.15 + 0.003))
        self.continue_threshold = min(self.speech_threshold * 0.95, max(0.015, noise_floor * 1.05 + 0.001))
        self.strong_threshold = min(1.0, max(self.speech_threshold * 1.4, noise_floor * 1.6 + 0.005))
        self.end_silence_seconds = end_silence_seconds
        self.window = deque(maxlen=5)
        self.speech_seen = False
        self.quiet_seconds = 0.0

    def feed(self, rms, seconds):
        if not math.isfinite(rms) or not 0 <= rms <= 1 or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Endpoint input must be normalized RMS and positive duration.")
        self.window.append(rms >= self.speech_threshold)
        if not self.speech_seen:
            if sum(self.window) >= 2 or rms >= self.strong_threshold:
                self.speech_seen = True
            return False
        if rms >= self.continue_threshold:
            self.quiet_seconds = 0.0
        else:
            self.quiet_seconds += seconds
        return self.quiet_seconds + 1e-9 >= self.end_silence_seconds


class NativeRequestReader:
    """One arecord, bounded nonblocking pipe reads, no sleeping/unread warm-up.

    Exact-size reads preserve phase boundaries even with short pipe reads.
    Blocking selector waits and a wall deadline also bound a stalled device.
    """

    def __init__(self, device, timeout_seconds):
        self.device = device
        self.timeout_seconds = timeout_seconds
        self.process = None
        self.selector = None
        self.deadline = None

    def start(self):
        try:
            self.process = subprocess.Popen(
                ["arecord", "-D", self.device, "-c", "2", "-r", "48000", "-f", "S32_LE", "-t", "raw"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
            )
            self.deadline = time.monotonic() + self.timeout_seconds
            os.set_blocking(self.process.stdout.fileno(), False)
            self.selector = selectors.DefaultSelector()
            self.selector.register(self.process.stdout, selectors.EVENT_READ)
        except BaseException:
            self.close()
            raise

    def read_frames(self, frames):
        pending = bytearray()
        size = frames * FRAME_BYTES
        while len(pending) < size:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise HandsFreeCaptureError("Request arecord exceeded its bounded capture deadline.")
            if not self.selector.select(min(0.25, remaining)):
                if self.process.poll() is not None:
                    raise HandsFreeCaptureError(f"Request arecord exited (exit {self.process.returncode}).")
                continue
            try:
                chunk = os.read(self.process.stdout.fileno(), size - len(pending))
            except BlockingIOError:
                continue
            if not chunk:
                raise HandsFreeCaptureError("Request arecord closed its PCM stream; check the ALSA capture device.")
            pending.extend(chunk)
        return bytes(pending)

    def close(self):
        process, self.process = self.process, None
        try:
            if process is not None:
                if process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        pass
                    process.wait(timeout=5)
        finally:
            if self.selector is not None:
                self.selector.close()
                self.selector = None
            if process is not None and process.stdout is not None:
                process.stdout.close()


def record_audio_hands_free(output_path: Path, device: str, config: HandsFreeCaptureConfig,
                           *, on_capture_ready=None, on_amplitude=None):
    """Drain, calibrate, then write every post-ready frame to a bounded WAV.

    No classification result can remove/reset audio or stop a waiting budget.
    The detector can only shorten the recording after speech followed by quiet.
    """
    config.validate()
    if not device:
        raise HandsFreeCaptureError("An ALSA device is required for hands-free request capture.")
    reader = NativeRequestReader(device, config.warmup_seconds + config.calibration_seconds + config.max_seconds + 5)
    captured_frames = 0
    detector = None
    stop_reason = None

    def phase(seconds):
        remaining = round(seconds * SAMPLE_RATE)
        while remaining:
            count = min(CHUNK_FRAMES, remaining)
            yield reader.read_frames(count)
            remaining -= count

    try:
        reader.start()
        for _chunk in phase(config.warmup_seconds):
            pass  # Actively drain exact native frames; never retain warm-up PCM.
        noise = [normalized_left_rms(chunk) for chunk in phase(config.calibration_seconds)]
        detector = HandsFreeEndpointDetector(median(noise), config.end_silence_seconds)
        print("Speak now...")
        if on_capture_ready is not None:
            on_capture_ready()
        max_frames = round(config.max_seconds * SAMPLE_RATE)
        with wave.open(str(output_path), "wb") as output:
            output.setnchannels(CHANNELS)
            output.setsampwidth(SAMPLE_WIDTH)
            output.setframerate(SAMPLE_RATE)
            while captured_frames < max_frames:
                count = min(CHUNK_FRAMES, max_frames - captured_frames)
                chunk = reader.read_frames(count)
                output.writeframesraw(chunk)  # Always retain before consulting the endpoint.
                captured_frames += count
                rms = normalized_left_rms(chunk)
                ended = detector.feed(rms, count / SAMPLE_RATE)
                observe(on_amplitude, rms)
                if captured_frames >= max_frames:
                    stop_reason = "max_duration"
                    break
                if ended:
                    stop_reason = "silence"
                    break
    except (HandsFreeCaptureError, KeyboardInterrupt):
        raise
    except Exception as exc:
        raise HandsFreeCaptureError(f"Hands-free recording failed: {exc}") from exc
    finally:
        reader.close()
        if stop_reason is not None:
            print(
                f"Hands-free capture: noise_floor={detector.noise_floor:.4f} "
                f"speech_threshold={detector.speech_threshold:.4f} "
                f"continue_threshold={detector.continue_threshold:.4f} "
                f"speech_seen={str(detector.speech_seen).lower()} "
                f"capture_duration={captured_frames / SAMPLE_RATE:.2f}s stop_reason={stop_reason}"
            )
