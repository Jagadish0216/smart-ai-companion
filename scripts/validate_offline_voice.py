#!/usr/bin/env python3
"""One isolated voice cycle, using the production pipeline; never a deployment tool."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager, redirect_stdout, redirect_stderr
from dataclasses import asdict
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
from threading import RLock
import time
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import compare_offline_models as models

MODEL = "llama3.2:1b"
FIXTURES = dict(models.FIXTURES, fact=(
    "Name India's capital. Is it north or south of the equator?", models.FIXTURES["fact"][1]),
    technical=("How do CPU cycles per second differ from instructions per second?", models.FIXTURES["technical"][1]),
    reasoning=("Three red and two blue balls. Remove two red. What fraction remains blue?", models.FIXTURES["reasoning"][1]),
    followup=("Which one keeps data without power, and why?", models.FIXTURES["followup"][1]),
    unknown=("Do my notes give the Lumen board's serial number?",
    "The supplied note has no serial number. Say it is unknown; never invent one."),
    concise=("In one sentence, name the largest planet in our solar system.",
             "Jupiter; exactly one natural, complete sentence, no unrelated facts."))
REVIEW = ("accuracy", "completeness", "relevance", "hallucinations_absent",
          "grounding", "memory_context", "truncation_absent", "natural_first_sentence",
          "natural_audio", "tft_synchronization")
REQUIRED = ("capture_start", "capture_end", "stt_start", "stt_end", "dispatch",
            "llm_first_text", "first_complete_sentence_ready", "piper_first_audio",
            "alsa_first_pcm_accepted", "alsa_drain_end", "cycle_end")
UNOBSERVED = {"wake_detection": "Manual single cycle: wake engine is not invoked.",
              "human_utterance_end": "No acoustic ground truth; capture includes warm-up and VAD/fixed wait.",
              "vad_end_delay_ms": "Cannot subtract a human utterance end that was not measured.",
              "actual_audible_start": "Requires external synchronized acoustic measurement.",
              "actual_spoken_end": "ALSA drain is software evidence, not acoustic verification.",
              "tft_visible_transition": "State calls are observed; physical pixels/ACK not measured."}


class Trace:
    def __init__(self, output=None, clock=time.monotonic):
        self.clock, self.origin, self.output = clock, clock(), output
        self.lock = RLock()
        self.data = {"status": "incomplete", "mode": None, "events_ms": {}, "display_calls": [],
                     "unobservable": UNOBSERVED, "human_review": dict.fromkeys(REVIEW),
                     "quality_accepted": False, "physical_latency_verified": False}

    def save(self):
        with self.lock:
            if self.output:
                pending = self.output.with_suffix(".pending")
                pending.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
                pending.replace(self.output)

    def update(self, **values):
        with self.lock:
            self.data.update(values)
            self.save()

    def mark(self, name):
        with self.lock:
            if name in self.data["events_ms"]:
                return
            self.data["events_ms"].setdefault(name, round((self.clock() - self.origin) * 1000, 3))
            self.save()

    def finish(self):
        events = self.data["events_ms"]
        missing = [name for name in REQUIRED if name not in events]
        chains = (REQUIRED[:6], ("dispatch", "piper_first_audio", "alsa_first_pcm_accepted",
                               "alsa_drain_end", "cycle_end"),
                  ("dispatch", "first_complete_sentence_ready", "cycle_end"))
        ordered = all(all(events[a] <= events[b] for a, b in zip(chain, chain[1:])
                          if a in events and b in events) for chain in chains)
        self.data.update(missing_events=missing, timing_order_valid=ordered,
                         status="sample_complete_quality_pending" if not missing and ordered else "incomplete")
        self.data["durations_ms"] = {name: round(events[end] - events[start], 3)
            if start in events and end in events else None
            for name, start, end in (
                ("capture_including_wait", "capture_start", "capture_end"),
                ("stt", "stt_start", "stt_end"),
                ("dispatch_to_text", "dispatch", "llm_first_text"),
                ("stt_to_pcm_submission", "stt_end", "alsa_first_pcm_accepted"),
                ("stt_to_drain", "stt_end", "alsa_drain_end"),
                ("cycle", "capture_start", "cycle_end"))}
        self.save()
        return self.data


def normalized(value):
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in value).split())


def public_transcript(value, expected):
    # Never send a misrecognized/private utterance to inference or persist it.
    if normalized(value) != normalized(expected):
        raise models.TrialRefused("transcription_did_not_match_public_fixture")
    return value


def require_resident(host, digest):
    actual = models.verify_identity(MODEL, models.inventory(host)[MODEL], digest)
    resident = models.metadata(host, "/api/ps").get("models", [])
    models.verify_residents(resident, MODEL, actual)
    if len(resident) != 1 or resident[0].get("context_length") != 4096:
        raise models.TrialRefused("selected_model_must_already_be_resident_context_4096")
    return resident


@contextmanager
def loopback_only():
    """Guard Python sockets as well as urllib proxies/redirects in this child."""
    original = socket.socket.connect
    original_ex = socket.socket.connect_ex
    def checked(operation, sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            try:
                allowed = ipaddress.ip_address(address[0]).is_loopback
            except ValueError:
                allowed = address[0] == "localhost"
            if not allowed:
                raise models.TrialRefused("non_loopback_network_refused")
        elif sock.family != getattr(socket, "AF_UNIX", None):
            raise models.TrialRefused("non_loopback_network_refused")
        return operation(sock, address)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), models.NoRedirect())
    with patch.object(socket.socket, "connect", lambda s, a: checked(original, s, a)), \
         patch.object(socket.socket, "connect_ex", lambda s, a: checked(original_ex, s, a)), \
         patch.object(urllib.request, "urlopen", opener.open):
        yield


@contextmanager
def observe_pipeline(voice, trace, expected, synthetic=False):
    """Wrap existing calls; do not change capture, packing, PCM, gain or TFT protocol."""
    real_timed, real_buffer, real_player = voice._timed_call, voice.StreamingSpeechBuffer, voice.PersistentAlsaPlayer
    def timed(timings, stage, operation, *args, **kwargs):
        name = {"record": "capture", "stt": "stt"}.get(stage)
        if name:
            trace.mark(name + "_start")
        result = real_timed(timings, stage, operation, *args, **kwargs)
        if name:
            trace.mark(name + "_end")
        if stage == "stt":
            return public_transcript(result, expected)
        if stage == "tts":
            trace.mark("piper_first_audio")  # Whole first WAV returned, not first synthesized sample.
        return result
    class Buffer(real_buffer):
        def feed(self, *args, **kwargs):
            units = super().feed(*args, **kwargs)
            if self.first_complete_unit is not None:
                trace.mark("first_complete_sentence_ready")
            return units
    class Player(real_player):
        def write_file(self, path, *, on_progress=None):
            def progress(written, total):
                if written:
                    trace.mark("alsa_first_pcm_accepted")
                if on_progress:
                    on_progress(written, total)
            return super().write_file(path, on_progress=progress)
        def finish(self):
            result = super().finish()
            trace.mark("alsa_drain_end")
            return result
    def summary(timings):
        trace.data["legacy_timings_seconds_or_named_scalar"] = {
            key: value for key, value in timings.items() if type(value) in (int, float)}
    with ExitStack() as stack:
        stack.enter_context(patch.object(voice, "_timed_call", timed))
        stack.enter_context(patch.object(voice, "StreamingSpeechBuffer", Buffer))
        stack.enter_context(patch.object(voice, "_print_timing_summary", summary))
        if not synthetic:
            stack.enter_context(patch.object(voice, "PersistentAlsaPlayer", Player))
        yield


def seed_context(fixture):
    from conversations.models import Conversation, Message
    from knowledge_base.models import Document, KnowledgeChunk
    doc = Document.objects.create(filename="public-voice-fixture.md", status="INDEXED", source_identifier="audit:synthetic")
    KnowledgeChunk.objects.create(document=doc, chunk_index=0,
        content="The Lumen board uses 3.3 volts. Do not connect it directly to a 5 volt signal.")
    conversation = Conversation.objects.create(title="Public voice validation")
    history = {"followup": [("USER", "We are comparing RAM and an SSD."),
                             ("AI", "RAM holds working data; an SSD stores files.")],
               "memory": [("USER", "Our project codename is Amber Finch.")] +
                         [("USER", "Synthetic unrelated context.")] * 6}.get(fixture, [])
    for sender, text in history:
        Message.objects.create(conversation=conversation, sender=sender, text=text)
    return conversation.id


def configure(settings, host):
    settings.AI_ENGINE, settings.OLLAMA_HOST, settings.OLLAMA_MODEL = "local", host, MODEL
    settings.OLLAMA_NUM_THREAD = settings.OLLAMA_NUM_BATCH = None
    settings.OLLAMA_TIMEOUT = settings.AI_LOCAL_GENERATION_TIMEOUT_SECONDS = 15
    settings.OLLAMA_KEEP_ALIVE, settings.OLLAMA_THINK = "60s", False
    settings.ONLINE_RETRIEVAL_ENABLED = settings.ONLINE_KNOWLEDGE_AUTO_PERSIST = False
    settings.DEVICE_CONTROL_ENABLED = False
    settings.MQTT_HOST, settings.SEARXNG_BASE_URL = "127.0.0.1", "http://127.0.0.1:8888"
    settings.RAG_ENABLED = True
    settings.STT_TIMEOUT = settings.TTS_TIMEOUT = 15


def run_cycle(args, trace):
    from scripts import voice_loop as voice
    from assistant.ai_engine import get_engine, reset_engine
    from assistant.voice.factory import reset_voice_providers
    from django.conf import settings
    from unittest.mock import MagicMock
    expected, reference = FIXTURES[args.fixture]
    trace.data.update(mode="synthetic" if args.synthetic else "physical", fixture=args.fixture,
                      public_question=expected, reference_criteria=reference,
                      synthetic_context=True, model=MODEL, model_digest=getattr(args, "digest", None))
    config = voice.VoiceLoopConfig.from_settings(require_capture=not args.synthetic)
    if args.synthetic:
        from dataclasses import replace
        config = replace(config, audio_backend="alsa")
    elif settings.STT_ENGINE != "whisper_cpp" or settings.TTS_ENGINE != "piper" or config.audio_backend != "alsa":
        raise models.TrialRefused("real_whisper_piper_alsa_required")
    if max(config.record_seconds, config.capture_vad_max_seconds, config.capture_vad_start_timeout_seconds) > 10:
        raise models.TrialRefused("capture_configuration_exceeds_short_trial_limit")
    trace.data["voice_config"] = {key: value for key, value in asdict(config).items()
                                  if key not in ("input_source", "alsa_capture_device", "alsa_playback_device")}
    trace.data["capture_mode"] = "vad" if config.capture_vad_enabled else "fixed"
    display = None
    with ExitStack() as stack:
        if args.synthetic:
            tts = MagicMock()
            tts.synthesize.return_value = b"synthetic-not-audio"
            stt = MagicMock()
            stt.transcribe.return_value = expected
            for name in ("record_audio", "record_audio_alsa", "record_audio_with_capture_vad", "normalize_audio", "prepare_alsa_playback"):
                stack.enter_context(patch.object(voice, name))
            stack.enter_context(patch.object(voice, "trim_pcm_edges", return_value=(0, 0)))
            stack.enter_context(patch.object(voice, "get_stt_provider", return_value=stt))
            stack.enter_context(patch.object(voice, "get_tts_provider", return_value=tts))
            player = MagicMock()
            def write_file(path, **kwargs):
                trace.mark("alsa_first_pcm_accepted")
            player.write_file.side_effect = write_file
            player.finish.side_effect = lambda: trace.mark("alsa_drain_end")
            stack.enter_context(patch.object(voice, "PersistentAlsaPlayer", return_value=player))
            answer = "New Delhi is the capital of India, and it lies north of the equator."
            def events(*unused, **kwargs):
                trace.mark("dispatch")
                yield {"type": "start", "conversation_id": 1}
                trace.mark("llm_first_text")
                for part in (answer[:30], answer[30:]):
                    yield {"type": "delta", "text": part}
                yield {"type": "done", "response": answer, "conversation_id": 1}
            stack.enter_context(patch.object(voice.AssistantService, "process_message_stream", side_effect=events))
        else:
            reset_engine()
            engine = get_engine()
            builder, generate = engine._build_chat_request, engine.generate_stream
            requests = []
            def build(*a, **kw):
                if requests:
                    raise models.TrialRefused("multiple_inferences_refused")
                from scripts.benchmark_inference_ab import telemetry
                point = telemetry()
                reason = models.pressure_reason(point, starting=True)
                trace.update(pre_inference_telemetry=point)
                if reason:
                    raise models.TrialRefused(reason)
                require_resident(args.host, args.digest)
                request = builder(*a, **kw)
                payload = json.loads(request.data)
                if payload.get("model") != MODEL:
                    raise models.TrialRefused("model_selection_changed")
                payload["options"].update(models.request_options())
                # Respect a smaller protective-resource generation budget.
                payload["options"]["num_predict"] = min(48, kw.get("num_predict") or 48)
                request.data = json.dumps(payload).encode()
                requests.append(True)
                trace.update(request_controls={k: payload.get(k) for k in ("model", "options", "think", "keep_alive")})
                trace.mark("inference_request_ready")
                return request
            def streaming(*a, **kw):
                for event in generate(*a, **kw):
                    if event.type == "delta" and any(c.isalnum() for c in event.text or ""):
                        trace.mark("llm_first_text")
                    if event.type == "done":
                        trace.update(ollama_metrics={k: v for k, v in event.result.metrics.items()
                            if type(v) in (int, float, bool) or k == "done_reason" and v in ("stop", "length")},
                            prompt_cache=models.prompt_accounting(event.result.metrics))
                    yield event
            stack.enter_context(patch.object(engine, "_build_chat_request", build))
            stack.enter_context(patch.object(engine, "generate_stream", streaming))
            service = voice.AssistantService.process_message_stream
            def service_stream(*a, **kw):
                trace.mark("dispatch")
                for event in service(*a, **kw):
                    if event.get("type") == "done":
                        trace.update(public_fixture_answer=event.get("response", "")[:8192],
                            routing={k: event.get(k) for k in
                            ("route", "num_predict", "rag_used", "memory_used", "history_used", "resource_profile", "output_limit_reached")})
                    yield event
            stack.enter_context(patch.object(voice.AssistantService, "process_message_stream", service_stream))
            display = voice.get_companion_display()
            trace.data["display_enabled"] = bool(getattr(display, "enabled", False))
        if display is not None:
            state_call = display.set_state
            def state(value):
                with trace.lock:
                    trace.data["display_calls"].append({"state": str(getattr(value, "value", value)),
                        "at_ms": round((trace.clock() - trace.origin) * 1000, 3)})
                    trace.save()
                return state_call(value)
            stack.enter_context(patch.object(display, "set_state", state))
        try:
            with observe_pipeline(voice, trace, expected, args.synthetic):
                voice.run_voice_cycle(config, conversation_id=None if args.synthetic else seed_context(args.fixture), display=display)
            trace.mark("cycle_end")
        finally:
            reset_voice_providers()
            if display is not None:
                display.close()
    result = trace.finish()
    if not args.synthetic:
        metrics = trace.data.get("ollama_metrics", {})
        load = metrics.get("load_duration_ms")
        trace.data["residency_label"] = "warm" if type(load) in (int, float) and 0 <= load <= 100 else "reload_or_unknown"
        if trace.data["residency_label"] != "warm":
            trace.data.update(status="invalid", reason="model_reloaded_or_load_unknown")
        if metrics.get("done_reason") == "length":
            trace.data["quality_warning"] = "generation_length_limit_reached_review_truncation"
        trace.save()
    return result


def child(args):
    # Parent-owned directory survives child termination only until parent finally cleanup.
    directory = Path(args.child_dir)
    os.environ["TMPDIR"] = os.environ["TEMP"] = os.environ["TMP"] = str(directory)
    tempfile.tempdir = str(directory)
    signal.signal(signal.SIGTERM, lambda *unused: (_ for _ in ()).throw(KeyboardInterrupt()))
    # Do this before bootstrap: the subprocess has no console and must not
    # configure application logging destinations or Windows console detection.
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    import config.settings as config_settings
    config_settings.LOGGING_CONFIG = None
    from scripts.validate_pi_performance import bootstrap
    bootstrap(directory)
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connections
    configure(settings, args.host)
    # voice_loop calls django.setup again; prevent duplicate logging setup while
    # stdout is suppressed (Windows Django color setup can probe the registry).
    settings.LOGGING_CONFIG = None
    trace = Trace(directory / "trace.json")
    try:
        with open(os.devnull, "w") as quiet, redirect_stdout(quiet), redirect_stderr(quiet), loopback_only():
            if not args.synthetic:
                call_command("migrate", verbosity=0, interactive=False)
            run_cycle(args, trace)
        return 0 if trace.data["status"] == "sample_complete_quality_pending" else 2
    except BaseException as exc:
        trace.data.update(status="aborted", reason=exc.reason if isinstance(exc, models.TrialRefused) else "cycle_failed_or_cancelled")
        trace.save()
        return 2
    finally:
        connections.close_all()


def monitor(process, sample, check_resident, report, *, clock=time.monotonic, sleep=time.sleep, timeout=60):
    if not 0 < timeout <= 60:
        raise ValueError("Monitor timeout must be positive and at most 60 seconds")
    deadline = clock() + timeout
    while process.poll() is None:
        point = sample()
        report["telemetry"].append(point)
        reason = models.pressure_reason(point)
        if reason:
            raise models.TrialRefused(reason)
        check_resident()
        if clock() >= deadline:
            raise models.TrialRefused("bounded_cycle_timeout")
        sleep(0.5)
    if clock() >= deadline:
        raise models.TrialRefused("bounded_cycle_timeout")


def run(args):
    report = {"status": "incomplete", "telemetry": [], "physical_latency_verified": False,
              "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
    from scripts.validate_pi_performance import isolated_database
    lock = None
    try:
        with isolated_database("voice-monitor-"):
            from scripts.benchmark_inference_ab import telemetry
            if not args.synthetic:
                if sys.platform != "linux" or not args.operator_live_trial:
                    raise models.TrialRefused("linux_operator_live_trial_required")
                lock = models.acquire_trial_lock()
                point = telemetry()
                report["telemetry"].append(point)
                reason = models.pressure_reason(point, starting=True)
                if reason:
                    raise models.TrialRefused(reason)
                args.digest = models.verify_identity(MODEL, models.inventory(args.host)[MODEL], "baf6a787fdff")
                require_resident(args.host, args.digest)
                print("One live trial: say only this public question after the microphone is ready:\n" + FIXTURES[args.fixture][0], flush=True)
            with tempfile.TemporaryDirectory(prefix="voice-cycle-") as directory:
                command = [sys.executable, str(Path(__file__).resolve()), "--child-dir", directory,
                           "--fixture", args.fixture, "--host", args.host]
                command += ["--synthetic"] if args.synthetic else ["--digest", args.digest, "--operator-live-trial"]
                process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           start_new_session=sys.platform == "linux")
                try:
                    if args.synthetic:
                        process.wait(timeout=30)
                    else:
                        monitor(process, telemetry, lambda: require_resident(args.host, args.digest), report)
                        point = telemetry()
                        report["telemetry"].append(point)
                        reason = models.pressure_reason(point)
                        if reason:
                            raise models.TrialRefused(reason)
                        require_resident(args.host, args.digest)
                    result = Path(directory) / "trace.json"
                    if result.is_file():
                        report["cycle"] = json.loads(result.read_text(encoding="utf-8"))
                    report["status"] = report.get("cycle", {}).get("status", "incomplete")
                    if process.returncode != 0:
                        report["status"] = "aborted"
                finally:
                    if sys.platform == "linux":
                        models.terminate(process)  # Also reap group descendants after leader exits.
                    elif process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                    result = Path(directory) / "trace.json"
                    if result.is_file():
                        report["cycle"] = json.loads(result.read_text(encoding="utf-8"))
    except BaseException as exc:
        report.update(status="aborted", reason=exc.reason if isinstance(exc, models.TrialRefused) else "trial_failed_or_cancelled")
    finally:
        if lock:
            lock.close()
    # Caller output must be new: never overwrite existing artifacts/settings.
    with Path(args.output).open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
    return 0 if report["status"] == "sample_complete_quality_pending" else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--synthetic", action="store_true", help="No mic, model, speaker or TFT; default mode")
    mode.add_argument("--operator-live-trial", action="store_true", help="ONE physical microphone/speaker cycle")
    parser.add_argument("--fixture", choices=FIXTURES, default="fact")
    parser.add_argument("--host", type=models.local_host, default="http://127.0.0.1:11434")
    parser.add_argument("--output", default="offline-voice-result.json")
    parser.add_argument("--child-dir", help=argparse.SUPPRESS)
    parser.add_argument("--digest", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.synthetic = not args.operator_live_trial
    if args.synthetic and args.fixture != "fact":
        parser.error("Synthetic orchestration uses the fixed fact fixture only")
    if args.child_dir:
        return child(args)
    if Path(args.output).exists():
        parser.error("Choose a new report path; existing files are never overwritten")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
