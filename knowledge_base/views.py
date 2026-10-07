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
        return index_document(document)


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
