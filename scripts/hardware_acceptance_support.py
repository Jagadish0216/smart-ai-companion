"""Read-only runtime evidence helpers for the file-backed hardware acceptance tool."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import urllib.request
from urllib.parse import urlsplit


def decode_throttle(value):
    raw=int(value.strip().split('=')[-1],16)
    return {'raw':hex(raw),'current_under_voltage':bool(raw&1),'current_frequency_capped':bool(raw&2),
            'current_throttled':bool(raw&4),'current_soft_temperature_limit':bool(raw&8),
            'historical_under_voltage':bool(raw&(1<<16)),'historical_frequency_capped':bool(raw&(1<<17)),
            'historical_throttled':bool(raw&(1<<18)),'historical_soft_temperature_limit':bool(raw&(1<<19))}


def resources():
    import shutil, re
    temp=Path('/sys/class/thermal/thermal_zone0/temp');mem=Path('/proc/meminfo')
    available=re.search(r'MemAvailable:\s+(\d+)',mem.read_text()) if mem.exists() else None
    result={'temperature_c':int(temp.read_text())/1000 if temp.exists() else None,
            'memory_available_bytes':int(available[1])*1024 if available else None,
            'load_average':list(os.getloadavg()) if hasattr(os,'getloadavg') else None,
            'cpu_count':os.cpu_count(),'nice':os.getpriority(os.PRIO_PROCESS,0) if hasattr(os,'getpriority') else None}
    if shutil.which('vcgencmd'):
        value=subprocess.run(['vcgencmd','get_throttled'],capture_output=True,text=True,timeout=3)
        if value.returncode==0: result['throttle']=decode_throttle(value.stdout)
    return result


def ollama_probe(host):
    parsed=urlsplit(host)
    if parsed.hostname not in ('localhost','127.0.0.1','::1') or parsed.username or parsed.password:
        raise ValueError('Expected existing local Ollama endpoint')
    report={}
    for name in ('ps','version'):
        try:
            with urllib.request.urlopen(host.rstrip('/')+'/api/'+name,timeout=3) as response:
                report[name]=json.loads(response.read(1024*1024))
        except Exception as exc: report[name]={'error_type':type(exc).__name__}
    return report


def snapshot_database(source, destination):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if source==destination or destination.exists(): raise ValueError('Snapshot must be a new private file')
    tables=['knowledge_base_document','knowledge_base_knowledgechunk','knowledge_base_evidenceversion','conversations_conversation','conversations_message','auth_user']
    original=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
    copy=sqlite3.connect(destination)
    try:
        original.execute('BEGIN')
        before={table:original.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in tables}
        original.backup(copy)
        if copy.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Snapshot integrity check failed')
        after={table:copy.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in tables}
        if before!=after: raise ValueError('Snapshot data counts differ')
        return {'status':'PASS','integrity_check':'ok','counts':after,'production_open_mode':'ro'}
    finally:
        copy.close();original.close()


def serial_owners(port):
    import shutil
    if not shutil.which('fuser'): return {'status':'PARTIAL','reason':'fuser unavailable'}
    result=subprocess.run(['fuser','--',str(Path(port).resolve())],capture_output=True,text=True,timeout=3)
    return {'status':'PASS' if result.returncode in (0,1) else 'PARTIAL',
            'pids':[int(item) for item in result.stdout.split() if item.isdigit()]}


def compare_runs(before, after):
    if before.get('synthetic') or after.get('synthetic'):
        return {'status':'PARTIAL','reason':'Synthetic measurements cannot establish Pi speedup'}
    keys=('questions','model','context_limit')
    if any(before.get(key)!=after.get(key) for key in keys) or before.get('snapshot',{}).get('counts')!=after.get('snapshot',{}).get('counts'):
        return {'status':'PARTIAL','reason':'Inputs/model/context/data differ; reference only'}
    if len(before.get('turns',[]))!=len(after.get('turns',[])):
        return {'status':'PARTIAL','reason':'Turn counts differ'}
    pairs=[]
    for old,new in zip(before.get('turns',[]),after.get('turns',[])):
        if old.get('citations')!=new.get('citations'): return {'status':'PARTIAL','reason':'Retrieved evidence differs'}
        controls=('num_ctx','requested_num_predict','think_enabled','num_thread','num_batch','keep_alive')
        if any(old.get('ollama_metrics',{}).get(key)!=new.get('ollama_metrics',{}).get(key) for key in controls):
            return {'status':'PARTIAL','reason':'Runtime controls differ'}
        pairs.append({'before_ms':old['latency']['total_cycle_ms'],'after_ms':new['latency']['total_cycle_ms']})
    return {'status':'PASS' if pairs else 'PARTIAL','pairs':pairs,'meaning':'Observed comparable runs, not proof of causation or acoustic onset.'}
