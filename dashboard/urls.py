from django.urls import path
from .views import (
    AssistantView,
    CompanionStudioView,
    ConversationsView,
    DashboardView,
    DeviceStatusView,
    KnowledgeBaseView,
    LogsView,
    NetworkView,
    ResourceManagerView,
    SettingsView,
    SetupView,
)

urlpatterns = [
    path('setup/', SetupView.as_view(), name='setup'),
    path('', DashboardView.as_view(), name='dashboard'),
    path('studio/', CompanionStudioView.as_view(), name='studio'),
    path('assistant/', AssistantView.as_view(), name='assistant'),
    path('conversations/', ConversationsView.as_view(), name='conversations'),
    path('knowledge/', KnowledgeBaseView.as_view(), name='knowledge'),
    path('device/', DeviceStatusView.as_view(), name='device'),
    path('resource-manager/', ResourceManagerView.as_view(), name='resource-manager'),
    path('network/', NetworkView.as_view(), name='network'),
    path('logs/', LogsView.as_view(), name='logs'),
    path('settings/', SettingsView.as_view(), name='settings'),
]
