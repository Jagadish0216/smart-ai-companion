"""Read-only network state collection with NetworkManager support."""

from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any


COMMAND_TIMEOUT_SECONDS = 1.5


def _run(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


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
    ethernet_active = next(
        (item for item in ethernet_devices if item["state"] == "CONNECTED"), None
    )
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
            "supported": bool(ethernet_devices),
            "connected": bool(ethernet_active),
            "interface": (
                ethernet_active["interface"]
                if ethernet_active
                else (
                    ethernet_devices[0]["interface"] if ethernet_devices else None
                )
            ),
            "ip_address": (
                ethernet_active["addresses"][0]
                if ethernet_active and ethernet_active["addresses"]
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
    ethernet_active = next(
        (item for item in ethernet_devices if item["state"] == "CONNECTED"), None
    )
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
            "supported": bool(ethernet_devices),
            "connected": bool(ethernet_active),
            "interface": (
                ethernet_active["interface"]
                if ethernet_active
                else (
                    ethernet_devices[0]["interface"] if ethernet_devices else None
                )
            ),
            "ip_address": ethernet_active["addresses"][0] if ethernet_active else None,
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
