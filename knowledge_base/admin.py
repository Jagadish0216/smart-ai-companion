from django.contrib import admin
from .models import Document, KnowledgeChunk


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("filename", "file_type", "status", "uploaded_at", "indexed_at")
    search_fields = ("filename", "source_identifier")
    list_filter = ("status", "file_type")


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(admin.ModelAdmin):
    list_display = ("document", "chunk_index", "created_at")
    search_fields = ("document__filename", "content")
    list_select_related = ("document",)
