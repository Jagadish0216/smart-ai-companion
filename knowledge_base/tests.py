from django.test import TestCase
from .models import Document
from django.core.files.uploadedfile import SimpleUploadedFile

class KnowledgeBaseTests(TestCase):
    def test_document_creation(self):
        file = SimpleUploadedFile("test_doc.txt", b"file_content")
        doc = Document.objects.create(file=file, file_type='text/plain')
        
        self.assertEqual(doc.filename, "test_doc.txt")
        self.assertEqual(doc.status, "PENDING")
