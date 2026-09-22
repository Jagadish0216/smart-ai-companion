from django.contrib import admin
from .models import SystemSetting, SystemLog, DeviceCommand, CompanionState

admin.site.register(SystemSetting)
admin.site.register(SystemLog)
admin.site.register(DeviceCommand)
admin.site.register(CompanionState)
