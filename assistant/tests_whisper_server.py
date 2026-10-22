from pathlib import Path
from unittest.mock import MagicMock, patch
import json
import tempfile
import wave
from django.test import SimpleTestCase
from assistant.voice.whisper_server import WhisperServerProvider, URL
from assistant.voice.stt import STTError


class ResidentWhisperTests(SimpleTestCase):
    def audio(self,path):
        with wave.open(str(path),'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*160)

    @patch('assistant.voice.whisper_server.urllib.request.build_opener')
    def test_two_requests_reuse_local_session_without_loading_a_model_or_proxy(self,session):
        provider=WhisperServerProvider()
        response=session.return_value.open.return_value.__enter__.return_value
        response.read.side_effect=[b'{"text":"public question one"}',b'{"text":"public question two"}']
        with tempfile.TemporaryDirectory() as directory, patch('subprocess.run') as run:
            path=Path(directory)/'q.wav';self.audio(path)
            self.assertEqual(provider.transcribe(str(path)),'public question one')
            self.assertEqual(provider.transcribe(str(path)),'public question two')
            run.assert_not_called()
        self.assertEqual(session.call_args.args[0].proxies, {})
        self.assertEqual(session.call_count,1)
        self.assertTrue(all(c.args[0].full_url==URL+'/inference' for c in session.return_value.open.call_args_list))

    @patch('assistant.voice.whisper_server.urllib.request.build_opener')
    def test_busy_malformed_and_oversize_responses_are_bounded_and_release_lock(self,session):
        provider=WhisperServerProvider()
        response=session.return_value.open.return_value.__enter__.return_value
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'q.wav';self.audio(path)
            provider.lock.acquire()
            with self.assertRaisesRegex(STTError,'busy'):provider.transcribe(str(path))
            provider.lock.release()
            for data in (b'[]',b'broken',b'x'*65537):
                response.read.return_value=data
                with self.assertRaises(STTError):provider.transcribe(str(path))
                self.assertFalse(provider.lock.locked())

    @patch('assistant.voice.whisper_server.urllib.request.build_opener')
    def test_readiness_is_bounded_and_no_audio_is_sent(self,session):
        session.return_value.open.return_value.status_code=404
        WhisperServerProvider().warm(.1)
        self.assertEqual(session.return_value.open.call_args.args[0],URL+'/')
