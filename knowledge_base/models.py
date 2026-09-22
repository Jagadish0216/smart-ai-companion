from django.db import models
import os

def document_upload_path(instance, filename):
    return f'knowledge_base/{filename}'

class Document(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending Indexing'),
        ('INDEXED', 'Indexed'),
        ('ERROR', 'Error'),
    ]
    
    file = models.FileField(upload_to=document_upload_path)
    filename = models.CharField(max_length=255)
    file_type = models.CharField(max_length=50, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    
    def save(self, *args, **kwargs):
        if not self.filename and self.file:
            self.filename = os.path.basename(self.file.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.filename
