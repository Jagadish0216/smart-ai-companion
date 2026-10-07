"""Persistent, best-effort USB serial transport for existing ESP32 firmware."""

import logging
import threading
import time

try:
    import serial
except ImportError:  # Disabled development environments need no serial package.
    serial = None

from .base import CompanionDisplay, DisplayState

logger = logging.getLogger(__name__)
WRITE_TIMEOUT_SECONDS = 0.05
RECONNECT_INTERVAL_SECONDS = 5.0
MAX_TEXT_CHARS = 40  # Existing firmware status-text limit.


class SerialCompanionDisplay(CompanionDisplay):
    def __init__(self, *, port, baudrate=115200, startup_delay_seconds=1.5):
        self.port = port
        self.baudrate = baudrate
        self.startup_delay_seconds = startup_delay_seconds
        self._lock = threading.RLock()
        self._serial = None
        self._closed = False
        self._next_reconnect_at = 0.0
        self._ready_at = 0.0
        # This one initialization delay is outside the voice request path.
        if self._connect() and startup_delay_seconds:
            try:
                time.sleep(startup_delay_seconds)
            except BaseException:
                self.close()
                raise

    def _connect(self):
        connection = None
        try:
            if serial is None:
                raise RuntimeError("serial dependency unavailable")
            connection = serial.Serial(
                port=None, baudrate=self.baudrate, timeout=0,
                write_timeout=WRITE_TIMEOUT_SECONDS, rtscts=False, dsrdtr=False,
            )
            # Set inactive lines before open; do not toggle reset lines per command.
            # Some OS/drivers can still glitch these lines on opening the port.
            connection.dtr = False
            connection.rts = False
            connection.port = self.port
            connection.open()
            self._serial = connection
            self._ready_at = time.monotonic() + self.startup_delay_seconds
            return True
        except Exception:
            if connection is not None:
                self._close_connection(connection)
            self._next_reconnect_at = time.monotonic() + RECONNECT_INTERVAL_SECONDS
            logger.warning("Companion display unavailable; will retry on a later update")
            return False

    @staticmethod
    def _close_connection(connection):
        try:
            connection.close()
        except Exception:
            logger.warning("Companion display connection cleanup failed")

    def _send(self, command):
        payload = (command + "\n").encode("ascii")
        with self._lock:
            if self._closed:
                return False
            if self._serial is None:
                if time.monotonic() < self._next_reconnect_at or not self._connect():
                    return False
            # Reconnect startup waits are non-blocking: a later state update sends
            # the latest state after the device settles, never replaying stale ones.
            if time.monotonic() < self._ready_at:
                return False
            try:
                if self._serial.write(payload) != len(payload):
                    raise OSError("incomplete serial write")
                return True
            except Exception:
                self._close_connection(self._serial)
                self._serial = None
                self._next_reconnect_at = time.monotonic() + RECONNECT_INTERVAL_SECONDS
                logger.warning("Companion display write failed; will retry on a later update")
                return False

    def set_state(self, state):
        return self._send("STATE " + DisplayState(state).value)

    def set_text(self, text):
        # Printable ASCII, one line, bounded to firmware size. Never allow injected
        # STATE/CLEAR commands through CR/LF or control bytes in a status label.
        ascii_text = str(text).encode("ascii", errors="replace").decode("ascii")
        clean = "".join(character if 32 <= ord(character) <= 126 else " " for character in ascii_text)
        clean = " ".join(clean.split())[:MAX_TEXT_CHARS].strip()
        return self._send("TEXT " + clean) if clean else self.clear_text()

    def clear_text(self):
        return self._send("CLEAR TEXT")

    def close(self):
        with self._lock:
            self._closed = True
            if self._serial is not None:
                self._close_connection(self._serial)
                self._serial = None
