"""Resident local whisper.cpp adapter. CLI provider remains the rollback option."""
from pathlib import Path
from threading import Lock
import os
import time
import wave
import urllib.request
import urllib.error
import uuid
from .stt import SpeechToTextProvider, STTError
from assistant import latency_trace as latency

URL = 'http://127.0.0.1:8178'


def server_command(binary, model, threads):
    """Check only the options we actually pass to the installed server."""
    import re
    import subprocess
    options = ['--model', '--threads', '--language', '--host', '--port']
    result = subprocess.run([str(binary), '--help'], capture_output=True, timeout=5)
    advertised = set(re.findall(rb'(?<![\w-])--[a-z][a-z-]*(?![\w-])', result.stdout + result.stderr))
    if any(option.encode() not in advertised for option in options):
        raise STTError('Installed whisper-server has incompatible flags.')
    return [str(binary), '--model', str(model), '--threads', str(threads),
            '--language', 'en', '--host', '127.0.0.1', '--port', '8178']


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, 'Local STT redirects are refused.', headers, fp)


def server_binary(cli):
    cli = Path(cli).resolve()
    candidates = [cli.with_name('whisper-server'), cli.parent / 'build' / 'bin' / 'whisper-server']
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise STTError('Existing whisper-server binary is required for resident STT; no download or model replacement attempted.')


def prepare_server_binary(cli):
    """Operator deployment only: build just the existing local server target if needed."""
    try:
        return server_binary(cli)
    except STTError:
        pass
    import subprocess
    cli = Path(cli).resolve()
    builds = [cli.parent.parent] if cli.parent.name == 'bin' else [cli.parent / 'build']
    for build in builds:
        if (build / 'CMakeCache.txt').is_file():
            try:
                subprocess.run(['cmake', '--build', str(build), '--target', 'whisper-server', '--parallel', '2'],
                               timeout=180, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return server_binary(cli)
            except (OSError, subprocess.SubprocessError, STTError):
                raise STTError('Cannot prepare whisper-server from the existing local build; original CLI assets were retained.') from None
    raise STTError('No existing whisper-server or local CMake build was found; original CLI assets were retained.')


class WhisperServerProvider(SpeechToTextProvider):
    def __init__(self, timeout=30):
        self.timeout = timeout
        # Never send local audio through environment proxies or follow redirects.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.lock = Lock()

    def warm(self, seconds=45):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                response = self.opener.open(URL + '/', timeout=1)
                # Older whisper servers omit public UI assets (404), but load before listening.
                status = response.status_code
                response.close()
                if status in (200, 404):
                    return
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    exc.close()
                    return
                exc.close()
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(.25)
        raise STTError('Resident whisper.cpp server did not become ready within its bounded startup time.')

    @latency.span('stt_server')
    def transcribe(self, audio_file_path):
        path = Path(audio_file_path)
        if not path.is_file() or not 0 < path.stat().st_size <= 2 * 1024 * 1024:
            raise STTError('Normalized request audio is missing or exceeds the bounded upload size.')
        try:
            with wave.open(str(path)) as audio:
                if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
                    raise STTError('Resident STT requires normalized mono 16 kHz PCM16 WAV.')
        except (wave.Error, EOFError):
            raise STTError('Resident STT requires a valid WAV file.') from None
        if not self.lock.acquire(blocking=False):
            raise STTError('Resident speech recognizer is busy; retry after the current request.')
        try:
            boundary = 'companion-' + uuid.uuid4().hex
            # whisper.cpp v1.7.6 exposes no_context via HTTP, not CLI, and defaults
            # it to false. Reset it and any retained prompt on EVERY request.
            # https://github.com/ggml-org/whisper.cpp/blob/v1.7.6/examples/server/server.cpp
            fields = b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode()
                              for key, value in [('response_format', 'json'), ('language', 'en'), ('temperature', '0.0'),
                                                 ('no_context', 'true'), ('prompt', '')])
            body = (fields + f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="request.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
                    + path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode())
            request = urllib.request.Request(URL + '/inference', data=body,
                                            headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
            with self.opener.open(request, timeout=self.timeout) as response:
                data = response.read(65537)
                if len(data) > 65536:
                    raise STTError('Resident STT response exceeds its bounded size.')
                import json
                result = json.loads(data)
                text = result.get('text') if isinstance(result, dict) else None
                if not isinstance(text, str):
                    raise STTError('Resident STT returned an invalid response.')
                return text.strip()
        except (urllib.error.URLError, ValueError, OSError):
            raise STTError('Resident speech transcription failed; check the local STT service.') from None
        finally:
            self.lock.release()


def main():
    import sys
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()
    from django.conf import settings
    if '--wait' in sys.argv:
        WhisperServerProvider().warm()
        return
    binary = server_binary(settings.STT_WHISPER_BIN)
    model = Path(settings.STT_WHISPER_MODEL)
    if not model.is_file() or model.name != 'ggml-base.en.bin':
        raise STTError('Resident demo STT requires the existing ggml-base.en.bin asset.')
    threads = int(settings.STT_WHISPER_THREADS)
    if not 1 <= threads <= 8:
        raise STTError('Whisper thread configuration is outside safe demo bounds.')
    os.execv(str(binary), server_command(binary, model, threads))


if __name__ == '__main__':
    main()
