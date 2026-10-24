import json
import time
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from django.test import SimpleTestCase
from assistant.online import enrichment as e
from assistant.online.base import OnlineRetrievalResult
from assistant.online.selection import rank_candidates, diverse_selection
from assistant.online.searxng import SearXNGOnlineRetriever, clear_online_cache
from assistant.online.grounding import select_current_evidence

QUERY = 'latest Raspberry Pi news October 2026'


def article(index=0, host='publisher.example'):
    return OnlineRetrievalResult('Raspberry Pi OS release '+str(index),f'https://{host}/article-{index}',f'Raspberry Pi OS adds informative desktop features in update {index}.','searxng',datetime.now(timezone.utc))


class SelectionTests(SimpleTestCase):
    def setUp(self):
        clear_online_cache(); e._CACHE.clear(); e._HOST_BACKOFF.clear()

    def test_provider_redirects_never_follow_portal_location(self):
        from assistant.online.searxng import _NoProviderRedirect
        self.assertIsNone(_NoProviderRedirect().redirect_request(None,None,302,'',{},'http://127.0.0.1/private'))

    @patch('assistant.online.enrichment.enrich_results',side_effect=lambda results,*args:results)
    @patch('assistant.online.searxng._open_search')
    def test_28_results_ranked_before_final_four(self,http,enrich):
        now=datetime.now(timezone.utc)
        raw=[]
        for i in range(28):
            item=article(i, f'publisher{i}.example')
            raw.append(dict(title=item.title,url=item.url,content=item.content))
        raw[0].update(url='https://www.raspberrypi.com/news/',title='Official Raspberry Pi news')
        raw[1].update(content='Raspberry Pi shop now special offers discount codes')
        for i in (8,12,17,25): raw[i]['publishedDate']=now.isoformat()
        response=MagicMock();response.__enter__.return_value=response
        response.geturl.return_value='http://127.0.0.1:8888/search';response.read1=None
        response.read.return_value=json.dumps({'results':raw}).encode();http.return_value=response
        results=SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888',timeout_seconds=4,max_results=4).retrieve(QUERY)
        self.assertEqual(len(enrich.call_args.args[0]),20)
        self.assertEqual(len(results),4)
        self.assertEqual({r.url for r in results},{raw[i]['url'] for i in (8,12,17,25)})
        self.assertTrue(all(r.accessibility=='unchecked' for r in results))

    def test_duplicates_landing_promotions_and_diversity(self):
        items=[replace(article(0),url='https://www.raspberrypi.com/news/'),replace(article(1),content='Raspberry Pi shop now special offers'),article(2),replace(article(2),url=article(2).url+'?utm_source=feed')]
        ranked=rank_candidates(items,QUERY)
        self.assertEqual(ranked[0].url,article(2).url)
        self.assertEqual(len(ranked),3)
        selected=diverse_selection([article(i) for i in range(5)]+[article(6,'second.example'),article(7,'third.example')],4)
        self.assertEqual(len(selected),4)
        self.assertEqual(len({r.url.split('/')[2] for r in selected}),3)

    @patch('assistant.online.enrichment.fetch_metadata')
    def test_fast_404_does_not_starve_later_candidates_and_is_cached(self,fetch):
        now=datetime.now(timezone.utc)
        fetch.side_effect=[e.PageUnavailable(404),(now,None,False),(now,None,False),(now,None,False)]
        sources=[replace(article(0),url='https://www.raspberrypi.com/news/')]+[article(i) for i in range(1,7)]
        results=e.enrich_results(sources,QUERY,time.monotonic()+8)
        self.assertEqual(fetch.call_count,4)
        self.assertEqual(results[1].accessibility,'unavailable')
        self.assertEqual(len(select_current_evidence(results,QUERY,now)),3)
        self.assertNotIn(sources[1].url,[r.url for r in rank_candidates(sources,QUERY)])
        self.assertTrue(all(call.args[1] <= time.monotonic()+.65 for call in fetch.call_args_list))

    @patch('assistant.online.enrichment.fetch_metadata',side_effect=e.PageUnavailable(429))
    def test_rate_limit_backoff_applies_to_publisher(self,fetch):
        e.enrich_results([article(0),article(1)],QUERY,time.monotonic()+8)
        self.assertEqual(fetch.call_count,1)

    @patch('assistant.online.enrichment.fetch_metadata')
    def test_four_fresh_search_dates_need_no_page_fetch(self,fetch):
        now=datetime.now(timezone.utc)
        e.enrich_results([replace(article(i),published_at=now) for i in range(4)]+[article(5)],QUERY,time.monotonic()+8)
        fetch.assert_not_called()

    @patch('assistant.online.enrichment.fetch_metadata',return_value=(None,None,False))
    def test_verification_budget_is_shared_across_publishers(self,fetch):
        e.enrich_results([article(i) for i in range(8)]+[article(9,'second.example'),article(10,'third.example')],QUERY,time.monotonic()+8)
        hosts=[call.args[0].split('/')[2] for call in fetch.call_args_list]
        self.assertEqual(hosts[:3],['publisher.example','second.example','third.example'])
        self.assertEqual(len(hosts),4)

    def test_old_future_and_unavailable_cannot_be_admitted(self):
        now=datetime.now(timezone.utc)
        for item in (replace(article(),published_at=now-timedelta(days=3)),replace(article(),published_at=now+timedelta(days=1)),replace(article(),published_at=now,accessibility='unavailable'),article()):
            self.assertEqual(select_current_evidence([item],QUERY,now),[])
        candidates=[replace(article(i),published_at=now-timedelta(days=20)) for i in range(28)]+[article(40)]
        self.assertEqual(rank_candidates(candidates,QUERY)[0].url,article(40).url)
        self.assertEqual(select_current_evidence([replace(article(),url='https://www.raspberrypi.com/news/',published_at=now)],QUERY,now),[])

    @patch('assistant.online.enrichment.public_target')
    @patch('assistant.online.enrichment.socket.create_connection')
    @patch('assistant.online.enrichment.http.client.HTTPConnection')
    def test_html_200_error_page_is_not_verified(self,connection,connect,target):
        from urllib.parse import urlsplit
        target.return_value=(urlsplit('http://example.org/article'),80,'93.184.216.34')
        response=connection.return_value.getresponse.return_value;response.status=200
        response.getheader.side_effect=lambda key,default='':'text/html' if key=='Content-Type' else default
        response.read1.side_effect=[b'<title>404 Not Found</title><meta property="article:published_time" content="2026-10-24">',b'']
        with self.assertRaises(ValueError):e.fetch_metadata('http://example.org/article',time.monotonic()+2)

    @patch('assistant.online.enrichment.fetch_metadata')
    def test_concurrent_verification_skips_duplicate_work(self,fetch):
        from threading import Thread, Event
        entered,release=Event(),Event()
        def slow(*args):
            entered.set()
            release.wait(1)
            return datetime.now(timezone.utc),None,False
        fetch.side_effect=slow
        worker=Thread(target=lambda:e.enrich_results([article()],QUERY,time.monotonic()+2))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            result=e.enrich_results([article()],QUERY,time.monotonic()+2)
            self.assertEqual(result[0].accessibility,'unchecked')
            self.assertEqual(fetch.call_count,1)
        finally:
            release.set();worker.join(2)
        self.assertFalse(worker.is_alive())
