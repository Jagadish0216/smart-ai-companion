"""Opt-in service-level Pi verification; never controls physical hardware."""
import json
import time
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings
from assistant.execution_policy import get_ai_execution_plan
from assistant.policy import AssistantResponsePolicy
from assistant.services import AssistantService
from assistant.ai_engine import get_engine

QUESTIONS = (
    'Explain how a Raspberry Pi 5 works.',
    'What is the latest Raspberry Pi OS version as of October 24, 2026?',
    'What are the latest Raspberry Pi news updates today, October 24, 2026?',
)


class Command(BaseCommand):
    help='Run three public examples and optional warm voice comparison. Creates normal conversation rows, never resets data.'

    def add_arguments(self,parser):
        parser.add_argument('--warm-voice',action='store_true')

    def handle(self,*args,**options):
        cases=[(q,False) for q in QUESTIONS]
        if options['warm_voice']:
            cases.append((QUESTIONS[0],True))
        # Verification must not ingest retrieved data into knowledge storage.
        with override_settings(ONLINE_KNOWLEDGE_AUTO_PERSIST=False):
            for query,voice in cases:
                plan=get_ai_execution_plan()
                if not plan.generation_allowed or plan.resource_profile not in {'BALANCED','PERFORMANCE'}:
                    raise CommandError('Verification stopped: resource policy is not healthy for a benchmark.')
                start=time.perf_counter()
                if voice:
                    done=None
                    for event in AssistantService.process_message_stream(query,response_plan=AssistantResponsePolicy.plan_voice_response(query)):
                        if event['type']=='error':
                            raise CommandError('Voice generation failed; see service health.')
                        if event['type']=='done':
                            done=event
                    if done is None:
                        raise CommandError('No completion metadata.')
                    text,metadata,error=done['response'],done,None
                else:
                    _,text,metadata,error=AssistantService.process_message(query)
                if error or metadata is None:
                    raise CommandError('Verification generation failed.')
                self.stdout.write(json.dumps({
                    'question':query,'voice':voice,'route':metadata.get('route'),'response':text,
                    'sources':metadata.get('online_sources',[]),'evidence_used':metadata.get('online_used',False),
                    'fallback_reason':metadata.get('fallback_reason'),'online_success':metadata.get('online_success'),
                    'wall_ms':round((time.perf_counter()-start)*1000),'generation_ms':metadata.get('generation_ms'),
                    'request_ttft_ms':metadata.get('request_ttft_ms'),'online_ms':metadata.get('online_latency_ms'),
                    'model':metadata.get('model'),'num_predict':metadata.get('num_predict'),
                    'resource_profile':metadata.get('resource_profile'),'ollama_metrics':metadata.get('ollama_metrics',{}),
                    'thinking_requested':getattr(get_engine(),'_think',None),
                },ensure_ascii=True))
                if query==QUESTIONS[0] and metadata.get('route')!='local':
                    raise CommandError('General explanation did not route locally.')
