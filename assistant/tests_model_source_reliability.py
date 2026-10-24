"""Network-free model comparison and evidence acquisition regressions."""
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from assistant.ai_engine.base import AIEngineResult, AIEngineStreamEvent
from assistant.model_benchmark import BenchmarkEngine, CASES, MODELS, assess, run_benchmark, summary
from assistant.online.base import OnlineRetrievalResult, OnlineRetrievalTimeoutError
from assistant.online.grounding import select_current_evidence
from assistant.online.searxng import SearXNGOnlineRetriever, clear_online_cache
from assistant.services import AssistantService
from assistant.test_support import install_resource_report
from assistant.technical_facts import specification_instruction
from assistant.tests_adaptive_online import Response


def source(**changes):
    now = datetime.now(timezone.utc)
    return replace(OnlineRetrievalResult('Raspberry Pi OS release announcement',
        'https://www.raspberrypi.com/news/os-release/',
        'Raspberry Pi OS release announcement describes the new desktop update and its supported software.',
        'searxng', now, published_at=now), **changes)


class DiscoveryTests(SimpleTestCase):
    def setUp(self):
        clear_online_cache()
        self.retriever = SearXNGOnlineRetriever(base_url='http://127.0.0.1:8888', timeout_seconds=8, max_results=4)

    @patch('assistant.online.enrichment.enrich_results', side_effect=lambda results,*args: results)
    @patch.object(SearXNGOnlineRetriever, '_search')
    def test_undated_release_discovers_dated_official_notes_and_caches(self, search, enrich):
        search.side_effect = [[source(published_at=None)], [source(url='https://www.raspberrypi.com/news/new-os/')]]
        query = 'latest Raspberry Pi OS version'
        results = self.retriever.retrieve(query)
        self.assertTrue(select_current_evidence(results, query, datetime.now(timezone.utc)))
        self.assertEqual(search.call_count, 2)
        self.assertIn('site:raspberrypi.com/news/', search.call_args.args[0])
        self.assertEqual(search.call_args_list[0].args[1], search.call_args_list[1].args[1])
        self.assertEqual(self.retriever.diagnostics['results_retrieved'], 2)
        self.assertEqual(self.retriever.diagnostics['fresh_evidence_accepted'], 1)
        self.retriever.retrieve(query)
        self.assertEqual(search.call_count, 2)

    @patch('assistant.online.enrichment.enrich_results', side_effect=lambda results,*args: results)
    @patch.object(SearXNGOnlineRetriever, '_search')
    def test_fallback_failure_preserves_rejection_without_health_lie(self, search, enrich):
        for bad in (source(published_at=None), source(published_at=datetime.now(timezone.utc)-timedelta(days=100)),
                    source(published_at=datetime.now(timezone.utc)+timedelta(days=1)), source(date_conflict=True)):
            with self.subTest(bad=bad):
                clear_online_cache()
                search.side_effect = [[bad], OnlineRetrievalTimeoutError('timeout')]
                results = self.retriever.retrieve('Raspberry Pi news today')
                self.assertEqual(select_current_evidence(results,'Raspberry Pi news today',datetime.now(timezone.utc)), [])
                self.assertTrue(self.retriever.diagnostics['provider_reachable'])
                self.assertEqual(self.retriever.diagnostics['fresh_evidence_accepted'], 0)

    @patch('assistant.online.enrichment.enrich_results', side_effect=lambda results,*args: results)
    @patch('assistant.online.searxng._open_search')
    def test_news_uses_date_capable_category_and_does_not_refetch_fresh(self, http, enrich):
        item = source()
        http.return_value = Response([dict(title=item.title,url=item.url,content=item.content,publishedDate=item.published_at.isoformat())])
        self.retriever.retrieve('Raspberry Pi news today')
        params = parse_qs(urlsplit(http.call_args.args[0].full_url).query)
        self.assertEqual(params['categories'], ['news'])
        self.assertEqual(params['time_range'], ['day'])
        self.assertEqual(http.call_count, 1)

    @patch('assistant.online.enrichment.fetch_metadata')
    def test_denied_catalog_retains_status_from_cache_without_retry(self, fetch):
        import time
        from assistant.online import enrichment
        enrichment._CACHE.clear(); enrichment._HOST_BACKOFF.clear()
        self.addCleanup(enrichment._CACHE.clear)
        self.addCleanup(enrichment._HOST_BACKOFF.clear)
        fetch.side_effect = enrichment.PageUnavailable(403)
        item = source(url='https://www.raspberrypi.com/software/operating-systems/', published_at=None)
        for _ in range(2):
            results = enrichment.enrich_results([item], 'latest Raspberry Pi OS version', time.monotonic()+2)
            self.assertEqual(results[0].accessibility, 'unavailable')
            self.assertEqual(results[0].http_status, 403)
        self.assertEqual(fetch.call_count, 1)

    def test_local_spec_reference_is_small_scoped_and_not_personal_evidence(self):
        facts = specification_instruction('Explain how Raspberry Pi 5 works')
        self.assertIn('BCM2712', facts)
        self.assertIn('not the main CPU', facts)
        self.assertLess(len(facts), 600)
        self.assertIsNone(specification_instruction('Hello'))

    @patch.object(SearXNGOnlineRetriever, '_search')
    def test_denied_source_count_survives_candidate_filtering(self, search):
        search.side_effect=[[source(published_at=None)], [source(url='https://example.org/pi-os-release')]]
        def enrich(results, *args):
            return [replace(r,accessibility='unavailable',http_status=403) if '/operating-systems/' in r.url else r for r in results]
        with patch('assistant.online.enrichment.enrich_results',side_effect=enrich):
            self.retriever.retrieve('latest Raspberry Pi OS version')
        self.assertEqual(self.retriever.diagnostics['sources_access_denied'],1)
        self.assertEqual(self.retriever.diagnostics['sources_unavailable'],1)


@override_settings(AI_ENGINE='local', ONLINE_RETRIEVAL_ENABLED=True, RAG_ENABLED=False, ONLINE_KNOWLEDGE_AUTO_PERSIST=False)
class EvidenceMetadataTests(TestCase):
    def setUp(self):
        install_resource_report(self, online_allowed=True)

    @patch('assistant.services.get_engine')
    @patch('assistant.services.get_online_retriever')
    def test_both_paths_distinguish_provider_from_accepted_evidence(self, retrieve, engine):
        answer = AIEngineResult(text='Raspberry Pi OS has a desktop update.', engine='local')
        engine.return_value.generate.return_value = answer
        engine.return_value.generate_stream.side_effect = lambda *args,**kwargs: iter([
            AIEngineStreamEvent(type='delta',text=answer.text), AIEngineStreamEvent(type='done', result=answer)])
        retrieve.return_value.diagnostics = {'results_retrieved': 4, 'sources_verified': 1,
            'sources_unavailable': 1, 'sources_access_denied': 1}
        for stream in (False, True):
            for fresh in (False, True):
                with self.subTest(stream=stream, fresh=fresh):
                    retrieve.return_value.retrieve.return_value = [source(published_at=datetime.now(timezone.utc) if fresh else None)]
                    query='Raspberry Pi news today'
                    if stream:
                        metadata=list(AssistantService.process_message_stream(query))[-1]
                    else:
                        _,_,metadata,error=AssistantService.process_message(query)
                        self.assertIsNone(error)
                    self.assertTrue(metadata['provider_reachable'])
                    self.assertEqual(metadata['online_success'], fresh)
                    self.assertEqual(metadata['online_used'], fresh)
                    self.assertEqual(metadata['fresh_evidence_accepted'], int(fresh))
                    self.assertEqual(metadata['results_retrieved'], 4)
                    self.assertEqual(metadata['sources_access_denied'], 1)

    @patch('assistant.services.get_engine')
    def test_pi_spec_reference_in_both_offline_paths(self, engine):
        answer=AIEngineResult(text='The Raspberry Pi 5 uses a BCM2712.',engine='local')
        engine.return_value.generate.return_value=answer
        engine.return_value.generate_stream.return_value=iter([AIEngineStreamEvent(type='done',result=answer)])
        AssistantService.process_message('What processor does a Raspberry Pi 5 use?')
        self.assertIn('BCM2712',engine.return_value.generate.call_args.kwargs['system_instruction'])
        list(AssistantService.process_message_stream('Explain how Raspberry Pi 5 works'))
        self.assertIn('BCM2712',engine.return_value.generate_stream.call_args.kwargs['system_instruction'])


class BenchmarkTests(SimpleTestCase):
    @patch('assistant.model_benchmark.request.urlopen')
    @patch('assistant.model_benchmark.threading.Timer')
    def test_benchmark_stream_measures_tokens_and_cleans_up_watchdog(self,timer,http):
        http.return_value.__enter__.return_value.readline.side_effect=[
            json.dumps({'message':{'content':'Hello'}}).encode()+b'\n',
            json.dumps({'message':{'content':''},'done':True,'eval_count':2,'eval_duration':1_000_000_000}).encode()+b'\n']
        engine=BenchmarkEngine(think=False,num_ctx=2048)
        events=list(engine.generate_stream('fixed',system_instruction='brief',num_predict=64,model=MODELS[0],timeout_seconds=5))
        result=events[-1].result
        self.assertEqual(result.text,'Hello')
        self.assertEqual(result.metrics['tokens_per_sec'],2)
        self.assertGreaterEqual(result.ttft_ms,0)
        timer.return_value.cancel.assert_called_once()
        timer.return_value.join.assert_called_once_with(timeout=2)

    @patch('assistant.model_benchmark.request.urlopen')
    @patch('assistant.model_benchmark.threading.Timer')
    def test_bad_streams_fail_without_a_recommendation_and_close_socket_context(self,timer,http):
        engine=BenchmarkEngine()
        for payload in (b'not json', b'x'*65537, b'', b'{"error":"private details"}'):
            with self.subTest(payload=payload[:10]):
                http.reset_mock()
                http.return_value.__enter__.return_value.readline.side_effect=None
                http.return_value.__enter__.return_value.readline.return_value=payload
                with self.assertRaises(ValueError):
                    list(engine.generate_stream('fixed',system_instruction='brief',num_predict=64,model=MODELS[0],timeout_seconds=5))
                http.return_value.__exit__.assert_called_once()

    def test_generation_settings_are_identical_bounded_and_thinking_off(self):
        engine=BenchmarkEngine(model=MODELS[0],num_ctx=2048,think=False,num_thread=4,num_batch=128)
        payloads=[]
        for model in MODELS:
            payload=json.loads(engine._build_chat_request([{'role':'user','content':'fixed'}],stream=True,num_predict=64,model=model).data)
            self.assertFalse(payload['think'])
            payloads.append(payload['options'])
        self.assertTrue(all(p==payloads[0] for p in payloads))
        self.assertEqual(payloads[0]['temperature'],0)
        self.assertEqual(payloads[0]['num_ctx'],2048)

    def test_rubric_does_not_reward_wrong_pi_cpu(self):
        self.assertTrue(assess('pi5_cpu','BCM2712 quad-core Cortex-A76 at 2.4 GHz',False)['rubric_pass'])
        self.assertFalse(assess('pi5_cpu','BCM2711 Cortex-A72 at 2.4 GHz',False)['rubric_pass'])
        self.assertFalse(assess('coding','```python\ndef f(x): return x*x\n```',True)['spoken_concise'])

    @patch('assistant.model_benchmark.maintenance_guard')
    @patch('assistant.voice.push_to_talk.microphone_ownership',return_value=nullcontext())
    @patch('assistant.model_benchmark.BenchmarkEngine')
    @patch('assistant.model_benchmark.residents',return_value=[])
    @patch('assistant.model_benchmark.unload')
    @patch('assistant.model_benchmark.check_resources')
    @patch('assistant.model_benchmark.Samples')
    def test_all_models_run_sequentially_cold_then_warm_and_no_download(self,samples,resources,unload,residents,engine,ownership,guard):
        resources.return_value=SimpleNamespace(max_output_tokens=128,timeout_seconds=120)
        samples.return_value.unsafe=False
        samples.return_value.finish.return_value={'throttled_bits':0}
        engine.return_value.available_models.return_value=set(MODELS)
        engine.return_value._host='http://localhost:11434'
        def generate(*args,**kwargs):
            yield AIEngineStreamEvent(type='done', result=AIEngineResult(text='Fixed answer',engine='local',ttft_ms=5))
        engine.return_value.generate_stream.side_effect=generate
        rows=[]
        run_benchmark(rows.append)
        measurements=rows[:-1]
        self.assertEqual(len(measurements), len(MODELS)*len(CASES)*4)
        self.assertEqual([r['model'] for r in measurements], sorted([r['model'] for r in measurements],key=MODELS.index))
        self.assertEqual([r['phase'] for r in measurements], ['cold','warm']*(len(measurements)//2))
        self.assertEqual(unload.call_count,len(MODELS)*(len(CASES)*2+1))
        self.assertFalse(rows[-1]['settings_changed'])
        ownership.assert_called_once()

    @patch('assistant.model_benchmark.maintenance_guard',side_effect=ValueError('private-host'))
    @patch('assistant.model_benchmark.BenchmarkEngine')
    def test_command_fails_before_model_access_when_services_not_quiescent(self,engine,guard):
        with self.assertRaises(CommandError) as exc:
            call_command('benchmark_models',stdout=io.StringIO())
        self.assertNotIn('private-host',str(exc.exception))
        engine.assert_not_called()

    def test_no_recommendation_without_real_eligible_measurements(self):
        self.assertIsNone(summary([])['candidate_for_manual_review'])


class OnlineDiagnosticCommandTests(SimpleTestCase):
    @patch('assistant.management.commands.verify_online_sources.get_online_retriever')
    def test_command_reports_no_evidence_without_pretending_model_use(self,retriever):
        retriever.return_value.retrieve.return_value=[]
        retriever.return_value.diagnostics={'results_retrieved':4,'sources_access_denied':1}
        output=io.StringIO()
        call_command('verify_online_sources',stdout=output)
        rows=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(rows),2)
        for row in rows:
            self.assertTrue(row['provider_reachable'])
            self.assertFalse(row['model_called'])
            self.assertFalse(row['answerable_evidence'])
            self.assertFalse(row['evidence_used_by_model'])
            self.assertEqual(row['fresh_evidence_accepted'],0)
        with self.assertRaises(CommandError):
            call_command('verify_online_sources',require_evidence=True,stdout=io.StringIO())
