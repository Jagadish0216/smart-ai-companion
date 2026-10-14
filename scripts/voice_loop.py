#!/usr/bin/env python3
"""Standalone interactive or opt-in hands-free voice loop for the Raspberry Pi."""

from __future__ import annotations

import argparse
import io
import logging
import math
import os
import re
import struct
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue
import subprocess
import sys
import tempfile
from threading import Event, Lock, Timer
import time
from typing import Callable
import wave


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

from assistant.services import AssistantService  # noqa: E402
from assistant.display import DisplayState, get_companion_display  # noqa: E402
from assistant.policy import AssistantResponsePolicy  # noqa: E402
from assistant.execution_policy import CONSTRAINED_RESOURCE_STATUS, REDUCED_RESOURCE_STATUS  # noqa: E402
from assistant.voice.factory import close_tts_provider, get_stt_provider, get_tts_provider  # noqa: E402
from assistant.voice.processes import run_cancellable  # noqa: E402
from assistant.voice.tts import TextToSpeechProvider  # noqa: E402
from assistant.voice.alsa_stream import PersistentAlsaPlayer, trim_pcm_edges  # noqa: E402
from assistant.voice.capture_vad import AdaptiveCaptureVAD  # noqa: E402
from assistant.voice.hands_free import HandsFreeCaptureConfig, record_audio_hands_free  # noqa: E402
from assistant.voice.wakeword.base import WakeWordConfig, WakeWordError  # noqa: E402
from assistant.voice.wakeword.factory import get_wake_word_provider  # noqa: E402

CAPTURE_SAMPLE_RATE = 16000
CAPTURE_SAMPLE_WIDTH = 2
CAPTURE_CHUNK_SECONDS = 0.02
CAPTURE_PREROLL_SECONDS = 0.3
ALSA_SAMPLE_RATE = 48000
ALSA_SAMPLE_WIDTH = 4
ALSA_CHANNELS = 2
logger = logging.getLogger(__name__)


def _set_display_state(display, state):
    """Keep even unexpected display-provider failures out of the voice path."""
    if display is not None:
        try:
            display.set_state(state)
        except Exception:
            logger.warning("Companion display update failed; voice interaction continues")


def _set_display_text(display, text):
    """Keep answer-text display failures out of the voice path and logs."""
    if display is not None:
        try:
            display.set_text(text)
        except Exception:
            logger.warning("Companion display text update failed; voice interaction continues")


class VoiceLoopError(Exception):
    """Raised when one stage of a voice cycle cannot complete."""


class NoSpeechDetected(VoiceLoopError):
    """A completed hands-free recording had no recognized request speech."""


@dataclass(frozen=True)
class VoiceLoopConfig:
    input_source: str
    record_seconds: float = 5.0
    leading_silence_seconds: float = 0.7
    tts_chunk_chars: int = 300
    tts_max_total_chars: int = 0
    input_warmup_seconds: float = 1.0
    capture_vad_enabled: bool = False
    capture_vad_start_threshold: float = 0.02
    capture_vad_silence_seconds: float = 0.8
    capture_vad_max_seconds: float = 10.0
    capture_vad_start_timeout_seconds: float = 5.0
    audio_backend: str = "pulse"
    alsa_capture_device: str = "hw:CARD=sndrpigooglevoi,DEV=0"
    alsa_playback_device: str = "hw:CARD=sndrpigooglevoi,DEV=0"
    playback_gain: float = 4.0
    stream_tts_target_chars: int = 180
    stream_inter_chunk_silence_seconds: float = 0.12
    stream_first_chunk_min_chars: int = 60
    stream_first_chunk_target_chars: int = 100
    capture_vad_adaptive: bool = True
    capture_vad_noise_calibration_seconds: float = 0.5
    capture_vad_start_confirm_ms: int = 60
    capture_vad_min_speech_ms: int = 300

    @classmethod
    def from_settings(cls, *, require_capture: bool = True) -> "VoiceLoopConfig":
        config = cls(
            input_source=str(getattr(settings, "VOICE_INPUT_SOURCE", "")).strip(),
            record_seconds=float(getattr(settings, "VOICE_RECORD_SECONDS", 5)),
            leading_silence_seconds=float(
                getattr(settings, "VOICE_LEADING_SILENCE_SECONDS", 0.7)
            ),
            tts_chunk_chars=int(getattr(settings, "VOICE_TTS_CHUNK_CHARS", 300)),
            tts_max_total_chars=int(
                getattr(settings, "VOICE_TTS_MAX_TOTAL_CHARS", 0)
            ),
            input_warmup_seconds=float(
                getattr(settings, "VOICE_INPUT_WARMUP_SECONDS", 1.0)
            ),
            capture_vad_enabled=_as_bool(
                getattr(settings, "VOICE_CAPTURE_VAD_ENABLED", False)
            ),
            capture_vad_start_threshold=float(
                getattr(settings, "VOICE_CAPTURE_VAD_START_THRESHOLD", 0.02)
            ),
            capture_vad_silence_seconds=float(
                getattr(settings, "VOICE_CAPTURE_VAD_SILENCE_SECONDS", 0.8)
            ),
            capture_vad_max_seconds=float(
                getattr(settings, "VOICE_CAPTURE_VAD_MAX_SECONDS", 10)
            ),
            capture_vad_start_timeout_seconds=float(
                getattr(settings, "VOICE_CAPTURE_VAD_START_TIMEOUT_SECONDS", 5)
            ),
            audio_backend=str(getattr(settings, "VOICE_AUDIO_BACKEND", "pulse")).strip().lower(),
            alsa_capture_device=str(
                getattr(settings, "VOICE_ALSA_CAPTURE_DEVICE", "hw:CARD=sndrpigooglevoi,DEV=0")
            ).strip(),
            alsa_playback_device=str(
                getattr(settings, "VOICE_ALSA_PLAYBACK_DEVICE", "hw:CARD=sndrpigooglevoi,DEV=0")
            ).strip(),
            playback_gain=float(getattr(settings, "VOICE_PLAYBACK_GAIN", 4.0)),
            stream_tts_target_chars=int(getattr(settings, "VOICE_STREAM_TTS_TARGET_CHARS", 180)),
            stream_inter_chunk_silence_seconds=float(
                getattr(settings, "VOICE_STREAM_INTER_CHUNK_SILENCE_SECONDS", 0.12)
            ),
            stream_first_chunk_min_chars=int(getattr(settings, "VOICE_STREAM_FIRST_CHUNK_MIN_CHARS", 60)),
            stream_first_chunk_target_chars=int(getattr(settings, "VOICE_STREAM_FIRST_CHUNK_TARGET_CHARS", 100)),
            capture_vad_adaptive=_as_bool(getattr(settings, "VOICE_CAPTURE_VAD_ADAPTIVE", True)),
            capture_vad_noise_calibration_seconds=float(getattr(settings, "VOICE_CAPTURE_VAD_NOISE_CALIBRATION_SECONDS", 0.5)),
            capture_vad_start_confirm_ms=int(getattr(settings, "VOICE_CAPTURE_VAD_START_CONFIRM_MS", 60)),
            capture_vad_min_speech_ms=int(getattr(settings, "VOICE_CAPTURE_VAD_MIN_SPEECH_MS", 300)),
        )
        config.validate(require_capture=require_capture)
        return config

    def validate(self, *, require_capture: bool = True) -> None:
        if self.audio_backend not in {"pulse", "alsa"}:
            raise VoiceLoopError("VOICE_AUDIO_BACKEND must be pulse or alsa.")
        if self.audio_backend == "alsa" and (
            (require_capture and not self.alsa_capture_device) or not self.alsa_playback_device
        ):
            raise VoiceLoopError("ALSA capture and playback devices must be configured.")
        if not math.isfinite(self.playback_gain) or self.playback_gain <= 0:
            raise VoiceLoopError("VOICE_PLAYBACK_GAIN must be finite and greater than zero.")
        for name in (
            "record_seconds", "leading_silence_seconds", "input_warmup_seconds",
            "capture_vad_start_threshold", "capture_vad_silence_seconds",
            "capture_vad_max_seconds", "capture_vad_start_timeout_seconds",
        ):
            if not math.isfinite(getattr(self, name)):
                raise VoiceLoopError(f"Voice setting {name} must be finite.")
        if require_capture and self.audio_backend == "pulse" and not self.input_source:
            raise VoiceLoopError(
                "VOICE_INPUT_SOURCE is not configured. Use 'pactl list short sources' "
                "to find the PipeWire/PulseAudio source name."
            )
        if self.record_seconds <= 0:
            raise VoiceLoopError("VOICE_RECORD_SECONDS must be greater than zero.")
        if self.leading_silence_seconds < 0:
            raise VoiceLoopError(
                "VOICE_LEADING_SILENCE_SECONDS cannot be negative."
            )
        if self.tts_chunk_chars <= 0:
            raise VoiceLoopError("VOICE_TTS_CHUNK_CHARS must be greater than zero.")
        if self.stream_tts_target_chars <= 0:
            raise VoiceLoopError("VOICE_STREAM_TTS_TARGET_CHARS must be greater than zero.")
        if not math.isfinite(self.stream_inter_chunk_silence_seconds) or not 0 <= self.stream_inter_chunk_silence_seconds <= 1:
            raise VoiceLoopError("VOICE_STREAM_INTER_CHUNK_SILENCE_SECONDS must be finite and between 0 and 1.")
        if self.stream_first_chunk_min_chars <= 0 or self.stream_first_chunk_target_chars < self.stream_first_chunk_min_chars:
            raise VoiceLoopError("VOICE_STREAM_FIRST_CHUNK_MIN_CHARS must be positive and no greater than VOICE_STREAM_FIRST_CHUNK_TARGET_CHARS.")
        if self.tts_max_total_chars < 0:
            raise VoiceLoopError("VOICE_TTS_MAX_TOTAL_CHARS cannot be negative.")
        if self.input_warmup_seconds < 0:
            raise VoiceLoopError("VOICE_INPUT_WARMUP_SECONDS cannot be negative.")
        if not 0 < self.capture_vad_start_threshold <= 1:
            raise VoiceLoopError(
                "VOICE_CAPTURE_VAD_START_THRESHOLD must be greater than zero and at most 1."
            )
        if self.capture_vad_silence_seconds <= 0:
            raise VoiceLoopError(
                "VOICE_CAPTURE_VAD_SILENCE_SECONDS must be greater than zero."
            )
        if self.capture_vad_max_seconds <= 0:
            raise VoiceLoopError(
                "VOICE_CAPTURE_VAD_MAX_SECONDS must be greater than zero."
            )
        if self.capture_vad_start_timeout_seconds <= 0:
            raise VoiceLoopError(
                "VOICE_CAPTURE_VAD_START_TIMEOUT_SECONDS must be greater than zero."
            )
        for name in ("capture_vad_noise_calibration_seconds", "capture_vad_start_confirm_ms", "capture_vad_min_speech_ms"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise VoiceLoopError(f"Voice setting {name} must be finite and positive.")
        if not 40 <= self.capture_vad_start_confirm_ms <= 100:
            raise VoiceLoopError("VOICE_CAPTURE_VAD_START_CONFIRM_MS must be between 40 and 100.")


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _stderr_text(stderr: bytes | str | None) -> str:
    if isinstance(stderr, bytes):
        return stderr.decode("utf-8", errors="replace").strip()
    return (stderr or "").strip()


def _terminate_and_reap(process: subprocess.Popen) -> tuple[bytes, bytes]:
    """Stop a child process and ensure it is reaped, killing as a fallback."""
    try:
        process.terminate()
    except OSError:
        pass

    try:
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        return process.communicate()


def record_audio(
    output_path: Path,
    source: str,
    duration_seconds: float,
    warmup_seconds: float = 1.0,
) -> None:
    """Warm up a PulseAudio source, then record a full speech window."""
    command = [
        "parecord",
        f"--device={source}",
        "--file-format=wav",
        str(output_path),
    ]
    _record_audio_command(command, output_path, duration_seconds, warmup_seconds)


def record_audio_alsa(
    output_path: Path, device: str, duration_seconds: float,
    warmup_seconds: float = 1.0,
) -> None:
    """Capture the native INMP441 stereo S32_LE format without channel mixing."""
    command = ["arecord", "-D", device, "-c", "2", "-r", "48000", "-f", "S32_LE",
               "-t", "wav", str(output_path)]
    _record_audio_command(command, output_path, duration_seconds, warmup_seconds)


def _record_audio_command(
    command: list[str], output_path: Path, duration_seconds: float, warmup_seconds: float,
) -> None:
    binary = command[0]

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise VoiceLoopError(
            f"Recording failed: {binary} was not found on PATH."
        ) from exc
    except OSError as exc:
        raise VoiceLoopError(f"Recording failed: {exc}") from exc

    try:
        time.sleep(warmup_seconds)
        print("Speak now...")
        _, stderr = process.communicate(timeout=duration_seconds)
    except subprocess.TimeoutExpired:
        try:
            _terminate_and_reap(process)
        except Exception as exc:
            raise VoiceLoopError(f"Recording cleanup failed: {exc}") from exc
    except KeyboardInterrupt:
        try:
            _terminate_and_reap(process)
        except Exception:
            pass
        raise
    except Exception as exc:
        try:
            _terminate_and_reap(process)
        except Exception:
            pass
        raise VoiceLoopError(f"Recording failed: {exc}") from exc
    else:
        if process.returncode != 0:
            detail = _stderr_text(stderr)
            raise VoiceLoopError(
                f"Recording failed ({binary} exit {process.returncode})"
                + (f": {detail}" if detail else ".")
            )

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise VoiceLoopError(f"Recording failed: {binary} produced no audio file.")


def _normalized_pcm_rms(chunk: bytes) -> float:
    """Return the RMS amplitude of little-endian signed 16-bit PCM on a 0..1 scale."""
    sample_bytes = len(chunk) - (len(chunk) % CAPTURE_SAMPLE_WIDTH)
    if sample_bytes == 0:
        return 0.0
    samples = struct.iter_unpack("<h", chunk[:sample_bytes])
    square_sum = sum(sample[0] * sample[0] for sample in samples)
    sample_count = sample_bytes // CAPTURE_SAMPLE_WIDTH
    return math.sqrt(square_sum / sample_count) / 32768.0


def _normalized_alsa_left_rms(chunk: bytes) -> float:
    """Normalize signed 32-bit LEFT samples; the silent/right channel is ignored."""
    frame_bytes = ALSA_SAMPLE_WIDTH * ALSA_CHANNELS
    length = len(chunk) - len(chunk) % frame_bytes
    if not length:
        return 0.0
    squares = sum(left * left for left, _right in struct.iter_unpack("<ii", chunk[:length]))
    return math.sqrt(squares / (length // frame_bytes)) / 2147483648.0


def record_audio_with_capture_vad(
    output_path: Path,
    source: str,
    warmup_seconds: float,
    start_threshold: float,
    silence_seconds: float,
    max_seconds: float,
    start_timeout_seconds: float,
    *, backend: str = "pulse", adaptive: bool = True,
    noise_calibration_seconds: float = 0.5, start_confirm_ms: int = 60,
    min_speech_ms: int = 300,
    on_capture_ready: Callable[[], None] | None = None,
    max_start_threshold: float | None = None,
) -> None:
    """Record until speech ends; signal ready once after warm-up/calibration.

    Callback errors follow the same terminate/reap path as capture errors.
    Omitting it and the optional adaptive ceiling preserves ordinary capture.
    """
    vad = AdaptiveCaptureVAD(
        start_threshold=start_threshold, silence_seconds=silence_seconds,
        max_seconds=max_seconds, start_timeout_seconds=start_timeout_seconds,
        adaptive=adaptive, noise_calibration_seconds=noise_calibration_seconds,
        start_confirm_ms=start_confirm_ms, min_speech_ms=min_speech_ms,
        chunk_seconds=CAPTURE_CHUNK_SECONDS,
        max_start_threshold=max_start_threshold,
    )
    sample_rate, sample_width, channels = CAPTURE_SAMPLE_RATE, CAPTURE_SAMPLE_WIDTH, 1
    command = [
        "parecord",
        f"--device={source}",
        "--raw",
        "--format=s16le",
        f"--rate={CAPTURE_SAMPLE_RATE}",
        "--channels=1",
    ]
    if backend == "alsa":
        sample_rate, sample_width, channels = ALSA_SAMPLE_RATE, ALSA_SAMPLE_WIDTH, ALSA_CHANNELS
        command = ["arecord", "-D", source, "-c", "2", "-r", "48000", "-f", "S32_LE", "-t", "raw"]
    elif backend != "pulse":
        raise VoiceLoopError("Unsupported capture audio backend.")
    binary = command[0]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise VoiceLoopError(
            f"Recording failed: {binary} was not found on PATH."
        ) from exc
    except OSError as exc:
        raise VoiceLoopError(f"Recording failed: {exc}") from exc

    chunk_bytes = round(
        sample_rate
        * sample_width * channels
        * CAPTURE_CHUNK_SECONDS
    )
    preroll_chunks = max(
        1,
        math.ceil(CAPTURE_PREROLL_SECONDS / CAPTURE_CHUNK_SECONDS),
    )
    preroll = deque(maxlen=preroll_chunks)
    frames: list[bytes] = []
    watchdog = None
    if backend == "alsa":
        # A stalled arecord pipe must not block forever waiting for PCM. Killing
        # closes stdout; the main thread performs the normal terminate/reap path.
        def stop_stalled_capture():
            try:
                process.kill()
            except OSError:
                pass
        watchdog = Timer(
            warmup_seconds + start_timeout_seconds + max_seconds + 1.0,
            stop_stalled_capture,
        )
        watchdog.daemon = True

    try:
        if watchdog is not None:
            watchdog.start()
        time.sleep(warmup_seconds)
        if adaptive:
            print(f"Keep quiet: calibrating capture noise for {noise_calibration_seconds:g}s...")
        else:
            print("Speak now...")
            if on_capture_ready is not None:
                on_capture_ready()
        if process.stdout is None:
            raise VoiceLoopError(f"Recording failed: {binary} audio stream is unavailable.")

        while True:
            chunk = process.stdout.read(chunk_bytes)
            if not chunk:
                raise VoiceLoopError(
                    f"Recording failed: {binary} stopped before capture completed or timed out."
                )
            chunk_duration = (
                len(chunk) / (sample_width * channels) / sample_rate
            )
            rms = (
                _normalized_alsa_left_rms(chunk)
                if backend == "alsa" else _normalized_pcm_rms(chunk)
            )
            was_started, was_calibrated = vad.speech_started, vad.calibrated
            event = vad.feed(rms, chunk_duration)
            if not was_calibrated and vad.calibrated and vad.stop_reason is None:
                print("Speak now...")
                if on_capture_ready is not None:
                    on_capture_ready()
            if not was_started:
                preroll.append(chunk)
                if event == "start":
                    frames.extend(preroll)
                    preroll.clear()
            else:
                frames.append(chunk)
            if event == "reset":
                frames.clear()
                preroll.clear()
            if vad.stop_reason == "start_timeout":
                raise VoiceLoopError(
                    "Recording stopped: no speech detected before the start timeout."
                )
            if vad.stop_reason is not None:
                if not vad.accepted:
                    raise VoiceLoopError("Recording stopped: insufficient voiced speech before maximum duration.")
                break
    except KeyboardInterrupt:
        try:
            _terminate_and_reap(process)
        except Exception:
            pass
        raise
    except VoiceLoopError:
        try:
            _terminate_and_reap(process)
        except Exception:
            pass
        raise
    except Exception as exc:
        try:
            _terminate_and_reap(process)
        except Exception:
            pass
        raise VoiceLoopError(f"Recording failed: {exc}") from exc
    else:
        try:
            _terminate_and_reap(process)
        except Exception as exc:
            raise VoiceLoopError(f"Recording cleanup failed: {exc}") from exc
    finally:
        if watchdog is not None:
            watchdog.cancel()
        if vad.stop_reason is not None:
            print(
                f"Capture VAD: noise_floor={vad.noise_floor:.4f} "
                f"start_threshold={vad.start_threshold:.4f} "
                f"continue_threshold={vad.continue_threshold:.4f} "
                f"max_wait_rms={vad.max_wait_rms:.4f} "
                f"speech_duration={vad.speech_duration:.2f}s "
                f"capture_duration={vad.capture_duration:.2f}s "
                f"elapsed={vad.elapsed:.2f}s stop_reason={vad.stop_reason}"
            )

    try:
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(b"".join(frames))
    except (OSError, wave.Error) as exc:
        raise VoiceLoopError(f"Recording failed: could not save WAV ({exc}).") from exc

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise VoiceLoopError(f"Recording failed: {binary} produced no audio file.")
    captured_audio_seconds = sum(len(frame) for frame in frames) / (
        sample_rate * sample_width * channels
    )
    print(f"Capture complete: {captured_audio_seconds:.1f}s audio.")


def normalize_audio(input_path: Path, output_path: Path, *, left_channel: bool = False) -> None:
    """Convert the recording to the mono 16 kHz PCM format whisper.cpp expects."""
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        *(["-af", "pan=mono|c0=FL"] if left_channel else []),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=60)
    except FileNotFoundError as exc:
        raise VoiceLoopError(
            "Audio conversion failed: ffmpeg was not found on PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise VoiceLoopError("Audio conversion failed: ffmpeg timed out.") from exc
    except OSError as exc:
        raise VoiceLoopError(f"Audio conversion failed: {exc}") from exc

    if result.returncode != 0:
        detail = _stderr_text(result.stderr)
        raise VoiceLoopError(
            f"Audio conversion failed (ffmpeg exit {result.returncode})"
            + (f": {detail}" if detail else ".")
        )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise VoiceLoopError("Audio conversion failed: ffmpeg produced no WAV file.")


def prepend_leading_silence(wav_bytes: bytes, duration_seconds: float) -> bytes:
    """Return PCM WAV bytes with silence prepended using the source WAV format."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as source:
            params = source.getparams()
            frames = source.readframes(source.getnframes())
    except (EOFError, wave.Error) as exc:
        raise VoiceLoopError(f"TTS failed: invalid WAV output ({exc}).") from exc

    silence_frame_count = round(params.framerate * duration_seconds)
    silence = b"\x00" * silence_frame_count * params.nchannels * params.sampwidth
    output = io.BytesIO()
    try:
        with wave.open(output, "wb") as destination:
            destination.setparams(params)
            destination.writeframes(silence + frames)
    except wave.Error as exc:
        raise VoiceLoopError(f"TTS failed: could not prepare playback WAV ({exc}).") from exc
    return output.getvalue()


def sanitize_text_for_speech(text: str) -> str:
    """Remove common Markdown syntax while preserving its readable words."""
    speech_text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    speech_text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", speech_text)
    speech_text = re.sub(r"(?m)^\s*(?:[-*_]\s*){3,}$", " ", speech_text)
    speech_text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", speech_text)
    speech_text = re.sub(r"(?m)^\s*[-*•]+\s+", "", speech_text)
    speech_text = re.sub(r"(?m)^\s*>\s?", "", speech_text)
    speech_text = re.sub(r"[*_`]+", "", speech_text)
    return re.sub(r"\s+", " ", speech_text).strip()


def _sentence_spans(text: str) -> list[str]:
    """Split normalized text after sentence punctuation, retaining all words."""
    sentences = []
    start = 0
    for match in re.finditer(r"[.!?](?:[\"')\]]+)?(?=\s|$)", text):
        sentences.append(text[start:match.end()].strip())
        start = match.end()
    remainder = text[start:].strip()
    if remainder:
        sentences.append(remainder)
    return [sentence for sentence in sentences if sentence]


def limit_text_for_speech(text: str, max_total_chars: int) -> str:
    """Apply an optional total safety limit only at a complete sentence boundary."""
    if max_total_chars == 0 or len(text) <= max_total_chars:
        return text

    sentence_ends = [
        match.end()
        for match in re.finditer(r"[.!?](?:[\"')\]]+)?(?=\s|$)", text)
    ]
    boundaries_within_limit = [
        boundary for boundary in sentence_ends if boundary <= max_total_chars
    ]
    if boundaries_within_limit:
        return text[:boundaries_within_limit[-1]].strip()

    # Never cut a sentence or word merely to meet the safety limit. If the first
    # sentence itself is longer, retain it as the smallest clean spoken unit.
    if sentence_ends:
        return text[:sentence_ends[0]].strip()
    return text


def _split_words(text: str, target_chars: int) -> list[str]:
    """Split an unusually long sentence without splitting individual words."""
    chunks = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > target_chars:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def chunk_text_for_speech(text: str, target_chars: int) -> list[str]:
    """Pack text into natural chunks, preferring complete sentence boundaries."""
    chunks = []
    current = ""
    for sentence in _sentence_spans(text):
        if len(sentence) > target_chars:
            if current:
                chunks.append(current)
                current = ""
            sentence_parts = _split_words(sentence, target_chars)
            chunks.extend(sentence_parts[:-1])
            current = sentence_parts[-1]
            continue

        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > target_chars:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def prepare_speech_chunks(
    text: str,
    chunk_chars: int = 300,
    max_total_chars: int = 0,
) -> list[str]:
    """Create Markdown-free, sentence-aware TTS chunks from an AI response."""
    sanitized = sanitize_text_for_speech(text)
    limited = limit_text_for_speech(sanitized, max_total_chars)
    return chunk_text_for_speech(limited, chunk_chars)


class StreamingSpeechBuffer:
    """Keep incomplete words/punctuation/Markdown links until safe to speak.

    A sentence end needs one character of look-ahead (normally whitespace), so
    a closing quote/bracket arriving in the next delta stays with its sentence.
    """

    def __init__(self, target_chars=180, *, first_min_chars=60, first_target_chars=100):
        self.target_chars = target_chars
        self.first_min_chars = first_min_chars
        self.first_target_chars = first_target_chars
        self.first_unit_emitted = False
        self.complete_sentence_emitted = False
        self.emitted_units = 0
        self.first_complete_unit = None
        self.pending = ""

    def _outside_groups(self, position, protected):
        """Conservative first-unit punctuation safety, not a general NLP parser."""
        stack, quote = [], None
        pairs = {"(": ")", "[": "]", "{": "}"}
        quotes = {'"': '"', "'": "'", "“": "”", "‘": "’"}
        for index, char in enumerate(self.pending[:position]):
            if any(start <= index < end for start, end in protected):
                continue
            before = self.pending[index - 1] if index else ""
            after = self.pending[index + 1:index + 2]
            if char in {"'", "’"} and before.isalnum() and after.isalnum():
                continue  # Apostrophes in words are not quotation marks.
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in quotes and not (char == "'" and before.isalnum()):
                quote = quotes[char]
            elif char in pairs:
                stack.append(pairs[char])
            elif stack and char == stack[-1]:
                stack.pop()
        return not quote and not stack

    def _first_abbreviation(self, period_position):
        token = re.search(r"([A-Za-z][A-Za-z.]*)\.$", self.pending[:period_position + 1])
        if token is None:
            return False
        word = token.group(1).lower()
        return word in {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e"} or bool(
            re.fullmatch(r"[a-z](?:\.[a-z])*", word)
        )

    @staticmethod
    def _obviously_incomplete(text):
        """Only clear dangling syntax; an unpunctuated answer is not itself an error."""
        return bool(re.search(
            r"(?:\b(?:a|an|the|and|or|because|which)|"
            r"\b(?:a|an|the)\s+[\w]+-based|[-,:;])$",
            text.strip(), re.IGNORECASE,
        ))

    def _first_fallback_boundary(self, protected, sentence_boundary, *, final):
        """Already-available phrase, then word boundary; never split a group."""
        candidates = []
        dangling = {
            "a", "an", "the", "and", "or", "but", "so", "because", "while", "which", "that",
            "to", "of", "for", "in", "on", "at", "by", "with", "from", "as", "into", "onto",
            "than", "if", "due", "up", "about", "after", "before", "between", "through", "during",
            "under", "over", "without", "within", "until", "since", "near", "around", "across",
            "toward", "towards", "throughout", "despite", "beside", "beyond", "via",
        }

        def usable(position):
            if sentence_boundary is not None and position >= sentence_boundary:
                return None
            if any(start < position < end for start, end in protected):
                return None
            if not self._outside_groups(position, protected):
                return None
            tail = self.pending[position:].lstrip()
            if self.pending[position - 1:position].isdigit() and (
                tail[:1].isdigit() or not tail and not final
            ):
                return None  # A spaced number may still be arriving.
            spoken = sanitize_text_for_speech(self.pending[:position])
            length = len(spoken)
            if not self.first_min_chars <= length <= self.first_target_chars + 20:
                return None
            last_word = re.search(r"\b([a-z]+)[^a-z]*$", spoken, re.IGNORECASE)
            if last_word and last_word.group(1).lower() in dangling:
                return None
            return length

        # Require the connector to be complete, not the start of another word.
        for match in re.finditer(r"\s+(?:because|due\s+to|while|but|so|which|that)(?=\s|$)", self.pending, re.IGNORECASE):
            if not final and match.end() == len(self.pending):
                continue
            length = usable(match.start())
            if length is not None:
                candidates.append((length, match.start()))
        if candidates:
            return max(candidates)[1]
        if len(sanitize_text_for_speech(self.pending)) < self.first_target_chars:
            return None
        for match in re.finditer(r"\s+", self.pending):
            length = usable(match.start())
            if length is not None:
                candidates.append((length, match.start()))
        before = [item for item in candidates if item[0] <= self.first_target_chars]
        return max(before)[1] if before else (min(candidates)[1] if candidates else None)

    def feed(self, text, *, final=False, omit_incomplete_tail=False):
        self.pending += text
        units = []
        while self.pending:
            protected = [match.span() for match in re.finditer(r"!?\[[^\]]*\]\([^)]*\)", self.pending)]
            # Do not split a Markdown link whose label/URL is still arriving.
            for match in re.finditer(r"!?\[", self.pending):
                if not any(start <= match.start() < end for start, end in protected):
                    if (
                        "]" not in self.pending[match.end():]
                        or re.search(r"\]\([^)]*$", self.pending[match.end():])
                        or not self.first_unit_emitted and not final and re.search(r"\]\s*$", self.pending[match.end():])
                    ):
                        protected.append((match.start(), len(self.pending)))
            if not self.first_unit_emitted:
                # First-unit fallbacks must not cut bare URLs or formatted
                # numbers (including spaced thousands, decimals and ratios).
                protected.extend(match.span() for match in re.finditer(
                    r'''\b(?:[a-z][a-z0-9+.-]*://|www\.)[^\s<>()[\]{}"']+''', self.pending, re.IGNORECASE,
                ))
                protected.extend(match.span() for match in re.finditer(
                    r"\b\d+(?:\s*[.,:/-]\s*\d+|\s+\d{3}\b)+", self.pending,
                ))

            def safe(position):
                return not any(start < position < end for start, end in protected)

            boundary = None
            sentences = []
            for match in re.finditer(r'''[.!?](?:["'’”)\]}*_`]+)?(?=\s|$)''', self.pending):
                if safe(match.start()) and safe(match.end()) and (final or match.end() < len(self.pending)):
                    if self.pending[match.start()] == "." and self._first_abbreviation(match.start()):
                        continue
                    if not self._outside_groups(match.end(), protected):
                        continue
                    sentences.append((match.end(), len(sanitize_text_for_speech(self.pending[:match.end()]))))
            # Keep punctuation inside Piper input. Hold tiny completed sentences
            # for their neighbours, with a bounded size-based (not timed) wait.
            lower = max(1, self.target_chars - 60)
            upper = self.target_chars + 40
            if not self.first_unit_emitted:
                useful = [(end, length) for end, length in sentences if length >= self.first_min_chars]
                near_first = [end for end, length in useful if length <= self.first_target_chars]
                boundary = near_first[-1] if near_first else (useful[0][0] if useful else None)
            else:
                available = [(end, length) for end, length in sentences if length <= upper]
                if available:
                    end, length = available[-1]
                    if final or length >= lower or len(sanitize_text_for_speech(self.pending)) >= upper:
                        boundary = end
                elif sentences:
                    boundary = sentences[0][0]
            if not self.first_unit_emitted and (
                boundary is None or len(sanitize_text_for_speech(self.pending[:boundary])) > self.first_target_chars
            ):
                # Prefer the latest already-available safe clause within a
                # small target grace window. Never wait just to reach target.
                clauses = []
                for match in re.finditer(r'''[,;:](?:["'’”)\]}*_`]+)?(?=\s|$)''', self.pending):
                    if boundary is not None and match.end() >= boundary:
                        break
                    if not (final or match.end() < len(self.pending)) or not safe(match.start()) or not safe(match.end()):
                        continue
                    if not self._outside_groups(match.end(), protected):
                        continue
                    before = self.pending[match.start() - 1:match.start()]
                    after = self.pending[match.end():].lstrip()[:1]
                    if before.isdigit() and after.isdigit():
                        continue  # Numeric commas/ratios are not clauses.
                    length = len(sanitize_text_for_speech(self.pending[:match.end()]))
                    if self.first_min_chars <= length <= self.first_target_chars + 20:
                        clauses.append(match.end())
                if clauses:
                    boundary = clauses[-1]
                else:
                    fallback = self._first_fallback_boundary(protected, boundary, final=final)
                    if fallback is not None:
                        boundary = fallback
            if self.first_unit_emitted and boundary is None and len(sanitize_text_for_speech(self.pending)) > self.target_chars:
                spaces = [match.start() for match in re.finditer(r"\s+", self.pending)
                          if match.start() >= min(40, self.target_chars / 2) and safe(match.start())]
                before = [position for position in spaces if position <= self.target_chars]
                boundary = before[-1] if before else (spaces[0] if spaces else None)
            if boundary is None:
                if not final:
                    break
                if (omit_incomplete_tail and self.complete_sentence_emitted
                        and self._obviously_incomplete(sanitize_text_for_speech(self.pending))):
                    self.pending = ""
                    break
                boundary = len(self.pending)
            complete_sentence = any(end <= boundary for end, _length in sentences)
            raw_unit, self.pending = self.pending[:boundary], self.pending[boundary:].lstrip()
            spoken = sanitize_text_for_speech(raw_unit)
            if spoken:
                # Later very long units still split only between words.
                # A protected first group/word may exceed the target: do not
                # undo its safe boundary by splitting it a second time.
                packed = _split_words(spoken, upper) if self.first_unit_emitted else [spoken]
                units.extend(packed)
                self.emitted_units += len(packed)
                self.first_unit_emitted = True
                self.complete_sentence_emitted |= complete_sentence
                if complete_sentence and self.first_complete_unit is None:
                    self.first_complete_unit = self.emitted_units
        return units


class LeadingResourceNoticeFilter:
    """Presentation-only removal of repeated, exact notices at the answer start.

    Model-generated repeats may span deltas. Hold only a possible leading
    notice until it is recognized or disproved; never delete interior text.
    """

    def __init__(self):
        self.pending = ""
        self.leading = True
        self.removed_notice = False

    def feed(self, text, *, final=False):
        if not self.leading:
            return text, []
        self.pending += text
        notices = []
        prefixes = (REDUCED_RESOURCE_STATUS, CONSTRAINED_RESOURCE_STATUS)
        while True:
            candidate = self.pending.lstrip()
            for prefix in prefixes:
                if candidate.startswith(prefix):
                    suffix = candidate[len(prefix):]
                    if (suffix and suffix[0].isspace()) or (not suffix and final):
                        self.pending = suffix
                        self.removed_notice = True
                        notices.append(prefix)
                        break
            else:
                if not final and (not candidate or any(prefix.startswith(candidate) for prefix in prefixes)):
                    return "", notices
                answer = self.pending.lstrip() if self.removed_notice else self.pending
                self.pending = ""
                self.leading = False
                return answer, notices


@dataclass(frozen=True)
class PreparedAudio:
    number: int
    path: Path
    speech_text: str
    leading_silence_frames: int = 0
    trailing_silence_frames: int = 0


def speak_stream_response(events, tts_provider, temp_path, config, timings, cycle_started,
                          on_playback_start: Callable[[str], None] | None = None):
    """Service producer -> bounded text -> one preparer -> bounded audio -> one player."""
    pending = Queue(maxsize=2)
    ready_audio = Queue(maxsize=2)
    stopped = Event()
    finish = object()
    errors = []
    error_lock = Lock()
    spoken_count = 0
    stream_started = time.perf_counter()
    buffer = StreamingSpeechBuffer(
        config.stream_tts_target_chars, first_min_chars=config.stream_first_chunk_min_chars,
        first_target_chars=config.stream_first_chunk_target_chars,
    )
    notice_filter = LeadingResourceNoticeFilter()
    printed_notices = set()
    terminal_open = False

    def fail(exc):
        with error_lock:
            if not stopped.is_set():
                errors.append(exc if isinstance(exc, VoiceLoopError) else VoiceLoopError(f"Streaming audio failed: {exc}"))
            stopped.set()

    def enqueue(queue, unit):
        while not stopped.is_set():
            try:
                queue.put(unit, timeout=0.05)
                return
            except Full:
                pass
        raise errors[0] if errors else VoiceLoopError("Voice stream cancelled.")

    def receive(queue):
        while not stopped.is_set():
            try:
                return queue.get(timeout=0.05)
            except Empty:
                pass
        return finish

    def prepare_audio(audio, number, text):
        if number == 1 and config.audio_backend != "alsa":
            audio = prepend_leading_silence(audio, config.leading_silence_seconds)
        playback_path = temp_path / f"stream-playback-{number:03d}.wav"
        playback_path.write_bytes(audio)
        if config.audio_backend == "alsa":
            source_pcm = temp_path / f"stream-speaker-source-{number:03d}.pcm"
            speaker_path = temp_path / f"stream-speaker-{number:03d}.pcm"
            prepare_alsa_playback(playback_path, source_pcm, config.playback_gain, cancel_event=stopped, raw_pcm=True)
            leading, trailing = trim_pcm_edges(source_pcm, speaker_path, trim_leading=number > 1, cancel_event=stopped)
            return PreparedAudio(number, speaker_path, text, leading, trailing)
        return PreparedAudio(number, playback_path, text)

    def preparation_worker():
        number = 0
        try:
            while not stopped.is_set():
                text = receive(pending)
                if stopped.is_set():
                    return
                if text is finish:
                    enqueue(ready_audio, finish)
                    return
                number += 1
                if number == 1:
                    timings["first_tts_chars"] = len(text)
                try:
                    synthesize = (
                        tts_provider.synthesize_cancellable
                        if isinstance(tts_provider, TextToSpeechProvider) else tts_provider.synthesize
                    )
                    args = (text, stopped) if isinstance(tts_provider, TextToSpeechProvider) else (text,)
                    audio = _timed_call(timings, "tts", synthesize, *args)
                except Exception as exc:
                    raise VoiceLoopError(f"TTS failed on streaming chunk {number}: {exc}") from exc
                if stopped.is_set():
                    return
                try:
                    prepared = _timed_call(timings, "audio_prepare", prepare_audio, audio, number, text)
                except Exception as exc:
                    raise VoiceLoopError(f"Audio preparation failed on streaming chunk {number}: {exc}") from exc
                if number == 1:
                    timings["first_audio_ready"] = time.perf_counter() - stream_started
                enqueue(ready_audio, prepared)
        except BaseException as exc:
            fail(exc)

    def playback_worker():
        nonlocal spoken_count
        player = None
        playback_started = None
        previous_trailing_frames = 0
        timings.update(playback_chunks=0, playback_starvation_ms=0.0, max_playback_starvation_ms=0.0)
        try:
            while not stopped.is_set():
                # An already-ready item costs zero starvation. Time only a real
                # queue miss; do not include PCM pauses or the final EOF marker.
                wait_started = None
                try:
                    prepared = ready_audio.get_nowait()
                except Empty:
                    if spoken_count:
                        wait_started = time.perf_counter()
                    while not stopped.is_set():
                        if player is not None:
                            player.check()
                        try:
                            prepared = ready_audio.get(timeout=0.05)
                            break
                        except Empty:
                            pass
                    else:
                        return
                if prepared is finish or stopped.is_set():
                    if player is not None and not stopped.is_set():
                        player.finish()
                    return
                if wait_started is not None:
                    starvation = (time.perf_counter() - wait_started) * 1000
                    timings["playback_starvation_ms"] += starvation
                    timings["max_playback_starvation_ms"] = max(timings["max_playback_starvation_ms"], starvation)
                if prepared.number == 1:
                    if on_playback_start is not None:
                        try:
                            on_playback_start(prepared.speech_text)
                        except Exception:
                            logger.warning("Companion display playback update failed; audio continues")
                    playback_started = time.perf_counter()
                    timings["first_speech"] = playback_started - cycle_started
                    timings["ai_to_first_speech"] = playback_started - stream_started
                try:
                    if config.audio_backend == "alsa":
                        if player is None:
                            player = PersistentAlsaPlayer(config.alsa_playback_device, stopped, temp_path)
                            player.start()
                            player.write_silence(round(config.leading_silence_seconds * ALSA_SAMPLE_RATE))
                        else:
                            gap_frames = max(0, round(config.stream_inter_chunk_silence_seconds * ALSA_SAMPLE_RATE)
                                             - previous_trailing_frames - prepared.leading_silence_frames)
                            player.write_silence(gap_frames)
                        player.write_file(prepared.path)
                        previous_trailing_frames = prepared.trailing_silence_frames
                    else:
                        _timed_call(timings, "playback", play_audio, prepared.path, cancel_event=stopped)
                except Exception as exc:
                    raise VoiceLoopError(f"Playback failed on streaming chunk {prepared.number}: {exc}") from exc
                spoken_count += 1
                timings["playback_chunks"] = spoken_count
        except BaseException as exc:
            fail(exc)
        finally:
            if player is not None:
                try:
                    player.abort()
                except Exception as exc:
                    fail(exc)
                finally:
                    timings["playback"] = time.perf_counter() - playback_started

    def present(text, *, final=False):
        nonlocal terminal_open
        answer, notices = notice_filter.feed(text, final=final)
        for notice in notices:
            if notice not in printed_notices:
                print(f"Status: {notice}", flush=True)
                printed_notices.add(notice)
        if answer:
            if "ai_ttft" not in timings and answer.strip():
                timings["ai_ttft"] = time.perf_counter() - stream_started
            if not terminal_open:
                print("Assistant: ", end="", flush=True)
                terminal_open = True
            print(answer, end="", flush=True)
        complete_sentence_played = (buffer.first_complete_unit is not None
                                    and spoken_count >= buffer.first_complete_unit)
        for unit in buffer.feed(answer, final=final, omit_incomplete_tail=final and complete_sentence_played):
            first_ready = time.perf_counter() - stream_started if "first_tts_text_ready" not in timings else None
            if first_ready is not None:
                timings["first_tts_text_ready"] = first_ready
                timings["first_tts_unit_ready"] = first_ready
            enqueue(pending, unit)

    def finish_terminal_line():
        nonlocal terminal_open
        if terminal_open:
            terminal_open = False
            print(flush=True)

    iterator = iter(events)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="voice-stream-audio")
    # Two long-lived jobs, never a Future/process per delta or concurrent Piper jobs.
    workers = []
    done = None
    start = None
    canonical_parts = []
    try:
        workers.append(executor.submit(preparation_worker))
        workers.append(executor.submit(playback_worker))
        for event in iterator:
            if stopped.is_set():
                raise errors[0] if errors else VoiceLoopError("Voice stream cancelled.")
            if not isinstance(event, dict):
                raise VoiceLoopError("AI response failed: malformed stream event.")
            kind = event.get("type")
            if kind == "error":
                raise VoiceLoopError(f"AI response failed: {event.get('detail') or event.get('error') or 'stream interrupted.'}")
            if kind == "start":
                if start is not None:
                    raise VoiceLoopError("AI response failed: duplicate stream start.")
                start = event
            elif kind == "delta":
                text = event.get("text", "")
                if not isinstance(text, str):
                    raise VoiceLoopError("AI response failed: malformed text delta.")
                if not text:
                    continue
                canonical_parts.append(text)
                present(text)
            elif kind == "done":
                done = event
                if event.get("response") != "".join(canonical_parts):
                    raise VoiceLoopError("AI response failed: stream text did not match its final response.")
                timings["ai_total"] = time.perf_counter() - stream_started
                timings["ai"] = timings["ai_total"]  # Retain the existing metric.
                if isinstance(done.get("num_predict"), int):
                    timings["num_predict"] = done["num_predict"]
                present("", final=True)
                finish_terminal_line()
                break
            else:
                raise VoiceLoopError("AI response failed: unexpected stream event.")
        if done is None:
            raise VoiceLoopError("AI response failed: stream ended before completion.")
        enqueue(pending, finish)
        for worker in workers:
            while not worker.done():
                if errors:
                    raise errors[0]
                try:
                    worker.result(timeout=0.05)
                except TimeoutError:
                    pass
        if errors:
            raise errors[0]
        if not spoken_count:
            raise VoiceLoopError("TTS failed: AI response contained no speakable text.")
        print(f"TTS chunks: {spoken_count}")
        return done.get("conversation_id") or (start or {}).get("conversation_id"), done
    except (Exception, KeyboardInterrupt):
        stopped.set()
        raise
    finally:
        stopped.set()
        try:
            try:
                finish_terminal_line()
            except OSError:
                pass  # A closed terminal must not prevent process/worker cleanup.
            finally:
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()
        finally:
            executor.shutdown(wait=True, cancel_futures=True)


def is_no_speech_transcription(text: str) -> bool:
    """Return whether an STT result is a known placeholder rather than speech."""
    normalized = text.strip().lower()
    normalized = re.sub(r"[\[\](){}<>]", " ", normalized)
    normalized = re.sub(r"[_-]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized in {"blank audio", "no speech", "silence", "inaudible"}


def _timed_call(timings: dict[str, float], stage: str, operation, *args, **kwargs):
    started = time.perf_counter()
    try:
        return operation(*args, **kwargs)
    finally:
        elapsed = time.perf_counter() - started
        timings[stage] = timings.get(stage, 0.0) + elapsed


def _print_timing_summary(timings: dict[str, float]) -> None:
    order = ("record", "normalize", "stt", "ai", "ai_ttft", "first_tts_text_ready", "first_tts_unit_ready", "first_audio_ready", "ai_to_first_speech", "first_speech",
             "ai_total", "tts", "audio_prepare", "playback", "total")
    summary = " ".join(f"{stage}={timings[stage]:.3f}s" for stage in order if stage in timings)
    for metric in ("first_tts_chars", "playback_chunks", "playback_starvation_ms", "max_playback_starvation_ms", "num_predict"):
        if metric in timings:
            summary += f" {metric}={timings[metric]:.3f}" if metric.endswith("_ms") else f" {metric}={timings[metric]}"
    print(f"Timing: {summary}")


def _run_audio_subprocess(command, *, timeout, cancel_event=None):
    if cancel_event is None:
        return subprocess.run(command, capture_output=True, timeout=timeout)
    return run_cancellable(command, timeout=timeout, cancel_event=cancel_event)


def play_audio(audio_path: Path, *, cancel_event=None) -> None:
    """Play a WAV through the current PulseAudio/PipeWire default sink."""
    try:
        result = _run_audio_subprocess(
            ["paplay", str(audio_path)], timeout=300, cancel_event=cancel_event,
        )
    except FileNotFoundError as exc:
        raise VoiceLoopError("Playback failed: paplay was not found on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise VoiceLoopError("Playback failed: paplay timed out.") from exc
    except OSError as exc:
        raise VoiceLoopError(f"Playback failed: {exc}") from exc

    if result.returncode != 0:
        detail = _stderr_text(result.stderr)
        raise VoiceLoopError(
            f"Playback failed (paplay exit {result.returncode})"
            + (f": {detail}" if detail else ".")
        )


def prepare_alsa_playback(input_path: Path, output_path: Path, gain: float, *, cancel_event=None, raw_pcm=False) -> None:
    """Convert Piper WAV to native speaker WAV (batch) or headerless PCM (stream)."""
    command = ["ffmpeg", "-y", "-i", str(input_path), "-af",
               f"volume={gain},alimiter=limit=0.98:level=false",
               "-ar", "48000", "-ac", "2", "-c:a", "pcm_s32le",
               *(["-f", "s32le"] if raw_pcm else []), str(output_path)]
    try:
        result = _run_audio_subprocess(command, timeout=60, cancel_event=cancel_event)
    except FileNotFoundError as exc:
        raise VoiceLoopError("Speaker conversion failed: ffmpeg was not found on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise VoiceLoopError("Speaker conversion failed: ffmpeg timed out.") from exc
    except OSError as exc:
        raise VoiceLoopError(f"Speaker conversion failed: {exc}") from exc
    if result.returncode != 0:
        raise VoiceLoopError(
            f"Speaker conversion failed (ffmpeg exit {result.returncode}): {_stderr_text(result.stderr)}"
        )
    if not output_path.exists() or output_path.stat().st_size == 0:
        kind = "PCM" if raw_pcm else "WAV"
        raise VoiceLoopError(f"Speaker conversion failed: ffmpeg produced no {kind} file.")


def play_audio_alsa(audio_path: Path, device: str, *, cancel_event=None) -> None:
    try:
        result = _run_audio_subprocess(
            ["aplay", "-D", device, str(audio_path)], timeout=300, cancel_event=cancel_event,
        )
    except FileNotFoundError as exc:
        raise VoiceLoopError("Playback failed: aplay was not found on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise VoiceLoopError("Playback failed: aplay timed out.") from exc
    except OSError as exc:
        raise VoiceLoopError(f"Playback failed: {exc}") from exc
    if result.returncode != 0:
        raise VoiceLoopError(f"Playback failed (aplay exit {result.returncode}): {_stderr_text(result.stderr)}")


def speak_speech_chunks(
    speech_chunks: list[str],
    tts_provider,
    temp_path: Path,
    leading_silence_seconds: float,
    timings: dict[str, float],
    on_playback_start: Callable[[], None] | None = None,
    *, audio_config: VoiceLoopConfig | None = None,
) -> None:
    """Pipeline one future synthesis while playing the current chunk."""
    chunk_count = len(speech_chunks)

    def synthesize(chunk_number: int) -> bytes:
        try:
            return _timed_call(
                timings,
                "tts",
                tts_provider.synthesize,
                speech_chunks[chunk_number - 1],
            )
        except Exception as exc:
            raise VoiceLoopError(
                f"TTS failed on chunk {chunk_number}/{chunk_count}: {exc}"
            ) from exc

    current_speech = synthesize(1)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice-tts")
    next_synthesis: Future | None = None
    try:
        for chunk_number in range(1, chunk_count + 1):
            if chunk_number < chunk_count:
                next_synthesis = executor.submit(synthesize, chunk_number + 1)
            else:
                next_synthesis = None

            playback_audio = (
                prepend_leading_silence(
                    current_speech,
                    leading_silence_seconds,
                )
                if chunk_number == 1
                else current_speech
            )
            playback_path = temp_path / f"playback-{chunk_number:03d}.wav"
            try:
                playback_path.write_bytes(playback_audio)
            except OSError as exc:
                raise VoiceLoopError(
                    f"TTS failed on chunk {chunk_number}/{chunk_count}: "
                    f"could not save playback WAV ({exc})."
                ) from exc
            try:
                playback_operation = play_audio
                playback_args = (playback_path,)
                if audio_config is not None and audio_config.audio_backend == "alsa":
                    speaker_path = temp_path / f"speaker-{chunk_number:03d}.wav"
                    _timed_call(
                        timings, "audio_prepare", prepare_alsa_playback, playback_path,
                        speaker_path, audio_config.playback_gain,
                    )
                    playback_operation = play_audio_alsa
                    playback_args = (speaker_path, audio_config.alsa_playback_device)
                if chunk_number == 1 and on_playback_start is not None:
                    try:
                        on_playback_start()
                    except Exception:
                        logger.warning("Companion display playback update failed; audio continues")
                _timed_call(timings, "playback", playback_operation, *playback_args)
            except Exception as exc:
                raise VoiceLoopError(
                    f"Playback failed on chunk {chunk_number}/{chunk_count}: {exc}"
                ) from exc

            if next_synthesis is not None:
                current_speech = next_synthesis.result()
    finally:
        if next_synthesis is not None:
            next_synthesis.cancel()
        executor.shutdown(wait=True, cancel_futures=True)


def run_voice_cycle(
    config: VoiceLoopConfig,
    conversation_id: int | None = None,
    display=None,
    *,
    audio_file: Path | None = None,
    text: str | None = None,
    hands_free_capture: HandsFreeCaptureConfig | None = None,
) -> int | None:
    """Run a cycle; hardware display failures are isolated from voice failures."""
    try:
        return _run_voice_cycle(
            config, conversation_id, display, audio_file=audio_file, text=text,
            hands_free_capture=hands_free_capture,
        )
    except NoSpeechDetected:
        _set_display_state(display, DisplayState.READY)
        raise
    except (Exception, KeyboardInterrupt):
        _set_display_state(display, DisplayState.ERROR)
        try:
            close_tts_provider()
        except Exception:
            logger.warning("Piper cleanup after voice failure failed")
        raise


def _run_voice_cycle(config, conversation_id, display, *, audio_file=None, text=None,
                     hands_free_capture=None):
    """Select an input, then reuse the production respond/speak cycle."""
    if audio_file is not None and text is not None:
        raise VoiceLoopError("Choose either audio-file or text input, not both.")
    hands_free_request = hands_free_capture is not None and audio_file is None and text is None
    cycle_started = time.perf_counter()
    timings = {}
    with tempfile.TemporaryDirectory(prefix="smart-ai-voice-") as temp_dir:
        temp_path = Path(temp_dir)
        recorded_path = temp_path / "recorded.wav"
        normalized_path = temp_path / "normalized.wav"

        if text is not None:
            transcript = text.strip()
            if not transcript:
                raise VoiceLoopError("Injected text must not be empty.")
            print(f"Injected text: {transcript}")
        else:
            if audio_file is not None:
                recorded_path = Path(audio_file).expanduser()
                if not recorded_path.is_file():
                    raise VoiceLoopError(f"Input audio is not a file: {recorded_path}")
                print(f"Input audio: {recorded_path}")
            else:
                if not hands_free_request:
                    _set_display_state(display, DisplayState.LISTENING)
                source = config.alsa_capture_device if config.audio_backend == "alsa" else config.input_source
                if hands_free_request:
                    _timed_call(
                        timings, "record", record_audio_hands_free, recorded_path,
                        config.alsa_capture_device, hands_free_capture,
                        on_capture_ready=lambda: _set_display_state(display, DisplayState.LISTENING),
                    )
                    _set_display_state(display, DisplayState.THINKING)
                elif config.capture_vad_enabled:
                    print(
                        f"Preparing microphone ({config.input_warmup_seconds:g}s warm-up, "
                        "then speech-driven capture)..."
                    )
                    _timed_call(
                        timings,
                        "record",
                        record_audio_with_capture_vad,
                        recorded_path,
                        source,
                        config.input_warmup_seconds,
                        config.capture_vad_start_threshold,
                        config.capture_vad_silence_seconds,
                        config.capture_vad_max_seconds,
                        config.capture_vad_start_timeout_seconds,
                        adaptive=config.capture_vad_adaptive,
                        noise_calibration_seconds=config.capture_vad_noise_calibration_seconds,
                        start_confirm_ms=config.capture_vad_start_confirm_ms,
                        min_speech_ms=config.capture_vad_min_speech_ms,
                        **({"backend": "alsa"} if config.audio_backend == "alsa" else {}),
                    )
                else:
                    print(
                        f"Preparing microphone ({config.input_warmup_seconds:g}s warm-up, "
                        f"then {config.record_seconds:g}s recording)..."
                    )
                    _timed_call(
                        timings,
                        "record",
                        record_audio_alsa if config.audio_backend == "alsa" else record_audio,
                        recorded_path,
                        source,
                        config.record_seconds,
                        config.input_warmup_seconds,
                    )
            _timed_call(
                timings,
                "normalize",
                normalize_audio,
                recorded_path,
                normalized_path,
                **({"left_channel": True} if config.audio_backend == "alsa" and audio_file is None else {}),
            )
            try:
                transcript = _timed_call(
                    timings,
                    "stt",
                    get_stt_provider().transcribe,
                    str(normalized_path),
                ).strip()
            except Exception as exc:
                raise VoiceLoopError(f"Transcription failed: {exc}") from exc
            if not transcript or is_no_speech_transcription(transcript):
                if hands_free_request:
                    raise NoSpeechDetected("No request speech recognized; returning to wake listening.")
                raise VoiceLoopError("Transcription failed: no speech was recognized.")
            print(f"You: {transcript}")
        response_plan = AssistantResponsePolicy.plan_voice_response(transcript)

        if not hands_free_request:
            _set_display_state(display, DisplayState.THINKING)

        def on_playback_start(first_speech_text):
            _set_display_state(display, DisplayState.SPEAKING)
            _set_display_text(display, first_speech_text)

        try:
            events = AssistantService.process_message_stream(
                transcript, conversation_id=conversation_id, response_plan=response_plan,
            )
            returned_id, metadata = speak_stream_response(
                events, get_tts_provider(), temp_path, config, timings, cycle_started,
                on_playback_start=on_playback_start,
            )
        except VoiceLoopError:
            raise
        except Exception as exc:
            raise VoiceLoopError(f"AI response failed: {exc}") from exc
        next_conversation_id = returned_id if returned_id is not None else conversation_id

    timings["total"] = time.perf_counter() - cycle_started
    _print_timing_summary(timings)
    profile = (metadata or {}).get("resource_profile")
    idle = {"ECO": DisplayState.ECO, "PROTECTIVE": DisplayState.PROTECTIVE}.get(profile, DisplayState.READY)
    _set_display_state(display, idle)
    return next_conversation_id


def run_hands_free(config, wake_config, provider, display, *, capture_config=None):
    """Serialize wake capture, the complete production cycle and cooldown.

    The provider releases arecord before returning. run_voice_cycle returns
    only after playback drains (or its failure cleanup finishes). No detector
    thread exists to hear request capture, inference, TTS or speaker output.
    """
    conversation_id = None
    capture_config = capture_config if capture_config is not None else HandsFreeCaptureConfig()
    capture_config.validate()
    print("Wake word:")
    print(f"  engine={wake_config.engine}")
    print(f"  model={wake_config.selected_model}")
    print(f"  threshold={wake_config.threshold:.2f}")
    provider.warm()
    _set_display_state(display, DisplayState.READY)
    while True:
        try:
            detection = provider.wait_for_wake_word(config.alsa_capture_device)
            if not detection.detected:
                provider.reset()
                # A tiny configured timeout must not create a rapid reopen loop.
                time.sleep(max(0.1, wake_config.cooldown_seconds))
                continue
            print(f"Wake detected: model={detection.model} score={detection.score:.3f} latency={detection.elapsed_seconds:.3f}s")
            _set_display_state(display, DisplayState.READY)
            conversation_id = run_voice_cycle(
                config, conversation_id, display=display,
                hands_free_capture=capture_config,
            )
            time.sleep(wake_config.cooldown_seconds)
            provider.reset()
        except KeyboardInterrupt:
            raise
        except NoSpeechDetected as exc:
            _set_display_state(display, DisplayState.READY)
            print(str(exc))
            time.sleep(max(0.1, wake_config.cooldown_seconds))
            provider.reset()
        except Exception as exc:
            _set_display_state(display, DisplayState.ERROR)
            print(f"Hands-free cycle error: {exc}", file=sys.stderr)
            time.sleep(max(0.5, wake_config.cooldown_seconds))
            provider.reset()
            _set_display_state(display, DisplayState.READY)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Production voice loop with optional development input injection.")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--audio-file", type=Path, help="Run once using an audio file instead of microphone capture.")
    inputs.add_argument("--text", help="Run once using text, bypassing capture, normalization and STT.")
    inputs.add_argument("--text-mode", action="store_true", help="Interactively enter text while retaining the conversation.")
    inputs.add_argument("--hands-free", action="store_true", help="Continuously wait for the offline wake word, then retain a bounded microphone request (requires enabled wake word and ALSA).")
    args = parser.parse_args(argv)
    if args.text is not None and not args.text.strip():
        parser.error("--text must not be empty")
    single_input = args.audio_file is not None or args.text is not None
    wake_config = None
    hands_free_capture = None
    try:
        config = (
            VoiceLoopConfig.from_settings(require_capture=False) if single_input or args.text_mode
            else VoiceLoopConfig.from_settings()
        )
        if args.hands_free:
            wake_config = WakeWordConfig.from_settings()
            if not wake_config.enabled:
                raise WakeWordError("--hands-free requires VOICE_WAKE_WORD_ENABLED=true. Omit the flag for Enter-to-record mode.")
            if config.audio_backend != "alsa":
                raise WakeWordError("--hands-free requires VOICE_AUDIO_BACKEND=alsa.")
            hands_free_capture = HandsFreeCaptureConfig.from_settings()
    except (TypeError, ValueError, VoiceLoopError, WakeWordError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    print("Smart AI Companion voice loop")
    print(f"Audio backend: {config.audio_backend}")
    if not single_input and not args.text_mode:
        print(f"Input source: {config.alsa_capture_device if config.audio_backend == 'alsa' else config.input_source}")
        if not args.hands_free:
            print("Press Enter to record, or type q and press Enter to quit.")
    conversation_id = None
    display = get_companion_display()
    tts_provider = None
    wake_provider = None
    try:
        _set_display_state(display, DisplayState.READY)
        try:
            tts_provider = get_tts_provider()
            print("Warming configured speech provider...")
            tts_provider.warm()
        except KeyboardInterrupt:
            _set_display_state(display, DisplayState.ERROR)
            print("TTS warm-up cancelled.", file=sys.stderr)
            return 130
        except Exception as exc:
            _set_display_state(display, DisplayState.ERROR)
            print(f"TTS warm-up failed: {exc}", file=sys.stderr)
            return 1
        if args.hands_free:
            try:
                wake_provider = get_wake_word_provider(wake_config)
                run_hands_free(
                    config, wake_config, wake_provider, display,
                    capture_config=hands_free_capture,
                )
            except KeyboardInterrupt:
                print("\nExiting hands-free voice loop.")
                return 130
            except Exception as exc:
                _set_display_state(display, DisplayState.ERROR)
                print(f"Hands-free startup/runtime failed: {exc}", file=sys.stderr)
                return 1
        while True:
            injection = {}
            if single_input:
                injection = {"audio_file": args.audio_file} if args.audio_file is not None else {"text": args.text}
            else:
                try:
                    command = input("[text, q=quit] > " if args.text_mode else "\n[Enter=record, q=quit] > ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nExiting voice loop.")
                    return 0
                if command.lower() == "q":
                    print("Exiting voice loop.")
                    return 0
                if args.text_mode:
                    if not command:
                        continue
                    injection = {"text": command}
                elif command:
                    print("Type q to quit, or press Enter to start recording.")
                    continue

            exit_code = 0
            try:
                conversation_id = run_voice_cycle(config, conversation_id, display=display, **injection)
            except VoiceLoopError as exc:
                print(f"Voice cycle error: {exc}", file=sys.stderr)
                tts_provider.close()
                exit_code = 1
            except KeyboardInterrupt:
                print("\nVoice cycle cancelled.", file=sys.stderr)
                tts_provider.close()
                exit_code = 130
            except Exception as exc:
                print(f"Voice cycle error: unexpected failure ({exc}).", file=sys.stderr)
                tts_provider.close()
                exit_code = 1
            if single_input:
                return exit_code
    finally:
        try:
            if wake_provider is not None:
                wake_provider.close()
        except Exception:
            logger.warning("Wake provider cleanup failed")
        finally:
            try:
                if tts_provider is not None:
                    tts_provider.close()
            except Exception:
                logger.warning("Speech provider cleanup failed")
            finally:
                try:
                    display.close()
                except Exception:
                    logger.warning("Companion display cleanup failed")


if __name__ == "__main__":
    raise SystemExit(main())
