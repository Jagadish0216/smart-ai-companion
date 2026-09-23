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
from assistant.voice.factory import reset_voice_providers

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
    def test_voice_transcribe_success(self):
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
    @patch('assistant.views.get_stt_provider')
    def test_stt_failure(self, mock_get_stt):
        mock_stt = MagicMock()
        mock_stt.transcribe.side_effect = Exception("Mock STT Error")
        mock_get_stt.return_value = mock_stt

        response = self.client.post(self.url, {'audio': self._get_audio_file()}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn("Mock STT Error", response.data['detail'])

    @override_settings(STT_ENGINE='mock', TTS_ENGINE='mock', AI_ENGINE='local')
    @patch('assistant.ai_engine.local.urllib.request.urlopen')
    def test_ai_engine_failure(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        response = self.client.post(self.url, {'audio': self._get_audio_file()}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn("unavailable", response.data['detail'].lower())

    @override_settings(STT_ENGINE='mock', TTS_ENGINE='mock', AI_ENGINE='mock')
    @patch('assistant.views.get_tts_provider')
    def test_tts_failure(self, mock_get_tts):
        mock_tts = MagicMock()
        mock_tts.synthesize.side_effect = Exception("Mock TTS Error")
        mock_get_tts.return_value = mock_tts

        response = self.client.post(self.url, {'audio': self._get_audio_file()}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

        # User message and AI message should still be saved even if TTS fails
        self.assertEqual(Message.objects.count(), 2)
