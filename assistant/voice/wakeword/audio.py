"""Bounded, streaming conversion of native INMP441 PCM for speech KWS."""

import os
import selectors
import subprocess
import time

from .base import WakeWordError

NATIVE_FRAME_BYTES = 8
KWS_FRAME_SAMPLES = 1280  # 80 ms at 16 kHz.
CAPTURE_READ_BYTES = KWS_FRAME_SAMPLES * 3 * NATIVE_FRAME_BYTES


class LeftChannelConverter:
    """LEFT S32_LE -> low-pass FIR -> decimate by 3 -> mono S16_LE.

    A 63-tap Hamming-windowed sinc at 7 kHz suppresses aliasing above the
    new 8 kHz Nyquist frequency. History and decimation phase survive arbitrary
    pipe chunk boundaries. Only a partial stereo frame and 62 samples persist.
    """

    def __init__(self):
        import numpy as np
        self.np = np
        taps = np.arange(63) - 31
        kernel = (2 * 7000 / 48000) * np.sinc((2 * 7000 / 48000) * taps) * np.hamming(63)
        self.kernel = kernel / kernel.sum()
        self.reset()

    def reset(self):
        self.partial = b""
        self.history = self.np.zeros(62)
        self.phase = 0

    def convert(self, chunk):
        np = self.np
        data = self.partial + chunk
        length = len(data) - len(data) % NATIVE_FRAME_BYTES
        self.partial = data[length:]
        if not length:
            return b""
        left = np.frombuffer(data[:length], dtype="<i4").reshape(-1, 2)[:, 0].astype(np.float64)
        samples = np.concatenate((self.history, left))
        filtered = np.convolve(samples, self.kernel, mode="valid")
        self.history = samples[-62:].copy()
        output = filtered[(-self.phase) % 3::3]
        self.phase = (self.phase + len(left)) % 3
        return np.clip(np.rint(output / 65536), -32768, 32767).astype("<i2").tobytes()


class AlsaWakeCapture:
    """One raw arecord process per listening period, with no threads or files.

    Selector blocking allows finite timeouts and Ctrl+C even if ALSA stalls.
    Diagnostics go to /dev/null to avoid unbounded pipes and idle disk I/O.
    """

    def __init__(self, device):
        self.device = device
        self.process = None
        self.selector = None

    def start(self):
        try:
            self.process = subprocess.Popen(
                ["arecord", "-q", "-D", self.device, "-c", "2", "-r", "48000",
                 "-f", "S32_LE", "-t", "raw"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0,
            )
            os.set_blocking(self.process.stdout.fileno(), False)
            self.selector = selectors.DefaultSelector()
            self.selector.register(self.process.stdout, selectors.EVENT_READ)
        except BaseException:
            self.close()
            raise

    def chunks(self, deadline=None, cancel_event=None):
        last_audio = time.monotonic()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                return
            if now - last_audio >= 5:
                raise WakeWordError("Wake microphone stalled for 5 seconds.")
            timeout = min(0.25, max(0, deadline - now)) if deadline is not None else 0.25
            if not self.selector.select(timeout):
                if self.process.poll() is not None:
                    raise WakeWordError(f"Wake arecord exited (exit {self.process.returncode}).")
                continue
            try:
                chunk = os.read(self.process.stdout.fileno(), CAPTURE_READ_BYTES)
            except BlockingIOError:
                continue
            if not chunk:
                raise WakeWordError("Wake arecord closed its audio stream; check the ALSA capture device.")
            last_audio = time.monotonic()
            yield chunk

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
                        pass  # The child may have exited between timeout and kill.
                    process.wait(timeout=5)
        finally:
            if self.selector is not None:
                self.selector.close()
                self.selector = None
            if process is not None and process.stdout is not None:
                process.stdout.close()
