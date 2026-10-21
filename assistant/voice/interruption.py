"""Opt-in response cancellation and spoken-delivery persistence boundary."""
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event, Lock, Thread
import socket
import sys
import time

_active = ContextVar("voice_delivery", default=None)


class VoiceInterrupted(Exception):
    def __init__(self, control):
        super().__init__("Voice response interrupted.")
        self.control = control


class InterruptionCleanupError(RuntimeError):
    """Do not start another capture after an unconfirmed monitor shutdown."""


def current():
    return _active.get()


@contextmanager
def delivery(control):
    token = _active.set(control)
    try:
        yield control
    finally:
        _active.reset(token)
        control.pending.clear()


def defer_response(conversation, text, latency_ms):
    control = current()
    if control is None:
        return False
    if not control.interrupted.is_set():
        control.pending[:] = [(conversation, text, latency_ms)]
    return True


@contextmanager
def interruptible_response(response):
    control = current()
    if control is None:
        yield
        return
    # Shutdown wakes a blocked HTTP readline without touching Ollama's process.
    sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    if sock is None:
        raise RuntimeError("Interruptible HTTP socket unavailable; use original mode.")
    callback = lambda: sock.shutdown(socket.SHUT_RDWR)
    control.register(callback)
    try:
        yield
    finally:
        control.unregister(callback)


class EchoGuard:
    """Bounded speaker-reference rejection, not acoustic echo cancellation."""
    def __init__(self):
        import numpy as np
        self.np = np
        self.reference = np.empty(0)
        self.lock = Lock()
        self.unsafe_text = False
        self.block_until = 0.0
        self.partial = b""

    def text(self, text):
        import re
        unsafe = re.search(r"\bjarvis\b", text, re.I) is not None
        with self.lock:
            if self.unsafe_text and not unsafe:
                self.block_until = time.monotonic() + 2
            self.unsafe_text = unsafe

    def accept(self, pcm):
        np = self.np
        with self.lock:
            pcm = self.partial + pcm
            size = len(pcm)//8*8
            self.partial = pcm[size:]
            if not size:
                return
            mono = np.frombuffer(pcm[:size], dtype="<i4").reshape(-1, 2)[:, 0].astype(float)[::24]
            self.reference = np.concatenate((self.reference,mono))[-4000:] # 2 s at 2 kHz

    def permits(self, frame):
        np = self.np
        incoming = np.frombuffer(frame,dtype="<i2").astype(float)[::8]
        incoming -= incoming.mean()
        energy = float(np.dot(incoming,incoming))
        with self.lock:
            reference = self.reference.copy()
            blocked = self.unsafe_text or time.monotonic() < self.block_until
        if blocked or len(reference) < len(incoming) or energy < 1:
            return False
        reference -= reference.mean()
        window_energy = np.convolve(reference*reference,np.ones(len(incoming)),mode="valid")
        if window_energy.max(initial=0) < 1:
            return False # No valid playback reference: fail closed.
        correlation = abs(np.correlate(reference,incoming,mode="valid"))
        score = (correlation/np.sqrt(np.maximum(window_energy*energy,1))).max(initial=0)
        return bool(score < .35)


class InterruptControl:
    def __init__(self, *, provider=None, device=None, voice=False, keyboard=True):
        self.stop_event = Event()
        self.interrupted = Event()
        self.monitor_stop = Event()
        self.callbacks = []
        self.lock = Lock()
        self.pending = []
        self.conversation_id = None
        self.mode = "stop"
        self.detection = None
        self.provider, self.device = provider, device
        self.voice, self.keyboard = voice, keyboard
        self.guard = EchoGuard() if voice else None
        self.threads = []
        self.started = self.voice_started = False
        self.cleanup_error = False

    def register(self, callback):
        with self.lock:
            self.callbacks.append(callback)
            cancelled = self.stop_event.is_set()
        if cancelled:
            try: callback()
            except OSError: pass

    def unregister(self, callback):
        with self.lock:
            if callback in self.callbacks: self.callbacks.remove(callback)

    def cancel(self, mode="stop", detection=None):
        with self.lock:
            if self.interrupted.is_set() or self.monitor_stop.is_set(): return
            self.mode, self.detection = mode, detection
            self.interrupted.set()
        self.abort_generation()

    def abort_generation(self):
        self.stop_event.set()
        with self.lock:
            callbacks = list(self.callbacks)
        for callback in callbacks:
            try: callback()
            except OSError: pass

    def start(self):
        if self.started: return
        self.started = True
        if self.keyboard and sys.stdin.isatty():
            def keys():
                import os
                command = ""
                while not self.monitor_stop.wait(.05) and not self.interrupted.is_set():
                    if os.name == "nt":
                        import msvcrt
                        if not msvcrt.kbhit(): continue
                        key = msvcrt.getwch()
                        if key not in ("\r", "\n"):
                            command = (command + key)[-16:]
                            continue
                        key, command = command, ""
                    else:
                        import select
                        if not select.select([sys.stdin],[],[],.1)[0]: continue
                        key = sys.stdin.readline()
                        if not key: return
                    key = key.strip().lower()
                    if key in ("", "s", "n"):
                        self.cancel("listen" if key == "n" else "stop")
                        return
            self._thread(keys,"voice-interrupt-key")

    def reference(self, pcm):
        if not self.voice: return
        self.guard.accept(pcm)
        if self.voice_started: return
        self.voice_started = True
        def listen():
            from .wakeword.base import WakeWordCleanupError
            try:
                self.provider.reset()
                result = self.provider.wait_for_wake_word(
                    self.device,cancel_event=self.monitor_stop,guard=self.guard.permits,
                    confirmations=2,threshold=.85,
                    on_detection=lambda result: self.cancel("listen", result))
                if result.detected:
                    self.detection = result # Final retained PCM after microphone release.
            except WakeWordCleanupError:
                self.cleanup_error = True
            except Exception:
                if self.interrupted.is_set():
                    self.mode, self.detection = "stop", None
                print("Voice barge-in unavailable: duplex capture/monitor failed; use Enter/s or Ctrl+C. Repeat after a fresh wake if handoff failed.",file=sys.stderr)
        self._thread(listen,"voice-interrupt-wake")

    def _thread(self, function, name):
        thread = Thread(target=function,name=name,daemon=True)
        self.threads.append(thread)
        thread.start()

    def close(self):
        self.monitor_stop.set()
        for thread in self.threads:
            thread.join(timeout=8) # Includes owned arecord terminate/kill/reap bounds.
            if thread.is_alive():
                raise InterruptionCleanupError("Interruption monitor did not release capture; refusing another request.")
        if self.cleanup_error:
            raise InterruptionCleanupError("Wake microphone release failed; refusing another request.")
        if self.guard is not None:
            self.guard.reference = self.guard.np.empty(0)
        self.detection = self.detection if self.interrupted.is_set() else None

    def commit(self):
        if self.interrupted.is_set(): return
        from assistant.services import _persist_generated_response
        pending, self.pending = self.pending, []
        token = _active.set(None)
        try:
            for conversation,text,latency_ms in pending:
                _persist_generated_response(conversation,text,latency_ms)
        finally:
            _active.reset(token)
