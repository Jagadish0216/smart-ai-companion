"""
Speech-to-Text Providers.

Defines the base interface for transcription and implements a local provider
using whisper.cpp.
"""

import logging
import subprocess
import os

logger = logging.getLogger(__name__)

class STTError(Exception):
    """Raised when Speech-to-Text transcription fails."""
    pass

class SpeechToTextProvider:
    """Base interface for STT engines."""

    def transcribe(self, audio_file_path: str) -> str:
        """
        Transcribe an audio file to text.

        Args:
            audio_file_path: Absolute path to a safe, valid 16kHz WAV file.

        Returns:
            The transcribed text.

        Raises:
            STTError: If transcription fails.
        """
        raise NotImplementedError

class WhisperCppProvider(SpeechToTextProvider):
    """
    Executes a local whisper.cpp binary for offline STT.
    """
    def __init__(
        self,
        binary_path: str,
        model_path: str,
        timeout: int = 30,
        vad_enabled: bool = False,
        vad_model_path: str = "",
        vad_threshold: float = 0.5,
        vad_min_speech_ms: int = 250,
        vad_min_silence_ms: int = 700,
        vad_speech_pad_ms: int = 100,
    ):
        self.binary_path = binary_path
        self.model_path = model_path
        self.timeout = timeout
        self.vad_enabled = vad_enabled
        self.vad_model_path = vad_model_path
        self.vad_threshold = vad_threshold
        self.vad_min_speech_ms = vad_min_speech_ms
        self.vad_min_silence_ms = vad_min_silence_ms
        self.vad_speech_pad_ms = vad_speech_pad_ms

    def transcribe(self, audio_file_path: str) -> str:
        if not os.path.exists(self.binary_path):
            raise STTError(f"whisper.cpp binary not found at: {self.binary_path}")
        if not os.path.exists(self.model_path):
            raise STTError(f"whisper.cpp model not found at: {self.model_path}")
        if self.vad_enabled:
            if not self.vad_model_path:
                raise STTError(
                    "whisper.cpp VAD is enabled but STT_VAD_MODEL is not configured."
                )
            if not os.path.exists(self.vad_model_path):
                raise STTError(
                    f"whisper.cpp VAD model not found at: {self.vad_model_path}"
                )
        if not os.path.exists(audio_file_path):
            raise STTError(f"Audio file not found: {audio_file_path}")

        # Usage: ./main -m models/ggml-base.en.bin -f audio.wav -nt
        # -nt avoids printing timestamps to stdout
        cmd = [
            self.binary_path,
            "-m", self.model_path,
            "-f", audio_file_path,
            "-nt"
        ]

        if self.vad_enabled:
            cmd.extend([
                "--vad",
                "--vad-model", self.vad_model_path,
                "--vad-threshold", str(self.vad_threshold),
                "--vad-min-speech-duration-ms", str(self.vad_min_speech_ms),
                "--vad-min-silence-duration-ms", str(self.vad_min_silence_ms),
                "--vad-speech-pad-ms", str(self.vad_speech_pad_ms),
            ])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )

            if result.returncode != 0:
                logger.error("whisper.cpp failed. stderr: %s", result.stderr)
                raise STTError("whisper.cpp transcription process failed.")

            # whisper.cpp prints the transcript to stdout.
            # Clean up potential leading/trailing spaces and newlines.
            transcript = result.stdout.strip()

            # Remove any stray blank lines
            transcript = " ".join(line.strip() for line in transcript.splitlines() if line.strip())

            if not transcript:
                raise STTError("Transcription returned empty result.")

            return transcript

        except subprocess.TimeoutExpired as exc:
            logger.error("whisper.cpp timed out after %s seconds.", self.timeout)
            raise STTError("Transcription timed out.") from exc
        except subprocess.SubprocessError as exc:
            logger.exception("whisper.cpp subprocess error.")
            raise STTError("Transcription process error.") from exc

class MockSTTProvider(SpeechToTextProvider):
    """A mock STT provider for tests and development without binaries."""
    def transcribe(self, audio_file_path: str) -> str:
        return "This is a simulated voice transcription from the mock provider."
