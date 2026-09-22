from django.urls import path
from .views import DeviceMetricsAPIView, SystemLogAPIView, CompanionControlAPIView, CompanionStateAPIView

urlpatterns = [
    path('metrics/', DeviceMetricsAPIView.as_view(), name='api-metrics'),
    path('logs/', SystemLogAPIView.as_view(), name='api-logs'),
    path('companion/control/', CompanionControlAPIView.as_view(), name='api-companion-control'),
    path('companion/state/', CompanionStateAPIView.as_view(), name='api-companion-state'),
]
