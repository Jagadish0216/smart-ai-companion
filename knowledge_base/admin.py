from django.contrib import admin
from .models import Document, KnowledgeChunk


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("filename", "source_type", "provider", "status", "freshness", "uploaded_at", "last_synced_at")
    search_fields = ("filename", "source_identifier", "source_title", "source_url", "sync_query")
    list_filter = ("source_type", "provider", "status", "file_type")
    readonly_fields = ("source_type", "provider", "source_url", "source_title", "sync_query",
                       "content_hash", "retrieved_at", "last_synced_at", "expires_at", "sync_error_message", "freshness")


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(admin.ModelAdmin):
    list_display = ("document", "chunk_index", "created_at")
    search_fields = ("document__filename", "content")
    list_select_related = ("document",)
