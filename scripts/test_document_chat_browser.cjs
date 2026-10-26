/* Real-browser acceptance against scripts/preview_document_chat_ui.py only. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
(async () => {
    const base = process.argv[2] || 'http://127.0.0.1:8017';
    if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base)) throw new Error('Use the isolated local fixture server.');
    const pdf = process.argv[3];
    const output = process.argv[4] || 'docs/screenshots/assistant-knowledge-ui-integration';
    fs.mkdirSync(output, {recursive:true});
    const browser = await chromium.launch({headless:true, ...(process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH} : {})});
    try {
        const page = await browser.newPage({viewport:{width:1440,height:1000}});
        const errors = []; page.on('pageerror', error => errors.push(error.message));
        await page.goto(base + '/login/?next=/assistant/');
        await page.screenshot({path:path.join(output,'login-desktop.png'),fullPage:true});
        await page.getByLabel('Username', {exact:true}).fill('ui-acceptance');
        await page.getByLabel('Password', {exact:true}).fill('local-test-only');
        await page.getByRole('button', {name:'Show password',exact:true}).click();
        assert.equal(await page.locator('#id_password').getAttribute('type'), 'text');
        await page.getByRole('button', {name:'Hide password',exact:true}).click();
        await page.getByRole('button', {name:'Sign in',exact:false}).click();
        await page.waitForURL('**/assistant/');
        await page.locator('#assistant-file-picker').setInputFiles(pdf);
        await page.getByRole('button', {name:'Deselect aurora.pdf'}).waitFor();
        const send = async query => {
            await page.locator('#chat-input').fill(query);
            const request = page.waitForRequest(r => r.url().endsWith('/api/assistant/chat/stream/') && r.method() === 'POST');
            await page.getByRole('button', {name:'Send message',exact:true}).click();
            const payload = (await request).postDataJSON();
            await page.waitForFunction(() => !document.getElementById('chat-input').disabled);
            return payload;
        };
        await page.locator('#knowledge-mode').selectOption('offline_only');
        const first = await send('What is the aurora battery capacity?');
        assert.equal(first.document_ids.length,1); assert.equal(first.knowledge_mode,'offline_only');
        assert.match(await page.locator('.chat-msg.ai').last().innerText(), /42 Wh/);
        assert.match(await page.locator('.message-sources').last().innerText(), /aurora.pdf.*p\. 1/);
        const followup = await send('Tell me the aurora battery capacity again');
        assert.deepEqual(followup.document_ids,first.document_ids);
        await page.locator('#assistant-file-picker').setInputFiles({name:'notes.md',mimeType:'text/markdown',buffer:Buffer.from('# Extra notes\nNebula telescope aperture is 90 mm.')});
        await page.getByRole('button', {name:'Deselect notes.md'}).waitFor();
        const multiple = await send('What is the aurora battery capacity?');assert.equal(multiple.document_ids.length,2);
        await page.getByRole('button', {name:'Deselect notes.md'}).click();
        const conversation = followup.conversation_id;
        await page.screenshot({path:path.join(output,'assistant-desktop.png'),fullPage:true});
        await page.reload(); await page.getByRole('button', {name:'Deselect aurora.pdf'}).waitFor();
        await page.locator('.message-sources').last().waitFor();
        await page.getByRole('button', {name:'Deselect aurora.pdf'}).click();
        await page.locator('#knowledge-mode').selectOption('online_preferred');
        const automatic = await send('Hello'); assert.equal('document_ids' in automatic,false);assert.equal(automatic.knowledge_mode,'online_preferred');
        await page.locator('#knowledge-mode').selectOption('online_only');
        const online = await send('Hello');assert.equal(online.knowledge_mode,'online_only');
        await page.evaluate(() => window.startNewChat());
        assert.equal(await page.locator('#selected-documents button').count(),0);
        await page.evaluate(id => window.loadConversationId(id),conversation);
        await page.locator('.chat-msg.ai').first().waitFor();
        // Browser voice is transcribe-only; sending the reviewed draft uses the same scope.
        await page.route('**/api/assistant/voice/transcribe-only/', route => route.fulfill({json:{transcription:'What is the aurora battery capacity?'}}));
        await page.evaluate(() => {
            navigator.mediaDevices.getUserMedia = async () => ({getTracks:()=>[{stop(){}}]});
            window.MediaRecorder = class {
                static isTypeSupported(){return true;}
                constructor(){this.mimeType='audio/webm';this.state='inactive';this.events={};}
                addEventListener(name, callback){this.events[name]=callback;}
                start(){this.state='recording';}
                stop(){this.state='inactive';this.events.dataavailable({data:new Blob(['mock-audio'])});this.events.stop();}
            };
        });
        await page.locator('#document-library summary').click();
        await page.locator('#assistant-documents label').filter({hasText:'aurora.pdf'}).locator('input').check();
        await page.locator('#document-library summary').click();
        await page.locator('#knowledge-mode').selectOption('offline_only');
        await page.getByRole('button',{name:'Start or stop microphone'}).click();
        await page.waitForFunction(()=>document.getElementById('voice-status').textContent==='Listening');
        await page.getByRole('button',{name:'Start or stop microphone'}).click();
        await page.waitForFunction(()=>document.getElementById('chat-input').value.includes('aurora battery'));
        await page.route('**/api/assistant/voice/synthesize/', route=>route.fulfill({status:503,json:{error:'Mock audio unavailable'}}));
        const voice = await send('What is the aurora battery capacity?');assert.deepEqual(voice.document_ids,first.document_ids);assert.equal(voice.knowledge_mode,'offline_only');
        // Citation metadata must remain text and reject script/credential URLs.
        const safety = await page.evaluate(() => {
            const parent=document.createElement('div');document.body.append(parent);
            AssistantUI.renderSources(parent,{rag_sources:[{title:'<img src=x onerror=alert(1)>',page_number:2,url:'javascript:alert(1)'},{title:'Credentials',url:'https://user:password@example.org/'}],online_sources:[{title:'Safe',url:'https://example.org',verified_at:'2026-10-26',freshness:'HISTORICAL'}]});
            const result={images:parent.querySelectorAll('img').length,links:parent.querySelectorAll('a').length,text:parent.textContent};parent.remove();return result;
        });
        assert.equal(safety.images,0);assert.equal(safety.links,1);assert.match(safety.text,/Last verified/);assert.match(safety.text,/HISTORICAL/);
        for(const [width,height] of [[390,844],[768,1024]]) {
            await page.setViewportSize({width,height});
            assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
            await page.locator('.chat-msg.ai').last().scrollIntoViewIfNeeded();
            await page.locator('.message-sources').last().scrollIntoViewIfNeeded();
            await page.waitForFunction(() => {const source=document.querySelector('.chat-msg.ai:last-child .message-sources');return source && source.getBoundingClientRect().bottom <= document.getElementById('chat-history').getBoundingClientRect().bottom + 1;});
            await page.locator('.toast').evaluateAll(items => items.forEach(item => item.remove()));
            await page.screenshot({path:path.join(output,`assistant-${width}.png`),fullPage:true});
        }
        await page.goto(base+'/login/');await page.setViewportSize({width:390,height:844});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
        await page.screenshot({path:path.join(output,'login-mobile.png'),fullPage:true});
        assert.deepEqual(errors,[]);
        console.log('PASS: upload/index/select, citations, followup, reload/history, deselect, modes, new chat, browser voice, safe rendering, responsive layouts, login controls.');
    } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
