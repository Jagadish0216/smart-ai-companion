import io
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from assistant.display import DisplayState, NoOpDisplay, get_companion_display
from assistant.display.serial_display import SerialCompanionDisplay, WRITE_TIMEOUT_SECONDS
from scripts import voice_loop


class SerialDisplayTests(SimpleTestCase):
    def setUp(self):
        self.clock = 100.0
        self.serial_patch = patch("assistant.display.serial_display.serial")
        self.serial = self.serial_patch.start()
        self.addCleanup(self.serial_patch.stop)
        self.connection = self.serial.Serial.return_value
        self.connection.write.side_effect = lambda payload: len(payload)
        clock_patch = patch("assistant.display.serial_display.time.monotonic", side_effect=lambda: self.clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        sleep_patch = patch("assistant.display.serial_display.time.sleep", side_effect=self.advance)
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def advance(self, seconds):
        self.clock += seconds

    def controller(self, delay=0):
        display = SerialCompanionDisplay(port="/dev/serial/by-id/example", startup_delay_seconds=delay)
        self.addCleanup(display.close)
        return display

    @override_settings(COMPANION_DISPLAY_ENABLED=False)
    def test_disabled_factory_noop_never_opens_hardware(self):
        display = get_companion_display()
        self.assertIsInstance(display, NoOpDisplay)
        self.assertFalse(display.enabled)
        self.assertFalse(display.set_state(DisplayState.READY))
        self.assertFalse(display.set_text("hello"))
        self.assertFalse(display.clear_text())
        display.close()
        self.serial.Serial.assert_not_called()

    @override_settings(COMPANION_DISPLAY_ENABLED=True, COMPANION_DISPLAY_PORT="/dev/serial/by-id/example",
                       COMPANION_DISPLAY_BAUD="115200", COMPANION_DISPLAY_STARTUP_DELAY_SECONDS="1.5")
    def test_enabled_factory_configures_port_baud_and_inactive_reset_lines(self):
        def opened():
            self.assertEqual(self.connection.port, "/dev/serial/by-id/example")
            self.assertFalse(self.connection.dtr)
            self.assertFalse(self.connection.rts)
        self.connection.open.side_effect = opened
        display = get_companion_display()
        self.addCleanup(display.close)
        self.assertIsInstance(display, SerialCompanionDisplay)
        self.serial.Serial.assert_called_once_with(
            port=None, baudrate=115200, timeout=0, write_timeout=WRITE_TIMEOUT_SECONDS,
            rtscts=False, dsrdtr=False,
        )
        self.sleep.assert_called_once_with(1.5)
        self.assertTrue(display.set_state(DisplayState.READY))

    def test_persistent_connection_and_exact_state_command(self):
        display = self.controller()
        for state in (DisplayState.READY, DisplayState.THINKING, DisplayState.SPEAKING):
            self.assertTrue(display.set_state(state))
        self.serial.Serial.assert_called_once()
        self.connection.open.assert_called_once()
        self.connection.close.assert_not_called()
        self.assertEqual(self.connection.write.call_args_list,
                         [call(b"STATE READY\n"), call(b"STATE THINKING\n"), call(b"STATE SPEAKING\n")])
        self.sleep.assert_not_called()
        self.connection.read.assert_not_called()
        self.connection.readline.assert_not_called()
        self.connection.flush.assert_not_called()

    def test_text_clear_ascii_and_injection_protection(self):
        display = self.controller()
        display.set_text("Hello face")
        display.clear_text()
        display.set_text("hello\nSTATE ERROR\r\x00café")
        display.set_text("x" * 200)
        display.set_text("\n\t")
        self.assertEqual(self.connection.write.call_args_list, [
            call(b"TEXT Hello face\n"), call(b"CLEAR TEXT\n"),
            call(b"TEXT hello STATE ERROR caf?\n"), call(b"TEXT " + b"x" * 40 + b"\n"), call(b"CLEAR TEXT\n"),
        ])

    def test_enum_exact_firmware_states_and_invalid_input(self):
        self.assertEqual({state.value for state in DisplayState}, {
            "BOOTING", "SETUP", "CONNECTING", "READY", "LISTENING", "THINKING",
            "SPEAKING", "OFFLINE", "ECO", "PROTECTIVE", "ERROR",
        })
        display = self.controller()
        for operation in (DisplayState, display.set_state, NoOpDisplay().set_state):
            with self.assertRaises(ValueError):
                operation("ALERT")
        self.connection.write.assert_not_called()

    def test_missing_port_permission_and_dependency_fail_safely(self):
        for failure in (FileNotFoundError("private path"), PermissionError("private details")):
            with self.subTest(failure=type(failure).__name__):
                self.connection.open.side_effect = failure
                with self.assertLogs("assistant.display", level="WARNING") as logs:
                    display = self.controller()
                self.assertFalse(display.set_state(DisplayState.READY))
                self.assertNotIn("private", str(logs.output))
                self.assertNotIn("Traceback", str(logs.output))
        with patch("assistant.display.serial_display.serial", None):
            display = self.controller()
            self.assertFalse(display.set_state(DisplayState.READY))

    def test_disconnect_and_later_reconnect_no_tight_retry_loop(self):
        display = self.controller()
        self.connection.write.side_effect = OSError("device unplugged")
        self.assertFalse(display.set_state(DisplayState.THINKING))
        for _ in range(20):
            self.assertFalse(display.set_state(DisplayState.LISTENING))
        self.serial.Serial.assert_called_once()
        self.connection.close.assert_called_once()
        self.advance(5)
        self.connection.write.side_effect = lambda payload: len(payload)
        self.assertTrue(display.set_state(DisplayState.READY))
        self.assertEqual(self.serial.Serial.call_count, 2)
        self.assertEqual(self.connection.write.call_args.args[0], b"STATE READY\n")

    def test_missing_port_later_becomes_available(self):
        self.connection.open.side_effect = OSError("absent")
        display = self.controller()
        self.connection.open.side_effect = None
        self.assertFalse(display.set_state(DisplayState.READY))
        self.advance(5)
        self.assertTrue(display.set_state(DisplayState.LISTENING))
        self.assertEqual(self.connection.open.call_count, 2)

    def test_reconnect_startup_settling_does_not_sleep_or_replay_stale_state(self):
        display = self.controller(delay=1.5)
        self.sleep.assert_called_once_with(1.5)
        self.connection.write.side_effect = OSError("unplugged")
        display.set_state(DisplayState.THINKING)
        self.advance(5)
        self.connection.write.side_effect = lambda payload: len(payload)
        self.assertFalse(display.set_state(DisplayState.ERROR))
        self.advance(1.5)
        self.assertTrue(display.set_state(DisplayState.LISTENING))
        self.sleep.assert_called_once_with(1.5)
        self.assertEqual(self.connection.write.call_args_list,
                         [call(b"STATE THINKING\n"), call(b"STATE LISTENING\n")])

    def test_short_write_is_failure_and_close_is_idempotent(self):
        display = self.controller()
        self.connection.write.side_effect = lambda payload: 1
        self.assertFalse(display.set_state(DisplayState.READY))
        display.close()
        display.close()
        self.advance(20)
        self.assertFalse(display.set_state(DisplayState.READY))
        self.serial.Serial.assert_called_once()
        self.connection.close.assert_called_once()

    def test_startup_cancellation_closes_open_port(self):
        self.sleep.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.controller(delay=1.5)
        self.connection.close.assert_called_once()

    def test_writes_are_serialized_between_threads(self):
        display = self.controller()
        first_entered, release, second_started, second_done = Event(), Event(), Event(), Event()
        count = 0
        def write(payload):
            nonlocal count
            count += 1
            if count == 1:
                first_entered.set()
                if not release.wait(1):
                    raise RuntimeError("test timed out")
            return len(payload)
        self.connection.write.side_effect = write
        def second():
            second_started.set()
            display.set_state(DisplayState.THINKING)
            second_done.set()
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(display.set_state, DisplayState.READY)
            self.assertTrue(first_entered.wait(1))
            other = executor.submit(second)
            try:
                self.assertTrue(second_started.wait(1))
                self.assertFalse(second_done.wait(0.02))
            finally:
                release.set()
            self.assertTrue(first.result())
            other.result()
        self.assertEqual(self.connection.write.call_args_list,
                         [call(b"STATE READY\n"), call(b"STATE THINKING\n")])

    @override_settings(COMPANION_DISPLAY_ENABLED=True)
    def test_invalid_configuration_falls_back_without_failure(self):
        for values in ({"COMPANION_DISPLAY_PORT": ""},
                       {"COMPANION_DISPLAY_PORT": "example", "COMPANION_DISPLAY_BAUD": "invalid"},
                       {"COMPANION_DISPLAY_PORT": "example", "COMPANION_DISPLAY_STARTUP_DELAY_SECONDS": "nan"}):
            with self.settings(**values):
                self.assertIsInstance(get_companion_display(), NoOpDisplay)
        self.serial.Serial.assert_not_called()


class DisplayCommandTests(SimpleTestCase):
    @override_settings(COMPANION_DISPLAY_ENABLED=False)
    def test_disabled_command_does_not_claim_success(self):
        with self.assertRaisesRegex(CommandError, "disabled"):
            call_command("companion_display_state", "READY", stdout=io.StringIO())

    @patch("assistant.management.commands.companion_display_state.get_companion_display")
    def test_command_uses_factory_reports_sent_and_closes(self, factory):
        output = io.StringIO()
        factory.return_value.enabled = True
        factory.return_value.set_state.return_value = True
        call_command("companion_display_state", "LISTENING", stdout=output)
        factory.return_value.set_state.assert_called_once_with(DisplayState.LISTENING)
        factory.return_value.close.assert_called_once()
        self.assertIn("Sent LISTENING", output.getvalue())
        self.assertIn("not acknowledgment-verified", output.getvalue())

    @patch("assistant.management.commands.companion_display_state.get_companion_display")
    def test_failure_command_reports_error_and_still_closes(self, factory):
        factory.return_value.enabled = True
        factory.return_value.set_state.return_value = False
        with self.assertRaisesRegex(CommandError, "could not be sent"):
            call_command("companion_display_state", "ERROR", stdout=io.StringIO())
        factory.return_value.close.assert_called_once()

    @patch("assistant.management.commands.companion_display_state.get_companion_display")
    def test_command_rejects_unknown_state_before_opening(self, factory):
        with self.assertRaises(CommandError):
            call_command("companion_display_state", "ALERT")
        factory.assert_not_called()


@override_settings(RAG_ENABLED=False, ONLINE_RETRIEVAL_ENABLED=False, DEVICE_CONTROL_ENABLED=False)
class DisplayVoiceLifecycleTests(SimpleTestCase):
    def setUp(self):
        self.events = []
        self.display = MagicMock()
        self.display.set_state.side_effect = lambda state: self.events.append(state.value)
        self.mocks = {}
        targets = ("record_audio", "record_audio_with_capture_vad", "normalize_audio", "get_stt_provider",
                   "get_tts_provider", "AssistantService.process_message", "play_audio", "prepend_leading_silence")
        for name in targets:
            patcher = patch("scripts.voice_loop." + name)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.mocks["record_audio"].side_effect = lambda *args: self.events.append("record")
        self.mocks["record_audio_with_capture_vad"].side_effect = lambda *args: self.events.append("record_vad")
        self.mocks["normalize_audio"].side_effect = lambda *args: self.events.append("normalize")
        self.stt = self.mocks["get_stt_provider"].return_value
        self.stt.transcribe.side_effect = lambda *args: self.events.append("stt") or "Explain local AI."
        self.tts = self.mocks["get_tts_provider"].return_value
        self.tts.synthesize.side_effect = lambda *args: self.events.append("tts") or b"audio"
        self.mocks["prepend_leading_silence"].return_value = b"prepared audio"
        self.mocks["play_audio"].side_effect = lambda *args: self.events.append("play")
        self.profile = "BALANCED"
        self.mocks["AssistantService.process_message"].side_effect = self.generate

    def generate(self, *args, **kwargs):
        self.events.append("ai")
        return SimpleNamespace(id=42), "First sentence. Second sentence. Third sentence.", {"resource_profile": self.profile}, None

    def cycle(self, **kwargs):
        return voice_loop.run_voice_cycle(voice_loop.VoiceLoopConfig("source", tts_chunk_chars=17, **kwargs),
                                         conversation_id=12, display=self.display)

    def test_real_lifecycle_states_at_stage_boundaries_and_speaking_once(self):
        self.assertEqual(self.cycle(), 42)
        self.assertEqual(self.events[:6], ["LISTENING", "record", "normalize", "stt", "THINKING", "ai"])
        self.assertEqual([event for event in self.events if event in {state.value for state in DisplayState}],
                         ["LISTENING", "THINKING", "SPEAKING", "READY"])
        main_events = [event for event in self.events if event != "tts"]
        self.assertEqual(main_events[main_events.index("SPEAKING") + 1], "play")
        self.assertLess(self.events.index("tts"), self.events.index("SPEAKING"))
        self.assertEqual(self.events.count("play"), 3)
        self.assertEqual(self.events[-1], "READY")

    def test_capture_vad_is_listening_before_recording(self):
        self.cycle(capture_vad_enabled=True)
        self.assertEqual(self.events[:2], ["LISTENING", "record_vad"])

    def test_exact_resource_profile_mapping(self):
        for profile, expected in (("ECO", "ECO"), ("PROTECTIVE", "PROTECTIVE"),
                                  ("PERFORMANCE", "READY"), ("BALANCED", "READY"), ("eco", "READY"),
                                  ("UNKNOWN", "READY"), (None, "READY")):
            with self.subTest(profile=profile):
                self.profile = profile
                self.cycle()
                self.assertEqual(self.events[-1], expected)

    def test_voice_stage_failures_set_error_without_ready_overwrite(self):
        operations = [self.mocks["record_audio"], self.mocks["normalize_audio"], self.stt.transcribe,
                      self.mocks["AssistantService.process_message"], self.tts.synthesize, self.mocks["play_audio"]]
        for operation in operations:
            with self.subTest(operation=operation):
                original = operation.side_effect
                operation.side_effect = RuntimeError("test failure")
                try:
                    with self.assertRaises(Exception):
                        self.cycle()
                    self.assertEqual(self.events[-1], "ERROR")
                finally:
                    operation.side_effect = original

    def test_blank_transcript_and_ai_error_result_set_error(self):
        self.stt.transcribe.side_effect = lambda *args: "[BLANK_AUDIO]"
        with self.assertRaises(voice_loop.VoiceLoopError):
            self.cycle()
        self.assertEqual(self.events[-1], "ERROR")
        self.mocks["AssistantService.process_message"].assert_not_called()
        self.stt.transcribe.side_effect = lambda *args: "Explain local AI."
        self.mocks["AssistantService.process_message"].side_effect = lambda *args, **kwargs: (None, None, None, "offline")
        with self.assertRaises(voice_loop.VoiceLoopError):
            self.cycle()
        self.assertEqual(self.events[-1], "ERROR")

    def test_next_attempt_transitions_from_error_to_listening(self):
        original = self.stt.transcribe.side_effect
        self.stt.transcribe.side_effect = RuntimeError("failure")
        with self.assertRaises(voice_loop.VoiceLoopError):
            self.cycle()
        self.events.clear()
        self.stt.transcribe.side_effect = original
        self.cycle()
        self.assertEqual(self.events[0], "LISTENING")
        self.assertEqual(self.events[-1], "READY")

    def test_unexpected_display_exceptions_do_not_change_success_or_voice_failure(self):
        self.display.set_state.side_effect = RuntimeError("private hardware failure")
        with self.assertLogs("scripts.voice_loop", level="WARNING") as logs:
            self.assertEqual(self.cycle(), 42)
        self.assertNotIn("private", str(logs.output))
        self.stt.transcribe.side_effect = RuntimeError("speech failure")
        with self.assertRaisesRegex(voice_loop.VoiceLoopError, "Transcription failed"):
            self.cycle()

    @patch("assistant.display.serial_display.serial")
    def test_missing_port_and_write_failure_do_not_fail_real_voice_cycle(self, serial):
        serial.Serial.return_value.open.side_effect = FileNotFoundError("missing")
        display = SerialCompanionDisplay(port="example", startup_delay_seconds=0)
        self.assertEqual(voice_loop.run_voice_cycle(voice_loop.VoiceLoopConfig("source"), display=display), 42)
        display.close()
        serial.Serial.return_value.open.side_effect = None
        serial.Serial.return_value.write.side_effect = OSError("disconnected")
        display = SerialCompanionDisplay(port="example", startup_delay_seconds=0)
        self.assertEqual(voice_loop.run_voice_cycle(voice_loop.VoiceLoopConfig("source"), display=display), 42)
        display.close()

    def test_optional_playback_callback_failure_does_not_stop_audio(self):
        callback = MagicMock(side_effect=OSError("display"))
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_loop.speak_speech_chunks(["First.", "Second."], self.tts, Path(temp_dir), 0.7, {}, callback)
        callback.assert_called_once()
        self.assertEqual(self.mocks["play_audio"].call_count, 2)

    @patch("scripts.voice_loop.get_companion_display")
    @patch("scripts.voice_loop.VoiceLoopConfig.from_settings")
    @patch("builtins.input", side_effect=["", "", "q"])
    def test_main_owns_one_controller_startup_ready_and_closes_once(self, user_input, config, factory):
        config.return_value = voice_loop.VoiceLoopConfig("source")
        factory.return_value = self.display
        self.assertEqual(voice_loop.main(), 0)
        factory.assert_called_once()
        self.assertEqual(self.events[0], "READY")
        self.assertEqual(self.events.count("LISTENING"), 2)
        self.assertEqual(self.events.count("READY"), 3)
        self.display.close.assert_called_once()

    @patch("scripts.voice_loop.get_companion_display")
    @patch("scripts.voice_loop.VoiceLoopConfig.from_settings")
    @patch("builtins.input", side_effect=EOFError)
    def test_main_eof_closes_controller(self, user_input, config, factory):
        config.return_value = voice_loop.VoiceLoopConfig("source")
        factory.return_value = self.display
        self.assertEqual(voice_loop.main(), 0)
        self.display.close.assert_called_once()

    @patch("scripts.voice_loop.get_companion_display")
    @patch("scripts.voice_loop.VoiceLoopConfig.from_settings")
    @patch("builtins.input", side_effect=KeyboardInterrupt)
    def test_main_interrupt_and_display_close_failure_are_safe(self, user_input, config, factory):
        config.return_value = voice_loop.VoiceLoopConfig("source")
        factory.return_value = self.display
        self.display.close.side_effect = OSError("private port details")
        with self.assertLogs("scripts.voice_loop", level="WARNING") as logs:
            self.assertEqual(voice_loop.main(), 0)
        self.display.close.assert_called_once()
        self.assertNotIn("private", str(logs.output))
