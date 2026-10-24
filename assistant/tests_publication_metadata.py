import json
import socket
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from assistant.online import enrichment as e
from assistant.online.base import OnlineRetrievalResult
from assistant.online.dates import parse_publication_date, consistent_date
from assistant.online.grounding import select_current_evidence
from assistant.online.searxng import SearXNGOnlineRetriever, clear_online_cache
from assistant.ai_engine.base import AIEngineResult, AIEngineStreamEvent
from assistant.services import AssistantService
from assistant.test_support import install_resource_report

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
QUERY = 'latest Raspberry Pi news October 2026'


def source():
    return OnlineRetrievalResult('Raspberry Pi OS release', 'https://www.raspberrypi.com/news/os/', 'Raspberry Pi OS adds new desktop features.', 'searxng', NOW)


class DateTests(SimpleTestCase):
    def test_common_formats_and_offsets(self):
        for value in ('2026-10-24T12:00:00Z', '2026-10-24T17:30:00+05:30', 'Sat, 24 Oct 2026 12:00:00 GMT', '2026-10-24 12:00:00+0000'):
            self.assertEqual(parse_publication_date(value), NOW)
        for value in ('October 24, 2026', '24 October 2026', '2026-10-24'):
            self.assertEqual(parse_publication_date(value).date(), NOW.date())
        for value in ('2 hours ago', 'October 2026', '2026', None, {}, 1792843200):
            self.assertIsNone(parse_publication_date(value))

    def test_conflicting_dates_are_not_resolved_by_selecting_newest(self):
        self.assertIsNone(consistent_date(['2020-01-01', NOW.isoformat()]))

    @patch('assistant.online.searxng.request.urlopen')
    def test_actual_searxng_fields_and_invalid_primary_fallback(self, http):
        for key in ('publishedDate', 'pubdate', 'published_date', 'datePublished', 'date_of_publication'):
            clear_online_cache()
            raw = dict(title=source().title, url=source().url, content=source().content, publishedDate='bad')
            raw[key] = 'Sat, 24 Oct 2026 12:00:00 GMT'
            response = MagicMock()
            response.__enter__.return_value = response
            response.geturl.return_value = 'http://127.0.0.1:8888/search'
            response.read1 = None
            response.read.return_value = json.dumps({'results': [raw]}).encode()
            http.return_value = response
            results = SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888', timeout_seconds=4, max_results=4).retrieve(QUERY)
            self.assertEqual(results[0].published_at, NOW)
            self.assertEqual(results[0].publication_source, 'searxng')

    def test_metadata_publication_modification_conflict_and_malformed(self):
        parser = e.ArticleMetadata()
        parser.feed('<meta property="article:published_time" content="2026-10-24T12:00:00Z"><script type="application/ld+json">'+json.dumps({'@type':'NewsArticle','datePublished':NOW.isoformat(),'dateModified':'2026-10-25'})+'</script>')
        self.assertEqual(consistent_date(parser.published), NOW)
        self.assertNotEqual(consistent_date(parser.modified), NOW)
        parser.feed('<meta itemprop="datePublished" content="2020-01-01">')
        self.assertIsNone(consistent_date(parser.published))
        for body in ('<script type="application/ld+json">not-json</script>', '<meta property="article:modified_time" content="2026-10-24">', '<script type="application/ld+json">{"@type":"Event","datePublished":"2026-10-24"}</script>', '<title>Latest 2026 news</title>'):
            parser = e.ArticleMetadata(); parser.feed(body)
            self.assertIsNone(consistent_date(parser.published))
        parser=e.ArticleMetadata('https://example.org/article')
        parser.feed('<script type="application/ld+json">'+json.dumps({'@type':'Article','url':'https://example.org/another','datePublished':NOW.isoformat()})+'</script>')
        self.assertIsNone(consistent_date(parser.published))

    def test_stale_future_conflicting_and_incidental_dates_abstain(self):
        for item in (replace(source(), published_at=NOW-timedelta(days=10)), replace(source(), published_at=NOW+timedelta(days=1)), replace(source(), published_at=NOW, date_conflict=True), replace(source(), content='Raspberry Pi OS conference scheduled for 2026-10-24.')):
            self.assertEqual(select_current_evidence([item], QUERY, NOW), [])


class FetchTests(SimpleTestCase):
    @patch('assistant.online.enrichment.ssl.create_default_context')
    @patch('assistant.online.enrichment.socket.create_connection')
    @patch('assistant.online.enrichment.http.client.HTTPConnection')
    @patch('assistant.online.enrichment.socket.getaddrinfo')
    def test_https_pins_address_and_verifies_original_hostname(self,dns,connection,connect,context):
        dns.return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
        response=connection.return_value.getresponse.return_value
        response.status=200
        response.getheader.side_effect=lambda key,default='': 'text/html' if key=='Content-Type' else default
        response.read1.side_effect=[b'<meta property="article:published_time" content="2026-10-24T12:00:00Z">',b'']
        self.assertEqual(e.fetch_metadata('https://example.org/article',time.monotonic()+2)[0],NOW)
        context.return_value.wrap_socket.assert_called_once_with(connect.return_value,server_hostname='example.org')
        self.assertEqual(connect.call_args.args[0],('93.184.216.34',443))

    @patch('assistant.online.enrichment.socket.getaddrinfo')
    def test_private_dns_mixed_answers_and_local_urls(self, dns):
        for addresses in (['127.0.0.1'], ['10.0.0.1'], ['::1'], ['169.254.169.254'], ['93.184.216.34', '192.168.1.1']):
            dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443)) for ip in addresses]
            with self.assertRaises(ValueError):
                e.public_target('https://example.org/article')
        for url in ('http://localhost/', 'file:///etc/passwd', 'https://user:secret@example.org', 'http://example.org:8080/'):
            with self.assertRaises(ValueError): e.public_target(url)

    @patch('assistant.online.enrichment.socket.create_connection')
    @patch('assistant.online.enrichment.http.client.HTTPConnection')
    @patch('assistant.online.enrichment.socket.getaddrinfo')
    def test_pinned_connection_html_and_redirect_validation(self, dns, connection, connect):
        dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34',80))]
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.getheader.side_effect = lambda key, default='': {'Content-Type':'text/html; charset=utf-8'}.get(key,default)
        response.read1.side_effect = [b'<meta property="article:published_time" content="2026-10-24T12:00:00Z">', b'']
        self.assertEqual(e.fetch_metadata('http://example.org/article', time.monotonic()+2)[0], NOW)
        self.assertEqual(connect.call_args.args[0], ('93.184.216.34',80))
        connect.reset_mock()
        response.status = 302
        response.getheader.side_effect = lambda key, default='': 'http://127.0.0.1/private' if key == 'Location' else default
        with self.assertRaises(ValueError): e.fetch_metadata('http://example.org/article', time.monotonic()+2)
        self.assertEqual(connect.call_count, 1)

    @patch('assistant.online.enrichment.public_target')
    @patch('assistant.online.enrichment.socket.create_connection')
    @patch('assistant.online.enrichment.http.client.HTTPConnection')
    def test_content_type_size_and_timeout(self, connection, connect, target):
        from urllib.parse import urlsplit
        target.return_value = (urlsplit('http://example.org/a'),80,'93.184.216.34')
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.getheader.side_effect = lambda key, default='': 'application/pdf' if key == 'Content-Type' else default
        with self.assertRaises(ValueError): e.fetch_metadata('http://example.org/a', time.monotonic()+2)
        response.getheader.side_effect = lambda key, default='': 'text/html' if key == 'Content-Type' else default
        response.read1.side_effect = lambda n: b'x'*n
        with self.assertRaises(ValueError): e.fetch_metadata('http://example.org/a', time.monotonic()+2)
        with self.assertRaises(TimeoutError): e.fetch_metadata('http://example.org/a', time.monotonic()-1)


class EnrichmentTests(SimpleTestCase):
    def setUp(self): e._CACHE.clear()

    @patch('assistant.online.enrichment.fetch_metadata', return_value=(NOW,None,False))
    def test_cache_bounds_and_skips_dated_or_irrelevant(self, fetch):
        results = e.enrich_results([source()], QUERY, time.monotonic()+4)
        self.assertEqual(select_current_evidence(results, QUERY, NOW), results)
        self.assertEqual(e.enrich_results([source()], QUERY, time.monotonic()+4), results)
        self.assertEqual(fetch.call_count, 1)
        e.enrich_results([replace(source(), published_at=NOW), replace(source(), title='Coupons',content='Discount vouchers and promotions!')], QUERY, time.monotonic()+4)
        self.assertEqual(fetch.call_count,1)
        e._CACHE.clear(); fetch.reset_mock()
        e.enrich_results([replace(source(),url=source().url+str(i)) for i in range(8)], QUERY,time.monotonic()+4)
        self.assertEqual(fetch.call_count,2)

    @patch('assistant.online.enrichment.fetch_metadata', side_effect=TimeoutError())
    def test_failures_are_cached_without_fabricated_date(self, fetch):
        for _ in range(2): self.assertIsNone(e.enrich_results([source()], QUERY,time.monotonic()+4)[0].published_at)
        self.assertEqual(fetch.call_count,1)

    @patch('assistant.online.enrichment.fetch_metadata', return_value=(NOW,None,False))
    def test_expiry_and_busy_slot(self, fetch):
        e._CACHE[source().url] = (time.monotonic()-1,(None,None,False))
        e._SLOT.acquire()
        try:
            self.assertIsNone(e.enrich_results([source()],QUERY,time.monotonic()+4)[0].published_at)
        finally:
            e._SLOT.release()
        fetch.assert_not_called()
        self.assertEqual(e.enrich_results([source()],QUERY,time.monotonic()+4)[0].published_at,NOW)

    @patch('assistant.online.enrichment.fetch_metadata')
    def test_credible_search_dates_never_fetch_articles(self, fetch):
        e.enrich_results([replace(source(),published_at=NOW)],QUERY,time.monotonic()+4)
        fetch.assert_not_called()

    @patch('assistant.online.enrichment.fetch_metadata')
    def test_page_dates_remain_subject_to_admission(self,fetch):
        for metadata in ((NOW-timedelta(days=10),NOW,False),(NOW+timedelta(days=1),None,False),(None,NOW,False),(None,None,True)):
            e._CACHE.clear(); fetch.return_value=metadata
            results=e.enrich_results([source()],QUERY,time.monotonic()+4)
            self.assertEqual(select_current_evidence(results,QUERY,NOW),[])


@override_settings(AI_ENGINE='local',ONLINE_RETRIEVAL_ENABLED=True,ONLINE_KNOWLEDGE_AUTO_PERSIST=False,RAG_ENABLED=False,SEARXNG_BASE_URL='http://127.0.0.1:8888')
class EnrichedServiceTests(TestCase):
    @patch('assistant.services.django_timezone.now', return_value=NOW)
    @patch('assistant.services.get_engine')
    @patch('assistant.online.enrichment.fetch_metadata', return_value=(NOW,None,False))
    @patch('assistant.online.searxng.request.urlopen')
    def test_streaming_and_nonstreaming_use_enriched_provenance(self,http,fetch,engine,clock):
        install_resource_report(self,online_allowed=True)
        response = MagicMock(); response.__enter__.return_value=response
        response.geturl.return_value='http://127.0.0.1:8888/search'
        response.read1=None
        response.read.return_value=json.dumps({'results':[dict(title=source().title,url=source().url,content=source().content)]}).encode()
        http.return_value=response
        result=AIEngineResult(text='Raspberry Pi OS adds new desktop features.',engine='local',latency_ms=1)
        engine.return_value.generate.return_value=result
        engine.return_value.generate_stream.side_effect=lambda *a,**k:iter([AIEngineStreamEvent(type='delta',text=result.text),AIEngineStreamEvent(type='done',result=result)])
        for stream in (False,True):
            clear_online_cache(); e._CACHE.clear()
            metadata=list(AssistantService.process_message_stream(QUERY))[-1] if stream else AssistantService.process_message(QUERY)[2]
            self.assertTrue(metadata['online_used'])
            self.assertEqual(metadata['online_sources'][0]['publication_source'],'article_metadata')
