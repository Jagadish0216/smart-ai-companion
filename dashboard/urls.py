from django.urls import path
from .views import DashboardView, AssistantView, ConversationsView, KnowledgeBaseView, DeviceStatusView, LogsView, SettingsView, CompanionStudioView

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('studio/', CompanionStudioView.as_view(), name='studio'),
    path('assistant/', AssistantView.as_view(), name='assistant'),
    path('conversations/', ConversationsView.as_view(), name='conversations'),
    path('knowledge/', KnowledgeBaseView.as_view(), name='knowledge'),
    path('device/', DeviceStatusView.as_view(), name='device'),
    path('logs/', LogsView.as_view(), name='logs'),
    path('settings/', SettingsView.as_view(), name='settings'),
]
