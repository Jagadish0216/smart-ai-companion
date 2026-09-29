#!/usr/bin/env python3
"""Standalone push-to-talk voice loop for the Raspberry Pi."""

from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import wave


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

from assistant.services import AssistantService  # noqa: E402
from assistant.voice.factory import get_stt_provider, get_tts_provider  # noqa: E402


VOICE_SYSTEM_INSTRUCTION = (
    "This is a spoken voice interaction. Answer in 2–3 short sentences by default. "
    "Use conversational spoken language. Do not use Markdown, bullet lists, or "
    "headings. Avoid long explanations unless the user explicitly asks for detail."
)


class VoiceLoopError(Exception):
    """Raised when one stage of a voice cycle cannot complete."""


@dataclass(frozen=True)
class VoiceLoopConfig:
    input_source: str
    record_seconds: float = 5.0
    leading_silence_seconds: float = 0.7
    max_speech_chars: int = 500
    input_warmup_seconds: float = 1.0

    @classmethod
    def from_settings(cls) -> "VoiceLoopConfig":
        config = cls(
            input_source=str(getattr(settings, "VOICE_INPUT_SOURCE", "")).strip(),
            record_seconds=float(getattr(settings, "VOICE_RECORD_SECONDS", 5)),
            leading_silence_seconds=float(
                getattr(settings, "VOICE_LEADING_SILENCE_SECONDS", 0.7)
            ),
            max_speech_chars=int(getattr(settings, "VOICE_MAX_SPEECH_CHARS", 500)),
            input_warmup_seconds=float(
                getattr(settings, "VOICE_INPUT_WARMUP_SECONDS", 1.0)
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
        if self.max_speech_chars <= 0:
            raise VoiceLoopError("VOICE_MAX_SPEECH_CHARS must be greater than zero.")
        if self.input_warmup_seconds < 0:
            raise VoiceLoopError("VOICE_INPUT_WARMUP_SECONDS cannot be negative.")


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


def truncate_text_for_speech(text: str, max_chars: int) -> str:
    """Limit speech text, preferring a complete sentence near the limit."""
    if len(text) <= max_chars:
        return text

    candidate = text[:max_chars]
    sentence_ends = [
        match.end()
        for match in re.finditer(r"[.!?](?:[\"')\]]+)?(?=\s|$)", candidate)
    ]
    practical_boundary = max_chars // 2
    if sentence_ends and sentence_ends[-1] >= practical_boundary:
        return candidate[:sentence_ends[-1]].strip()

    shortened = candidate.rstrip()
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0]
    shortened = shortened.rstrip(" ,;:-")
    if max_chars == 1:
        return "…"
    return shortened[:max_chars - 1].rstrip() + "…"


def prepare_text_for_speech(text: str, max_chars: int = 500) -> str:
    """Create a Markdown-free, speech-length copy of an AI response."""
    return truncate_text_for_speech(sanitize_text_for_speech(text), max_chars)


def _timed_call(timings: dict[str, float], stage: str, operation, *args, **kwargs):
    started = time.perf_counter()
    try:
        return operation(*args, **kwargs)
    finally:
        timings[stage] = time.perf_counter() - started


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


def run_voice_cycle(
    config: VoiceLoopConfig,
    conversation_id: int | None = None,
) -> int | None:
    """Run one record/transcribe/respond/speak cycle."""
    cycle_started = time.perf_counter()
    timings = {}
    with tempfile.TemporaryDirectory(prefix="smart-ai-voice-") as temp_dir:
        temp_path = Path(temp_dir)
        recorded_path = temp_path / "recorded.wav"
        normalized_path = temp_path / "normalized.wav"
        playback_path = temp_path / "playback.wav"

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
        if not transcript:
            raise VoiceLoopError("Transcription failed: no speech was recognized.")
        print(f"You: {transcript}")

        try:
            conversation, response_text, _metadata, error = (
                _timed_call(
                    timings,
                    "ai",
                    AssistantService.process_message,
                    transcript,
                    conversation_id=conversation_id,
                    system_instruction=VOICE_SYSTEM_INSTRUCTION,
                )
            )
        except Exception as exc:
            raise VoiceLoopError(f"AI response failed: {exc}") from exc
        if error or not response_text:
            raise VoiceLoopError(f"AI response failed: {error or 'empty response.'}")
        print(f"Assistant: {response_text}")

        speech_text = prepare_text_for_speech(
            response_text,
            config.max_speech_chars,
        )
        if not speech_text:
            raise VoiceLoopError("TTS failed: AI response contained no speakable text.")

        try:
            speech = _timed_call(
                timings,
                "tts",
                get_tts_provider().synthesize,
                speech_text,
            )
        except Exception as exc:
            raise VoiceLoopError(f"TTS failed: {exc}") from exc

        playback_audio = prepend_leading_silence(
            speech,
            config.leading_silence_seconds,
        )
        try:
            playback_path.write_bytes(playback_audio)
        except OSError as exc:
            raise VoiceLoopError(f"TTS failed: could not save playback WAV ({exc}).") from exc
        _timed_call(timings, "playback", play_audio, playback_path)

        next_conversation_id = (
            conversation.id if conversation is not None else conversation_id
        )

    timings["total"] = time.perf_counter() - cycle_started
    _print_timing_summary(timings)
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
            conversation_id = run_voice_cycle(config, conversation_id)
        except VoiceLoopError as exc:
            print(f"Voice cycle error: {exc}", file=sys.stderr)
        except KeyboardInterrupt:
            print("\nVoice cycle cancelled.", file=sys.stderr)
        except Exception as exc:
            print(f"Voice cycle error: unexpected failure ({exc}).", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
