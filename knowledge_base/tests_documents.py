import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from .models import Document, KnowledgeChunk
from .services import KnowledgeIngestionService, KnowledgeIngestionError, safe_document_filename


def pdf_bytes(text=None):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if text is not None:
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
        })
        stream = DecodedStreamObject()
        stream.set_data(("BT /F1 12 Tf 72 720 Td (" + text + ") Tj ET").encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class DocumentAPITests(TestCase):
    url = "/api/knowledge/documents/"

    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.admin = get_user_model().objects.create_user(username="knowledge-admin", is_staff=True)
        self.client.force_login(self.admin)

    def upload(self, filename="notes.txt", content=b"Edge computing processes data locally."):
        return self.client.post(self.url, {"file": SimpleUploadedFile(filename, content)})

    def test_text_upload_indexes_and_lists_safe_metadata(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201)
        doc = response.json()["document"]
        self.assertEqual(doc["status"], "INDEXED")
        self.assertEqual(doc["filename"], "notes.txt")
        self.assertEqual(doc["file_type"], "text/plain")
        self.assertGreater(doc["chunk_count"], 0)
        self.assertIsNotNone(doc["indexed_at"])
        self.assertGreater(doc["file_size"], 0)
        payload = self.client.get(self.url).json()
        self.assertEqual(payload["documents"], [doc])
        self.assertNotIn(self.media.name, json.dumps(payload))
        self.assertNotIn("source_identifier", doc)
        self.assertNotIn("file", doc)

    def test_markdown_extensions_are_indexed(self):
        for filename in ("notes.md", "notes.markdown", "NOTES.MD"):
            with self.subTest(filename=filename):
                response = self.upload(filename, b"# Edge computing\nLocal processing.")
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.json()["document"]["file_type"], "text/markdown")

    def test_pdf_text_extraction_indexes_real_text(self):
        response = self.upload("guide.pdf", pdf_bytes("Edge computing uses local processing."))
        self.assertEqual(response.status_code, 201)
        doc = Document.objects.get(pk=response.json()["document"]["id"])
        self.assertEqual(doc.file_type, "application/pdf")
        self.assertIn("Edge computing", doc.chunks.first().content)

    def test_pdf_without_text_has_useful_error_state(self):
        response = self.upload("scanned.pdf", pdf_bytes())
        self.assertEqual(response.status_code, 422)
        doc = response.json()["document"]
        self.assertEqual(doc["status"], "ERROR")
        self.assertIn("no extractable text", doc["error_message"])
        self.assertIn("OCR", doc["error_message"])
        self.assertEqual(doc["chunk_count"], 0)
        self.assertTrue(Document.objects.get(pk=doc["id"]).file.storage.exists(
            Document.objects.get(pk=doc["id"]).file.name,
        ))

    def test_malformed_pdf_is_rejected_and_record_is_retained(self):
        response = self.upload("broken.pdf", b"%PDF-1.7\nnot a PDF")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["document"]["status"], "ERROR")
        self.assertEqual(Document.objects.count(), 1)

    def test_encrypted_pdf_is_rejected(self):
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.encrypt("private-password")
        output = io.BytesIO()
        writer.write(output)
        response = self.upload("encrypted.pdf", output.getvalue())
        self.assertEqual(response.status_code, 422)
        self.assertIn("Encrypted", response.json()["document"]["error_message"])
        self.assertNotIn("private-password", response.content.decode())

    def test_unsupported_extension_does_not_create_document(self):
        response = self.upload("program.exe")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Document.objects.count(), 0)

    def test_storage_failure_returns_safe_error(self):
        with patch("knowledge_base.views.Document.objects.create", side_effect=OSError("private/filesystem/path")):
            response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private/filesystem/path", response.content.decode())

    @override_settings(RAG_CHUNK_CHARS=0)
    def test_ingestion_configuration_failure_marks_document_error(self):
        response = self.upload()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["document"]["status"], "ERROR")

    def test_oversized_upload_does_not_create_document(self):
        response = self.upload(content=b"x" * (10 * 1024 * 1024 + 1))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(Document.objects.count(), 0)

    def test_path_like_filenames_use_safe_basename(self):
        for value in ("../../private/notes.txt", "C:\\private\\notes.txt", "../<script>notes.md"):
            with self.subTest(value=value):
                safe = safe_document_filename(value)
                self.assertNotIn("/", safe)
                self.assertNotIn("\\", safe)
                self.assertNotIn("<", safe)
        response = self.upload("..\\private\\notes.txt")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["document"]["filename"], "notes.txt")
        document = Document.objects.get()
        self.assertTrue(Path(document.file.path).resolve().is_relative_to(Path(self.media.name).resolve()))

    def test_duplicate_filenames_create_independent_documents_and_files(self):
        first = self.upload(content=b"First local document.").json()["document"]["id"]
        second = self.upload(content=b"Second local document.").json()["document"]["id"]
        self.assertNotEqual(first, second)
        documents = list(Document.objects.order_by("id"))
        self.assertNotEqual(documents[0].file.name, documents[1].file.name)
        self.assertIn("First", documents[0].chunks.first().content)
        self.assertIn("Second", documents[1].chunks.first().content)

    def test_delete_removes_document_chunks_and_uploaded_file(self):
        doc_id = self.upload().json()["document"]["id"]
        document = Document.objects.get(pk=doc_id)
        file_name, storage = document.file.name, document.file.storage
        response = self.client.delete(f"{self.url}{doc_id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Document.objects.filter(pk=doc_id).exists())
        self.assertFalse(KnowledgeChunk.objects.filter(document_id=doc_id).exists())
        self.assertFalse(storage.exists(file_name))

    def test_reindex_reuses_file_replaces_chunks_and_updates_timestamp(self):
        doc_id = self.upload().json()["document"]["id"]
        document = Document.objects.get(pk=doc_id)
        old_ids = set(document.chunks.values_list("id", flat=True))
        old_time, file_name = document.indexed_at, document.file.name
        with document.file.open("wb") as source:
            source.write(b"Updated knowledge about local relays.")
        response = self.client.post(f"{self.url}{doc_id}/reindex/")
        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.file.name, file_name)
        self.assertGreaterEqual(document.indexed_at, old_time)
        self.assertFalse(old_ids & set(document.chunks.values_list("id", flat=True)))
        self.assertIn("Updated", document.chunks.first().content)

    def test_failed_chunk_replacement_rolls_back_and_reports_safe_error(self):
        doc_id = self.upload().json()["document"]["id"]
        document = Document.objects.get(pk=doc_id)
        old = list(document.chunks.values_list("id", "content"))
        with patch("knowledge_base.services.KnowledgeChunk.objects.bulk_create", side_effect=RuntimeError("private/path")):
            response = self.client.post(f"{self.url}{doc_id}/reindex/")
        self.assertEqual(response.status_code, 422)
        document.refresh_from_db()
        self.assertEqual(document.status, "ERROR")
        self.assertEqual(old, list(document.chunks.values_list("id", "content")))
        self.assertNotIn("private/path", response.content.decode())

    def test_binary_or_invalid_utf8_text_is_not_indexed(self):
        for content in (b"binary\x00data", b"\xff\xfe", b"   "):
            with self.subTest(content=content):
                response = self.upload(content=content)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["document"]["status"], "ERROR")

    def test_service_marks_error_and_successful_retry_clears_it(self):
        document = Document.objects.create(file=SimpleUploadedFile("empty.txt", b""), file_type="text/plain")
        with self.assertRaises(KnowledgeIngestionError):
            KnowledgeIngestionService().ingest_document(document)
        document.refresh_from_db()
        self.assertEqual(document.status, "ERROR")
        with document.file.open("wb") as source:
            source.write(b"Recoverable local knowledge.")
        KnowledgeIngestionService().ingest_document(document)
        document.refresh_from_db()
        self.assertEqual(document.status, "INDEXED")
        self.assertEqual(document.error_message, "")

    def test_missing_document_returns_not_found(self):
        self.assertEqual(self.client.post(f"{self.url}999/reindex/").status_code, 404)
        self.assertEqual(self.client.delete(f"{self.url}999/").status_code, 404)

    def test_anonymous_and_non_staff_users_cannot_mutate(self):
        self.client.logout()
        self.assertEqual(self.upload().status_code, 403)
        user = get_user_model().objects.create_user(username="reader")
        self.client.force_login(user)
        self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(self.client.delete(f"{self.url}1/").status_code, 403)
        self.assertEqual(self.client.post(f"{self.url}1/reindex/").status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_all_document_mutations_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        page = client.get("/knowledge/")
        token = page.cookies["csrftoken"].value
        self.assertEqual(client.post(self.url, {"file": SimpleUploadedFile("notes.txt", b"Local facts.")}).status_code, 403)
        accepted = client.post(self.url, {"file": SimpleUploadedFile("notes.txt", b"Local facts.")}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(accepted.status_code, 201)
        doc_id = accepted.json()["document"]["id"]
        self.assertEqual(client.post(f"{self.url}{doc_id}/reindex/").status_code, 403)
        self.assertEqual(client.delete(f"{self.url}{doc_id}/").status_code, 403)
        self.assertEqual(client.post(f"{self.url}{doc_id}/reindex/", HTTP_X_CSRFTOKEN=token).status_code, 200)
        self.assertEqual(client.delete(f"{self.url}{doc_id}/", HTTP_X_CSRFTOKEN=token).status_code, 204)

    def test_dashboard_and_script_describe_real_pipeline_and_use_safe_rendering(self):
        response = self.client.get("/knowledge/")
        for label in ("Text Extraction", "Chunking", "Lexical Retrieval", "Local AI Grounding", "knowledge-file-picker"):
            self.assertContains(response, label)
        self.assertNotContains(response, "Vector Retrieval")
        self.assertNotContains(response, "upload pipeline is currently simulated")
        script = (Path(settings.BASE_DIR) / "static" / "js" / "knowledge.js").read_text(encoding="utf-8")
        self.assertIn("text.textContent = message", script)
        self.assertIn("event.dataTransfer.files", script)
        self.assertIn("window.confirm", script)
        self.assertIn("request.upload.addEventListener('progress'", script)
        self.assertNotIn("innerHTML", script)
