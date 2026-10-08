"""
Text-to-Speech Providers.

Defines the base interface for speech synthesis and implements a local provider
using Piper.
"""

import logging
import subprocess
import os
from .processes import AudioCancelledError, run_cancellable

logger = logging.getLogger(__name__)

class TTSError(Exception):
    """Raised when Text-to-Speech synthesis fails."""
    pass

class TextToSpeechProvider:
    """Base interface for TTS engines."""

    def synthesize(self, text: str) -> bytes:
        """
        Synthesize text to audio.

        Args:
            text: The text to synthesize.

        Returns:
            The synthesized audio as raw WAV bytes.

        Raises:
            TTSError: If synthesis fails.
        """
        raise NotImplementedError

    def synthesize_cancellable(self, text, cancel_event):
        if cancel_event.is_set():
            raise TTSError("TTS synthesis cancelled.")
        return self.synthesize(text)


class PiperProvider(TextToSpeechProvider):
    """
    Executes a local piper binary for offline TTS.
    """
    def __init__(self, binary_path: str, voice_path: str, timeout: int = 30):
        self.binary_path = binary_path
        self.voice_path = voice_path
        self.timeout = timeout

    def synthesize(self, text: str) -> bytes:
        return self._synthesize(text)

    def synthesize_cancellable(self, text, cancel_event):
        return self._synthesize(text, cancel_event=cancel_event)

    def _synthesize(self, text, cancel_event=None):
        if not text or not text.strip():
            raise TTSError("Cannot synthesize empty text.")
        if not os.path.exists(self.binary_path):
            raise TTSError(f"Piper binary not found at: {self.binary_path}")
        if not os.path.exists(self.voice_path):
            raise TTSError(f"Piper voice model not found at: {self.voice_path}")

        # Usage: echo "Text" | ./piper -m voice.onnx -f -
        # The '-f -' argument tells Piper to write WAV output to stdout
        cmd = [
            self.binary_path,
            "-m", self.voice_path,
            "-f", "-"
        ]

        try:
            # We pipe the text to stdin and read WAV from stdout
            if cancel_event is None:
                result = subprocess.run(
                    cmd, input=text.encode("utf-8"), capture_output=True, timeout=self.timeout,
                )
            else:
                result = run_cancellable(
                    cmd, input=text.encode("utf-8"), timeout=self.timeout, cancel_event=cancel_event,
                )

            if result.returncode != 0:
                logger.error("Piper TTS failed. stderr: %s", result.stderr.decode("utf-8", errors="ignore"))
                raise TTSError("Piper TTS process failed.")

            wav_bytes = result.stdout

            if not wav_bytes:
                raise TTSError("Piper returned empty audio.")

            return wav_bytes

        except AudioCancelledError as exc:
            raise TTSError("TTS synthesis cancelled.") from exc
        except subprocess.TimeoutExpired as exc:
            logger.error("Piper TTS timed out after %s seconds.", self.timeout)
            raise TTSError("TTS synthesis timed out.") from exc
        except subprocess.SubprocessError as exc:
            logger.exception("Piper TTS subprocess error.")
            raise TTSError("TTS synthesis process error.") from exc


class MockTTSProvider(TextToSpeechProvider):
    """A mock TTS provider for tests and development without binaries."""
    def synthesize(self, text: str) -> bytes:
        # Return a minimal valid WAV header for a 1-sample 16kHz mono audio file
        # RIFF(44)WAVEfmt(16)data(2)
        import struct
        header = b'RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x02\x00\x00\x00'
        data = struct.pack('<h', 0)
        return header + data
