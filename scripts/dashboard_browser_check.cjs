/* Responsive acceptance. Fixtures stay in the browser; never POST to a live device. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
const path = require('path');
const baseURL = process.env.DASHBOARD_BASE_URL || 'http://127.0.0.1:8000';
const baseline = process.env.DASHBOARD_AUDIT_BASELINE === '1';
const label = process.env.DASHBOARD_AUDIT_LABEL || (baseline ? 'baseline' : 'local-fixed');
const output = path.join('docs/screenshots/responsive', label);
const matrix = process.env.DASHBOARD_AUDIT_FUNCTIONAL_ONLY === '1' ? [] : [[1440,960],[1366,768],[1280,720],[1024,768],[768,1024],[430,932],[430,844],[390,844],[375,667],[360,800]];
const routes = [ ['home','/'], ['assistant-empty','/assistant/?new=1'], ['assistant-active','/assistant/'], ['conversations','/conversations/'], ['memory','/knowledge/'], ['device','/device/'], ['resources','/resource-manager/'], ['network','/network/'], ['logs','/logs/'], ['settings','/settings/'], ['studio','/studio/'], ['setup','/setup/'] ];
const captures = new Set(['home-1440x960','home-1366x768','home-390x844',...['assistant-empty','assistant-active'].flatMap(n=>['1440x960','1366x768','390x844','375x667'].map(v=>n+'-'+v)),...['memory','device'].flatMap(n=>['1366x768','390x844'].map(v=>n+'-'+v)), 'settings-390x844','logs-390x844','studio-390x844']);
const conversation = Array.from({length:24}, (_,i)=>({role:i%2?'AI':'USER',content:i===0?'FIRST_MESSAGE_MARKER — Begin the controlled conversation.':i===23?'FINAL_MESSAGE_MARKER — The final response must be reachable above the composer.':i%2?'A long response for scroll acceptance.\n\n'+('Readable explanation with enough lines to require scrolling.\n\n'.repeat(5))+(i===5?'```javascript\nconst longLine = "'+('unbroken-code-'.repeat(30))+'";\n```\n\nhttps://example.com/'+('long-path-'.repeat(30)):''):'Question '+i+' '+('unbroken-user-value-'.repeat(i===4?20:1))}));
function auditGeometry() {
    const issues=[];
    const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
    const visible=e=>!!(e&&e.getClientRects().length&&e.checkVisibility({checkVisibilityCSS:true}));
    const describe=e=>e.id?'#'+e.id:e.tagName.toLowerCase()+'.'+String(e.className).split(' ').filter(Boolean).slice(0,2).join('.');
    const nav=document.querySelector('.app-rail');
    const mobile=nav&&rect(nav).width>innerWidth*.9;
    const usableBottom=mobile?rect(nav).y:innerHeight;
    if(document.documentElement.scrollWidth>innerWidth+2) issues.push('Document horizontal overflow: '+document.documentElement.scrollWidth+' > '+innerWidth);
    const groups=['.topbar','.assistant-product-row','.assistant-tools','.model-info','.readiness dl > div','.info-row','.card-header','.document-row','.conv-card','.setting-row','.resource-card-heading','.log-entry','.memory-toolbar','.composer-actions','.mode-group'];
    for(const selector of groups) for(const group of document.querySelectorAll(selector)) {
        if(!visible(group)) continue;
        const items=Array.from(group.children).filter(visible);
        for(let i=0;i<items.length;i++) for(let j=i+1;j<items.length;j++) {
            const a=rect(items[i]),b=rect(items[j]);
            if(Math.min(a.right,b.right)-Math.max(a.x,b.x)>3&&Math.min(a.bottom,b.bottom)-Math.max(a.y,b.y)>3) issues.push('Overlap in '+selector+': '+describe(items[i])+' / '+describe(items[j]));
        }
    }
    for(const id of ['global-ai-engine','global-internet-label']) {const e=document.getElementById(id);if(e&&!visible(e))issues.push('Required status label hidden: #'+id);}
    const assistant=document.body.dataset.page==='assistant';
    const viewport=document.getElementById('chat-history');
    for(const el of document.querySelectorAll('.chat-msg,.msg-bubble,.card,.info-row-value,.resource-card-value,.log-message,.log-component,.doc-name,.doc-status,.conv-title,.page-header-title,.hero-copy h2,.topbar-left,.status-pill,.mode-btn,.setting-desc')) {
        if(!visible(el)) continue;
        const r=rect(el), style=getComputedStyle(el);
        if(r.x<-2||r.right>innerWidth+2) issues.push('Content outside viewport width: '+describe(el));
        if(el.scrollWidth>el.clientWidth+3&&el.clientWidth>0&&style.textOverflow!=='ellipsis') issues.push('Text/panel escapes container: '+describe(el));
    }
    for(const el of document.querySelectorAll('main .card,main .memory-detail,main .page-header,main .log-entry,.topbar-left,.status-pill')) {
        if(!visible(el))continue;
        if(['hidden','clip'].includes(getComputedStyle(el).overflowY)&&el.scrollHeight>el.clientHeight+2)issues.push('Vertically clipped text/panel: '+describe(el));
    }
    for(const table of document.querySelectorAll('table')) {
        if(!visible(table))continue;
        let owner=table.parentElement;
        while(owner&&owner!==document.body&&!['auto','scroll'].includes(getComputedStyle(owner).overflowX))owner=owner.parentElement;
        if(table.getBoundingClientRect().width>innerWidth&&(!owner||owner===document.body))issues.push('Wide table has no deliberate horizontal scroller');
        for(const cell of table.querySelectorAll('th'))if(cell.getBoundingClientRect().width<60)issues.push('Table columns squeezed below readable width');
    }
    for(const el of document.querySelectorAll('main input:not([type=hidden]),main textarea,main button,main a[href],main summary')) {
        if(!visible(el)||el.closest('.chat-sidebar')||el.closest('.chat-viewport')) continue;
        // Normal pages scroll the document. Assistant controls must already be in its bounded shell.
        if(!assistant) el.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
        const r=rect(el);
        if(r.x<-2||r.right>innerWidth+2||r.y<-2||r.bottom>usableBottom+2) issues.push('Control clipped/covered by navigation: '+describe(el));
        if(r.width>0&&r.height>0) {
            const target=Array.from(el.getClientRects()).find(box=>box.width>0&&box.height>0) || r;
            const hit=document.elementFromPoint(target.x+target.width/2,target.y+Math.min(target.height/2,22));
            if(hit&&!el.contains(hit)&&hit!==el) issues.push('Control occluded: '+describe(el)+' by '+describe(hit));
        }
    }
    if(!assistant) {
        for(const el of document.querySelectorAll('.content-area,#log-container,.studio-controls')) if(visible(el)&&['auto','scroll'].includes(getComputedStyle(el).overflowY)&&el.scrollHeight>el.clientHeight+2) issues.push('Competing page scroll owner: '+describe(el));
        window.scrollTo({top:document.scrollingElement.scrollHeight,behavior:'instant'});
        const main=document.querySelector('main');
        if(main&&rect(main).bottom>usableBottom+2) issues.push('Final page content extends under navigation');
    } else {
        const composer=document.querySelector('.chat-input-wrapper');
        const header=document.querySelector('.chat-main-header');
        const cr=rect(composer),hr=rect(header);
        if(cr.bottom>usableBottom+2||cr.y<0) issues.push('Assistant composer outside usable viewport');
        if(document.scrollingElement.scrollHeight>innerHeight+2) issues.push('Assistant has competing document scroll');
        if(document.querySelector('.has-messages')) {
            const vr=rect(viewport);
            if(vr.height<100) issues.push('Conversation viewport collapsed/unusable: '+vr.height);
            if(hr.bottom>vr.y+2) issues.push('Header overlaps transcript');
            if(vr.bottom>cr.y+2) issues.push('Composer overlaps transcript');
        } else {
            const empty=document.getElementById('assistant-empty');
            if(rect(empty).height<80) issues.push('Empty conversation region collapsed');
            for(const e of [empty.querySelector('h1'),empty.querySelector('p')]) {const r=rect(e);if(r.y<rect(empty).y-2||r.bottom>cr.y+2) issues.push('Empty-state text is clipped');}
        }
    }
    window.scrollTo({top:0,behavior:'instant'});
    return {issues:[...new Set(issues)], viewport:{width:innerWidth,height:innerHeight,scale:visualViewport?.scale || 1},usableBottom,scrollOwner:assistant?'#chat-history (active) / #assistant-empty (new)':'document'};
}
async function checkAssistant(page) {
    const issues=[];
    const add=(condition,message)=>{if(!condition) issues.push(message);};
    let metrics=await page.evaluate(()=>{
        const view=document.getElementById('chat-history');
        const first=view.querySelector('.chat-msg'),last=view.querySelector('.chat-msg:last-child');
        if(!first||!last) return {missing:true};
        view.scrollTop=0;
        const topVisible=first.getBoundingClientRect().top>=view.getBoundingClientRect().top-2&&first.getBoundingClientRect().top<view.getBoundingClientRect().bottom;
        view.scrollTop=view.scrollHeight;
        const endVisible=last.getBoundingClientRect().bottom<=view.getBoundingClientRect().bottom+2&&last.getBoundingClientRect().bottom<=document.querySelector('.chat-input-wrapper').getBoundingClientRect().top+2;
        return {topVisible,endVisible,height:view.clientHeight,scrollHeight:view.scrollHeight,scrollTop:view.scrollTop,firstText:first.textContent,lastText:last.textContent};
    });
    add(!metrics.missing,'Controlled long conversation was not rendered');
    if(!metrics.missing){add(metrics.scrollHeight>metrics.height+200,'Fixture does not guarantee scrolling');add(metrics.topVisible,'scrollTop=0 does not expose first message');add(metrics.endVisible,'scrollTop=scrollHeight does not expose final message above composer');add(metrics.firstText.includes('FIRST_MESSAGE_MARKER'),'First message missing');add(metrics.lastText.includes('FINAL_MESSAGE_MARKER'),'Final message missing');}
    const obstruction=await page.evaluate(()=>{
        const last=document.querySelector('.chat-msg:last-child').getBoundingClientRect();
        const selectors=['#jump-latest','#history-trigger','.chat-input-wrapper','.app-rail'];
        return selectors.filter(selector=>{const el=document.querySelector(selector);if(!el?.getClientRects().length)return false;const r=el.getBoundingClientRect();return Math.min(last.right,r.right)>Math.max(last.left,r.left)+2&&Math.min(last.bottom,r.bottom)>Math.max(last.top,r.top)+2;});
    });
    add(!obstruction.length,'Final message obscured by '+obstruction.join(', '));
    await page.locator('#history-trigger').click();
    const drawer=await page.locator('#chat-history-sidebar').boundingBox();
    const nav=await page.locator('.app-rail').boundingBox();
    const size=page.viewportSize(),bottom=size.width<=600?nav.y:size.height;
    add(drawer&&drawer.x>=0&&drawer.y>=0&&drawer.x+drawer.width<=size.width+1&&drawer.y+drawer.height<=bottom+1,'History drawer does not fit above navigation');
    await page.keyboard.press('Escape');
    add(await page.evaluate(()=>document.activeElement.id==='history-trigger'),'History focus was not restored');
    return {issues,scroll:metrics};
}
async function configureFixtures(page) {
    await page.addInitScript(()=>{if(location.pathname==='/assistant/'&&!location.search.includes('new=1'))localStorage.setItem('companion_conversation_id','774001');});
    await page.route('**/api/assistant/chat/?conversation_id=*',route=>route.fulfill({json:{conversation_id:774001,messages:conversation}}));
    // No real generation, playback requests, provisioning commands, uploads or deletions during audit.
    await page.route('**/api/**',async route=>{
        if(!['GET','HEAD'].includes(route.request().method())) return route.abort('blockedbyclient');
        await route.fallback();
    });
}
async function functionality(page) {
    const checks=[];
    const expect=(v,m)=>{if(!v)throw new Error(m);checks.push(m);};
    await page.setViewportSize({width:390,height:844});
    await page.goto(baseURL+'/assistant/?new=1');
    await page.evaluate(()=>{
        const original=window.fetch;
        window.fetch=(url,options={})=>{
            if(String(url).includes('/chat/stream/')) {
                let i=0;const enc=new TextEncoder();const stream=new ReadableStream({start(controller){function emit(){if(options.signal.aborted){controller.error(new DOMException('Aborted','AbortError'));return;}if(i++<45){controller.enqueue(enc.encode(JSON.stringify({type:'delta',text:'Streaming explanation '+i+' '+('content '.repeat(20))+'\n\n'})+'\n'));setTimeout(emit,60);}else{controller.enqueue(enc.encode(JSON.stringify({type:'done'})+'\n'));controller.close();}}emit();}});
                return Promise.resolve(new Response(stream));
            }
            if(String(url).includes('/voice/synthesize/'))return Promise.resolve(new Response(JSON.stringify({audio_base64:'fixture'})));
            if(String(url).includes('/voice/transcribe-only/'))return Promise.resolve(new Response(JSON.stringify({transcription:'Voice fixture question'}),{headers:{'Content-Type':'application/json'}}));
            return original(url,options);
        };
    });
    await page.locator('#chat-input').fill('Controlled responsive stream');await page.locator('#btn-submit').click();await page.waitForTimeout(1000);
    await page.locator('#chat-history').evaluate(el=>{el.scrollTop=0;el.dispatchEvent(new Event('scroll'));});await page.waitForTimeout(250);
    expect(await page.locator('#chat-history').evaluate(e=>e.scrollTop===0),'Streaming preserves the reading position');
    expect(await page.locator('#jump-latest').isVisible(),'Jump to latest is visible');await page.locator('#jump-latest').click();
    expect(await page.locator('#chat-history').evaluate(e=>e.scrollTop+e.clientHeight>=e.scrollHeight-4),'Jump to latest exposes current output');
    await page.locator('#interrupt-response').click();await page.waitForTimeout(150);expect(await page.locator('#chat-input').isEnabled(),'Interrupt restores the composer');
    await page.locator('#history-trigger').click();await page.getByRole('button',{name:'New Chat'}).click();
    await page.locator('#chat-input').fill('line one');await page.locator('#chat-input').press('Shift+Enter');expect(await page.locator('#chat-input').inputValue()==='line one\n','Shift+Enter keeps multiline input');
    await page.evaluate(()=>{
        if (!navigator.mediaDevices) Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{}});
        navigator.mediaDevices.getUserMedia=async()=>({getTracks:()=>[{stop(){}}]});
        window.MediaRecorder=class extends EventTarget{static isTypeSupported(){return true;}constructor(){super();this.mimeType='audio/webm';this.state='inactive';}start(){this.state='recording';}stop(){this.state='inactive';const e=new Event('dataavailable');Object.defineProperty(e,'data',{value:new Blob(['fixture'])});this.dispatchEvent(e);this.dispatchEvent(new Event('stop'));}};
        window.Audio=class{play(){return Promise.resolve();}pause(){}};
    });
    await page.locator('#chat-input').fill('');
    await page.locator('#btn-voice').click();await page.waitForFunction(()=>document.getElementById('voice-status').textContent==='Listening');expect(await page.locator('#voice-status').innerText()==='Listening','Microphone fixture enters Listening');
    await page.locator('#btn-voice').click();await page.waitForFunction(()=>document.getElementById('chat-input').value==='Voice fixture question');
    expect(await page.locator('#btn-submit').isEnabled(),'Voice transcript is an editable draft with Send enabled');
    expect(await page.locator('.chat-msg').count()===0,'Voice transcript is not automatically submitted');
    await page.locator('#btn-submit').click();await page.waitForFunction(()=>document.getElementById('voice-status').textContent==='Speaking');
    expect((await page.locator('.chat-messages-container').innerText()).includes('Streaming explanation'),'Voice response is streamed before playback');
    await page.locator('#interrupt-response').click();expect(await page.locator('#voice-status').innerText()==='IDLE','Playback interrupt returns to Idle');
    return checks;
}
(async()=>{
    fs.mkdirSync(output,{recursive:true});
    const browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL || 'msedge'});
    const results=[],runtimeErrors=[],assetFailures=[],screenshots=[];
    const page=await browser.newPage({reducedMotion:'reduce'});
    page.setDefaultTimeout(8000);
    page.on('pageerror',e=>runtimeErrors.push({url:page.url(),message:e.message}));
    page.on('response',r=>{if(r.status()>=400&&new URL(r.url()).pathname.startsWith('/static/'))assetFailures.push({url:r.url(),status:r.status()});});
    await configureFixtures(page);
    for(const [width,height] of matrix) {
        await page.setViewportSize({width,height});
        for(const [name,url] of routes) {
            const key=name+'-'+width+'x'+height;
            const result={route:url,state:name,width,height,issues:[]};
            try {
                const response=await page.goto(baseURL+url,{timeout:20000,waitUntil:'load'});result.status=response.status();
                if(name==='setup') {
                    result.access=response.status()===404?'correctly unavailable outside setup mode':page.url().endsWith('/setup/')&&response.status()===200?'setup mode available':'unexpected';
                    if(result.access==='unexpected')result.issues.push('Unexpected setup access response');
                    if(response.status()===404){results.push(result);console.log(key+': access rule PASS');continue;}
                } else if(response.status()!==200||new URL(page.url()).pathname!==new URL(url,baseURL).pathname)result.issues.push('Unexpected route response/redirect: '+response.status()+' '+page.url());
                if(name==='assistant-active')await page.waitForFunction(()=>document.querySelectorAll('.chat-msg').length===24,{},{timeout:5000});
                await page.waitForTimeout(100);
                if(name==='studio')await page.locator('#dev-trigger').click();
                if(name==='home') {
                    await page.locator('.rail-link').last().click();
                    const box=await page.locator('#more-menu').boundingBox();
                    const nav=await page.locator('.app-rail').boundingBox();
                    const bottom=width<=600?nav.y:height;
                    if(!box||box.x<0||box.y<0||box.x+box.width>width+1||box.y+box.height>bottom+1)result.issues.push('More dialog does not fit in usable viewport');
                    await page.keyboard.press('Escape');
                }

                const geometry=await page.evaluate(auditGeometry);result.issues.push(...geometry.issues);result.viewport=geometry.viewport;result.usableBottom=geometry.usableBottom;result.scrollOwner=geometry.scrollOwner;
                if(name==='assistant-active') {const chat=await checkAssistant(page);result.issues.push(...chat.issues);result.scroll=chat.scroll;}
                if(name.startsWith('assistant')) {
                    await page.locator('#chat-input').focus();
                    const focused=await page.evaluate(auditGeometry);result.issues.push(...focused.issues.map(i=>'Focused input: '+i));
                    if(width<=430) {
                        await page.setViewportSize({width,height:Math.min(height,420)});await page.waitForTimeout(80);
                        const keyboard=await page.evaluate(auditGeometry);result.keyboard={simulated:true,...keyboard};result.issues.push(...keyboard.issues.map(i=>'Keyboard-height simulation: '+i));
                        if(!baseline&&keyboard.issues.length){const file='issue-keyboard-'+key+'.png';await page.screenshot({path:path.join(output,file)});screenshots.push(file);}
                        await page.setViewportSize({width,height});await page.locator('#chat-input').blur();await page.waitForTimeout(80);
                    }
                }
                result.issues=[...new Set(result.issues)];
                if(!baseline&&captures.has(key)||baseline&&result.issues.length&&['assistant-empty-1366x768','assistant-active-1366x768','assistant-empty-375x667','assistant-active-375x667'].includes(key)) {
                    const file=key+'.png';await page.screenshot({path:path.join(output,file)});screenshots.push(file);
                } else if(!baseline&&result.issues.length) {const file='issue-'+key+'.png';await page.screenshot({path:path.join(output,file)});screenshots.push(file);}
            } catch(e) {result.issues.push(e.message.split('\n')[0]);if(!baseline){try{const file='issue-'+key+'.png';await page.screenshot({path:path.join(output,file)});screenshots.push(file);}catch(_){}}}
            results.push(result);console.log(key+': '+(result.issues.length?'FAIL '+result.issues.join('; '):'PASS'));
        }
    }
    const supplementary=[];
    if(!baseline) {
        // Long text is a browser-only fixture, never saved as operational data.
        for(const [width,height] of matrix) {
            await page.setViewportSize({width,height});
            for(const route of ['/','/knowledge/','/network/','/resource-manager/']) {
                await page.goto(baseURL+route);
                await page.waitForTimeout(80);
                await page.evaluate(()=>{
                    for(const selector of ['#global-ai-engine','#global-ai-model','#global-internet-label','#readiness-input','#resource-ai-value','#network-hostname']) {
                        const e=document.querySelector(selector);if(e)e.textContent='Long status fixture: '+('extended-status-value-'.repeat(5));
                    }
                    const name=document.querySelector('.doc-name');if(name)name.textContent='long_document_filename_'.repeat(7)+'.pdf';
                    const title=document.querySelector('.memory-detail h3');if(title)title.textContent='long_selected_document_'.repeat(7)+'.md';
                });
                const geometry=await page.evaluate(auditGeometry);
                supplementary.push({kind:'long-text',route,width,height,...geometry});
                if(geometry.issues.length){const file='issue-long-text-'+route.replaceAll('/','-')+width+'x'+height+'.png';await page.screenshot({path:path.join(output,file)});screenshots.push(file);}
            }
        }
        if(process.env.DASHBOARD_STAFF_COOKIE_FILE) {
            if(!['127.0.0.1','localhost'].includes(new URL(baseURL).hostname))throw new Error('Staff fixture is restricted to the local preview');
            const cookie=JSON.parse(fs.readFileSync(process.env.DASHBOARD_STAFF_COOKIE_FILE,'utf8'));
            await page.context().addCookies([{...cookie,url:baseURL}]);
            await page.route('**/api/system/network/wifi/scan/',r=>r.fulfill({json:{supported:true,networks:[{ssid:'Local responsive fixture network',signal_percent:78,security:'WPA2'}]}}));
            for(const [width,height] of matrix) {
                await page.setViewportSize({width,height});
                for(const route of ['/knowledge/','/network/','/settings/']) {
                    await page.goto(baseURL+route);await page.waitForTimeout(100);
                    const issues=[];
                    if(route==='/network/') {await page.locator('#network-list .network-row button').click();if(!await page.locator('#network-connect-panel').isVisible())issues.push('Wi-Fi connection panel did not open');}
                    if(route==='/knowledge/'&&!await page.locator('#btn-upload-doc').isVisible())issues.push('Staff upload control unavailable');
                    if(route==='/settings/'&&!await page.locator('.table-scroll').count())issues.push('Populated Settings table not exercised');
                    const geometry=await page.evaluate(auditGeometry);issues.push(...geometry.issues);
                    supplementary.push({kind:'staff-controls',route,width,height,issues});
                    if(issues.length){const file='issue-staff-'+route.replaceAll('/','-')+width+'x'+height+'.png';await page.screenshot({path:path.join(output,file)});screenshots.push(file);}
                }
            }
            await page.context().clearCookies();
        }
        if(process.env.DASHBOARD_SETUP_BASE_URL) {
            const setupURL=process.env.DASHBOARD_SETUP_BASE_URL;
            if(!['127.0.0.1','localhost'].includes(new URL(setupURL).hostname))throw new Error('Setup fixture is restricted to a local isolated preview');
            for(const [width,height] of matrix) {
                await page.setViewportSize({width,height});const response=await page.goto(setupURL+'/setup/');await page.waitForTimeout(100);
                const geometry=await page.evaluate(auditGeometry);if(response.status()!==200)geometry.issues.push('Setup-mode fixture did not render');
                supplementary.push({kind:'setup-mode',route:'/setup/',width,height,status:response.status(),...geometry});
                if(geometry.issues.length){const file='issue-setup-'+width+'x'+height+'.png';await page.screenshot({path:path.join(output,file)});screenshots.push(file);}
            }
        }
    }
    let functionalChecks=[];
    if(!baseline)try{functionalChecks=await functionality(page);}catch(e){runtimeErrors.push({phase:'functional fixtures',message:e.message});}
    const report={baseURL,baseline,matrix,results,supplementary,functionalChecks,runtimeErrors,assetFailures,screenshots,summary:{cases:results.length,passed:results.filter(r=>!r.issues.length).length,failed:results.filter(r=>r.issues.length).length,supplementaryCases:supplementary.length,supplementaryFailed:supplementary.filter(r=>r.issues.length).length}};
    fs.writeFileSync(path.join(output,'responsive-validation.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report.summary));await browser.close();if(!baseline&&(report.summary.failed||report.summary.supplementaryFailed||runtimeErrors.length||assetFailures.length))process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1;});
