"""Shared deterministic fixtures for assistant unit tests."""

from unittest.mock import patch


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
