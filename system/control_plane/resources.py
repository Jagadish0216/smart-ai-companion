"""Truthful, dependency-free host resource telemetry."""

from __future__ import annotations

import ctypes
import os
import platform as platform_module
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


CPU_SAMPLE_SECONDS = 0.1
COMMAND_TIMEOUT_SECONDS = 1.0
GIB = 1024 ** 3


def _round(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None else None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip("\x00\n ")
    except (OSError, UnicodeError):
        return None


def _linux_cpu_times() -> tuple[int, int] | None:
    text = _read_text(Path("/proc/stat"))
    if not text:
        return None
    first_line = text.splitlines()[0].split()
    if not first_line or first_line[0] != "cpu":
        return None
    try:
        values = [int(value) for value in first_line[1:]]
    except ValueError:
        return None
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def _windows_cpu_times() -> tuple[int, int] | None:
    if os.name != "nt":
        return None
    try:
        from ctypes import wintypes

        idle = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return None

        def as_int(value: Any) -> int:
            return (value.dwHighDateTime << 32) | value.dwLowDateTime

        return as_int(kernel) + as_int(user), as_int(idle)
    except (AttributeError, OSError):
        return None


def _cpu_percent() -> float | None:
    reader = _windows_cpu_times if os.name == "nt" else _linux_cpu_times
    before = reader()
    if before is None:
        return None
    time.sleep(CPU_SAMPLE_SECONDS)
    after = reader()
    if after is None:
        return None
    total_delta = after[0] - before[0]
    idle_delta = after[1] - before[1]
    if total_delta <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * (total_delta - idle_delta) / total_delta))


def _linux_memory() -> dict[str, float | None] | None:
    text = _read_text(Path("/proc/meminfo"))
    if not text:
        return None
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        try:
            values[key] = int(value.strip().split()[0]) * 1024
        except (ValueError, IndexError):
            continue
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if available is None and total is not None:
        available = sum(
            values.get(key, 0) for key in ("MemFree", "Buffers", "Cached")
        )
    if not total or available is None:
        return None
    used = max(0, total - available)
    return {
        "total": float(total),
        "available": float(available),
        "used": float(used),
        "percent": min(100.0, 100.0 * used / total),
    }


def _windows_memory() -> dict[str, float | None] | None:
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        total = float(status.ullTotalPhys)
        available = float(status.ullAvailPhys)
        return {
            "total": total,
            "available": available,
            "used": max(0.0, total - available),
            "percent": float(status.dwMemoryLoad),
        }
    except (AttributeError, OSError):
        return None


def _posix_memory() -> dict[str, float | None] | None:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        total = float(page_size * os.sysconf("SC_PHYS_PAGES"))
        available = float(page_size * os.sysconf("SC_AVPHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None
    if total <= 0:
        return None
    used = max(0.0, total - available)
    return {
        "total": total,
        "available": available,
        "used": used,
        "percent": min(100.0, 100.0 * used / total),
    }


def _memory() -> dict[str, float | None]:
    measured = (
        _windows_memory()
        if os.name == "nt"
        else (_linux_memory() or _posix_memory())
    )
    return measured or {
        "total": None,
        "available": None,
        "used": None,
        "percent": None,
    }


def _storage() -> dict[str, float | None]:
    root = Path.cwd().anchor or "/"
    try:
        usage = shutil.disk_usage(root)
    except OSError:
        return {"total": None, "used": None, "free": None, "percent": None}
    percent = 100.0 * usage.used / usage.total if usage.total else None
    return {
        "total": float(usage.total),
        "used": float(usage.used),
        "free": float(usage.free),
        "percent": percent,
    }


def _uptime_seconds() -> float | None:
    if os.name == "nt":
        try:
            get_tick_count = ctypes.windll.kernel32.GetTickCount64
            get_tick_count.restype = ctypes.c_ulonglong
            return get_tick_count() / 1000.0
        except (AttributeError, OSError):
            return None
    text = _read_text(Path("/proc/uptime"))
    if text:
        try:
            return float(text.split()[0])
        except (ValueError, IndexError):
            pass
    try:
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError):
        return None


def _load_average() -> tuple[float | None, float | None, float | None]:
    try:
        values = os.getloadavg()
        return tuple(round(value, 2) for value in values)  # type: ignore[return-value]
    except (AttributeError, OSError):
        return None, None, None


def _temperature_c() -> float | None:
    thermal_root = Path("/sys/class/thermal")
    try:
        zones = list(thermal_root.glob("thermal_zone*"))
    except OSError:
        zones = []
    preferred: list[Path] = []
    other: list[Path] = []
    for zone in zones:
        zone_type = (_read_text(zone / "type") or "").lower()
        target = preferred if any(
            marker in zone_type for marker in ("cpu", "soc", "bcm", "package")
        ) else other
        target.append(zone / "temp")
    for path in preferred + other:
        text = _read_text(path)
        if text is None:
            continue
        try:
            value = float(text)
        except ValueError:
            continue
        if abs(value) > 1000:
            value /= 1000.0
        if -50.0 <= value <= 200.0:
            return round(value, 1)
    return None


def _throttling() -> dict[str, Any] | None:
    executable = shutil.which("vcgencmd")
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [executable, "get_throttled"],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    prefix, separator, raw_value = completed.stdout.strip().partition("=")
    if not separator or prefix.strip() != "throttled":
        return None
    try:
        mask = int(raw_value.strip(), 16)
    except ValueError:
        return None
    return {
        "raw": f"0x{mask:x}",
        "under_voltage": bool(mask & (1 << 0)),
        "frequency_capped": bool(mask & (1 << 1)),
        "currently_throttled": bool(mask & (1 << 2)),
        "soft_temperature_limit": bool(mask & (1 << 3)),
        "under_voltage_occurred": bool(mask & (1 << 16)),
        "frequency_cap_occurred": bool(mask & (1 << 17)),
        "throttling_occurred": bool(mask & (1 << 18)),
        "soft_temperature_limit_occurred": bool(mask & (1 << 19)),
    }


def _device_name(hostname: str) -> str:
    model = _read_text(Path("/proc/device-tree/model"))
    return model or hostname


def get_resource_metrics() -> dict[str, Any]:
    """Return measured host telemetry, using ``None`` when unavailable."""
    hostname = platform_module.node() or "UNKNOWN"
    memory = _memory()
    storage = _storage()
    load_1m, load_5m, load_15m = _load_average()

    def gib(value: float | None) -> float | None:
        return _round(value / GIB, 2) if value is not None else None

    return {
        "device_name": _device_name(hostname),
        "status": "ONLINE",
        "hostname": hostname,
        "platform": platform_module.platform(),
        "architecture": platform_module.machine() or None,
        "cpu_count": os.cpu_count(),
        "cpu_percent": _round(_cpu_percent()),
        "uptime_seconds": _round(_uptime_seconds()),
        "ram_percent": _round(memory["percent"]),
        "ram_used_bytes": int(memory["used"]) if memory["used"] is not None else None,
        "ram_total_bytes": int(memory["total"]) if memory["total"] is not None else None,
        "ram_available_bytes": (
            int(memory["available"]) if memory["available"] is not None else None
        ),
        "ram_used_gb": gib(memory["used"]),
        "ram_total_gb": gib(memory["total"]),
        "ram_available_gb": gib(memory["available"]),
        "storage_percent": _round(storage["percent"]),
        "storage_total_bytes": (
            int(storage["total"]) if storage["total"] is not None else None
        ),
        "storage_used_bytes": (
            int(storage["used"]) if storage["used"] is not None else None
        ),
        "storage_free_bytes": (
            int(storage["free"]) if storage["free"] is not None else None
        ),
        "storage_total_gb": gib(storage["total"]),
        "storage_used_gb": gib(storage["used"]),
        "storage_free_gb": gib(storage["free"]),
        "temperature_c": _temperature_c(),
        "load_1m": load_1m,
        "load_5m": load_5m,
        "load_15m": load_15m,
        "throttled": _throttling(),
        "network": "UNKNOWN",
        "audio_status": "NOT_CONFIGURED",
        "display_status": "NOT_CONFIGURED",
    }
