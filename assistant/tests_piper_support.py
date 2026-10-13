"""Spawn-safe synthetic workers: no Django, Piper or ONNX imports."""

import base64
import io
import json
import os
from threading import Event
import wave


def wav_bytes(text="test"):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(int(len(text)).to_bytes(2, "little") * 160)
    return output.getvalue()


def fake_piper_worker(connection, voice_path):
    def send(value):
        connection.send_bytes(json.dumps(value).encode())
    try:
        if voice_path == "hang-load":
            Event().wait()
        send({"type": "ready"})
        while True:
            request = json.loads(connection.recv_bytes().decode())
            if request["type"] == "shutdown":
                if voice_path == "hang-shutdown":
                    Event().wait()
                send({"type": "closed"})
                return
            text, request_id = request["text"], request["request_id"]
            if text == "hang":
                Event().wait()
            if text == "crash":
                os._exit(7)
            if text == "malformed":
                connection.send_bytes(b"not json")
                continue
            if text == "error":
                send({"type": "error", "request_id": request_id, "code": "SYNTHESIS_FAILED"})
                continue
            send({"type": "result", "request_id": request_id - 1 if text == "stale" else request_id,
                  "wav": base64.b64encode(b"not WAV" if text == "invalidwav" else wav_bytes(text)).decode()})
    finally:
        connection.close()


def sigint_probe_piper_worker(connection, voice_path):
    """Run the production worker with a fake voice that raises real SIGINT."""
    import signal
    import sys
    from types import ModuleType
    from assistant.voice.piper_worker import worker_main

    class FakeVoice:
        @staticmethod
        def load(path, use_cuda=False):
            return FakeVoice()

        def synthesize_wav(self, text, wav):
            signal.raise_signal(signal.SIGINT)
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\x01\x00" * 160)

    module = ModuleType("piper.voice")
    module.PiperVoice = FakeVoice
    sys.modules["piper"] = ModuleType("piper")
    sys.modules["piper.voice"] = module
    worker_main(connection, voice_path)
