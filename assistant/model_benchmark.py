"""Opt-in sequential Ollama comparison. No model downloads or settings writes."""
import json
import math
import os
from pathlib import Path
import re
import statistics
import socket
import subprocess
import threading
import time
from urllib import request
from urllib.parse import urlsplit

from assistant.ai_engine.local import LocalLLMEngine
from assistant.ai_engine.base import AIEngineResult, AIEngineStreamEvent
from assistant.execution_policy import get_ai_execution_plan


class BenchmarkError(ValueError):
    """Controlled, public-safe benchmark diagnostic."""


MODELS = ('qwen3.5:4b-q4_K_M', 'llama3.2:3b', 'llama3.2:1b')
# Public fixed prompts only. Never include user conversation or personal knowledge.
CASES = (
    ('general', 'Why does water freeze?', (r'cold|temperature|heat|energy',)),
    ('coding', 'Write a Python function that returns the square of its argument.', (r'def\s', r'\*|pow\(')),
    ('iot', 'What does MQTT do in an IoT system?', (r'publish|subscribe|broker',)),
    ('pi5_cpu', 'Name the Raspberry Pi 5 SoC, CPU cores and clock speed.', (r'BCM2712', r'Cortex.?A76', r'2\.4\s*GHz', r'quad|four|4')),
    ('pi5_io', 'Is RP1 the main CPU of a Raspberry Pi 5? Explain its role.', (r'I/?O|input.*output',)),
    ('esp32', 'Is an ESP32 a microcontroller or a Linux single-board computer?', (r'microcontroller',)),
    ('conversation', 'Hello, can you help me learn electronics?', (r'yes|help|sure|hello|absolutely|of course',)),
)


def assess(case, text, voice):
    patterns = next(item[2] for item in CASES if item[0] == case)
    wrong = case.startswith('pi5') and re.search(r'BCM2711|Cortex.?A72|RP1 is (?:the )?(?:main )?CPU', text, re.I)
    return {
        'rubric_pass': bool(text.strip()) and not wrong and all(re.search(p, text, re.I) for p in patterns),
        'spoken_concise': len(text.split()) <= 45 and not re.search(r'```|https?://', text) if voice else None,
        'word_count': len(text.split()), 'manual_review_required': True,
    }


class BenchmarkEngine(LocalLLMEngine):
    def _build_chat_request(self, *args, **kwargs):
        req = super()._build_chat_request(*args, **kwargs)
        payload = json.loads(req.data)
        payload['options'].update(temperature=0, seed=42)
        req.data = json.dumps(payload).encode()
        return req

    def generate_stream(self, query, *, system_instruction, num_predict, model, timeout_seconds, monitor=None):
        req = self._build_chat_request(self._build_messages(query, None, system_instruction=system_instruction),
                                       stream=True, num_predict=num_predict, model=model)
        started = time.perf_counter()
        first = None
        text = ''
        total_bytes = 0
        with request.urlopen(req, timeout=timeout_seconds) as response:
            sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
            if sock is None:
                raise BenchmarkError('Benchmark requires a cancellable HTTP socket')
            def abort():
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            timer = threading.Timer(max(0, timeout_seconds-(time.perf_counter()-started)), abort)
            timer.daemon = True
            if monitor is not None:
                monitor.abort = abort
            timer.start()
            try:
                while True:
                    line = response.readline(65537)
                    total_bytes += len(line)
                    if not line or len(line)>65536 or total_bytes>1_000_000:
                        raise BenchmarkError('Incomplete or oversized benchmark stream')
                    event = json.loads(line)
                    if not isinstance(event, dict) or event.get('error'):
                        raise BenchmarkError('Invalid benchmark stream')
                    content = event.get('message', {}).get('content', '')
                    if not isinstance(content, str):
                        raise BenchmarkError('Invalid benchmark content')
                    if content:
                        first = first if first is not None else time.perf_counter()
                        text += content
                        yield AIEngineStreamEvent(type='delta', text=content)
                    if event.get('done') is True:
                        if first is None or time.perf_counter()-started >= timeout_seconds:
                            raise BenchmarkError('Empty or expired benchmark response')
                        yield AIEngineStreamEvent(type='done', result=AIEngineResult(
                            text=text, engine='local', model=model, ttft_ms=round((first-started)*1000),
                            metrics=self._log_performance_metrics(event)))
                        return
            finally:
                if monitor is not None:
                    monitor.abort = lambda: None
                timer.cancel()
                timer.join(timeout=2)


def api(engine, path, payload=None):
    req = request.Request(engine._host + '/api/' + path,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={'Content-Type': 'application/json'})
    with request.urlopen(req, timeout=10) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise BenchmarkError('Ollama management response too large')
    return json.loads(data)


def residents(engine):
    data = api(engine, 'ps')
    if not isinstance(data, dict) or not isinstance(data.get('models'), list):
        raise BenchmarkError('Invalid resident model inventory')
    names = [item.get('name') or item.get('model') for item in data['models'] if isinstance(item, dict)]
    if any(not isinstance(name, str) or not name for name in names):
        raise BenchmarkError('Invalid resident model name')
    return names


def unload(engine, model):
    api(engine, 'generate', {'model': model, 'keep_alive': 0, 'stream': False})
    deadline = time.monotonic() + 10
    while model in residents(engine):
        if time.monotonic() >= deadline:
            raise BenchmarkError('Model did not unload within the deadline')
        threading.Event().wait(.1)


def maintenance_guard():
    if os.name != 'posix':
        raise BenchmarkError('Run the real benchmark on the Linux Pi in a maintenance window')
    for service in ('smart-ai-companion.service', 'smart-ai-companion-voice.service'):
        result = subprocess.run(['systemctl', 'is-active', service], capture_output=True, text=True, timeout=5)
        if result.stdout.strip() != 'inactive':
            raise BenchmarkError('Application and voice services must be inactive during model comparison')


def check_resources():
    plan = get_ai_execution_plan()
    if not plan.generation_allowed or plan.resource_profile not in {'BALANCED', 'PERFORMANCE'}:
        raise BenchmarkError('Benchmark stopped by existing thermal/memory resource policy')
    return plan


class Samples:
    """250ms sampled sum of Ollama server/runner RSS, not Python RSS or exact PSS."""
    def __init__(self):
        self.stop = threading.Event()
        self.peak_rss_bytes = None
        self.peak_temperature_c = None
        self.throttled_bits = None
        self.unsafe = False
        self.abort = lambda: None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def sample(self):
        rss = 0
        found = False
        for process in Path('/proc').glob('[0-9]*'):
            try:
                if 'ollama' in (process / 'comm').read_text(errors='replace').lower():
                    status = (process / 'status').read_text()
                    rss += int(re.search(r'^VmRSS:\s+(\d+)', status, re.M)[1]) * 1024
                    found = True
            except (OSError, AttributeError, TypeError, ValueError):
                continue
        if found:
            self.peak_rss_bytes = max(self.peak_rss_bytes or 0, rss)
        try:
            temperature = float(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000
            self.peak_temperature_c = max(self.peak_temperature_c or temperature, temperature)
            self.unsafe |= temperature >= 75
        except (OSError, ValueError):
            pass
        try:
            available = int(re.search(r'^MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M)[1])*1024
            self.unsafe |= available < 384 * 1024**2
        except (OSError, TypeError, ValueError):
            self.unsafe = True
        if self.unsafe:
            self.abort()

    def run(self):
        while not self.stop.is_set():
            self.sample()
            self.stop.wait(.25)

    def finish(self):
        self.sample()
        self.stop.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise BenchmarkError('Benchmark sampler did not stop')
        try:
            output = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True, text=True, timeout=2).stdout
            self.throttled_bits = int(output.strip().split('=')[1], 16)
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            pass
        return {'sampled_peak_ollama_rss_bytes': self.peak_rss_bytes,
                'peak_temperature_c': self.peak_temperature_c, 'throttled_bits': self.throttled_bits}


def summary(rows):
    summaries = []
    for model in MODELS:
        group = [r for r in rows if r['model'] == model]
        warm = [r for r in group if r['phase'] == 'warm']
        latencies = sorted(r['wall_ms'] for r in warm)
        if not latencies:
            continue
        summaries.append({'model': model, 'rubric_passes': sum(r['rubric_pass'] for r in group),
                          'samples': len(group), 'warm_p50_ms': statistics.median(latencies),
                          'warm_p95_ms': latencies[math.ceil(.95*len(latencies))-1],
                          'voice_concise_passes': sum(r['spoken_concise'] is True for r in group)})
    # Candidate only; heuristic checks and answers require human review.
    eligible = [s for s in summaries if s['rubric_passes'] == s['samples'] and s['voice_concise_passes'] == s['samples']//2]
    return {'type': 'summary', 'models': summaries,
            'candidate_for_manual_review': min(eligible, key=lambda s:s['warm_p50_ms'])['model'] if eligible else None,
            'settings_changed': False, 'cold_definition': 'Ollama model unloaded; OS page cache not flushed'}


def run_benchmark(emit, repeats=1):
    if not 1 <= repeats <= 3:
        raise BenchmarkError('Repeats must be between 1 and 3')
    maintenance_guard()
    # Reuse the microphone ownership lock without opening any audio device.
    from assistant.voice.push_to_talk import microphone_ownership
    with microphone_ownership():
        engine = BenchmarkEngine(model=MODELS[0], keep_alive='5m', num_ctx=2048, think=False, num_thread=4, num_batch=128)
        host = urlsplit(engine._host)
        if host.hostname not in {'localhost', '127.0.0.1', '::1'} or host.username or host.password:
            raise BenchmarkError('The Pi benchmark requires a local Ollama endpoint')
        if not set(MODELS) <= engine.available_models():
            raise BenchmarkError('All three benchmark models must already be installed; no downloads performed')
        previous = residents(engine)
        if len(previous) > 1:
            raise BenchmarkError('Multiple models already resident; stop and review memory ownership')
        rows = []
        active = None
        try:
            if previous:
                unload(engine, previous[0])
            for model in MODELS:
                for case, prompt, _ in CASES:
                    maintenance_guard()
                    for voice in (False, True):
                        for repeat in range(repeats):
                            active = model
                            unload(engine, model)
                            for phase in ('cold', 'warm'):
                                plan = check_resources()
                                if any(name != model for name in residents(engine)):
                                    raise BenchmarkError('Unexpected concurrent model load; stopping')
                                monitor = Samples()
                                monitor.sample()
                                if monitor.unsafe:
                                    raise BenchmarkError('Unsafe initial benchmark telemetry')
                                monitor.thread.start()
                                started = time.perf_counter()
                                result = None
                                try:
                                    instruction = ('Answer in one or two short spoken sentences, at most 40 words.' if voice else 'Answer concisely, at most 80 words.')
                                    events = engine.generate_stream(prompt, system_instruction=instruction,
                                        num_predict=min(64 if voice else 96, plan.max_output_tokens), model=model,
                                        timeout_seconds=min(120, plan.timeout_seconds), monitor=monitor)
                                    try:
                                        for event in events:
                                            if monitor.unsafe:
                                                raise BenchmarkError('Benchmark interrupted by thermal/memory guard')
                                            if event.type == 'done':
                                                result = event.result
                                    finally:
                                        events.close()
                                finally:
                                    wall_ms = round((time.perf_counter()-started)*1000)
                                    sampled = monitor.finish()
                                if result is None:
                                    raise BenchmarkError('Benchmark stream did not complete')
                                if monitor.unsafe or sampled['throttled_bits'] is not None and sampled['throttled_bits'] & 0xf:
                                    raise BenchmarkError('Unsafe benchmark telemetry; no recommendation produced')
                                check_resources()
                                row = {'type':'sample', 'model':model, 'case':case, 'voice':voice,
                                       'repeat':repeat, 'phase':phase, 'wall_ms':wall_ms,
                                       'ttft_ms':result.ttft_ms, 'response':result.text, 'metrics':result.metrics,
                                       'settings': {'num_ctx':2048, 'num_predict':min(64 if voice else 96, plan.max_output_tokens),
                                                    'temperature':0, 'seed':42, 'think':False, 'num_thread':4, 'num_batch':128},
                                       **sampled, **assess(case,result.text,voice)}
                                rows.append(row)
                                emit(row)
                unload(engine, model)
                active = None
            emit(summary(rows))
        finally:
            if active:
                unload(engine, active)
            if previous:
                # Restore residency only; never mutate the configured model.
                try:
                    check_resources()
                except BenchmarkError:
                    emit({'type':'residency_restore_skipped', 'reason':'resource_protection'})
                else:
                    api(engine, 'generate', {'model':previous[0], 'stream':False, 'keep_alive':'5m'})
