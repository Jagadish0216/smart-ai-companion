"""Synthetic publisher markup; mocked transport, never live-compatibility evidence."""
import io
import json
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.parse import urlsplit

from django.test import SimpleTestCase, TestCase, override_settings

from assistant.online import enrichment as e
from assistant.online.base import OnlineRetrievalResult
from assistant.online.facts import source_supported_answer
from assistant.online.grounding import select_current_evidence, build_online_grounding_instruction, has_conflicting_claims
from assistant.online.partial_html import extract_partial_article, PartialEvidenceError
from assistant.online.selection import candidate_kind, rank_candidates
from assistant.online.searxng import clear_online_cache, SearXNGOnlineRetriever
from knowledge_base.models import Document
from knowledge_base.online_sync import OnlineKnowledgeSyncService, load_verified_current

NOW = datetime.now(timezone.utc)
URL = 'https://publisher.example/news/telugu-release-report'
QUERY = 'Latest Telugu movie releases'
CLAIM = 'Telugu film Example Movie will release on '+NOW.date().isoformat()+'.'
TEMPLATE = (Path(__file__).parent/'fixtures/bounded_article.html').read_text()


def html(**changes):
    values = {'URL': URL, 'PUBLISHED': NOW.isoformat(),
              'SCHEMA': json.dumps({'@type': 'NewsArticle', 'url': URL, 'datePublished': NOW.isoformat()}),
              'NAV': '', 'CLAIM': CLAIM, 'EXTRA': '',
              'TAIL': '<div class="advertisement">'+('advertising ' * 40000)}
    values.update(changes)
    document = TEMPLATE
    for name, value in values.items():
        document = document.replace('__'+name+'__', value)
    return document.encode()


def candidate(url=URL, **changes):
    return replace(OnlineRetrievalResult('Telugu movie release reporting', url, CLAIM, 'searxng', NOW), **changes)


class TransportTests(SimpleTestCase):
    def setUp(self):
        clear_online_cache(); e._CACHE.clear(); e._HOST_BACKOFF.clear()
        self.patchers = [patch('assistant.online.enrichment.public_target'),
                         patch('assistant.online.enrichment.socket.create_connection'),
                         patch('assistant.online.enrichment.http.client.HTTPConnection'),
                         patch('assistant.online.enrichment.ssl.create_default_context')]
        self.target, self.connect, self.connection, self.tls = [p.start() for p in self.patchers]
        for p in self.patchers:
            self.addCleanup(p.stop)
        self.target.return_value = (urlsplit(URL), 443, '93.184.216.34')
        self.response = self.connection.return_value.getresponse.return_value
        self.response.status = 200
        self.response.getheader.side_effect = lambda name, default='': {
            'Content-Type': 'text/html', 'Content-Encoding': 'identity'}.get(name, default)

    def serve(self, body):
        self.stream = io.BytesIO(body)
        self.response.read1.side_effect = lambda size: self.stream.read(size)

    def fetch(self, body):
        self.serve(body)
        return e.fetch_metadata(URL, time.monotonic()+2)

    def test_oversized_response_accepts_only_closed_page_bound_article_at_unchanged_byte_cap(self):
        with self.assertLogs('assistant.online.enrichment', level='INFO') as captured:
            fields = self.fetch(html())
        self.assertTrue(fields[5]['partial_response'])
        self.assertEqual(fields[5]['partial_scope'], 'closed_article')
        self.assertEqual(fields[0], NOW)
        self.assertIn(CLAIM, fields[5]['text'])
        self.assertNotIn('advertising', fields[5]['text'])
        self.assertEqual(self.stream.tell(), e.MAX_BYTES+1)
        self.assertEqual(fields[5]['bytes_read'], e.MAX_BYTES+1)
        self.assertEqual(e.MAX_BYTES, 262144)
        event = captured.records[-1].article_fetch
        self.assertEqual(event['outcome'], 'partial')
        self.assertNotIn(URL, repr(event))
        self.assertNotIn(CLAIM, repr(event))
        self.tls.return_value.wrap_socket.assert_called_once_with(self.connect.return_value, server_hostname='publisher.example')
        self.connection.return_value.close.assert_called_once()

    def test_complete_html_remains_complete_and_does_not_require_new_partial_metadata(self):
        fields = self.fetch(b'<html><head><meta property="article:published_time" content="'+NOW.isoformat().encode()+b'"></head><body><p>'+CLAIM.encode()+b'</p></body></html>')
        self.assertFalse(fields[5]['partial_response'])
        self.assertEqual(fields[0], NOW)

    def test_truncated_article_paragraph_head_and_missing_publication_all_reject(self):
        variants = [html().replace(b'</article>', b''),
                    html().replace(b'</p>', b''),
                    html().replace(b'</head>', b''),
                    html(PUBLISHED='', SCHEMA=json.dumps({'@type': 'Article', 'url': URL})),
                    html(SCHEMA='{"@type":"Article",', PUBLISHED=NOW.isoformat()),
                    html(CLAIM=''), html(EXTRA='<article><p>'+CLAIM+'</p></article>')]
        for body in variants:
            with self.subTest(prefix=body[:20]), self.assertRaises(PartialEvidenceError):
                self.fetch(body)

    def test_date_conflicts_wrong_canonical_and_other_article_schema_reject(self):
        older = (NOW-timedelta(days=1)).isoformat()
        for body in (html(PUBLISHED=older), html(EXTRA='<p>Published: '+(NOW-timedelta(days=1)).date().isoformat()+'</p>'),
                     html(TAIL='<meta property="article:published_time" content="'+older+'">'+('x'*e.MAX_BYTES)),
                     html(URL='https://publisher.example/news/other'),
                     html(SCHEMA=json.dumps({'@type': 'NewsArticle', 'url': URL+'?other=1', 'datePublished': NOW.isoformat()})),
                     html(SCHEMA=json.dumps({'@type': 'NewsArticle', 'url': URL+'-other', 'datePublished': NOW.isoformat()})),
                     html(SCHEMA=json.dumps([{'@type': 'Article', 'url': URL, 'datePublished': NOW.isoformat()}]*2))):
            with self.assertRaises(PartialEvidenceError):
                self.fetch(body)

    def test_large_navigation_and_ads_never_supply_claim_text(self):
        fields = self.fetch(html(NAV='unrelated navigation '*5000,
                                 EXTRA='<aside><p>Other Movie will release on 2020-01-01.</p></aside>'
                                       '<div class="advertisement">'+('unused ' * 15000)+'</div>'))
        self.assertIn(CLAIM, fields[5]['text'])
        self.assertNotIn('Other Movie', fields[5]['text'])
        self.assertNotIn('unused', fields[5]['text'])
        with self.assertRaises(PartialEvidenceError):
            self.fetch(html(NAV='navigation '*30000))  # Article lies beyond the cap.

    def test_parser_depth_text_json_and_token_limits_are_finite(self):
        for body in (html(EXTRA='<div>'*70), html(EXTRA='<p>'+'x'*13000+'</p>'),
                     html(SCHEMA=json.dumps({'padding': 'x'*33000})),
                     html(EXTRA='<br>'*8100)):
            with self.assertRaises(PartialEvidenceError):
                self.fetch(body)

    def test_challenges_compression_403_and_429_are_not_bypassed(self):
        for code in (403, 429):
            self.response.status = code
            self.serve(html())
            self.response.read1.reset_mock()
            with self.assertRaises(e.PageUnavailable):
                e.fetch_metadata(URL, time.monotonic()+2)
            self.response.read1.assert_not_called()
        self.response.status = 200
        for body in (html().replace(b'<head>', b'<head><title>Just a moment</title>'),
                     html(EXTRA='<p>Subscribe to continue reading this article.</p>'),
                     html(EXTRA='<meta http-equiv="refresh" content="0;url=/login">')):
            with self.assertRaises(ValueError):
                self.fetch(body)
        self.response.getheader.side_effect = lambda name, default='': {'Content-Type': 'text/html', 'Content-Encoding': 'gzip'}.get(name, default)
        with self.assertRaisesRegex(ValueError, 'Compressed'):
            self.fetch(html())

    def test_genuine_timeout_does_not_salvage_even_a_closed_article_prefix(self):
        data = html()
        self.serve(data)
        def read(size):
            if self.stream.tell() >= 8192:
                raise TimeoutError('private socket details')
            return self.stream.read(size)
        self.response.read1.side_effect = read
        with self.assertLogs('assistant.online.enrichment', level='INFO') as captured:
            with self.assertRaises(TimeoutError):
                e.fetch_metadata(URL, time.monotonic()+2)
        self.assertEqual(captured.records[-1].article_fetch['reason'], 'timeout')
        self.assertNotIn('private socket details', repr(captured.records[-1].article_fetch))

    def test_early_eof_with_declared_bytes_remaining_is_not_a_complete_response(self):
        self.response.getheader.side_effect = lambda name, default='': {
            'Content-Type': 'text/html', 'Content-Length': '800000'}.get(name, default)
        with self.assertRaisesRegex(ValueError, 'Incomplete article response'):
            self.fetch(html(TAIL=''))

    def test_unsafe_redirects_and_partial_cross_page_attribution_reject(self):
        self.response.status = 302
        self.response.getheader.side_effect = lambda name, default='': 'http://publisher.example/article' if name == 'Location' else default
        with self.assertRaisesRegex(ValueError, 'Unsafe article redirect'):
            e.fetch_metadata(URL, time.monotonic()+2)
        self.response.getheader.side_effect = lambda name, default='': 'https://127.0.0.1/private' if name == 'Location' else default
        self.target.side_effect = [(urlsplit(URL), 443, '93.184.216.34'), ValueError('Unsafe article address')]
        self.connect.reset_mock()
        with self.assertRaisesRegex(ValueError, 'Unsafe article address'):
            e.fetch_metadata(URL, time.monotonic()+2)
        self.connect.assert_called_once()

    def test_partial_parsing_obeys_the_original_fetch_deadline(self):
        clock = [100.0]
        from assistant.online.partial_html import _ArticleIsland
        original = _ArticleIsland.feed
        def slow(parser, value):
            original(parser, value)
            clock[0] = 100.1
        with patch('assistant.online.partial_html.time.monotonic', side_effect=lambda: clock[0]), patch.object(_ArticleIsland, 'feed', slow):
            with self.assertRaises(TimeoutError):
                extract_partial_article(html(TAIL='')[:8192], URL, 100.05)

    def test_real_pipeline_marks_partial_and_never_replaces_missing_dates_with_search_dates(self):
        self.serve(html())
        with patch('assistant.online.enrichment.datetime') as clock:
            clock.now.return_value = NOW
            results = e.enrich_results([candidate()], QUERY, time.monotonic()+2)
        self.assertTrue(select_current_evidence(results, QUERY, NOW))
        self.assertTrue(results[0].partial_response)
        instruction, _ = build_online_grounding_instruction(results, query=QUERY, request_time=NOW, max_context_chars=3600)
        self.assertIn('Unread document content is unknown', instruction)
        self.assertIsNone(source_supported_answer('When will Example Movie release?', results, NOW))
        e._CACHE.clear()
        self.serve(html(PUBLISHED='', SCHEMA=json.dumps({'@type': 'Article', 'url': URL})))
        results = e.enrich_results([candidate(published_at=NOW)], QUERY, time.monotonic()+2)
        self.assertEqual(select_current_evidence(results, QUERY, NOW), [])
        e._CACHE.clear()
        self.serve(html(EXTRA='<p>Telugu film Example Movie will release on '+(NOW+timedelta(days=1)).date().isoformat()+'.</p>'))
        diagnostics = {}
        results = e.enrich_results([candidate()], QUERY, time.monotonic()+2, diagnostics=diagnostics)
        self.assertEqual(select_current_evidence(results, QUERY, NOW), [])
        self.assertEqual(diagnostics['fetch_failures']['partial_conflicting_claims'], 1)


class PrioritizationTests(SimpleTestCase):
    def setUp(self):
        clear_online_cache(); e._CACHE.clear(); e._HOST_BACKOFF.clear()

    def candidates(self):
        return [candidate('https://en.wikipedia.org/wiki/Example_film'),
                candidate('https://www.quora.com/What-is-the-next-film'),
                candidate('https://www.facebook.com/example/posts/123'),
                candidate('https://publisher.example/movies', title='Telugu movie listings'),
                candidate('https://publisher.example/prabhas-biography', title='Telugu actor biography'),
                candidate('https://news.example/reports/telugu-release', published_at=NOW)]

    def test_current_reports_outrank_reference_social_qa_biography_and_listing(self):
        sources = self.candidates()
        self.assertEqual(rank_candidates(sources, QUERY)[0], sources[-1])
        self.assertEqual(candidate_kind(sources[0], QUERY)[0], 'reference')
        self.assertEqual(candidate_kind(sources[1], QUERY)[0], 'qa_or_forum')
        self.assertEqual(candidate_kind(sources[2], QUERY)[0], 'social_feed')
        # Source classes are not a blanket exclusion on timeless reference work.
        self.assertEqual(candidate_kind(sources[0], 'Explain Example film cinematography'), ('neutral', 1))
        self.assertEqual(len(rank_candidates(sources, 'Explain film cinematography')), len(sources))

    @patch('assistant.online.enrichment.fetch_metadata')
    def test_enrichment_defers_low_value_candidates_and_records_fixed_diagnostics(self, fetch):
        sources = self.candidates()
        fetch.return_value = (NOW, None, False, None, '', {'text': CLAIM, 'url': sources[-1].url})
        diagnostics = {}
        with self.assertLogs('assistant.online.enrichment', level='INFO') as records:
            results = e.enrich_results(sources, QUERY, time.monotonic()+2, diagnostics=diagnostics)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0], sources[-1].url)
        self.assertEqual(diagnostics['candidate_decisions']['deferred_low_value'], 5)
        self.assertEqual(diagnostics['fetch_candidates'], {'dated_report': 1})
        self.assertTrue(select_current_evidence(results, QUERY, datetime.now(timezone.utc)))
        self.assertNotIn('https://', repr(records.records[-1].article_candidates))

    @patch('assistant.online.enrichment.fetch_metadata', side_effect=e.PageUnavailable(429))
    def test_blocked_publisher_backoff_and_failure_diagnostics_preserved(self, fetch):
        diagnostics = {}
        e.enrich_results([candidate(), candidate(URL+'-second')], QUERY, time.monotonic()+2, diagnostics=diagnostics)
        fetch.assert_called_once()
        self.assertEqual(diagnostics['fetch_failures']['http_429'], 1)
        self.assertIn('publisher.example', e._HOST_BACKOFF)

    @patch.object(SearXNGOnlineRetriever, '_search')
    @patch('assistant.online.enrichment.fetch_metadata', side_effect=ValueError('private URL and user details'))
    def test_retriever_exposes_internal_counters_without_sensitive_failure_text(self, fetch, search):
        search.return_value = [candidate()]
        instance = SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888', timeout_seconds=4, max_results=4)
        instance.retrieve(QUERY)
        values = instance.diagnostics['article_diagnostics']
        self.assertGreaterEqual(values['fetch_failures']['invalid_page'], 1)
        self.assertNotIn('private URL', repr(values))
        self.assertLessEqual(search.call_count, 2)
        fetch.assert_called_once()


@override_settings(ONLINE_KNOWLEDGE_SYNC_ENABLED=True, ONLINE_KNOWLEDGE_AUTO_PERSIST=True)
class PartialPersistenceTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        media = override_settings(MEDIA_ROOT=folder.name); media.enable(); self.addCleanup(media.disable)

    def test_partial_provenance_and_actual_verification_time_survive_sqlite_cache(self):
        item = candidate(accessibility='available', evidence_kind='fetched_report',
                         entertainment_status='reported_confirmed_or_scheduled', claim_status='reported_scheduled',
                         published_at=NOW, publication_source='article_metadata', verified_at=NOW,
                         verified_url=URL, partial_response=True)
        self.assertEqual(OnlineKnowledgeSyncService().persist_results(QUERY, [item]).created, 1)
        loaded = load_verified_current(QUERY)
        self.assertTrue(loaded[0].partial_response)
        self.assertEqual(loaded[0].verified_at, NOW)
        doc = Document.objects.get(source_type='ONLINE')
        self.assertEqual(doc.expires_at, NOW+timedelta(minutes=5))
        # Partial provenance cannot promote a missing body-publication date.
        for bad in (replace(item, published_at=None), replace(item, publication_source='searxng')):
            self.assertEqual(select_current_evidence([bad], QUERY, NOW), [])
        conflict = replace(item, content=CLAIM.replace(NOW.date().isoformat(), (NOW+timedelta(days=1)).date().isoformat()))
        self.assertTrue(has_conflicting_claims([item, conflict], QUERY))

    @patch('assistant.online.enrichment.fetch_metadata')
    def test_cached_partial_metadata_cannot_verify_a_current_office_profile(self, fetch):
        e._CACHE.clear(); e._HOST_BACKOFF.clear()
        url = 'https://www.telangana.gov.in/government/chief-minister/'
        e._CACHE[url.rstrip('/')] = (time.monotonic()+60, (
            NOW, None, False, 'available', None, '', NOW, None,
            {'text': 'Chief Minister of Telangana is Example Person.', 'url': url,
             'partial_response': True, 'partial_scope': 'closed_article'}))
        values = e.enrich_results([candidate(url)], 'Who is the Chief Minister of Telangana?', time.monotonic()+2)
        self.assertEqual(select_current_evidence(values, 'Who is the Chief Minister of Telangana?', NOW), [])
        fetch.assert_not_called()
        self.addCleanup(e._CACHE.clear)
