"""Content-free tracing tests. Synthetic clocks and devices are not Pi measurements."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import io
import json
from pathlib import Path
import subprocess
import tempfile
from threading import Event, Lock
import time
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from assistant import latency_trace as tracing
from assistant.ai_engine.local import LocalLLMEngine
from assistant.voice.tts import TextToSpeechProvider
from assistant.tests_voice_stream import stream_events, FIRST_SENTENCE, SECOND_SENTENCE
from scripts import voice_loop as voice


class Clock:
    def __init__(self):
        self.value = 0
        self.lock = Lock()
    def __call__(self):
        with self.lock:
            self.value += 1000000
            return self.value


class TraceTests(SimpleTestCase):
    def tearDown(self):
        store = tracing.configure(None)
        if store:
            store.flush()

    def test_disabled_has_no_clock_storage_or_context_copy(self):
        @tracing.span('tts')
        def operation(value): return value
        with patch.object(tracing, 'Trace', side_effect=AssertionError), patch.object(tracing, 'copy_context', side_effect=AssertionError):
            with tracing.cycle():
                tracing.mark('first_speech_unit', transcript='NEVER EXPORT')
                self.assertEqual(operation(9), 9)
                with ThreadPoolExecutor() as executor:
                    self.assertEqual(tracing.submit(executor, lambda: 11, 'audio_prepare').result(), 11)

    def test_fixed_names_numeric_allowlist_and_unknown_cache(self):
        trace = tracing.Trace(clock=Clock(), synthetic=True)
        with tracing.cycle(trace):
            tracing.mark('secret private question')
            tracing.mark('ollama_complete', transcript='secret', answer='secret', token='secret',
                         eval_count=True, load_duration_ms=float('nan'), prompt_eval_count=136)
        raw = json.dumps(trace.report())
        self.assertNotIn('secret', raw)
        self.assertEqual(trace.report()['cache_accounting'], 'unknown_or_invalid')
        self.assertNotIn('prompt_eval_cached_count', raw)
        self.assertNotIn('eval_count', next(e for e in trace.events if e['name']=='ollama_complete'))
        for count, expected in ((0,'reported_consistent'), (120,'reported_consistent'), (140,'unknown_or_invalid')):
            trace = tracing.Trace(clock=Clock())
            trace.emit('ollama_complete', prompt_eval_count=136, prompt_eval_cached_count=count)
            self.assertEqual(trace.report()['cache_accounting'], expected)

    def test_partial_error_timeout_and_cancel(self):
        for error, outcome in ((RuntimeError('private question'), 'error'), (TimeoutError('token=secret'), 'timeout'),
                               (subprocess.TimeoutExpired('secret', 1), 'timeout'), (KeyboardInterrupt(), 'cancelled')):
            trace = tracing.Trace(clock=Clock())
            @tracing.span('stt_cli')
            def fail():
                tracing.mark('capture_first_frame')
                raise error
            with self.assertRaises(type(error)), tracing.cycle(trace):
                fail()
            report = trace.report()
            self.assertEqual(report['status'], outcome)
            self.assertEqual(report['events'][-1]['name'], 'cycle_end')
            self.assertIn('llm_first_alphanumeric', report['missing_events'])
            self.assertNotIn('secret', json.dumps(report))

    def test_nonblocking_lock_event_bounds_and_report_size(self):
        trace = tracing.Trace(clock=Clock())
        trace.lock.acquire()
        trace.emit('capture_first_frame')
        trace.lock.release()
        self.assertEqual(trace.dropped, 1)
        for _ in range(2000):
            for name in tracing.EVENT_NAMES:
                trace.emit(name)
        self.assertLessEqual(len(trace.events), tracing.MAX_EVENTS)
        self.assertLess(len(json.dumps(trace.report()).encode()), tracing.MAX_BYTES)
        self.assertGreater(trace.dropped, 1)

    def test_overlap_and_cross_thread_correlation(self):
        trace = tracing.Trace(clock=Clock())
        entered, release = Event(), Event()
        @tracing.span('tts')
        def prepare():
            entered.set()
            if not release.wait(2): raise AssertionError('Test synchronization failed')
        with tracing.cycle(trace), ThreadPoolExecutor(max_workers=1) as executor:
            future = tracing.submit(executor, prepare, 'audio_prepare')
            self.assertTrue(entered.wait(2))
            tracing.mark('llm_first_alphanumeric')
            release.set()
            future.result()
        events = trace.report()['events']
        names = [e['name'] for e in events]
        self.assertLess(names.index('tts_start'), names.index('llm_first_alphanumeric'))
        self.assertLess(names.index('llm_first_alphanumeric'), names.index('tts_end'))
        self.assertEqual([e['at_ms'] for e in events], sorted(e['at_ms'] for e in events))
        self.assertEqual(next(e for e in events if e['name']=='tts_start')['lane'], 'audio_prepare')
        self.assertRegex(trace.cycle_id, r'^[a-f0-9]{32}$')

    def test_background_storage_rotation_errors_and_disable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = tracing.TraceStore(directory)
            for _ in range(12):
                trace = tracing.Trace(clock=Clock(), synthetic=True)
                with tracing.cycle(trace): pass
                store.submit(trace.report())
                self.assertTrue(store.flush())
            self.assertEqual(len(list(Path(directory).glob('cycle-*.json'))), tracing.MAX_FILES)
            self.assertFalse((Path(directory)/'cycle.pending').exists())
            with patch.object(tracing.os, 'replace', side_effect=OSError('private path')):
                store.submit(trace.report())
                self.assertTrue(store.flush())
            self.assertEqual(store.write_errors, 1)
            store.close(); store.flush()
            tracing.configure(directory)
            with tracing.cycle(): tracing.mark('capture_first_frame')
            old = tracing.configure(None)
            self.assertTrue(old.flush())
            with tracing.cycle() as disabled: self.assertIsNone(disabled)

    def test_report_queue_backpressure_never_waits(self):
        # No writer needed to prove submit bounds its queue and never calls blocking put.
        store = object.__new__(tracing.TraceStore)
        from queue import Queue
        store.queue, store.dropped_reports = Queue(maxsize=2), 0
        for _ in range(100): store.submit({})
        self.assertEqual(store.queue.qsize(), 2)
        self.assertEqual(store.dropped_reports, 98)

    def test_bounded_overhead_clock_reads(self):
        # Deterministic operation-count budget, not a fragile wall-time benchmark.
        clock = Mock(side_effect=Clock())
        trace = tracing.Trace(clock=clock)
        for _ in range(10000): trace.emit('first_speech_unit', once=True)
        self.assertEqual(clock.call_count, 2)  # origin plus one accepted event
        self.assertEqual(len(trace.events), 1)

    def test_real_ollama_stream_hooks_exclude_whitespace_and_missing_counters(self):
        response = io.BytesIO(b'\n'.join(json.dumps(e).encode() for e in (
            {'message':{'content':' ... '}}, {'message':{'content':'Public answer.'}},
            {'done':True,'done_reason':'stop','prompt_eval_count':10,'load_duration':9000000,'eval_count':3})))
        trace = tracing.Trace(clock=Clock())
        with patch('assistant.ai_engine.local.urllib.request.urlopen', return_value=response), tracing.cycle(trace):
            result = list(LocalLLMEngine().generate_stream('PRIVATE_QUERY', system_instruction='PRIVATE_TOKEN'))
        self.assertEqual(result[-1].result.text, ' ... Public answer.')
        events = trace.events
        self.assertLess(next(e['at_ms'] for e in events if e['name']=='ollama_request_ready'),
                        next(e['at_ms'] for e in events if e['name']=='llm_first_alphanumeric'))
        self.assertEqual(next(e for e in events if e['name']=='ollama_complete')['load_duration_ms'], 9)
        self.assertEqual(trace.report()['residency'], 'unknown_not_probed')
        self.assertNotIn('PRIVATE', json.dumps(trace.report()))

    def test_native_pcm_mark_after_success_excludes_injected_silence(self):
        from assistant.voice.alsa_stream import PersistentAlsaPlayer
        player = PersistentAlsaPlayer('fake', Event(), Path('.'))
        player.fd = 10
        trace = tracing.Trace(clock=Clock())
        with tempfile.TemporaryDirectory() as directory:
            pcm = Path(directory)/'public.pcm'; pcm.write_bytes(b'\x01'*16)
            with patch('assistant.voice.alsa_stream.os.write', side_effect=lambda fd,data:len(data)), tracing.cycle(trace):
                player.write_silence(2)
                self.assertNotIn('first_speech_pcm_accepted', trace.counts)
                player.write_file(pcm)
        self.assertEqual(trace.counts['first_speech_pcm_accepted'], 1)
        names = [e['name'] for e in trace.events]
        self.assertLess(names.index('leading_silence_end'), names.index('first_speech_pcm_accepted'))

    def test_full_fake_microphone_to_player_pipeline_and_no_private_content(self):
        trace = tracing.Trace(clock=Clock(), synthetic=True)
        entered, continued = Event(), Event()
        class STT:
            @tracing.span('stt_cli')
            def transcribe(self, path): return 'PRIVATE_QUESTION_CANARY'
        class TTS(TextToSpeechProvider):
            @tracing.span('tts')
            def synthesize(self, text):
                tracing.mark('piper_state', reused=True)
                entered.set()
                if not continued.wait(2): raise AssertionError('Test synchronization failed')
                return b'fake WAV; not audio'
        class Player:
            def __init__(self,*args,**kwargs): pass
            @tracing.span('player_start')
            def start(self): pass
            @tracing.span('leading_silence')
            def write_silence(self, frames): pass
            @tracing.span('speech_file')
            def write_file(self, path, **kwargs): tracing.mark('first_speech_pcm_accepted', once=True)
            @tracing.span('drain')
            def finish(self): pass
            def abort(self): pass
            def check(self): pass
        def record(path,*args,**kwargs):
            tracing.mark('capture_open_start'); tracing.mark('capture_open_end')
            tracing.mark('capture_first_frame'); tracing.mark('calibration_end')
            tracing.mark('speech_detected'); tracing.mark('endpoint_detected',reason='silence')
            path.write_bytes(b'not real microphone audio')
        def normalize(source,target,**kwargs): target.write_bytes(b'fake normalized')
        def convert(source,target,*args,**kwargs): target.write_bytes(b'\x00'*8)
        def service(*args,**kwargs):
            tracing.mark('ollama_request_ready')
            yield {'type':'start','conversation_id':1}
            tracing.first_text(FIRST_SENTENCE)
            yield {'type':'delta','text':FIRST_SENTENCE+' '}
            if not entered.wait(2): raise AssertionError('Test synchronization failed')
            continued.set()
            answer = FIRST_SENTENCE+' '+SECOND_SENTENCE+' PRIVATE_ANSWER_CANARY'
            yield {'type':'delta','text':SECOND_SENTENCE+' PRIVATE_ANSWER_CANARY'}
            yield {'type':'done','conversation_id':1,'response':answer}
        with ExitStack() as stack:
            stack.enter_context(patch.object(voice,'record_audio_with_capture_vad',side_effect=record))
            stack.enter_context(patch.object(voice,'normalize_audio',side_effect=normalize))
            stack.enter_context(patch.object(voice,'get_stt_provider',return_value=STT()))
            stack.enter_context(patch.object(voice,'get_tts_provider',return_value=TTS()))
            stack.enter_context(patch.object(voice.AssistantService,'process_message_stream',side_effect=service))
            stack.enter_context(patch.object(voice,'prepare_alsa_playback',side_effect=convert))
            stack.enter_context(patch.object(voice,'trim_pcm_edges',return_value=(0,0)))
            stack.enter_context(patch.object(voice,'PersistentAlsaPlayer',Player))
            stack.enter_context(tracing.cycle(trace))
            result=voice.run_voice_cycle(voice.VoiceLoopConfig('',audio_backend='alsa',capture_vad_enabled=True),display=Mock())
        self.assertEqual(result,1)
        raw=json.dumps(trace.report())
        self.assertNotIn('PRIVATE',raw)
        self.assertNotIn(FIRST_SENTENCE,raw)
        names=[e['name'] for e in trace.events]
        for a,b in (('capture_first_frame','stt_cli_start'),('stt_cli_end','ollama_request_ready'),
                    ('llm_first_alphanumeric','first_speech_unit'),('first_speech_unit','first_audio_ready'),
                    ('first_audio_ready','first_speech_pcm_accepted'),('first_speech_pcm_accepted','drain_end')):
            self.assertLess(names.index(a),names.index(b))
        self.assertEqual({e['lane'] for e in trace.events},{'main','audio_prepare','audio_playback'})

@override_settings(AI_ENGINE='local', RAG_ENABLED=True, ONLINE_RETRIEVAL_ENABLED=False,
                   DEVICE_CONTROL_ENABLED=False, COMPANION_DISPLAY_ENABLED=False, MQTT_HOST='',
                   RAG_MIN_RELEVANCE=0.1, CONVERSATION_MEMORY_MIN_RELEVANCE=0.1)
class TraceServiceCompatibilityTests(TestCase):
    def test_real_rag_history_memory_and_persistence_unchanged(self):
        from assistant.ai_engine.base import AIEngineResult, AIEngineStreamEvent
        from assistant.services import AssistantService
        from assistant.policy import AssistantResponsePolicy
        from assistant.test_support import install_resource_report
        from conversations.models import Conversation, Message
        from knowledge_base.models import Document, KnowledgeChunk
        install_resource_report(self)
        document=Document.objects.create(filename='public.md',status='INDEXED',source_identifier='test:public')
        KnowledgeChunk.objects.create(document=document,chunk_index=0,content='The test controller uses GPIO18 for an indicator.')
        def generate(*args,**kwargs):
            yield AIEngineStreamEvent(type='delta',text='A public grounded answer.')
            yield AIEngineStreamEvent(type='done',result=AIEngineResult(text='A public grounded answer.',engine='local'))
        engine=Mock(); engine.generate_stream.side_effect=generate
        captured=[]
        for enabled in (False,True):
            conversation=Conversation.objects.create(title='public fixture')
            Message.objects.create(conversation=conversation,sender='USER',text='Remember the controller uses GPIO18.')
            Message.objects.create(conversation=conversation,sender='AI',text='Noted.')
            query='Recall GPIO18 controller indicator according to our project notes.'
            plan=AssistantResponsePolicy.plan_voice_response(query)
            trace=tracing.Trace(clock=Clock(),synthetic=True)
            with patch('assistant.services.get_engine',return_value=engine):
                if enabled:
                    with tracing.cycle(trace):
                        events=list(AssistantService.process_message_stream(query,conversation.id,response_plan=plan))
                else:
                    events=list(AssistantService.process_message_stream(query,conversation.id,response_plan=plan))
            captured.append((events[-1]['response'],events[-1]['rag_used'],events[-1]['memory_used'],
                             engine.generate_stream.call_args.kwargs,
                             list(Message.objects.filter(conversation=conversation).values_list('sender','text'))))
            if enabled:
                names={e['name'] for e in trace.events}
                self.assertIn('rag_start',names)
                self.assertIn('memory_start',names)
                self.assertNotIn('GPIO18',json.dumps(trace.report()))
        self.assertEqual(captured[0],captured[1])
        self.assertTrue(captured[1][1])
        self.assertTrue(captured[1][2])


class AdditionalTraceTests(SimpleTestCase):
    def test_sentence_ready_precedes_held_short_speech_unit(self):
        trace=tracing.Trace(clock=Clock())
        with tracing.cycle(trace):
            buffer=voice.StreamingSpeechBuffer()
            self.assertEqual(buffer.feed('This is a complete answer. '),[])
        self.assertIn('first_complete_sentence',trace.counts)
        self.assertNotIn('first_speech_unit',trace.counts)

    def test_cached_resource_report_avoids_new_ancillary_checks(self):
        from system.control_plane import resource_manager as manager
        report={'snapshot':{},'timing':{'collection_ms':123}}
        trace=tracing.Trace(clock=Clock())
        with patch.object(manager.cache,'get',return_value=report), patch.object(manager,'collect_resource_snapshot') as collect, tracing.cycle(trace):
            self.assertIs(manager.get_resource_manager_report(),report)
        collect.assert_not_called()
        self.assertIn('resource_cache_hit',trace.counts)
        self.assertNotIn('resource_cache_miss',trace.counts)

    def test_error_cycle_is_persisted_and_io_never_runs_in_producer(self):
        with tempfile.TemporaryDirectory() as directory:
            tracing.configure(directory)
            try:
                with self.assertRaises(TimeoutError), tracing.cycle():
                    tracing.mark('first_speech_unit')
                    raise TimeoutError('PRIVATE_SECRET')
            finally:
                store=tracing.configure(None)
                self.assertTrue(store.flush())
            files=list(Path(directory).glob('cycle-*.json'))
            self.assertEqual(len(files),1)
            report=json.loads(files[0].read_text())
            self.assertEqual(report['status'],'timeout')
            self.assertIn('cycle_end',[e['name'] for e in report['events']])
            self.assertNotIn('PRIVATE_SECRET',json.dumps(report))

@override_settings(AI_ENGINE='mock', RAG_ENABLED=False, ONLINE_RETRIEVAL_ENABLED=False,
                   DEVICE_CONTROL_ENABLED=False, MQTT_HOST='')
class ServiceFailureTraceTests(TestCase):
    def test_stream_timeout_category_survives_service_error_translation(self):
        from assistant.services import AssistantService
        from assistant.ai_engine import EngineTimeoutError
        engine=Mock()
        engine.generate_stream.side_effect=EngineTimeoutError('PRIVATE_TIMEOUT_DETAIL')
        trace=tracing.Trace(clock=Clock(),synthetic=True)
        with patch('assistant.services.get_engine',return_value=engine), tracing.cycle(trace):
            events=list(AssistantService.process_message_stream('PUBLIC_FIXTURE'))
        self.assertEqual(events[-1]['type'],'error')
        self.assertTrue(any(e['name']=='stream_error' and e.get('outcome')=='timeout' for e in trace.events))
        self.assertNotIn('PRIVATE_TIMEOUT_DETAIL',json.dumps(trace.report()))


class SchemaAndBoundaryTests(SimpleTestCase):
    def test_published_schema_and_synthetic_fixture_match_event_allowlist(self):
        root=Path(__file__).resolve().parents[1]
        schema=json.loads((root/'docs/voice-latency-trace.schema.json').read_text())
        report=json.loads((root/'docs/voice-latency-trace.synthetic.json').read_text())
        allowed=set(schema['properties']['events']['items']['properties']['name']['enum'])
        self.assertEqual(allowed,tracing.EVENT_NAMES)
        self.assertTrue(report['synthetic'])
        self.assertIn('SYNTHETIC FIXTURE',report['interpretation'])
        self.assertTrue(set(schema['required'])<=report.keys())
        self.assertLessEqual(len(report['events']),tracing.MAX_EVENTS)
        for event in report['events']:
            self.assertIn(event['name'],allowed)
            self.assertLessEqual(event['at_ms'],report['elapsed_ms'])
        self.assertEqual(report['cache_accounting'],'unknown_or_invalid')
        self.assertEqual(report['acoustic_onset'],'unobserved')

    def test_lane_names_cannot_export_user_supplied_strings(self):
        trace=tracing.Trace(clock=Clock())
        with tracing.cycle(trace), ThreadPoolExecutor() as executor:
            tracing.submit(executor,lambda:tracing.mark('first_audio_ready'),'SECRET_LANE').result()
        self.assertNotIn('SECRET_LANE',json.dumps(trace.report()))
        self.assertEqual(next(e for e in trace.events if e['name']=='first_audio_ready')['lane'],'other')

class EnabledAudioCompatibilityTests(SimpleTestCase):
    def test_existing_bit_exact_alsa_pipeline_with_tracing_enabled(self):
        # Reuse the established byte-for-byte expected PCM, gain, edge/gap and drain assertions.
        from assistant.tests_alsa_stream import PersistentPipelineTests
        case=PersistentPipelineTests('test_real_persistent_player_pipeline_bytes_silence_trim_and_single_process')
        case.setUp()
        trace=tracing.Trace(clock=Clock(),synthetic=True)
        with tracing.cycle(trace):
            case.test_real_persistent_player_pipeline_bytes_silence_trim_and_single_process()
        self.assertIn('first_speech_pcm_accepted',trace.counts)
        self.assertIn('audio_prepare_end',trace.counts)
        self.assertEqual(trace.status,'ok')

    def test_failed_observer_does_not_change_operation_or_exception(self):
        trace=tracing.Trace(clock=Clock())
        @tracing.span('normalize')
        def operation(): return 17
        with tracing.cycle(trace), patch.object(trace,'emit',side_effect=RuntimeError('private observer failure')):
            self.assertEqual(operation(),17)
        self.assertEqual(trace.status,'ok')
