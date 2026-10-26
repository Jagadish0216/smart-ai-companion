"""User-path acceptance with disposable SQLite/media and mocked transports."""
import tempfile
from dataclasses import replace
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from assistant.ai_engine.base import AIEngineResult
from assistant.online.base import OnlineRetrievalResult, OnlineRetrievalTimeoutError
from assistant.services import AssistantService
from assistant.test_support import install_resource_report
from .models import Document, EvidenceVersion
from .online_sync import OnlineKnowledgeSyncService
from .tests_documents import pdf_bytes


@override_settings(AI_ENGINE='local', RAG_ENABLED=True, ONLINE_RETRIEVAL_ENABLED=True,
                   KNOWLEDGE_REQUIRE_EVIDENCE=True,
                   ONLINE_KNOWLEDGE_AUTO_PERSIST=True, ONLINE_PROVIDER='searxng',
                   SEARXNG_BASE_URL='http://127.0.0.1:8888', RAG_MIN_RELEVANCE=.3)
class CompleteKnowledgeTests(TestCase):
    def setUp(self):
        install_resource_report(self, online_allowed=True)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        config = override_settings(MEDIA_ROOT=self.directory.name)
        config.enable(); self.addCleanup(config.disable)
        self.client.force_login(get_user_model().objects.create_user('acceptance', is_staff=True))
        self.now = timezone.now()
        self.source = OnlineRetrievalResult('Prabhas project report', 'https://publisher.example/cinema/prabhas-project',
            'Prabhas is working on the announced movie Example Project.', 'searxng', self.now,
            published_at=self.now, verified_at=self.now, accessibility='available',
            verified_url='https://publisher.example/cinema/prabhas-project', evidence_kind='fetched_report',
            entertainment_status='announced', claim_status='confirmed', publication_source='article_metadata')
        self.transport = MagicMock()
        self.transport.retrieve.return_value = [self.source]
        self.transport.diagnostics = {}
        patcher = patch('assistant.services.get_online_retriever', return_value=self.transport)
        patcher.start(); self.addCleanup(patcher.stop)
        engine = MagicMock()
        def grounded_generation(query, **kwargs):
            instruction = kwargs.get('system_instruction', '')
            text = ('Quartz protocol uses amber tokens.' if 'Quartz protocol uses amber tokens.' in instruction else
                    'Recursion solves a problem by calling the same procedure on smaller instances.' if 'Recursion solves' in instruction else
                    'Prabhas is working on Example Project.')
            return AIEngineResult(text=text, engine='local', model='test', mode='local', latency_ms=1)
        engine.generate.side_effect = grounded_generation
        patcher = patch('assistant.services.get_engine', return_value=engine)
        patcher.start(); self.addCleanup(patcher.stop)
        # Capability detection is local configuration, never an internet probe.
        patcher = patch('assistant.online.factory.is_online_retrieval_available', return_value=True)
        patcher.start(); self.addCleanup(patcher.stop)

    def ask(self, query, **options):
        response = self.client.post('/api/assistant/chat/', dict(query=query, **options), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def seed(self):
        result = OnlineKnowledgeSyncService().persist_results('What movie is Prabhas currently working on?', [self.source])
        self.assertEqual(result.created, 1)

    def test_online_answer_persists_then_recreated_service_reuses_offline(self):
        first = self.ask('What movie is Prabhas currently working on?')
        self.assertIn('Example Project', first['response'])
        self.assertEqual(EvidenceVersion.objects.filter(state='VERIFIED').count(), 1)
        from assistant.online.searxng import clear_online_cache
        clear_online_cache()
        self.transport.retrieve.side_effect = AssertionError('Offline must not contact transport')
        answer = AssistantService().process_message('Which movie is Prabhas working on?', knowledge_mode='offline_only')
        self.assertIn('Example Project', answer[1])
        self.assertIn('not checked live', answer[1])

    def test_expired_paraphrase_and_pronoun_history_remain_available(self):
        self.seed()
        yesterday = self.now - timedelta(days=1)
        EvidenceVersion.objects.update(verified_at=yesterday, expires_at=yesterday)
        Document.objects.update(expires_at=yesterday)
        answer = self.ask('Which movie was Prabhas working on when I checked yesterday?', knowledge_mode='offline_only')
        self.assertIn('Example Project', answer['response'])
        self.assertIn('HISTORICAL', answer['response'])
        answer = self.ask('What is he working on now?', knowledge_mode='offline_only', conversation_id=answer['conversation_id'])
        self.assertIn('Example Project', answer['response'])
        self.transport.retrieve.assert_not_called()

    def test_outage_fallback_and_online_only_abstention(self):
        self.seed()
        Document.objects.update(expires_at=self.now-timedelta(days=1))
        self.transport.retrieve.side_effect = OnlineRetrievalTimeoutError()
        answer = self.ask('Which movie is Prabhas working on now?')
        self.assertIn('Example Project', answer['response'])
        answer = self.ask('Which movie is Prabhas working on now?', knowledge_mode='online_only')
        self.assertNotIn('Example Project', answer['response'])

    def test_refresh_failure_preserves_version_and_actual_stamps(self):
        self.seed()
        before = list(EvidenceVersion.objects.values())
        retriever = MagicMock(); retriever.retrieve.side_effect = OnlineRetrievalTimeoutError()
        result = OnlineKnowledgeSyncService(retriever=retriever).sync_document(Document.objects.get())
        self.assertEqual(result.failed, 1)
        self.assertEqual(before, list(EvidenceVersion.objects.values()))

    def test_changed_source_retains_superseded_text(self):
        self.seed()
        newer = replace(self.source, content='Prabhas is working on the announced movie Another Project.', retrieved_at=self.now+timedelta(seconds=1))
        OnlineKnowledgeSyncService().persist_results('What movie is Prabhas currently working on?', [newer])
        self.assertEqual(Document.objects.count(), 1)
        self.assertEqual(EvidenceVersion.objects.count(), 2)
        self.assertTrue(EvidenceVersion.objects.filter(state='SUPERSEDED', content__contains='Example Project').exists())

    def test_unverified_and_undated_current_not_admitted(self):
        for source in [replace(self.source, verified_at=None), replace(self.source, published_at=None), replace(self.source, accessibility='unavailable')]:
            result = OnlineKnowledgeSyncService().persist_results('What movie is Prabhas currently working on?', [source])
            self.assertEqual(result.failed, 1)
        self.assertFalse(EvidenceVersion.objects.exists())

    def test_pdf_txt_markdown_scoping_and_delete(self):
        for name, content in [('guide.pdf', pdf_bytes('Quartz protocol uses amber tokens.')), ('guide.txt', b'Quartz protocol uses amber tokens.'), ('guide.md', b'# Quartz protocol\nQuartz protocol uses amber tokens.')]:
            response = self.client.post('/api/knowledge/documents/', {'file': SimpleUploadedFile(name, content)})
            self.assertEqual(response.status_code, 201)
            doc = response.json()['document']
            answer = self.ask('What tokens does Quartz protocol use?', document_ids=[doc['id']], knowledge_mode='online_only')
            self.assertTrue(answer['rag_used'])
            self.assertIn('amber tokens', answer['response'])
            self.assertEqual(answer['rag_sources'][0]['document_id'], doc['id'])
            if name.endswith('.pdf'):
                self.assertEqual(answer['rag_sources'][0]['page_number'], 1)
            self.assertEqual(self.client.delete(f"/api/knowledge/documents/{doc['id']}/").status_code, 204)
            from .retrieval import LexicalRetriever
            self.assertEqual(LexicalRetriever(document_ids=[doc['id']]).retrieve('Quartz protocol'), [])
        self.transport.retrieve.assert_not_called()

    def test_library_real_bundle_indexes_idempotently(self):
        from .library import initialize_library
        first = initialize_library()
        self.assertGreaterEqual(first['indexed'], 9)
        self.assertEqual(first['failed'], 0)
        second = initialize_library()
        self.assertEqual(second['indexed'], 0)
        self.assertEqual(second['unchanged'], first['indexed'])
        self.assertTrue(Document.objects.filter(source_identifier__startswith='reference:', chunks__isnull=False).exists())

    def test_general_factual_prefers_verified_web_over_local(self):
        from .services import KnowledgeIngestionService
        KnowledgeIngestionService().ingest_text('Recursion is an old local explanation.', filename='old.txt', file_type='text/plain', source_identifier='manual:old')
        self.transport.retrieve.return_value = [replace(self.source, title='Recursion reference',
            content='Recursion solves a problem by calling the same procedure on smaller instances.',
            evidence_kind='fetched_reference', entertainment_status='')]
        answer = self.ask('What is recursion?')
        self.assertTrue(answer['online_used'])
        self.assertFalse(answer['rag_used'])
        self.transport.retrieve.assert_called_once()
        self.assertEqual(EvidenceVersion.objects.filter(state='VERIFIED').count(), 1)

    def test_semantic_fresh_cache_avoids_duplicate_search(self):
        self.seed()
        from .archive import fresh_variations
        from .online_sync import load_verified_current
        self.assertTrue(fresh_variations('Which movie is Prabhas currently working on?'))
        from assistant.online.grounding import select_current_evidence
        values = fresh_variations('Which movie is Prabhas currently working on?')
        self.assertTrue(select_current_evidence(values, 'Which movie is Prabhas currently working on?', timezone.now()), repr(values))
        self.assertTrue(load_verified_current('Which movie is Prabhas currently working on?'))
        answer = self.ask('Which movie is Prabhas currently working on?')
        self.assertIn('Example Project', answer['response'])
        self.transport.retrieve.assert_not_called()

    def test_streaming_voice_uses_same_persistent_archive(self):
        from assistant.policy import AssistantResponsePolicy
        self.seed()
        query = 'Which movie is Prabhas working on?'
        events = list(AssistantService.process_message_stream(query, knowledge_mode='offline_only',
            response_plan=AssistantResponsePolicy.plan_voice_response(query)))
        self.assertEqual(events[-1]['type'], 'done')
        self.assertIn('Example Project', events[-1]['response'])
        self.assertEqual(events[-1]['mode'], 'offline')
        self.transport.retrieve.assert_not_called()

    def test_archive_capacity_is_fail_closed_without_deleting_old_data(self):
        self.seed()
        with override_settings(KNOWLEDGE_ARCHIVE_MAX_BYTES=1024):
            larger = replace(self.source, content=self.source.content+' Evidence context.'*200)
            result = OnlineKnowledgeSyncService().persist_results('What movie is Prabhas currently working on?', [larger])
        self.assertEqual(result.failed, 1)
        self.assertEqual(EvidenceVersion.objects.count(), 1)

    def test_corrupted_archive_text_is_never_used(self):
        self.seed()
        EvidenceVersion.objects.update(content='Prabhas is working on Invented Project.')
        answer = self.ask('Which movie is Prabhas working on?', knowledge_mode='offline_only')
        self.assertNotIn('Invented Project', answer['response'])

    @override_settings(ONLINE_RETRIEVAL_ENABLED=False)
    def test_production_source_gate_abstains_without_evidence(self):
        with patch('assistant.online.factory.is_online_retrieval_available', return_value=False), patch('assistant.services.get_engine') as engine:
            answer = self.ask('What is the habitat of the imaginary quartz bird?')
        self.assertIn('not have enough source evidence', answer['response'])
        engine.assert_not_called()
        self.transport.retrieve.assert_not_called()

    def test_unknown_selected_document_abstains_without_model(self):
        with patch('assistant.services.get_engine') as engine:
            answer = self.ask('What does the report say?', document_ids=[99999])
        self.assertIn('selected documents do not contain enough', answer['response'])
        engine.assert_not_called()


class MaintenanceTests(TestCase):
    def test_backup_schema_restore_retains_uploads_and_displaced_database(self):
        import sqlite3
        from pathlib import Path
        from scripts.knowledge_backup import backup, restore, connection
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'app'; root.mkdir()
            (root/'media').mkdir(); (root/'media/original.txt').write_text('keep')
            (root/'.env').write_text('original environment')
            with connection(root/'db.sqlite3') as db:
                db.execute('CREATE TABLE example (value TEXT)')
                db.execute("INSERT INTO example VALUES ('before')")
            destination = Path(directory)/'backup'
            backup(root, destination)
            with connection(root/'db.sqlite3') as db:
                db.execute('ALTER TABLE example ADD COLUMN extra TEXT')
                db.execute("INSERT INTO example(value) VALUES ('after')")
            (root/'media/new.txt').write_text('new upload')
            (root/'.env').write_text('updated environment')
            recovery = restore(root, destination)
            with connection(root/'db.sqlite3') as db:
                self.assertEqual(db.execute('SELECT * FROM example').fetchall(), [('before',)])
            with connection(recovery) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM example').fetchone()[0], 2)
            self.assertEqual((root/'media/new.txt').read_text(), 'new upload')
            self.assertEqual((root/'.env').read_text(), 'updated environment')

    def test_sqlite_archive_survives_separate_process_restart(self):
        import os
        import subprocess
        import sys
        from pathlib import Path
        from django.conf import settings
        with tempfile.TemporaryDirectory() as directory:
            common = '''
import os, django
os.environ['DJANGO_SETTINGS_MODULE']='config.test_settings'
from django.conf import settings
settings.DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':os.environ['PROBE_DB']}}
settings.MEDIA_ROOT=os.environ['PROBE_MEDIA']
settings.AI_ENGINE='local'
django.setup()
'''
            first = common + '''
from django.core.management import call_command
call_command('migrate',verbosity=0)
from django.utils import timezone
from assistant.online.base import OnlineRetrievalResult
from knowledge_base.online_sync import OnlineKnowledgeSyncService
now=timezone.now()
source=OnlineRetrievalResult('Quartz protocol reference','https://example.org/quartz','Quartz protocol uses amber tokens.', 'searxng',now,verified_at=now,accessibility='available',evidence_kind='fetched_reference')
assert OnlineKnowledgeSyncService().persist_results('What tokens does Quartz protocol use?',[source]).created==1
'''
            second = common + '''
from unittest.mock import patch
from assistant.services import AssistantService
report={'recommended_profile':'BALANCED','ai_recommendation':{'local_ai_allowed':True,'online_allowed':False,'generation_budget':'NORMAL'}}
with patch('socket.create_connection',side_effect=AssertionError('No external network')), patch('assistant.execution_policy.get_resource_manager_report',return_value=report), patch('assistant.services.get_online_retriever',side_effect=AssertionError('No retrieval')):
    _,text,_,error=AssistantService.process_message('Which tokens are used by Quartz protocol?',knowledge_mode='offline_only')
    assert error is None and 'amber tokens' in text and 'not checked live' in text, text
'''
            env = dict(os.environ, PROBE_DB=str(Path(directory)/'probe.sqlite3'), PROBE_MEDIA=str(Path(directory)/'media'))
            for code in (first, second):
                result = subprocess.run([sys.executable, '-c', code], env=env, cwd=settings.BASE_DIR, capture_output=True, text=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stderr)
