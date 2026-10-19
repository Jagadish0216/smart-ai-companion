"""Speech providers and a cancellable persistent Piper worker process."""

import atexit
import base64
import io
import json
import logging
import math
import multiprocessing
import os
from queue import Empty, Queue
from threading import Event, Lock, Thread
import time
import wave
import weakref

from .piper_worker import MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, MAX_WAV_BYTES, send_message, worker_main

logger = logging.getLogger(__name__)
_providers = weakref.WeakSet()


def _close_providers_at_exit():
    for provider in list(_providers):
        try:
            provider.close()
        except Exception:
            logger.warning("Piper worker exit cleanup failed")


atexit.register(_close_providers_at_exit)


class TTSError(Exception):
    """Raised when Text-to-Speech synthesis fails."""


class TextToSpeechProvider:
    """Backends return standard WAV bytes; lifecycle hooks are optional no-ops."""

    def synthesize(self, text: str) -> bytes:
        raise NotImplementedError

    def synthesize_cancellable(self, text, cancel_event):
        if cancel_event.is_set():
            raise TTSError("TTS synthesis cancelled.")
        return self.synthesize(text)

    def warm(self):
        """Optional appliance initialization."""

    def close(self):
        """Optional deterministic cleanup."""


class PiperProvider(TextToSpeechProvider):
    """One spawn-isolated CPU voice, reused across chunks and cycles.

    binary_path remains a legacy field. The worker uses the piper-tts package
    installed in this Python environment rather than launching that binary.
    """

    def __init__(self, binary_path: str, voice_path: str, timeout: int = 30):
        self.binary_path = binary_path
        self.voice_path = voice_path
        self.timeout = timeout
        if not math.isfinite(float(timeout)) or float(timeout) <= 0:
            raise TTSError("TTS_TIMEOUT must be finite and greater than zero.")
        self._context = multiprocessing.get_context("spawn")
        self._lock = Lock()
        self._close_lock = Lock()
        self._closing = Event()
        self._serial = 0
        self._request_id = 0
        self._worker = None
        self._connection = None
        self._ready = False
        _providers.add(self)

    def synthesize(self, text: str) -> bytes:
        return self._synthesize(text)

    def synthesize_cancellable(self, text, cancel_event):
        return self._synthesize(text, cancel_event)

    def _synthesize(self, text, cancel_event=None):
        if not isinstance(text, str) or not text.strip():
            raise TTSError("Cannot synthesize empty text.")
        deadline, serial = time.monotonic() + float(self.timeout), self._serial
        try:
            self._acquire(cancel_event, deadline, serial)
        except TTSError:
            # An expired waiter must not stop another owner. Stop idle state.
            if self._lock.acquire(blocking=False):
                try:
                    self._stop_worker()
                finally:
                    self._lock.release()
            raise
        try:
            ready_started = time.perf_counter()
            worker_reused = self._ready
            self._ensure_ready(cancel_event, deadline, serial)
            ready_ms = round((time.perf_counter() - ready_started) * 1000, 2)
            self._request_id += 1
            request_id = self._request_id
            message = {"type": "synthesize", "request_id": request_id, "text": text}
            if len(json.dumps(message).encode("utf-8")) > MAX_REQUEST_BYTES:
                raise TTSError("Piper synthesis request is too large.")
            exchange_started = time.perf_counter()
            response = self._exchange(message, cancel_event, deadline, serial)
            exchange_ms = round((time.perf_counter() - exchange_started) * 1000, 2)
            self._check(cancel_event, deadline, serial)
            self._validate_error(response, request_id)
            if (set(response) != {"type", "request_id", "wav"} or response["type"] != "result"
                    or type(response["request_id"]) is not int or response["request_id"] != request_id):
                raise TTSError("Piper worker returned a malformed or stale response.")
            try:
                audio = base64.b64decode(response["wav"], validate=True)
                if not 0 < len(audio) <= MAX_WAV_BYTES:
                    raise ValueError("Invalid audio size")
                with wave.open(io.BytesIO(audio), "rb") as wav:
                    frames = wav.getnframes()
                    if frames <= 0 or len(wav.readframes(frames)) != frames * wav.getnchannels() * wav.getsampwidth():
                        raise ValueError("Incomplete WAV")
            except Exception as exc:
                raise TTSError("Piper worker returned invalid or empty WAV audio.") from exc
            self._check(cancel_event, deadline, serial)
            metrics = {
                "worker_reused": worker_reused, "ready_ms": ready_ms,
                "exchange_ms": exchange_ms, "text_chars": len(text),
                "wav_bytes": len(audio),
            }
            logger.info("Piper synthesis timings: %s", metrics,
                        extra={"piper_timings": metrics})
            return audio
        except BaseException:
            self._stop_worker()
            raise
        finally:
            self._lock.release()

    def _check(self, cancel_event, deadline, serial):
        if cancel_event is not None and cancel_event.is_set():
            raise TTSError("TTS synthesis cancelled.")
        if self._closing.is_set() or serial != self._serial:
            raise TTSError("Piper provider was closed.")
        if time.monotonic() >= deadline:
            raise TTSError("TTS synthesis timed out.")

    def _acquire(self, cancel_event, deadline, serial):
        while True:
            self._check(cancel_event, deadline, serial)
            if self._lock.acquire(timeout=min(0.05, max(0, deadline - time.monotonic()))):
                return

    def _exchange(self, message, cancel_event, deadline, serial):
        # Partial/large Connection frames must not block the cancellation owner.
        # A killed child unblocks the helper thread's send/receive operations.
        connection = self._connection
        result = Queue(maxsize=1)

        def communicate():
            try:
                if message is not None:
                    send_message(connection, message)
                payload = json.loads(connection.recv_bytes(MAX_RESPONSE_BYTES).decode("utf-8"))
                result.put((True, payload))
            except Exception:
                result.put((False, None))

        reader = Thread(target=communicate, name="piper-ipc", daemon=True)
        reader.start()
        try:
            while True:
                self._check(cancel_event, deadline, serial)
                try:
                    valid, payload = result.get(timeout=min(0.05, max(0, deadline - time.monotonic())))
                except Empty:
                    if not self._worker.is_alive():
                        raise TTSError("Piper worker exited unexpectedly.")
                    continue
                if not valid or not isinstance(payload, dict):
                    raise TTSError("Piper worker communication failed.")
                return payload
        except BaseException:
            self._stop_worker()
            raise
        finally:
            reader.join(timeout=1)
            if reader.is_alive():
                logger.error("Piper IPC thread did not stop after worker cleanup")

    @staticmethod
    def _validate_error(response, request_id):
        if response.get("type") != "error":
            return
        if (set(response) != {"type", "request_id", "code"} or response.get("request_id") != request_id
                or type(response.get("request_id")) is not type(request_id) or not isinstance(response.get("code"), str)):
            raise TTSError("Piper worker returned a malformed or stale response.")
        errors = {
            "IMPORT_FAILED": "Persistent Piper requires the piper-tts Python package in this environment.",
            "LOAD_FAILED": "Piper voice model could not be loaded.",
            "SYNTHESIS_FAILED": "Piper TTS synthesis failed.",
            "PROTOCOL_ERROR": "Piper worker protocol failed.",
        }
        raise TTSError(errors.get(response["code"], "Piper worker protocol failed."))

    def _ensure_ready(self, cancel_event, deadline, serial):
        self._check(cancel_event, deadline, serial)
        if self._ready:
            if self._worker.is_alive():
                return
            raise TTSError("Piper worker exited unexpectedly.")
        self._stop_worker()
        if not os.path.exists(self.voice_path):
            raise TTSError(f"Piper voice model not found at: {self.voice_path}")
        try:
            parent, child = self._context.Pipe(duplex=True)
        except Exception as exc:
            raise TTSError("Piper worker IPC could not be created.") from exc
        self._connection = parent
        try:
            self._worker = self._context.Process(target=worker_main, args=(child, self.voice_path), daemon=True)
            self._worker.start()
        except Exception as exc:
            self._stop_worker()
            raise TTSError("Piper worker could not be started.") from exc
        finally:
            child.close()
        response = self._exchange(None, cancel_event, deadline, serial)
        self._validate_error(response, None)
        if response != {"type": "ready"}:
            raise TTSError("Piper worker returned invalid readiness data.")
        self._ready = True

    def warm(self):
        deadline, serial = time.monotonic() + float(self.timeout), self._serial
        self._acquire(None, deadline, serial)
        try:
            self._ensure_ready(None, deadline, serial)
            self._check(None, deadline, serial)
        except BaseException:
            self._stop_worker()
            raise
        finally:
            self._lock.release()

    def _stop_worker(self, *, graceful=False):
        process, connection = self._worker, self._connection
        self._ready = False
        shutdown = None
        try:
            if process is not None and process.pid is not None:
                if graceful and process.is_alive() and connection is not None:
                    def request_shutdown():
                        try:
                            send_message(connection, {"type": "shutdown"})
                            connection.recv_bytes(MAX_RESPONSE_BYTES)
                        except Exception:
                            pass
                    shutdown = Thread(target=request_shutdown, name="piper-shutdown", daemon=True)
                    shutdown.start()
                    process.join(timeout=0.5)
                if process.is_alive():
                    try:
                        process.terminate()
                    except OSError:
                        pass
                    process.join(timeout=2)
                if process.is_alive():
                    try:
                        process.kill()
                    except OSError:
                        pass
                    process.join(timeout=2)
                if process.is_alive():
                    raise TTSError("Piper worker could not be stopped.")
                process.join(timeout=0)
            if process is not None:
                process.close()
            self._worker = None
        finally:
            if connection is not None:
                connection.close()
            self._connection = None
            if shutdown is not None:
                shutdown.join(timeout=1)

    def close(self):
        """Idempotent teardown; a later request may lazily start a new worker."""
        with self._close_lock:
            self._serial += 1
            self._closing.set()
            try:
                with self._lock:
                    self._stop_worker(graceful=True)
            finally:
                self._closing.clear()


class MockTTSProvider(TextToSpeechProvider):
    """A mock TTS provider for tests and development without binaries."""
    def synthesize(self, text: str) -> bytes:
        # Return a minimal valid WAV header for a 1-sample 16kHz mono audio file
        # RIFF(44)WAVEfmt(16)data(2)
        import struct
        header = b'RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x02\x00\x00\x00'
        data = struct.pack('<h', 0)
        return header + data
