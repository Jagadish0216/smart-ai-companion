from django.http import JsonResponse
from django.shortcuts import redirect
from django.conf import settings
from django.contrib.auth.views import redirect_to_login

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
        if getattr(settings, 'DEMO_AP_ENABLED', False) or provisioning_state == NetworkProvisioningState.State.DEMO_AP:
            if request.path in {'/api/system/network/wifi/connect/', '/api/system/setup/wifi/connect/'}:
                return JsonResponse({'detail': 'Radio changes are console-only during the demo.'}, status=403)
            allowed = request.path in {'/login/', '/logout/'} or request.path.startswith(self.STATIC_PREFIXES)
            if not allowed and not request.user.is_authenticated:
                if request.path.startswith('/api/') or request.method not in {'GET', 'HEAD'}:
                    return JsonResponse({'detail': 'Sign in for local demo access.'}, status=401)
                return redirect_to_login(request.get_full_path(), '/login/')
            return self.get_response(request) # Existing view/DRF staff permissions still apply.
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
