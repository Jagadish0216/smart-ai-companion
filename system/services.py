"""Compatibility-facing services for the system API."""

from .control_plane.resources import get_resource_metrics


def get_device_metrics() -> dict:
    """Return real host metrics from the read-only control plane."""
    return get_resource_metrics()
