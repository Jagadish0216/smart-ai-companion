"""File-backed PCM preparation and a cancellable, response-long ALSA player."""

import os
import logging
import math
from collections import deque
from pathlib import Path
import struct
import subprocess
import tempfile
import time

from .processes import AudioCancelledError
from assistant.display.audio_level import observe

SAMPLE_RATE = 48000
FRAME_BYTES = 8  # Stereo signed 32-bit little-endian.
COPY_BYTES = 65536
EDGE_WINDOW_FRAMES = 480  # 10 ms analysis windows.
EDGE_PADDING_FRAMES = 1440  # Preserve 30 ms beside each detected speech edge.
EDGE_THRESHOLD = round(2147483648 * 0.0001)  # -80 dBFS peak, deliberately conservative.
logger = logging.getLogger(__name__)


class PlaybackAmplitudeObserver:
    """Bounded PCM windows on an estimated device timeline, never audio pacing.

    Accepted pipe bytes can lead physical playback by ALSA/device buffering.
    Retain only amplitude summaries, service them from existing writer waits
    and drain checks, and skip expired windows instead of bursting UART writes.
    """
    window_bytes = 3840 * FRAME_BYTES  # 80 ms

    def __init__(self, callback, *, clock=time.monotonic):
        self.callback, self.clock = callback, clock
        self.pending = bytearray()
        self.windows = deque()
        self.end = None
        self.current = 0.0
        self.next_poll = 0.0

    def accept(self, data):
        self.pending.extend(data)
        while len(self.pending) >= self.window_bytes:
            block = self.pending[:self.window_bytes]
            del self.pending[:self.window_bytes]
            self._window(block)
        self.poll()

    def _window(self, block):
        now = self.clock()
        start = max(now, self.end) if self.end is not None else now
        self.end = start + len(block) / (SAMPLE_RATE * FRAME_BYTES)
        squares = sum(sample * sample for (sample,) in struct.iter_unpack("<i", block))
        rms = math.sqrt(squares / (len(block) // 4)) / 2147483648.0
        # Far more than normal pipe/device buffering; retain nearest windows
        # under pathological buffering, coalescing only the furthest summary.
        if len(self.windows) >= 256:
            self.windows.pop()
        self.windows.append((start, rms))

    def flush(self):
        length = len(self.pending) // FRAME_BYTES * FRAME_BYTES
        if length:
            self._window(self.pending[:length])
            del self.pending[:length]
        self.poll()

    def poll(self):
        now = self.clock()
        if self.end is None:
            return
        if now < self.next_poll:
            return
        self.next_poll = now + 0.08
        while self.windows and self.windows[0][0] <= now:
            _, self.current = self.windows.popleft()
        if self.end is None or now >= self.end:
            self.current = 0.0
        observe(self.callback, self.current)


class AlsaStreamError(Exception):
    pass


def trim_pcm_edges(source: Path, destination: Path, *, trim_leading, cancel_event):
    """Trim only excess quiet edge windows; preserve all above-threshold samples.

    First-chunk leading audio stays intact. All-quiet chunks stay intact too:
    without a detected speech edge, guessing where to cut would be unsafe.
    Return retained leading/trailing quiet-frame counts for pause accounting.
    """
    size = source.stat().st_size
    if not size or size % FRAME_BYTES:
        raise AlsaStreamError("Prepared PCM is empty or contains an incomplete stereo frame.")
    frames = size // FRAME_BYTES

    def check_cancel():
        if cancel_event.is_set():
            raise AudioCancelledError("PCM preparation cancelled.")

    def quiet(data):
        return all(abs(left) <= EDGE_THRESHOLD and abs(right) <= EDGE_THRESHOLD
                   for left, right in struct.iter_unpack("<ii", data))

    with source.open("rb") as audio:
        leading = 0
        while leading < frames:
            check_cancel()
            count = min(EDGE_WINDOW_FRAMES, frames - leading)
            if not quiet(audio.read(count * FRAME_BYTES)):
                break
            leading += count
        trailing = 0
        while trailing < frames and leading != frames:
            check_cancel()
            count = min(EDGE_WINDOW_FRAMES, frames - trailing)
            audio.seek((frames - trailing - count) * FRAME_BYTES)
            if not quiet(audio.read(count * FRAME_BYTES)):
                break
            trailing += count
        start, end = 0, frames
        if leading != frames:
            if trim_leading:
                start = max(0, leading - EDGE_PADDING_FRAMES)
            end = frames - max(0, trailing - EDGE_PADDING_FRAMES)
        else:
            trailing = frames
        audio.seek(start * FRAME_BYTES)
        remaining = (end - start) * FRAME_BYTES
        with destination.open("wb") as output:
            while remaining:
                check_cancel()
                block = audio.read(min(COPY_BYTES, remaining))
                if not block:
                    raise AlsaStreamError("Prepared PCM ended unexpectedly during edge trimming.")
                output.write(block)
                remaining -= len(block)
    return max(0, leading - start), max(0, trailing - (frames - end))


class PersistentAlsaPlayer:
    """Own one aplay process, writing bounded blocks without a blocking pipe.

    No extra writer/drain thread. Nonblocking stdin allows cancellation even
    when the device stops consuming PCM. stderr is file-backed to avoid pipe
    deadlock. One 300-second response-lifetime deadline also bounds final drain.
    """

    def __init__(self, device, cancel_event, temp_path, timeout=300, *, on_amplitude=None):
        self.device = device
        self.cancel_event = cancel_event
        self.temp_path = temp_path
        self.timeout = timeout
        self.process = None
        self.stderr = None
        self.deadline = None
        self.fd = None
        self.amplitude = PlaybackAmplitudeObserver(on_amplitude) if on_amplitude is not None else None

    def start(self):
        self.check()
        self.stderr = tempfile.TemporaryFile(dir=self.temp_path)
        try:
            self.process = subprocess.Popen(
                ["aplay", "-D", self.device, "-t", "raw", "-f", "S32_LE", "-r", "48000", "-c", "2"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self.stderr, bufsize=0,
            )
            self.deadline = time.monotonic() + self.timeout
            self.fd = self.process.stdin.fileno()
            os.set_blocking(self.fd, False)
            self.check()
        except FileNotFoundError as exc:
            self.abort()
            raise AlsaStreamError("Playback failed: aplay was not found on PATH.") from exc
        except BaseException:
            self.abort()
            raise

    def check(self):
        if self.cancel_event.is_set():
            raise AudioCancelledError("Persistent ALSA playback cancelled.")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise AlsaStreamError("Persistent aplay exceeded the bounded playback timeout.")
        if self.process is not None and self.process.poll() is not None:
            raise AlsaStreamError(f"aplay exited unexpectedly (exit {self.process.returncode}){self._detail()}")
        if self.amplitude is not None:
            self.amplitude.poll()

    def _detail(self):
        if self.stderr is None:
            return "."
        self.stderr.seek(0, os.SEEK_END)
        self.stderr.seek(max(0, self.stderr.tell() - 4096))
        text = self.stderr.read().decode("utf-8", errors="replace").strip()
        return ": " + text if text else "."

    def write(self, data, *, on_write=None):
        remaining = memoryview(data)
        while remaining:
            self.check()
            try:
                count = os.write(self.fd, remaining[:COPY_BYTES])
            except BlockingIOError:
                self.cancel_event.wait(0.01)  # Device backpressure, not an inter-chunk pause.
                continue
            except BrokenPipeError as exc:
                raise AlsaStreamError("Persistent aplay stdin closed during playback.") from exc
            if count <= 0:
                raise AlsaStreamError("Persistent aplay accepted no PCM data.")
            if self.amplitude is not None:
                self.amplitude.accept(remaining[:count])
            remaining = remaining[count:]
            if on_write is not None:
                on_write(count)

    def write_silence(self, frames):
        zero_block = b"\x00" * COPY_BYTES
        remaining = frames * FRAME_BYTES
        while remaining:
            count = min(remaining, COPY_BYTES)
            self.write(zero_block[:count])
            remaining -= count
        if self.amplitude is not None:
            self.amplitude.flush()

    def write_file(self, path, *, on_progress=None):
        """Optionally report accepted PCM bytes / total, not device timestamps.

        Reports run on the existing writer after each successful partial write.
        A failed observer is disabled for this file without interrupting audio.
        """
        with path.open("rb") as audio:
            if on_progress is not None:
                total = os.fstat(audio.fileno()).st_size
                written = 0

                def report(count):
                    nonlocal written, on_progress
                    written += count
                    if on_progress is not None:
                        try:
                            on_progress(written, total)
                        except Exception:
                            on_progress = None
                            logger.warning("Playback progress update failed; audio continues")

            while True:
                self.check()
                block = audio.read(COPY_BYTES)
                if not block:
                    if self.amplitude is not None:
                        self.amplitude.flush()
                    return
                if on_progress is None:
                    self.write(block)
                else:
                    self.write(block, on_write=report)

    def finish(self):
        """EOF, drain the device, verify status, reap, then release descriptors."""
        try:
            self.check()
            self.process.stdin.close()
            while True:
                if self.cancel_event.is_set():
                    raise AudioCancelledError("Persistent ALSA playback cancelled during drain.")
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise AlsaStreamError("Persistent aplay timed out during final drain.")
                if self.amplitude is not None:
                    self.amplitude.poll()
                try:
                    result = self.process.wait(timeout=min(0.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pass
            if result != 0:
                raise AlsaStreamError(f"Playback failed (aplay exit {result}){self._detail()}")
        finally:
            self.abort()

    def abort(self):
        """Terminate/kill/reap the single child and close all owned handles."""
        try:
            if self.process is not None:
                if self.process.poll() is None:
                    try:
                        self.process.terminate()
                    except OSError:
                        pass
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        self.process.kill()
                    except OSError:
                        pass
                    self.process.wait(timeout=5)
        finally:
            if self.process is not None and self.process.stdin is not None:
                self.process.stdin.close()
            if self.stderr is not None:
                self.stderr.close()
                self.stderr = None
