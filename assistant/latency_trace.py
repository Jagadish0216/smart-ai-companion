"""Opt-in, content-free voice timing. No I/O or clock reads when disabled."""
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from functools import wraps
import json
import math
import os
from pathlib import Path
from queue import Queue, Full, Empty
from threading import Lock, Thread
import time
import uuid

MAX_EVENTS = 512
MAX_BYTES = 128 * 1024
MAX_FILES = 8
SPANS = frozenset('rag playback_conversion capture capture_open capture_cleanup normalize stt_cli service_prepare conversation_prepare policy history user_persist routing memory response_persist resource_snapshot resource_metrics network_checks ancillary_checks provisioning_check piper_ready piper_exchange tts audio_prepare text_queue_wait audio_queue_wait player_start leading_silence speech_file drain display_callback serial_write'.split())
MARKS = frozenset('cycle_start cycle_end capture_first_frame calibration_end speech_detected capture_stopped endpoint_detected capture_ready ollama_request_ready llm_first_alphanumeric ollama_complete first_complete_sentence first_speech_unit piper_state first_audio_ready first_speech_pcm_accepted resource_cache_hit resource_cache_miss resource_state resource_policy cycle_config route stream_error'.split())
EVENT_NAMES = MARKS | {s + suffix for s in SPANS for suffix in ('_start', '_end')}
EVENT_NAMES = EVENT_NAMES | frozenset(("capture_warmup_start", "capture_warmup_end", "calibration_start",
                                    "speech_start_window_open", "minimum_speech_accepted"))
FIELDS = frozenset('load_duration_ms prompt_eval_duration_ms eval_duration_ms total_duration_ms prompt_eval_count prompt_eval_cached_count eval_count temperature_c ram_available_bytes ready_ms exchange_ms'.split())
_active = ContextVar('voice_latency_trace', default=None)
_lane = ContextVar('voice_latency_lane', default='main')
_speech_file = ContextVar('voice_latency_speech_file', default=False)
_store = None


def failure_kind(exc):
    for _ in range(6):
        if isinstance(exc, (KeyboardInterrupt, SystemExit)) or type(exc).__name__ == 'AudioCancelledError':
            return 'cancelled'
        if isinstance(exc, TimeoutError) or type(exc).__name__ in {'TimeoutExpired', 'EngineTimeoutError'}:
            return 'timeout'
        exc = getattr(exc, '__cause__', None)
        if exc is None:
            break
    return 'error'


class Trace:
    def __init__(self, *, clock=time.monotonic_ns, synthetic=False):
        self.clock = clock
        self.origin = clock()
        self.cycle_id = uuid.uuid4().hex
        self.synthetic = bool(synthetic)
        self.events = []
        self.counts = {}
        self.lock = Lock()
        self.dropped = 0
        self.status = 'running'
        self.elapsed_ms = None

    def emit(self, name, *, once=False, **values):
        if name not in EVENT_NAMES:
            return
        # Never wait for another audio thread or execute file I/O here.
        if not self.lock.acquire(blocking=False):
            self.dropped += 1
            return
        try:
            count = self.counts.get(name, 0)
            if once and count:
                return
            if len(self.events) >= MAX_EVENTS or count >= 16:
                self.dropped += 1
                return
            event = dict(name=name, at_ms=round((self.clock() - self.origin) / 1e6, 6),
                         lane=_lane.get() if _lane.get() in ('main', 'audio_prepare', 'audio_playback') else 'other')
            for key, value in values.items():
                if key in FIELDS and type(value) in (int, float) and math.isfinite(value) and value >= 0:
                    event[key] = value
                elif key in ('reused', 'generation_allowed', 'under_voltage', 'frequency_capped', 'currently_throttled', 'soft_temperature_limit') and type(value) is bool:
                    event[key] = value
                elif key == 'audio_backend' and value in ('alsa', 'pulse'):
                    event[key] = value
                elif key == 'input_mode' and value in ('fixed', 'vad', 'hands_free', 'audio_file', 'text'):
                    event[key] = value
                elif key == 'route' and value in ('local', 'local_rag', 'online', 'action', 'clarification'):
                    event[key] = value
                elif key == 'done_reason' and value in ('stop', 'length', 'load', 'unload'):
                    event[key] = value
                elif key == 'profile' and value in ('BALANCED', 'PERFORMANCE', 'ECO', 'PROTECTIVE', 'UNKNOWN'):
                    event[key] = value
                elif key == 'outcome' and value in ('ok', 'error', 'timeout', 'cancelled'):
                    event[key] = value
                elif key == 'reason' and value in ('silence', 'max_duration', 'start_timeout', 'preparation_activity', 'preparation_unstable', 'calibration_unreliable'):
                    event[key] = value
            self.events.append(event)
            self.counts[name] = count + 1
        finally:
            self.lock.release()

    def report(self):
        metrics = next((e for e in self.events if e['name'] == 'ollama_complete'), {})
        total, cached = metrics.get('prompt_eval_count'), metrics.get('prompt_eval_cached_count')
        known = type(total) is int and type(cached) is int and 0 <= cached <= total
        return dict(cache_accounting='reported_consistent' if known else 'unknown_or_invalid', schema_version=1, cycle_id=self.cycle_id, clock='monotonic_ns',
                    synthetic=self.synthetic, status=self.status, elapsed_ms=self.elapsed_ms,
                    events=list(self.events), dropped_events=self.dropped,
                    residency='unknown_not_probed', acoustic_onset='unobserved',
                    missing_events=[name for name in ('cycle_start', 'cycle_end', 'capture_first_frame', 'llm_first_alphanumeric',
                        'first_complete_sentence', 'first_speech_unit', 'first_speech_pcm_accepted')
                        if not self.counts.get(name)],
                    cache_counter_semantics='backend_reported; missing is unknown; no derived uncached claim',
                    interpretation='Concurrent lanes overlap. Do not sum stages as end-to-end latency. PCM acceptance is a pipe write, not acoustic onset.')


def mark(name, *, once=False, **values):
    trace = _active.get()
    if trace is not None:
        try:
            trace.emit(name, once=once, **values)
        except Exception:
            pass  # Timing must never break production work.


def span(name):
    if name not in SPANS:
        raise ValueError('Unknown timing span')
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if _active.get() is None:
                return fn(*args, **kwargs)
            mark(name + '_start')
            outcome = 'ok'
            token = _speech_file.set(True) if name == 'speech_file' else None
            try:
                return fn(*args, **kwargs)
            except BaseException as exc:
                outcome = failure_kind(exc)
                raise
            finally:
                mark(name + '_end', outcome=outcome)
                if token is not None:
                    _speech_file.reset(token)
        return wrapped
    return decorate


def submit(executor, fn, lane):
    if _active.get() is None:
        return executor.submit(fn)
    context = copy_context()
    def work():
        _lane.set(lane)
        return fn()
    return executor.submit(context.run, work)


@contextmanager
def cycle(trace=None):
    if _active.get() is not None or (trace is None and _store is None):
        yield _active.get()
        return
    trace = trace or Trace()
    token = _active.set(trace)
    mark('cycle_start')
    outcome = 'ok'
    try:
        yield trace
    except BaseException as exc:
        outcome = failure_kind(exc)
        raise
    finally:
        trace.status = outcome
        mark('cycle_end', outcome=outcome)
        trace.elapsed_ms = round((trace.clock() - trace.origin) / 1e6, 6)
        _active.reset(token)
        if _store is not None:
            try:
                _store.submit(trace.report())
            except Exception:
                pass


class TraceStore:
    """One bounded background writer; fixed ring files, no audio-thread disk waits."""
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.queue = Queue(maxsize=2)
        self.dropped_reports = 0
        self.write_errors = 0
        self.closed = False
        self.thread = Thread(target=self._write_loop, name='voice-latency-writer', daemon=True)
        self.thread.start()

    def submit(self, report):
        try:
            self.queue.put_nowait(report)
        except Full:
            self.dropped_reports += 1

    def _write_loop(self):
        while True:
            try:
                report = self.queue.get(timeout=0.1)
            except Empty:
                if self.closed:
                    return
                continue
            try:
                report = dict(report, storage_dropped_reports=self.dropped_reports, storage_write_errors=self.write_errors)
                data = json.dumps(report, separators=(',', ':'), allow_nan=False).encode('utf-8')
                if len(data) > MAX_BYTES:
                    self.dropped_reports += 1
                    continue
                slots = [self.directory / ('cycle-%d.json' % i) for i in range(MAX_FILES)]
                target = next((p for p in slots if not p.exists()), None)
                if target is None:
                    target = min(slots, key=lambda p: p.stat().st_mtime_ns)
                # Fixed temp slot bounds storage even after a previous interrupted write.
                temporary = self.directory / 'cycle.pending'
                temporary.unlink(missing_ok=True)
                with temporary.open('xb') as stream:
                    stream.write(data)
                os.replace(temporary, target)
            except Exception:
                self.write_errors += 1
            finally:
                self.queue.task_done()
            if self.closed and self.queue.empty():
                return

    def close(self):
        self.closed = True

    def flush(self, timeout=1):
        deadline = time.monotonic() + min(1, max(0, timeout))
        while self.queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.005)
        return self.queue.unfinished_tasks == 0


def configure(directory=None):
    """Explicit CLI activation; passing None disables new cycles immediately."""
    global _store
    previous = _store
    _store = TraceStore(directory) if directory is not None else None
    if previous is not None:
        previous.close()
    return previous


def first_text(text):
    trace = _active.get()
    if trace is not None and not trace.counts.get('llm_first_alphanumeric') and any(c.isalnum() for c in text):
        mark('llm_first_alphanumeric', once=True)


def pcm_accepted():
    if _speech_file.get():
        mark('first_speech_pcm_accepted', once=True)
