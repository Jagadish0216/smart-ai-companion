from pathlib import Path
from unittest.mock import MagicMock, patch
import json
import tempfile
import wave
from django.test import SimpleTestCase
from assistant.voice.whisper_server import WhisperServerProvider, URL, server_command, main
from assistant.voice.stt import STTError


class ResidentWhisperTests(SimpleTestCase):
    @patch('subprocess.run')
    def test_server_without_no_context_cli_flags_passes_preflight_and_starts(self, run):
        run.return_value.stdout = b''
        run.return_value.stderr = b'--model FNAME --threads N --language LANG --host HOST --port PORT'
        expected = ['whisper-server', '--model', 'ggml-base.en.bin', '--threads', '4',
                    '--language', 'en', '--host', '127.0.0.1', '--port', '8178']
        self.assertEqual(server_command('whisper-server', 'ggml-base.en.bin', 4), expected)
        with self.settings(STT_WHISPER_MODEL='ggml-base.en.bin', STT_WHISPER_THREADS=4), \
                patch('django.setup'), patch('sys.argv', ['whisper_server']), \
                patch('assistant.voice.whisper_server.server_binary', return_value=Path('whisper-server')), \
                patch('assistant.voice.whisper_server.Path.is_file', return_value=True), \
                patch('assistant.voice.whisper_server.os.execv') as execute:
            main()
        execute.assert_called_once_with('whisper-server', expected)
        self.assertNotIn('-nc', execute.call_args.args[1])
        self.assertNotIn('--no-context', execute.call_args.args[1])

    @patch('subprocess.run')
    def test_preflight_rejects_missing_required_option(self, run):
        for missing in (b'--model', b'--threads', b'--language', b'--host', b'--port'):
            with self.subTest(missing=missing):
                run.return_value.stdout = b'--model --threads --language --host --port'.replace(missing, missing + b'-other')
                run.return_value.stderr = b''
                with self.assertRaisesRegex(STTError, 'incompatible flags'):
                    server_command('whisper-server', 'ggml-base.en.bin', 4)

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
        # Decode actual multipart requests: each explicitly resets server context
        # and prompt; the first transcription is never fed into the second.
        from email.parser import BytesParser
        from email.policy import default
        for call in session.return_value.open.call_args_list:
            request = call.args[0]
            message = BytesParser(policy=default).parsebytes(
                ('Content-Type: ' + request.get_header('Content-type') + '\r\n\r\n').encode() + request.data)
            fields = {part.get_param('name', header='content-disposition'): part.get_payload(decode=True)
                      for part in message.iter_parts()}
            self.assertEqual(fields['no_context'], b'true')
            self.assertEqual(fields['prompt'], b'')
            self.assertNotIn(b'public question one', request.data)
            self.assertIn('file', fields)

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
        # urllib responses expose status, not requests' status_code.
        from types import SimpleNamespace
        for status in (200, 404):
            response = SimpleNamespace(status=status, close=MagicMock())
            session.return_value.open.return_value = response
            WhisperServerProvider().warm(.1)
            response.close.assert_called_once_with()
        self.assertEqual(session.return_value.open.call_args.args[0],URL+'/')
