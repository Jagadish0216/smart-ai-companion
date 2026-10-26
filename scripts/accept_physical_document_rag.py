"""Opt-in physical acceptance against a disposable snapshot, never production knowledge."""
import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--microphone',action='store_true',help='Exercise actual USB microphone, configured STT/Qwen/TTS and speaker.')
    parser.add_argument('--manage-voice-service',action='store_true',help='Explicitly pause/restore the hardware service if currently active.')
    parser.add_argument('--turns',type=int,default=2)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if not args.microphone or not 1<=args.turns<=10:
        parser.error('Use --microphone and 1..10 turns for hardware acceptance; synthetic tests are separate.')
    if sys.platform!='linux': raise SystemExit('Run physical acceptance on the Pi.')
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1]/'.env',override=False)
    load_dotenv('/etc/smart-ai-companion/demo.env',override=True)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
    from django.conf import settings
    production=Path(settings.DATABASES['default']['NAME']).resolve()
    output=Path(args.output).resolve()
    if not output.is_relative_to(Path(tempfile.gettempdir()).resolve()) or output.exists() or not output.parent.is_dir():
        raise SystemExit('Choose a new report filename under the system temporary directory; production paths are forbidden.')
    if settings.DATABASES['default']['ENGINE']!='django.db.backends.sqlite3': raise SystemExit('SQLite snapshot required.')
    if settings.AI_ENGINE!='local' or settings.OLLAMA_MODEL!='qwen3.5:4b-q4_K_M':
        raise SystemExit('Expected existing local Qwen configuration; no changes will be made.')
    if settings.STT_ENGINE not in ('whisper_cpp','whisper_server') or settings.TTS_ENGINE!='piper': raise SystemExit('Expected configured Whisper and Piper for live audio acceptance.')
    active=subprocess.run(['systemctl','is-active','--quiet','smart-ai-companion-voice.service']).returncode==0
    if active and not args.manage_voice_service: raise SystemExit('Voice service owns microphone. Use --manage-voice-service for a bounded pause and restoration.')
    report={'synthetic':False,'microphone_exercised':True,'production_database_modified':False,'model':settings.OLLAMA_MODEL,'stt':settings.STT_ENGINE,'tts':settings.TTS_ENGINE,'turns':[]}
    def thermal():
        file=Path('/sys/class/thermal/thermal_zone0/temp')
        memory=Path('/proc/meminfo').read_text()
        import re, shutil
        available=re.search(r'MemAvailable:\s+(\d+)',memory)
        throttled=None
        if shutil.which('vcgencmd'):
            value=subprocess.run(['vcgencmd','get_throttled'],capture_output=True,text=True,timeout=2)
            if value.returncode==0: throttled=value.stdout.strip()
        return {'temperature_c':int(file.read_text())/1000 if file.exists() else None,
                'memory_available_bytes':int(available[1])*1024 if available else None,'throttle_flags':throttled}
    tts_cleanup=None
    try:
        if active: subprocess.run(['sudo','systemctl','stop','smart-ai-companion-voice.service'],check=True)
        with tempfile.TemporaryDirectory(prefix='physical-rag-acceptance-') as directory:
            target=Path(directory)/'acceptance.sqlite3'
            with sqlite3.connect(production.as_uri()+'?mode=ro',uri=True) as source, sqlite3.connect(target) as snapshot:
                source.backup(snapshot)
            source.close();snapshot.close()
            settings.DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':str(target)}}
            settings.MEDIA_ROOT=str(Path(directory)/'media')
            settings.ONLINE_RETRIEVAL_ENABLED=False;settings.ONLINE_KNOWLEDGE_AUTO_PERSIST=False
            settings.COMPANION_DISPLAY_ENABLED=False
            settings.PHYSICAL_VOICE_DOCUMENTS_ENABLED=True
            settings.PHYSICAL_VOICE_SESSION_KEY='hardware-acceptance'
            import django
            django.setup()
            from django.db import connections
            from django.core.management import call_command
            from assistant.latency_trace import Trace,cycle
            from assistant.voice.rag_metrics import summarize
            from assistant.voice.push_to_talk import microphone_ownership
            from scripts.voice_loop import VoiceLoopConfig,run_voice_cycle
            # Migrate only the private copied DB, including pre-release baselines.
            call_command('migrate',interactive=False,verbosity=0)
            from assistant.voice.factory import close_tts_provider
            tts_cleanup=close_tts_provider
            from conversations.models import Message
            from dataclasses import replace
            config=replace(VoiceLoopConfig.from_settings(),stream_eager_first_sentence=True,voice_refinements=True)
            from assistant.voice.factory import get_tts_provider
            get_tts_provider().warm()
            conversation=None
            with microphone_ownership():
                for number in range(args.turns):
                    print(f'Wait through the configured {config.input_warmup_seconds:g}s microphone warm-up, then speak a document question. Later turns may ask a follow-up.',flush=True)
                    trace=Trace(synthetic=False);before=thermal()
                    try:
                        with cycle(trace),redirect_stdout(io.StringIO()): conversation=run_voice_cycle(config,conversation)
                        message=Message.objects.filter(conversation_id=conversation,sender='AI').last()
                        sources=(message.source_metadata or {}).get('rag_sources',[]) if message else []
                        report['turns'].append({'turn':number+1,'latency':summarize(trace.report()),'trace':trace.report(),'response_truncated':bool((message.source_metadata or {}).get('response_truncated')) if message else None,'grounded_document_ids':sorted({s['document_id'] for s in sources if s.get('document_id')}),'citations':[{key:source.get(key) for key in ('document_id','title','page_number','chunk_index')} for source in sources],'thermal_before':before,'thermal_after':thermal()})
                    except Exception as exc:
                        report['turns'].append({'turn':number+1,'error_type':type(exc).__name__,'latency':summarize(trace.report()),'trace':trace.report()})
                        raise
            close_tts_provider();connections.close_all()
    finally:
        if tts_cleanup:
            try: tts_cleanup()
            except Exception: report['tts_cleanup_failed']=True
        from django.db import connections
        connections.close_all()
        try:
            with output.open('x',encoding='utf-8') as stream: json.dump(report,stream,indent=2)
        finally:
            if active: subprocess.run(['sudo','systemctl','start','smart-ai-companion-voice.service'],check=True)
    if not report['turns'] or any(not turn.get('grounded_document_ids') for turn in report['turns']):
        raise SystemExit('Audio path ran, but not every turn had document evidence. Review the report and spoken answers.')
    print('Physical audio path completed with source attribution. Human listening/answer-quality review still required.')

if __name__=='__main__': main()
