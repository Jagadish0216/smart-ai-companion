"""Persistent ALSA byte-stream tests; no audio device or Piper installation."""

from pathlib import Path
from queue import Full, Queue
import struct
import subprocess
import sys
import tempfile
from threading import Event, Timer, enumerate as threads
from unittest import skipUnless
from unittest.mock import MagicMock, call, patch

from django.test import SimpleTestCase, override_settings

from assistant.voice import alsa_stream
from assistant.voice.processes import AudioCancelledError
from assistant.tests_voice_stream import FIRST_SENTENCE, SECOND_SENTENCE, stream_events
from scripts import voice_loop

DEVICE = "hw:CARD=sndrpigooglevoi,DEV=0"


def pcm(amplitude, frames):
    return struct.pack("<ii", amplitude, -amplitude) * frames


class PcmEdgeTests(SimpleTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name) / "source.pcm"
        self.destination = Path(directory.name) / "trimmed.pcm"

    def trim(self, audio, leading=True):
        self.source.write_bytes(audio)
        metadata = alsa_stream.trim_pcm_edges(self.source, self.destination, trim_leading=leading, cancel_event=Event())
        return self.destination.read_bytes(), metadata

    def test_excess_silence_is_trimmed_but_every_speech_sample_is_preserved(self):
        speech = b"".join(pcm(2**25 + index, 1) for index in range(4800))
        trimmed, edges = self.trim(pcm(0, 48000) + speech + pcm(0, 48000))
        self.assertEqual(edges, (1440, 1440))
        self.assertEqual(trimmed, pcm(0, 1440) + speech + pcm(0, 1440))

    def test_first_chunk_leading_audio_is_not_trimmed(self):
        speech = pcm(2**25, 4800)
        leading = pcm(0, 9600)
        trimmed, edges = self.trim(leading + speech + pcm(0, 24000), leading=False)
        self.assertEqual(trimmed, leading + speech + pcm(0, 1440))
        self.assertEqual(edges, (9600, 1440))

    def test_quiet_phonemes_beside_speech_are_retained_bit_for_bit(self):
        quiet_onset, quiet_end = pcm(100, 960), pcm(120, 960)
        speech = quiet_onset + pcm(2**25, 4800) + quiet_end
        trimmed, _ = self.trim(pcm(0, 48000) + speech + pcm(0, 48000))
        self.assertIn(speech, trimmed)

    def test_short_edges_all_quiet_audio_and_internal_pauses_are_not_deleted(self):
        for audio in (pcm(0, 48000), pcm(100, 48000),
                      pcm(0, 480) + pcm(2**25, 960) + pcm(0, 480),
                      pcm(2**25, 960) + pcm(0, 9600) + pcm(2**25, 960)):
            with self.subTest(size=len(audio)):
                self.assertEqual(self.trim(audio)[0], audio)

    def test_stereo_either_channel_protects_speech(self):
        speech = struct.pack("<ii", 0, 2**25) * 4800
        trimmed, _ = self.trim(pcm(0, 48000) + speech + pcm(0, 48000))
        self.assertIn(speech, trimmed)

    def test_invalid_pcm_and_cancelled_trim_fail(self):
        for audio in (b"", b"not a complete frame"):
            self.source.write_bytes(audio)
            with self.assertRaises(alsa_stream.AlsaStreamError):
                alsa_stream.trim_pcm_edges(self.source, self.destination, trim_leading=True, cancel_event=Event())
        self.source.write_bytes(pcm(0, 960))
        stopped = Event()
        stopped.set()
        with self.assertRaises(AudioCancelledError):
            alsa_stream.trim_pcm_edges(self.source, self.destination, trim_leading=True, cancel_event=stopped)


class PersistentPlayerTests(SimpleTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.stopped = Event()
        self.child = MagicMock()
        self.child.stdin.fileno.return_value = 123
        self.child.poll.return_value = None
        self.child.returncode = None
        self.written = []
        patcher = patch("assistant.voice.alsa_stream.subprocess.Popen", return_value=self.child)
        self.popen = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("assistant.voice.alsa_stream.os.set_blocking", create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("assistant.voice.alsa_stream.os.write", side_effect=self.write)
        self.os_write = patcher.start()
        self.addCleanup(patcher.stop)
        self.player = alsa_stream.PersistentAlsaPlayer(DEVICE, self.stopped, self.directory)
        self.addCleanup(self.player.abort)
        def wait(**kwargs):
            self.child.poll.return_value = 0
            self.child.returncode = 0
            return 0
        self.child.wait.side_effect = wait

    def write(self, fd, data):
        self.assertEqual(fd, 123)
        self.written.append(bytes(data))
        self.assertLessEqual(len(data), alsa_stream.COPY_BYTES)
        return len(data)

    def test_native_raw_command_one_process_and_ordered_stdin(self):
        self.player.start()
        for index in (1, 2, 3):
            path = self.directory / f"chunk-{index}.pcm"
            path.write_bytes(pcm(2**25 + index, 480))
            self.player.write_file(path)
        self.player.finish()
        self.popen.assert_called_once()
        self.assertEqual(self.popen.call_args.args[0],
                         ["aplay", "-D", DEVICE, "-t", "raw", "-f", "S32_LE", "-r", "48000", "-c", "2"])
        self.assertEqual(b"".join(self.written), b"".join(pcm(2**25 + index, 480) for index in (1, 2, 3)))
        self.child.stdin.close.assert_called()
        self.child.wait.assert_called()
        self.child.terminate.assert_not_called()
        self.assertIsNone(self.player.stderr)

    def test_half_second_silence_exact_size_and_bounded_writes(self):
        self.player.start()
        self.player.write_silence(24000)
        self.assertEqual(b"".join(self.written), b"\x00" * 192000)

    def test_partial_pipe_writes_do_not_lose_or_duplicate_data(self):
        received = []
        def partial_write(fd, data):
            count = min(13, len(data))
            received.append(bytes(data[:count]))
            return count
        self.os_write.side_effect = partial_write
        self.player.start()
        audio = pcm(2**25, 100)
        self.player.write(audio)
        self.assertEqual(b"".join(received), audio)

    def test_absent_progress_preserves_original_file_writes_without_metadata_lookup(self):
        path = self.directory / "audio.pcm"
        audio = pcm(2**25, 20000)
        path.write_bytes(audio)
        self.player.start()
        with patch("assistant.voice.alsa_stream.os.fstat") as metadata:
            self.player.write_file(path)
        metadata.assert_not_called()
        self.assertEqual(b"".join(self.written), audio)
        self.assertEqual([len(block) for block in self.written], [65536, 65536, 28928])

    def test_progress_reports_only_successful_partial_writes_with_exact_total(self):
        path = self.directory / "audio.pcm"
        audio = pcm(2**25, 20000)
        path.write_bytes(audio)
        received, reports = [], []
        blocked = False

        def write(fd, data):
            nonlocal blocked
            if not blocked:
                blocked = True
                raise BlockingIOError
            count = min(4096, len(data))
            received.append(bytes(data[:count]))
            return count

        def progress(written, total):
            self.assertEqual(written, sum(map(len, received)))
            self.assertEqual(total, len(audio))
            reports.append(written)

        self.os_write.side_effect = write
        self.player.start()
        self.player.write_file(path, on_progress=progress)
        self.assertEqual(b"".join(received), audio)
        self.assertEqual(reports[-1], len(audio))
        self.assertTrue(all(a < b for a, b in zip(reports, reports[1:])))
        self.assertEqual(len(reports), len(received))

    def test_progress_exception_is_generic_nonfatal_and_disables_observer_for_file(self):
        path = self.directory / "audio.pcm"
        audio = pcm(2**25, 20000)
        path.write_bytes(audio)
        callback = MagicMock(side_effect=RuntimeError("private path and answer"))
        self.player.start()
        with self.assertLogs("assistant.voice.alsa_stream", level="WARNING") as logs:
            self.player.write_file(path, on_progress=callback)
        callback.assert_called_once_with(65536, len(audio))
        self.assertEqual(b"".join(self.written), audio)
        self.assertEqual(logs.output, [
            "WARNING:assistant.voice.alsa_stream:Playback progress update failed; audio continues",
        ])

    def test_progress_does_not_bypass_cancellation_deadline_or_broken_pipe(self):
        path = self.directory / "audio.pcm"
        path.write_bytes(pcm(2**25, 20000))
        self.player.start()
        callback = MagicMock(side_effect=lambda *args: self.stopped.set())
        with self.assertRaises(AudioCancelledError):
            self.player.write_file(path, on_progress=callback)
        callback.assert_called_once()
        self.assertEqual(sum(map(len, self.written)), 65536)
        self.stopped.clear()
        self.written.clear()

        def expire(*args):
            self.player.deadline = 0

        with self.assertRaisesRegex(alsa_stream.AlsaStreamError, "bounded playback timeout"):
            self.player.write_file(path, on_progress=expire)
        self.assertEqual(sum(map(len, self.written)), 65536)
        self.player.deadline = None
        self.os_write.side_effect = BrokenPipeError
        callback = MagicMock()
        with self.assertRaisesRegex(alsa_stream.AlsaStreamError, "stdin closed"):
            self.player.write_file(path, on_progress=callback)
        callback.assert_not_called()

    def test_missing_binary_and_device_setup_error_clean_up(self):
        self.popen.side_effect = FileNotFoundError
        with self.assertRaisesRegex(alsa_stream.AlsaStreamError, "aplay was not found"):
            self.player.start()
        self.assertIsNone(self.player.stderr)
        self.popen.side_effect = None
        with patch("assistant.voice.alsa_stream.os.set_blocking", create=True, side_effect=OSError("pipe setup failed")):
            with self.assertRaisesRegex(OSError, "pipe setup failed"):
                self.player.start()
        self.child.terminate.assert_called_once()
        self.child.wait.assert_called()
        self.child.stdin.close.assert_called()

    def test_unexpected_exit_and_nonzero_final_status_are_not_success(self):
        self.player.start()
        self.player.stderr.write(b"ALSA device unavailable")
        self.player.stderr.flush()
        self.child.poll.return_value = 1
        self.child.returncode = 1
        with self.assertRaisesRegex(alsa_stream.AlsaStreamError, "exited unexpectedly.*ALSA device unavailable"):
            self.player.write(pcm(2**25, 10))
        self.child.poll.return_value = None
        def failed_wait(**kwargs):
            self.child.poll.return_value = 2
            self.child.returncode = 2
            return 2
        self.child.wait.side_effect = failed_wait
        with self.assertRaisesRegex(alsa_stream.AlsaStreamError, "aplay exit 2"):
            self.player.finish()
        self.assertIsNone(self.player.stderr)

    def test_broken_pipe_cancelled_backpressure_and_kill_fallback(self):
        self.player.start()
        self.os_write.side_effect = BrokenPipeError
        with self.assertRaisesRegex(alsa_stream.AlsaStreamError, "stdin closed"):
            self.player.write(pcm(2**25, 10))
        def blocked(fd, data):
            self.stopped.set()
            raise BlockingIOError
        self.os_write.side_effect = blocked
        with self.assertRaises(AudioCancelledError):
            self.player.write(pcm(2**25, 10))
        self.child.wait.side_effect = [subprocess.TimeoutExpired("aplay", 2), 0]
        self.player.abort()
        self.child.kill.assert_called_once()
        self.child.stdin.close.assert_called()
        self.child.poll.return_value = 0
        self.child.wait.side_effect = None

    def test_deadline_and_cancelled_final_drain_are_bounded(self):
        self.player.start()
        with patch("assistant.voice.alsa_stream.time.monotonic", return_value=self.player.deadline + 1):
            with self.assertRaisesRegex(alsa_stream.AlsaStreamError, "bounded playback timeout"):
                self.player.check()
        def draining(**kwargs):
            self.stopped.set()
            raise subprocess.TimeoutExpired("aplay", 0.1)
        def reap(**kwargs):
            self.child.poll.return_value = 0
            return 0
        calls = 0
        def wait(**kwargs):
            nonlocal calls
            calls += 1
            return draining(**kwargs) if calls == 1 else reap(**kwargs)
        self.child.wait.side_effect = wait
        with self.assertRaises(AudioCancelledError):
            self.player.finish()
        self.child.terminate.assert_called_once()
        self.assertIsNone(self.player.stderr)


class PersistentPipelineTests(SimpleTestCase):
    def setUp(self):
        self.config = voice_loop.VoiceLoopConfig("", audio_backend="alsa", leading_silence_seconds=0.5)
        self.provider = MagicMock()
        self.provider.synthesize.return_value = b"Piper WAV fixture"
        self.timings = {}
        self.states = []

    def speak(self, events):
        with tempfile.TemporaryDirectory() as directory:
            result = voice_loop.speak_stream_response(events, self.provider, Path(directory), self.config, self.timings,
                                                      voice_loop.time.perf_counter(), lambda text: self.states.append("SPEAKING"))
        self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in threads()))
        return result

    @patch("assistant.voice.alsa_stream.os.set_blocking", create=True)
    @patch("assistant.voice.alsa_stream.os.write")
    @patch("assistant.voice.alsa_stream.subprocess.Popen")
    @patch("scripts.voice_loop.prepare_alsa_playback")
    def test_real_persistent_player_pipeline_bytes_silence_trim_and_single_process(self, convert, popen, write, blocking):
        speech1, speech2 = pcm(2**25, 4800), pcm(2**26, 4800)
        source = [speech1 + pcm(0, 48000), pcm(0, 48000) + speech2 + pcm(0, 48000)]
        def conversion(input_path, output, gain, **kwargs):
            self.assertTrue(kwargs["raw_pcm"])
            self.assertEqual(gain, 4)
            output.write_bytes(source.pop(0))
        convert.side_effect = conversion
        child = popen.return_value
        child.poll.return_value = None
        child.stdin.fileno.return_value = 123
        def wait(**kwargs):
            child.poll.return_value = 0
            return 0
        child.wait.side_effect = wait
        received = []
        write.side_effect = lambda fd, data: received.append(bytes(data)) or len(data)
        self.assertEqual(self.speak(stream_events(FIRST_SENTENCE + " " + SECOND_SENTENCE))[0], 42)
        expected = (pcm(0, 24000) + speech1 + pcm(0, 1440)
                    + pcm(0, 2880) + pcm(0, 1440) + speech2 + pcm(0, 1440))
        self.assertEqual(b"".join(received), expected)
        popen.assert_called_once()
        self.assertEqual(self.states, ["SPEAKING"])
        self.assertEqual(self.timings["playback_chunks"], 2)
        child.terminate.assert_not_called()
        child.stdin.close.assert_called()

    @patch("scripts.voice_loop.PersistentAlsaPlayer")
    @patch("scripts.voice_loop.trim_pcm_edges", return_value=(0, 7200))
    @patch("scripts.voice_loop.prepare_alsa_playback")
    def test_retained_trailing_silence_prevents_double_pause(self, convert, trim, player):
        self.speak(stream_events(FIRST_SENTENCE + " " + SECOND_SENTENCE))
        self.assertEqual(player.return_value.write_silence.call_args_list, [call(24000), call(0)])

    def test_ready_queue_has_exactly_zero_starvation(self):
        second_ready = Event()
        class ObservedQueue(Queue):
            def put(queue, item, *args, **kwargs):
                super().put(item, *args, **kwargs)
                if isinstance(item, voice_loop.PreparedAudio) and item.number == 2:
                    second_ready.set()
        def consume(path):
            if path.name.endswith("001.pcm"):
                self.assertTrue(second_ready.wait(2))
        with patch("scripts.voice_loop.PersistentAlsaPlayer") as player, patch("scripts.voice_loop.Queue", ObservedQueue), \
                patch("scripts.voice_loop.prepare_alsa_playback"), patch("scripts.voice_loop.trim_pcm_edges", return_value=(0, 0)):
            player.return_value.write_file.side_effect = consume
            self.speak(stream_events(FIRST_SENTENCE + " " + SECOND_SENTENCE))
        self.assertEqual(self.timings["playback_starvation_ms"], 0)
        self.assertEqual(self.timings["max_playback_starvation_ms"], 0)

    def test_late_queue_item_measures_wait_not_intentional_silence_or_eof(self):
        first_consumed, starving = Event(), Event()
        clock = [0]
        class ObservedQueue(Queue):
            def get(queue, block=True, timeout=None):
                if block and first_consumed.is_set():
                    starving.set()
                return super().get(block=block, timeout=timeout)
        def consume(path):
            first_consumed.set()
        def events():
            yield {"type": "start", "direct": False, "conversation_id": 42}
            yield {"type": "delta", "text": FIRST_SENTENCE + " "}
            self.assertTrue(starving.wait(2))
            clock[0] = 0.075
            yield {"type": "delta", "text": SECOND_SENTENCE + " "}
            yield {"type": "done", "response": FIRST_SENTENCE + " " + SECOND_SENTENCE + " ", "conversation_id": 42}
        with patch("scripts.voice_loop.PersistentAlsaPlayer") as player, patch("scripts.voice_loop.Queue", ObservedQueue), \
                patch("scripts.voice_loop.prepare_alsa_playback"), patch("scripts.voice_loop.trim_pcm_edges", return_value=(0, 0)), \
                patch("scripts.voice_loop.time.perf_counter", side_effect=lambda: clock[0]):
            player.return_value.write_file.side_effect = consume
            self.speak(events())
        self.assertAlmostEqual(self.timings["playback_starvation_ms"], 75)
        self.assertAlmostEqual(self.timings["max_playback_starvation_ms"], 75)

    @patch("scripts.voice_loop.PersistentAlsaPlayer")
    @patch("scripts.voice_loop.trim_pcm_edges", return_value=(0, 0))
    @patch("scripts.voice_loop.prepare_alsa_playback")
    def test_failure_to_start_write_or_drain_is_voice_error_and_cleans_workers(self, convert, trim, player):
        for operation in ("start", "write_file", "finish"):
            with self.subTest(operation=operation):
                player.return_value.reset_mock()
                failed = getattr(player.return_value, operation)
                failed.side_effect = alsa_stream.AlsaStreamError("aplay " + operation + " failed")
                with self.assertRaisesRegex(voice_loop.VoiceLoopError, "aplay " + operation + " failed"):
                    self.speak(stream_events("A sentence."))
                player.return_value.abort.assert_called()
                failed.side_effect = None
                self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in threads()))

    @override_settings(VOICE_STREAM_INTER_CHUNK_SILENCE_SECONDS="0.12", VOICE_AUDIO_BACKEND="alsa", VOICE_INPUT_SOURCE="")
    def test_setting_default_and_finite_bounded_validation(self):
        self.assertEqual(voice_loop.VoiceLoopConfig.from_settings().stream_inter_chunk_silence_seconds, 0.12)
        for value in (-1, 2, float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(voice_loop.VoiceLoopError):
                voice_loop.VoiceLoopConfig("source", stream_inter_chunk_silence_seconds=value).validate()

    def test_persistent_playback_failure_unblocks_full_audio_and_text_queues(self):
        full_text, full_audio = Event(), Event()
        queues = []
        class ObservedQueue(Queue):
            def put(queue, item, *args, **kwargs):
                try:
                    super().put(item, *args, **kwargs)
                except Full:
                    (full_text if queue is queues[0] else full_audio).set()
                    raise
        def factory(*args, **kwargs):
            queues.append(ObservedQueue(*args, **kwargs))
            return queues[-1]
        def fail_write(path):
            self.assertTrue(full_text.wait(2))
            self.assertTrue(full_audio.wait(2))
            raise alsa_stream.AlsaStreamError("persistent player failed")
        with patch("scripts.voice_loop.PersistentAlsaPlayer") as player, patch("scripts.voice_loop.Queue", side_effect=factory), \
                patch("scripts.voice_loop.prepare_alsa_playback"), patch("scripts.voice_loop.trim_pcm_edges", return_value=(0, 0)):
            player.return_value.write_file.side_effect = fail_write
            with self.assertRaisesRegex(voice_loop.VoiceLoopError, "persistent player failed"):
                self.speak(stream_events("Another sentence. " * 100))
        player.assert_called_once()
        player.return_value.abort.assert_called()
        self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in threads()))

    @override_settings(RAG_ENABLED=False)
    def test_ai_failure_before_or_after_speech_sets_error_and_closes_player(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                active = Event()
                display = MagicMock()
                with patch("scripts.voice_loop.PersistentAlsaPlayer") as player, \
                        patch("scripts.voice_loop.prepare_alsa_playback"), \
                        patch("scripts.voice_loop.trim_pcm_edges", return_value=(0, 0)), \
                        patch("scripts.voice_loop.record_audio_alsa"), patch("scripts.voice_loop.normalize_audio"), \
                        patch("scripts.voice_loop.get_stt_provider") as stt, \
                        patch("scripts.voice_loop.get_tts_provider", return_value=self.provider), \
                        patch("scripts.voice_loop.AssistantService.process_message_stream") as service:
                    stt.return_value.transcribe.return_value = "A question."
                    def factory(device, stopped, path, *, on_amplitude=None):
                        def write(raw_path, *, on_progress=None):
                            active.set()
                            self.assertTrue(stopped.wait(2))
                            raise AudioCancelledError("cancelled")
                        player.return_value.write_file.side_effect = write
                        return player.return_value
                    player.side_effect = factory
                    def events():
                        yield {"type": "start", "direct": False, "conversation_id": 42}
                        if partial:
                            yield {"type": "delta", "text": FIRST_SENTENCE + " "}
                            self.assertTrue(active.wait(2))
                        yield {"type": "error", "detail": "AI disconnected"}
                    service.return_value = events()
                    with self.assertRaisesRegex(voice_loop.VoiceLoopError, "AI disconnected"):
                        voice_loop.run_voice_cycle(self.config, display=display)
                states = [item.args[0].value for item in display.set_state.call_args_list]
                self.assertEqual(states, ["LISTENING", "THINKING"] + (["SPEAKING"] if partial else []) + ["ERROR"])
                display.set_level.assert_called_with(0)
                if partial:
                    player.assert_called_once()
                    player.return_value.abort.assert_called()
                else:
                    player.assert_not_called()
                self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in threads()))

    def test_ffmpeg_and_piper_failures_do_not_start_playback(self):
        for stage in ("ffmpeg", "piper"):
            with self.subTest(stage=stage), patch("scripts.voice_loop.PersistentAlsaPlayer") as player, \
                    patch("scripts.voice_loop.prepare_alsa_playback") as convert:
                self.provider.synthesize.side_effect = RuntimeError("Piper failure") if stage == "piper" else None
                convert.side_effect = RuntimeError("FFmpeg failure") if stage == "ffmpeg" else None
                with self.assertRaises(voice_loop.VoiceLoopError):
                    self.speak(stream_events("A sentence."))
                player.assert_not_called()
                self.assertFalse(any(thread.name.startswith("voice-stream-audio") for thread in threads()))


@skipUnless(hasattr(alsa_stream.os, "set_blocking"), "Requires Unix nonblocking pipe support")
class RealPipeTests(SimpleTestCase):
    def test_real_nonblocking_pipe_normal_eof_and_cancelled_stalled_reader(self):
        original_popen = subprocess.Popen
        for stalled in (False, True):
            with self.subTest(stalled=stalled), tempfile.TemporaryDirectory() as directory:
                stopped = Event()
                children = []
                command = "import time; time.sleep(30)" if stalled else "import sys; sys.stdin.buffer.read()"
                def launch(_command, **kwargs):
                    child = original_popen([sys.executable, "-c", command], **kwargs)
                    children.append(child)
                    return child
                player = alsa_stream.PersistentAlsaPlayer(DEVICE, stopped, Path(directory), timeout=5)
                timer = Timer(0.2, stopped.set) if stalled else None
                try:
                    with patch("assistant.voice.alsa_stream.subprocess.Popen", side_effect=launch):
                        player.start()
                        if timer:
                            timer.start()
                            with self.assertRaises(AudioCancelledError):
                                player.write_silence(48000)
                        else:
                            player.write_silence(24000)
                            player.finish()
                finally:
                    player.abort()
                    if timer:
                        timer.cancel()
                        timer.join()
                self.assertIsNotNone(children[0].poll())
                self.assertTrue(children[0].stdin.closed)
