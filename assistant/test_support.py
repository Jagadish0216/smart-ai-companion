"""Shared deterministic fixtures for assistant unit tests."""

from unittest.mock import patch


# Complete baseline for VoiceLoopConfig.from_settings tests. Appliance gain,
# profiles, calibrated readiness and chunk settings must not leak into fixtures.
VOICE_LOOP_TEST_SETTINGS = {
    'VOICE_AUDIO_BACKEND': 'pulse',
    'VOICE_INPUT_SOURCE': 'test-source',
    'VOICE_ALSA_CAPTURE_DEVICE': 'test-capture',
    'VOICE_ALSA_PLAYBACK_DEVICE': 'test-playback',
    'VOICE_PLAYBACK_PROFILE': 'legacy',
    'VOICE_PLAYBACK_GAIN': '4.0',
    'VOICE_RECORD_SECONDS': '5',
    'VOICE_LEADING_SILENCE_SECONDS': '0.7',
    'VOICE_TTS_CHUNK_CHARS': '300',
    'VOICE_TTS_MAX_TOTAL_CHARS': '0',
    'VOICE_INPUT_WARMUP_SECONDS': '1.0',
    'VOICE_STREAM_TTS_TARGET_CHARS': '180',
    'VOICE_STREAM_INTER_CHUNK_SILENCE_SECONDS': '0.12',
    'VOICE_STREAM_FIRST_CHUNK_MIN_CHARS': '60',
    'VOICE_STREAM_FIRST_CHUNK_TARGET_CHARS': '100',
    'VOICE_STREAM_FIRST_SENTENCE_MIN_CHARS': '',
    'VOICE_CAPTURE_PREPARATION_POLICY': 'legacy',
    'VOICE_CAPTURE_READY_LIFECYCLE': False,
    'VOICE_CAPTURE_VAD_ENABLED': False,
    'VOICE_CAPTURE_VAD_ADAPTIVE': True,
    'VOICE_CAPTURE_VAD_START_THRESHOLD': '0.02',
    'VOICE_CAPTURE_VAD_SILENCE_SECONDS': '0.8',
    'VOICE_CAPTURE_VAD_MAX_SECONDS': '10',
    'VOICE_CAPTURE_VAD_START_TIMEOUT_SECONDS': '5',
    'VOICE_CAPTURE_VAD_NOISE_CALIBRATION_SECONDS': '0.5',
    'VOICE_CAPTURE_VAD_START_CONFIRM_MS': '60',
    'VOICE_CAPTURE_VAD_MIN_SPEECH_MS': '300',
}


# Fixtures that assert default wake behavior must not inherit appliance .env values.
WAKE_WORD_TEST_SETTINGS = {
    "VOICE_WAKE_WORD_ENABLED": False,
    "VOICE_WAKE_WORD_ENGINE": "openwakeword",
    "VOICE_WAKE_WORD_MODEL": "hey_jarvis",
    "VOICE_WAKE_WORD_MODEL_PATH": "",
    "VOICE_WAKE_WORD_THRESHOLD": "0.5",
    "VOICE_WAKE_WORD_VAD_THRESHOLD": "0.5",
    "VOICE_WAKE_WORD_TIMEOUT_SECONDS": "0",
    "VOICE_WAKE_WORD_COOLDOWN_SECONDS": "0.75",
}


def install_resource_report(
    testcase,
    *,
    profile: str = "BALANCED",
    local_ai_allowed: bool = True,
    online_allowed: bool = False,
    generation_budget: str = "NORMAL",
):
    report = {
        "recommended_profile": profile,
        "reasons": [],
        "ai_recommendation": {
            "local_ai_allowed": local_ai_allowed,
            "online_allowed": online_allowed,
            "generation_budget": generation_budget,
        },
    }
    patcher = patch(
        "assistant.execution_policy.get_resource_manager_report",
        return_value=report,
    )
    mocked = patcher.start()
    testcase.addCleanup(patcher.stop)
    return mocked
