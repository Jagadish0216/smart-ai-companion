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

class VoiceAPIView(APIView):
    def post(self, request):
        if 'audio' not in request.FILES:
            return Response({"error": "No audio file provided."}, status=status.HTTP_400_BAD_REQUEST)

        audio_file = request.FILES['audio']
        if audio_file.size == 0:
            return Response({"error": "Empty audio file."}, status=status.HTTP_400_BAD_REQUEST)

        # Optional max size e.g. 5MB
        if audio_file.size > 5 * 1024 * 1024:
            return Response({"error": "Audio file too large."}, status=status.HTTP_400_BAD_REQUEST)

        conversation_id = request.data.get('conversation_id')
        if conversation_id:
            try:
                conversation_id = int(conversation_id)
            except ValueError:
                return Response({"error": "Invalid conversation_id."}, status=status.HTTP_400_BAD_REQUEST)

        # Create temporary files for processing
        fd_in, temp_in_path = tempfile.mkstemp(suffix=".webm")
        fd_out, temp_out_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd_out)  # Close the output fd immediately so ffmpeg and others can write to it/we can delete it

        try:
            with os.fdopen(fd_in, 'wb') as f:
                for chunk in audio_file.chunks():
                    f.write(chunk)

            # Convert to 16kHz mono WAV for whisper.cpp using ffmpeg
            # Even if the system mock is used, we try to run ffmpeg.
            # If ffmpeg is missing, we log it and just copy the file to avoid crashing mocks.
            try:
                subprocess.run([
                    'ffmpeg', '-y', '-i', temp_in_path,
                    '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le',
                    temp_out_path
                ], check=True, capture_output=True)
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                logger.warning("ffmpeg conversion failed or not found. STT might fail if file is not standard 16kHz WAV.")
                import shutil
                shutil.copy2(temp_in_path, temp_out_path)

            # 1. STT
            stt_provider = get_stt_provider()
            try:
                transcribed_text = stt_provider.transcribe(temp_out_path)
            except Exception as e:
                logger.error("STT failed: %s", e)
                return Response({"error": "Speech-to-text failed.", "detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            if not transcribed_text:
                return Response({"error": "Empty transcription."}, status=status.HTTP_400_BAD_REQUEST)

            # 2. Assistant Inference
            conversation, ai_text, metadata, error = AssistantService.process_message(transcribed_text, conversation_id)
            if error:
                return Response({"error": "Assistant processing error.", "detail": error}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # 3. TTS
            tts_provider = get_tts_provider()
            try:
                audio_bytes = tts_provider.synthesize(ai_text)
            except Exception as e:
                logger.error("TTS failed: %s", e)
                # If TTS fails, we can still return the text, but the user requested explicit error handling.
                # However, the assistant message is already persisted. Returning 500 is okay as per requirements.
                return Response({"error": "Text-to-speech failed.", "detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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
