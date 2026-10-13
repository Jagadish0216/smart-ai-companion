"""Internal framed JSON IPC worker; Piper/ONNX is imported only in the child."""

import base64
import io
import json
import signal
import wave

MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 48 * 1024 * 1024
MAX_WAV_BYTES = 32 * 1024 * 1024


def send_message(connection, message):
    connection.send_bytes(json.dumps(message).encode("utf-8"))


def worker_main(connection, voice_path):
    """Load one CPU voice and return one complete WAV per synthesis request."""
    # Terminal SIGINT also reaches the child on the Pi. The parent owns Ctrl+C
    # handling and shuts us down through IPC (or its terminate fallback).
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    request_id = None
    try:
        try:
            from piper.voice import PiperVoice
        except ImportError:
            send_message(connection, {"type": "error", "request_id": None, "code": "IMPORT_FAILED"})
            return
        try:
            voice = PiperVoice.load(voice_path, use_cuda=False)
        except Exception:
            send_message(connection, {"type": "error", "request_id": None, "code": "LOAD_FAILED"})
            return
        send_message(connection, {"type": "ready"})
        previous_id = 0
        while True:
            message = json.loads(connection.recv_bytes(MAX_REQUEST_BYTES).decode("utf-8"))
            if message == {"type": "shutdown"}:
                send_message(connection, {"type": "closed"})
                return
            request_id = message.get("request_id") if isinstance(message, dict) else None
            if (
                not isinstance(message, dict) or set(message) != {"type", "request_id", "text"}
                or message["type"] != "synthesize" or type(request_id) is not int
                or request_id <= previous_id or not isinstance(message["text"], str) or not message["text"].strip()
            ):
                send_message(connection, {"type": "error", "request_id": request_id, "code": "PROTOCOL_ERROR"})
                return
            previous_id = request_id
            try:
                output = io.BytesIO()
                with wave.open(output, "wb") as wav:
                    voice.synthesize_wav(message["text"], wav)
                audio = output.getvalue()
                if len(audio) > MAX_WAV_BYTES:
                    raise ValueError("Oversized audio")
                send_message(connection, {"type": "result", "request_id": request_id,
                                          "wav": base64.b64encode(audio).decode("ascii")})
            except Exception:
                send_message(connection, {"type": "error", "request_id": request_id, "code": "SYNTHESIS_FAILED"})
                return
    except (EOFError, OSError):
        pass
    except Exception:
        try:
            send_message(connection, {"type": "error", "request_id": request_id, "code": "PROTOCOL_ERROR"})
        except (EOFError, OSError):
            pass
    finally:
        connection.close()
