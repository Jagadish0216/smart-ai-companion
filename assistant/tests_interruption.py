"""Deterministic interruption tests. No Pi timing or acoustic claims."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
from pathlib import Path
import socket
import tempfile
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from assistant.voice.interruption import (
    EchoGuard, InterruptControl, InterruptionCleanupError, VoiceInterrupted,
    current, defer_response, delivery, interruptible_response,
)
from assistant.voice.wakeword.base import WakeWordConfig, WakeWordDetection
from assistant.voice.wakeword.openwakeword_provider import OpenWakeWordProvider
from assistant import tests_voice_stream as stream_fixtures
from assistant.tests_voice_stream import stream_events, FIRST_SENTENCE
from assistant.tests_wakeword import native_pcm
from assistant.voice.hands_free import HandsFreeCaptureConfig, record_audio_hands_free
from assistant.voice.tts import MockTTSProvider
from conversations.models import Conversation, Message
from scripts import voice_loop


class CancellationTests(SimpleTestCase):
    def test_blocked_http_read_is_woken_by_socket_shutdown(self):
        shutdown = Event()
        reader = MagicMock()
        reader.shutdown.side_effect = lambda how: shutdown.set()
        response = SimpleNamespace(fp=SimpleNamespace(raw=SimpleNamespace(_sock=reader)))
        control = InterruptControl(keyboard=False)
        began, ended = Event(), Event()
        def read():
            with delivery(control), interruptible_response(response):
                began.set()
                try:
                    self.assertTrue(shutdown.wait(2)) # Fake blocked HTTP readline.
                finally:
                    ended.set()
        worker = Thread(target=read)
        worker.start()
        self.assertTrue(began.wait(2))
        control.cancel()
        self.assertTrue(ended.wait(2))
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(control.callbacks, [])
        reader.shutdown.assert_called_once_with(socket.SHUT_RDWR)

    def test_cancel_is_once_and_late_registration_is_cancelled(self):
        control = InterruptControl(keyboard=False)
        abort = MagicMock()
        control.register(abort)
        control.cancel("listen", WakeWordDetection(True))
        control.cancel("stop")
        self.assertEqual(control.mode, "listen")
        abort.assert_called_once()
        late = MagicMock()
        control.register(late)
        late.assert_called_once()
        self.assertTrue(control.stop_event.is_set())

    def test_worker_failure_unblocks_http_without_becoming_operator_interrupt(self):
        control = InterruptControl(keyboard=False)
        abort = MagicMock()
        control.register(abort)
        control.abort_generation()
        abort.assert_called_once()
        self.assertFalse(control.interrupted.is_set())

    def test_original_http_and_delivery_modes_are_unchanged(self):
        self.assertIsNone(current())
        with interruptible_response(object()):
            pass
        self.assertFalse(defer_response(None, "answer", 1))

    def test_missing_socket_fails_before_interruptible_read(self):
        with delivery(InterruptControl(keyboard=False)), self.assertRaisesRegex(RuntimeError, "socket unavailable"):
            with interruptible_response(object()):
                self.fail("Unsafe blocking read")

    def test_unclean_monitor_prevents_reusing_capture(self):
        control = InterruptControl(keyboard=False)
        thread = MagicMock()
        thread.is_alive.return_value = True
        control.threads.append(thread)
        with self.assertRaises(InterruptionCleanupError):
            control.close()
        thread.join.assert_called_once_with(timeout=8)

    def test_duplex_failure_keeps_current_answer_and_operator_fallback(self):
        provider = MagicMock()
        provider.wait_for_wake_word.side_effect = OSError("device busy")
        control = InterruptControl(provider=provider, voice=True, keyboard=False)
        with patch("sys.stderr", new_callable=io.StringIO) as output:
            control.reference(b"\0" * 7680)
            control.close()
        self.assertIn("use Enter/s or Ctrl+C", output.getvalue())
        self.assertFalse(control.stop_event.is_set())
        provider.reset.assert_called_once()

    def test_voice_monitor_cancels_and_releases_before_handoff(self):
        detected, released = Event(), Event()
        provider = MagicMock()
        result = WakeWordDetection(True, audio_preroll=b"\0" * 8)
        def wait(device, **kwargs):
            self.assertEqual(kwargs["threshold"], .85)
            self.assertEqual(kwargs["confirmations"], 2)
            kwargs["on_detection"](result)
            detected.set()
            self.assertTrue(kwargs["cancel_event"].wait(2))
            released.set()
            return result
        provider.wait_for_wake_word.side_effect = wait
        control = InterruptControl(provider=provider, voice=True, keyboard=False)
        control.reference(b"\0" * 7680)
        self.assertTrue(detected.wait(2))
        control.close()
        self.assertTrue(released.is_set())
        self.assertEqual(control.detection, result)
        self.assertEqual(control.mode, "listen")
        self.assertTrue(control.stop_event.is_set())

    def test_monitor_cleanup_failure_is_fatal_before_next_capture(self):
        from assistant.voice.wakeword.base import WakeWordCleanupError
        provider = MagicMock()
        provider.wait_for_wake_word.side_effect = WakeWordCleanupError("unreaped microphone")
        control = InterruptControl(provider=provider, voice=True, keyboard=False)
        control.reference(b"\0" * 7680)
        with self.assertRaises(InterruptionCleanupError):
            control.close()

    def test_keyboard_stop_and_new_request(self):
        for key, mode in (("s\n", "stop"), ("\n", "stop"), ("n\n", "listen")):
            control = InterruptControl()
            with patch("assistant.voice.interruption.sys.stdin") as stdin, \
                 patch("os.name", "posix"), patch("select.select", return_value=([stdin], [], [])):
                stdin.isatty.return_value = True
                stdin.readline.return_value = key
                control.start()
                self.assertTrue(control.interrupted.wait(2))
                control.close()
            self.assertEqual(control.mode, mode)


class EchoRejectionTests(SimpleTestCase):
    def test_bounded_reference_rejects_correlated_speaker_and_missing_reference(self):
        import numpy as np
        rng = np.random.default_rng(26)
        signal = rng.integers(-10000, 10000, 16000, dtype=np.int16)
        guard = EchoGuard()
        frame = signal[4000:5280].astype("<i2").tobytes()
        self.assertFalse(guard.permits(frame))
        native = np.repeat(signal.astype(np.int32) * 65536, 3)
        stereo = np.column_stack((native, native)).astype("<i4").tobytes()
        guard.accept(stereo)
        self.assertFalse(guard.permits(frame))
        unrelated = rng.integers(-10000, 10000, 1280, dtype=np.int16).tobytes()
        self.assertTrue(guard.permits(unrelated))
        for _ in range(10):
            guard.accept(stereo)
        self.assertLessEqual(len(guard.reference), 4000)
        guard.text("Say Hey Jarvis to continue.")
        self.assertFalse(guard.permits(unrelated))
        guard.text("Another sentence.")
        self.assertFalse(guard.permits(unrelated))  # Echo tail hold.


class WakeMonitorTests(SimpleTestCase):
    def test_guard_confidence_and_consecutive_evidence_are_all_required(self):
        provider = OpenWakeWordProvider(WakeWordConfig(enabled=True))
        provider.model = MagicMock()
        provider.model.predict.side_effect = [{"hey_jarvis": score} for score in (.9, .8, .9, .9, .9)]
        capture = MagicMock()
        capture.chunks.return_value = iter([native_pcm()] * 5)
        guard = MagicMock(side_effect=[True, False, True, True])
        with patch("assistant.voice.wakeword.openwakeword_provider.AlsaWakeCapture", return_value=capture):
            result = provider.wait_for_wake_word("mic", guard=guard, threshold=.85, confirmations=2)
        self.assertTrue(result.detected)
        self.assertEqual(provider.model.predict.call_count, 5)
        capture.close.assert_called_once()

    def test_handoff_retains_opening_until_cancel_and_releases_mic(self):
        provider = OpenWakeWordProvider(WakeWordConfig(enabled=True))
        provider.model = MagicMock()
        provider.model.predict.return_value = {"hey_jarvis": .9}
        capture = MagicMock()
        chunks = [native_pcm(left=10000000 + i) for i in range(5)]
        capture.chunks.return_value = iter(chunks)
        seen = []
        with patch("assistant.voice.wakeword.openwakeword_provider.AlsaWakeCapture", return_value=capture):
            result = provider.wait_for_wake_word("mic", on_detection=seen.append)
        self.assertEqual(len(seen), 1)
        self.assertEqual(result.audio_preroll, b"".join(chunks))
        self.assertIsNone(provider.capture)
        capture.close.assert_called_once()

    def test_long_handoff_refuses_instead_of_dropping_opening(self):
        provider = OpenWakeWordProvider(WakeWordConfig(enabled=True))
        provider.model = MagicMock()
        provider.model.predict.return_value = {"hey_jarvis": .9}
        capture = MagicMock()
        capture.chunks.return_value = iter([native_pcm()] * 27)
        with patch("assistant.voice.wakeword.openwakeword_provider.AlsaWakeCapture", return_value=capture), \
             self.assertRaisesRegex(Exception, "exceeded two seconds"):
            provider.wait_for_wake_word("mic", on_detection=lambda result: None)
        capture.close.assert_called_once()


class InterruptAudioTests(SimpleTestCase):
    setUp = stream_fixtures.StreamingAudioTests.setUp
    speak = stream_fixtures.StreamingAudioTests.speak

    def test_cancel_reaches_inflight_piper_and_closes_ai_producer(self):
        began, cancelled, closed = Event(), Event(), Event()
        control = InterruptControl(keyboard=False)
        self.provider = MockTTSProvider()
        def synthesize(text, stop_event):
            began.set()
            self.assertTrue(stop_event.wait(2))
            cancelled.set()
            raise RuntimeError("cancelled synthesis")
        self.provider.synthesize_cancellable = synthesize
        def events():
            try:
                yield {"type": "start", "conversation_id": 19}
                yield {"type": "delta", "text": FIRST_SENTENCE + " "}
                self.assertTrue(began.wait(2))
                control.cancel()
                yield {"type": "error", "detail": "request disconnected"}
            finally:
                closed.set()
        with delivery(control), self.assertRaises(VoiceInterrupted):
            self.speak(events())
        self.assertTrue(cancelled.is_set())
        self.assertTrue(closed.is_set())
        self.play_audio.assert_not_called()

    def test_cancellation_stops_actual_player_path_and_discards_queue(self):
        control = InterruptControl(keyboard=False)
        def play(*args, **kwargs):
            control.cancel()
            self.assertTrue(kwargs["cancel_event"].is_set())
        self.stream_write.side_effect = play
        config = replace(self.config, audio_backend="alsa", alsa_playback_device="speaker")
        with delivery(control), self.assertRaises(VoiceInterrupted):
            self.speak(stream_events(FIRST_SENTENCE + " " + FIRST_SENTENCE), config)
        self.player.abort.assert_called_once()
        self.player.finish.assert_not_called()
        self.assertEqual(self.stream_write.call_count, 1)
        self.assertEqual(control.conversation_id, 42)

    def test_player_cleanup_failure_prevents_next_request(self):
        control = InterruptControl(keyboard=False)
        self.stream_write.side_effect = lambda *args, **kwargs: control.cancel()
        self.player.abort.side_effect = RuntimeError("unreaped player")
        config = replace(self.config, audio_backend="alsa", alsa_playback_device="speaker")
        with delivery(control), self.assertRaises(InterruptionCleanupError):
            self.speak(stream_events(FIRST_SENTENCE), config)

    def test_response_generator_closes_on_interrupt_and_no_stale_playback_follows(self):
        control = InterruptControl(keyboard=False)
        closed = Event()
        def events():
            try:
                yield {"type": "start", "conversation_id": 99}
                control.cancel()
                yield {"type": "delta", "text": "Never play this."}
            finally:
                closed.set()
        with delivery(control), self.assertRaises(VoiceInterrupted):
            self.speak(events())
        self.assertTrue(closed.is_set())
        self.provider.synthesize.assert_not_called()
        self.speak(stream_events("Fresh answer."))
        self.provider.synthesize.assert_called_once_with("Fresh answer.")

    def test_responsive_short_complete_sentence_precedes_model_done(self):
        played = Event()
        self.play_audio.side_effect = lambda *args, **kwargs: played.set()
        def events():
            yield {"type": "start", "conversation_id": 42}
            yield {"type": "delta", "text": "Guido van Rossum. "}
            self.assertTrue(played.wait(2))
            yield {"type": "delta", "text": "He created Python."}
            yield {"type": "done", "response": "Guido van Rossum. He created Python."}
        self.speak(events(), replace(self.config, stream_eager_first_sentence=True))
        self.assertEqual([call.args[0] for call in self.provider.synthesize.call_args_list],
                         ["Guido van Rossum.", "He created Python."])

    def test_responsive_delta_boundaries_preserve_complete_text(self):
        text = 'Yes. Dr. Rao explains Python. He says "It works!" Another complete thought.'
        for width in range(1, len(text) + 1):
            buffer = voice_loop.StreamingSpeechBuffer(first_sentence_min_chars=1)
            units = []
            for index in range(0, len(text), width):
                units.extend(buffer.feed(text[index:index+width]))
            units.extend(buffer.feed("", final=True))
            self.assertEqual(" ".join(units), text)


@override_settings(AI_ENGINE="mock", RAG_ENABLED=False, ONLINE_RETRIEVAL_ENABLED=False,
                   DEVICE_CONTROL_ENABLED=False, CONVERSATION_MEMORY_ENABLED=False)
class DeliveryPersistenceTests(TestCase):
    def test_success_commits_once_after_drain_and_interrupt_discards(self):
        from assistant.services import _persist_generated_response
        conversation = Conversation.objects.create()
        for interrupted in (False, True):
            control = InterruptControl(keyboard=False)
            with delivery(control):
                _persist_generated_response(conversation, "Complete answer.", 1)
                self.assertEqual(Message.objects.filter(sender="AI").count(), int(interrupted))
                if interrupted:
                    control.cancel()
                control.close()
                control.commit()
                control.commit()
        self.assertEqual(Message.objects.filter(sender="AI").count(), 1)
        self.assertEqual(control.pending, [])
        self.assertIsNone(current())

    @patch("scripts.voice_loop.get_tts_provider", return_value=MockTTSProvider())
    @patch("scripts.voice_loop.play_audio")
    def test_real_service_defers_until_playback_and_keeps_followup_context(self, play, tts):
        control = InterruptControl(keyboard=False)
        with delivery(control), redirect_stdout(io.StringIO()):
            conversation_id = voice_loop.run_voice_cycle(voice_loop.VoiceLoopConfig("source"), text="Explain local AI.")
            self.assertEqual(Message.objects.filter(sender="AI").count(), 0)
            self.assertTrue(play.called)
            control.commit()
        self.assertEqual(Message.objects.filter(sender="AI").count(), 1)
        control = InterruptControl(keyboard=False)
        play.side_effect = lambda *args, **kwargs: control.cancel()
        with delivery(control), redirect_stdout(io.StringIO()), self.assertRaises(VoiceInterrupted):
            voice_loop.run_voice_cycle(voice_loop.VoiceLoopConfig("source"), conversation_id, text="Tell me more.")
        self.assertEqual(control.conversation_id, conversation_id)
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(Message.objects.filter(sender="USER").count(), 2)
        self.assertEqual(Message.objects.filter(sender="AI").count(), 1)


class RuntimeHandoffTests(SimpleTestCase):
    def test_extended_handoff_wav_retains_initial_bytes_and_is_bounded(self):
        import wave
        from assistant.voice.hands_free import MAX_INTERRUPTION_HANDOFF_BYTES, HandsFreeCaptureError
        initial = native_pcm() * 6
        reader = MagicMock()
        reader.read_frames.side_effect = lambda count: b"\0" * count * 8
        config = HandsFreeCaptureConfig(max_seconds=.1)
        with tempfile.TemporaryDirectory() as directory, \
             patch("assistant.voice.hands_free.NativeRequestReader", return_value=reader):
            path = Path(directory) / "handoff.wav"
            record_audio_hands_free(path, "mic", config, immediate=True, initial_audio=initial,
                                    interruption_handoff=True)
            with wave.open(str(path), "rb") as wav:
                self.assertEqual(wav.readframes(len(initial)//8), initial)
                self.assertEqual(wav.getnframes(), len(initial)//8 + 4800)
            with self.assertRaises(HandsFreeCaptureError):
                record_audio_hands_free(path, "mic", config, immediate=True, initial_audio=initial)
            with self.assertRaises(HandsFreeCaptureError):
                record_audio_hands_free(path, "mic", config, immediate=True,
                                        initial_audio=b"\0" * (MAX_INTERRUPTION_HANDOFF_BYTES + 8),
                                        interruption_handoff=True)
        reader.close.assert_called_once()

    def test_new_question_reuses_conversation_without_waiting_another_wake(self):
        provider = MagicMock()
        provider.wait_for_wake_word.side_effect = [WakeWordDetection(True), KeyboardInterrupt()]
        requests = []
        def cycle(config, conversation_id, **kwargs):
            requests.append((conversation_id, kwargs))
            if len(requests) == 1:
                control = current()
                control.conversation_id = 77
                control.cancel("listen", WakeWordDetection(True, audio_preroll=b"\0"*8))
                raise VoiceInterrupted(control)
            return conversation_id
        with patch("scripts.voice_loop.run_voice_cycle", side_effect=cycle), \
             patch("scripts.voice_loop.time.sleep"), redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            voice_loop.run_hands_free(voice_loop.VoiceLoopConfig("source"), WakeWordConfig(enabled=True),
                                     provider, MagicMock(), barge_in=True)
        self.assertEqual([item[0] for item in requests], [None, 77])
        self.assertEqual(requests[1][1]["hands_free_preroll"], b"\0"*8)
        self.assertEqual(provider.wait_for_wake_word.call_count, 2)

    def test_stop_commands_do_not_dispatch_ai(self):
        for text in ("Hey Jarvis, stop.", "cancel", "Jarvis, cancel!", "stop"):
            with self.subTest(text=text), delivery(InterruptControl(keyboard=False)), \
                 patch("scripts.voice_loop.record_audio_hands_free"), patch("scripts.voice_loop.normalize_audio"), \
                 patch("scripts.voice_loop.get_stt_provider") as stt, \
                 patch("scripts.voice_loop.AssistantService.process_message_stream") as service:
                stt.return_value.transcribe.return_value = text
                with self.assertRaises(voice_loop.NoSpeechDetected):
                    voice_loop.run_voice_cycle(voice_loop.VoiceLoopConfig("source"),
                                              hands_free_capture=HandsFreeCaptureConfig(), hands_free_preroll=b"")
                service.assert_not_called()

    def test_keyboard_stop_requires_fresh_wake(self):
        provider = MagicMock()
        provider.wait_for_wake_word.side_effect = [WakeWordDetection(True), KeyboardInterrupt()]
        def cycle(*args, **kwargs):
            control = current()
            control.cancel("stop")
            raise VoiceInterrupted(control)
        with patch("scripts.voice_loop.run_voice_cycle", side_effect=cycle) as respond, \
             patch("scripts.voice_loop.time.sleep"), redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            voice_loop.run_hands_free(voice_loop.VoiceLoopConfig("source"), WakeWordConfig(enabled=True),
                                     provider, MagicMock(), barge_in=True)
        respond.assert_called_once()
        self.assertEqual(provider.wait_for_wake_word.call_count, 2)
