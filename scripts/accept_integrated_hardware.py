#!/usr/bin/env python3
"""File-backed real text -> RAG/Qwen -> Piper/speaker + ESP32 acceptance. No microphone."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts.hardware_acceptance_support import snapshot_database,resources,ollama_probe,serial_owners,compare_runs


def load_environment():
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env',override=False)
    load_dotenv('/etc/smart-ai-companion/demo.env',override=True)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')


def write_json(path,data):
    temporary=Path(str(path)+'.partial')
    temporary.write_text(json.dumps(data,indent=2),encoding='utf-8');temporary.replace(path)


def worker(request_path):
    load_environment()
    request=json.loads(Path(request_path).read_text())
    from django.conf import settings
    production=Path(settings.DATABASES['default']['NAME']).resolve()
    database=Path(request['database']).resolve()
    if database==production or not database.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise ValueError('Worker requires a private snapshot')
    settings.DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':str(database)}}
    settings.MEDIA_ROOT=str(database.parent/'media')
    settings.ONLINE_RETRIEVAL_ENABLED=False;settings.ONLINE_KNOWLEDGE_AUTO_PERSIST=False
    settings.PHYSICAL_VOICE_DOCUMENTS_ENABLED=True
    settings.PHYSICAL_VOICE_SESSION_KEY=request['session_key']
    # Do not disable the actual display, change serial settings or substitute inference/audio.
    import django
    django.setup()
    from django.db import connection,connections
    from django.db.migrations.executor import MigrationExecutor
    executor=MigrationExecutor(connection)
    if executor.migration_plan(executor.loader.graph.leaf_nodes()): raise RuntimeError('Snapshot has unapplied migrations; deploy the reviewed release first')
    from assistant.display import get_companion_display,DisplayState
    from assistant.voice.factory import get_tts_provider,close_tts_provider
    from assistant.voice.push_to_talk import microphone_ownership
    from assistant.latency_trace import Trace,cycle
    from assistant.voice.rag_metrics import summarize
    from scripts.voice_loop import VoiceLoopConfig,run_voice_cycle
    from conversations.models import Message
    from dataclasses import replace
    display=None;report={'turns':[],'subsystems':{}}
    def interrupted(signum,frame): raise KeyboardInterrupt('Acceptance interrupted')
    signal.signal(signal.SIGTERM,interrupted)
    try:
        with microphone_ownership():
            display=get_companion_display(nonblocking=True,capture=True)
            if not display.enabled: raise RuntimeError('Configured companion display is disabled/unavailable')
            display.set_state(DisplayState.READY)
            display.flush(timeout=3)
            report['display_start']=display.evidence()
            if not report['display_start'].get('protocol_supported'):
                raise RuntimeError('Connected firmware HELP does not confirm the required display protocol')
            get_tts_provider().warm()
            config=replace(VoiceLoopConfig.from_settings(require_capture=False),stream_eager_first_sentence=True,voice_refinements=True)
            conversation=None
            for question in request['questions']:
                before=resources();residency=ollama_probe(settings.OLLAMA_HOST);trace=Trace(synthetic=False);metadata={}
                def completed(event): metadata.update(event)
                prior=len(display.evidence()['audit'])
                try:
                    with cycle(trace):
                        conversation=run_voice_cycle(config,conversation,display=display,text=question,on_result=completed)
                    flushed=display.flush(timeout=3)
                    message=Message.objects.filter(conversation_id=conversation,sender='AI').last()
                    sources=(message.source_metadata or {}).get('rag_sources',[]) if message else []
                    audit=display.evidence()
                    turn={'question':question,'response':message.text if message else metadata.get('response'),
                          'citations':[{key:source.get(key) for key in ('document_id','title','page_number','chunk_index')} for source in sources],
                          'latency':summarize(trace.report()),'trace':trace.report(),
                          'ollama_metrics':metadata.get('ollama_metrics',{}),'ttft_ms':metadata.get('ttft_ms'),
                          'response_truncated':metadata.get('response_truncated',False),
                          'resources_before':before,'resources_after':resources(),'ollama_before':residency,
                          'display_flushed':flushed,'display_commands':audit['audit'][prior:]}
                    report['turns'].append(turn)
                    write_json(request['result'],report)
                except BaseException as exc:
                    report['turns'].append({'question':question,'error_type':type(exc).__name__,'trace':trace.report(),'latency':summarize(trace.report())})
                    raise
            report['display']=display.evidence()
            report['ollama_after']=ollama_probe(settings.OLLAMA_HOST)
    except BaseException as exc:
        report['error_type']=type(exc).__name__
        if display is not None: report['display']=display.evidence()
        raise
    finally:
        try: close_tts_provider()
        finally:
            try:
                if display is not None:
                    display.set_state(DisplayState.READY);display.flush(timeout=2);display.close()
                    report['display_after_close']=display.evidence()
            finally:
                connections.close_all();write_json(request['result'],report)


def assess(report, expect_file=None):
    turns=report.get('turns',[])
    complete=not report.get('error_type') and report.get('worker_exit_code',0)==0 and len(turns)==len(report.get('questions',turns)) and len(turns)>=2 and all(turn.get('response') and not turn.get('error_type') for turn in turns)
    grounded=complete and all(turn.get('citations') for turn in turns)
    if expect_file: grounded=grounded and all(any(source.get('title')==expect_file for source in turn.get('citations',[])) for turn in turns)
    played=complete and all(turn.get('latency',{}).get('first_playback_launch_ms') is not None for turn in turns)
    display=report.get('display',{});counts=display.get('counts',{})
    written=counts.get('written',0)>0;acked=written and counts.get('ack_timeout',0)==0 and counts.get('rejected',0)==0 and counts.get('acknowledged',0)==counts.get('written',0)
    lifecycle=complete and all(any(item.get('command')=='STATE THINKING' for item in turn.get('display_commands',[])) and any(item.get('command')=='STATE SPEAKING' for item in turn.get('display_commands',[])) and any(item.get('command') in ('STATE READY','STATE ECO','STATE PROTECTIVE') for item in turn.get('display_commands',[])) and any(item.get('command')=='MODE KNOWLEDGE' for item in turn.get('display_commands',[])) and any(item.get('command','').startswith('QUERY ') for item in turn.get('display_commands',[])) and any(item.get('command','').startswith('TEXT ') for item in turn.get('display_commands',[])) and turn.get('display_flushed') for turn in turns)
    return {'rag':{'status':'PASS' if grounded else 'FAIL'},'qwen':{'status':'PASS' if complete else 'FAIL'},
            'piper_speaker_handoff':{'status':'PASS' if played else 'FAIL','acoustic_output':'unobserved'},
            'serial_writes':{'status':'PASS' if written else 'FAIL'},
            'firmware_acknowledgments':{'status':'PASS' if acked else 'PARTIAL' if written else 'FAIL','sequence_ids':'not supported by legacy firmware'},
            'display_lifecycle_protocol':{'status':'PASS' if lifecycle else 'FAIL'},
            'tft_visible_rendering':{'status':'PARTIAL','reason':'Requires operator/hardware observation; serial replies are not pixels'},
            'answer_quality':{'status':'PARTIAL','reason':'Review response text against cited source'},
            'microphone_whisper':{'status':'PARTIAL','reason':'Intentionally not exercised by text injection'}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--question',action='append',default=[])
    parser.add_argument('--expect-file')
    parser.add_argument('--output')
    parser.add_argument('--baseline')
    parser.add_argument('--manage-services',action='store_true')
    parser.add_argument('--display-owner-unit',action='append',default=[])
    parser.add_argument('--timeout',type=int,default=600)
    parser.add_argument('--confirm-visual',action='store_true')
    parser.add_argument('--confirm-answers',action='store_true')
    parser.add_argument('--worker',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.worker: worker(args.worker);return 0
    if sys.platform!='linux': parser.error('Run real hardware acceptance on the Pi')
    if not 2<=len(args.question)<=8 or any(not 1<=len(question)<=1000 for question in args.question): parser.error('Provide two to eight questions, at most 1000 characters each')
    if not args.output or not 30<=args.timeout<=1800: parser.error('A new /tmp output and a 30..1800 second timeout are required')
    load_environment()
    from django.conf import settings
    if settings.AI_ENGINE!='local' or settings.OLLAMA_MODEL!='qwen3.5:4b-q4_K_M' or settings.TTS_ENGINE!='piper': raise RuntimeError('Expected existing local Qwen/Piper configuration')
    if not settings.COMPANION_DISPLAY_ENABLED or not settings.COMPANION_DISPLAY_PORT: raise RuntimeError('Enable/configure the real display through the existing hardware configuration before acceptance')
    output=Path(args.output).resolve()
    if not output.is_relative_to(Path(tempfile.gettempdir()).resolve()) or output.exists() or not output.parent.is_dir(): raise ValueError('Choose a new report path under /tmp')
    if output.is_relative_to(ROOT) or output==Path(settings.DATABASES['default']['NAME']).resolve(): raise ValueError('Production output paths are forbidden')
    units=['smart-ai-companion-voice.service']+args.display_owner_unit
    import re,uuid
    if any(not re.fullmatch(r'smart-ai-companion-[a-z0-9-]+\.service',unit) for unit in units): raise ValueError('Unsupported owner unit')
    active=[unit for unit in dict.fromkeys(units) if subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0]
    if active and not args.manage_services: raise RuntimeError('Voice/display owner services are active; use --manage-services for a bounded handover')
    report={'synthetic':False,'questions':args.question,'model':settings.OLLAMA_MODEL,'context_limit':settings.OLLAMA_NUM_CTX,
            'subsystems':{},'services_before':active,'serial_owners_before':serial_owners(settings.COMPANION_DISPLAY_PORT)}
    process=None
    def interrupted(signum,frame): raise KeyboardInterrupt('Acceptance interrupted')
    previous=signal.signal(signal.SIGTERM,interrupted)
    handle=os.fdopen(os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w',encoding='utf-8')
    try:
        for unit in active: subprocess.run(['sudo','systemctl','stop',unit],check=True,timeout=30)
        owners=serial_owners(settings.COMPANION_DISPLAY_PORT);report['serial_owners_during']=owners
        if owners.get('status')!='PASS': raise RuntimeError('Serial ownership could not be verified; fuser is required for safe handover')
        if owners.get('pids'): raise RuntimeError('An unrecognized process still owns the configured serial device; no process was killed')
        with tempfile.TemporaryDirectory(prefix='companion-full-acceptance-') as directory:
            directory=Path(directory);db=directory/'acceptance.sqlite3';result=directory/'result.json'
            report['snapshot']=snapshot_database(settings.DATABASES['default']['NAME'],db)
            request={'database':str(db),'result':str(result),'questions':args.question,'session_key':'acceptance-'+uuid.uuid4().hex}
            request_path=directory/'request.json';write_json(request_path,request)
            with (directory/'worker.log').open('w') as log:
                process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker',str(request_path)],stdout=log,stderr=log,start_new_session=True)
                try: process.wait(timeout=args.timeout)
                except BaseException:
                    os.killpg(process.pid,signal.SIGTERM)
                    try: process.wait(timeout=15)
                    except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
                    raise
                finally:
                    if result.exists(): report.update(json.loads(result.read_text()))
            report['worker_exit_code']=process.returncode
            report['subsystems'].update(assess(report,args.expect_file))
            report['subsystems']['snapshot']={'status':'PASS'}
            if args.confirm_visual:
                visible=input('Did the TFT visibly show query, KNOWLEDGE, matching spoken captions, SPEAKING then READY (or the appropriate thermal idle state)? [yes/no] ').strip().lower()=='yes'
                report['subsystems']['tft_visible_rendering']={'status':'PASS' if visible else 'FAIL','assurance':'operator reported, not automated pixel verification'}
            if args.confirm_answers:
                verified=input('Were both answers grounded in the cited material and audibly spoken correctly? [yes/no] ').strip().lower()=='yes'
                report['subsystems']['answer_quality']={'status':'PASS' if verified else 'FAIL','assurance':'operator reported content and acoustic observation'}
            if args.baseline: report['comparison']=compare_runs(json.loads(Path(args.baseline).read_text()),report)
    except BaseException as exc:
        report['error_type']=type(exc).__name__;report['status']='FAIL'
    finally:
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        prior_int=signal.signal(signal.SIGINT,signal.SIG_IGN)
        restoration=[]
        for unit in active:
            try:
                subprocess.run(['sudo','systemctl','start',unit],check=True,timeout=30)
                ok=subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0
                restoration.append({'unit':unit,'restored':ok})
            except Exception as exc: restoration.append({'unit':unit,'restored':False,'error_type':type(exc).__name__})
        report['services_after']=restoration
        report['subsystems']['service_restoration']={'status':'PASS' if all(item['restored'] for item in restoration) else 'FAIL'}
        if 'status' not in report:
            statuses=[value['status'] for key,value in report['subsystems'].items() if key!='microphone_whisper']
            report['status']='FAIL' if 'FAIL' in statuses else 'PARTIAL' if 'PARTIAL' in statuses else 'PASS'
        if any(not item['restored'] for item in restoration): report['status']='FAIL'
        json.dump(report,handle,indent=2);handle.close();signal.signal(signal.SIGTERM,previous);signal.signal(signal.SIGINT,prior_int)
    print(report['status']+': '+str(output))
    return {'PASS':0,'PARTIAL':2,'FAIL':1}[report['status']]

if __name__=='__main__': raise SystemExit(main())
