from django.conf import settings
from django.core.cache import cache
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from .control_plane.health import get_service_health
from .control_plane.network import (
    connect_wifi,
    get_network_status,
    scan_wifi_networks,
)
from .control_plane.provisioning import (
    get_setup_status,
    provision_wifi,
    setup_mode_available,
)
from .services import get_device_metrics
from .models import SystemLog

class DeviceMetricsAPIView(APIView):
    def get(self, request):
        metrics = get_device_metrics()
        return Response(metrics)


class NetworkStatusAPIView(APIView):
    def get(self, request):
        return Response(get_network_status())


class WifiScanAPIView(APIView):
    def get(self, request):
        try:
            return Response(scan_wifi_networks())
        except Exception:
            return Response({
                "supported": False,
                "interface": None,
                "networks": [],
                "message": "Available networks could not be loaded.",
            })


class WifiConnectAPIView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser]

    def post(self, request):
        if not isinstance(request.data, dict):
            result = {
                "success": False,
                "state": "INVALID",
                "message": "The connection request is invalid.",
            }
        else:
            try:
                result = connect_wifi(
                    request.data.get("ssid"),
                    request.data.get("password"),
                )
            except Exception:
                result = {
                    "success": False,
                    "state": "UNKNOWN",
                    "message": "The connection result could not be determined.",
                }
        response_status = {
            "INVALID": status.HTTP_400_BAD_REQUEST,
            "CONNECTING": status.HTTP_202_ACCEPTED,
            "NOT_AVAILABLE": status.HTTP_503_SERVICE_UNAVAILABLE,
            "UNKNOWN": status.HTTP_503_SERVICE_UNAVAILABLE,
        }.get(result["state"], status.HTTP_200_OK)
        return Response(result, status=response_status)


def _setup_unavailable_response():
    return Response(
        {"detail": "Wi-Fi setup is not currently available."},
        status=status.HTTP_404_NOT_FOUND,
    )


class SetupStatusAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        if not setup_mode_available():
            return _setup_unavailable_response()
        return Response(get_setup_status())


class SetupWifiScanAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        if not setup_mode_available():
            return _setup_unavailable_response()
        try:
            return Response(scan_wifi_networks())
        except Exception:
            return Response({
                "supported": False,
                "interface": None,
                "networks": [],
                "message": "Available networks could not be loaded.",
            })


@method_decorator(csrf_protect, name="dispatch")
class SetupWifiConnectAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        if not setup_mode_available():
            return _setup_unavailable_response()
        cooldown = max(
            1,
            min(int(getattr(settings, "SETUP_CONNECT_COOLDOWN_SECONDS", 5)), 60),
        )
        if not cache.add("setup-wifi-connect.cooldown.v1", True, cooldown):
            return Response(
                {
                    "success": False,
                    "state": "RATE_LIMITED",
                    "message": "Please wait before trying another Wi-Fi connection.",
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        if not isinstance(request.data, dict):
            result = {
                "success": False,
                "state": "INVALID",
                "message": "The connection request is invalid.",
            }
        else:
            result = provision_wifi(
                request.data.get("ssid"),
                request.data.get("password"),
            )
        response_status = {
            "INVALID": status.HTTP_400_BAD_REQUEST,
            "BUSY": status.HTTP_409_CONFLICT,
            "NOT_AVAILABLE": status.HTTP_404_NOT_FOUND,
            "FAILED": status.HTTP_503_SERVICE_UNAVAILABLE,
        }.get(result["state"], status.HTTP_200_OK)
        return Response(result, status=response_status)


class ServiceHealthAPIView(APIView):
    def get(self, request):
        return Response(get_service_health())

class SystemLogAPIView(APIView):
    def get(self, request):
        logs = SystemLog.objects.all().order_by('-timestamp')[:50]
        data = []
        for log in logs:
            data.append({
                "id": log.id,
                "timestamp": log.timestamp.isoformat(),
                "level": log.level,
                "component": log.component,
                "message": log.message
            })
        return Response(data)

from .device_controller import device

class CompanionControlAPIView(APIView):
    def post(self, request):
        action = request.data.get('action')
        value = request.data.get('value')
        
        if not action:
            return Response({"error": "No action provided."}, status=status.HTTP_400_BAD_REQUEST)
            
        cmd_result = None
        
        if action == 'set_expression':
            cmd_result = device.set_expression(value)
        elif action == 'set_eye_style':
            cmd_result = device.set_eye_style(value)
        elif action == 'set_animation':
            cmd_result = device.set_animation(value)
        elif action == 'set_display_text':
            cmd_result = device.set_display_text(value)
        elif action == 'set_brightness':
            try:
                val_int = int(value)
                cmd_result = device.set_brightness(val_int)
            except ValueError:
                return Response({"error": "Brightness must be an integer."}, status=status.HTTP_400_BAD_REQUEST)
        elif action == 'set_state':
            cmd_result = device.set_state(value)
        else:
            return Response({"error": "Unknown action."}, status=status.HTTP_400_BAD_REQUEST)
            
        state = CompanionState.get_current()
        return Response({
            "status": "SUCCESS",
            "command_id": cmd_result.id if cmd_result else None,
            "simulated": True,
            "expression": state.expression,
            "animation": state.animation,
            "display_text": state.display_text,
            "state": state.state,
            "brightness": state.brightness,
            "eye_style": state.eye_style,
            "updated_at": state.updated_at
        })

from .models import CompanionState

class CompanionStateAPIView(APIView):
    def get(self, request):
        state = CompanionState.get_current()
        return Response({
            "expression": state.expression,
            "animation": state.animation,
            "display_text": state.display_text,
            "state": state.state,
            "brightness": state.brightness,
            "eye_style": state.eye_style,
            "updated_at": state.updated_at
        })
