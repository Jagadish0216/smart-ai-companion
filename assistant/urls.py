from django.urls import path
from .views import ChatAPIView, ChatStreamAPIView, VoiceAPIView

urlpatterns = [
    path('chat/', ChatAPIView.as_view(), name='api-chat'),
    path('chat/stream/', ChatStreamAPIView.as_view(), name='api-chat-stream'),
    path('voice/transcribe/', VoiceAPIView.as_view(), name='api-voice-transcribe'),
]
