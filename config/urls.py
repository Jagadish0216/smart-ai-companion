from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/assistant/', include('assistant.urls')),
    path('api/system/', include('system.urls')),
    path('api/knowledge/', include('knowledge_base.urls')),
    path('', include('dashboard.urls')),
]
