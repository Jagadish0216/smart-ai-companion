"""Coordinated document-chat and Django login acceptance, isolated storage/inference."""
import json
import tempfile
from unittest.mock import MagicMock, patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from knowledge_base.tests_documents import pdf_bytes
from knowledge_base.models import KnowledgeChunk
from assistant.ai_engine import AIEngineResult, AIEngineStreamEvent
from assistant.services import AssistantService


@override_settings(RAG_ENABLED=True, ONLINE_RETRIEVAL_ENABLED=False)
class AssistantDocumentChatTests(TestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable(); self.addCleanup(override.disable)
        self.user = get_user_model().objects.create_user('acceptance', password='test-password', is_staff=True)
        self.client.force_login(self.user)
        engine = MagicMock()
        result = AIEngineResult(text='The aurora battery capacity is 42 Wh.', engine='mock', model='mock', latency_ms=1)
        engine.generate.return_value = result
        engine.generate_stream.return_value = iter([AIEngineStreamEvent(type='delta', text=result.text), AIEngineStreamEvent(type='done', result=result)])
        self.engine = engine
        self.patcher = patch('assistant.services.get_engine', return_value=engine)
        self.patcher.start(); self.addCleanup(self.patcher.stop)

    def upload(self):
        response = self.client.post('/api/knowledge/documents/', {'file': SimpleUploadedFile('aurora.pdf', pdf_bytes('Aurora battery capacity is 42 Wh.'))})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['document']['status'], 'INDEXED')
        return response.json()['document']['id']

    def test_upload_select_answer_citation_followup_deselect_and_history(self):
        doc_id = self.upload()
        payload = {'query': 'What is the aurora battery capacity?', 'document_ids': [doc_id], 'knowledge_mode': 'online_only'}
        with patch('assistant.services.get_online_retriever') as network:
            response = self.client.post('/api/assistant/chat/', payload, content_type='application/json')
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data['rag_sources'][0]['page_number'], 1)
            self.assertEqual(data['rag_sources'][0]['title'], 'aurora.pdf')
            network.assert_not_called()
        self.assertIn('42 Wh', self.engine.generate.call_args.kwargs['system_instruction'])
        payload['conversation_id'] = data['conversation_id']
        payload['query'] = 'Tell me the aurora battery capacity again'
        response = self.client.post('/api/assistant/chat/stream/', payload, content_type='application/json')
        events = [json.loads(line) for line in b''.join(response.streaming_content).decode().splitlines()]
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['rag_sources'][0]['document_id'], doc_id)
        history = self.client.get('/api/assistant/chat/', {'conversation_id': data['conversation_id']}).json()
        self.assertEqual(history['messages'][-1]['rag_sources'][0]['page_number'], 1)
        payload.update(document_ids=[], query='Hello', knowledge_mode='offline_only')
        response = self.client.post('/api/assistant/chat/', payload, content_type='application/json').json()
        self.assertEqual(response['rag_sources'], [])

    def test_no_relevant_document_never_generates_or_goes_online(self):
        doc_id = self.upload()
        with patch('assistant.services.get_online_retriever') as network:
            response = self.client.post('/api/assistant/chat/', {'query': 'Volcano tectonic pressure?', 'document_ids': [doc_id]}, content_type='application/json')
            self.assertIn('do not contain relevant', response.json()['response'])
            self.engine.generate.assert_not_called(); network.assert_not_called()

    def test_invalid_scope_rejected_by_both_transports(self):
        for endpoint in ('/api/assistant/chat/', '/api/assistant/chat/stream/'):
            for scope in ({'document_ids': [999]}, {'document_ids': [-1]}, {'knowledge_mode': 'unsafe'}):
                self.assertEqual(self.client.post(endpoint, {'query': 'Hello', **scope}, content_type='application/json').status_code, 400)

    def test_modes_are_request_local_and_hardware_default_unchanged(self):
        with patch('assistant.services.get_online_retriever') as network:
            offline = AssistantService.process_message('Latest chip price?', knowledge_mode='offline_only')
            self.assertNotEqual(offline[2]['route'], 'online'); network.assert_not_called()
        online = AssistantService.process_message('Hello', knowledge_mode='online_only')
        self.assertEqual(online[2]['route'], 'online')
        default = AssistantService.process_message('Hello')
        self.assertNotEqual(default[2]['route'], 'online')

    def test_running_old_voice_process_can_insert_without_new_column(self):
        from django.db import connection
        from django.utils import timezone
        from conversations.models import Conversation, Message
        conversation = Conversation.objects.create(title='Existing hardware voice')
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO conversations_message (conversation_id, sender, text, timestamp) VALUES (%s, %s, %s, %s)',
                           [conversation.pk, 'AI', 'Older voice process answer', timezone.now()])
        self.assertIsNone(Message.objects.get(conversation=conversation).source_metadata)
        history = self.client.get('/api/assistant/chat/', {'conversation_id': conversation.pk})
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()['messages'][0]['content'], 'Older voice process answer')

    def test_pdf_pages_preserved_without_cross_page_chunks(self):
        import io
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter()
        for text in ('Aurora battery capacity is 42 Wh.', 'Nebula telescope aperture is 90 mm.'):
            writer.add_page(PdfReader(io.BytesIO(pdf_bytes(text))).pages[0])
        output = io.BytesIO(); writer.write(output)
        response = self.client.post('/api/knowledge/documents/', {'file': SimpleUploadedFile('pages.pdf', output.getvalue())})
        doc_id = response.json()['document']['id']
        self.assertEqual(list(KnowledgeChunk.objects.filter(document_id=doc_id).values_list('page_number', flat=True)), [1, 2])
        data = self.client.post('/api/assistant/chat/', {'query': 'Nebula telescope aperture?', 'document_ids': [doc_id]}, content_type='application/json').json()
        self.assertEqual(data['rag_sources'][0]['page_number'], 2)


class LoginAcceptanceTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_user('login-user', password='test-password')

    def test_success_failure_csrf_and_safe_redirect(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post('/login/', {'username': 'login-user', 'password': 'test-password'}).status_code, 403)
        page = client.get('/login/?next=/assistant/')
        self.assertContains(page, 'autocomplete="current-password"')
        token = client.cookies['csrftoken'].value
        bad = client.post('/login/', {'username': 'login-user', 'password': 'wrong', 'csrfmiddlewaretoken': token})
        self.assertContains(bad, 'role="alert"')
        response = client.post('/login/', {'username': 'login-user', 'password': 'test-password', 'csrfmiddlewaretoken': token, 'next': '/assistant/'})
        self.assertRedirects(response, '/assistant/', fetch_redirect_response=False)
        self.assertIn('_auth_user_id', client.session)
        client.logout(); client.get('/login/')
        response = client.post('/login/', {'username': 'login-user', 'password': 'test-password', 'csrfmiddlewaretoken': client.cookies['csrftoken'].value, 'next': 'https://evil.example/'})
        self.assertRedirects(response, '/', fetch_redirect_response=False)
