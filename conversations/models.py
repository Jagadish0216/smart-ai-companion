from django.db import models

class Conversation(models.Model):
    title = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title or f"Conversation {self.id}"

class Message(models.Model):
    SENDER_CHOICES = [
        ('USER', 'User'),
        ('AI', 'AI'),
    ]
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.CharField(max_length=10, choices=SENDER_CHOICES)
    text = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    
    # Optional fields for deeper Edge AI tracking
    processing_time_ms = models.IntegerField(null=True, blank=True)
    tokens_used = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.sender}: {self.text[:50]}"
