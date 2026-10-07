"""Deterministic resource-aware execution plans for one assistant request."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Callable, Iterable

from django.conf import settings

from system.control_plane.network import get_internet_status
from system.control_plane.resource_manager import get_resource_manager_report


RESOURCE_PROFILES = {"PERFORMANCE", "BALANCED", "ECO", "PROTECTIVE"}
GENERATION_BUDGETS = {"NORMAL", "REDUCED", "MINIMAL"}


@dataclass(frozen=True)
class AIExecutionPlan:
    resource_profile: str
    configured_model: str
    effective_model: str | None
    model_reason: str
    generation_budget: str
    max_output_tokens: int
    context_message_limit: int
    local_ai_allowed: bool
    generation_allowed: bool
    online_allowed: bool
    timeout_seconds: float
    policy_reason: str
    resource_policy_available: bool
    status_message: str | None = None

    def metadata(self) -> dict:
        return asdict(self)


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _bounded_timeout() -> float:
    try:
        timeout = float(
            getattr(settings, "AI_LOCAL_GENERATION_TIMEOUT_SECONDS", 120)
        )
    except (TypeError, ValueError):
        timeout = 120.0
    return min(300.0, max(1.0, timeout))


def _budget_values() -> dict[str, tuple[int, int]]:
    return {
        "NORMAL": (
            _positive_int("AI_GENERATION_NORMAL_NUM_PREDICT", 128),
            _positive_int("AI_CONTEXT_NORMAL_MESSAGES", 10),
        ),
        "REDUCED": (
            _positive_int("AI_GENERATION_REDUCED_NUM_PREDICT", 96),
            _positive_int("AI_CONTEXT_REDUCED_MESSAGES", 6),
        ),
        "MINIMAL": (
            _positive_int("AI_GENERATION_MINIMAL_NUM_PREDICT", 48),
            _positive_int("AI_CONTEXT_MINIMAL_MESSAGES", 3),
        ),
    }


def _first_policy_reason(report: dict, default: str) -> str:
    for reason in report.get("reasons", []):
        if isinstance(reason, dict) and reason.get("code"):
            return str(reason["code"])
    return default


def build_execution_plan(
    report: dict,
    *,
    available_models: Iterable[str] | None = None,
    resource_policy_available: bool = True,
) -> AIExecutionPlan:
    """Translate one resource report and optional local inventory into a plan."""
    configured_model = str(getattr(settings, "OLLAMA_MODEL", "llama3.2:3b"))
    lightweight_model = str(
        getattr(settings, "AI_LIGHTWEIGHT_MODEL", "llama3.2:1b")
    )
    profile = str(report.get("recommended_profile", "UNKNOWN")).upper()
    if profile not in RESOURCE_PROFILES:
        profile = "UNKNOWN"
        resource_policy_available = False

    ai_recommendation = report.get("ai_recommendation", {})
    local_ai_allowed = bool(ai_recommendation.get("local_ai_allowed", False))
    online_allowed = bool(ai_recommendation.get("online_allowed", False))
    is_local_engine = str(getattr(settings, "AI_ENGINE", "mock")).lower() == "local"
    generation_allowed = local_ai_allowed if is_local_engine else True
    policy_reason = _first_policy_reason(
        report,
        "RESOURCE_POLICY_UNAVAILABLE" if not resource_policy_available else profile,
    )

    if not resource_policy_available:
        budget = "REDUCED"
    else:
        budget = {
            "PERFORMANCE": "NORMAL",
            "BALANCED": "NORMAL",
            "ECO": "REDUCED",
            "PROTECTIVE": "MINIMAL",
        }[profile]
    recommended_budget = str(ai_recommendation.get("generation_budget", "")).upper()
    if resource_policy_available and recommended_budget in GENERATION_BUDGETS:
        budget = recommended_budget

    installed = set(available_models) if available_models is not None else None
    effective_model: str | None = configured_model
    model_reason = "CONFIGURED_MODEL"
    status_message = None
    if profile == "ECO":
        status_message = "Running in reduced-resource mode."
        if installed is not None and lightweight_model in installed:
            effective_model = lightweight_model
            model_reason = policy_reason or "RESOURCE_PRESSURE"
        elif installed is None:
            model_reason = "LIGHTWEIGHT_AVAILABILITY_UNKNOWN"
        else:
            model_reason = "LIGHTWEIGHT_UNAVAILABLE"
    elif profile == "PROTECTIVE":
        if not generation_allowed:
            effective_model = None
            model_reason = "RESOURCE_POLICY_BLOCK"
            status_message = (
                "Local AI is temporarily unavailable because the device is "
                "protecting itself from a critical resource condition."
            )
        elif installed is not None and lightweight_model in installed:
            effective_model = lightweight_model
            model_reason = policy_reason or "RESOURCE_PRESSURE"
            status_message = (
                "System resources are constrained, so a lightweight response "
                "mode is active."
            )
        else:
            effective_model = None
            generation_allowed = False
            model_reason = (
                "LIGHTWEIGHT_AVAILABILITY_UNKNOWN"
                if installed is None
                else "LIGHTWEIGHT_UNAVAILABLE"
            )
            status_message = (
                "Local AI is temporarily unavailable because the device is "
                "protecting itself from a critical resource condition."
            )

    max_output_tokens, context_message_limit = _budget_values()[budget]
    return AIExecutionPlan(
        resource_profile=profile,
        configured_model=configured_model,
        effective_model=effective_model,
        model_reason=model_reason,
        generation_budget=budget,
        max_output_tokens=max_output_tokens,
        context_message_limit=context_message_limit,
        local_ai_allowed=local_ai_allowed,
        generation_allowed=generation_allowed,
        online_allowed=online_allowed,
        timeout_seconds=_bounded_timeout(),
        policy_reason=policy_reason,
        resource_policy_available=resource_policy_available,
        status_message=status_message,
    )


def get_ai_execution_plan(
    *,
    model_inventory_provider: Callable[[], Iterable[str]] | None = None,
) -> AIExecutionPlan:
    """Evaluate the effective plan once, reusing Resource Manager's cache."""
    if str(getattr(settings, "AI_ENGINE", "mock")).lower() != "local":
        return build_execution_plan({
            "recommended_profile": "BALANCED",
            "reasons": [],
            "ai_recommendation": {
                "local_ai_allowed": False,
                "online_allowed": False,
                "generation_budget": "NORMAL",
            },
        })
    try:
        report = get_resource_manager_report()
        if not isinstance(report, dict):
            raise ValueError("resource report is not a mapping")
        profile = str(report.get("recommended_profile", "UNKNOWN")).upper()
        if profile not in RESOURCE_PROFILES:
            raise ValueError("resource profile is unavailable")
        policy_available = True
    except Exception:
        try:
            internet = get_internet_status()
            online_allowed = bool(
                getattr(settings, "ONLINE_RETRIEVAL_ENABLED", False)
                and internet.get("state") in {"FULL", "LIMITED"}
            )
        except Exception:
            online_allowed = False
        report = {
            "recommended_profile": "UNKNOWN",
            "reasons": [{"code": "RESOURCE_POLICY_UNAVAILABLE"}],
            "ai_recommendation": {
                "local_ai_allowed": (
                    str(getattr(settings, "AI_ENGINE", "mock")).lower() == "local"
                ),
                "online_allowed": online_allowed,
                "generation_budget": "REDUCED",
            },
        }
        policy_available = False

    available_models = None
    if (
        str(report.get("recommended_profile", "")).upper() in {"ECO", "PROTECTIVE"}
        and str(getattr(settings, "AI_ENGINE", "mock")).lower() == "local"
        and model_inventory_provider is not None
    ):
        try:
            available_models = set(model_inventory_provider())
        except Exception:
            available_models = None

    return build_execution_plan(
        report,
        available_models=available_models,
        resource_policy_available=policy_available,
    )


def select_rag_model(plan: AIExecutionPlan, available_models: Iterable[str]) -> AIExecutionPlan:
    """Select a grounded-synthesis model without relaxing any resource limits."""
    if not plan.generation_allowed or not plan.local_ai_allowed:
        return plan
    lightweight = str(getattr(settings, "AI_LIGHTWEIGHT_MODEL", "llama3.2:1b"))
    rag_model = str(getattr(settings, "RAG_MODEL", lightweight)).strip() or lightweight
    if plan.resource_profile in {"ECO", "PROTECTIVE"} and rag_model not in {
        lightweight, plan.effective_model,
    }:
        return plan
    if rag_model not in set(available_models):
        return plan
    return replace(plan, effective_model=rag_model, model_reason="RAG_MODEL")
