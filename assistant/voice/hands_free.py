"""Post-wake request recording: retain audio unconditionally, endpoint optionally."""

from assistant import latency_trace as latency
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
MAX_PREROLL_BYTES = 15360 * FRAME_BYTES  # 320 ms, memory only until request WAV.
MAX_INTERRUPTION_HANDOFF_BYTES = 2 * SAMPLE_RATE * FRAME_BYTES


class HandsFreeCaptureError(Exception):
    """A hardware, pipe or recording-file failure, rather than absence of speech."""


@dataclass(frozen=True)
class HandsFreeCaptureConfig:
    warmup_seconds: float = 0.25
    calibration_seconds: float = 0.25
    max_seconds: float = 6.0
    end_silence_seconds: float = 0.9
    noise_relative_endpoint: bool = False

    @classmethod
    def from_settings(cls):
        from django.conf import settings
        values = {}
        for field in cls.__dataclass_fields__.values():
            if field.name == 'noise_relative_endpoint':
                continue # CLI opt-in only, never change deployed settings.
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

    def __init__(self, noise_floor, end_silence_seconds, *, relative=False, noise_relative=False):
        if not math.isfinite(noise_floor) or not 0 <= noise_floor <= 1:
            raise ValueError("Endpoint noise floor must be normalized RMS.")
        if not math.isfinite(end_silence_seconds) or end_silence_seconds <= 0:
            raise ValueError("Endpoint silence must be finite and positive.")
        self.noise_floor = noise_floor
        self.speech_threshold = min(0.08, max(0.035, noise_floor * 1.15 + 0.003))
        self.continue_threshold = min(self.speech_threshold * 0.95, max(0.015, noise_floor * 1.05 + 0.001))
        self.strong_threshold = min(1.0, max(self.speech_threshold * 1.4, noise_floor * 1.6 + 0.005))
        if relative:
            self.speech_threshold = min(.08, max(.012, noise_floor * 1.6 + .002))
            self.continue_threshold = min(self.speech_threshold * .95, max(.008, noise_floor * 1.2 + .001))
            self.strong_threshold = min(1.0, max(self.speech_threshold * 1.4, noise_floor * 1.6 + .005))
        self.minimum_voice_seconds = .3 if relative else 0.0
        if noise_relative:
            # Never cap below the observed fan floor. Do not learn from speech.
            self.speech_threshold = min(1., max(.002, noise_floor * 1.8 + .001))
            self.continue_threshold = min(self.speech_threshold * .95, max(.001, noise_floor * 1.25 + .0005))
            self.strong_threshold = min(1., self.speech_threshold * 1.4)
            self.minimum_voice_seconds = .3
        self.voiced_seconds = 0.0
        self.end_silence_seconds = end_silence_seconds
        self.window = deque(maxlen=5)
        self.speech_seen = False
        self.quiet_seconds = 0.0
        self.quiet_resets = 0
        self.longest_quiet_seconds = 0.0

    def feed(self, rms, seconds):
        if not math.isfinite(rms) or not 0 <= rms <= 1 or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Endpoint input must be normalized RMS and positive duration.")
        self.window.append(rms >= self.speech_threshold)
        if rms >= self.speech_threshold or self.speech_seen and rms >= self.continue_threshold:
            self.voiced_seconds += seconds
        if not self.speech_seen:
            if sum(self.window) >= 2 or rms >= self.strong_threshold:
                self.speech_seen = True
            return False
        if rms >= self.continue_threshold:
            if self.quiet_seconds:
                self.quiet_resets += 1
            self.quiet_seconds = 0.0
        else:
            self.quiet_seconds += seconds
            self.longest_quiet_seconds = max(self.longest_quiet_seconds, self.quiet_seconds)
        return (self.quiet_seconds + 1e-9 >= self.end_silence_seconds and
                self.voiced_seconds + 1e-9 >= self.minimum_voice_seconds)


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

    @latency.span("capture_open")
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

    def read_frames(self, frames, *, keep_recording=None):
        pending = bytearray()
        size = frames * FRAME_BYTES
        while len(pending) < size:
            if keep_recording is not None and not keep_recording():
                return bytes(pending[:len(pending) // FRAME_BYTES * FRAME_BYTES])
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise HandsFreeCaptureError("Request arecord exceeded its bounded capture deadline.")
            if not self.selector.select(min(0.01 if keep_recording is not None else 0.25, remaining)):
                if self.process.poll() is not None:
                    raise HandsFreeCaptureError(f"Request arecord exited (exit {self.process.returncode}).")
                continue
            try:
                chunk = os.read(self.process.stdout.fileno(), size - len(pending))
            except BlockingIOError:
                continue
            if not chunk:
                raise HandsFreeCaptureError("Request arecord closed its PCM stream; check the ALSA capture device.")
            latency.mark("capture_first_frame", once=True)
            pending.extend(chunk)
        return bytes(pending)

    @latency.span("capture_cleanup")
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


@latency.span("capture")
def record_audio_hands_free(output_path: Path, device: str, config: HandsFreeCaptureConfig,
                           *, on_capture_ready=None, on_amplitude=None,
                           immediate=False, initial_audio=b"", background_rms=None, interruption_handoff=False):
    """Retain native request PCM with bounded endpoint/fixed-duration capture.

    CLI immediate mode skips the legacy caller's drain/calibration phases and
    prepends the bounded wake tail. No classification result removes/reset audio.
    The detector can only shorten the recording after speech followed by quiet.
    """
    config.validate()
    if not device:
        raise HandsFreeCaptureError("An ALSA device is required for hands-free request capture.")
    limit = MAX_INTERRUPTION_HANDOFF_BYTES if interruption_handoff else MAX_PREROLL_BYTES
    if len(initial_audio) > limit or len(initial_audio) % FRAME_BYTES:
        raise HandsFreeCaptureError("Invalid bounded native wake handoff PCM.")
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
        if immediate:
            # Wake standby already supplied background; never discard a question
            # to calibrate on the user's opening words. Unknown floor => fixed cap.
            if background_rms is not None and math.isfinite(background_rms) and .0001 <= background_rms <= 1:
                detector = HandsFreeEndpointDetector(background_rms, config.end_silence_seconds, relative=True,
                                                    noise_relative=config.noise_relative_endpoint)
        else:
            for _chunk in phase(config.warmup_seconds):
                pass
            noise = [normalized_left_rms(chunk) for chunk in phase(config.calibration_seconds)]
            latency.mark("calibration_end", once=True)
            detector = HandsFreeEndpointDetector(median(noise), config.end_silence_seconds,
                                                noise_relative=config.noise_relative_endpoint)
        latency.mark("capture_ready", once=True)
        print("Speak now...")
        if on_capture_ready is not None:
            on_capture_ready()
        max_frames = round(config.max_seconds * SAMPLE_RATE)
        with wave.open(str(output_path), "wb") as output:
            output.setnchannels(CHANNELS)
            output.setsampwidth(SAMPLE_WIDTH)
            output.setframerate(SAMPLE_RATE)
            output.writeframesraw(initial_audio)
            while captured_frames < max_frames:
                count = min(CHUNK_FRAMES, max_frames - captured_frames)
                chunk = reader.read_frames(count)
                output.writeframesraw(chunk)  # Always retain before consulting the endpoint.
                captured_frames += count
                rms = normalized_left_rms(chunk)
                ended = detector.feed(rms, count / SAMPLE_RATE) if detector else False
                if detector and detector.speech_seen:
                    latency.mark("speech_detected", once=True)
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
        if stop_reason is not None:
            latency.mark("capture_stopped", once=True, reason=stop_reason)
            if stop_reason == "silence":
                latency.mark("endpoint_detected", once=True, reason=stop_reason)
        reader.close()
        if stop_reason is not None and detector is None:
            print(f"Hands-free capture: background=unknown capture_duration={captured_frames / SAMPLE_RATE:.2f}s stop_reason={stop_reason}")
        elif stop_reason is not None:
            if config.noise_relative_endpoint:
                print(f"Endpoint diagnostics: voiced_seconds={detector.voiced_seconds:.3f} "
                      f"longest_quiet_seconds={detector.longest_quiet_seconds:.3f} quiet_resets={detector.quiet_resets}")
            print(
                f"Hands-free capture: noise_floor={detector.noise_floor:.4f} "
                f"speech_threshold={detector.speech_threshold:.4f} "
                f"continue_threshold={detector.continue_threshold:.4f} "
                f"speech_seen={str(detector.speech_seen).lower()} "
                f"capture_duration={captured_frames / SAMPLE_RATE:.2f}s stop_reason={stop_reason}"
            )
