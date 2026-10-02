from django.contrib import admin
from .models import (
    CompanionState,
    DeviceCommand,
    NetworkProvisioningState,
    SystemLog,
    SystemSetting,
)

admin.site.register(SystemSetting)
admin.site.register(SystemLog)
admin.site.register(DeviceCommand)
admin.site.register(CompanionState)
admin.site.register(NetworkProvisioningState)
