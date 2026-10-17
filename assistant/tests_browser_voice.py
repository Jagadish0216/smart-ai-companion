"""Browser dictation endpoints; no real microphone, Whisper, Piper or inference."""
import base64
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from conversations.models import Conversation, Message
from .test_support import install_resource_report
from .tests_streaming import FakeStreamingEngine


class BrowserVoiceAPITests(TestCase):
    stt_url = '/api/assistant/voice/transcribe-only/'
    tts_url = '/api/assistant/voice/synthesize/'

    def setUp(self):
        self.client = APIClient()
        self.ffmpeg = patch('assistant.views.subprocess.run', return_value=MagicMock(returncode=0)).start()
        self.stt = patch('assistant.views.get_stt_provider').start()
        self.tts = patch('assistant.views.get_tts_provider').start()
        self.ai = patch('assistant.views.AssistantService.process_message').start()
        self.stream_patcher = patch('assistant.views.AssistantService.process_message_stream')
        self.stream = self.stream_patcher.start()
        self.addCleanup(patch.stopall)
        self.stt.return_value.transcribe.return_value = '  What is Raspberry Pi?\n'

    def upload(self, content=b'controlled-recording', mime='audio/webm;codecs=opus'):
        return self.client.post(self.stt_url, {'audio': SimpleUploadedFile('recording.webm', content, content_type=mime)}, format='multipart')

    def test_transcription_is_exact_and_has_no_ai_tts_or_persistence(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'transcription': '  What is Raspberry Pi?\n'})
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertIn('conversion;dur=', response['Server-Timing'])
        self.assertIn('whisper;dur=', response['Server-Timing'])
        self.ai.assert_not_called()
        self.stream.assert_not_called()
        self.tts.assert_not_called()
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(Message.objects.count(), 0)
        command = self.ffmpeg.call_args.args[0]
        self.assertIn('16000', command)
        self.assertIn('pcm_s16le', command)
        self.assertTrue(command[command.index('-i') + 1].endswith('.webm'))
        self.assertFalse(Path(command[-1]).exists())
        self.assertFalse(Path(command[command.index('-i') + 1]).exists())

    def test_missing_empty_oversized_audio_rejected_before_conversion(self):
        self.assertEqual(self.client.post(self.stt_url, {}, format='multipart').status_code, 400)
        for value in (b'', b'x' * (5 * 1024 * 1024 + 1)):
            with self.subTest(size=len(value)):
                self.assertEqual(self.upload(value).status_code, 400)
        self.ffmpeg.assert_not_called()
        self.stt.assert_not_called()

    def test_invalid_container_missing_converter_and_conversion_timeout(self):
        self.ffmpeg.return_value = MagicMock(returncode=1, stderr=b'invalid audio')
        self.assertEqual(self.upload(b'not audio').status_code, 500)
        for error in (FileNotFoundError(), subprocess.TimeoutExpired('ffmpeg', 15)):
            with self.subTest(error=type(error).__name__):
                self.ffmpeg.side_effect = error
                self.assertEqual(self.upload().status_code, 500)
        self.stt.assert_not_called()

    def test_stt_failure_and_empty_transcription_never_start_ai(self):
        for value in ('', '   ', None):
            self.stt.return_value.transcribe.return_value = value
            self.assertEqual(self.upload().status_code, 400)
        self.stt.return_value.transcribe.side_effect = RuntimeError('private speech detail')
        response = self.upload()
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('private', str(response.data))
        self.ai.assert_not_called()
        self.tts.assert_not_called()

    def test_tts_only_has_no_inference_or_messages(self):
        self.tts.return_value.synthesize.return_value = b'controlled wav'
        response = self.client.post(self.tts_url, {'text': 'Completed answer'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(base64.b64decode(response.data['audio_base64']), b'controlled wav')
        self.assertIn('tts;dur=', response['Server-Timing'])
        self.tts.return_value.synthesize.assert_called_once_with('Completed answer')
        self.ai.assert_not_called()
        self.stream.assert_not_called()
        self.stt.assert_not_called()
        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(Conversation.objects.count(), 0)

    def test_tts_rejects_invalid_and_over_limit_text(self):
        for text in (None, '', '   ', 123, [], 'x' * 12001):
            with self.subTest(kind=type(text).__name__):
                self.assertEqual(self.client.post(self.tts_url, {'text': text}, format='json').status_code, 400)
        self.tts.assert_not_called()
        self.tts.return_value.synthesize.return_value = b'wav'
        self.assertEqual(self.client.post(self.tts_url, {'text': 'x' * 12000}, format='json').status_code, 200)

    def test_tts_failures_are_safe_and_do_not_create_messages(self):
        self.tts.return_value.synthesize.side_effect = RuntimeError('private path')
        response = self.client.post(self.tts_url, {'text': 'Answer'}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private', str(response.data))
        self.assertEqual(Message.objects.count(), 0)

    def test_new_endpoints_are_post_only_and_require_csrf(self):
        client = APIClient(enforce_csrf_checks=True)
        for url in (self.stt_url, self.tts_url):
            self.assertEqual(self.client.get(url).status_code, 405)
            self.assertEqual(client.post(url, {}, format='json').status_code, 403)
        token = client.get('/assistant/').cookies['csrftoken'].value
        response = client.post(self.stt_url, {}, format='multipart', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 400)
        response = client.post(self.tts_url, {'text': ''}, format='json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 400)

    def test_review_then_stream_then_tts_persists_exactly_one_pair(self):
        install_resource_report(self)
        self.assertEqual(self.upload().status_code, 200)
        self.assertEqual(Message.objects.count(), 0)
        # Exercise the existing service/database path after the draft step.
        self.stream_patcher.stop()
        with patch('assistant.services.get_engine', return_value=FakeStreamingEngine()):
            response = self.client.post('/api/assistant/chat/stream/', {'query': 'Edited question'}, format='json')
            events = [json.loads(line) for line in b''.join(response.streaming_content).decode().splitlines()]
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(list(Message.objects.order_by('timestamp').values_list('sender', 'text')),
                         [('USER', 'Edited question'), ('AI', 'Hello world')])
        with patch('assistant.views.get_tts_provider') as tts:
            tts.return_value.synthesize.return_value = b'wav'
            self.assertEqual(self.client.post(self.tts_url, {'text': events[-1]['response']}, format='json').status_code, 200)
        self.assertEqual(Message.objects.count(), 2)
