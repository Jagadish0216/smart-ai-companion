from rest_framework.views import APIView
from rest_framework.response import Response
from .services import get_mock_device_metrics
from .models import SystemLog

class DeviceMetricsAPIView(APIView):
    def get(self, request):
        metrics = get_mock_device_metrics()
        return Response(metrics)

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

from rest_framework import status
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
