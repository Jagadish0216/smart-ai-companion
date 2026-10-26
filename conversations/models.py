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
    source_metadata = models.JSONField(default=dict, blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    # Optional fields for deeper Edge AI tracking
    processing_time_ms = models.IntegerField(null=True, blank=True)
    tokens_used = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.sender}: {self.text[:50]}"


class HardwareDocumentScope(models.Model):
    """Trusted hardware-session state; never shared with browser conversation scope."""
    session_key = models.CharField(max_length=64, primary_key=True)
    process_token = models.CharField(max_length=32)
    conversation = models.ForeignKey(Conversation, null=True, on_delete=models.SET_NULL)
    document = models.ForeignKey('knowledge_base.Document', null=True, on_delete=models.SET_NULL)
    pending_document_ids = models.JSONField(default=list)
    document_indexed_at = models.DateTimeField(null=True)
    chunk_cursor = models.IntegerField(default=-1)
    expires_at = models.DateTimeField()
