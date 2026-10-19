"""Deterministic LEVEL conversion and real PCM observation without hardware."""

import math
from pathlib import Path
import struct
import tempfile
from threading import Event
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from assistant.display import CompanionDisplay, DisplayState, NoOpDisplay
from assistant.display.audio_level import AudioLevelMeter
from assistant.display.serial_display import SerialCompanionDisplay
from assistant.voice.processes import AudioCancelledError
from assistant.voice.alsa_stream import PersistentAlsaPlayer, PlaybackAmplitudeObserver
from scripts import voice_loop


class LevelProtocolTests(SimpleTestCase):
    def test_valid_exact_commands_and_noop(self):
        with patch("assistant.display.serial_display.serial") as serial:
            serial.Serial.return_value.write.side_effect = len
            display = SerialCompanionDisplay(port="mock", startup_delay_seconds=0)
            self.addCleanup(display.close)
            for value in (0, 1, 50, 100):
                self.assertTrue(display.set_level(value))
                serial.Serial.return_value.write.assert_called_with(f"LEVEL {value}\n".encode("ascii"))
                self.assertFalse(NoOpDisplay().set_level(value))

    def test_invalid_values_never_write(self):
        with patch("assistant.display.serial_display.serial") as serial:
            display = SerialCompanionDisplay(port="mock", startup_delay_seconds=0)
            self.addCleanup(display.close)
            for value in (-1, 101, True, False, 1.0, None, "10", "0\nSTATE ERROR", math.nan, math.inf):
                for provider in (display, NoOpDisplay()):
                    with self.subTest(value=value), self.assertRaises(ValueError):
                        provider.set_level(value)
            serial.Serial.return_value.write.assert_not_called()

    def test_old_provider_inherits_optional_method_and_failed_serial_is_nonfatal(self):
        class Legacy(CompanionDisplay):
            def set_state(self, state): pass
            def set_text(self, text): pass
            def clear_text(self): pass
            def close(self): pass
        self.assertFalse(Legacy().set_level(50))
        voice_loop._set_display_state(object(), DisplayState.READY)
        with patch("assistant.display.serial_display.serial") as serial:
            display = SerialCompanionDisplay(port="mock", startup_delay_seconds=0)
            self.addCleanup(display.close)
            serial.Serial.return_value.write.side_effect = OSError("disconnected")
            self.assertFalse(display.set_level(20))

    def test_every_state_resets_even_if_state_provider_fails(self):
        display = MagicMock()
        for state in DisplayState:
            voice_loop._set_display_state(display, state)
            display.set_level.assert_called_with(0)
        display.set_state.side_effect = RuntimeError()
        display.set_level.side_effect = RuntimeError()
        voice_loop._set_display_state(display, DisplayState.ERROR)


class MeterTests(SimpleTestCase):
    def setUp(self):
        self.now = 0.0
        self.levels = []
        self.meter = AudioLevelMeter(self.levels.append, clock=lambda: self.now)

    def tick(self, rms, seconds=0.081):
        self.now += seconds
        self.meter.feed(rms)

    def test_silence_representative_amplitudes_saturation_and_invalid_values(self):
        for rms, expected in ((0, 0), (0.025, 10), (0.125, 50), (0.25, 100), (1, 100)):
            self.meter.reset()
            for _ in range(30):
                self.tick(rms)
            self.assertEqual(self.levels[-1], expected)
        for rms in (-2, math.nan, math.inf, -math.inf, None, "invalid"):
            self.meter.reset()
            self.tick(rms)
            self.assertEqual(self.levels[-1], 0)

    def test_attack_release_throttle_and_silence_settles(self):
        self.meter.reset()
        self.tick(0.25)
        peak = self.levels[-1]
        self.assertTrue(80 < peak < 100)
        self.tick(0, 0.001)
        self.assertEqual(self.levels[-1], peak)
        self.tick(0)
        self.assertTrue(0 < self.levels[-1] < peak)
        for _ in range(30):
            self.tick(0)
        self.assertEqual(self.levels[-1], 0)

    def test_coalescing_and_firmware_freshness_keepalive(self):
        self.meter.reset()
        for _ in range(9):
            self.tick(0)
        self.assertEqual(self.levels, [0])
        self.tick(0)
        self.assertEqual(self.levels, [0, 0])

    def test_reset_bypasses_throttle_and_observer_failure_is_nonfatal(self):
        self.tick(0.25)
        self.meter.reset()
        self.assertEqual(self.levels[-1], 0)
        meter = AudioLevelMeter(MagicMock(side_effect=RuntimeError()))
        meter.feed(0.5)
        meter.reset()
        for value in (0, -1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                AudioLevelMeter(None, gain=value)


class PlaybackObservationTests(SimpleTestCase):
    def setUp(self):
        self.now = 100.0
        self.values = []
        self.observer = PlaybackAmplitudeObserver(self.values.append, clock=lambda: self.now)

    def pcm(self, left, right, frames=3840):
        return struct.pack("<ii", left, right) * frames

    def test_both_signed_channels_and_partial_byte_writes(self):
        data = self.pcm(2**30, -(2**30))
        for start in range(0, len(data), 13):
            self.observer.accept(data[start:start + 13])
        self.assertEqual(self.values, [0.5])
        self.assertEqual(self.observer.pending, b"")

    def test_queued_audio_is_not_reported_ahead_and_drain_silence(self):
        self.observer.accept(self.pcm(0, 0) + self.pcm(-(2**31), -(2**31)))
        self.assertEqual(self.values, [0])
        self.now += 0.081
        self.observer.poll()
        self.assertEqual(self.values[-1], 1)
        self.now += 0.081
        self.observer.poll()
        self.assertEqual(self.values[-1], 0)

    def test_short_tail_and_failed_callback(self):
        self.observer.poll()  # An empty check cannot suppress the first short file.
        self.observer.accept(self.pcm(2**30, 0, frames=10))
        self.assertEqual(self.values, [])
        self.observer.flush()
        self.assertAlmostEqual(self.values[-1], math.sqrt(0.125))
        self.observer.callback = MagicMock(side_effect=RuntimeError())
        self.now += 1
        self.observer.poll()

    def test_summary_queue_is_bounded_and_late_checks_skip_expired_windows(self):
        for _ in range(300):
            self.observer.accept(self.pcm(2**30, 0))
        self.assertLessEqual(len(self.observer.windows), 256)
        self.now += 40
        self.observer.poll()
        self.assertEqual(len(self.values), 2)
        self.assertEqual(self.values[-1], 0)

    def test_player_observes_only_accepted_pcm_preserving_progress_bytes_and_failures(self):
        data = self.pcm(2**30, -(2**30), frames=5000)
        received, reports, amplitudes = [], [], []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prepared-after-gain.pcm"
            path.write_bytes(data)
            player = PersistentAlsaPlayer("mock", Event(), Path(directory), on_amplitude=amplitudes.append)
            player.amplitude.clock = lambda: self.now
            blocked = True
            def write(fd, block):
                nonlocal blocked
                if blocked:
                    blocked = False
                    raise BlockingIOError()
                count = min(13, len(block))
                received.append(bytes(block[:count]))
                return count
            with patch("assistant.voice.alsa_stream.os.write", side_effect=write):
                player.write_file(path, on_progress=lambda count, total: reports.append((count, total)))
            self.assertEqual(b"".join(received), data)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(reports[-1], (len(data), len(data)))
            self.assertEqual(amplitudes, [0.5])
            player.amplitude.callback = MagicMock(side_effect=RuntimeError())
            self.now += 1
            received.clear()
            with patch("assistant.voice.alsa_stream.os.write", side_effect=write):
                player.write_file(path)
            self.assertEqual(b"".join(received), data)
            player.cancel_event.set()
            with self.assertRaises(AudioCancelledError):
                player.write(data)


class DisconnectedDisplayAudioTests(SimpleTestCase):
    def setUp(self):
        serial_patch = patch("assistant.display.serial_display.serial")
        self.serial = serial_patch.start()
        self.addCleanup(serial_patch.stop)
        self.display = SerialCompanionDisplay(port="mock", startup_delay_seconds=0)
        self.addCleanup(self.display.close)
        self.serial.Serial.return_value.write.side_effect = OSError("TFT disconnected")

    def report(self, rms):
        self.display.set_level(round(rms * 100))

    def test_serial_write_failure_does_not_interrupt_microphone_capture(self):
        speech = struct.pack("<h", 2000) * 320
        quiet = b"\x00" * 640
        chunks = [speech] * 3 + [quiet] * 2
        with tempfile.TemporaryDirectory() as directory, \
                patch("scripts.voice_loop.subprocess.Popen") as popen, \
                patch("scripts.voice_loop.time.sleep"), patch("builtins.print"):
            import wave
            process = popen.return_value
            process.stdout.read.side_effect = chunks
            process.communicate.return_value = (b"", b"")
            path = Path(directory) / "capture.wav"
            voice_loop.record_audio_with_capture_vad(
                path, "mock", 0, 0.02, 0.04, 10, 2,
                adaptive=False, on_amplitude=self.report,
            )
            with wave.open(str(path), "rb") as audio:
                self.assertEqual(audio.readframes(audio.getnframes()), b"".join(chunks))
            process.terminate.assert_called_once()
            self.serial.Serial.return_value.write.assert_called_once()

    def test_serial_write_failure_does_not_interrupt_pcm_or_captions_progress(self):
        data = struct.pack("<ii", 2**30, -(2**30)) * 5000
        received, progress = [], []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "playback.pcm"
            path.write_bytes(data)
            player = PersistentAlsaPlayer("mock", Event(), Path(directory), on_amplitude=self.report)
            def write(fd, block):
                count = min(4096, len(block))
                received.append(bytes(block[:count]))
                return count
            with patch("assistant.voice.alsa_stream.os.write", side_effect=write):
                player.write_file(path, on_progress=lambda count, total: progress.append((count, total)))
            self.assertEqual(b"".join(received), data)
            self.assertEqual(progress[-1], (len(data), len(data)))
            self.serial.Serial.return_value.write.assert_called_once()
