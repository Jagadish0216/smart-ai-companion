"""Network-free current-state acquisition, cache and routing regressions."""
import json
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from django.test import SimpleTestCase, TestCase, override_settings

from assistant.ai_engine.base import AIEngineResult, AIEngineStreamEvent
from assistant.online import enrichment as e
from assistant.online.base import OnlineRetrievalResult
from assistant.online.base import OnlineRetrievalTimeoutError
from assistant.online.current_sources import current_topic, page_evidence, known_office_profile
from assistant.online.dates import parse_publication_date
from assistant.online.grounding import select_current_evidence, has_conflicting_claims
from assistant.online.searxng import SearXNGOnlineRetriever, clear_online_cache
from assistant.routing import QueryRouter
from assistant.services import AssistantService
from assistant.test_support import install_resource_report
from knowledge_base.models import Document, KnowledgeChunk
from knowledge_base.online_sync import OnlineKnowledgeSyncService, load_verified_current
from knowledge_base.retrieval import LexicalRetriever

NOW = datetime.now(timezone.utc)
CM = 'Who is the Chief Minister of Telangana?'
CM_URL = 'https://www.telangana.gov.in/government/chief-minister/'
PM = 'Who is the Prime Minister of India?'
ACTOR = 'What movie is actor Prabhas working on now?'


def source(query=CM, **changes):
    political = query == CM
    item = OnlineRetrievalResult('Official Telangana Chief Minister' if political else 'Prabhas project report',
        CM_URL if political else 'https://publisher.example/cinema/prabhas-project',
        'Chief Minister of Telangana is Example Person.' if political else 'Prabhas will star in the announced film Example Project.',
        'searxng', NOW)
    return replace(item, **changes)


def verified(query=CM, **changes):
    item = source(query, accessibility='available', verified_at=NOW,
        evidence_kind='official_current_state' if query == CM else 'fetched_report',
        verified_url=CM_URL if query == CM else source(query).url,
        published_at=None if query == CM else NOW,
        entertainment_status='' if query == CM else 'reported_project')
    return replace(item, **changes)


class IntentTests(SimpleTestCase):
    def test_current_queries_and_stable_exceptions(self):
        for query in (CM, PM, ACTOR, 'Telugu movie releases this week', 'Hindi OTT releases',
                      'Tamil film releases today', 'Malayalam releases', 'Latest actor news',
                      'AI developments', 'current affairs'):
            with self.subTest(query=query):
                self.assertTrue(QueryRouter.requires_online(query))
        for query in ('Who was the Chief Minister of Telangana?', 'Who was Prime Minister of India in 2014?',
                      'Who is the Prime Minister of India in 2014?', 'Telugu movie releases in 2014',
                      'What is a prime minister?', 'Explain the role of a chief minister',
                      'What is electrical current?', 'What does latest mean?', 'Who is actor Prabhas?',
                      'According to my notes, current movie releases', 'Answer offline only: '+CM):
            with self.subTest(query=query):
                self.assertFalse(QueryRouter.requires_online(query))

    def test_official_page_snapshot_is_not_a_dated_news_article(self):
        self.assertTrue(select_current_evidence([verified()], CM, NOW))
        for item in (source(published_at=NOW), verified(accessibility='unavailable'),
                     verified(verified_at=NOW-timedelta(minutes=6)),
                     verified(verified_url='https://www.telangana.gov.in/news/old-appointment/'),
                     verified(verified_url='https://telangana.gov.in.evil.example/government/chief-minister/'),
                     source(published_at=NOW-timedelta(days=365), accessibility='available', verified_at=NOW)):
            self.assertEqual(select_current_evidence([item], CM, NOW), [])
        self.assertEqual(select_current_evidence([verified()], CM+' as of 2020-01-01', NOW), [])

    def test_dates_and_officeholder_conflicts_are_not_reconciled_by_recency(self):
        for value in ('2026/10/25', '25 Oct 2026 17:30:00 +0530', 'October 25, 2026 05:30 PM +0530'):
            self.assertEqual(parse_publication_date(value).date().isoformat(), '2026-10-25')
        self.assertTrue(has_conflicting_claims([verified(), verified(content='Chief Minister of Telangana is Other Person.')], CM))
        self.assertFalse(has_conflicting_claims([verified(), verified()], CM))

    def test_scoped_live_profile_layouts_and_ambiguous_rosters(self):
        text = 'Government\nSRI EXAMPLE PERSON\nThe Hon\u2019ble Chief Minister\nName Sri Example Person'
        claim, _ = page_evidence(text, CM_URL, CM)
        self.assertEqual(claim, 'Chief Minister of Telangana: SRI EXAMPLE PERSON.')
        self.assertEqual(page_evidence(text, 'https://publisher.example/old-story', CM), ('', ''))
        self.assertEqual(page_evidence(text+'\nSRI OTHER PERSON\nThe Hon\u2019ble Chief Minister', CM_URL, CM), ('', ''))
        intro = 'Know the PM\nShri Example Person was sworn-in as India\u2019s Prime Minister for the third time on 9th June 2024.'
        self.assertIn('Prime Minister of India: Shri Example Person.', page_evidence(intro, 'https://www.pmindia.gov.in/en/pms-profile/', PM)[0])
        self.assertEqual(page_evidence(intro, 'https://www.pmindia.gov.in/en/news/old-oath/', PM), ('', ''))

    def test_same_film_with_incompatible_release_dates_is_a_conflict(self):
        first = verified(ACTOR, content='Prabhas film Example Project will release on 2026-10-30.')
        other = replace(first, content='Prabhas film Example Project will release on 2026-11-01.')
        self.assertTrue(has_conflicting_claims([first, other], ACTOR))
        self.assertFalse(has_conflicting_claims([first, replace(other, content=other.content.replace('Example Project', 'Other Project'))], ACTOR))

    def test_entertainment_requires_fetched_relevant_claims_and_recent_publication(self):
        self.assertTrue(select_current_evidence([verified(ACTOR)], ACTOR, NOW))
        for item in (source(ACTOR, published_at=NOW), verified(ACTOR, published_at=None),
                     verified(ACTOR, published_at=NOW-timedelta(days=40)),
                     verified(ACTOR, published_at=NOW+timedelta(days=1)),
                     verified(ACTOR, date_conflict=True), verified(ACTOR, entertainment_status='')):
            self.assertEqual(select_current_evidence([item], ACTOR, NOW), [])

    def test_project_rumour_scheduled_and_completed_are_distinct(self):
        for sentence, status in (
            ('Prabhas will star in Example Project.', 'reported_project'),
            ('Prabhas officially announced Example Project.', 'reported_project'),
            ('Prabhas has not officially announced Example Project.', 'unconfirmed_or_denied'),
            ('Prabhas is reportedly in talks for Example Project.', 'rumour_or_unconfirmed'),
            ('Prabhas film Example Project will release on 2027-01-01.', 'reported_confirmed_or_scheduled'),
            ('Prabhas film Example Project was released on 2026-10-20.', 'reported_released')):
            claim, actual = page_evidence(sentence, source(ACTOR).url, ACTOR)
            self.assertEqual(claim, sentence)
            self.assertEqual(actual, status)
        self.assertEqual(page_evidence('Unrelated film will release tomorrow.', source(ACTOR).url, ACTOR), ('', ''))
        self.assertEqual(page_evidence('Prabhas Example Project 2026', source(ACTOR).url, ACTOR), ('', ''))


class AcquisitionTests(SimpleTestCase):
    def setUp(self):
        clear_online_cache(); e._CACHE.clear(); e._HOST_BACKOFF.clear()
        clock = patch('assistant.online.enrichment.datetime')
        clock.start().now.return_value = NOW
        self.addCleanup(clock.stop)
        self.addCleanup(e._CACHE.clear)

    @patch('assistant.online.enrichment.fetch_metadata')
    def test_undated_official_page_and_dated_entertainment_both_require_body_fetch(self, fetch):
        for query, item in ((CM, source()), (ACTOR, source(ACTOR, published_at=NOW))):
            fetch.return_value = (None, NOW, False, None, '', {'text': item.content, 'url': item.url})
            results = e.enrich_results([item], query, time.monotonic()+2)
            self.assertEqual(results[0].verified_at, NOW)
            self.assertEqual(results[0].published_at, item.published_at)
            self.assertTrue(select_current_evidence(results, query, NOW))
            self.assertEqual(results[0].modified_at, NOW)
        self.assertEqual(fetch.call_count, 2)

    @patch('assistant.online.enrichment.fetch_metadata')
    def test_regional_releases_require_report_text_not_search_titles(self, fetch):
        for language in ('Telugu', 'Hindi', 'Tamil', 'Malayalam'):
            query = language+' movie releases this week'
            item = source(ACTOR, title=language+' releases', content=language+' movie release calendar and upcoming films.', url='https://publisher.example/'+language)
            text = language+' film Example Movie will release on '+NOW.date().isoformat()+'.'
            fetch.return_value = (NOW, None, False, None, '', {'text': text, 'url': item.url})
            admitted = select_current_evidence(e.enrich_results([item], query, time.monotonic()+2), query, NOW)
            self.assertEqual(admitted[0].content, text)

    @patch('assistant.online.enrichment.fetch_metadata')
    def test_search_page_date_conflict_and_changed_redirect_authority_abstain(self, fetch):
        for query, item, metadata in (
            (CM, source(), (None, None, False, None, '', {'text': source().content, 'url': 'https://publisher.example/claim'})),
            (ACTOR, source(ACTOR, published_at=NOW), (NOW-timedelta(days=2), None, False, None, '', {'text': source(ACTOR).content, 'url': source(ACTOR).url}))):
            e._CACHE.clear()
            fetch.return_value = metadata
            self.assertEqual(select_current_evidence(e.enrich_results([item], query, time.monotonic()+2), query, NOW), [])

    @patch('assistant.online.enrichment.fetch_metadata', side_effect=TimeoutError())
    def test_timeout_is_negative_cached_and_per_page_deadline_is_bounded(self, fetch):
        started = time.monotonic()
        for _ in range(2):
            self.assertEqual(e.enrich_results([source()], CM, started+2)[0].accessibility, 'unavailable')
        self.assertEqual(fetch.call_count, 1)
        self.assertLessEqual(fetch.call_args.args[1], started+1.3)

    @patch('assistant.online.enrichment.public_target')
    @patch('assistant.online.enrichment.socket.create_connection')
    @patch('assistant.online.enrichment.http.client.HTTPConnection')
    def test_visible_body_dates_and_jsonld_event_remain_separate(self, connection, connect, target):
        url = source(ACTOR).url
        target.return_value = (urlsplit(url), 443, '93.184.216.34')
        # Avoid TLS in this transport fixture; security transport tests cover it.
        with patch('assistant.online.enrichment.ssl.create_default_context'):
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.getheader.side_effect = lambda key, default='': 'text/html' if key == 'Content-Type' else default
            html = ('<head><title>Prabhas secret confirmed release</title></head><nav>Prabhas was released tomorrow.</nav>'
                    '<meta name="parsely-pub-date" content="'+NOW.isoformat()+'">'
                    '<script type="application/ld+json">'+json.dumps({'@type':'ScreeningEvent','startDate':'2027-01-01'})+'</script>'
                    '<script>Prabhas will release a secret film.</script><p>'+source(ACTOR).content+'</p>')
            response.read1.side_effect = [html.encode(), b'']
            fields = e.fetch_metadata(url, time.monotonic()+2)
        self.assertEqual(fields[0], NOW)
        self.assertEqual(fields[5]['event_at'].year, 2027)
        self.assertNotIn('secret', fields[5]['text'])
        self.assertNotIn('tomorrow', fields[5]['text'])

    @patch('assistant.online.enrichment.fetch_metadata')
    @patch.object(SearXNGOnlineRetriever, '_search')
    def test_direct_official_verification_and_valid_cache_avoid_search(self, search, fetch):
        search.return_value = [source(url='https://publisher.example/old-news')]
        fetch.return_value = (None, None, False, None, '', {'text': source().content, 'url': CM_URL})
        provider = SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888', timeout_seconds=8, max_results=4)
        first = provider.retrieve(CM)
        self.assertTrue(select_current_evidence(first, CM, NOW))
        self.assertEqual(provider.diagnostics['search_ms'], 0)
        self.assertEqual(provider.diagnostics['fallback_ms'], 0)
        first_calls = fetch.call_count
        cached = provider.retrieve(CM)
        self.assertEqual(cached[0].verified_at, first[0].verified_at)
        self.assertTrue(provider.diagnostics['cache_hit'])
        self.assertEqual(provider.diagnostics['search_ms'], 0)
        self.assertEqual(provider.diagnostics['enrichment_ms'], 0)
        self.assertEqual(fetch.call_count, first_calls)
        self.assertEqual(provider.diagnostics['direct_fetch_ms'], 0)
        search.assert_not_called()

    @patch('assistant.online.enrichment.fetch_metadata')
    @patch.object(SearXNGOnlineRetriever, '_search')
    def test_pm_direct_profile_and_search_health_are_independent(self, search, fetch):
        from assistant.online import searxng as s
        provider = SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888', timeout_seconds=2, max_results=4)
        s._HEALTH[provider.base_url] = (time.monotonic()+30, False)
        url = known_office_profile(PM)
        fetch.return_value = (None, None, False, None, '', {'text': 'Know the PM\nShri Example Person was sworn-in as India’s Prime Minister for a new term.', 'url': url})
        self.assertEqual(provider.retrieve(PM)[0].evidence_kind, 'official_current_state')
        self.assertEqual(fetch.call_args.args[0], url)
        search.assert_not_called()
        self.assertFalse(s._HEALTH[provider.base_url][1])

    @patch('assistant.online.enrichment.fetch_metadata')
    @patch.object(SearXNGOnlineRetriever, '_search')
    def test_failed_direct_fetch_uses_bounded_search_and_other_scoped_profile(self, search, fetch):
        alternate = 'https://www.telangana.gov.in/chief-minister'
        clock = [100.0]
        def page(url, deadline):
            self.assertLessEqual(deadline, 100.8)
            clock[0] += .02
            if url == CM_URL:
                raise TimeoutError()
            return (None, None, False, None, '', {'text': source().content, 'url': alternate})
        def discover(query, deadline):
            self.assertEqual(deadline, 100.8)
            self.assertEqual(fetch.call_count, 1)
            clock[0] += .03
            return [source(url=alternate)]
        fetch.side_effect = page
        search.side_effect = discover
        with patch('assistant.online.searxng.time.monotonic', side_effect=lambda: clock[0]):
            provider = SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888', timeout_seconds=.8, max_results=4)
            fresh = select_current_evidence(provider.retrieve(CM), CM, NOW)
        self.assertEqual(fresh[0].verified_url, alternate)
        self.assertEqual(provider.diagnostics['direct_fetch_ms'], 20)
        self.assertEqual(provider.diagnostics['search_ms'], 30)
        self.assertEqual(provider.diagnostics['fallback_ms'], 50)

    @patch('assistant.online.enrichment.fetch_metadata', side_effect=TimeoutError())
    @patch.object(SearXNGOnlineRetriever, '_search')
    def test_failed_verification_cannot_promote_old_news_or_search_snippets(self, search, fetch):
        search.return_value = [source(url='https://publisher.example/appointment', published_at=NOW-timedelta(days=365))]
        provider = SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888', timeout_seconds=2, max_results=4)
        self.assertEqual(select_current_evidence(provider.retrieve(CM), CM, NOW), [])
        self.assertTrue(search.called)
        self.assertEqual(fetch.call_count, 1)  # Negative cache reused, never bypassed.

    @patch('assistant.online.enrichment.fetch_metadata')
    @patch.object(SearXNGOnlineRetriever, '_search')
    def test_fallback_can_verify_after_slow_discovery_with_remaining_total_budget(self, search, fetch):
        alternate = 'https://www.telangana.gov.in/chief-minister'
        clock = [100.0]
        def page(url, deadline):
            self.assertLessEqual(deadline, 104.0)
            clock[0] += .1
            if url == CM_URL:
                raise TimeoutError()
            return (None, None, False, None, '', {'text': source().content, 'url': alternate})
        def discover(query, deadline):
            self.assertEqual(deadline, 104.0)
            clock[0] += 2.2
            return [source(url=alternate)]
        fetch.side_effect = page
        search.side_effect = discover
        with patch('assistant.online.searxng.time.monotonic', side_effect=lambda: clock[0]):
            provider = SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888', timeout_seconds=4, max_results=4)
            self.assertTrue(select_current_evidence(provider.retrieve(CM), CM, NOW))
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(provider.diagnostics['direct_fetch_ms'], 100)
        self.assertEqual(provider.diagnostics['fallback_ms'], 2300)

    @patch('assistant.online.enrichment.fetch_metadata')
    @patch.object(SearXNGOnlineRetriever, '_search')
    def test_direct_redirect_authority_and_ambiguous_roster_still_abstain(self, search, fetch):
        search.return_value = [source(url='https://publisher.example/old-article')]
        for text, final_url in (
            (source().content, 'https://publisher.example/claim'),
            ('SRI EXAMPLE PERSON\nThe Hon’ble Chief Minister\nSRI OTHER PERSON\nThe Hon’ble Chief Minister', CM_URL)):
            clear_online_cache(); e._CACHE.clear()
            fetch.return_value = (None, None, False, None, '', {'text': text, 'url': final_url})
            provider = SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888', timeout_seconds=2, max_results=4)
            self.assertEqual(select_current_evidence(provider.retrieve(CM), CM, NOW), [])

    @patch('assistant.online.enrichment.fetch_metadata')
    @patch.object(SearXNGOnlineRetriever, '_search')
    def test_direct_timeout_exhausts_shared_deadline_without_fallback(self, search, fetch):
        clock = [100.0]
        def timeout(url, deadline):
            self.assertEqual(deadline, 100.05)
            clock[0] = deadline
            raise TimeoutError()
        fetch.side_effect = timeout
        with patch('assistant.online.searxng.time.monotonic', side_effect=lambda: clock[0]):
            provider = SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888', timeout_seconds=.05, max_results=4)
            with self.assertRaises(OnlineRetrievalTimeoutError):
                provider.retrieve(CM)
        search.assert_not_called()
        self.assertEqual(provider.diagnostics['direct_fetch_ms'], 50)
        self.assertEqual(provider.diagnostics['fallback_ms'], 0)

    @patch('assistant.online.enrichment.fetch_metadata', side_effect=TimeoutError())
    @patch.object(SearXNGOnlineRetriever, '_search')
    def test_fallback_cannot_restart_request_deadline(self, search, fetch):
        clock = [100.0]
        def late_search(query, deadline):
            self.assertEqual(deadline, 100.5)
            clock[0] = deadline
            return [source()]
        search.side_effect = late_search
        with patch('assistant.online.searxng.time.monotonic', side_effect=lambda: clock[0]):
            provider = SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888', timeout_seconds=.5, max_results=4)
            with self.assertRaises(OnlineRetrievalTimeoutError):
                provider.retrieve(CM)
        search.assert_called_once()
        self.assertEqual(provider.diagnostics['fallback_ms'], 500)

    @patch('assistant.online.enrichment.fetch_metadata')
    @patch.object(SearXNGOnlineRetriever, '_search')
    def test_direct_conflicting_claims_survive_max_results_one_and_are_not_cached(self, search, fetch):
        from assistant.online import searxng as s
        fetch.return_value = (None, None, False, None, '', {'text': source().content+'\nChief Minister of Telangana is Other Person.', 'url': CM_URL})
        provider = SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888', timeout_seconds=2, max_results=1)
        self.assertTrue(has_conflicting_claims(provider.retrieve(CM), CM))
        self.assertFalse(s._CACHE)
        search.assert_not_called()

    @patch('assistant.online.enrichment.enrich_results')
    @patch.object(SearXNGOnlineRetriever, '_search')
    def test_fallback_conflicting_sources_survive_truncation_and_are_not_cached(self, search, enrich):
        from assistant.online import searxng as s
        enrich.side_effect = [[source(accessibility='unavailable')],
                              [verified(), verified(content='Chief Minister of Telangana is Other Person.')]]
        search.return_value = [source()]
        provider = SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888', timeout_seconds=2, max_results=1)
        self.assertEqual(len(provider.retrieve(CM)), 2)
        self.assertFalse(s._CACHE)

    @patch('assistant.online.enrichment.fetch_metadata')
    @patch.object(SearXNGOnlineRetriever, '_search')
    def test_stale_process_snapshot_is_reverified_without_refreshing_on_cache_hit(self, search, fetch):
        from assistant.online import searxng as s
        fetch.return_value = (None, None, False, None, '', {'text': source().content, 'url': CM_URL})
        provider = SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888', timeout_seconds=2, max_results=4)
        provider.retrieve(CM)
        key, value = next(iter(s._CACHE.items()))
        s._CACHE[key] = (value[0], (replace(value[1][0], verified_at=NOW-timedelta(minutes=6)),), value[2])
        e._CACHE.clear()
        self.assertEqual(provider.retrieve(CM)[0].verified_at, NOW)
        self.assertFalse(provider.diagnostics['cache_hit'])
        self.assertEqual(fetch.call_count, 2)
        search.assert_not_called()

    @patch('assistant.online.enrichment.fetch_metadata')
    @patch.object(SearXNGOnlineRetriever, '_search')
    def test_unsupported_offices_and_historical_queries_do_not_get_direct_adapter(self, search, fetch):
        for query in ('Who is the Governor of Telangana?', 'Who is the President of India?',
                      'Who is the Chief Minister of Karnataka?', 'Who was the Chief Minister of Telangana?'):
            self.assertEqual(known_office_profile(query), '')
        search.return_value = [source()]
        provider = SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888', timeout_seconds=2, max_results=4)
        query = 'Who is the Governor of Telangana?'
        self.assertEqual(select_current_evidence(provider.retrieve(query), query, NOW), [])
        self.assertEqual(provider.diagnostics['direct_fetch_ms'], 0)
        fetch.assert_not_called()

    @patch('assistant.online.enrichment.fetch_metadata')
    def test_catalog_body_extension_preserves_existing_release_evidence(self, fetch):
        url = 'https://www.raspberrypi.com/software/operating-systems/'
        item = source(ACTOR, url=url, title='Raspberry Pi OS', content='Official Raspberry Pi OS download catalog.')
        fetch.return_value = (None, None, False, NOW-timedelta(days=20),
            'Official Raspberry Pi OS catalog release: Debian version 13.',
            {'text': 'Raspberry Pi OS', 'url': url})
        results = e.enrich_results([item], 'latest Raspberry Pi OS version', time.monotonic()+2)
        self.assertTrue(select_current_evidence(results, 'latest Raspberry Pi OS version', NOW))
        self.assertEqual(results[0].evidence_kind, 'official_release_catalog')


@override_settings(AI_ENGINE='local', ONLINE_RETRIEVAL_ENABLED=True, RAG_ENABLED=True,
                   ONLINE_KNOWLEDGE_SYNC_ENABLED=True, ONLINE_KNOWLEDGE_AUTO_PERSIST=True)
class CurrentServiceTests(TestCase):
    def setUp(self):
        install_resource_report(self, online_allowed=True)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        media = override_settings(MEDIA_ROOT=folder.name)
        media.enable(); self.addCleanup(media.disable)
        clock = patch('assistant.services.django_timezone.now', return_value=NOW)
        clock.start(); self.addCleanup(clock.stop)
        self.engine_patch = patch('assistant.services.get_engine')
        self.engine = self.engine_patch.start().return_value
        self.addCleanup(self.engine_patch.stop)
        answer = AIEngineResult(text='Source-supported example.', engine='local')
        self.engine.generate.return_value = answer
        self.engine.generate_stream.side_effect = lambda *a, **kw: iter([
            AIEngineStreamEvent(type='delta', text=answer.text), AIEngineStreamEvent(type='done', result=answer)])

    def ask(self, query, stream):
        return list(AssistantService.process_message_stream(query))[-1] if stream else AssistantService.process_message(query)[2]

    @patch('assistant.services.get_online_retriever')
    def test_both_transports_verify_persist_and_reuse_without_http_or_expiry_extension(self, provider):
        provider.return_value.retrieve.return_value = [verified()]
        provider.return_value.diagnostics = {'search_ms': 4, 'enrichment_ms': 5, 'evidence_selection_ms': 1,
                                             'direct_fetch_ms': 22, 'fallback_ms': 33}
        first = self.ask(CM, False)
        self.assertTrue(first['online_used'])
        self.assertEqual(first['enrichment_ms'], 5)
        self.assertEqual(first['direct_fetch_ms'], 22)
        self.assertEqual(first['fallback_ms'], 33)
        doc = Document.objects.get(source_type='ONLINE')
        self.assertEqual(doc.last_synced_at, NOW)
        self.assertEqual(doc.expires_at, NOW+timedelta(minutes=5))
        for stream in (False, True):
            cached = self.ask(CM, stream)
            self.assertTrue(cached['cache_hit'])
            self.assertFalse(cached['online_attempted'])
            self.assertEqual(cached['online_sources'][0]['verified_at'], NOW.isoformat())
            self.assertEqual(cached['search_ms'], 0)
            self.assertEqual(cached['direct_fetch_ms'], 0)
            self.assertEqual(cached['fallback_ms'], 0)
        provider.return_value.retrieve.assert_called_once()
        doc.refresh_from_db()
        self.assertEqual(doc.last_synced_at, NOW)
        self.assertEqual(doc.expires_at, NOW+timedelta(minutes=5))

    @patch('assistant.services.get_online_retriever')
    def test_offline_cache_is_dated_and_expired_cache_abstains_without_network(self, provider):
        OnlineKnowledgeSyncService().persist_results(CM, [verified()])
        for stream in (False, True):
            metadata = self.ask('Answer offline only: '+CM, stream)
            self.assertEqual(metadata['fallback_reason'], 'offline_verified_cache')
            self.assertFalse(metadata['online_attempted'])
        answer = AssistantService.process_message('Answer offline only: '+CM)[1]
        self.assertIn('not a live check', answer)
        self.assertIn(NOW.isoformat(), answer)
        Document.objects.update(expires_at=NOW-timedelta(seconds=1))
        self.assertEqual(load_verified_current(CM), [])
        for stream in (False, True):
            self.assertEqual(self.ask('Answer offline only: '+CM, stream)['fallback_reason'], 'offline_requested')
        provider.assert_not_called()
        self.engine.generate.assert_not_called()
        self.engine.generate_stream.assert_not_called()

    @patch('assistant.services.get_online_retriever')
    def test_no_evidence_and_conflicts_abstain_without_generation_or_persistence(self, provider):
        for items, reason in (([source(published_at=NOW)], 'no_relevant_current_results'),
                              ([verified(), verified(content='Chief Minister of Telangana is Other Person.')], 'conflicting_evidence')):
            provider.return_value.retrieve.return_value = items
            for stream in (False, True):
                self.assertEqual(self.ask(CM, stream)['fallback_reason'], reason)
        self.assertFalse(Document.objects.exists())
        self.engine.generate.assert_not_called()
        self.engine.generate_stream.assert_not_called()

    def test_sync_rejects_unverified_current_claim_and_manual_lexical_stays_one_query(self):
        self.assertEqual(OnlineKnowledgeSyncService().persist_results(CM, [source(published_at=NOW)]).created, 0)
        doc = Document.objects.create(filename='Pi reference', status='INDEXED', source_identifier='manual:pi')
        KnowledgeChunk.objects.create(document=doc, chunk_index=0, content='Raspberry Pi 5 uses BCM2712.')
        with self.assertNumQueries(1):
            self.assertTrue(LexicalRetriever().retrieve('Raspberry Pi BCM2712'))

    @patch('assistant.services.load_verified_current', side_effect=OSError('private cache path'))
    @patch('assistant.services.get_online_retriever')
    def test_cache_failure_does_not_block_real_provider_attempt(self, provider, cache):
        provider.return_value.retrieve.return_value = [verified()]
        metadata = self.ask(CM, False)
        self.assertTrue(metadata['online_attempted'])
        self.assertTrue(metadata['online_used'])
        provider.return_value.retrieve.assert_called_once()

    def test_mutated_or_expired_provenance_cannot_refresh_cached_claims(self):
        OnlineKnowledgeSyncService().persist_results(CM, [verified()])
        doc = Document.objects.get(source_type='ONLINE')
        with doc.file.open('wb') as saved:
            saved.write(b'Unverified replacement')
        self.assertEqual(load_verified_current(CM), [])

    def test_general_current_ai_report_uses_same_verified_cache_and_expiry(self):
        query = 'Latest AI developments'
        item = verified(ACTOR, title='AI development report', content='AI developments include a new publicly announced model evaluation.',
                        evidence_kind='fetched_reference', entertainment_status='')
        self.assertEqual(OnlineKnowledgeSyncService().persist_results(query, [item]).created, 1)
        cached = load_verified_current(query)
        self.assertEqual(cached[0].published_at, NOW)
        self.assertEqual(cached[0].verified_at, NOW)
        Document.objects.update(expires_at=NOW)
        self.assertEqual(load_verified_current(query), [])
