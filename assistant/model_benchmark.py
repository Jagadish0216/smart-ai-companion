"""Opt-in sequential Ollama comparison. No model downloads or settings writes."""
import json
import math
import os
from pathlib import Path
import re
import statistics
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from queue import Queue
from urllib.error import HTTPError, URLError
from urllib import request
from urllib.parse import urlsplit

from assistant.ai_engine.local import LocalLLMEngine
from assistant.ai_engine.base import AIEngineResult, AIEngineStreamEvent
from assistant.execution_policy import get_ai_execution_plan


class BenchmarkError(ValueError):
    """Controlled, public-safe benchmark diagnostic."""


class ResourceProtectionError(BenchmarkError):
    pass


class BenchmarkFailure(BenchmarkError):
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        super().__init__(json.dumps(diagnostic, sort_keys=True))

    def __str__(self):
        return json.dumps(self.diagnostic, sort_keys=True)


INVENTORY_TIMEOUT = 20.0
RESIDENCY_TIMEOUT = 20.0
UNLOAD_TIMEOUT = 90.0  # HTTP command and residency polling share this deadline.
UNLOAD_HTTP_TIMEOUT = 60.0
RESTORE_TIMEOUT = 180.0
RESOURCE_TIMEOUT = 15.0
_RESOURCE_SLOT = threading.Lock()


def failure(exc, operation, model, started, **context):
    if isinstance(exc, BenchmarkFailure):
        data = dict(exc.diagnostic)
        if data.get('model') is None:
            data['model'] = model
        for key, value in context.items():
            if data.get(key) is None:
                data[key] = value
        return BenchmarkFailure(data)
    reason = exc.reason if isinstance(exc, URLError) else exc
    category = ('timeout' if isinstance(reason, (TimeoutError, subprocess.TimeoutExpired)) else
                'interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else
                'http_error' if isinstance(exc, HTTPError) else
                'resource_protection' if isinstance(exc, ResourceProtectionError) else
                'validation_failure' if isinstance(exc, (BenchmarkError, ValueError)) else
                'connection_failure' if isinstance(exc, OSError) else 'unexpected_error')
    return BenchmarkFailure({'type':'error', 'operation':operation, 'model':model,
        'case':None, 'phase':None, 'elapsed_ms':round((time.monotonic()-started)*1000),
        'category':category, **context})


@contextmanager
def operation(emit, name, model=None, cleanup=False, **context):
    started = time.monotonic()
    emit({'type':'progress', 'operation':name, 'model':model, 'case':None, 'phase':None, **context})
    try:
        yield
    except BaseException as exc:
        diagnostic = failure(exc, name, model, started, **context)
        if cleanup:
            diagnostic.diagnostic['type'] = 'cleanup_error'
        try:
            emit(diagnostic.diagnostic)
        except Exception:
            pass  # A broken report sink must not replace the original failure.
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise diagnostic from None


def cleanup_call(emit, name, action, *, primary=None, model=None, **context):
    try:
        with operation(emit, name, model, cleanup=True, **context):
            return action()
    except BaseException:
        if primary is None:
            raise
        return None


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
        started = time.monotonic()
        try:
            yield from self._generate_stream(query, system_instruction=system_instruction,
                num_predict=num_predict, model=model, timeout_seconds=timeout_seconds, monitor=monitor)
        except Exception as exc:
            raise failure(exc, 'generation', model, started) from None

    def _generate_stream(self, query, *, system_instruction, num_predict, model, timeout_seconds, monitor=None):
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
            expired = threading.Event()
            def expire():
                expired.set()
                abort()
            timer = threading.Timer(max(0, timeout_seconds-(time.perf_counter()-started)), expire)
            timer.daemon = True
            if monitor is not None:
                monitor.abort = abort
            timer.start()
            try:
                while True:
                    try:
                        line = response.readline(65537)
                    except (OSError, ValueError):
                        if monitor is not None and monitor.unsafe:
                            raise ResourceProtectionError('Unsafe streaming telemetry') from None
                        if expired.is_set():
                            raise TimeoutError() from None
                        raise
                    if monitor is not None and monitor.unsafe:
                        raise ResourceProtectionError('Unsafe streaming telemetry')
                    if expired.is_set() or time.perf_counter()-started >= timeout_seconds:
                        raise TimeoutError()
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
                        if first is None:
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


def api(engine, path, payload=None, *, timeout=INVENTORY_TIMEOUT, operation_name=None, model=None):
    started = time.monotonic()
    operation_name = operation_name or ('residency_check' if path == 'ps' else 'model_inventory')
    req = request.Request(engine._host + '/api/' + path,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={'Content-Type': 'application/json'})
    try:
        if timeout <= 0:
            raise TimeoutError()
        with request.urlopen(req, timeout=timeout) as response:
            sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
            if sock is None:
                raise BenchmarkError('Management HTTP socket cannot be bounded')
            expired = threading.Event()
            def abort():
                expired.set()
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            guard = threading.Timer(max(0, timeout-(time.monotonic()-started)), abort)
            guard.daemon = True
            guard.start()
            try:
                data = response.read(1_000_001)
                if expired.is_set() or time.monotonic()-started >= timeout:
                    raise TimeoutError()
            except (OSError, ValueError):
                if expired.is_set():
                    raise TimeoutError() from None
                raise
            finally:
                guard.cancel()
                guard.join(timeout=2)
        if len(data) > 1_000_000:
            raise BenchmarkError('Ollama management response too large')
        parsed = json.loads(data)
        if not isinstance(parsed, dict) or parsed.get('error'):
            raise BenchmarkError('Ollama management operation failed')
        return parsed
    except Exception as exc:
        raise failure(exc, operation_name, model, started) from None


def installed_models(engine):
    data = api(engine, 'tags')
    if not isinstance(data, dict) or not isinstance(data.get('models'), list):
        raise BenchmarkError('Invalid installed model inventory')
    return {item.get('name') or item.get('model') for item in data['models'] if isinstance(item, dict)}


def residents(engine, *, timeout=RESIDENCY_TIMEOUT, operation_name='residency_check', model=None):
    data = api(engine, 'ps', timeout=timeout, operation_name=operation_name, model=model)
    if not isinstance(data, dict) or not isinstance(data.get('models'), list):
        raise BenchmarkError('Invalid resident model inventory')
    names = [item.get('name') or item.get('model') for item in data['models'] if isinstance(item, dict)]
    if any(not isinstance(name, str) or not name for name in names):
        raise BenchmarkError('Invalid resident model name')
    return names


def unload(engine, model):
    started = time.monotonic()
    deadline = started + UNLOAD_TIMEOUT
    api(engine, 'generate', {'model': model, 'keep_alive': 0, 'stream': False},
        timeout=min(UNLOAD_HTTP_TIMEOUT, deadline-time.monotonic()), operation_name='unload_http', model=model)
    while True:
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise failure(TimeoutError(), 'unload_residency', model, started)
        if model not in residents(engine, timeout=min(RESIDENCY_TIMEOUT, remaining), operation_name='unload_residency', model=model):
            return
        threading.Event().wait(min(.1, max(0, deadline-time.monotonic())))


def maintenance_guard():
    if os.name != 'posix':
        raise BenchmarkError('Run the real benchmark on the Linux Pi in a maintenance window')
    for service in ('smart-ai-companion.service', 'smart-ai-companion-voice.service'):
        result = subprocess.run(['systemctl', 'is-active', service], capture_output=True, text=True, timeout=5)
        # `failed` is quiescent too; neither state owns the microphone. Unknown,
        # activating/deactivating and missing units must still stop the run.
        if result.returncode != 3 or result.stdout.strip() not in {'inactive', 'failed'}:
            raise BenchmarkError('Application and voice services must be inactive during model comparison')


def check_resources():
    from django.conf import settings
    if settings.AI_ENGINE != 'local':
        raise ResourceProtectionError('Use the existing local-engine deployment environment for resource checks')
    started = time.monotonic()
    if not _RESOURCE_SLOT.acquire(blocking=False):
        raise BenchmarkError('Previous resource check is still pending')
    complete = threading.Event()
    result = Queue(maxsize=1)
    def collect():
        from django.db import close_old_connections
        try:
            result.put((True, get_ai_execution_plan()))
        except Exception as exc:
            result.put((False, exc))
        finally:
            try:
                close_old_connections()
            except Exception:
                pass
            finally:
                _RESOURCE_SLOT.release()
                complete.set()
    threading.Thread(target=collect, daemon=True, name='benchmark-resource-check').start()
    if not complete.wait(RESOURCE_TIMEOUT):
        raise failure(TimeoutError(), 'resource_check', None, started)
    valid, plan = result.get_nowait()
    if not valid:
        raise failure(plan, 'resource_check', None, started)
    if not plan.generation_allowed or plan.resource_profile not in {'BALANCED', 'PERFORMANCE'}:
        raise ResourceProtectionError('Benchmark stopped by existing thermal/memory resource policy')
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
        try:
            self.sample()
        finally:
            self.stop.set()
            self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise TimeoutError('Benchmark sampler did not stop')
        probe_started = time.monotonic()
        probe_status = 'ok'
        try:
            output = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True, text=True, timeout=2).stdout
            self.throttled_bits = int(output.strip().split('=')[1], 16)
        except subprocess.TimeoutExpired:
            probe_status = 'timeout'
        except (OSError, ValueError, IndexError):
            probe_status = 'unavailable'
        return {'sampled_peak_ollama_rss_bytes': self.peak_rss_bytes,
                'peak_temperature_c': self.peak_temperature_c, 'throttled_bits': self.throttled_bits,
                'throttling_probe': {'operation':'throttling_probe', 'category':probe_status,
                                     'elapsed_ms':round((time.monotonic()-probe_started)*1000)}}


def summary(rows, *, quick=False, complete=True):
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
                          'warm_ttft_p50_ms': statistics.median([r['ttft_ms'] for r in warm if r.get('ttft_ms') is not None]) if any(r.get('ttft_ms') is not None for r in warm) else None,
                          'warm_tokens_per_sec': statistics.median([r['metrics']['tokens_per_sec'] for r in warm if r.get('metrics',{}).get('tokens_per_sec') is not None]) if any(r.get('metrics',{}).get('tokens_per_sec') is not None for r in warm) else None,
                          'voice_samples': sum(bool(r.get('voice',False)) for r in group),
                          'voice_concise_passes': sum(r['spoken_concise'] is True for r in group)})
    # Candidate only; heuristic checks and answers require human review.
    eligible = [s for s in summaries if s['rubric_passes'] == s['samples'] and s['voice_concise_passes'] == s['voice_samples']]
    if not complete or len(summaries) != len(MODELS):
        eligible = []
    return {'type': 'summary', 'models': summaries,
            'mode':'quick' if quick else 'full', 'complete':complete,
            'candidate_for_manual_review': min(eligible, key=lambda s:s['warm_p50_ms'])['model'] if eligible else None,
            'settings_changed': False, 'cold_definition': 'Ollama model unloaded; OS page cache not flushed'}


def run_benchmark(emit, repeats=1, quick=False):
    if not 1 <= repeats <= 3:
        raise BenchmarkError('Repeats must be between 1 and 3')
    mode = 'quick' if quick else 'full'
    context = {'mode':mode, 'case':None, 'phase':None}
    with operation(emit, 'maintenance_check', **context):
        maintenance_guard()
    # Reuse the microphone ownership lock without opening any audio device.
    from assistant.voice.push_to_talk import microphone_ownership
    with microphone_ownership():
        engine = BenchmarkEngine(model=MODELS[0], keep_alive='5m', num_ctx=2048, think=False, num_thread=4, num_batch=128)
        host = urlsplit(engine._host)
        if host.hostname not in {'localhost', '127.0.0.1', '::1'} or host.username or host.password:
            raise BenchmarkError('The Pi benchmark requires a local Ollama endpoint')
        with operation(emit, 'model_inventory', **context):
            if not set(MODELS) <= installed_models(engine):
                raise BenchmarkError('All three benchmark models must already be installed; no downloads performed')
        with operation(emit, 'initial_residency', **context):
            previous = residents(engine)
        if len(previous) > 1:
            raise BenchmarkError('Multiple models already resident; stop and review memory ownership')
        rows = []
        active = None
        primary = None
        trials = [(case,prompt,voice) for case,prompt,_ in CASES for voice in (False,True)]
        if quick:
            trials = [(case,prompt,voice) for case,prompt,voice in trials
                      if (case,voice) in {('pi5_cpu',False),('iot',True),('esp32',True)}]
        try:
            if previous:
                active = previous[0]
                with operation(emit, 'unload_previous', active, **context):
                    unload(engine, active)
                active = None
            for model in MODELS:
                for case, prompt, voice in trials:
                    context.update(case=case, voice=voice, phase='cold')
                    with operation(emit, 'maintenance_check', model, **context):
                        maintenance_guard()
                    for repeat in range(repeats):
                        context['repeat'] = repeat
                        active = model
                        with operation(emit, 'unload', model, **context):
                            unload(engine, model)
                        for phase in ('cold', 'warm'):
                            context.update(phase=phase, repeat=repeat)
                            with operation(emit, 'resource_check', model, **context):
                                plan = check_resources()
                            with operation(emit, 'residency_check', model, **context):
                                if any(name != model for name in residents(engine, model=model)):
                                    raise BenchmarkError('Unexpected concurrent model load; stopping')
                            monitor = Samples()
                            with operation(emit, 'sampler_start', model, **context):
                                monitor.sample()
                                if monitor.unsafe:
                                    raise ResourceProtectionError('Unsafe initial benchmark telemetry')
                                monitor.thread.start()
                            started = time.perf_counter()
                            result = None
                            try:
                                instruction = ('Answer in one or two short spoken sentences, at most 40 words.' if voice else 'Answer concisely, at most 80 words.')
                                with operation(emit, 'generation', model, **context):
                                    events = engine.generate_stream(prompt, system_instruction=instruction,
                                        num_predict=min(64 if voice else 96, plan.max_output_tokens), model=model,
                                        timeout_seconds=min(120, plan.timeout_seconds), monitor=monitor)
                                    try:
                                        for event in events:
                                            if monitor.unsafe:
                                                raise ResourceProtectionError('Benchmark interrupted by thermal/memory guard')
                                            if event.type == 'done':
                                                result = event.result
                                    finally:
                                        cleanup_call(emit, 'stream_cleanup', events.close,
                                                     primary=sys.exc_info()[1], model=model, **context)
                            finally:
                                wall_ms = round((time.perf_counter()-started)*1000)
                                sampled = cleanup_call(emit, 'sampler_cleanup', monitor.finish,
                                                       primary=sys.exc_info()[1], model=model, **context)
                            with operation(emit, 'thermal_guard', model, **context):
                                if result is None:
                                    raise BenchmarkError('Benchmark stream did not complete')
                                if monitor.unsafe or sampled['throttled_bits'] is not None and sampled['throttled_bits'] & 0xf:
                                    raise ResourceProtectionError('Unsafe benchmark telemetry; no recommendation produced')
                            row = {'type':'sample', 'model':model, 'case':case, 'voice':voice,
                                   'mode':mode,
                                   'repeat':repeat, 'phase':phase, 'wall_ms':wall_ms,
                                   'ttft_ms':result.ttft_ms, 'response':result.text, 'metrics':result.metrics,
                                   'settings': {'num_ctx':2048, 'num_predict':min(64 if voice else 96, plan.max_output_tokens),
                                                'temperature':0, 'seed':42, 'think':False, 'num_thread':4, 'num_batch':128},
                                   **sampled, **assess(case,result.text,voice)}
                            rows.append(row)
                            emit(row)
                            with operation(emit, 'resource_check', model, **context):
                                check_resources()
                with operation(emit, 'unload', model, **context):
                    unload(engine, model)
                active = None
        except BaseException as exc:
            primary = exc
            try:
                emit(summary(rows, quick=quick, complete=False))
            except Exception:
                pass
            raise
        finally:
            cleanup_error = None
            if active:
                try:
                    cleanup_call(emit, 'cleanup_unload', lambda:unload(engine, active), model=active, **context)
                except BaseException as exc:
                    cleanup_error = exc
            if previous and cleanup_error is None and not isinstance(primary, (KeyboardInterrupt,SystemExit)):
                # Restore residency only; never mutate the configured model.
                try:
                    with operation(emit, 'resource_check', previous[0], cleanup=True, **context):
                        check_resources()
                    cleanup_call(emit, 'restore_residency', lambda:api(engine, 'generate',
                        {'model':previous[0], 'stream':False, 'keep_alive':'5m'}, timeout=RESTORE_TIMEOUT,
                        operation_name='restore_residency', model=previous[0]), model=previous[0], **context)
                except BaseException as exc:
                    cleanup_error = exc
            if cleanup_error is not None and primary is None:
                try:
                    emit(summary(rows, quick=quick, complete=False))
                except Exception:
                    pass
                raise cleanup_error
        emit(summary(rows, quick=quick))
