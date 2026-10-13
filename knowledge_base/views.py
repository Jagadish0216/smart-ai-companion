"""Document management over the existing local ingestion pipeline."""

import logging
import re

from django.db import transaction
from django.db.models import Count
from rest_framework.authentication import SessionAuthentication
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Document
from .online_sync import OnlineKnowledgeSyncService
from .services import (
    KnowledgeIngestionError, KnowledgeIngestionService, MAX_UPLOAD_BYTES,
    SUPPORTED_FORMATS, safe_document_filename,
)

logger = logging.getLogger(__name__)


def document_payload(document):
    return {
        "id": document.pk,
        "filename": re.split(r"[\\/]", document.filename)[-1],
        "file_type": document.file_type,
        "file_size": document.file_size,
        "status": document.status,
        "chunk_count": document.chunk_count
        if hasattr(document, "chunk_count") else document.chunks.count(),
        "uploaded_at": document.uploaded_at.isoformat(),
        "indexed_at": document.indexed_at.isoformat() if document.indexed_at else None,
        "error_message": document.error_message,
        "source_type": document.source_type,
        "provider": document.provider,
        "source_url": document.source_url,
        "source_title": document.source_title,
        "sync_query": document.sync_query,
        "retrieved_at": document.retrieved_at.isoformat() if document.retrieved_at else None,
        "last_synced_at": document.last_synced_at.isoformat() if document.last_synced_at else None,
        "expires_at": document.expires_at.isoformat() if document.expires_at else None,
        "freshness": document.freshness,
        "sync_error_message": document.sync_error_message,
    }


def index_document(document):
    try:
        KnowledgeIngestionService().ingest_document(document)
    except KnowledgeIngestionError as exc:
        if document.status != "ERROR":
            document.status = "ERROR"
            document.error_message = str(exc)[:255]
            document.save(update_fields=("status", "error_message"))
        return Response({"document": document_payload(document)}, status=422)
    return Response({"document": document_payload(document)}, status=200)


class DocumentsAPIView(APIView):
    authentication_classes = [SessionAuthentication]
    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        return [AllowAny()] if self.request.method in {"GET", "HEAD", "OPTIONS"} else [IsAdminUser()]

    def get(self, request):
        documents = Document.objects.annotate(chunk_count=Count("chunks")).order_by("-uploaded_at", "-id")
        response = Response({"documents": [document_payload(document) for document in documents]})
        response["Cache-Control"] = "no-store"
        return response

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"error": "Choose a document to upload."}, status=400)
        if upload.size > MAX_UPLOAD_BYTES:
            return Response({"error": "Documents must be at most 10 MB."}, status=413)
        try:
            filename = safe_document_filename(upload.name)
        except KnowledgeIngestionError as exc:
            return Response({"error": str(exc)}, status=400)
        suffix = "." + filename.rsplit(".", 1)[-1].lower()
        upload.name = filename
        try:
            document = Document.objects.create(
                file=upload, filename=filename, file_type=SUPPORTED_FORMATS[suffix],
                file_size=upload.size,
            )
        except OSError:
            logger.warning("Document upload storage unavailable")
            return Response({"error": "The document could not be stored. Try again."}, status=503)
        response = index_document(document)
        if response.status_code == 200:
            response.status_code = 201
        return response


class ReindexDocumentAPIView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser]

    def post(self, request, document_id):
        try:
            document = Document.objects.get(pk=document_id)
        except Document.DoesNotExist:
            return Response({"error": "Document not found."}, status=404)
        if document.source_type == Document.SourceType.ONLINE:
            return Response({"error": "Use Sync to refresh an online source."}, status=400)
        return index_document(document)


class SyncDocumentAPIView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser]

    def post(self, request, document_id):
        try:
            document = Document.objects.get(pk=document_id)
        except Document.DoesNotExist:
            return Response({"error": "Document not found."}, status=404)
        if document.source_type != Document.SourceType.ONLINE:
            return Response({"error": "Only online sources can be synchronized."}, status=400)
        try:
            result = OnlineKnowledgeSyncService().sync_document(document)
            document.refresh_from_db()
            return Response({"sync": result.as_dict(), "document": document_payload(document)})
        except Exception:
            logger.warning("Manual online knowledge synchronization unavailable")
            return Response({"error": "Synchronization could not run. Check configuration and local storage."}, status=503)


class SyncStaleKnowledgeAPIView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser]

    def post(self, request):
        try:
            result = OnlineKnowledgeSyncService().sync_stale()
            return Response({"sync": result.as_dict()})
        except Exception:
            logger.warning("Stale online knowledge synchronization unavailable")
            return Response({"error": "Synchronization could not run. Check configuration and local storage."}, status=503)


class DeleteDocumentAPIView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser]

    def delete(self, request, document_id):
        try:
            with transaction.atomic():
                document = Document.objects.select_for_update().get(pk=document_id)
                document.file.delete(save=False)
                document.delete()
        except Document.DoesNotExist:
            return Response({"error": "Document not found."}, status=404)
        except OSError:
            logger.warning("Stored document deletion failed")
            return Response({"error": "The document could not be deleted. Try again."}, status=503)
        return Response(status=204)

# Create your views here.
