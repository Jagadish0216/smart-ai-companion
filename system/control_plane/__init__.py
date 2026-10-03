"""Read-only operating-system control-plane services."""

from .health import get_service_health
from .network import get_network_status
from .resource_manager import get_resource_manager_report
from .resources import get_resource_metrics

__all__ = [
    "get_network_status",
    "get_resource_manager_report",
    "get_resource_metrics",
    "get_service_health",
]
