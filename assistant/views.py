from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .serializers import ChatQuerySerializer
from .services import AssistantService
from conversations.models import Conversation

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
            return Response(
                {"error": "Processing error", "detail": error},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        response_data = {
            "conversation_id": conversation.id,
            "response": ai_text,
        }

        # Include engine metadata (backwards-compatible addition)
        if metadata:
            response_data["engine"] = metadata.get("engine")
            response_data["model"] = metadata.get("model")
            response_data["mode"] = metadata.get("mode")

        return Response(response_data, status=status.HTTP_200_OK)

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
                return Response(
                    {"error": "The local AI could not generate a response.", "detail": error},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

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
                    **({"engine": metadata.get("engine"), "model": metadata.get("model"), "mode": metadata.get("mode")} if metadata else {}),
                }, status=status.HTTP_200_OK)

            # Base64 encode audio
            audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')

            response_data = {
                "transcription": transcribed_text,
                "response": ai_text,
                "conversation_id": conversation.id,
                "audio_base64": audio_b64,
            }
            if metadata:
                response_data["engine"] = metadata.get("engine")
                response_data["model"] = metadata.get("model")
                response_data["mode"] = metadata.get("mode")

            return Response(response_data, status=status.HTTP_200_OK)

        finally:
            # Clean up temp files
            if os.path.exists(temp_in_path):
                os.remove(temp_in_path)
            if os.path.exists(temp_out_path):
                os.remove(temp_out_path)
