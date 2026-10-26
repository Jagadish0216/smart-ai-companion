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
        payload.pop('document_ids')
        payload.update(query='Hello', knowledge_mode='offline_only')
        response = self.client.post('/api/assistant/chat/', payload, content_type='application/json').json()
        self.assertEqual(response['rag_sources'], [])

    def test_no_relevant_document_never_generates_or_goes_online(self):
        doc_id = self.upload()
        with patch('assistant.services.get_online_retriever') as network:
            response = self.client.post('/api/assistant/chat/', {'query': 'Volcano tectonic pressure?', 'document_ids': [doc_id]}, content_type='application/json')
            self.assertIn('do not contain enough', response.json()['response'])
            self.engine.generate.assert_not_called(); network.assert_not_called()

    def test_invalid_scope_rejected_by_both_transports(self):
        for endpoint in ('/api/assistant/chat/', '/api/assistant/chat/stream/'):
            for scope in ({'document_ids': [-1]}, {'document_ids': 'invalid'}, {'knowledge_mode': 'unsafe'}):
                self.assertEqual(self.client.post(endpoint, {'query': 'Hello', **scope}, content_type='application/json').status_code, 400)

    def test_modes_are_request_local_and_hardware_default_unchanged(self):
        with patch('assistant.services.get_online_retriever') as network:
            offline = AssistantService.process_message('Latest chip price?', knowledge_mode='offline_only')
            self.assertNotEqual(offline[2]['route'], 'online'); network.assert_not_called()
        online = AssistantService.process_message('What is recursion?', knowledge_mode='online_only')
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

@override_settings(AI_ENGINE='local', RAG_ENABLED=True, ONLINE_RETRIEVAL_ENABLED=True,
                   KNOWLEDGE_REQUIRE_EVIDENCE=True, ONLINE_KNOWLEDGE_AUTO_PERSIST=True,
                   ONLINE_PROVIDER='searxng', SEARXNG_BASE_URL='http://127.0.0.1:8888', RAG_MIN_RELEVANCE=.3)
class AssistantKnowledgeCompatibilityTests(TestCase):
    """Main Assistant scopes must coexist with the integrated evidence archive."""
    from knowledge_base.tests_complete_knowledge import CompleteKnowledgeTests as _Fixtures
    setUp = _Fixtures.setUp
    ask = _Fixtures.ask
    seed = _Fixtures.seed

    def test_deselect_restores_online_then_expired_evidence_reused_by_stream_and_history(self):
        from datetime import timedelta
        from django.utils import timezone
        from knowledge_base.models import Document, EvidenceVersion
        response = self.client.post('/api/knowledge/documents/', {'file': SimpleUploadedFile('quartz.pdf', pdf_bytes('Quartz protocol uses amber tokens.'))})
        doc_id = response.json()['document']['id']
        first = self.ask('What tokens does Quartz protocol use?', document_ids=[doc_id], knowledge_mode='online_only')
        self.assertIn('amber tokens', first['response'])
        self.assertEqual(first['rag_sources'][0]['page_number'], 1)
        # Omission, rather than [], is the integrated API's automatic-source contract.
        online = self.ask('What movie is Prabhas currently working on?', conversation_id=first['conversation_id'], knowledge_mode='online_preferred')
        self.assertTrue(online['online_used'])
        self.assertEqual(EvidenceVersion.objects.count(), 1)
        self.transport.retrieve.reset_mock()
        self.transport.retrieve.side_effect = AssertionError('Offline follow-up must not use the network')
        yesterday = timezone.now() - timedelta(days=1)
        EvidenceVersion.objects.update(verified_at=yesterday, expires_at=yesterday)
        Document.objects.filter(source_type='ONLINE').update(expires_at=yesterday)
        response = self.client.post('/api/assistant/chat/stream/', {'query':'Which movie was Prabhas working on when I checked yesterday?', 'conversation_id':first['conversation_id'], 'knowledge_mode':'offline_only'}, content_type='application/json')
        done = [json.loads(line) for line in b''.join(response.streaming_content).decode().splitlines()][-1]
        self.assertEqual(done['type'], 'done')
        self.assertIn('Example Project', done['response'])
        self.assertEqual(done['rag_sources'][0]['freshness'], 'HISTORICAL')
        self.assertTrue(done['rag_sources'][0]['verified_at'])
        history = self.client.get('/api/assistant/chat/', {'conversation_id':first['conversation_id']}).json()
        self.assertEqual(history['messages'][-1]['rag_sources'], done['rag_sources'])
        self.assertEqual(history['messages'][-1]['fallback_reason'], 'offline_historical')
        self.transport.retrieve.assert_not_called()

    def test_memory_all_local_contract_and_existing_dashboard_remain_available(self):
        from knowledge_base.services import KnowledgeIngestionService
        KnowledgeIngestionService().ingest_text('Quartz protocol uses amber tokens.', filename='local.txt', file_type='text/plain', source_identifier='compatibility:local')
        answer = self.ask('What tokens does Quartz protocol use?', document_ids=[], knowledge_mode='online_only')
        self.assertTrue(answer['rag_used'])
        self.assertIn('amber tokens', answer['response'])
        self.transport.retrieve.assert_not_called()
        page = self.client.get('/knowledge/')
        self.assertContains(page, 'knowledge-question-form')
        self.assertContains(page, 'knowledge-library-status')
        self.assertEqual(self.client.get('/api/knowledge/library/').status_code, 200)

    def test_selected_document_absence_never_reuses_unrelated_archived_evidence(self):
        self.seed()
        response = self.client.post('/api/knowledge/documents/', {'file': SimpleUploadedFile('quartz.txt', b'Quartz protocol uses amber tokens.')})
        answer = self.ask('What movie is Prabhas currently working on?', document_ids=[response.json()['document']['id']], knowledge_mode='offline_only')
        self.assertIn('selected documents do not contain enough', answer['response'])
        self.assertNotIn('Example Project', answer['response'])
        self.transport.retrieve.assert_not_called()

    def test_knowledge_migration_graph_has_one_existing_leaf_and_message_dependency(self):
        from django.db import connection
        from django.db.migrations.loader import MigrationLoader
        loader = MigrationLoader(connection)
        self.assertEqual(loader.graph.leaf_nodes('knowledge_base'), [('knowledge_base', '0007_document_normalized_title_and_more')])
        self.assertIn(('knowledge_base', '0005_knowledgechunk_page_number_evidenceversion_and_more'), loader.disk_migrations)
        self.assertNotIn(('knowledge_base', '0005_knowledgechunk_page_number'), loader.disk_migrations)
        migration = loader.disk_migrations[('conversations', '0002_message_source_metadata')]
        self.assertEqual(migration.dependencies, [('conversations', '0001_initial')])

from django.test import TransactionTestCase


class IntegratedMessageMigrationTests(TransactionTestCase):
    def test_upgrade_existing_knowledge_schema_preserves_evidence_and_pdf_chunks(self):
        """Exercise the exact one-column Pi upgrade over the existing knowledge graph."""
        import hashlib
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from django.utils import timezone
        old_targets = [('conversations', '0001_initial'), ('knowledge_base', '0006_preserve_verified_snapshots')]
        executor = MigrationExecutor(connection)
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes()))
        executor.migrate(old_targets)
        apps = executor.loader.project_state(old_targets).apps
        conversation = apps.get_model('conversations', 'Conversation').objects.create(title='Existing Pi history')
        message = apps.get_model('conversations', 'Message').objects.create(conversation=conversation, sender='AI', text='Existing answer')
        doc = apps.get_model('knowledge_base', 'Document').objects.create(filename='retained.pdf', file='documents/retained.pdf', status='INDEXED')
        chunk = apps.get_model('knowledge_base', 'KnowledgeChunk').objects.create(document=doc, chunk_index=0, page_number=2, content='Original page two evidence')
        content = 'Previously verified immutable evidence'
        version = apps.get_model('knowledge_base', 'EvidenceVersion').objects.create(document=doc, content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(), retrieved_at=timezone.now(), verified_at=timezone.now(), state='VERIFIED', metadata={'title':'Stored source'})
        apps.get_model('knowledge_base', 'EvidenceQuery').objects.create(evidence=version, conversation=conversation, query='Original evidence query')
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        self.assertEqual({(item.app_label,item.name,backwards) for item,backwards in plan}, {('conversations','0002_message_source_metadata',False), ('conversations','0003_hardwaredocumentscope',False), ('knowledge_base','0007_document_normalized_title_and_more',False)})
        executor.migrate(executor.loader.graph.leaf_nodes())
        from conversations.models import Message
        from knowledge_base.models import KnowledgeChunk, Document, EvidenceVersion, EvidenceQuery
        self.assertEqual(Message.objects.get(pk=message.pk).text, 'Existing answer')
        self.assertEqual(KnowledgeChunk.objects.get(pk=chunk.pk).page_number, 2)
        self.assertEqual(Document.objects.get(pk=doc.pk).file.name, 'documents/retained.pdf')
        self.assertEqual(EvidenceVersion.objects.get(pk=version.pk).content, content)
        self.assertEqual(EvidenceQuery.objects.get(evidence_id=version.pk).conversation_id, conversation.pk)
