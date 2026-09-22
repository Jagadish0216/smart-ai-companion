from django.db import models

class SystemSetting(models.Model):
    key = models.CharField(max_length=100, unique=True)
    value = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.key}: {self.value}"

class SystemLog(models.Model):
    LEVEL_CHOICES = [
        ('INFO', 'Information'),
        ('WARN', 'Warning'),
        ('ERROR', 'Error'),
    ]
    timestamp = models.DateTimeField(auto_now_add=True)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default='INFO')
    component = models.CharField(max_length=100) # e.g., 'RAG_ENGINE', 'CORE_LLM'
    message = models.TextField()

    def __str__(self):
        return f"[{self.level}] {self.component}: {self.message[:50]}"

class DeviceCommand(models.Model):
    STATUS_CHOICES = [
        ('SIMULATED', 'Simulated (Mock)'),
        ('PENDING', 'Pending Device Ack'),
        ('SUCCESS', 'Success'),
        ('ERROR', 'Error'),
    ]
    command_type = models.CharField(max_length=50) # expression, animation, display, etc
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='SIMULATED')
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.command_type} [{self.status}] at {self.timestamp}"

class CompanionState(models.Model):
    state = models.CharField(max_length=20, default="READY")
    expression = models.CharField(max_length=20, default="NEUTRAL")
    eye_style = models.CharField(max_length=20, default="ROUND")
    animation = models.CharField(max_length=20, default="IDLE")
    display_text = models.CharField(max_length=255, default="Ready to assist")
    brightness = models.IntegerField(default=80)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"State: {self.state} | Expr: {self.expression}"

    @classmethod
    def get_current(cls):
        state, _ = cls.objects.get_or_create(id=1)
        return state
