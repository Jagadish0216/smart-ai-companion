from django.db import models
import os
import uuid

def document_upload_path(instance, filename):
    return f'knowledge_base/{uuid.uuid4().hex}/{os.path.basename(filename)}'

class Document(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending Indexing'),
        ('INDEXED', 'Indexed'),
        ('ERROR', 'Error'),
    ]
    
    file = models.FileField(upload_to=document_upload_path)
    filename = models.CharField(max_length=255)
    file_type = models.CharField(max_length=50, blank=True)
    source_identifier = models.CharField(max_length=1024, blank=True, db_index=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    indexed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    file_size = models.PositiveBigIntegerField(null=True, blank=True)
    error_message = models.CharField(max_length=255, blank=True)
    
    def save(self, *args, **kwargs):
        if not self.filename and self.file:
            self.filename = os.path.basename(self.file.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.filename


class KnowledgeChunk(models.Model):
    """A deterministic text chunk belonging to one local knowledge source."""

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("document_id", "chunk_index")
        constraints = [
            models.UniqueConstraint(
                fields=("document", "chunk_index"),
                name="unique_document_chunk_index",
            ),
        ]

    def __str__(self):
        return f"{self.document.filename} [{self.chunk_index}]"
