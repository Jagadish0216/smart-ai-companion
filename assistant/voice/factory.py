"""
Voice Provider Factory

Instantiates STT and TTS providers based on Django settings.
"""

from django.conf import settings
from threading import RLock
from .stt import SpeechToTextProvider, WhisperCppProvider, MockSTTProvider
from .tts import TextToSpeechProvider, PiperProvider, MockTTSProvider

_stt_provider_cache = None
_tts_provider_cache = None
_tts_cache_lock = RLock()

def get_stt_provider() -> SpeechToTextProvider:
    global _stt_provider_cache
    if _stt_provider_cache is not None:
        return _stt_provider_cache

    engine = getattr(settings, 'STT_ENGINE', 'mock')

    if engine == 'whisper_server':
        from .whisper_server import WhisperServerProvider
        _stt_provider_cache = WhisperServerProvider(getattr(settings, 'STT_TIMEOUT', 30))
    elif engine == 'whisper_cpp':
        bin_path = getattr(settings, 'STT_WHISPER_BIN', '')
        model_path = getattr(settings, 'STT_WHISPER_MODEL', '')
        timeout = getattr(settings, 'STT_TIMEOUT', 30)
        _stt_provider_cache = WhisperCppProvider(
            bin_path,
            model_path,
            timeout,
            vad_enabled=getattr(settings, 'STT_VAD_ENABLED', False),
            vad_model_path=getattr(settings, 'STT_VAD_MODEL', ''),
            vad_threshold=getattr(settings, 'STT_VAD_THRESHOLD', 0.5),
            vad_min_speech_ms=getattr(settings, 'STT_VAD_MIN_SPEECH_MS', 250),
            vad_min_silence_ms=getattr(settings, 'STT_VAD_MIN_SILENCE_MS', 700),
            vad_speech_pad_ms=getattr(settings, 'STT_VAD_SPEECH_PAD_MS', 100),
            threads=getattr(settings, 'STT_WHISPER_THREADS', 4),
        )
    else:
        _stt_provider_cache = MockSTTProvider()

    return _stt_provider_cache

def get_tts_provider() -> TextToSpeechProvider:
    global _tts_provider_cache
    with _tts_cache_lock:
        if _tts_provider_cache is None:
            engine = getattr(settings, 'TTS_ENGINE', 'mock')
            if engine == 'piper':
                _tts_provider_cache = PiperProvider(
                    getattr(settings, 'TTS_PIPER_BIN', ''),
                    getattr(settings, 'TTS_PIPER_VOICE', ''),
                    getattr(settings, 'TTS_TIMEOUT', 30),
                )
            else:
                _tts_provider_cache = MockTTSProvider()
        return _tts_provider_cache


def close_tts_provider():
    """Stop cached inference after a failed cycle without constructing a provider."""
    with _tts_cache_lock:
        if _tts_provider_cache is not None:
            _tts_provider_cache.close()

def reset_voice_providers():
    """Clear the provider cache (useful for testing)."""
    global _stt_provider_cache, _tts_provider_cache
    with _tts_cache_lock:
        close_tts_provider()
        _stt_provider_cache = None
        _tts_provider_cache = None
