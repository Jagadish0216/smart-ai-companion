import io
import json
import base64
import subprocess
import multiprocessing
import signal
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Timer, enumerate as threads
import time
from types import ModuleType
import wave
from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase, TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from rest_framework import status

from conversations.models import Conversation, Message
from assistant.test_support import install_resource_report
from assistant.voice.stt import WhisperCppProvider, STTError
from assistant.voice.tts import PiperProvider, TTSError
from assistant.voice.factory import get_stt_provider, reset_voice_providers
from assistant.voice.piper_worker import worker_main
from assistant.tests_piper_support import fake_piper_worker, sigint_probe_piper_worker, wav_bytes

class VoiceProviderTests(TestCase):
    def setUp(self):
        self.stt = WhisperCppProvider(binary_path="/fake/main", model_path="/fake/model")
        self.tts = PiperProvider(binary_path="/fake/piper", voice_path="/fake/voice")

    @patch('os.path.exists', return_value=True)
    @patch('subprocess.run')
    def test_custom_threads_are_honored_with_and_without_vad(self, run, exists):
        run.return_value = MagicMock(returncode=0, stdout="A transcript.")
        for vad in (False, True):
            with self.subTest(vad=vad):
                provider = WhisperCppProvider("/fake/main", "/fake/model", threads="2", vad_enabled=vad,
                                              vad_model_path="/fake/silero.bin", timeout=41)
                self.assertEqual(provider.transcribe("/fake/audio.wav"), "A transcript.")
                command = run.call_args.args[0]
                self.assertEqual(command[command.index("-t") + 1], "2")
                self.assertEqual("--vad" in command, vad)
                self.assertEqual(run.call_args.kwargs["timeout"], 41)

    def test_invalid_thread_values_are_rejected_before_launch(self):
        for threads in (0, -1, "0", "-2", "", "no", "4.5", 4.5, True, None, "²"):
            with self.subTest(threads=threads), self.assertRaisesRegex(STTError, "positive integer"):
                WhisperCppProvider("/fake/main", "/fake/model", threads=threads)

    @override_settings(STT_ENGINE="whisper_cpp", STT_WHISPER_THREADS="invalid")
    def test_factory_rejects_invalid_thread_setting(self):
        reset_voice_providers()
        self.addCleanup(reset_voice_providers)
        with self.assertRaisesRegex(STTError, "positive integer"):
            get_stt_provider()

    @patch('os.path.exists', return_value=True)
    @patch('subprocess.run', side_effect=subprocess.TimeoutExpired("whisper", 7))
    def test_explicit_threads_preserve_timeout_error_contract(self, run, exists):
        provider = WhisperCppProvider("/fake/main", "/fake/model", timeout=7, threads=3)
        with self.assertRaisesRegex(STTError, "Transcription timed out"):
            provider.transcribe("/fake/audio.wav")
        self.assertEqual(run.call_args.kwargs["timeout"], 7)

    @patch('os.path.exists')
    @patch('subprocess.run')
    def test_whisper_cpp_success(self, mock_run, mock_exists):
        mock_exists.return_value = True
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "   Hello world!  \n  "
        mock_run.return_value = mock_result

        text = self.stt.transcribe("/fake/audio.wav")
        self.assertEqual(text, "Hello world!")
        command = mock_run.call_args.args[0]
        self.assertEqual(
            command,
            ["/fake/main", "-m", "/fake/model", "-f", "/fake/audio.wav", "-t", "4", "-nt"],
        )
        self.assertNotIn("--vad", command)

    @patch('os.path.exists', return_value=True)
    @patch('subprocess.run')
    def test_whisper_cpp_vad_flags_are_passed_when_enabled(self, mock_run, _mock_exists):
        provider = WhisperCppProvider(
            binary_path="/fake/main",
            model_path="/fake/model",
            vad_enabled=True,
            vad_model_path="/fake/silero.bin",
            vad_threshold=0.6,
            vad_min_speech_ms=300,
            vad_min_silence_ms=800,
            vad_speech_pad_ms=120,
        )
        mock_result = MagicMock(returncode=0, stdout="Hello with VAD")
        mock_run.return_value = mock_result

        text = provider.transcribe("/fake/audio.wav")

        self.assertEqual(text, "Hello with VAD")
        self.assertEqual(
            mock_run.call_args.args[0],
            [
                "/fake/main", "-m", "/fake/model", "-f", "/fake/audio.wav", "-t", "4", "-nt",
                "--vad",
                "--vad-model", "/fake/silero.bin",
                "--vad-threshold", "0.6",
                "--vad-min-speech-duration-ms", "300",
                "--vad-min-silence-duration-ms", "800",
                "--vad-speech-pad-ms", "120",
            ],
        )

    @patch('os.path.exists')
    @patch('subprocess.run')
    def test_whisper_cpp_vad_missing_model_is_clear(self, mock_run, mock_exists):
        mock_exists.side_effect = lambda path: path != "/missing/silero.bin"
        provider = WhisperCppProvider(
            binary_path="/fake/main",
            model_path="/fake/model",
            vad_enabled=True,
            vad_model_path="/missing/silero.bin",
        )

        with self.assertRaisesRegex(STTError, "VAD model not found"):
            provider.transcribe("/fake/audio.wav")

        mock_run.assert_not_called()

    @override_settings(
        STT_ENGINE='whisper_cpp',
        STT_WHISPER_BIN='/fake/main',
        STT_WHISPER_MODEL='/fake/model',
        STT_WHISPER_THREADS='3',
        STT_TIMEOUT=45,
        STT_VAD_ENABLED=True,
        STT_VAD_MODEL='/fake/silero.bin',
        STT_VAD_THRESHOLD=0.65,
        STT_VAD_MIN_SPEECH_MS=275,
        STT_VAD_MIN_SILENCE_MS=750,
        STT_VAD_SPEECH_PAD_MS=110,
    )
    def test_voice_factory_wires_vad_settings(self):
        reset_voice_providers()
        try:
            provider = get_stt_provider()
        finally:
            reset_voice_providers()

        self.assertIsInstance(provider, WhisperCppProvider)
        self.assertEqual(provider.threads, 3)
        self.assertTrue(provider.vad_enabled)
        self.assertEqual(provider.vad_model_path, '/fake/silero.bin')
        self.assertEqual(provider.vad_threshold, 0.65)
        self.assertEqual(provider.vad_min_speech_ms, 275)
        self.assertEqual(provider.vad_min_silence_ms, 750)
        self.assertEqual(provider.vad_speech_pad_ms, 110)

    @patch('os.path.exists')
    @patch('subprocess.run')
    def test_whisper_cpp_failure(self, mock_run, mock_exists):
        mock_exists.return_value = True
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Error parsing model"
        mock_run.return_value = mock_result

        with self.assertRaises(STTError):
            self.stt.transcribe("/fake/audio.wav")

    def test_piper_success(self):
        self.addCleanup(self.tts.close)
        audio = wav_bytes()
        with patch.object(self.tts, "_ensure_ready"), patch.object(self.tts, "_exchange", return_value={
            "type": "result", "request_id": 1, "wav": base64.b64encode(audio).decode(),
        }):
            self.assertEqual(self.tts.synthesize("Hello"), audio)


class PiperWorkerProtocolTests(SimpleTestCase):
    def setUp(self):
        # These protocol tests run the worker in-process; do not change the
        # test runner/parent's real Ctrl+C handler.
        patcher = patch("assistant.voice.piper_worker.signal.signal")
        self.worker_signal = patcher.start()
        self.addCleanup(patcher.stop)
        logging_patch = patch("assistant.voice.piper_worker.configure_cpu_onnx_logging")
        self.worker_logging = logging_patch.start()
        self.addCleanup(logging_patch.stop)

    def test_worker_loads_once_and_returns_complete_wav_for_multiple_requests(self):
        module = ModuleType("piper.voice")
        module.PiperVoice = MagicMock()
        def synthesize(text, wav):
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\x01\x00" * 160)
        module.PiperVoice.load.return_value.synthesize_wav.side_effect = synthesize
        connection = MagicMock()
        connection.recv_bytes.side_effect = [json.dumps(message).encode() for message in (
            {"type": "synthesize", "request_id": 1, "text": "First"},
            {"type": "synthesize", "request_id": 2, "text": "Second"}, {"type": "shutdown"},
        )]
        with patch.dict("sys.modules", {"piper": ModuleType("piper"), "piper.voice": module}):
            worker_main(connection, "configured-voice.onnx")
        module.PiperVoice.load.assert_called_once_with("configured-voice.onnx", use_cuda=False)
        self.assertEqual(module.PiperVoice.load.return_value.synthesize_wav.call_count, 2)
        messages = [json.loads(item.args[0]) for item in connection.send_bytes.call_args_list]
        self.assertEqual(messages[0], {"type": "ready"})
        self.assertEqual(messages[-1], {"type": "closed"})
        for message, request_id in zip(messages[1:3], (1, 2)):
            self.assertEqual(message["request_id"], request_id)
            with wave.open(io.BytesIO(base64.b64decode(message["wav"]))) as wav:
                self.assertEqual(wav.getnframes(), 160)
        connection.close.assert_called_once()

    def test_worker_error_codes_do_not_expose_native_exception(self):
        module = ModuleType("piper.voice")
        module.PiperVoice = MagicMock()
        module.PiperVoice.load.side_effect = RuntimeError("private-token-and-path")
        connection = MagicMock()
        with patch.dict("sys.modules", {"piper": ModuleType("piper"), "piper.voice": module}):
            worker_main(connection, "voice.onnx")
        self.assertEqual(json.loads(connection.send_bytes.call_args.args[0]),
                         {"type": "error", "request_id": None, "code": "LOAD_FAILED"})
        self.assertNotIn("private", str(connection.send_bytes.call_args_list))

    def test_worker_ignores_only_sigint_before_voice_load_and_exits_on_parent_eof(self):
        module = ModuleType("piper.voice")
        module.PiperVoice = MagicMock()

        def load(*args, **kwargs):
            self.worker_signal.assert_called_once_with(signal.SIGINT, signal.SIG_IGN)
            self.worker_logging.assert_called_once_with()
            return MagicMock()

        module.PiperVoice.load.side_effect = load
        connection = MagicMock()
        connection.recv_bytes.side_effect = EOFError
        with patch.dict("sys.modules", {"piper": ModuleType("piper"), "piper.voice": module}):
            worker_main(connection, "voice.onnx")
        self.worker_signal.assert_called_once_with(signal.SIGINT, signal.SIG_IGN)
        connection.close.assert_called_once()
        self.assertEqual(json.loads(connection.send_bytes.call_args.args[0]), {"type": "ready"})


class PiperWorkerSignalTests(SimpleTestCase):
    def start_worker(self):
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(target=sigint_probe_piper_worker, args=(child, "fake-voice"))
        process.start()
        child.close()

        def cleanup():
            parent.close()
            process.join(timeout=2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            process.close()

        self.addCleanup(cleanup)
        self.assertTrue(parent.poll(5), "Worker did not become ready")
        self.assertEqual(json.loads(parent.recv_bytes()), {"type": "ready"})
        return parent, process

    def test_spawned_worker_ignores_real_sigint_and_preserves_shutdown_ipc(self):
        parent_handler = signal.getsignal(signal.SIGINT)
        parent, process = self.start_worker()
        parent.send_bytes(json.dumps({"type": "synthesize", "request_id": 1, "text": "probe SIGINT"}).encode())
        self.assertTrue(parent.poll(5))
        response = json.loads(parent.recv_bytes())
        self.assertEqual(response["type"], "result")
        with wave.open(io.BytesIO(base64.b64decode(response["wav"]))) as wav:
            self.assertGreater(wav.getnframes(), 0)
        parent.send_bytes(json.dumps({"type": "shutdown"}).encode())
        self.assertTrue(parent.poll(5))
        self.assertEqual(json.loads(parent.recv_bytes()), {"type": "closed"})
        process.join(timeout=5)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(signal.getsignal(signal.SIGINT), parent_handler)

    def test_spawned_worker_exits_when_parent_connection_closes(self):
        parent, process = self.start_worker()
        parent.close()
        process.join(timeout=5)
        self.assertEqual(process.exitcode, 0)


class PersistentPiperLifecycleTests(SimpleTestCase):
    def setUp(self):
        for target, kwargs in (("assistant.voice.tts.worker_main", {"new": fake_piper_worker}),
                               ("assistant.voice.tts.os.path.exists", {"return_value": True})):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.provider = PiperProvider("legacy-cli", "fake-voice", timeout=5)
        self.addCleanup(self.provider.close)

    def assert_stopped(self, pid):
        self.assertIsNone(self.provider._worker)
        self.assertIsNone(self.provider._connection)
        self.assertNotIn(pid, [child.pid for child in multiprocessing.active_children()])
        self.assertFalse(any(thread.name in {"piper-ipc", "piper-shutdown"} for thread in threads()))

    def test_warm_and_normal_cancellable_syntheses_reuse_one_spawn_worker(self):
        self.provider.warm()
        worker, pid = self.provider._worker, self.provider._worker.pid
        self.provider.warm()
        self.assertEqual(self.provider.synthesize("First"), wav_bytes("First"))
        self.assertEqual(self.provider.synthesize_cancellable("Second", Event()), wav_bytes("Second"))
        self.assertIs(self.provider._worker, worker)
        self.assertEqual(self.provider._context.get_start_method(), "spawn")
        self.provider.close()
        self.provider.close()
        self.assert_stopped(pid)

    def test_preset_cancel_starts_no_worker(self):
        stopped = Event()
        stopped.set()
        with self.assertRaisesRegex(TTSError, "TTS synthesis cancelled"):
            self.provider.synthesize_cancellable("Hello", stopped)
        self.assertIsNone(self.provider._worker)

    def test_preset_cancel_stops_an_already_warmed_idle_worker(self):
        self.provider.warm()
        pid = self.provider._worker.pid
        stopped = Event()
        stopped.set()
        with self.assertRaisesRegex(TTSError, "cancelled"):
            self.provider.synthesize_cancellable("Hello", stopped)
        self.assert_stopped(pid)

    def test_idle_worker_crash_fails_current_call_then_allows_recovery(self):
        self.provider.warm()
        worker, pid = self.provider._worker, self.provider._worker.pid
        worker.kill()
        worker.join(timeout=2)
        with self.assertRaisesRegex(TTSError, "exited unexpectedly"):
            self.provider.synthesize("Next")
        self.assert_stopped(pid)
        self.assertEqual(self.provider.synthesize("Recovered"), wav_bytes("Recovered"))

    def test_cancellation_reaps_worker_and_later_request_recovers(self):
        self.provider.warm()
        pid = self.provider._worker.pid
        stopped = Event()
        timer = Timer(0.1, stopped.set)
        timer.start()
        try:
            with self.assertRaisesRegex(TTSError, "TTS synthesis cancelled"):
                self.provider.synthesize_cancellable("hang", stopped)
        finally:
            timer.cancel()
        self.assert_stopped(pid)
        self.assertEqual(self.provider.synthesize("Recovered"), wav_bytes("Recovered"))
        self.assertNotEqual(self.provider._worker.pid, pid)

    def test_timeout_terminates_and_reaps_worker(self):
        self.provider.warm()
        pid = self.provider._worker.pid
        self.provider.timeout = 0.15
        started = time.monotonic()
        with self.assertRaisesRegex(TTSError, "TTS synthesis timed out"):
            self.provider.synthesize("hang")
        self.assertLess(time.monotonic() - started, 3)
        self.assert_stopped(pid)

    def test_warmup_timeout_leaves_no_worker(self):
        self.provider.voice_path = "hang-load"
        self.provider.timeout = 0.15
        with self.assertRaisesRegex(TTSError, "timed out"):
            self.provider.warm()
        self.assert_stopped(None)

    def test_crash_stale_malformed_invalid_wav_and_worker_error_fail_safely(self):
        for text in ("crash", "stale", "malformed", "invalidwav", "error"):
            with self.subTest(text=text):
                self.provider.warm()
                pid = self.provider._worker.pid
                with self.assertRaises(TTSError):
                    self.provider.synthesize(text)
                self.assert_stopped(pid)
                self.assertEqual(self.provider.synthesize("Recovered"), wav_bytes("Recovered"))

    def test_cancelled_lock_wait_does_not_stop_someone_elses_worker(self):
        self.provider.warm()
        pid = self.provider._worker.pid
        self.provider._lock.acquire()
        stopped = Event()
        timer = Timer(0.1, stopped.set)
        timer.start()
        try:
            with self.assertRaisesRegex(TTSError, "cancelled"):
                self.provider.synthesize_cancellable("waiting", stopped)
        finally:
            self.provider._lock.release()
            timer.cancel()
        self.assertEqual(self.provider._worker.pid, pid)
        self.assertEqual(self.provider.synthesize("Still alive"), wav_bytes("Still alive"))

    def test_lock_wait_also_consumes_timeout_budget(self):
        self.provider._lock.acquire()
        self.provider.timeout = 0.1
        try:
            with self.assertRaisesRegex(TTSError, "timed out"):
                self.provider.synthesize("waiting")
        finally:
            self.provider._lock.release()

    def test_concurrent_requests_are_serialized_and_have_distinct_ids(self):
        self.provider.warm()
        pid = self.provider._worker.pid
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.provider.synthesize, ("One", "Another")))
        self.assertEqual(results, [wav_bytes("One"), wav_bytes("Another")])
        self.assertEqual(self.provider._request_id, 2)
        self.assertEqual(self.provider._worker.pid, pid)

    def test_close_interrupts_active_request_and_later_warm_can_restart(self):
        self.provider.warm()
        pid = self.provider._worker.pid
        sent = Event()
        from assistant.voice.tts import send_message as actual_send
        def send(connection, message):
            actual_send(connection, message)
            if message.get("text") == "hang":
                sent.set()
        with patch("assistant.voice.tts.send_message", side_effect=send), ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.provider.synthesize, "hang")
            self.assertTrue(sent.wait(2))
            self.provider.close()
            with self.assertRaisesRegex(TTSError, "closed"):
                future.result(timeout=2)
        self.assert_stopped(pid)
        self.provider.warm()
        self.assertNotEqual(self.provider._worker.pid, pid)

    def test_reset_cache_closes_cached_provider(self):
        from assistant.voice import factory
        self.provider.warm()
        pid = self.provider._worker.pid
        with patch.object(factory, "_tts_provider_cache", self.provider):
            reset_voice_providers()
            self.assertIsNone(factory._tts_provider_cache)
        self.assert_stopped(pid)

    def test_blocking_partial_ipc_is_cancellable_and_cannot_deliver_stale_audio(self):
        self.provider.warm()
        pid = self.provider._worker.pid
        connection = self.provider._connection
        closed, stopped = Event(), Event()
        real_close = connection.close
        def close():
            real_close()
            closed.set()
        def receive(*args):
            if not closed.wait(2):
                raise RuntimeError("test IPC was not closed")
            raise EOFError
        timer = Timer(0.1, stopped.set)
        with patch.object(connection, "recv_bytes", side_effect=receive), patch.object(connection, "close", side_effect=close):
            timer.start()
            try:
                with self.assertRaisesRegex(TTSError, "cancelled"):
                    self.provider.synthesize_cancellable("Old audio", stopped)
            finally:
                timer.cancel()
        self.assert_stopped(pid)
        self.assertEqual(self.provider.synthesize("New request"), wav_bytes("New request"))

    def test_close_uses_kill_fallback_when_terminate_does_not_stop_child(self):
        self.provider.voice_path = "hang-shutdown"
        self.provider.warm()
        worker, pid = self.provider._worker, self.provider._worker.pid
        with patch.object(worker, "terminate"), patch.object(worker, "kill", wraps=worker.kill) as kill:
            self.provider.close()
            kill.assert_called_once()
        self.assert_stopped(pid)


class VoiceAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = '/api/assistant/voice/transcribe/'
        reset_voice_providers()
        install_resource_report(self)

    def tearDown(self):
        reset_voice_providers()

    def _get_audio_file(self):
        return SimpleUploadedFile("test.webm", b"file_content", content_type="audio/webm")

    @override_settings(STT_ENGINE='mock', TTS_ENGINE='mock', AI_ENGINE='mock')
    @patch('assistant.views.subprocess.run')
    def test_voice_transcribe_success(self, mock_ffmpeg):
        """FFmpeg succeeds, mock STT/TTS/AI — full pipeline works."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_ffmpeg.return_value = mock_result

        response = self.client.post(self.url, {'audio': self._get_audio_file()}, format='multipart')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('transcription', response.data)
        self.assertIn('response', response.data)
        self.assertIn('audio_base64', response.data)
        self.assertIn('conversation_id', response.data)

        # Verify persistence
        conv = Conversation.objects.get(id=response.data['conversation_id'])
        self.assertEqual(conv.messages.count(), 2)

    @override_settings(STT_ENGINE='mock', TTS_ENGINE='mock', AI_ENGINE='mock')
    def test_missing_audio(self):
        response = self.client.post(self.url, {}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(STT_ENGINE='mock', TTS_ENGINE='mock', AI_ENGINE='mock')
    def test_empty_audio(self):
        empty_file = SimpleUploadedFile("empty.webm", b"", content_type="audio/webm")
        response = self.client.post(self.url, {'audio': empty_file}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(STT_ENGINE='mock', TTS_ENGINE='mock', AI_ENGINE='mock')
    @patch('assistant.views.subprocess.run')
    @patch('assistant.views.get_stt_provider')
    def test_stt_failure(self, mock_get_stt, mock_ffmpeg):
        mock_ff_result = MagicMock()
        mock_ff_result.returncode = 0
        mock_ffmpeg.return_value = mock_ff_result

        mock_stt = MagicMock()
        mock_stt.transcribe.side_effect = Exception("Mock STT Error")
        mock_get_stt.return_value = mock_stt

        response = self.client.post(self.url, {'audio': self._get_audio_file()}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn("couldn't understand", response.data['error'])

    @override_settings(STT_ENGINE='mock', TTS_ENGINE='mock', AI_ENGINE='local')
    @patch('assistant.views.subprocess.run')
    @patch('assistant.ai_engine.local.urllib.request.urlopen')
    def test_ai_engine_failure(self, mock_urlopen, mock_ffmpeg):
        mock_ff_result = MagicMock()
        mock_ff_result.returncode = 0
        mock_ffmpeg.return_value = mock_ff_result

        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        response = self.client.post(self.url, {'audio': self._get_audio_file()}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data['error'], "AI unavailable")

    @override_settings(STT_ENGINE='mock', TTS_ENGINE='mock', AI_ENGINE='mock')
    @patch('assistant.views.subprocess.run')
    @patch('assistant.views.get_tts_provider')
    def test_tts_failure(self, mock_get_tts, mock_ffmpeg):
        mock_ff_result = MagicMock()
        mock_ff_result.returncode = 0
        mock_ffmpeg.return_value = mock_ff_result

        mock_tts = MagicMock()
        mock_tts.synthesize.side_effect = Exception("Mock TTS Error")
        mock_get_tts.return_value = mock_tts

        response = self.client.post(self.url, {'audio': self._get_audio_file()}, format='multipart')
        # TTS failure now returns 200 with tts_error field + text response
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('tts_error', response.data)
        self.assertIn('transcription', response.data)
        self.assertIn('response', response.data)

        # User message and AI message should still be saved
        self.assertEqual(Message.objects.count(), 2)

    @override_settings(STT_ENGINE='mock', TTS_ENGINE='mock', AI_ENGINE='mock')
    @patch('assistant.views.subprocess.run')
    def test_ffmpeg_failure_returns_error(self, mock_ffmpeg):
        """When FFmpeg fails, the API returns a clear error instead of silently continuing."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = b"Unknown format"
        mock_ffmpeg.return_value = mock_result

        response = self.client.post(self.url, {'audio': self._get_audio_file()}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn("could not be processed", response.data['error'])

    @override_settings(STT_ENGINE='mock', TTS_ENGINE='mock', AI_ENGINE='mock')
    @patch('assistant.views.subprocess.run', side_effect=FileNotFoundError)
    def test_ffmpeg_not_found_returns_error(self, mock_ffmpeg):
        """When FFmpeg binary is missing, the API returns a clear error."""
        response = self.client.post(self.url, {'audio': self._get_audio_file()}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn("ffmpeg", response.data['error'].lower())
