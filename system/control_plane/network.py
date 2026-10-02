"""Read-only network state collection with NetworkManager support."""

from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


COMMAND_TIMEOUT_SECONDS = 1.5
WIFI_SCAN_TIMEOUT_SECONDS = 8.0
WIFI_CONNECT_WAIT_SECONDS = 8
WIFI_CONNECT_TIMEOUT_SECONDS = 10.0
VIRTUAL_ETHERNET_PREFIXES = ("veth", "docker", "br-", "virbr")
PREFERRED_ETHERNET_PREFIXES = ("eth", "en")
MAX_SSID_BYTES = 32
MAX_WIFI_PASSWORD_BYTES = 64


@dataclass(frozen=True)
class _CommandOutcome:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    unavailable: bool = False


def _execute(command: list[str], timeout: float) -> _CommandOutcome:
    """Execute one allowlisted nmcli operation without exposing its arguments."""
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
            env={**os.environ, "LANG": "C", "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired:
        return _CommandOutcome(returncode=None, timed_out=True)
    except (OSError, subprocess.SubprocessError):
        return _CommandOutcome(returncode=None, unavailable=True)
    return _CommandOutcome(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def _run(command: list[str]) -> str | None:
    outcome = _execute(command, COMMAND_TIMEOUT_SECONDS)
    if outcome.returncode != 0:
        return None
    return outcome.stdout.strip()


def _split_terse(line: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for character in line:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def _address_without_prefix(value: str) -> str | None:
    try:
        return str(ipaddress.ip_interface(value).ip)
    except ValueError:
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            return None


def _nmcli_addresses(executable: str, interface: str) -> list[str]:
    output = _run([executable, "-t", "-f", "IP4.ADDRESS", "device", "show", interface])
    addresses: list[str] = []
    for line in (output or "").splitlines():
        _, separator, raw_address = line.partition(":")
        address = _address_without_prefix(raw_address.strip()) if separator else None
        if address and address not in addresses:
            addresses.append(address)
    return addresses


def _nmcli_gateway(executable: str, interface: str) -> str | None:
    output = _run([executable, "-t", "-f", "IP4.GATEWAY", "device", "show", interface])
    for line in (output or "").splitlines():
        _, separator, value = line.partition(":")
        candidate = value.strip() if separator else ""
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    return None


def _nmcli_wifi_details(executable: str, interface: str) -> tuple[str | None, int | None]:
    output = _run(
        [
            executable,
            "-t",
            "-f",
            "IN-USE,SSID,SIGNAL",
            "device",
            "wifi",
            "list",
            "ifname",
            interface,
        ]
    )
    for line in (output or "").splitlines():
        fields = _split_terse(line)
        if len(fields) < 3 or fields[0] != "*":
            continue
        try:
            signal = max(0, min(100, int(fields[-1])))
        except ValueError:
            signal = None
        return fields[1] or None, signal
    return None, None


def _connectivity(executable: str) -> dict[str, bool | str | None]:
    raw = (_run([executable, "-t", "-f", "CONNECTIVITY", "general"]) or "").lower()
    mapping = {
        "full": ("FULL", True),
        "limited": ("LIMITED", False),
        "portal": ("LIMITED", False),
        "none": ("NONE", False),
        "unknown": ("UNKNOWN", None),
    }
    state, available = mapping.get(raw, ("UNKNOWN", None))
    return {"state": state, "available": available}


def _wifi_interface(executable: str) -> tuple[str | None, str]:
    outcome = _execute(
        [executable, "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"],
        COMMAND_TIMEOUT_SECONDS,
    )
    if outcome.timed_out:
        return None, "TIMEOUT"
    if outcome.unavailable or outcome.returncode != 0:
        return None, "ERROR"
    wifi_devices: list[tuple[str, bool]] = []
    for line in outcome.stdout.splitlines():
        fields = _split_terse(line)
        if len(fields) < 3 or fields[1].lower() != "wifi":
            continue
        name = fields[0]
        if name:
            wifi_devices.append(
                (name, fields[2].lower() in {"connected", "activated"})
            )
    if not wifi_devices:
        return None, "NOT_FOUND"
    connected = next((name for name, active in wifi_devices if active), None)
    return connected or wifi_devices[0][0], "OK"


def _normalize_security(value: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized or normalized == "--":
        return "OPEN"
    return normalized.upper()


def _parse_wifi_networks(output: str) -> list[dict[str, Any]]:
    """Parse escaped terse nmcli rows and retain the strongest row per SSID."""
    networks: dict[str, dict[str, Any]] = {}
    for line in output.splitlines():
        fields = _split_terse(line)
        if len(fields) < 4:
            continue
        in_use, ssid, raw_signal, security = fields[:4]
        if not ssid or not ssid.strip():
            continue
        try:
            signal = max(0, min(100, int(raw_signal)))
        except ValueError:
            signal = None
        candidate = {
            "ssid": ssid,
            "signal_percent": signal,
            "security": _normalize_security(security),
            "connected": in_use == "*",
        }
        existing = networks.get(ssid)
        if existing is None:
            networks[ssid] = candidate
            continue
        was_connected = existing["connected"] or candidate["connected"]
        existing_signal = existing["signal_percent"]
        if signal is not None and (
            existing_signal is None or signal > existing_signal
        ):
            networks[ssid] = candidate
        networks[ssid]["connected"] = was_connected
    return sorted(
        networks.values(),
        key=lambda network: (
            not network["connected"],
            -(network["signal_percent"] if network["signal_percent"] is not None else -1),
            network["ssid"].casefold(),
        ),
    )


def scan_wifi_networks() -> dict[str, Any]:
    """Return nearby Wi-Fi networks without changing saved connections."""
    executable = shutil.which("nmcli")
    if not executable:
        return {
            "supported": False,
            "interface": None,
            "networks": [],
            "message": "Wi-Fi scanning is not available on this device.",
        }
    interface, interface_state = _wifi_interface(executable)
    if not interface:
        message = (
            "No Wi-Fi adapter was found."
            if interface_state == "NOT_FOUND"
            else "Wi-Fi adapter status could not be determined."
        )
        return {
            "supported": False,
            "interface": None,
            "networks": [],
            "message": message,
        }
    outcome = _execute(
        [
            executable,
            "-t",
            "--escape",
            "yes",
            "-f",
            "IN-USE,SSID,SIGNAL,SECURITY",
            "device",
            "wifi",
            "list",
            "ifname",
            interface,
            "--rescan",
            "yes",
        ],
        WIFI_SCAN_TIMEOUT_SECONDS,
    )
    if outcome.timed_out:
        message = "Wi-Fi scanning timed out. Please try again."
    elif outcome.unavailable or outcome.returncode != 0:
        message = "Available networks could not be loaded."
    else:
        return {
            "supported": True,
            "interface": interface,
            "networks": _parse_wifi_networks(outcome.stdout),
        }
    return {
        "supported": True,
        "interface": interface,
        "networks": [],
        "message": message,
    }


def _validation_error(ssid: object, password: object) -> str | None:
    if not isinstance(ssid, str) or not ssid or not ssid.strip():
        return "A Wi-Fi network name is required."
    try:
        ssid_bytes = ssid.encode("utf-8")
    except UnicodeError:
        return "The Wi-Fi network name is invalid."
    if len(ssid_bytes) > MAX_SSID_BYTES:
        return "The Wi-Fi network name is too long."
    if ssid.startswith("-") or any(ord(character) < 32 for character in ssid):
        return "The Wi-Fi network name contains unsupported characters."
    if password is None:
        return None
    if not isinstance(password, str):
        return "The Wi-Fi password must be text."
    try:
        password_bytes = password.encode("utf-8")
    except UnicodeError:
        return "The Wi-Fi password is invalid."
    if len(password_bytes) > MAX_WIFI_PASSWORD_BYTES:
        return "The Wi-Fi password is too long."
    if any(ord(character) < 32 for character in password):
        return "The Wi-Fi password contains unsupported characters."
    return None


def _safe_connection_failure(stderr: str) -> str:
    normalized = stderr.casefold()
    if any(marker in normalized for marker in ("secret", "password", "authentication")):
        return "Could not authenticate with the selected network."
    if any(marker in normalized for marker in ("not found", "no network")):
        return "The selected network is no longer available."
    return "Could not connect to the selected network."


def connect_wifi(ssid: object, password: object = None) -> dict[str, Any]:
    """Attempt a bounded NetworkManager Wi-Fi connection."""
    validation_error = _validation_error(ssid, password)
    if validation_error:
        return {
            "success": False,
            "state": "INVALID",
            "message": validation_error,
        }

    safe_ssid = str(ssid)
    safe_password = "" if password is None else str(password)
    executable = shutil.which("nmcli")
    if not executable:
        return {
            "success": False,
            "ssid": safe_ssid,
            "state": "NOT_AVAILABLE",
            "message": "Wi-Fi management is not available on this device.",
        }
    interface, interface_state = _wifi_interface(executable)
    if not interface:
        message = (
            "No Wi-Fi adapter was found."
            if interface_state == "NOT_FOUND"
            else "Wi-Fi adapter status could not be determined."
        )
        return {
            "success": False,
            "ssid": safe_ssid,
            "state": "NOT_AVAILABLE",
            "message": message,
        }

    command = [
        executable,
        "--wait",
        str(WIFI_CONNECT_WAIT_SECONDS),
        "device",
        "wifi",
        "connect",
        safe_ssid,
        "ifname",
        interface,
    ]
    if safe_password:
        command.extend(["password", safe_password])
    outcome = _execute(command, WIFI_CONNECT_TIMEOUT_SECONDS)
    if outcome.timed_out:
        return {
            "success": False,
            "ssid": safe_ssid,
            "state": "UNKNOWN",
            "message": (
                "The connection request timed out and its result is unknown. "
                "The Companion may be temporarily unreachable."
            ),
        }
    if outcome.unavailable:
        return {
            "success": False,
            "ssid": safe_ssid,
            "state": "UNKNOWN",
            "message": "The connection result could not be determined.",
        }
    if outcome.returncode == 0:
        return {
            "success": True,
            "ssid": safe_ssid,
            "state": "CONNECTED",
            "message": "Connected successfully.",
        }
    if outcome.returncode == 3:
        return {
            "success": False,
            "ssid": safe_ssid,
            "state": "CONNECTING",
            "message": (
                "The connection is still being applied. The Companion may be "
                "temporarily unreachable."
            ),
        }
    return {
        "success": False,
        "ssid": safe_ssid,
        "state": "FAILED",
        "message": _safe_connection_failure(outcome.stderr),
    }


def _primary_ethernet(
    ethernet_devices: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Prefer a physical Ethernet adapter and never promote container links."""
    candidates = []
    for device in ethernet_devices:
        name = str(device.get("interface", "")).lower()
        if not name or name == "lo" or name.startswith(VIRTUAL_ETHERNET_PREFIXES):
            continue
        candidates.append(device)
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda device: (
            not str(device["interface"]).lower().startswith(
                PREFERRED_ETHERNET_PREFIXES
            ),
            device.get("state") != "CONNECTED",
            str(device["interface"]).lower(),
        ),
    )


def _from_nmcli(executable: str) -> dict[str, Any] | None:
    output = _run(
        [
            executable,
            "-t",
            "-f",
            "DEVICE,TYPE,STATE,CONNECTION",
            "device",
            "status",
        ]
    )
    if output is None:
        return None
    interfaces: list[dict[str, Any]] = []
    wifi_devices: list[dict[str, Any]] = []
    ethernet_devices: list[dict[str, Any]] = []
    for line in output.splitlines():
        fields = _split_terse(line)
        if len(fields) < 3:
            continue
        name, device_type, state = fields[:3]
        if not name or name == "lo" or device_type not in {"wifi", "ethernet"}:
            continue
        connected = state.lower() in {"connected", "activated"}
        addresses = _nmcli_addresses(executable, name) if connected else []
        entry = {
            "interface": name,
            "type": device_type.upper(),
            "state": "CONNECTED" if connected else "DISCONNECTED",
            "addresses": addresses,
        }
        interfaces.append(entry)
        (wifi_devices if device_type == "wifi" else ethernet_devices).append(entry)

    wifi_active = next(
        (item for item in wifi_devices if item["state"] == "CONNECTED"), None
    )
    primary_ethernet = _primary_ethernet(ethernet_devices)
    ssid, signal = (
        _nmcli_wifi_details(executable, wifi_active["interface"])
        if wifi_active
        else (None, None)
    )
    default_route = None
    for entry in [item for item in interfaces if item["state"] == "CONNECTED"]:
        gateway = _nmcli_gateway(executable, entry["interface"])
        if gateway:
            default_route = {"interface": entry["interface"], "gateway": gateway}
            break

    return {
        "wifi": {
            "supported": bool(wifi_devices),
            "connected": bool(wifi_active),
            "interface": (
                wifi_active["interface"]
                if wifi_active
                else (wifi_devices[0]["interface"] if wifi_devices else None)
            ),
            "ssid": ssid,
            "signal_percent": signal,
            "ip_address": (
                wifi_active["addresses"][0]
                if wifi_active and wifi_active["addresses"]
                else None
            ),
        },
        "ethernet": {
            "supported": primary_ethernet is not None,
            "connected": bool(
                primary_ethernet
                and primary_ethernet["state"] == "CONNECTED"
            ),
            "interface": (
                primary_ethernet["interface"] if primary_ethernet else None
            ),
            "ip_address": (
                primary_ethernet["addresses"][0]
                if primary_ethernet and primary_ethernet["addresses"]
                else None
            ),
        },
        "interfaces": interfaces,
        "default_route": default_route,
        "internet": _connectivity(executable),
    }


def _linux_ipv4_address(interface: str) -> str | None:
    if os.name == "nt":
        return None
    try:
        import fcntl
        import struct

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as descriptor:
            packed = struct.pack("256s", interface[:15].encode("utf-8"))
            result = fcntl.ioctl(descriptor.fileno(), 0x8915, packed)
        return socket.inet_ntoa(result[20:24])
    except (ImportError, OSError, UnicodeError):
        return None


def _fallback_interfaces() -> list[dict[str, Any]]:
    try:
        names = [name for _, name in socket.if_nameindex() if name.lower() != "lo"]
    except OSError:
        names = []
    interfaces: list[dict[str, Any]] = []
    for name in names:
        sysfs = Path("/sys/class/net") / name
        is_wifi = (sysfs / "wireless").exists()
        is_ethernet = sysfs.exists() and not is_wifi
        if not (is_wifi or is_ethernet):
            continue
        operstate = ""
        try:
            operstate = (sysfs / "operstate").read_text(encoding="ascii").strip().lower()
        except OSError:
            pass
        address = _linux_ipv4_address(name)
        connected = operstate == "up" and address is not None
        interfaces.append(
            {
                "interface": name,
                "type": "WIFI" if is_wifi else "ETHERNET",
                "state": "CONNECTED" if connected else "DISCONNECTED",
                "addresses": [address] if address else [],
            }
        )
    return interfaces


def _fallback_status() -> dict[str, Any]:
    interfaces = _fallback_interfaces()
    wifi_devices = [item for item in interfaces if item["type"] == "WIFI"]
    ethernet_devices = [item for item in interfaces if item["type"] == "ETHERNET"]
    wifi_active = next(
        (item for item in wifi_devices if item["state"] == "CONNECTED"), None
    )
    primary_ethernet = _primary_ethernet(ethernet_devices)
    return {
        "wifi": {
            "supported": bool(wifi_devices),
            "connected": bool(wifi_active),
            "interface": (
                wifi_active["interface"]
                if wifi_active
                else (wifi_devices[0]["interface"] if wifi_devices else None)
            ),
            "ssid": None,
            "signal_percent": None,
            "ip_address": wifi_active["addresses"][0] if wifi_active else None,
        },
        "ethernet": {
            "supported": primary_ethernet is not None,
            "connected": bool(
                primary_ethernet
                and primary_ethernet["state"] == "CONNECTED"
            ),
            "interface": (
                primary_ethernet["interface"] if primary_ethernet else None
            ),
            "ip_address": (
                primary_ethernet["addresses"][0]
                if primary_ethernet and primary_ethernet["addresses"]
                else None
            ),
        },
        "interfaces": interfaces,
        "default_route": None,
        "internet": {"state": "UNKNOWN", "available": None},
    }


def get_network_status() -> dict[str, Any]:
    """Return network state without scanning, connecting, or changing configuration."""
    executable = shutil.which("nmcli")
    status = _from_nmcli(executable) if executable else None
    result = status or _fallback_status()
    try:
        hostname = socket.gethostname() or "UNKNOWN"
    except OSError:
        hostname = "UNKNOWN"
    result["hostname"] = hostname
    return result
