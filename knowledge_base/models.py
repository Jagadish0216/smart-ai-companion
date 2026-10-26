from django.db import models
from django.utils import timezone
import os
import uuid

def document_upload_path(instance, filename):
    return f'knowledge_base/{uuid.uuid4().hex}/{os.path.basename(filename)}'

class Document(models.Model):
    class SourceType(models.TextChoices):
        MANUAL = 'MANUAL', 'Manual'
        ONLINE = 'ONLINE', 'Online'

    STATUS_CHOICES = [
        ('PENDING', 'Pending Indexing'),
        ('INDEXED', 'Indexed'),
        ('ERROR', 'Error'),
    ]
    
    file = models.FileField(upload_to=document_upload_path)
    filename = models.CharField(max_length=255)
    normalized_title = models.CharField(max_length=255, blank=True, default="", null=True)
    file_type = models.CharField(max_length=50, blank=True)
    source_identifier = models.CharField(max_length=1024, blank=True, db_index=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    indexed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    file_size = models.PositiveBigIntegerField(null=True, blank=True)
    error_message = models.CharField(max_length=255, blank=True)
    source_type = models.CharField(max_length=10, choices=SourceType.choices, default=SourceType.MANUAL, db_index=True)
    provider = models.CharField(max_length=32, null=True, blank=True)
    source_url = models.URLField(max_length=2048, null=True, blank=True)
    source_title = models.CharField(max_length=255, blank=True)
    sync_query = models.CharField(max_length=600, null=True, blank=True)
    retrieved_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    content_hash = models.CharField(max_length=64, null=True, blank=True)
    sync_error_message = models.CharField(max_length=255, blank=True)

    class Meta:
        indexes = [models.Index(fields=("source_type", "normalized_title"), name="doc_type_title_idx"),
                   models.Index(fields=("source_type", "uploaded_at"), name="doc_type_uploaded_idx")]
        constraints = [models.UniqueConstraint(
            fields=('provider', 'source_url'), condition=models.Q(source_type='ONLINE'),
            name='unique_online_provider_url',
        )]

    @property
    def freshness(self):
        if self.source_type == self.SourceType.MANUAL:
            return 'MANUAL'
        return 'FRESH' if self.expires_at and self.expires_at > timezone.now() else 'STALE'
    
    def save(self, *args, **kwargs):
        if not self.filename and self.file:
            self.filename = os.path.basename(self.file.name)
        from .document_names import normalize_title
        self.normalized_title = normalize_title(self.filename)
        if kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'normalized_title'}
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
    page_number = models.PositiveIntegerField(null=True, blank=True)
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


class EvidenceVersion(models.Model):
    """Immutable source text/provenance; freshness never controls retention."""
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='evidence_versions')
    content = models.TextField()
    content_hash = models.CharField(max_length=64)
    retrieved_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True)
    published_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(null=True)
    metadata = models.JSONField(default=dict)
    state = models.CharField(max_length=24, default='UNVERIFIED', db_index=True)
    last_used_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('document', 'content_hash', 'retrieved_at'), name='unique_evidence_observation')]

    @property
    def freshness(self):
        if self.state != 'VERIFIED':
            return self.state
        return 'FRESH' if self.expires_at and self.expires_at > timezone.now() else 'HISTORICAL'


class EvidenceQuery(models.Model):
    evidence = models.ForeignKey(EvidenceVersion, on_delete=models.CASCADE, related_name='queries')
    query = models.CharField(max_length=600, db_index=True)
    conversation = models.ForeignKey('conversations.Conversation', null=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('evidence', 'query'), name='unique_evidence_query')]


class LibrarySource(models.Model):
    key = models.CharField(max_length=120, unique=True)
    manifest_version = models.CharField(max_length=40)
    document = models.ForeignKey(Document, null=True, on_delete=models.SET_NULL)
    metadata = models.JSONField(default=dict)
    status = models.CharField(max_length=24, default='PENDING')
    last_attempt_at = models.DateTimeField(null=True)
    last_success_at = models.DateTimeField(null=True)
    next_refresh_at = models.DateTimeField(null=True)
    error = models.CharField(max_length=255, blank=True)
