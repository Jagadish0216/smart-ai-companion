/* Browser dictation regression tests. All microphone/audio/API writes are fixtures. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.DASHBOARD_BASE_URL || 'http://127.0.0.1:8004';
if (!['localhost', '127.0.0.1'].includes(new URL(base).hostname)) throw new Error('Local test server required');
const output = 'docs/screenshots/voice-transcript';

(async () => {
    fs.mkdirSync(output, {recursive:true});
    const browser = await chromium.launch({headless:true, channel:process.env.PLAYWRIGHT_CHANNEL || 'msedge'});
    const page = await browser.newPage({viewport:{width:390,height:844}, reducedMotion:'reduce'});
    const errors = [], checks = [];
    page.on('pageerror', e => errors.push(e.message));
    // Fail closed if a test accidentally tries a real API write.
    await page.route('**/api/**', route => ['GET','HEAD'].includes(route.request().method()) ? route.continue() : route.abort());
    await page.addInitScript(() => {
        const fixture = window.voiceFixture = {requests:[], transcript:'  What is Raspberry Pi?\n', sttDelay:80, sttFail:false, ttsDelay:80, ttsFail:false, playReject:false, streamDelay:25, plays:0, pauses:0, captures:0, stopped:0};
        const wait = (ms, signal) => new Promise((resolve,reject) => {
            const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted','AbortError')); };
            const timer = setTimeout(() => { signal?.removeEventListener('abort',abort); resolve(); },ms);
            if (signal?.aborted) abort(); else signal?.addEventListener('abort',abort,{once:true});
        });
        Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async () => {
            fixture.captures++; return {getTracks:() => [{stop(){fixture.stopped++;}}]};
        }}});
        window.MediaRecorder = class extends EventTarget {
            static isTypeSupported(type){return type==='audio/webm;codecs=opus';}
            constructor(){super();this.state='inactive';this.mimeType='audio/webm;codecs=opus';}
            start(){this.state='recording';}
            stop(){this.state='inactive';const event=new Event('dataavailable');Object.defineProperty(event,'data',{value:new Blob(['fixture'])});this.dispatchEvent(event);this.dispatchEvent(new Event('stop'));}
        };
        window.Audio = class {
            async play(){fixture.plays++;fixture.audio=this;if(fixture.playReject)throw new Error('Playback denied');}
            pause(){fixture.pauses++;}
        };
        const original = window.fetch;
        window.fetch = async (url,options={}) => {
            const name=String(url), signal=options.signal;
            if (options.method==='POST' && name.startsWith('/api/assistant/')) {
                fixture.requests.push({url:name,body:typeof options.body==='string'?JSON.parse(options.body):null,csrf:options.headers['X-CSRFToken']});
                if (name.includes('transcribe-only')) {
                    await wait(fixture.sttDelay,signal);
                    return new Response(JSON.stringify(fixture.sttFail?{error:'Controlled STT failure'}:{transcription:fixture.transcript}),{status:fixture.sttFail?500:200});
                }
                if (name.includes('synthesize')) {
                    await wait(fixture.ttsDelay,signal);
                    return new Response(JSON.stringify(fixture.ttsFail?{error:'Unavailable'}:{audio_base64:'fixture'}),{status:fixture.ttsFail?503:200});
                }
                if (name.includes('chat/stream')) {
                    const encoder=new TextEncoder();
                    const stream=new ReadableStream({async start(controller){
                        try {
                            for (const event of [{type:'start',conversation_id:774002},{type:'delta',text:'Streamed '},{type:'delta',text:'answer'},{type:'done',response:'Streamed answer',conversation_id:774002,rag_sources:[{title:'Fixture source'}]}]) {
                                await wait(fixture.streamDelay,signal);
                                controller.enqueue(encoder.encode(JSON.stringify(event)+'\n'));
                            }
                            controller.close();
                        } catch(e){controller.error(e);}
                    }});
                    return new Response(stream);
                }
                throw new Error('Unexpected API write: '+name);
            }
            return original(url,options);
        };
    });
    const check = (condition,label) => {assert.ok(condition,label);checks.push(label);};
    const requests = () => page.evaluate(() => voiceFixture.requests);
    const status = text => page.waitForFunction(value => document.getElementById('voice-status').textContent===value,text);
    const reset = async (width=390,height=844) => {await page.setViewportSize({width,height});await page.goto(base+'/assistant/?new=1');await page.waitForFunction(() => !!window.AssistantUI);};
    const record = async () => {await page.locator('#btn-voice').click();await status('Listening');await page.locator('#btn-voice').click();};
    const draft = async () => {await record();await status('Voice transcript — review or send');};
    const fits = () => page.evaluate(() => {
        const input=document.querySelector('.chat-input-wrapper').getBoundingClientRect();
        const nav=document.querySelector('.app-rail').getBoundingClientRect();
        const transcript=document.getElementById('chat-history').getBoundingClientRect();
        return document.documentElement.scrollWidth<=innerWidth+2 && input.y>=0 && input.bottom<=(innerWidth<=600?nav.y:innerHeight)+2 && (!document.querySelector('.has-messages') || (transcript.height>=100 && transcript.bottom<=input.y+2));
    });
    try {
        await reset();
        await page.evaluate(() => voiceFixture.sttDelay=450);
        await record();await status('Transcribing…');
        check(await page.locator('#voice-status').isVisible(),'Transcribing status is visible');
        check(await page.locator('#chat-input').isDisabled(),'Textarea disabled only while STT is running');
        check(await page.locator('#btn-submit').isDisabled(),'Send disabled while STT is running');
        await page.locator('#btn-voice').click();
        check(await page.evaluate(() => voiceFixture.captures===1),'Another recording cannot start during STT');
        await status('Voice transcript — review or send');
        check(await page.locator('#voice-status').isVisible(),'Draft review indication is visible');
        check(await page.locator('#chat-input').inputValue()==='  What is Raspberry Pi?\n','Exact transcription enters textarea');
        check(await page.locator('#btn-submit').isEnabled(),'Send enabled for transcript draft');
        check(await page.evaluate(() => document.activeElement.id==='chat-input' && document.activeElement.selectionStart===document.activeElement.value.length),'Textarea focused with cursor at end');
        check(await page.locator('.chat-msg').count()===0,'Draft creates no conversation messages');
        check(await page.evaluate(() => !localStorage.getItem('companion_conversation_id')),'Draft creates no conversation ID');
        await page.waitForTimeout(100);
        check((await requests()).length===1,'Transcription is not automatically submitted or synthesized');
        await page.locator('#btn-voice').click();
        check(await page.locator('#chat-input').inputValue()==='  What is Raspberry Pi?\n' && await page.evaluate(() => voiceFixture.captures===1),'Rerecording preserves and refuses to overwrite a draft');
        await page.screenshot({path:path.join(output,'draft-390x844.png')});
        await page.locator('#chat-input').fill('Edited voice question');
        await page.evaluate(() => voiceFixture.ttsDelay=600);
        await page.locator('#btn-submit').click();
        await page.waitForFunction(() => voiceFixture.requests.some(r => r.url.includes('synthesize')));
        check((await requests()).find(r=>r.url.includes('chat/stream')).body.query==='Edited voice question','Edited draft is submitted via normal stream');
        check(await page.locator('.chat-msg.user').count()===1 && await page.locator('.chat-msg.ai').count()===1,'Exactly one user and one assistant message appear');
        check((await page.locator('.chat-msg.ai').innerText()).includes('Streamed answer') && await page.evaluate(()=>voiceFixture.plays===0),'Completed text appears before TTS finishes');
        check((await requests()).find(r=>r.url.includes('synthesize')).body.text==='Streamed answer','TTS receives completed response without a second inference');
        check((await page.locator('.chat-msg.ai').innerText()).includes('Fixture source'),'Streaming response retains sources');
        await status('Speaking');
        await page.locator('#interrupt-response').click();await status('IDLE');
        check(await page.evaluate(()=>voiceFixture.pauses===1),'Interrupt stops playback');
        check(await page.locator('#chat-input').isEnabled(),'Composer usable after playback interrupt');

        await reset();await draft();await page.evaluate(()=>voiceFixture.ttsFail=true);
        await page.locator('#btn-submit').click();await status('IDLE');
        check((await page.locator('.chat-msg.ai').innerText()).includes('Streamed answer'),'TTS failure preserves completed text');
        check((await page.locator('body').innerText()).includes('audio is unavailable'),'TTS failure shows audio-unavailable message');

        await reset();await draft();await page.evaluate(()=>voiceFixture.playReject=true);
        await page.locator('#btn-submit').click();await status('IDLE');
        check((await page.locator('.chat-msg.ai').innerText()).includes('Streamed answer') && await page.locator('#chat-input').isEnabled(),'Playback rejection preserves text and restores composer');

        await reset();await draft();await page.evaluate(()=>voiceFixture.streamDelay=250);
        await page.locator('#btn-submit').click();await status('Responding');
        await page.locator('#interrupt-response').click();await status('IDLE');
        check((await requests()).every(r=>!r.url.includes('synthesize')),'Interrupted stream does not synthesize an incomplete response');
        check((await page.locator('.chat-msg.ai').innerText()).includes('Response interrupted'),'Stream Interrupt preserves partial output');

        await reset();await draft();await page.evaluate(()=>voiceFixture.ttsDelay=800);
        await page.locator('#btn-submit').click();await page.waitForFunction(()=>voiceFixture.requests.some(r=>r.url.includes('synthesize')));
        await page.locator('#interrupt-response').click();await status('IDLE');await page.waitForTimeout(850);
        check(await page.evaluate(()=>voiceFixture.plays===0),'Interrupt during synthesis prevents late playback');
        check((await page.locator('.chat-msg.ai').innerText()).includes('Streamed answer'),'Synthesis cancellation preserves completed text');

        await reset();await page.evaluate(()=>voiceFixture.sttDelay=800);await record();await status('Transcribing…');
        await page.locator('#interrupt-response').click();await status('IDLE');await page.waitForTimeout(850);
        check(await page.locator('#chat-input').inputValue()==='' && await page.locator('.chat-msg').count()===0,'STT cancellation cannot insert a late draft or message');
        check(await page.locator('#chat-input').isEnabled(),'STT cancellation restores textarea');

        await reset();await page.evaluate(()=>voiceFixture.sttFail=true);await record();await status('Error');
        check(await page.locator('#chat-input').isEnabled() && await page.locator('.chat-msg').count()===0,'STT errors restore usable composer without history writes');

        await reset();await draft();await page.locator('#chat-input').fill('');
        check(await page.locator('#btn-submit').isDisabled(),'Deleting the draft disables Send');
        await draft();check(await page.evaluate(()=>voiceFixture.captures===2),'Clearing a draft allows recording again');
        await page.locator('#history-trigger').click();await page.getByRole('button',{name:'New Chat'}).click();
        check(await page.locator('#chat-input').inputValue()==='','New Chat clears draft and voice origin');
        await page.locator('#chat-input').fill('Ordinary typed question');await page.locator('#btn-submit').click();await status('IDLE');
        check((await requests()).filter(r=>r.url.includes('synthesize')).length===0,'Typed messages do not request speech');
        check(await page.locator('.voice-transcription-mark').count()===0,'Typed messages keep ordinary presentation');

        for(const [width,height] of [[1366,768],[375,667],[390,844]]) {
            await reset(width,height);await draft();check(await fits(),`Draft composer fits ${width}x${height}`);
            await page.screenshot({path:path.join(output,`draft-${width}x${height}.png`)});
            if(width<600){await page.setViewportSize({width,height:420});await page.waitForTimeout(100);check(await fits(),`Draft keyboard-height composer fits ${width}x420`);await page.setViewportSize({width,height});}
            await page.locator('#btn-submit').click();await status('Speaking');check(await fits(),`Active composer/transcript fit ${width}x${height}`);
            await page.locator('#interrupt-response').click();
            if(width<600){await page.setViewportSize({width,height:420});await page.locator('#chat-input').focus();await page.waitForTimeout(100);check(await fits(),`Keyboard-height composer fits ${width}x420`);}
        }
        check((await requests()).every(r=>!!r.csrf),'All fixture API writes carry CSRF');
        check(errors.length===0,'No browser JavaScript exceptions');
        fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({base,passed:checks.length,checks,errors},null,2));
        console.log(JSON.stringify({passed:checks.length,errors}));
    } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
