"""Synthetic reports and mocked transport only; no real-source availability claims."""
import json
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, TestCase, override_settings

from assistant.ai_engine.base import AIEngineResult, AIEngineStreamEvent
from assistant.online import enrichment as e
from assistant.online.base import OnlineRetrievalResult, OnlineRetrievalTimeoutError
from assistant.online.current_sources import page_evidence, classify_claims
from assistant.online.dates import parse_publication_date
from assistant.online.facts import source_supported_answer
from assistant.online.grounding import select_current_evidence, has_conflicting_claims, build_online_grounding_instruction
from assistant.online.intent import search_queries, search_filters
from assistant.online.searxng import SearXNGOnlineRetriever, clear_online_cache
from assistant.routing import QueryRouter
from assistant.services import AssistantService
from assistant.test_support import install_resource_report
from knowledge_base.models import Document
from knowledge_base.online_sync import OnlineKnowledgeSyncService, load_verified_current

NOW = datetime.now(timezone.utc)
DATE = (NOW+timedelta(days=1)).date().isoformat()
QUESTION = 'When will Example Movie release?'
CLAIM = 'Example Movie will release on '+DATE+'.'
URL = 'https://publisher.example/reports/example-movie'
REPORTS = json.loads((Path(__file__).parent/'fixtures/current_reports.json').read_text())


def result(content=CLAIM, **changes):
    item = OnlineRetrievalResult('Example source report', URL, content, 'searxng', NOW,
        published_at=NOW, publication_source='article_metadata', verified_at=NOW,
        verified_url=URL, accessibility='available', evidence_kind='fetched_report',
        entertainment_status='reported_confirmed_or_scheduled',
        claim_status='reported_scheduled')
    return replace(item, **changes)


def provider(timeout=4, limit=4):
    return SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888', timeout_seconds=timeout, max_results=limit)


class GeneralAcquisitionTests(SimpleTestCase):
    def setUp(self):
        clear_online_cache(); e._CACHE.clear(); e._HOST_BACKOFF.clear()
        clock = patch('assistant.online.enrichment.datetime')
        clock.start().now.return_value = NOW
        self.addCleanup(clock.stop)

    def test_formulation_preserves_regions_entities_dates_and_intent(self):
        for industry, region in (('Tollywood', 'Telugu'), ('Kollywood', 'Tamil'), ('Mollywood', 'Malayalam'), ('Bollywood', 'Hindi')):
            first, second = search_queries('Can you tell me about '+industry+' movie releases this week?')
            self.assertIn(region, first)
            self.assertIn('this week', first)
            self.assertNotIn('Can you', first)
            self.assertIn('release schedule', second)
        first, second = search_queries('What is actor Prabhas working on now?')
        self.assertIn('Prabhas', first)
        self.assertIn('production announcement', second)
        self.assertNotIn('release notes', second)
        self.assertIn('2026-11-01', search_queries('Tamil releases on 2026-11-01')[0])
        self.assertEqual(search_filters('news today')['time_range'], 'day')
        self.assertEqual(search_filters('films this week')['time_range'], 'week')
        self.assertTrue(QueryRouter.requires_online('When is Example Probe scheduled to launch?'))
        self.assertFalse(QueryRouter.requires_online('Answer offline only: '+QUESTION))

    @patch('assistant.online.enrichment.fetch_metadata')
    def test_report_fixtures_extract_admit_and_label_without_search_title_evidence(self, fetch):
        for record in REPORTS:
            with self.subTest(report=record['id']):
                text = record['text'].format(today=NOW.date().isoformat())
                url = 'https://publisher.example/reports/'+record['id']
                candidate = result(text, title=record['query'], url=url, published_at=None,
                                   verified_at=None, evidence_kind='', accessibility='unchecked')
                fetch.return_value = (NOW, None, False, None, '', {'text': text, 'url': url})
                admitted = select_current_evidence(e.enrich_results([candidate], record['query'], time.monotonic()+2), record['query'], NOW)
                self.assertEqual(len(admitted), 1)
                self.assertEqual(admitted[0].claim_status, record['status'])
                self.assertEqual(admitted[0].published_at, NOW)
                self.assertIn('Report status: '+record['status'], build_online_grounding_instruction(admitted, query=record['query'], request_time=NOW, max_context_chars=3600)[0])
        self.assertEqual(fetch.call_count, len(REPORTS))
        self.assertEqual(page_evidence('An unrelated singer announced a different album.', URL, 'Prabhas next movie'), ('', ''))

    def test_regional_heading_does_not_leak_to_a_different_language_section(self):
        text = 'Telugu films\nTamil films\nPaper Moon will release on '+DATE+'.'
        self.assertEqual(page_evidence(text, URL, 'Telugu movie releases this week'), ('', ''))
        self.assertEqual(page_evidence('Subscribe to read the latest technology news and updates.', URL, 'latest technology news'), ('', ''))
        self.assertEqual(page_evidence('Browse a broad collection of interesting local stories.', URL, 'Current affairs today'), ('', ''))

    def test_conversational_actor_query_and_named_organization_body_anchors(self):
        claim = 'Prabhas officially announced the film Example Journey.'
        self.assertEqual(page_evidence(claim, URL, 'What movies did actor Prabhas announce recently?')[0], claim)
        query = 'Latest OpenAI AI model developments'
        self.assertEqual(page_evidence('OtherLab researchers announced an AI model evaluation.', URL, query), ('', ''))
        claim = 'OpenAI researchers announced a new AI model evaluation.'
        self.assertEqual(page_evidence(claim, URL, query)[0], claim)

    def test_general_confirmation_schedule_production_and_completion_have_separate_labels(self):
        for text, expected in (
            ('Example Labs confirmed the proposed evaluation.', 'reported_confirmation'),
            (CLAIM, 'reported_scheduled'),
            ('Example Probe was launched on '+NOW.date().isoformat()+'.', 'reported_released'),
            ('Prabhas began filming Example Journey.', 'reported_production'),
            ('Example Probe will launch on 2020-01-01.', 'outdated_schedule')):
            self.assertEqual(classify_claims(text), expected)

    def test_rumours_denials_confirmed_reporting_and_outdated_schedules_are_distinct(self):
        query = 'Latest actor Prabhas movie announcements'
        for sentence, status in (
            ('Prabhas officially announced Example Journey.', 'reported_project'),
            ('Prabhas is reportedly in talks for Example Journey.', 'rumour_or_unconfirmed'),
            ('Prabhas might star in Example Journey.', 'rumour_or_unconfirmed'),
            ('Prabhas has not confirmed Example Journey.', 'unconfirmed_or_denied'),
            ('Prabhas denied rumours about Example Journey.', 'unconfirmed_or_denied'),
            ('Prabhas film Example Journey will release on 2020-01-01.', 'outdated_schedule')):
            self.assertEqual(page_evidence(sentence, URL, query)[1], status)
        outdated = result('Telugu film Example Journey will release on 2020-01-01.',
                          evidence_kind='fetched_report', entertainment_status='outdated_schedule')
        self.assertEqual(select_current_evidence([outdated], 'Telugu movie releases this week', NOW), [])

    @patch('assistant.online.enrichment.fetch_metadata')
    def test_missing_expired_future_conflicting_dates_and_inaccessible_pages_abstain(self, fetch):
        query = 'Latest actor Prabhas movie announcements'
        candidate = result('Prabhas will star in Example Journey.', evidence_kind='', published_at=None,
                           verified_at=None, accessibility='unchecked')
        for publication, failure in ((None, None), (NOW-timedelta(days=40), None),
                                     (NOW+timedelta(days=1), None), (NOW, e.PageUnavailable(403))):
            e._CACHE.clear(); e._HOST_BACKOFF.clear()
            fetch.side_effect = failure
            fetch.return_value = (publication, None, False, None, '', {'text': candidate.content, 'url': URL})
            self.assertEqual(select_current_evidence(e.enrich_results([candidate], query, time.monotonic()+2), query, NOW), [])
        stale = result(verified_at=NOW-timedelta(minutes=6))
        self.assertEqual(select_current_evidence([stale], QUESTION, NOW), [])
        self.assertEqual(source_supported_answer(QUESTION, [result(date_conflict=True)], NOW), None)
        self.assertIsNone(parse_publication_date('2 hours ago'))
        self.assertEqual(parse_publication_date('October 25 2026').date().isoformat(), '2026-10-25')

    @patch.object(SearXNGOnlineRetriever, '_search')
    @patch('assistant.online.enrichment.fetch_metadata')
    def test_single_successful_search_process_and_metadata_cache_preserve_verification(self, fetch, search):
        search.return_value = [result(evidence_kind='', accessibility='unchecked', published_at=None, verified_at=None)]
        fetch.return_value = (NOW, None, False, None, '', {'text': CLAIM, 'url': URL})
        first = provider().retrieve(QUESTION)
        cached_provider = provider()
        cached = cached_provider.retrieve(QUESTION)
        self.assertEqual(cached[0].verified_at, first[0].verified_at)
        self.assertTrue(cached_provider.diagnostics['cache_hit'])
        search.assert_called_once(); fetch.assert_called_once()
        clear_online_cache()  # URL metadata remains; do not manufacture a new check.
        with patch('assistant.online.enrichment.datetime') as clock:
            clock.now.return_value = NOW+timedelta(seconds=30)
            reused = provider().retrieve(QUESTION)
        self.assertEqual(reused[0].verified_at, first[0].verified_at)
        fetch.assert_called_once()

    @patch.object(SearXNGOnlineRetriever, '_search')
    @patch('assistant.online.enrichment.fetch_metadata', side_effect=e.PageUnavailable(404))
    def test_failed_discovery_is_at_most_two_distinct_intent_searches(self, fetch, search):
        search.return_value = [result('Prabhas will star in Example Journey.', verified_at=None,
                                      published_at=None, evidence_kind='', accessibility='unchecked')]
        instance = provider()
        query = 'What movie is actor Prabhas working on now?'
        self.assertEqual(select_current_evidence(instance.retrieve(query), query, NOW), [])
        calls = [call.args[0] for call in search.call_args_list]
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(set(calls)), 2)
        self.assertTrue(all('Prabhas' in call and 'IANA' not in call and 'release notes' not in call for call in calls))
        fetch.assert_called_once()

    @patch.object(SearXNGOnlineRetriever, '_search')
    @patch('assistant.online.enrichment.fetch_metadata')
    def test_fallback_search_preserves_remaining_fetch_and_total_budgets(self, fetch, search):
        clock = [100.0]
        candidate = result('Prabhas will star in Example Journey.', verified_at=None, published_at=None,
                           evidence_kind='', accessibility='unchecked')
        def discover(query, deadline):
            self.assertEqual(deadline, 104.0)
            clock[0] += 1.4
            return [replace(candidate, url=URL+str(search.call_count))]
        def page(url, deadline):
            self.assertLessEqual(deadline, 104.0)
            clock[0] += .1
            if fetch.call_count == 1:
                raise e.PageUnavailable(404)
            return (NOW, None, False, None, '', {'text': candidate.content, 'url': url})
        search.side_effect = discover; fetch.side_effect = page
        with patch('assistant.online.searxng.time.monotonic', side_effect=lambda: clock[0]):
            instance = provider()
            accepted = select_current_evidence(instance.retrieve('Latest actor Prabhas movie announcements'), 'Latest actor Prabhas movie announcements', NOW)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(search.call_count, 2)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(instance.diagnostics['search_ms'], 2800)
        self.assertEqual(instance.diagnostics['enrichment_ms'], 200)

    @patch.object(SearXNGOnlineRetriever, '_search')
    def test_slow_search_exhausts_shared_deadline_without_another_call(self, search):
        clock = [100.0]
        def slow(query, deadline):
            clock[0] = deadline
            return [result()]
        search.side_effect = slow
        with patch('assistant.online.searxng.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(OnlineRetrievalTimeoutError):
                provider(timeout=.2).retrieve(QUESTION)
        search.assert_called_once()

    @patch('assistant.online.enrichment.fetch_metadata')
    def test_narrow_general_date_question_fetches_dated_candidate_even_with_limit_one(self, fetch):
        query = 'When will Example Probe launch?'
        content = 'Example Probe will launch on '+DATE+'.'
        candidate = result(content, published_at=NOW, publication_source='searxng',
                           accessibility='unchecked', verified_at=None, evidence_kind='')
        fetch.return_value = (NOW, None, False, None, '', {'text': content, 'url': URL})
        items = e.enrich_results([candidate], query, time.monotonic()+2, target=1)
        fetch.assert_called_once()
        self.assertIn(content, source_supported_answer(query, items, NOW))

    @patch('assistant.online.enrichment.public_target')
    @patch('assistant.online.enrichment.socket.create_connection')
    @patch('assistant.online.enrichment.http.client.HTTPConnection')
    def test_visible_label_publication_is_not_update_event_or_title_date(self, connection, connect, target):
        target.return_value = (urlsplit(URL), 443, '93.184.216.34')
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.getheader.side_effect = lambda name, default='': 'text/html' if name == 'Content-Type' else default
        for label, expected in (('Published: 25 October 2026', '2026-10-25'),
                                ('Updated: 25 October 2026', None), ('Launch date: 25 October 2026', None)):
            response.read1.side_effect = [('<title>25 October 2026</title><p>'+label+'</p><p>'+CLAIM+'</p>').encode(), b'']
            with patch('assistant.online.enrichment.ssl.create_default_context'):
                publication = e.fetch_metadata(URL, time.monotonic()+2)[0]
            self.assertEqual(publication.date().isoformat() if publication else None, expected)


class DirectFactTests(SimpleTestCase):
    def test_narrow_attributed_schedule_and_announcement_without_generation(self):
        self.assertIn(CLAIM, source_supported_answer(QUESTION, [result()], NOW))
        self.assertIn(NOW.isoformat(), source_supported_answer(QUESTION, [result()], NOW))
        may = result('Example Movie will release on May 1, '+str(NOW.year+1)+'.')
        self.assertIsNotNone(source_supported_answer(QUESTION, [may], NOW))
        past = result('Example Tool was announced on '+NOW.date().isoformat()+'.')
        self.assertIsNotNone(source_supported_answer('When was Example Tool announced?', [past], NOW))

    def test_broad_ambiguous_rumoured_denied_unverified_or_outdated_keeps_llm_path(self):
        for query, item in ((QUESTION, result('Example Movie might release on '+DATE+'.')),
                            (QUESTION, result('Example Movie will not release on '+DATE+'.')),
                            (QUESTION, result('Example Movie will release on 2020-01-01.')),
                            (QUESTION, result('Other Movie will release on '+DATE+'.')),
                            (QUESTION, result(publication_source='searxng')),
                            (QUESTION, result(verified_at=None)),
                            (QUESTION, result(verified_url='http://publisher.example/article')),
                            ('Latest movies and AI news', result())):
            self.assertIsNone(source_supported_answer(query, [item], NOW))

    def test_incompatible_dates_and_denials_conflict_but_rumours_are_not_confirmation(self):
        other = result('Example Movie will release on '+(NOW+timedelta(days=2)).date().isoformat()+'.', url=URL+'-other')
        self.assertTrue(has_conflicting_claims([result(), other], QUESTION))
        self.assertIsNone(source_supported_answer(QUESTION, [result(), other], NOW))
        denied = result('Example Movie will not release on '+DATE+'.')
        self.assertTrue(has_conflicting_claims([result(), denied], QUESTION))
        rumour = result('Example Movie reportedly will release on '+DATE+'.')
        self.assertFalse(has_conflicting_claims([result(), rumour], QUESTION))
        self.assertIsNone(source_supported_answer(QUESTION, [result(), rumour], NOW))


@override_settings(AI_ENGINE='local', ONLINE_RETRIEVAL_ENABLED=True, RAG_ENABLED=False,
                   ONLINE_KNOWLEDGE_SYNC_ENABLED=True, ONLINE_KNOWLEDGE_AUTO_PERSIST=True)
class GeneralServiceTests(TestCase):
    def setUp(self):
        install_resource_report(self, online_allowed=True)
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        media = override_settings(MEDIA_ROOT=folder.name); media.enable(); self.addCleanup(media.disable)
        clock = patch('assistant.services.django_timezone.now', return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)
        engine = patch('assistant.services.get_engine'); self.engine = engine.start().return_value
        self.addCleanup(engine.stop)
        answer = AIEngineResult(text='Grounded source summary.', engine='local')
        self.engine.generate.return_value = answer
        self.engine.generate_stream.side_effect = lambda *a, **kw: iter([
            AIEngineStreamEvent(type='delta', text=answer.text), AIEngineStreamEvent(type='done', result=answer)])

    def ask(self, query, stream):
        return list(AssistantService.process_message_stream(query))[-1] if stream else AssistantService.process_message(query)[2]

    @patch('assistant.services.get_online_retriever')
    def test_direct_both_transports_persist_cache_and_offline_without_timestamp_extension(self, retriever):
        retriever.return_value.retrieve.return_value = [result()]
        retriever.return_value.diagnostics = {}
        first = self.ask(QUESTION, False)
        self.assertTrue(first['source_supported_direct'])
        self.assertTrue(first['online_used'])
        doc = Document.objects.get(source_type='ONLINE')
        self.assertEqual(doc.last_synced_at, NOW)
        self.assertEqual(load_verified_current(QUESTION)[0].claim_status, 'reported_scheduled')
        for stream in (False, True):
            cached = self.ask(QUESTION, stream)
            self.assertTrue(cached['cache_hit'])
            self.assertFalse(cached['online_attempted'])
            self.assertTrue(cached['source_supported_direct'])
            offline = self.ask('Answer offline only: '+QUESTION, stream)
            self.assertEqual(offline['fallback_reason'], 'offline_verified_cache')
        answer = AssistantService.process_message('Answer offline only: '+QUESTION)[1]
        self.assertIn('not a live check', answer)
        self.assertIn(NOW.isoformat(), answer)
        retriever.return_value.retrieve.assert_called_once()
        doc.refresh_from_db()
        self.assertEqual(doc.last_synced_at, NOW)
        self.assertEqual(doc.expires_at, NOW+timedelta(minutes=5))
        self.engine.generate.assert_not_called(); self.engine.generate_stream.assert_not_called()

    @patch('assistant.services.get_online_retriever')
    def test_cache_expiry_miss_reverifies_and_expired_offline_abstains(self, retriever):
        OnlineKnowledgeSyncService().persist_results(QUESTION, [result()])
        Document.objects.update(expires_at=NOW-timedelta(seconds=1))
        retriever.return_value.retrieve.return_value = [result()]
        retriever.return_value.diagnostics = {}
        for stream in (False, True):
            self.assertEqual(self.ask('Answer offline only: '+QUESTION, stream)['fallback_reason'], 'offline_requested')
        retriever.assert_not_called()
        self.assertTrue(self.ask(QUESTION, False)['online_attempted'])

    @patch('assistant.services.get_online_retriever')
    def test_broad_questions_and_rumours_use_grounded_llm_both_transports(self, retriever):
        query = 'Latest actor Prabhas movie announcements'
        retriever.return_value.retrieve.return_value = [result('Prabhas is reportedly in talks for Example Journey.',
            evidence_kind='fetched_report', entertainment_status='rumour_or_unconfirmed', claim_status='rumour_or_unconfirmed')]
        retriever.return_value.diagnostics = {}
        for stream in (False, True):
            metadata = self.ask(query, stream)
            self.assertTrue(metadata['online_used'])
            self.assertFalse(metadata.get('source_supported_direct', False))
        self.engine.generate.assert_called_once(); self.engine.generate_stream.assert_called_once()

    @patch('assistant.services.get_online_retriever')
    def test_no_evidence_and_conflicting_announcements_abstain_without_model_or_cache(self, retriever):
        for items, reason in (([], 'no_relevant_current_results'),
                              ([result(), result(CLAIM.replace(DATE, (NOW+timedelta(days=2)).date().isoformat()))], 'conflicting_evidence')):
            retriever.return_value.retrieve.return_value = items
            retriever.return_value.diagnostics = {}
            for stream in (False, True):
                self.assertEqual(self.ask(QUESTION, stream)['fallback_reason'], reason)
        self.assertFalse(Document.objects.exists())
        self.engine.generate.assert_not_called(); self.engine.generate_stream.assert_not_called()
