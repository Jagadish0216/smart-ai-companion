from django.http import JsonResponse
from django.shortcuts import redirect

from system.control_plane.provisioning import (
    SETUP_STATES,
    current_provisioning_state,
)
from system.models import NetworkProvisioningState


class SetupModeIsolationMiddleware:
    """Expose only the narrow provisioning surface while setup mode is active."""

    STATIC_PREFIXES = (
        "/static/",
    )
    SETUP_PATHS = {
        "/setup/",
        "/api/system/setup/status/",
        "/api/system/setup/handoff-status/",
        "/api/system/setup/wifi/scan/",
        "/api/system/setup/wifi/connect/",
    }
    CONNECTING_PATHS = {
        "/setup/",
        "/api/system/setup/status/",
        "/api/system/setup/handoff-status/",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        provisioning_state = current_provisioning_state()
        setup_active = provisioning_state in SETUP_STATES
        allowed_paths = (
            self.CONNECTING_PATHS
            if provisioning_state == NetworkProvisioningState.State.CONNECTING
            else self.SETUP_PATHS
        )
        path_allowed = request.path in allowed_paths or request.path.startswith(
            self.STATIC_PREFIXES
        )
        if setup_active and not path_allowed:
            if request.path.startswith("/api/") or request.method not in {"GET", "HEAD"}:
                return JsonResponse(
                    {"detail": "Only Wi-Fi setup is available in setup mode."},
                    status=404,
                )
            return redirect("setup")
        return self.get_response(request)
