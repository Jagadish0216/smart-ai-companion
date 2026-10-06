import json
from urllib import parse

from django.http import StreamingHttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .serializers import ChatQuerySerializer
from .services import AssistantService
from conversations.models import Conversation


def _public_assistant_metadata(metadata):
    if not metadata:
        return {}
    payload = {
        key: metadata.get(key)
        for key in (
            "engine",
            "model",
            "mode",
            "route",
            "rag_used",
            "rag_chunks",
            "online_used",
            "online_results",
            "online_latency_ms",
            "resource_profile",
            "configured_model",
            "effective_model",
            "generation_budget",
            "max_output_tokens",
            "local_ai_allowed",
            "online_allowed",
            "execution_ms",
            "latency_ms",
            "generation_ms",
            "ttft_ms",
            "prompt_prepare_ms",
            "policy_reason",
            "model_reason",
            "resource_policy_available",
            "status_message",
            "action_used",
            "action_name",
            "device_id",
            "action_success",
            "action_latency_ms",
            "sensor_used",
            "sensor_type",
            "sensor_value",
            "sensor_unit",
        )
        if key in metadata
    }
    if "rag_sources" in metadata:
        payload["rag_sources"] = [
            {
                key: value
                for key, value in source.items()
                if key != "source_identifier"
            }
            for source in metadata.get("rag_sources", [])
        ]
    if "online_sources" in metadata:
        allowed_source_fields = {"title", "url", "provider", "retrieved_at"}
        payload["online_sources"] = []
        for source in metadata.get("online_sources", []):
            if not isinstance(source, dict):
                continue
            public_source = {
                key: value
                for key, value in source.items()
                if key in allowed_source_fields and key != "url"
            }
            safe_url = _safe_public_source_url(source.get("url"))
            if safe_url:
                public_source["url"] = safe_url
            payload["online_sources"].append(public_source)
    return payload


def _safe_public_source_url(value):
    if not isinstance(value, str):
        return ""
    try:
        parsed = parse.urlsplit(value)
        if (
            parsed.scheme.lower() not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return ""
        return value
    except (ValueError, UnicodeError):
        return ""


def _assistant_error_response(error: str) -> Response:
    if error.startswith("Local AI generation timed out"):
        label = "Generation timeout"
        response_status = status.HTTP_504_GATEWAY_TIMEOUT
    elif error.startswith("The selected local AI model"):
        label = "Model unavailable"
        response_status = status.HTTP_503_SERVICE_UNAVAILABLE
    elif error.startswith("The local AI service"):
        label = "AI unavailable"
        response_status = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        label = "Processing error"
        response_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    return Response(
        {"error": label, "detail": error},
        status=response_status,
    )

class ChatAPIView(APIView):
    def get(self, request):
        conversation_id = request.query_params.get('conversation_id')
        if not conversation_id:
            return Response({"error": "Missing conversation_id."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            conversation = Conversation.objects.get(id=conversation_id)
            messages = conversation.messages.all().order_by('timestamp')
            msg_data = []
            for msg in messages:
                msg_data.append({
                    "role": msg.sender,
                    "content": msg.text,
                    "created_at": msg.timestamp.isoformat()
                })
            return Response({"conversation_id": conversation.id, "messages": msg_data}, status=status.HTTP_200_OK)
        except Conversation.DoesNotExist:
            return Response({"error": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

    def post(self, request):
        serializer = ChatQuerySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Invalid request", "detail": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        query = serializer.validated_data['query']
        if not query.strip():
            return Response(
                {"error": "Empty query", "detail": "Query cannot be empty."},
                status=status.HTTP_400_BAD_REQUEST
            )

        conversation_id = serializer.validated_data.get('conversation_id')

        conversation, ai_text, metadata, error = AssistantService.process_message(query, conversation_id)

        if error == "Conversation not found.":
            return Response(
                {"error": "Not found", "detail": error},
                status=status.HTTP_404_NOT_FOUND
            )
        elif error:
            return _assistant_error_response(error)

        response_data = {
            "conversation_id": conversation.id,
            "response": ai_text,
        }

        # Additive request metadata preserves the original response contract.
        response_data.update(_public_assistant_metadata(metadata))

        return Response(response_data, status=status.HTTP_200_OK)


def _conversation_display_fields(conversation):
    if conversation is None:
        return {}
    return {
        "conversation_title": conversation.title or f"Conversation {conversation.id}",
        "conversation_display_time": timezone.localtime(
            conversation.updated_at
        ).strftime("%b %d, %H:%M"),
    }


def _ndjson_stream(query, conversation_id):
    for event in AssistantService.process_message_stream(query, conversation_id):
        protocol_fields = {
            "type", "text", "response", "error", "detail", "partial",
            "conversation_id", "direct",
        }
        safe_event = {
            key: value
            for key, value in event.items()
            if key in protocol_fields
        }
        metadata = _public_assistant_metadata(event)
        safe_event.update(metadata)
        event_conversation_id = event.get("conversation_id")
        if event_conversation_id:
            try:
                conversation = Conversation.objects.get(id=event_conversation_id)
            except Conversation.DoesNotExist:
                conversation = None
            safe_event.update(_conversation_display_fields(conversation))
        yield json.dumps(safe_event, separators=(",", ":")) + "\n"


@method_decorator(csrf_protect, name="dispatch")
class ChatStreamAPIView(APIView):
    """POST-only newline-delimited JSON assistant stream."""

    def post(self, request):
        serializer = ChatQuerySerializer(data=request.data)
        if not serializer.is_valid():
            return StreamingHttpResponse(
                iter([json.dumps({
                    "type": "error",
                    "error": "invalid_request",
                    "detail": serializer.errors,
                }) + "\n"]),
                status=status.HTTP_400_BAD_REQUEST,
                content_type="application/x-ndjson",
            )
        query = serializer.validated_data["query"]
        if not query.strip():
            return StreamingHttpResponse(
                iter([json.dumps({
                    "type": "error",
                    "error": "empty_query",
                    "detail": "Query cannot be empty.",
                }) + "\n"]),
                status=status.HTTP_400_BAD_REQUEST,
                content_type="application/x-ndjson",
            )
        response = StreamingHttpResponse(
            _ndjson_stream(
                query,
                serializer.validated_data.get("conversation_id"),
            ),
            content_type="application/x-ndjson",
        )
        response["Cache-Control"] = "no-store"
        response["X-Accel-Buffering"] = "no"
        response["X-Content-Type-Options"] = "nosniff"
        return response

import os
import tempfile
import subprocess
import base64
import logging
from .voice.factory import get_stt_provider, get_tts_provider

logger = logging.getLogger(__name__)

# Map common browser MIME types to file extensions that FFmpeg recognises
_MIME_TO_EXT = {
    'audio/webm': '.webm',
    'audio/ogg': '.ogg',
    'audio/mp4': '.mp4',
    'audio/mpeg': '.mp3',
    'audio/wav': '.wav',
    'audio/x-wav': '.wav',
    'audio/flac': '.flac',
    'audio/aac': '.aac',
}


def _extension_for_mime(content_type: str) -> str:
    """Return a file extension for the given MIME type, defaulting to .webm."""
    if not content_type:
        return '.webm'
    # Strip codec parameters: "audio/webm;codecs=opus" -> "audio/webm"
    base = content_type.split(';')[0].strip().lower()
    return _MIME_TO_EXT.get(base, '.webm')


class VoiceAPIView(APIView):
    def post(self, request):
        if 'audio' not in request.FILES:
            return Response({"error": "No audio file provided."}, status=status.HTTP_400_BAD_REQUEST)

        audio_file = request.FILES['audio']
        if audio_file.size == 0:
            return Response({"error": "Empty audio file."}, status=status.HTTP_400_BAD_REQUEST)

        # Max size 5 MB
        if audio_file.size > 5 * 1024 * 1024:
            return Response({"error": "Audio file too large."}, status=status.HTTP_400_BAD_REQUEST)

        conversation_id = request.data.get('conversation_id')
        if conversation_id:
            try:
                conversation_id = int(conversation_id)
            except ValueError:
                return Response({"error": "Invalid conversation_id."}, status=status.HTTP_400_BAD_REQUEST)

        # Determine file extension from the uploaded MIME type so FFmpeg can
        # correctly detect the container format (WebM/Opus, OGG, etc.)
        upload_mime = audio_file.content_type or ''
        upload_ext = _extension_for_mime(upload_mime)

        logger.info(
            "Voice upload: name=%s content_type=%s size=%d ext=%s",
            audio_file.name, upload_mime, audio_file.size, upload_ext,
        )

        # Create temporary files for processing
        fd_in, temp_in_path = tempfile.mkstemp(suffix=upload_ext)
        fd_out, temp_out_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd_out)  # Close output fd so ffmpeg can write to it

        try:
            with os.fdopen(fd_in, 'wb') as f:
                for chunk in audio_file.chunks():
                    f.write(chunk)

            in_size = os.path.getsize(temp_in_path)
            logger.info("Saved upload to %s (%d bytes)", temp_in_path, in_size)

            # Convert to 16 kHz mono PCM s16le WAV for whisper.cpp
            ffmpeg_cmd = [
                'ffmpeg', '-y',
                '-i', temp_in_path,
                '-ar', '16000',
                '-ac', '1',
                '-c:a', 'pcm_s16le',
                temp_out_path,
            ]

            try:
                result = subprocess.run(
                    ffmpeg_cmd,
                    capture_output=True,
                    timeout=15,
                )
                if result.returncode != 0:
                    stderr_text = result.stderr.decode('utf-8', errors='replace')
                    logger.error(
                        "FFmpeg conversion failed (rc=%d). Command: %s\nstderr:\n%s",
                        result.returncode, ' '.join(ffmpeg_cmd), stderr_text,
                    )
                    return Response(
                        {"error": "The recorded audio format could not be processed."},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )

                out_size = os.path.getsize(temp_out_path)
                logger.info(
                    "FFmpeg conversion OK: %s (%d bytes) -> %s (%d bytes)",
                    temp_in_path, in_size, temp_out_path, out_size,
                )
            except FileNotFoundError:
                logger.error("FFmpeg binary not found on PATH.")
                return Response(
                    {"error": "Audio conversion tool (ffmpeg) is not installed on the server."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            except subprocess.TimeoutExpired:
                logger.error("FFmpeg conversion timed out after 15 s.")
                return Response(
                    {"error": "Audio conversion timed out."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            # 1. STT
            stt_provider = get_stt_provider()
            try:
                transcribed_text = stt_provider.transcribe(temp_out_path)
            except Exception as e:
                logger.error("STT failed: %s", e)
                return Response(
                    {"error": "I couldn't understand the recording. Please try again."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            if not transcribed_text:
                return Response(
                    {"error": "I couldn't understand the recording. Please try again."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # 2. Assistant inference
            conversation, ai_text, metadata, error = AssistantService.process_message(transcribed_text, conversation_id)
            if error:
                return _assistant_error_response(error)

            # 3. TTS
            tts_provider = get_tts_provider()
            try:
                audio_bytes = tts_provider.synthesize(ai_text)
            except Exception as e:
                logger.error("TTS failed: %s", e)
                # Return text response even though TTS failed
                return Response({
                    "transcription": transcribed_text,
                    "response": ai_text,
                    "conversation_id": conversation.id,
                    "audio_base64": None,
                    "tts_error": "The response was generated, but speech playback failed.",
                    **_public_assistant_metadata(metadata),
                }, status=status.HTTP_200_OK)

            # Base64 encode audio
            audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')

            response_data = {
                "transcription": transcribed_text,
                "response": ai_text,
                "conversation_id": conversation.id,
                "audio_base64": audio_b64,
            }
            response_data.update(_public_assistant_metadata(metadata))

            return Response(response_data, status=status.HTTP_200_OK)

        finally:
            # Clean up temp files
            if os.path.exists(temp_in_path):
                os.remove(temp_in_path)
            if os.path.exists(temp_out_path):
                os.remove(temp_out_path)
