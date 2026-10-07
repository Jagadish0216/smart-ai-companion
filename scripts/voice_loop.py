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
import subprocess
import sys
import tempfile
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
from assistant.voice.factory import get_stt_provider, get_tts_provider  # noqa: E402

CAPTURE_SAMPLE_RATE = 16000
CAPTURE_SAMPLE_WIDTH = 2
CAPTURE_CHUNK_SECONDS = 0.02
CAPTURE_PREROLL_SECONDS = 0.3
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
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.input_source:
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

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise VoiceLoopError(
            "Recording failed: parecord was not found on PATH."
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
                f"Recording failed (parecord exit {process.returncode})"
                + (f": {detail}" if detail else ".")
            )

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise VoiceLoopError("Recording failed: parecord produced no audio file.")


def _normalized_pcm_rms(chunk: bytes) -> float:
    """Return the RMS amplitude of little-endian signed 16-bit PCM on a 0..1 scale."""
    sample_bytes = len(chunk) - (len(chunk) % CAPTURE_SAMPLE_WIDTH)
    if sample_bytes == 0:
        return 0.0
    samples = struct.iter_unpack("<h", chunk[:sample_bytes])
    square_sum = sum(sample[0] * sample[0] for sample in samples)
    sample_count = sample_bytes // CAPTURE_SAMPLE_WIDTH
    return math.sqrt(square_sum / sample_count) / 32768.0


def record_audio_with_capture_vad(
    output_path: Path,
    source: str,
    warmup_seconds: float,
    start_threshold: float,
    silence_seconds: float,
    max_seconds: float,
    start_timeout_seconds: float,
) -> None:
    """Record raw PCM until speech ends, retaining a short pre-roll buffer."""
    command = [
        "parecord",
        f"--device={source}",
        "--raw",
        "--format=s16le",
        f"--rate={CAPTURE_SAMPLE_RATE}",
        "--channels=1",
    ]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise VoiceLoopError(
            "Recording failed: parecord was not found on PATH."
        ) from exc
    except OSError as exc:
        raise VoiceLoopError(f"Recording failed: {exc}") from exc

    chunk_bytes = round(
        CAPTURE_SAMPLE_RATE
        * CAPTURE_SAMPLE_WIDTH
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

    try:
        time.sleep(warmup_seconds)
        print("Speak now...")
        if process.stdout is None:
            raise VoiceLoopError("Recording failed: parecord audio stream is unavailable.")

        while True:
            chunk = process.stdout.read(chunk_bytes)
            if not chunk:
                raise VoiceLoopError(
                    "Recording failed: parecord stopped before capture completed."
                )
            chunk_duration = (
                len(chunk) / CAPTURE_SAMPLE_WIDTH / CAPTURE_SAMPLE_RATE
            )
            has_speech = _normalized_pcm_rms(chunk) >= start_threshold

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

    try:
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(CAPTURE_SAMPLE_WIDTH)
            wav_file.setframerate(CAPTURE_SAMPLE_RATE)
            wav_file.writeframes(b"".join(frames))
    except (OSError, wave.Error) as exc:
        raise VoiceLoopError(f"Recording failed: could not save WAV ({exc}).") from exc

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise VoiceLoopError("Recording failed: parecord produced no audio file.")
    captured_audio_seconds = sum(len(frame) for frame in frames) / (
        CAPTURE_SAMPLE_RATE * CAPTURE_SAMPLE_WIDTH
    )
    print(f"Capture complete: {captured_audio_seconds:.1f}s audio.")


def normalize_audio(input_path: Path, output_path: Path) -> None:
    """Convert the recording to the mono 16 kHz PCM format whisper.cpp expects."""
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
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
    order = ("record", "normalize", "stt", "ai", "tts", "playback", "total")
    summary = " ".join(f"{stage}={timings[stage]:.1f}s" for stage in order)
    print(f"Timing: {summary}")


def play_audio(audio_path: Path) -> None:
    """Play a WAV through the current PulseAudio/PipeWire default sink."""
    try:
        result = subprocess.run(
            ["paplay", str(audio_path)],
            capture_output=True,
            timeout=300,
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


def speak_speech_chunks(
    speech_chunks: list[str],
    tts_provider,
    temp_path: Path,
    leading_silence_seconds: float,
    timings: dict[str, float],
    on_playback_start: Callable[[], None] | None = None,
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
                if chunk_number == 1 and on_playback_start is not None:
                    try:
                        on_playback_start()
                    except Exception:
                        logger.warning("Companion display playback update failed; audio continues")
                _timed_call(timings, "playback", play_audio, playback_path)
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
                config.input_source,
                config.input_warmup_seconds,
                config.capture_vad_start_threshold,
                config.capture_vad_silence_seconds,
                config.capture_vad_max_seconds,
                config.capture_vad_start_timeout_seconds,
            )
        else:
            print(
                f"Preparing microphone ({config.input_warmup_seconds:g}s warm-up, "
                f"then {config.record_seconds:g}s recording)..."
            )
            _timed_call(
                timings,
                "record",
                record_audio,
                recorded_path,
                config.input_source,
                config.record_seconds,
                config.input_warmup_seconds,
            )
        _timed_call(
            timings,
            "normalize",
            normalize_audio,
            recorded_path,
            normalized_path,
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
            conversation, response_text, metadata, error = (
                _timed_call(
                    timings,
                    "ai",
                    AssistantService.process_message,
                    transcript,
                    conversation_id=conversation_id,
                    response_plan=response_plan,
                )
            )
        except Exception as exc:
            raise VoiceLoopError(f"AI response failed: {exc}") from exc
        if error or not response_text:
            raise VoiceLoopError(f"AI response failed: {error or 'empty response.'}")
        print(f"Assistant: {response_text}")

        speech_chunks = prepare_speech_chunks(
            response_text,
            config.tts_chunk_chars,
            config.tts_max_total_chars,
        )
        if not speech_chunks:
            raise VoiceLoopError("TTS failed: AI response contained no speakable text.")
        print(f"TTS chunks: {len(speech_chunks)}")
        speak_speech_chunks(
            speech_chunks,
            get_tts_provider(),
            temp_path,
            config.leading_silence_seconds,
            timings,
            on_playback_start=lambda: _set_display_state(display, DisplayState.SPEAKING),
        )

        next_conversation_id = (
            conversation.id if conversation is not None else conversation_id
        )

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
    print(f"Input source: {config.input_source}")
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
