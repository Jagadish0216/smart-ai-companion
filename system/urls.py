from django.urls import path
from .views import (
    CompanionControlAPIView,
    CompanionStateAPIView,
    DeviceMetricsAPIView,
    NetworkStatusAPIView,
    ServiceHealthAPIView,
    SetupStatusAPIView,
    SetupWifiConnectAPIView,
    SetupWifiScanAPIView,
    SystemLogAPIView,
    WifiConnectAPIView,
    WifiScanAPIView,
)

urlpatterns = [
    path('metrics/', DeviceMetricsAPIView.as_view(), name='api-metrics'),
    path('network/', NetworkStatusAPIView.as_view(), name='api-network'),
    path('network/wifi/scan/', WifiScanAPIView.as_view(), name='api-wifi-scan'),
    path('network/wifi/connect/', WifiConnectAPIView.as_view(), name='api-wifi-connect'),
    path('setup/status/', SetupStatusAPIView.as_view(), name='api-setup-status'),
    path('setup/wifi/scan/', SetupWifiScanAPIView.as_view(), name='api-setup-wifi-scan'),
    path('setup/wifi/connect/', SetupWifiConnectAPIView.as_view(), name='api-setup-wifi-connect'),
    path('services/', ServiceHealthAPIView.as_view(), name='api-services'),
    path('logs/', SystemLogAPIView.as_view(), name='api-logs'),
    path('companion/control/', CompanionControlAPIView.as_view(), name='api-companion-control'),
    path('companion/state/', CompanionStateAPIView.as_view(), name='api-companion-state'),
]
