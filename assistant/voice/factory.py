"""
Voice Provider Factory

Instantiates STT and TTS providers based on Django settings.
"""

from django.conf import settings
from .stt import SpeechToTextProvider, WhisperCppProvider, MockSTTProvider
from .tts import TextToSpeechProvider, PiperProvider, MockTTSProvider

_stt_provider_cache = None
_tts_provider_cache = None

def get_stt_provider() -> SpeechToTextProvider:
    global _stt_provider_cache
    if _stt_provider_cache is not None:
        return _stt_provider_cache

    engine = getattr(settings, 'STT_ENGINE', 'mock')

    if engine == 'whisper_cpp':
        bin_path = getattr(settings, 'STT_WHISPER_BIN', '')
        model_path = getattr(settings, 'STT_WHISPER_MODEL', '')
        timeout = getattr(settings, 'STT_TIMEOUT', 30)
        _stt_provider_cache = WhisperCppProvider(bin_path, model_path, timeout)
    else:
        _stt_provider_cache = MockSTTProvider()

    return _stt_provider_cache

def get_tts_provider() -> TextToSpeechProvider:
    global _tts_provider_cache
    if _tts_provider_cache is not None:
        return _tts_provider_cache

    engine = getattr(settings, 'TTS_ENGINE', 'mock')

    if engine == 'piper':
        bin_path = getattr(settings, 'TTS_PIPER_BIN', '')
        voice_path = getattr(settings, 'TTS_PIPER_VOICE', '')
        timeout = getattr(settings, 'TTS_TIMEOUT', 30)
        _tts_provider_cache = PiperProvider(bin_path, voice_path, timeout)
    else:
        _tts_provider_cache = MockTTSProvider()

    return _tts_provider_cache

def reset_voice_providers():
    """Clear the provider cache (useful for testing)."""
    global _stt_provider_cache, _tts_provider_cache
    _stt_provider_cache = None
    _tts_provider_cache = None
