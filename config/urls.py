from django.contrib import admin
from django.urls import path, include
from django.contrib.auth.views import LoginView, LogoutView

urlpatterns = [
    path('login/', LoginView.as_view(template_name='registration/demo_login.html'), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('admin/', admin.site.urls),
    path('api/assistant/', include('assistant.urls')),
    path('api/system/', include('system.urls')),
    path('api/knowledge/', include('knowledge_base.urls')),
    path('', include('dashboard.urls')),
]
