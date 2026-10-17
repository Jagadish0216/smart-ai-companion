const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs');
const baseURL=process.env.DASHBOARD_BASE_URL || 'http://127.0.0.1:8000';
fs.mkdirSync('docs/screenshots',{recursive:true});
const assert=require('assert/strict');
(async()=>{
 const b=await chromium.launch({headless:true,channel:'msedge'});
 const p=await b.newPage({viewport:{width:1440,height:960},reducedMotion:'reduce'});
 const errors=[], results=[];
 p.on('pageerror',e=>errors.push(e.message));
 p.on('response',r=>{if(r.status()>=400&&!r.url().includes('/setup/'))errors.push(r.status()+' '+r.url());});
 async function visit(path){const r=await p.goto(baseURL+path);assert.equal(r.status(),200,path);await p.waitForTimeout(500);assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,path+' overflow');}
 async function shot(name){await p.screenshot({path:'docs/screenshots/'+name+'.png'});}
 for(const [name,url] of [['home','/'],['assistant-empty','/assistant/?new=1'],['memory','/knowledge/'],['device','/device/']]){await visit(url);if(name==='memory'){await p.locator('.document-row').first().click();await p.locator('#memory-search').fill('no-such-file');assert.match(await p.locator('#knowledge-documents').innerText(),/No files match/);await p.locator('#memory-search').fill('');await p.locator('#memory-search').blur();}await shot(name);results.push(name+' desktop: 200, no overflow');}
 for(const path of ['/conversations/','/resource-manager/','/network/','/logs/','/settings/','/studio/']){await visit(path);results.push(path+' 200');}
 await p.locator('.rail-link').last().click();assert(await p.locator('#more-menu').isVisible());await p.keyboard.press('Escape');assert(!(await p.locator('#more-menu').isVisible()));
 await visit('/assistant/?new=1');await p.locator('#history-trigger').click();assert(await p.locator('#chat-history-sidebar').evaluate(el=>!el.inert));await p.keyboard.press('Escape');assert.equal(await p.evaluate(()=>document.activeElement.id),'history-trigger');
 // Load an existing conversation from the isolated copy, without creating history.
 await p.locator('#history-trigger').click();if(await p.locator('.conv-link').count()){await p.locator('.conv-link').first().click();await p.waitForTimeout(400);assert(await p.locator('.chat-msg').count()>0);await shot('assistant-conversation');}
 for(const width of [390,430]){await visit('/assistant/');await p.locator('#history-trigger').click();if(await p.locator('.conv-link').count()){await p.locator('.conv-link').first().click();await p.waitForTimeout(300);}await p.setViewportSize({width,height:844});await shot('assistant-mobile-'+width);assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'mobile overflow');for(const sel of ['#btn-voice','#btn-submit']){const box=await p.locator(sel).boundingBox();assert(box.width>=44&&box.height>=44,sel+' target');assert(box.y+box.height<768,sel+' above navigation');}await visit('/assistant/?new=1');await shot('assistant-mobile-empty-'+width);results.push(width+'px: no overflow; 44px composer controls');}
 await p.setViewportSize({width:1440,height:960});await visit('/assistant/?new=1');
 // Controlled in-browser stream fixture tests presentation without writing to the DB.
 await p.evaluate(()=>{const original=window.fetch;window.fetch=(url,options)=>{
  if(String(url).includes('/chat/stream/')){const encoder=new TextEncoder();let i=0;const stream=new ReadableStream({start(controller){const emit=()=>{if(options.signal.aborted){controller.error(new DOMException('Aborted','AbortError'));return;}if(i<40){controller.enqueue(encoder.encode(JSON.stringify({type:'delta',text:'Verification line '+(++i)+' — '+ 'content '.repeat(18)+'\n\n'})+'\n'));setTimeout(emit,70);}else{controller.enqueue(encoder.encode(JSON.stringify({type:'done',rag_sources:[{filename:'Browser test fixture.md'}]})+'\n'));controller.close();}};emit();}});return Promise.resolve(new Response(stream,{headers:{'Content-Type':'application/x-ndjson'}}));}return original(url,options);};});
 await p.locator('#chat-input').fill('Browser validation fixture');await p.locator('#btn-submit').click();await p.waitForTimeout(1200);await p.locator('#chat-history').evaluate(el=>{el.scrollTop=0;el.dispatchEvent(new Event('scroll'));});await p.waitForTimeout(350);assert.equal(await p.locator('#chat-history').evaluate(el=>el.scrollTop),0,'reading position preserved');assert(await p.locator('#jump-latest').isVisible());await p.locator('#jump-latest').click();await p.locator('#interrupt-response').click();await p.waitForTimeout(200);assert(await p.locator('#chat-input').isEnabled());assert.match(await p.locator('.chat-msg.ai').innerText(),/interrupted/i);results.push('Controlled stream fixture: reading position, jump to latest, interrupt and input recovery pass');
 await p.locator('#history-trigger').click();await p.getByRole('button',{name:'New Chat'}).click();assert(await p.locator('#assistant-empty').isVisible());await p.locator('#chat-input').fill('line one');await p.locator('#chat-input').press('Shift+Enter');assert.equal(await p.locator('#chat-input').inputValue(),'line one\n');
 // Mock only microphone and playback capabilities; exercise the real UI event handlers.
 await p.evaluate(() => {
   navigator.mediaDevices.getUserMedia=async()=>({getTracks:()=>[{stop(){}}]});
   window.MediaRecorder=class extends EventTarget {static isTypeSupported(){return true;}constructor(){super();this.mimeType='audio/webm';this.state='inactive';}start(){this.state='recording';}stop(){this.state='inactive';const event=new Event('dataavailable');Object.defineProperty(event,'data',{value:new Blob(['fixture'],{type:'audio/webm'})});this.dispatchEvent(event);this.dispatchEvent(new Event('stop'));}};
   window.Audio=class {play(){return Promise.resolve();}pause(){}};
 });
 await p.route('**/api/assistant/voice/transcribe/',route=>route.fulfill({json:{transcription:'Voice fixture question',response:'Voice fixture answer',audio_base64:'fixture',rag_sources:[{title:'Fixture source'}]}}));
 await p.locator('#btn-voice').click();await p.waitForTimeout(50);assert.match(await p.locator('#voice-status').innerText(),/Listening/);
 await p.locator('#btn-voice').click();await p.waitForFunction(()=>document.getElementById('voice-status').textContent==='Speaking',{},{timeout:5000}).catch(async e=>{console.log('VOICE DEBUG',await p.locator('.chat-messages-container').innerText(),await p.locator('#toast-container').innerText(),errors);throw e;});assert.match(await p.locator('#voice-status').innerText(),/Speaking/);assert.match(await p.locator('.chat-messages-container').innerText(),/Voice fixture answer/);assert.match(await p.locator('.message-sources').innerText(),/Fixture source/);
 await p.locator('#interrupt-response').click();assert.match(await p.locator('#voice-status').innerText(),/IDLE/);results.push('Voice fixture: recording, transcription, existing response, sources, playback state and interrupt pass');
 await p.evaluate(()=>{const target=document.createElement('div');window.AssistantUI.renderText(target,'<img src=x onerror=alert(1)> **bold** `code` [bad](javascript:alert(1))');if(target.querySelector('img') || target.querySelector('a')) throw new Error('Unsafe content rendered');});
 const assetChecks=await p.evaluate(()=>Array.from(document.images).map(img=>({src:img.getAttribute('src'),loaded:img.complete&&img.naturalWidth>0,width:img.getBoundingClientRect().width,height:img.getBoundingClientRect().height})));assert(assetChecks.every(a=>a.loaded));
 assert.deepEqual(errors,[]);results.push('No JavaScript exceptions or HTTP asset errors; images loaded; history focus and multiline input pass');
 fs.writeFileSync('docs/screenshots/browser-validation.json',JSON.stringify({results,errors,assetChecks},null,2));console.log(JSON.stringify(results,null,2));await b.close();
})().catch(e=>{console.error(e);process.exit(1)});
