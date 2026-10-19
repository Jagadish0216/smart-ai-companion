"""Shared deterministic fixtures for assistant unit tests."""

from unittest.mock import patch


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
