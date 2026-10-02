from django.http import JsonResponse
from django.shortcuts import redirect

from system.control_plane.provisioning import setup_mode_available


class SetupModeIsolationMiddleware:
    """Expose only the narrow provisioning surface while setup mode is active."""

    ALLOWED_PREFIXES = (
        "/setup/",
        "/api/system/setup/",
        "/static/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        setup_active = setup_mode_available()
        if setup_active and not request.path.startswith(self.ALLOWED_PREFIXES):
            if request.path.startswith("/api/") or request.method not in {"GET", "HEAD"}:
                return JsonResponse(
                    {"detail": "Only Wi-Fi setup is available in setup mode."},
                    status=404,
                )
            return redirect("setup")
        return self.get_response(request)
