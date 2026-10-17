from django.urls import path
from .views import (
    ChatAPIView, ChatStreamAPIView, VoiceAPIView,
    VoiceTranscribeOnlyAPIView, VoiceSynthesizeAPIView,
)

urlpatterns = [
    path('chat/', ChatAPIView.as_view(), name='api-chat'),
    path('chat/stream/', ChatStreamAPIView.as_view(), name='api-chat-stream'),
    path('voice/transcribe-only/', VoiceTranscribeOnlyAPIView.as_view(), name='api-voice-transcribe-only'),
    path('voice/synthesize/', VoiceSynthesizeAPIView.as_view(), name='api-voice-synthesize'),
    path('voice/transcribe/', VoiceAPIView.as_view(), name='api-voice-transcribe'),
]
