from django.urls import path
from .views import (
    CompanionControlAPIView,
    CompanionStateAPIView,
    DeviceMetricsAPIView,
    NetworkStatusAPIView,
    ServiceHealthAPIView,
    SystemLogAPIView,
)

urlpatterns = [
    path('metrics/', DeviceMetricsAPIView.as_view(), name='api-metrics'),
    path('network/', NetworkStatusAPIView.as_view(), name='api-network'),
    path('services/', ServiceHealthAPIView.as_view(), name='api-services'),
    path('logs/', SystemLogAPIView.as_view(), name='api-logs'),
    path('companion/control/', CompanionControlAPIView.as_view(), name='api-companion-control'),
    path('companion/state/', CompanionStateAPIView.as_view(), name='api-companion-state'),
]
