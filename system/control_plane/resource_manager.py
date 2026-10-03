"""Read-only resource aggregation and deterministic edge policy decisions."""

from __future__ import annotations

import time
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from system.models import NetworkProvisioningState

from .health import get_service_health
from .network import get_network_status
from .resources import get_resource_metrics


RESOURCE_MANAGER_CACHE_KEY = "system.resource-manager.v1"
LEVELS = ("HEALTHY", "CAUTION", "CONSTRAINED", "CRITICAL", "UNKNOWN")
LEVEL_RANK = {"HEALTHY": 0, "UNKNOWN": 1, "CAUTION": 2, "CONSTRAINED": 3, "CRITICAL": 4}
CURRENT_THROTTLE_FLAGS = (
    "under_voltage",
    "frequency_capped",
    "currently_throttled",
    "soft_temperature_limit",
)
HISTORICAL_THROTTLE_FLAGS = (
    "under_voltage_occurred",
    "frequency_cap_occurred",
    "throttling_occurred",
    "soft_temperature_limit_occurred",
)


def _threshold(name: str, default: float) -> float:
    try:
        return float(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def get_resource_thresholds() -> dict[str, float]:
    return {
        "cpu_caution_percent": _threshold("RESOURCE_CPU_CAUTION_PERCENT", 80.0),
        "cpu_constrained_percent": _threshold(
            "RESOURCE_CPU_CONSTRAINED_PERCENT", 95.0
        ),
        "load_caution_per_cpu": _threshold("RESOURCE_LOAD_CAUTION_PER_CPU", 1.0),
        "load_constrained_per_cpu": _threshold(
            "RESOURCE_LOAD_CONSTRAINED_PER_CPU", 1.5
        ),
        "memory_caution_percent": _threshold(
            "RESOURCE_MEMORY_CAUTION_PERCENT", 80.0
        ),
        "memory_constrained_percent": _threshold(
            "RESOURCE_MEMORY_CONSTRAINED_PERCENT", 90.0
        ),
        "memory_critical_percent": _threshold(
            "RESOURCE_MEMORY_CRITICAL_PERCENT", 95.0
        ),
        "storage_caution_percent": _threshold(
            "RESOURCE_STORAGE_CAUTION_PERCENT", 85.0
        ),
        "storage_critical_percent": _threshold(
            "RESOURCE_STORAGE_CRITICAL_PERCENT", 95.0
        ),
        "storage_caution_free_bytes": _threshold(
            "RESOURCE_STORAGE_CAUTION_FREE_BYTES", 5 * 1024 ** 3
        ),
        "storage_critical_free_bytes": _threshold(
            "RESOURCE_STORAGE_CRITICAL_FREE_BYTES", 1024 ** 3
        ),
        "temperature_caution_c": _threshold("RESOURCE_TEMP_CAUTION_C", 65.0),
        "temperature_constrained_c": _threshold(
            "RESOURCE_TEMP_CONSTRAINED_C", 75.0
        ),
        "temperature_critical_c": _threshold("RESOURCE_TEMP_CRITICAL_C", 80.0),
        "performance_cpu_max_percent": _threshold(
            "RESOURCE_PERFORMANCE_CPU_MAX_PERCENT", 40.0
        ),
        "performance_memory_max_percent": _threshold(
            "RESOURCE_PERFORMANCE_MEMORY_MAX_PERCENT", 65.0
        ),
        "performance_storage_max_percent": _threshold(
            "RESOURCE_PERFORMANCE_STORAGE_MAX_PERCENT", 75.0
        ),
        "performance_temperature_max_c": _threshold(
            "RESOURCE_PERFORMANCE_TEMP_MAX_C", 60.0
        ),
    }


def _unknown_network() -> dict[str, Any]:
    return {
        "wifi": {"connected": None, "ssid": None},
        "ethernet": {"connected": None},
        "internet": {"state": "UNKNOWN", "available": None},
    }


def _unknown_services() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "status": "UNKNOWN",
            "latency_ms": None,
            "detail": f"{label} health could not be determined.",
        }
        for name, label in (
            ("django", "Django"),
            ("ollama", "Ollama"),
            ("searxng", "SearXNG"),
            ("mosquitto", "Mosquitto"),
        )
    }


def collect_resource_snapshot() -> dict[str, Any]:
    """Collect each existing telemetry source independently and honestly."""
    try:
        metrics = get_resource_metrics()
    except Exception:
        metrics = {}
    try:
        network = get_network_status()
    except Exception:
        network = _unknown_network()
    try:
        services = get_service_health()
    except Exception:
        services = _unknown_services()
    try:
        provisioning_state = NetworkProvisioningState.get_current().state
    except Exception:
        provisioning_state = "UNKNOWN"

    wifi_connected = network.get("wifi", {}).get("connected")
    ethernet_connected = network.get("ethernet", {}).get("connected")
    if wifi_connected is None and ethernet_connected is None:
        lan_connected = None
    else:
        lan_connected = bool(wifi_connected or ethernet_connected)

    throttling = metrics.get("throttled")
    return {
        "snapshot_generated_at": timezone.now().isoformat(),
        "host": {
            "device_name": metrics.get("device_name"),
            "hostname": metrics.get("hostname"),
            "platform": metrics.get("platform"),
            "architecture": metrics.get("architecture"),
            "uptime_seconds": metrics.get("uptime_seconds"),
        },
        "cpu": {
            "utilization_percent": metrics.get("cpu_percent"),
            "logical_count": metrics.get("cpu_count"),
            "load_average": {
                "one_minute": metrics.get("load_1m"),
                "five_minutes": metrics.get("load_5m"),
                "fifteen_minutes": metrics.get("load_15m"),
            },
        },
        "memory": {
            "total_bytes": metrics.get("ram_total_bytes"),
            "used_bytes": metrics.get("ram_used_bytes"),
            "available_bytes": metrics.get("ram_available_bytes"),
            "used_percent": metrics.get("ram_percent"),
        },
        "storage": {
            "total_bytes": metrics.get("storage_total_bytes"),
            "used_bytes": metrics.get("storage_used_bytes"),
            "free_bytes": metrics.get("storage_free_bytes"),
            "used_percent": metrics.get("storage_percent"),
        },
        "thermal": {
            "cpu_temperature_c": metrics.get("temperature_c"),
            "throttling_supported": throttling is not None,
            "throttling": throttling,
        },
        "network": {
            "lan_connected": lan_connected,
            "wifi_connected": wifi_connected,
            "ethernet_connected": ethernet_connected,
            "internet_state": str(
                network.get("internet", {}).get("state", "UNKNOWN")
            ).upper(),
            "internet_available": network.get("internet", {}).get("available"),
        },
        "services": services,
        "ai": {
            "configured_engine": str(getattr(settings, "AI_ENGINE", "mock")),
            "configured_model": str(getattr(settings, "OLLAMA_MODEL", "Unknown")),
            "online_retrieval_enabled": bool(
                getattr(settings, "ONLINE_RETRIEVAL_ENABLED", False)
            ),
        },
        "provisioning": {"state": provisioning_state},
    }


def _component(level: str, summary: str) -> dict[str, str]:
    return {"level": level, "summary": summary}


def _classify_cpu(snapshot: dict[str, Any], limits: dict[str, float]) -> dict[str, str]:
    cpu = snapshot["cpu"]
    percent = cpu.get("utilization_percent")
    count = cpu.get("logical_count")
    load = cpu.get("load_average", {}).get("five_minutes")
    normalized_load = (
        float(load) / float(count)
        if isinstance(load, (int, float)) and isinstance(count, int) and count > 0
        else None
    )
    constrained = (
        isinstance(percent, (int, float))
        and percent >= limits["cpu_constrained_percent"]
    ) or (
        normalized_load is not None
        and normalized_load >= limits["load_constrained_per_cpu"]
    )
    caution = (
        isinstance(percent, (int, float)) and percent >= limits["cpu_caution_percent"]
    ) or (
        normalized_load is not None
        and normalized_load >= limits["load_caution_per_cpu"]
    )
    if constrained:
        return _component("CONSTRAINED", "CPU demand is near sustained capacity.")
    if caution:
        return _component("CAUTION", "CPU demand is elevated.")
    if percent is None and normalized_load is None:
        return _component("UNKNOWN", "CPU utilization could not be measured.")
    return _component("HEALTHY", "CPU demand is within the preferred range.")


def _classify_memory(snapshot: dict[str, Any], limits: dict[str, float]) -> dict[str, str]:
    percent = snapshot["memory"].get("used_percent")
    if not isinstance(percent, (int, float)):
        return _component("UNKNOWN", "Memory pressure could not be measured.")
    if percent >= limits["memory_critical_percent"]:
        return _component("CRITICAL", "Available memory is critically low.")
    if percent >= limits["memory_constrained_percent"]:
        return _component("CONSTRAINED", "Memory pressure is high.")
    if percent >= limits["memory_caution_percent"]:
        return _component("CAUTION", "Memory pressure is elevated.")
    return _component("HEALTHY", "Memory availability is healthy.")


def _classify_storage(snapshot: dict[str, Any], limits: dict[str, float]) -> dict[str, str]:
    storage = snapshot["storage"]
    percent = storage.get("used_percent")
    free = storage.get("free_bytes")
    if not isinstance(percent, (int, float)) or not isinstance(free, (int, float)):
        return _component("UNKNOWN", "Storage capacity could not be measured.")
    if (
        percent >= limits["storage_critical_percent"]
        or free <= limits["storage_critical_free_bytes"]
    ):
        return _component("CRITICAL", "Free storage is dangerously low.")
    if (
        percent >= limits["storage_caution_percent"]
        or free <= limits["storage_caution_free_bytes"]
    ):
        return _component("CAUTION", "Free storage is becoming low.")
    return _component("HEALTHY", "Storage capacity is healthy.")


def _classify_temperature(snapshot: dict[str, Any], limits: dict[str, float]) -> dict[str, str]:
    temperature = snapshot["thermal"].get("cpu_temperature_c")
    if not isinstance(temperature, (int, float)):
        return _component("UNKNOWN", "CPU temperature is unavailable.")
    if temperature >= limits["temperature_critical_c"]:
        return _component("CRITICAL", "CPU temperature is critical.")
    if temperature >= limits["temperature_constrained_c"]:
        return _component("CONSTRAINED", "CPU temperature is high.")
    if temperature >= limits["temperature_caution_c"]:
        return _component("CAUTION", "CPU temperature is warm.")
    return _component("HEALTHY", "CPU temperature is normal.")


def _classify_throttling(snapshot: dict[str, Any]) -> dict[str, str]:
    flags = snapshot["thermal"].get("throttling")
    if not isinstance(flags, dict):
        return _component("UNKNOWN", "Raspberry Pi throttling status is unavailable.")
    if any(bool(flags.get(name)) for name in CURRENT_THROTTLE_FLAGS):
        return _component("CRITICAL", "Current power or thermal throttling is active.")
    if any(bool(flags.get(name)) for name in HISTORICAL_THROTTLE_FLAGS):
        return _component("CAUTION", "A power or thermal throttle occurred previously.")
    return _component("HEALTHY", "No throttling flags are present.")


def _classify_network(snapshot: dict[str, Any]) -> dict[str, str]:
    network = snapshot["network"]
    lan = network.get("lan_connected")
    internet = network.get("internet_state")
    provisioning = snapshot["provisioning"].get("state")
    if lan is None:
        return _component("UNKNOWN", "Local network state could not be determined.")
    if not lan:
        level = "CAUTION" if provisioning != "NORMAL_MODE" else "CONSTRAINED"
        return _component(level, "No client LAN connection is currently active.")
    if internet == "NONE":
        return _component("CAUTION", "LAN is available without Internet access.")
    if internet == "UNKNOWN":
        return _component("UNKNOWN", "LAN is available; Internet state is unknown.")
    return _component("HEALTHY", "Local network connectivity is available.")


def _service_status(snapshot: dict[str, Any], name: str) -> str:
    return str(snapshot["services"].get(name, {}).get("status", "UNKNOWN")).upper()


def _classify_services(snapshot: dict[str, Any]) -> dict[str, str]:
    relevant = ["django"]
    if snapshot["ai"]["configured_engine"].lower() == "local":
        relevant.append("ollama")
    if snapshot["ai"]["online_retrieval_enabled"]:
        relevant.append("searxng")
    if bool(getattr(settings, "DEVICE_CONTROL_ENABLED", False)):
        relevant.append("mosquitto")
    states = [_service_status(snapshot, name) for name in relevant]
    if "UNAVAILABLE" in states:
        return _component("CONSTRAINED", "A configured local service is unavailable.")
    if "UNKNOWN" in states:
        return _component("UNKNOWN", "A configured service state is unknown.")
    return _component("HEALTHY", "Configured local services are ready.")


def _classify_ai(snapshot: dict[str, Any]) -> dict[str, str]:
    engine = snapshot["ai"]["configured_engine"].lower()
    if engine == "local":
        ollama = _service_status(snapshot, "ollama")
        if ollama == "READY":
            return _component("HEALTHY", "Configured local AI service is ready.")
        if ollama == "UNKNOWN":
            return _component("UNKNOWN", "Local AI availability is unknown.")
        return _component("CONSTRAINED", "Configured local AI service is unavailable.")
    return _component("HEALTHY", f"Configured AI engine is {engine or 'unknown'}.")


def _classify_provisioning(snapshot: dict[str, Any]) -> dict[str, str]:
    state = snapshot["provisioning"].get("state")
    if state == "NORMAL_MODE":
        return _component("HEALTHY", "Network provisioning is complete.")
    if state == "UNKNOWN":
        return _component("UNKNOWN", "Provisioning state could not be determined.")
    return _component("CAUTION", f"Network provisioning state is {state}.")


def _overall_level(health: dict[str, dict[str, str]]) -> str:
    levels = [component["level"] for component in health.values()]
    known = [level for level in levels if level != "UNKNOWN"]
    highest = max(known, key=lambda level: LEVEL_RANK[level], default="HEALTHY")
    if highest == "HEALTHY" and "UNKNOWN" in levels:
        return "UNKNOWN"
    return highest


def _reason(code: str, severity: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def _policy_reasons(
    snapshot: dict[str, Any],
    health: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    mappings = (
        ("throttling", "THROTTLING_ACTIVE", "THROTTLING_HISTORY", "THROTTLING_UNKNOWN"),
        ("temperature", "TEMPERATURE_PRESSURE", "TEMPERATURE_WARM", "TEMPERATURE_UNKNOWN"),
        ("memory", "MEMORY_PRESSURE", "MEMORY_PRESSURE", "MEMORY_UNKNOWN"),
        ("storage", "STORAGE_LOW", "STORAGE_LOW", "STORAGE_UNKNOWN"),
        ("cpu", "CPU_PRESSURE", "CPU_PRESSURE", "CPU_UNKNOWN"),
    )
    for name, severe_code, caution_code, unknown_code in mappings:
        component = health[name]
        level = component["level"]
        if level in {"CRITICAL", "CONSTRAINED"}:
            reasons.append(_reason(severe_code, level, component["summary"]))
        elif level == "CAUTION":
            reasons.append(_reason(caution_code, level, component["summary"]))
        elif level == "UNKNOWN":
            reasons.append(_reason(unknown_code, level, component["summary"]))

    if health["network"]["level"] != "HEALTHY":
        internet = snapshot["network"].get("internet_state")
        code = "INTERNET_OFFLINE" if internet == "NONE" else "NETWORK_LIMITED"
        reasons.append(_reason(code, health["network"]["level"], health["network"]["summary"]))
    if health["services"]["level"] != "HEALTHY":
        reasons.append(
            _reason("SERVICE_UNAVAILABLE", health["services"]["level"], health["services"]["summary"])
        )
    if health["provisioning"]["level"] != "HEALTHY":
        reasons.append(
            _reason(
                "PROVISIONING_INCOMPLETE",
                health["provisioning"]["level"],
                health["provisioning"]["summary"],
            )
        )
    return reasons


def _has_performance_headroom(
    snapshot: dict[str, Any], limits: dict[str, float]
) -> bool:
    values = (
        (snapshot["cpu"].get("utilization_percent"), limits["performance_cpu_max_percent"]),
        (snapshot["memory"].get("used_percent"), limits["performance_memory_max_percent"]),
        (snapshot["storage"].get("used_percent"), limits["performance_storage_max_percent"]),
        (snapshot["thermal"].get("cpu_temperature_c"), limits["performance_temperature_max_c"]),
    )
    return all(isinstance(value, (int, float)) and value <= maximum for value, maximum in values)


def evaluate_resource_policy(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Classify a snapshot and produce deterministic, non-mutating advice."""
    limits = get_resource_thresholds()
    health = {
        "cpu": _classify_cpu(snapshot, limits),
        "memory": _classify_memory(snapshot, limits),
        "storage": _classify_storage(snapshot, limits),
        "temperature": _classify_temperature(snapshot, limits),
        "throttling": _classify_throttling(snapshot),
        "network": _classify_network(snapshot),
        "services": _classify_services(snapshot),
        "ai": _classify_ai(snapshot),
        "provisioning": _classify_provisioning(snapshot),
    }
    overall = _overall_level(health)
    reasons = _policy_reasons(snapshot, health)
    resource_levels = {
        health[name]["level"]
        for name in ("cpu", "memory", "storage", "temperature", "throttling")
    }
    if "CRITICAL" in resource_levels:
        profile = "PROTECTIVE"
    elif resource_levels.intersection({"CAUTION", "CONSTRAINED"}):
        profile = "ECO"
    elif resource_levels == {"HEALTHY"} and _has_performance_headroom(snapshot, limits):
        profile = "PERFORMANCE"
    else:
        profile = "BALANCED"

    safety_unknown = bool(
        {health["temperature"]["level"], health["throttling"]["level"]}
        & {"UNKNOWN"}
    )
    ollama_ready = _service_status(snapshot, "ollama") == "READY"
    local_configured = snapshot["ai"]["configured_engine"].lower() == "local"
    ai_recommendation = {
        "local_ai_allowed": bool(
            local_configured and ollama_ready and profile != "PROTECTIVE"
        ),
        "preferred_model_class": (
            "LIGHTWEIGHT"
            if profile in {"ECO", "PROTECTIVE"} or safety_unknown
            else "STANDARD"
        ),
        "generation_budget": {
            "PROTECTIVE": "MINIMAL",
            "ECO": "REDUCED",
        }.get(profile, "REDUCED" if safety_unknown else "NORMAL"),
        "online_allowed": bool(
            snapshot["ai"]["online_retrieval_enabled"]
            and snapshot["network"]["internet_state"] in {"FULL", "LIMITED"}
        ),
    }

    storage_level = health["storage"]["level"]
    storage_actions: list[str] = []
    if storage_level in {"CAUTION", "CRITICAL"}:
        storage_actions.append("CLEANUP_RECOMMENDED")
    if storage_level == "CRITICAL":
        storage_actions.append("MODEL_DOWNLOADS_BLOCKED")
    storage_recommendation = {
        "state": storage_level,
        "actions": storage_actions,
    }

    first_pressure = next(
        (reason["message"] for reason in reasons if reason["severity"] != "UNKNOWN"),
        None,
    )
    explanation = {
        "PROTECTIVE": first_pressure or "Heavy local AI work should be avoided.",
        "ECO": first_pressure or "Lightweight local AI operation is recommended.",
        "PERFORMANCE": "Resources have ample headroom for normal local AI operation.",
        "BALANCED": "System resources support normal local AI operation.",
    }[profile]
    return {
        "subsystem_health": health,
        "overall_health": overall,
        "recommended_profile": profile,
        "explanation": explanation,
        "reasons": reasons,
        "ai_recommendation": ai_recommendation,
        "storage_recommendation": storage_recommendation,
        "thresholds": limits,
    }


def get_resource_manager_report(*, use_cache: bool = True) -> dict[str, Any]:
    if use_cache:
        cached = cache.get(RESOURCE_MANAGER_CACHE_KEY)
        if isinstance(cached, dict):
            return cached

    total_started = time.perf_counter()
    collection_started = time.perf_counter()
    snapshot = collect_resource_snapshot()
    collection_ms = round((time.perf_counter() - collection_started) * 1000, 2)
    policy_started = time.perf_counter()
    policy = evaluate_resource_policy(snapshot)
    policy_ms = round((time.perf_counter() - policy_started) * 1000, 2)
    report = {
        "snapshot": snapshot,
        **policy,
        "timing": {
            "collection_ms": collection_ms,
            "policy_ms": policy_ms,
            "total_ms": round((time.perf_counter() - total_started) * 1000, 2),
        },
    }
    if use_cache:
        try:
            timeout = max(0, int(getattr(settings, "RESOURCE_MANAGER_CACHE_SECONDS", 5)))
        except (TypeError, ValueError):
            timeout = 5
        if timeout:
            cache.set(RESOURCE_MANAGER_CACHE_KEY, report, timeout)
    return report
