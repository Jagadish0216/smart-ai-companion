(() => {
 'use strict';
 const put = (id,value) => { const el=document.getElementById(id); if(el) el.textContent=value; };
 const known = v => typeof v === 'string' && v ? v.replaceAll('_',' ').toLowerCase() : 'Status unavailable';
 let metricsBusy=false;
 async function metrics() {
   if(metricsBusy || document.hidden) return; metricsBusy=true;
   try {
     const r=await fetch('/api/system/metrics/',{cache:'no-store',signal:AbortSignal.timeout(10000)});
     if(!r.ok) throw new Error(); const d=await r.json();
     for(const [key,field,unit] of [['cpu','cpu_percent','%'],['ram','ram_percent','%'],['temp','temperature_c','°C'],['storage','storage_percent','%']]) {
       const v=d[field]; put('ov-'+key,typeof v==='number'?v+unit:'Unavailable');
       put('dev-'+key,typeof v==='number'?v+unit:'Unavailable');
       const devBar=document.getElementById('dev-'+key+'-bar'); if(devBar) devBar.style.width=typeof v==='number'?Math.max(0,Math.min(100,key==='temp'?v/85*100:v))+'%':'0%';
       const bar=document.getElementById('ov-'+key+'-bar'); if(bar) bar.style.width=typeof v==='number'?Math.max(0,Math.min(100,key==='temp'?v/85*100:v))+'%':'0%';
     }
     // The API currently reports a combined audio status; it cannot prove microphone or speaker readiness independently.
     put('readiness-input',d.audio_status==='NOT_CONFIGURED'?'Not configured':'Status unavailable');
     put('readiness-output',d.audio_status==='NOT_CONFIGURED'?'Not configured':'Status unavailable');
     put('readiness-display',known(d.display_status));
     put('readiness-connection','Device API reachable');
     put('dev-device-name',d.device_name || 'Device name unavailable'); put('dev-status-badge',known(d.status));
     put('dev-network',known(d.network)); put('dev-audio',known(d.audio_status));put('dev-display',known(d.display_status));put('dev-total-ram',typeof d.ram_total_gb==='number'?d.ram_total_gb+' GB':'Unavailable');
   } catch(_) { ['cpu','ram','temp','storage'].forEach(k=>{put('ov-'+k,'Unavailable');put('dev-'+k,'Unavailable');}); put('dev-status-badge','Telemetry unavailable'); ['input','output','display','connection'].forEach(k=>put('readiness-'+k,'Status unavailable')); }
   finally {metricsBusy=false;}
 }
 async function readiness() {
   const configured=document.getElementById('global-ai-engine')?.textContent.trim()==='LOCAL AI';
   if (!configured) {document.querySelectorAll('[data-ai-readiness]').forEach(el=>{el.textContent='Development AI configured';}); return;}
   try {
     const r=await fetch('/api/system/services/',{cache:'no-store',signal:AbortSignal.timeout(10000)}); if(!r.ok) throw new Error();
     const d=await r.json(); const service=(d.services || d).ollama;

     document.querySelectorAll('[data-ai-readiness]').forEach(el=>{el.textContent=!configured?'Development AI configured':service?.status==='READY'?'Local AI service available':service?.status==='UNKNOWN'?'AI status unavailable':'Local AI unavailable';});
   } catch(_) {document.querySelectorAll('[data-ai-readiness]').forEach(el=>{el.textContent='AI status unavailable';});}
 }
 metrics(); readiness(); setInterval(metrics,5000);
})();
