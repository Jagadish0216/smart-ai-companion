import io
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from django.test import TestCase, SimpleTestCase, override_settings
from django.core.management import call_command
from assistant.ai_engine.base import AIEngineResult, AIEngineStreamEvent
from assistant.online.base import OnlineRetrievalResult
from assistant.online.evidence_policy import evidence_category, release_statement
from assistant.online.grounding import select_current_evidence
from assistant.policy import AssistantResponsePolicy
from assistant.services import AssistantService
from assistant.test_support import install_resource_report
from knowledge_base.retrieval import RetrievedChunk

NOW=datetime(2026,10,24,12,tzinfo=timezone.utc)
CATALOG='https://www.raspberrypi.com/software/operating-systems/'


class EvidencePolicyTests(SimpleTestCase):
    def source(self,**kwargs):
        return replace(OnlineRetrievalResult('Raspberry Pi OS','https://example.org/article','Raspberry Pi OS implements a Linux desktop and hardware drivers.','searxng',NOW,accessibility='available'),**kwargs)

    def test_categories_and_undated_technical_references(self):
        for query,category in [('Search the web for how Raspberry Pi OS works','technical'),('Search the web for current CEO','current'),('latest Raspberry Pi OS version','release'),('Raspberry Pi news today','current')]:
            self.assertEqual(evidence_category(query),category)
        self.assertTrue(select_current_evidence([self.source()],'Search the web for how Raspberry Pi OS works',NOW))
        self.assertEqual(select_current_evidence([self.source()],'Raspberry Pi OS news today',NOW),[])
        self.assertEqual(select_current_evidence([self.source(accessibility='unavailable')],'Search the web for how Raspberry Pi OS works',NOW),[])
        self.assertEqual(select_current_evidence([self.source(published_at=NOW+timedelta(days=1))],'Search the web for how Raspberry Pi OS works',NOW),[])

    def test_official_catalog_extracts_primary_tables_not_legacy(self):
        text='Raspberry Pi OS (Legacy) navigation Raspberry Pi OS Release date 6 Oct 2026 Debian version 13 (trixie) Raspberry Pi OS Full Release date 6 Oct 2026 Debian version 13 (trixie) Raspberry Pi OS (Legacy, 64-bit) Release date 6 Oct 2026 Debian version 12 (bookworm)'
        date,evidence=release_statement(text,CATALOG)
        self.assertEqual(date.date().isoformat(),'2026-10-06')
        self.assertIn('13 (trixie)',evidence)
        self.assertNotIn('bookworm',evidence)
        self.assertEqual(release_statement(text,'https://raspberrypi.com.evil.example/software/operating-systems/'),(None,''))
        self.assertEqual(release_statement('Raspberry Pi OS Release date 6 Oct 2026 Debian version 13 Release date 7 Oct 2026 Debian version 12',CATALOG),(None,''))

    def test_old_release_date_can_support_verified_catalog_but_not_news(self):
        date,evidence=release_statement('Raspberry Pi OS Release date: 2026-10-06 Debian version: 13 (trixie)',CATALOG)
        result=self.source(url=CATALOG,release_at=date,release_evidence=evidence,verified_at=NOW)
        query='What is the latest Raspberry Pi OS version as of October 24, 2026?'
        admitted=select_current_evidence([result],query,NOW)
        self.assertEqual(len(admitted),1)
        self.assertIsNone(admitted[0].published_at)
        self.assertEqual(admitted[0].content,evidence)
        self.assertEqual(select_current_evidence([result],'Latest Raspberry Pi OS news today',NOW),[])
        for invalid in (replace(result,accessibility='unchecked'),replace(result,verified_at=NOW-timedelta(minutes=10)),replace(result,release_at=NOW+timedelta(days=1)),replace(result,date_conflict=True)):
            self.assertEqual(select_current_evidence([invalid],query,NOW),[])
        self.assertEqual(select_current_evidence([result],'Latest Raspberry Pi OS version as of October 23, 2026',NOW),[])
        self.assertEqual(select_current_evidence([result],'Latest Raspberry Pi OS version as of September 2026',NOW),[])

    @patch('assistant.online.enrichment.public_target')
    @patch('assistant.online.enrichment.socket.create_connection')
    @patch('assistant.online.enrichment.ssl.create_default_context')
    @patch('assistant.online.enrichment.http.client.HTTPConnection')
    def test_release_fields_are_extracted_from_verified_html(self,connection,ssl,connect,target):
        import time
        from urllib.parse import urlsplit
        from assistant.online.enrichment import fetch_metadata
        target.return_value=(urlsplit(CATALOG),443,'93.184.216.34')
        response=connection.return_value.getresponse.return_value;response.status=200
        response.getheader.side_effect=lambda key,default='':'text/html' if key=='Content-Type' else default
        response.read1.side_effect=[b'<h2>Raspberry Pi OS</h2><table><tr><td>Release date</td><td>6 Oct 2026</td></tr><tr><td>Debian version</td><td>13 (trixie)</td></tr></table>',b'']
        fields=fetch_metadata(CATALOG,time.monotonic()+2)
        self.assertIsNone(fields[0])
        self.assertEqual(fields[3].date().isoformat(),'2026-10-06')
        self.assertIn('Debian version: 13 (trixie)',fields[4])


@override_settings(AI_ENGINE='local',RAG_ENABLED=True,ONLINE_RETRIEVAL_ENABLED=False)
class RoutingReliabilityTests(TestCase):
    def setUp(self):install_resource_report(self)

    @patch('assistant.services.get_engine')
    @patch('assistant.routing.get_retriever')
    def test_general_questions_skip_irrelevant_rag_both_paths(self,retriever,engine):
        result=AIEngineResult(text='A Raspberry Pi 5 is a small Linux computer with a CPU, memory and hardware interfaces.',engine='local')
        engine.return_value.generate.return_value=result
        engine.return_value.generate_stream.return_value=iter([AIEngineStreamEvent(type='delta',text=result.text),AIEngineStreamEvent(type='done',result=result)])
        retriever.return_value.retrieve.return_value=[RetrievedChunk(1,1,'test','unrelated',0,'Our relay uses GPIO 22.',1)]
        for stream in (False,True):
            query='Explain how a Raspberry Pi 5 works.'
            if stream:
                metadata=list(AssistantService.process_message_stream(query))[-1]
                instruction=engine.return_value.generate_stream.call_args.kwargs['system_instruction']
            else:
                _,text,metadata,error=AssistantService.process_message(query)
                self.assertIsNone(error)
                instruction=engine.return_value.generate.call_args.kwargs['system_instruction']
            self.assertEqual(metadata['route'],'local')
            self.assertNotIn('local knowledge does not contain enough',instruction)
            self.assertNotIn('Runtime capabilities:',instruction)
        retriever.assert_not_called()

    @patch('assistant.services.get_engine')
    @patch('assistant.routing.get_retriever')
    @patch('knowledge_base.services.is_rag_available',return_value=True)
    def test_document_strict_and_project_general_are_distinct(self,available,retriever,engine):
        retriever.return_value.retrieve.return_value=[RetrievedChunk(1,1,'test','notes',0,'Our project uses an ESP32.',1)]
        engine.return_value.generate.return_value=AIEngineResult(text='A grounded answer.',engine='local')
        for query,expected in [('According to my notes, how does ESP32 work?','Use trusted local knowledge only'),('In our project, how does ESP32 work?','clearly labeled general explanation'),('Which GPIO did we use?','Use trusted local knowledge only')]:
            _,_,metadata,error=AssistantService.process_message(query)
            self.assertIsNone(error)
            self.assertEqual(metadata['route'],'local_rag')
            self.assertIn(expected,engine.return_value.generate.call_args.kwargs['system_instruction'])


class VerificationCommandTests(SimpleTestCase):
    @patch('assistant.management.commands.verify_intelligence.get_ai_execution_plan')
    @patch('assistant.management.commands.verify_intelligence.AssistantService.process_message')
    def test_command_prints_three_examples_without_persistence_ingestion(self,process,plan):
        plan.return_value=MagicMock(generation_allowed=True,resource_profile='BALANCED')
        process.side_effect=[(None,'A general explanation.',{'route':'local'},None),(None,'Cannot verify.',{'route':'online','online_used':False},None),(None,'Cannot verify news.',{'route':'online','online_used':False},None)]
        output=io.StringIO();call_command('verify_intelligence',stdout=output)
        rows=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(rows),3)
        self.assertTrue(all('wall_ms' in row for row in rows))

    def test_deployment_guards_precede_restart(self):
        script=Path('scripts/deploy_intelligence_reliability.sh').read_text()
        self.assertIn('bf8a08a',script)
        self.assertIn('git cherry-pick "$REVIEWED_COMMIT"',script)
        self.assertLess(script.index('manage.py test'),script.index('sudo systemctl restart'))
        self.assertIn('knowledge_signature',script)
        self.assertNotIn('git reset',script)
        self.assertNotIn('git stash',script)


@override_settings(AI_ENGINE='local',ONLINE_RETRIEVAL_ENABLED=True,ONLINE_KNOWLEDGE_AUTO_PERSIST=False,RAG_ENABLED=False,ONLINE_PROVIDER='searxng',SEARXNG_BASE_URL='http://127.0.0.1:8888')
class ReleaseServiceTests(TestCase):
    @patch('assistant.services.get_engine')
    @patch('assistant.services.django_timezone.now',return_value=NOW)
    @patch('assistant.online.enrichment.datetime')
    @patch('assistant.online.enrichment.fetch_metadata')
    @patch('assistant.online.searxng._open_search')
    def test_official_catalog_verified_in_both_transports(self,http,fetch,clock,now,engine):
        from assistant.online import enrichment
        from assistant.online.searxng import clear_online_cache
        install_resource_report(self,online_allowed=True)
        clock.now.return_value=NOW
        release,evidence=release_statement('Raspberry Pi OS Release date 6 Oct 2026 Debian version 13 (trixie)',CATALOG)
        fetch.side_effect=lambda url,deadline:(None,None,False,release,evidence) if url==CATALOG else (None,None,False)
        response=MagicMock();response.__enter__.return_value=response;response.read1=None
        response.geturl.return_value='http://127.0.0.1:8888/search'
        response.read.return_value=json.dumps({'results':[{'title':'Raspberry Pi OS updates','url':'https://example.org/article','content':'Raspberry Pi OS provides a Linux desktop environment.'}]}).encode()
        http.return_value=response
        result=AIEngineResult(text='The official catalog lists Debian 13 (trixie), with release date October 6.',engine='local')
        engine.return_value.generate.return_value=result
        engine.return_value.generate_stream.side_effect=lambda *a,**k:iter([AIEngineStreamEvent(type='delta',text=result.text),AIEngineStreamEvent(type='done',result=result)])
        for streaming in (False,True):
            clear_online_cache();enrichment._CACHE.clear()
            query='What is the latest Raspberry Pi OS version as of October 24, 2026?'
            metadata=list(AssistantService.process_message_stream(query))[-1] if streaming else AssistantService.process_message(query)[2]
            self.assertTrue(metadata['online_used'])
            source=metadata['online_sources'][0]
            self.assertIsNone(source['published_at'])
            self.assertEqual(source['release_at'][:10],'2026-10-06')
            self.assertEqual(source['accessibility'],'available')
