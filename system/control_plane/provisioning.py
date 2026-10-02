"""Deterministic first-boot and recovery Wi-Fi provisioning state machine."""

from __future__ import annotations

import hashlib
import socket
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from system.models import NetworkProvisioningState

from . import network


SETUP_STATES = {
    NetworkProvisioningState.State.SETUP_AP,
    NetworkProvisioningState.State.CONNECTING,
    NetworkProvisioningState.State.FAILED,
}
MACHINE_ID_PATHS = (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id"))
SETUP_SSID_PREFIX = "SmartCompanion"


def _set_state(state: str, last_error: str = "") -> NetworkProvisioningState:
    current = NetworkProvisioningState.get_current()
    current.state = state
    current.last_error = last_error[:255]
    current.save(update_fields=["state", "last_error", "updated_at"])
    return current


def _device_seed() -> str:
    for path in MACHINE_ID_PATHS:
        try:
            value = path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            continue
        if value:
            return value
    try:
        return socket.gethostname() or "smart-ai-companion"
    except OSError:
        return "smart-ai-companion"


def setup_ssid(device_seed: str | None = None) -> str:
    """Return a stable short identifier without disclosing the device MAC."""
    seed = device_seed if device_seed is not None else _device_seed()
    digest = hashlib.sha256(f"smart-ai-companion:{seed}".encode("utf-8")).hexdigest()
    return f"{SETUP_SSID_PREFIX}-{digest[:4].upper()}"


def setup_mode_available() -> bool:
    return NetworkProvisioningState.get_current().state in SETUP_STATES


def get_setup_status() -> dict[str, Any]:
    current = NetworkProvisioningState.get_current()
    result = {
        "state": current.state,
        "setup_ssid": setup_ssid(),
        "canonical_url": settings.COMPANION_CANONICAL_URL,
    }
    if current.last_error:
        result["message"] = current.last_error
    elif current.state == NetworkProvisioningState.State.SETUP_AP:
        result["message"] = "Choose a Wi-Fi network to finish setup."
    elif current.state == NetworkProvisioningState.State.CONNECTING:
        result["message"] = "Companion is connecting to the selected Wi-Fi network."
    return result


def start_setup_access_point(last_error: str = "") -> dict[str, Any]:
    """Ensure the setup AP is active and persist only safe status details."""
    result = network.activate_setup_access_point(
        setup_ssid(),
        getattr(settings, "SETUP_AP_PASSWORD", ""),
    )
    if result["success"]:
        _set_state(NetworkProvisioningState.State.SETUP_AP, last_error)
        return {
            "success": True,
            "state": NetworkProvisioningState.State.SETUP_AP,
            "setup_ssid": setup_ssid(),
            "message": last_error or result["message"],
            "already_active": result.get("already_active", False),
        }
    _set_state(NetworkProvisioningState.State.FAILED, result["message"])
    return {
        "success": False,
        "state": NetworkProvisioningState.State.FAILED,
        "setup_ssid": setup_ssid(),
        "message": result["message"],
    }


def enter_recovery_mode() -> dict[str, Any]:
    """Persist an explicit recovery request and switch the radio to setup AP mode."""
    _set_state(NetworkProvisioningState.State.SETUP_AP)
    return start_setup_access_point()


def _saved_attempt_limit() -> int:
    try:
        configured = int(getattr(settings, "SETUP_SAVED_PROFILE_ATTEMPTS", 5))
    except (TypeError, ValueError):
        return 5
    return max(1, min(configured, 10))


def ensure_network_mode(
    *,
    force_setup: bool = False,
    prefer_client: bool = False,
) -> dict[str, Any]:
    """Choose normal client mode or recovery AP using bounded operations."""
    if force_setup:
        return enter_recovery_mode()

    current = NetworkProvisioningState.get_current()
    if current.state in SETUP_STATES and not prefer_client:
        # SETUP_AP and FAILED are explicit recovery states. CONNECTING means a
        # reboot interrupted provisioning, so the recoverable choice is AP.
        return start_setup_access_point(current.last_error)

    active = network.get_active_wifi_connection()
    if active["connected"]:
        _set_state(NetworkProvisioningState.State.NORMAL_MODE)
        return {
            "success": True,
            "state": NetworkProvisioningState.State.NORMAL_MODE,
            "message": "An active local Wi-Fi connection is available.",
        }

    saved = network.list_saved_wifi_profiles()
    if saved["supported"]:
        for profile_name in saved["profiles"][:_saved_attempt_limit()]:
            network.activate_saved_wifi_profile(profile_name)
            active = network.get_active_wifi_connection()
            if active["connected"]:
                _set_state(NetworkProvisioningState.State.NORMAL_MODE)
                return {
                    "success": True,
                    "state": NetworkProvisioningState.State.NORMAL_MODE,
                    "message": "A saved local Wi-Fi connection is available.",
                }

    return start_setup_access_point()


def _restore_setup_after_failure(message: str) -> dict[str, Any]:
    _set_state(NetworkProvisioningState.State.FAILED, message)
    restored = start_setup_access_point(message)
    if restored["success"]:
        return {
            "success": False,
            "state": NetworkProvisioningState.State.SETUP_AP,
            "ssid": None,
            "message": message,
        }
    return {
        "success": False,
        "state": NetworkProvisioningState.State.FAILED,
        "ssid": None,
        "message": (
            f"{message} The setup network could not be restored; "
            "restart Companion to retry recovery."
        ),
    }


def provision_wifi(ssid: object, password: object = None) -> dict[str, Any]:
    """Switch AP -> client and restore the AP whenever client setup fails."""
    validation_error = network.validate_wifi_credentials(ssid, password)
    if validation_error:
        return {"success": False, "state": "INVALID", "message": validation_error}

    current = NetworkProvisioningState.get_current()
    claimed = NetworkProvisioningState.objects.filter(
        pk=current.pk,
        state__in={
            NetworkProvisioningState.State.SETUP_AP,
            NetworkProvisioningState.State.FAILED,
        },
    ).update(
        state=NetworkProvisioningState.State.CONNECTING,
        last_error="",
        updated_at=timezone.now(),
    )
    if not claimed:
        return {
            "success": False,
            "state": "BUSY",
            "message": "A Wi-Fi setup attempt is already in progress.",
        }

    safe_ssid = str(ssid)
    try:
        stopped = network.deactivate_setup_access_point()
        if not stopped["success"]:
            return _restore_setup_after_failure(stopped["message"])

        connection = network.connect_wifi(safe_ssid, password)
        observed = network.get_network_status().get("wifi", {})
        target_is_active = bool(
            observed.get("connected") and observed.get("ssid") == safe_ssid
        )
        if target_is_active:
            _set_state(NetworkProvisioningState.State.NORMAL_MODE)
            return {
                "success": True,
                "state": NetworkProvisioningState.State.NORMAL_MODE,
                "ssid": safe_ssid,
                "message": (
                    "Connected to Wi-Fi. Rejoin that network, then open "
                    "smart-ai-companion.local:8000."
                ),
                "canonical_url": settings.COMPANION_CANONICAL_URL,
            }

        failure_message = connection.get(
            "message", "Could not connect to the selected network."
        )
        if connection.get("success"):
            failure_message = "The selected Wi-Fi connection could not be verified."
        return _restore_setup_after_failure(failure_message)
    except Exception:
        return _restore_setup_after_failure(
            "The Wi-Fi connection result could not be determined."
        )
