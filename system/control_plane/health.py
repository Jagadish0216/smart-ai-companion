"""Bounded, read-only checks for local companion services."""

from __future__ import annotations

import math
import socket
import time
from typing import Any
from urllib import error, parse, request

from django.conf import settings


DEFAULT_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 2.0


def _timeout() -> float:
    try:
        configured = float(
            getattr(settings, "CONTROL_PLANE_HEALTH_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        )
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    if not math.isfinite(configured) or configured <= 0:
        return DEFAULT_TIMEOUT_SECONDS
    return min(configured, MAX_TIMEOUT_SECONDS)


def _result(status: str, started: float, detail: str) -> dict[str, Any]:
    return {
        "status": status,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "detail": detail,
    }


def _safe_http_url(base_url: object, path: str) -> str | None:
    try:
        parsed = parse.urlsplit(str(base_url).strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return None
        # Accessing port validates malformed and out-of-range values.
        parsed.port
    except (TypeError, ValueError):
        return None
    return str(base_url).strip().rstrip("/") + "/" + path.lstrip("/")


def _http_check(base_url: object, path: str, service_name: str) -> dict[str, Any]:
    started = time.perf_counter()
    url = _safe_http_url(base_url, path)
    if url is None:
        return _result("UNKNOWN", started, f"{service_name} configuration is invalid.")
    health_request = request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "smart-ai-companion-health/1",
        },
        method="GET",
    )
    try:
        with request.urlopen(health_request, timeout=_timeout()) as response:
            response.read(1)
            status_code = response.getcode()
        if 200 <= status_code < 400:
            return _result("READY", started, f"{service_name} responded successfully.")
        return _result(
            "UNAVAILABLE", started, f"{service_name} returned HTTP {status_code}."
        )
    except error.HTTPError as exc:
        return _result("UNAVAILABLE", started, f"{service_name} returned HTTP {exc.code}.")
    except (error.URLError, OSError, TimeoutError, ValueError):
        return _result(
            "UNAVAILABLE", started, f"{service_name} did not respond in time."
        )


def _mosquitto_check() -> dict[str, Any]:
    started = time.perf_counter()
    host = getattr(settings, "MQTT_HOST", "127.0.0.1")
    try:
        port = int(getattr(settings, "MQTT_PORT", 1883))
        if not isinstance(host, str) or not host.strip() or not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        return _result("UNKNOWN", started, "Mosquitto configuration is invalid.")
    try:
        with socket.create_connection((host.strip(), port), timeout=_timeout()):
            pass
        return _result("READY", started, "Mosquitto accepted a TCP connection.")
    except (OSError, TimeoutError):
        return _result(
            "UNAVAILABLE",
            started,
            "Mosquitto did not accept a TCP connection in time.",
        )


def _guarded_check(check: Any, service_name: str) -> dict[str, Any]:
    """Keep an unexpected failure in one probe from breaking the endpoint."""
    started = time.perf_counter()
    try:
        return check()
    except Exception:  # The response intentionally omits internal exception details.
        return _result("UNKNOWN", started, f"{service_name} health could not be determined.")


def get_service_health() -> dict[str, dict[str, Any]]:
    """Check configured local services; one failure never aborts other checks."""
    ollama = _guarded_check(
        lambda: _http_check(
            getattr(settings, "OLLAMA_HOST", "http://localhost:11434"),
            "api/tags",
            "Ollama",
        ),
        "Ollama",
    )
    searxng = _guarded_check(
        lambda: _http_check(
            getattr(settings, "SEARXNG_BASE_URL", "http://127.0.0.1:8888"),
            "",
            "SearXNG",
        ),
        "SearXNG",
    )
    return {
        "django": {
            "status": "READY",
            "latency_ms": 0,
            "detail": "Control-plane API is operational.",
        },
        "ollama": ollama,
        "searxng": searxng,
        "mosquitto": _guarded_check(_mosquitto_check, "Mosquitto"),
    }
