"""Cancellable subprocess execution for the standalone streaming audio worker."""

import subprocess
import time


class AudioCancelledError(subprocess.SubprocessError):
    pass


def run_cancellable(command, *, cancel_event, timeout, input=None):
    """Drain pipes while polling cancellation; always stop and reap the child."""
    if cancel_event.is_set():
        raise AudioCancelledError("Audio cancelled because the voice stream stopped.")
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + timeout
    first_communicate = True
    try:
        while True:
            if cancel_event.is_set():
                raise AudioCancelledError("Audio cancelled because the voice stream stopped.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(
                    input=input if first_communicate else None, timeout=min(0.1, remaining),
                )
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                first_communicate = False
    finally:
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            process.communicate(timeout=5)
