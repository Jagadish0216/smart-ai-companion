"""Step 46: demonstrate the existing opt-in, with fake streams/audio and logical clocks."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
from threading import Event
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from assistant import latency_trace as tracing
from scripts import voice_loop as voice

SENTENCE = 'RAM holds data used by running programs.'


class SentenceReleaseTests(SimpleTestCase):
    def test_baseline_matches_explicit_sixty_and_optin_releases(self):
        for minimum, expected in ((None,[]),(60,[]),(30,[SENTENCE])):
            buffer=voice.StreamingSpeechBuffer(first_sentence_min_chars=minimum)
            self.assertEqual(buffer.feed(SENTENCE+' '),expected)
            self.assertEqual(buffer.feed('',final=True),[SENTENCE] if not expected else [])

    def test_logical_reproduction_of_supplied_buffer_gap(self):
        # Substitute a public fixture, not the unknown/private answer from the Pi.
        for minimum, release in ((None,61581),(30,59384)):
            now=[0]
            trace=tracing.Trace(clock=lambda:now[0],synthetic=True)
            buffer=voice.StreamingSpeechBuffer(first_sentence_min_chars=minimum)
            with tracing.cycle(trace):
                now[0]=59384000000
                units=buffer.feed(SENTENCE+' ')
                if units: tracing.mark('first_speech_unit',once=True)
                now[0]=61559000000
                tracing.mark('ollama_complete')
                now[0]=61581000000
                units.extend(buffer.feed('',final=True))
                if units: tracing.mark('first_speech_unit',once=True)
            events={e['name']:e['at_ms'] for e in trace.events}
            self.assertEqual(events['first_complete_sentence'],59384)
            self.assertEqual(events['first_speech_unit'],release)
            self.assertEqual(' '.join(units),SENTENCE)

    def test_too_short_complete_answers_remain_held(self):
        for text in ('Yes.','The answer is 4.','New Delhi is the capital.'):
            self.assertLess(len(text),30)
            buffer=voice.StreamingSpeechBuffer(first_sentence_min_chars=30)
            self.assertEqual(buffer.feed(text+' '),[])
            self.assertEqual(buffer.feed('',final=True),[text])

    def test_lookahead_and_split_closing_quotes_preserved(self):
        buffer=voice.StreamingSpeechBuffer(first_sentence_min_chars=30)
        text='The guide says "RAM holds temporary data.'
        self.assertEqual(buffer.feed(text),[])
        self.assertEqual(buffer.feed('"'),[])
        self.assertEqual(buffer.feed(' '),[text+'"'])
        self.assertEqual(buffer.feed('',final=True),[])

    def test_no_lower_clause_minimum_or_early_dangling_tail(self):
        for text in ('RAM holds data, because', 'RAM holds data used by the',
                     'RAM holds the current values, which', 'It is a memory-based'):
            self.assertLess(len(text),60)
            buffer=voice.StreamingSpeechBuffer(first_sentence_min_chars=30)
            self.assertEqual(buffer.feed(text),[])
        # Existing final unpunctuated handling is unchanged, rather than silently deleting words.
        buffer=voice.StreamingSpeechBuffer(first_sentence_min_chars=30)
        text='RAM holds data used by the'
        buffer.feed(text)
        self.assertEqual(buffer.feed('',final=True),[text])

    def test_technical_names_numbers_dates_and_urls_reconstruct_exactly(self):
        texts=(
            'Dr. A. P. J. Raman uses the RP2040 at 3.3 volts. Firmware stays unchanged.',
            'Prof. Rao measured 1,024 samples at 12:30 on 20.10.2026. Check revision 1.2.',
            'The voltage is 3.14 V, not 31.4 V. GPIO18 stays unchanged.',
            'Read https://example.test/rev1.2 for the guide. Keep the link intact.',
            'Read [the technical guide](https://example.test/rev1.2). Use the stated values.',
            'The U.S. device uses SRAM, e.g. for temporary buffers. It is not storage.',
        )
        for text in texts:
            for width in (1,2,5,13,len(text)):
                with self.subTest(text=text,width=width):
                    buffer=voice.StreamingSpeechBuffer(first_sentence_min_chars=30)
                    spoken=[]
                    for start in range(0,len(text),width): spoken.extend(buffer.feed(text[start:start+width]))
                    spoken.extend(buffer.feed('',final=True))
                    self.assertEqual(' '.join(spoken),voice.sanitize_text_for_speech(text))

    def test_partial_abbreviation_decimal_and_url_not_released(self):
        for text in ('Dr. A. P. J. Raman uses the RP2040 at 3.',
                     'The service guide is https://example.test/rev1.',
                     'According to the local reference, Prof.'):
            buffer=voice.StreamingSpeechBuffer(first_sentence_min_chars=30)
            self.assertEqual(buffer.feed(text),[])

    def test_long_stream_baseline_and_optin_preserve_all_spoken_words(self):
        text=' '.join([SENTENCE]+['The controller checks each measured value before using the result.']*8)
        for minimum in (None,30):
            buffer=voice.StreamingSpeechBuffer(first_sentence_min_chars=minimum)
            spoken=[]
            for character in text: spoken.extend(buffer.feed(character))
            spoken.extend(buffer.feed('',final=True))
            self.assertGreater(len(spoken),1)
            self.assertEqual(' '.join(spoken),text)

    @override_settings(VOICE_INPUT_SOURCE='public-fake',VOICE_STREAM_FIRST_SENTENCE_MIN_CHARS='')
    def test_existing_setting_activation_and_rollback(self):
        self.assertIsNone(voice.VoiceLoopConfig.from_settings().stream_first_sentence_min_chars)
        with override_settings(VOICE_STREAM_FIRST_SENTENCE_MIN_CHARS='30'):
            config=voice.VoiceLoopConfig.from_settings()
            self.assertEqual(config.stream_first_sentence_min_chars,30)
            self.assertEqual(config.stream_first_chunk_min_chars,60)
            self.assertEqual(config.stream_first_chunk_target_chars,100)
        self.assertIsNone(voice.VoiceLoopConfig.from_settings().stream_first_sentence_min_chars)

    def test_safety_and_grounding_sentences_keep_text(self):
        for sentence in ("I cannot confirm that from the supplied evidence.",
                         "The local notes do not contain that information.",
                         "I cannot execute that action without a device result."):
            self.assertTrue(30<=len(sentence)<60)
            buffer=voice.StreamingSpeechBuffer(first_sentence_min_chars=30)
            self.assertEqual(buffer.feed(sentence+' '),[sentence])
            self.assertEqual(buffer.feed('',final=True),[])


class SentencePipelineTests(SimpleTestCase):
    def case(self):
        from assistant.tests_voice_stream import StreamingAudioTests
        case=StreamingAudioTests('test_sentence_plays_before_done_and_generation_continues_during_playback')
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_optin_piper_starts_before_done_baseline_waits_cross_thread_trace(self):
        for minimum in (None,30):
            case=self.case()
            case.config=replace(case.config,stream_first_sentence_min_chars=minimum)
            entered=Event()
            original=case.provider.synthesize.return_value
            case.provider.synthesize.side_effect=lambda text: entered.set() or original
            trace=tracing.Trace(synthetic=True)
            def events():
                yield {'type':'start','conversation_id':42}
                tracing.first_text(SENTENCE)
                yield {'type':'delta','text':SENTENCE+' '}
                if minimum is None:
                    self.assertNotIn('first_speech_unit',trace.counts)
                else:
                    self.assertTrue(entered.wait(2))
                    self.assertIn('tts_start',trace.counts)
                tracing.mark('ollama_complete')
                yield {'type':'done','conversation_id':42,'response':SENTENCE+' '}
            with tracing.cycle(trace), redirect_stdout(io.StringIO()): case.speak(events())
            case.provider.synthesize.assert_called_once_with(SENTENCE)
            names=[e['name'] for e in trace.events]
            if minimum is None: self.assertLess(names.index('ollama_complete'),names.index('tts_start'))
            else: self.assertLess(names.index('tts_start'),names.index('ollama_complete'))
            self.assertEqual(next(e for e in trace.events if e['name']=='tts_start')['lane'],'audio_prepare')
            self.assertEqual(trace.counts['first_complete_sentence'],1)
            self.assertEqual(trace.counts['first_speech_unit'],1)

    def test_existing_empty_failed_cancelled_and_truncated_contracts_under_optin(self):
        methods=(
            'test_malformed_mismatched_and_empty_streams_fail_without_hanging',
            'test_producer_interrupt_cancels_worker_and_closes_stream',
            'test_error_after_first_playback_cancels_active_audio_and_discards_queue',
            'test_incomplete_tail_is_speech_only_after_complete_sentence_playback',
            'test_ai_error_cancels_active_preparation_and_playback_together',
        )
        for name in methods:
            with self.subTest(contract=name):
                case=self.case()
                case.config=replace(case.config,stream_first_sentence_min_chars=30)
                with redirect_stdout(io.StringIO()): getattr(case,name)()
                case.doCleanups()


@override_settings(AI_ENGINE='local',RAG_ENABLED=True,ONLINE_RETRIEVAL_ENABLED=False,
                   DEVICE_CONTROL_ENABLED=False,MQTT_HOST='',RAG_MIN_RELEVANCE=0.1,
                   CONVERSATION_MEMORY_MIN_RELEVANCE=0.1)
class SentenceServiceTests(TestCase):
    def test_option_does_not_change_grounding_history_memory_caps_or_persistence(self):
        from assistant.services import AssistantService
        from assistant.policy import AssistantResponsePolicy
        from assistant.test_support import install_resource_report
        from assistant.ai_engine import AIEngineResult, AIEngineStreamEvent
        from conversations.models import Conversation, Message
        from knowledge_base.models import Document, KnowledgeChunk
        install_resource_report(self)
        document=Document.objects.create(filename='public.md',status='INDEXED',source_identifier='test:public')
        KnowledgeChunk.objects.create(document=document,chunk_index=0,content='The test controller uses GPIO18 for an indicator.')
        def generate(*args,**kwargs):
            yield AIEngineStreamEvent(type='delta',text=SENTENCE)
            yield AIEngineStreamEvent(type='done',result=AIEngineResult(text=SENTENCE,engine='local'))
        engine=Mock(); engine.generate_stream.side_effect=generate
        observed=[]
        for minimum in ('','30'):
            conversation=Conversation.objects.create(title='public test')
            Message.objects.create(conversation=conversation,sender='USER',text='Remember the controller uses GPIO18.')
            Message.objects.create(conversation=conversation,sender='AI',text='Noted.')
            query='Recall GPIO18 controller indicator according to our project notes.'
            with override_settings(VOICE_STREAM_FIRST_SENTENCE_MIN_CHARS=minimum), patch('assistant.services.get_engine',return_value=engine):
                events=list(AssistantService.process_message_stream(query,conversation.id,response_plan=AssistantResponsePolicy.plan_voice_response(query)))
            done=events[-1]
            observed.append((done['response'],done['rag_used'],done['memory_used'],engine.generate_stream.call_args.kwargs,
                             list(Message.objects.filter(conversation=conversation).values_list('sender','text'))))
        self.assertEqual(observed[0],observed[1])
        self.assertTrue(observed[1][1]); self.assertTrue(observed[1][2])

class ResidencyPayloadTests(SimpleTestCase):
    def test_chat_and_startup_warm_paths_keep_same_explicit_retention(self):
        from assistant.ai_engine.local import LocalLLMEngine
        engine=LocalLLMEngine(model='qwen3.5:4b-q4_K_M',keep_alive='24h',num_ctx=4096,think=False)
        chat=json.loads(engine._build_chat_request([{'role':'user','content':'Public test'}],stream=True).data)
        requests=[]
        def open_request(request,**kwargs):
            requests.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'done':True}).encode())
        with patch('assistant.ai_engine.local.urllib.request.urlopen',side_effect=open_request):
            engine.warm_model()
            engine.warm_chat_prefix(system_prompt='',system_instruction='Public safety fixture')
        for payload in [chat]+requests:
            self.assertEqual(payload['model'],'qwen3.5:4b-q4_K_M')
            self.assertEqual(payload['keep_alive'],'24h')
            self.assertEqual(payload['options']['num_ctx'],4096)
        self.assertEqual(len(requests),2)  # Fake explicit warm calls only; no hidden retry/refresh.

class EarlyShortFailureTests(SimpleTestCase):
    def test_short_early_audio_cancels_on_model_error_or_interrupt(self):
        from assistant.tests_voice_stream import StreamingAudioTests
        from assistant.voice.processes import AudioCancelledError
        for interrupt in (False,True):
            case=StreamingAudioTests('test_sentence_plays_before_done_and_generation_continues_during_playback')
            case.setUp()
            played,cancelled,closed=Event(),Event(),Event()
            case.config=replace(case.config,stream_first_sentence_min_chars=30)
            def play(*args,cancel_event):
                played.set()
                if not cancel_event.wait(2): raise AssertionError('Test synchronization failed')
                cancelled.set()
                raise AudioCancelledError('synthetic cancellation')
            case.play_audio.side_effect=play
            def events():
                try:
                    yield {'type':'start','conversation_id':42}
                    yield {'type':'delta','text':SENTENCE+' '}
                    self.assertTrue(played.wait(2))
                    if interrupt: raise KeyboardInterrupt
                    yield {'type':'error','detail':'synthetic model failure'}
                finally:
                    closed.set()
            try:
                with redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt if interrupt else voice.VoiceLoopError):
                    case.speak(events())
                self.assertTrue(cancelled.is_set())
                self.assertTrue(closed.is_set())
                case.provider.synthesize.assert_called_once_with(SENTENCE)
                case.play_audio.assert_called_once()
            finally:
                case.doCleanups()
