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
