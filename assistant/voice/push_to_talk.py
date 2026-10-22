"""Single-owner, hold-to-record input; release never waits for VAD or a cap."""
from contextlib import contextmanager
from pathlib import Path
import time
import wave
from assistant import latency_trace as latency

from .hands_free import NativeRequestReader, SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH, CHUNK_FRAMES, FRAME_BYTES

BCM_PIN = 17  # Header 11. WIRING.md reserves 18-21 (I2S) and 14/15 (UART).


class ShortPress(ValueError):
    pass


class TalkButton:
    def __init__(self):
        import lgpio
        self.gpio = lgpio
        self.handle = None
        # Pi OS revisions number RP1 differently. Select by label, never chip number.
        for path in sorted(Path('/dev').glob('gpiochip[0-9]*')):
            handle = None
            try:
                handle = lgpio.gpiochip_open(int(path.name[len('gpiochip'):]))
                if 'rp1' in str(lgpio.gpio_get_chip_info(handle)[-1]).lower():
                    self.handle = handle
                    break
            except lgpio.error:
                pass
            if handle is not None:
                lgpio.gpiochip_close(handle)
        if self.handle is None:
            raise RuntimeError('Accessible Pi 5 RP1 GPIO chip not found; check gpio-group permissions.')
        try:
            if lgpio.gpio_get_mode(self.handle, BCM_PIN) & 1:
                raise RuntimeError('BCM17 is already claimed; do not attach or reuse it.')
            lgpio.gpio_claim_input(self.handle, BCM_PIN, lgpio.SET_PULL_UP)
            self.held = self
        except BaseException:
            lgpio.gpiochip_close(self.handle)
            raise

    def is_set(self):
        return self.gpio.gpio_read(self.handle, BCM_PIN) == 0

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        pressed_at = None
        while time.monotonic() < deadline:
            if self.is_set():
                pressed_at = pressed_at if pressed_at is not None else time.monotonic()
                if time.monotonic() - pressed_at >= .04:
                    return True
            else:
                pressed_at = None
            time.sleep(.005)
        return False

    def close(self):
        try:
            self.gpio.gpio_free(self.handle, BCM_PIN)
        finally:
            self.gpio.gpiochip_close(self.handle)


def wait_released(held):
    released_at = None
    while True:
        if held.is_set():
            released_at = None
        else:
            released_at = released_at if released_at is not None else time.monotonic()
            if time.monotonic() - released_at >= .04:
                return
        time.sleep(.005)


@contextmanager
def microphone_ownership():
    """All Linux ALSA CLI modes share a per-user lock, including the boot service."""
    import fcntl
    directory = Path.home() / '.cache' / 'smart-ai-companion'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / 'microphone.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another voice loop owns the microphone; stop it first.') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


@latency.span('capture')
def record_held(path, device, held, *, max_seconds=20, on_ready=None, on_amplitude=None):
    """Preserve native frames. Release polling <=10ms even on a stalled pipe."""
    if not 1 <= max_seconds <= 30:
        raise ValueError('PTT safety limit must be 1 to 30 seconds.')
    reader = NativeRequestReader(device, max_seconds + 5)
    frames = 0
    from .hands_free import normalized_left_rms
    from assistant.display.audio_level import observe
    try:
        if not held.is_set():
            raise ShortPress('Button released before capture.')
        reader.start()
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(CHANNELS)
            output.setsampwidth(SAMPLE_WIDTH)
            output.setframerate(SAMPLE_RATE)
            while held.is_set() and frames < round(max_seconds * SAMPLE_RATE):
                chunk = reader.read_frames(min(CHUNK_FRAMES, round(max_seconds * SAMPLE_RATE) - frames),
                                           keep_recording=held.is_set)
                if not chunk:
                    break
                output.writeframesraw(chunk)
                frames += len(chunk) // FRAME_BYTES
                if frames == len(chunk) // FRAME_BYTES and on_ready is not None:
                    latency.mark('capture_ready', once=True)
                    on_ready() # First actual PCM, not just Popen success.
                observe(on_amplitude, normalized_left_rms(chunk))
            if not held.is_set():
                latency.mark('button_release', once=True)
                latency.mark('capture_stopped', once=True, reason='button_release')
            else:
                latency.mark('capture_stopped', once=True, reason='max_duration')
    finally:
        try:
            reader.close()
        except Exception as exc:
            from .interruption import InterruptionCleanupError
            raise InterruptionCleanupError('PTT microphone release was not confirmed; stop before another capture.') from exc
    if frames < round(.12 * SAMPLE_RATE):
        raise ShortPress('Press was too short; hold, wait for LISTENING, then speak.')
