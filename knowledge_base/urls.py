from django.urls import path

from .views import DeleteDocumentAPIView, DocumentsAPIView, ReindexDocumentAPIView, SyncDocumentAPIView, SyncStaleKnowledgeAPIView

urlpatterns = [
    path("documents/", DocumentsAPIView.as_view(), name="api-knowledge-documents"),
    path("documents/<int:document_id>/", DeleteDocumentAPIView.as_view(), name="api-knowledge-delete"),
    path("documents/<int:document_id>/reindex/", ReindexDocumentAPIView.as_view(), name="api-knowledge-reindex"),
    path("documents/<int:document_id>/sync/", SyncDocumentAPIView.as_view(), name="api-knowledge-sync"),
    path("sync-stale/", SyncStaleKnowledgeAPIView.as_view(), name="api-knowledge-sync-stale"),
]
