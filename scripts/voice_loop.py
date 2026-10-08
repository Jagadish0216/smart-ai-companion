#!/usr/bin/env python3
"""Standalone push-to-talk voice loop for the Raspberry Pi."""

from __future__ import annotations

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
from assistant.voice.factory import get_stt_provider, get_tts_provider  # noqa: E402
from assistant.voice.processes import run_cancellable  # noqa: E402
from assistant.voice.tts import TextToSpeechProvider  # noqa: E402

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


class VoiceLoopError(Exception):
    """Raised when one stage of a voice cycle cannot complete."""


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

    @classmethod
    def from_settings(cls) -> "VoiceLoopConfig":
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
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.audio_backend not in {"pulse", "alsa"}:
            raise VoiceLoopError("VOICE_AUDIO_BACKEND must be pulse or alsa.")
        if self.audio_backend == "alsa" and (
            not self.alsa_capture_device or not self.alsa_playback_device
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
        if self.audio_backend == "pulse" and not self.input_source:
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
    *, backend: str = "pulse",
) -> None:
    """Record raw PCM until speech ends, retaining a short pre-roll buffer."""
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
    wait_elapsed = 0.0
    capture_elapsed = 0.0
    silence_elapsed = 0.0
    speech_started = False
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
        print("Speak now...")
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
            has_speech = rms >= start_threshold

            if not speech_started:
                preroll.append(chunk)
                wait_elapsed += chunk_duration
                if has_speech:
                    speech_started = True
                    frames.extend(preroll)
                    preroll.clear()
                    capture_elapsed = chunk_duration
                elif wait_elapsed >= start_timeout_seconds:
                    raise VoiceLoopError(
                        "Recording stopped: no speech detected before the start timeout."
                    )
                continue

            frames.append(chunk)
            capture_elapsed += chunk_duration
            if has_speech:
                silence_elapsed = 0.0
            else:
                silence_elapsed += chunk_duration

            if silence_elapsed >= silence_seconds:
                break
            if capture_elapsed >= max_seconds:
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

    def __init__(self, target_chars=180):
        self.target_chars = target_chars
        self.pending = ""

    def feed(self, text, *, final=False):
        self.pending += text
        units = []
        while self.pending:
            protected = [match.span() for match in re.finditer(r"!?\[[^\]]*\]\([^)]*\)", self.pending)]
            # Do not split a Markdown link whose label/URL is still arriving.
            for match in re.finditer(r"!?\[", self.pending):
                if not any(start <= match.start() < end for start, end in protected):
                    if "]" not in self.pending[match.end():] or re.search(r"\]\([^)]*$", self.pending[match.end():]):
                        protected.append((match.start(), len(self.pending)))

            def safe(position):
                return not any(start < position < end for start, end in protected)

            boundary = None
            for match in re.finditer(r'''[.!?](?:["'’”)\]}*_`]+)?(?=\s|$)''', self.pending):
                if safe(match.start()) and safe(match.end()) and (final or match.end() < len(self.pending)):
                    boundary = match.end()
                    break
            if boundary is None and len(self.pending) > self.target_chars:
                spaces = [match.start() for match in re.finditer(r"\s+", self.pending)
                          if match.start() >= min(40, self.target_chars / 2) and safe(match.start())]
                before = [position for position in spaces if position <= self.target_chars]
                boundary = before[-1] if before else (spaces[0] if spaces else None)
            if boundary is None:
                if not final:
                    break
                boundary = len(self.pending)
            raw_unit, self.pending = self.pending[:boundary], self.pending[boundary:].lstrip()
            spoken = sanitize_text_for_speech(raw_unit)
            if spoken:
                units.extend(_split_words(spoken, self.target_chars))
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


def speak_stream_response(events, tts_provider, temp_path, config, timings, cycle_started,
                          on_playback_start=None):
    """Service producer -> bounded text -> one preparer -> bounded audio -> one player."""
    pending = Queue(maxsize=2)
    ready_audio = Queue(maxsize=2)
    stopped = Event()
    finish = object()
    errors = []
    error_lock = Lock()
    spoken_count = 0
    stream_started = time.perf_counter()
    buffer = StreamingSpeechBuffer(config.stream_tts_target_chars)
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

    def prepare_audio(audio, number):
        if number == 1:
            audio = prepend_leading_silence(audio, config.leading_silence_seconds)
        playback_path = temp_path / f"stream-playback-{number:03d}.wav"
        playback_path.write_bytes(audio)
        if config.audio_backend == "alsa":
            speaker_path = temp_path / f"stream-speaker-{number:03d}.wav"
            prepare_alsa_playback(playback_path, speaker_path, config.playback_gain, cancel_event=stopped)
            playback_path = speaker_path
        return PreparedAudio(number, playback_path)

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
                    prepared = _timed_call(timings, "audio_prepare", prepare_audio, audio, number)
                except Exception as exc:
                    raise VoiceLoopError(f"Audio preparation failed on streaming chunk {number}: {exc}") from exc
                enqueue(ready_audio, prepared)
        except BaseException as exc:
            fail(exc)

    def playback_worker():
        nonlocal spoken_count
        try:
            while not stopped.is_set():
                prepared = receive(ready_audio)
                if prepared is finish or stopped.is_set():
                    return
                if prepared.number == 1:
                    if on_playback_start is not None:
                        try:
                            on_playback_start()
                        except Exception:
                            logger.warning("Companion display playback update failed; audio continues")
                    playback_started = time.perf_counter()
                    timings["first_speech"] = playback_started - cycle_started
                    timings["ai_to_first_speech"] = playback_started - stream_started
                operation, args = play_audio, (prepared.path,)
                if config.audio_backend == "alsa":
                    operation, args = play_audio_alsa, (prepared.path, config.alsa_playback_device)
                try:
                    _timed_call(timings, "playback", operation, *args, cancel_event=stopped)
                except Exception as exc:
                    raise VoiceLoopError(f"Playback failed on streaming chunk {prepared.number}: {exc}") from exc
                spoken_count += 1
        except BaseException as exc:
            fail(exc)

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
        for unit in buffer.feed(answer, final=final):
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
    order = ("record", "normalize", "stt", "ai", "ai_ttft", "ai_to_first_speech", "first_speech",
             "ai_total", "tts", "audio_prepare", "playback", "total")
    summary = " ".join(f"{stage}={timings[stage]:.3f}s" for stage in order if stage in timings)
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


def prepare_alsa_playback(input_path: Path, output_path: Path, gain: float, *, cancel_event=None) -> None:
    """Convert a Piper chunk (including first-chunk silence) to native speaker PCM."""
    command = ["ffmpeg", "-y", "-i", str(input_path), "-af",
               f"volume={gain},alimiter=limit=0.98:level=false",
               "-ar", "48000", "-ac", "2", "-c:a", "pcm_s32le", str(output_path)]
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
        raise VoiceLoopError("Speaker conversion failed: ffmpeg produced no WAV file.")


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
) -> int | None:
    """Run a cycle; hardware display failures are isolated from voice failures."""
    try:
        return _run_voice_cycle(config, conversation_id, display)
    except (Exception, KeyboardInterrupt):
        _set_display_state(display, DisplayState.ERROR)
        raise


def _run_voice_cycle(config, conversation_id, display):
    """Run one record/transcribe/respond/speak cycle."""
    cycle_started = time.perf_counter()
    timings = {}
    with tempfile.TemporaryDirectory(prefix="smart-ai-voice-") as temp_dir:
        temp_path = Path(temp_dir)
        recorded_path = temp_path / "recorded.wav"
        normalized_path = temp_path / "normalized.wav"

        _set_display_state(display, DisplayState.LISTENING)
        source = config.alsa_capture_device if config.audio_backend == "alsa" else config.input_source
        if config.capture_vad_enabled:
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
            **({"left_channel": True} if config.audio_backend == "alsa" else {}),
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
            raise VoiceLoopError("Transcription failed: no speech was recognized.")
        print(f"You: {transcript}")
        response_plan = AssistantResponsePolicy.plan_voice_response(transcript)

        _set_display_state(display, DisplayState.THINKING)
        try:
            events = AssistantService.process_message_stream(
                transcript, conversation_id=conversation_id, response_plan=response_plan,
            )
            returned_id, metadata = speak_stream_response(
                events, get_tts_provider(), temp_path, config, timings, cycle_started,
                on_playback_start=lambda: _set_display_state(display, DisplayState.SPEAKING),
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


def main() -> int:
    try:
        config = VoiceLoopConfig.from_settings()
    except (TypeError, ValueError, VoiceLoopError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    print("Smart AI Companion voice loop")
    print(f"Audio backend: {config.audio_backend}")
    print(f"Input source: {config.alsa_capture_device if config.audio_backend == 'alsa' else config.input_source}")
    print("Press Enter to record, or type q and press Enter to quit.")
    conversation_id = None
    display = get_companion_display()
    try:
        _set_display_state(display, DisplayState.READY)
        while True:
            try:
                command = input("\n[Enter=record, q=quit] > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting voice loop.")
                return 0

            if command == "q":
                print("Exiting voice loop.")
                return 0
            if command:
                print("Type q to quit, or press Enter to start recording.")
                continue

            try:
                conversation_id = run_voice_cycle(config, conversation_id, display=display)
            except VoiceLoopError as exc:
                print(f"Voice cycle error: {exc}", file=sys.stderr)
            except KeyboardInterrupt:
                print("\nVoice cycle cancelled.", file=sys.stderr)
            except Exception as exc:
                print(f"Voice cycle error: unexpected failure ({exc}).", file=sys.stderr)
    finally:
        try:
            display.close()
        except Exception:
            logger.warning("Companion display cleanup failed")


if __name__ == "__main__":
    raise SystemExit(main())
