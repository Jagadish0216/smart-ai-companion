"""Synthetic inference, real disposable SQLite timing; never a Pi latency claim."""
import os, sys, tempfile, time, json, statistics
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE']='config.test_settings'
from django.conf import settings
with tempfile.TemporaryDirectory() as directory:
 settings.DATABASES['default']['NAME']=str(Path(directory)/'bench.sqlite3')
 settings.MEDIA_ROOT=str(Path(directory)/'media')
 settings.AI_ENGINE='local'; settings.RAG_ENABLED=True
 import django
 django.setup()
 from django.core.management import call_command
 from django.db import connection
 from django.test.utils import CaptureQueriesContext
 from knowledge_base.services import KnowledgeIngestionService
 from assistant.services import AssistantService
 from assistant.ai_engine import AIEngineResult, AIEngineStreamEvent
 from assistant.policy import AssistantResponsePolicy
 from assistant.latency_trace import Trace, cycle
 call_command('migrate',verbosity=0)
 ingest=KnowledgeIngestionService()
 ingest.ingest_text('Objective one: reduce power use. Objective two: improve local privacy.',filename='Project_Report.md',file_type='text/markdown',source_identifier='document:benchmark')
 report={'recommended_profile':'BALANCED','ai_recommendation':{'local_ai_allowed':True,'online_allowed':False,'generation_budget':'NORMAL'}}
 class Engine:
  def generate_stream(self,*args,**kwargs):
   self.prompt_chars=len(kwargs.get('system_instruction',''));self.budget=kwargs.get('num_predict')
   result=AIEngineResult('The second objective is to improve local privacy.',engine='mock',model='simulated',latency_ms=0)
   yield AIEngineStreamEvent('delta',text=result.text);yield AIEngineStreamEvent('done',result=result)
 records=[]
 for index in range(5):
  trace=Trace(synthetic=True);engine=Engine()
  with patch('assistant.execution_policy.get_resource_manager_report',return_value=report),patch('assistant.services.get_engine',return_value=engine),cycle(trace),CaptureQueriesContext(connection) as queries:
   started=time.perf_counter();events=list(AssistantService.process_message_stream('Explain the second objective from my project report.',response_plan=AssistantResponsePolicy.plan_voice_response('Explain the second objective from my project report.'), **({'voice_session_key':'benchmark'} if '--physical' in sys.argv else {})));elapsed=(time.perf_counter()-started)*1000
  records.append({'elapsed_ms':round(elapsed,3),'sql_queries':len(queries),'prompt_chars':getattr(engine,'prompt_chars',None),'output_budget':getattr(engine,'budget',None),'route':events[-1].get('route'),'sources':len(events[-1].get('rag_sources',[])),'trace':trace.report(), 'stages':__import__('assistant.voice.rag_metrics',fromlist=['summarize']).summarize(trace.report())})
 print(json.dumps({'synthetic':True,'inference':'mocked; no microphone/STT/TTS/thermal measurements','median_service_ms':statistics.median(r['elapsed_ms'] for r in records),'records':records},indent=2))

 from django.db import connections
 connections.close_all()
