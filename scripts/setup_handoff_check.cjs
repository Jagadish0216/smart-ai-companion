/* Run the production setup script with a deterministic browser/clock and API fixtures.
 * No real network, Wi-Fi changes, passwords or third-party dependencies.
 * Usage: node --test scripts/setup_handoff_check.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/js/setup.js'), 'utf8');
const canonical = 'http://smart-ai-companion.local:8000/';
const maliciousSsid = '<img src=x onerror=alert(1)> 東京';
const normal = {state: 'NORMAL_MODE', ready: true, connected_ssid: maliciousSsid, canonical_url: canonical};
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };

class Element {
    constructor() {
        this.events = {}; this.children = []; this.dataset = {}; this.textContent = '';
        this.value = ''; this.disabled = false; this.classes = new Set();
        this.classList = {
            toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name),
            add: (name) => this.classes.add(name), remove: (name) => this.classes.delete(name),
        };
    }
    set innerHTML(_value) { throw new Error('Unsafe DOM write'); }
    addEventListener(name, fn) { (this.events[name] ||= []).push(fn); }
    emit(name, event = {}) { for (const fn of this.events[name] || []) fn(event); }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.append(child); }
    replaceChildren() { this.children = []; }
    focus() {}
}

async function browser({statuses = [], post = null, ssid = maliciousSsid} = {}) {
    let now = 0, serial = 0, active = 0, maxActive = 0;
    const timers = new Map(), elements = new Map(), requests = [], redirects = [];
    const document = new Element(), window = new Element();
    const get = (id) => {
        if (!elements.has(id)) elements.set(id, new Element());
        return elements.get(id);
    };
    document.getElementById = get;
    document.createElement = () => new Element();
    document.cookie = 'csrftoken=fixture-csrf';
    document.visibilityState = 'visible';
    get('setup-handoff-panel').dataset.setupSsid = 'SmartCompanion Setup';
    get('setup-dashboard-link').href = canonical;
    window.setTimeout = (fn, ms) => { const id = ++serial; timers.set(id, {fn, at: now + ms}); return id; };
    window.clearTimeout = (id) => timers.delete(id);
    window.location = {assign: (url) => redirects.push(url)};
    const reply = (data, ok = true) => ({ok, json: async () => data});
    const hang = (signal) => new Promise((_resolve, reject) => {
        if (signal.aborted) reject(new Error('Aborted'));
        else signal.addEventListener('abort', () => reject(new Error('Aborted')), {once: true});
    });
    const fetch = async (url, options = {}) => {
        requests.push({url, options});
        if (url.endsWith('/wifi/scan/')) return reply({supported: true, networks: [{ssid}]});
        if (url.endsWith('/setup/status/')) return reply({state: 'SETUP_AP'});
        if (url.endsWith('/wifi/connect/')) return post ? post(options.signal, reply, hang) : reply({success: true});
        assert.equal(url, '/api/system/setup/handoff-status/');
        active++; maxActive = Math.max(active, maxActive);
        try {
            const next = statuses.shift();
            if (next === 'hang') return await hang(options.signal);
            if (next instanceof Error || next === undefined) throw new Error('DNS / network unavailable');
            return reply(next);
        } finally { active--; }
    };
    vm.runInNewContext(source, {document, window, fetch, AbortController, URL, Date: {now: () => now}});
    await flush();
    const tick = async (ms) => {
        const end = now + ms;
        for (let count = 0; count < 1000; count++) {
            const next = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
            if (!next) { now = end; await flush(); return; }
            const [id, timer] = next;
            now = timer.at; timers.delete(id); timer.fn(); await flush();
        }
        throw new Error('Unbounded timer loop');
    };
    const start = async () => {
        get('setup-network-list').children[0].emit('click');
        get('setup-wifi-password').value = 'synthetic-test-password';
        get('setup-connect-form').emit('submit', {preventDefault() {}});
        await flush();
    };
    return {get, window, document, tick, start, statuses, requests, redirects, timers,
        phase: () => get('setup-handoff-panel').dataset.phase,
        maxActive: () => maxActive,
        polls: () => requests.filter((r) => r.url.endsWith('/handoff-status/'))};
}

test('verified handoff uses API canonical URL, safe SSID text and persistent manual link', async () => {
    const destination = 'http://companion.local:8123/dashboard/';
    const b = await browser({statuses: [{...normal, canonical_url: destination}]});
    await b.start();
    assert.equal(b.phase(), 'SUCCESS');
    assert.equal(b.get('setup-handoff-title').textContent, `Connected to ${maliciousSsid}`);
    assert.equal(b.get('setup-dashboard-link').href, destination);
    assert.match(b.get('setup-handoff-guidance').textContent, /Open Dashboard/);
    assert.deepEqual(b.redirects, []);
    await b.tick(2000);
    assert.deepEqual(b.redirects, [destination]);
    assert.equal(b.timers.size, 0);
    assert.equal(b.polls()[0].options.cache, 'no-store');
    const post = b.requests.find((r) => r.options.method === 'POST');
    assert.equal(post.options.headers['X-CSRFToken'], 'fixture-csrf');
    assert.equal(post.options.credentials, 'same-origin');
    assert.equal(b.get('setup-wifi-password').value, '');
});

test('DNS loss and fetch timeout remain neutral; online/visibility events reuse one loop', async () => {
    const b = await browser({statuses: [new Error('DNS'), 'hang',
        {...normal, ready: false}, {...normal, state: 'CONNECTING'}, normal]});
    await b.start();
    assert.equal(b.phase(), 'WAITING_FOR_NETWORK');
    assert.match(b.get('setup-handoff-detail').textContent, /Wi-Fi settings.*return/);
    assert.equal(b.get('setup-dashboard-link').href, canonical);
    for (let i = 0; i < 10; i++) { b.window.emit('online'); b.document.emit('visibilitychange'); }
    await flush();
    assert.equal(b.polls().length, 2);
    await b.tick(4000); // Aborted request, not a stuck fetch.
    for (let i = 0; i < 2; i++) {
        b.document.emit('visibilitychange'); await flush();
        assert.equal(b.phase(), 'WAITING_FOR_NETWORK');
        assert.notEqual(b.get('setup-state-label').textContent, 'CONNECTED');
    }
    b.window.emit('online'); await flush();
    assert.equal(b.phase(), 'SUCCESS');
    assert.equal(b.maxActive(), 1);
});

test('timeout stops timers, keeps dashboard link and retry checks without resubmitting password', async () => {
    const b = await browser();
    await b.start(); await b.tick(90000);
    assert.equal(b.phase(), 'TIMED_OUT');
    assert.equal(b.timers.size, 0);
    assert.equal(b.get('setup-dashboard-link').href, canonical);
    assert.equal(b.get('setup-handoff-retry').classes.has('hidden'), false);
    const polls = b.polls().length;
    b.window.emit('online'); b.document.emit('visibilitychange'); await b.tick(100000);
    assert.equal(b.polls().length, polls);
    b.statuses.push(normal);
    for (let i = 0; i < 5; i++) b.get('setup-handoff-retry').emit('click');
    await flush();
    assert.equal(b.phase(), 'SUCCESS');
    assert.equal(b.polls().length, polls + 1);
    assert.equal(b.requests.filter((r) => r.options.method === 'POST').length, 1);
});

test('dropped POST response is bounded and does not prevent authoritative handoff', async () => {
    const b = await browser({post: (signal, _reply, hang) => hang(signal), statuses: ['hang', normal]});
    await b.start();
    assert.equal(b.phase(), 'CONNECTING');
    assert.match(b.get('setup-handoff-guidance').textContent, /browser cannot switch/);
    await b.tick(10000);
    assert.equal(b.phase(), 'SUCCESS');
    assert.equal(b.timers.size, 0);
    assert.deepEqual(b.redirects, [canonical]);
});

test('late unsuccessful POST cannot overwrite verified success', async () => {
    let finish;
    const b = await browser({post: () => new Promise((resolve) => { finish = resolve; }), statuses: [normal]});
    await b.start(); await b.tick(1200);
    assert.equal(b.phase(), 'SUCCESS');
    finish({ok: false, json: async () => ({success: false})}); await flush();
    assert.equal(b.phase(), 'SUCCESS');
    await b.tick(4000);
    assert.deepEqual(b.redirects, [canonical]);
});

test('page hide aborts work; back-forward restore retains original deadline', async () => {
    const b = await browser({statuses: ['hang']});
    await b.start(); await b.tick(3000);
    b.window.emit('pagehide'); await flush();
    assert.equal(b.timers.size, 0);
    await b.tick(90000);
    b.window.emit('pageshow', {persisted: true}); await flush();
    assert.equal(b.phase(), 'TIMED_OUT');
    assert.equal(b.polls().length, 1);
    assert.equal(b.timers.size, 0);
});

test('unsafe canonical URL is ignored and manual link survives a redirect failure', async () => {
    const b = await browser({statuses: [{...normal, canonical_url: 'javascript:alert(1)'}]});
    b.window.location.assign = () => { throw new Error('Navigation blocked'); };
    await b.start();
    await assert.rejects(b.tick(2000), /Navigation blocked/);
    assert.equal(b.phase(), 'SUCCESS');
    assert.equal(b.get('setup-dashboard-link').href, canonical);
});
