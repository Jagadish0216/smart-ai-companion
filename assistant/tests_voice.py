import io
import json
import base64
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from rest_framework import status

from conversations.models import Conversation, Message
from assistant.voice.stt import WhisperCppProvider, STTError
from assistant.voice.tts import PiperProvider, TTSError
from assistant.voice.factory import get_stt_provider, reset_voice_providers

class VoiceProviderTests(TestCase):
    def setUp(self):
        self.stt = WhisperCppProvider(binary_path="/fake/main", model_path="/fake/model")
        self.tts = PiperProvider(binary_path="/fake/piper", voice_path="/fake/voice")

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
            ["/fake/main", "-m", "/fake/model", "-f", "/fake/audio.wav", "-nt"],
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
                "/fake/main", "-m", "/fake/model", "-f", "/fake/audio.wav", "-nt",
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

    @patch('os.path.exists')
    @patch('subprocess.run')
    def test_piper_success(self, mock_run, mock_exists):
        mock_exists.return_value = True
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = b"WAV_DATA"
        mock_run.return_value = mock_result

        audio = self.tts.synthesize("Hello")
        self.assertEqual(audio, b"WAV_DATA")


class VoiceAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = '/api/assistant/voice/transcribe/'
        reset_voice_providers()

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
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn("could not generate", response.data['error'].lower())

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
