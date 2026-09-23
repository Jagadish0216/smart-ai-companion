from django.urls import path
from .views import ChatAPIView, VoiceAPIView

urlpatterns = [
    path('chat/', ChatAPIView.as_view(), name='api-chat'),
    path('voice/transcribe/', VoiceAPIView.as_view(), name='api-voice-transcribe'),
]
