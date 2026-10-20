"""Diagnostic orchestration tests; none claim physical latency or answer quality."""
from contextlib import ExitStack, redirect_stdout
import io
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from scripts import validate_offline_voice as voice
from scripts import voice_loop

DIGEST = "baf6a787fdff" + "0" * 52
RESIDENT = {"name": voice.MODEL, "digest": DIGEST, "context_length": 4096}


def point(**changes):
    result = dict(temperature_c=45.0, ram_available_bytes=1024**3, measured_arm_hz=2400000000,
                  throttled=dict.fromkeys(("under_voltage", "frequency_capped", "currently_throttled", "soft_temperature_limit"), False))
    result.update(changes)
    return result


@override_settings(VOICE_AUDIO_BACKEND="alsa", VOICE_RECORD_SECONDS=5,
                   VOICE_CAPTURE_VAD_ENABLED=False, VOICE_CAPTURE_VAD_MAX_SECONDS=10,
                   VOICE_CAPTURE_VAD_START_TIMEOUT_SECONDS=5)
class OfflineVoiceValidationTests(SimpleTestCase):
    def test_synthetic_reuses_streaming_cycle_without_devices_or_network(self):
        trace = voice.Trace()
        with patch.object(voice_loop, "get_companion_display") as display, \
             patch.object(voice.models, "metadata", side_effect=AssertionError("network")), \
             redirect_stdout(io.StringIO()):
            report = voice.run_cycle(SimpleNamespace(synthetic=True, fixture="fact"), trace)
        self.assertEqual(report["status"], "sample_complete_quality_pending")
        self.assertFalse(report["physical_latency_verified"])
        self.assertFalse(report["quality_accepted"])
        self.assertFalse(report["missing_events"])
        self.assertTrue(report["timing_order_valid"])
        self.assertGreater(report["legacy_timings_seconds_or_named_scalar"]["playback_chunks"], 0)
        display.assert_not_called()

    def test_missing_event_never_reports_completion(self):
        trace = voice.Trace()
        for name in voice.REQUIRED[:-1]:
            trace.mark(name)
        self.assertEqual(trace.finish()["status"], "incomplete")
        self.assertEqual(trace.data["missing_events"], ["cycle_end"])

    def test_bad_order_is_incomplete_but_clauses_may_precede_complete_sentence(self):
        trace = voice.Trace()
        trace.data["events_ms"] = {key: i for i, key in enumerate(voice.REQUIRED)}
        trace.data["events_ms"]["stt_end"] = -1
        self.assertFalse(trace.finish()["timing_order_valid"])
        trace.data["events_ms"] = {key: i for i, key in enumerate(voice.REQUIRED)}
        trace.data["events_ms"]["first_complete_sentence_ready"] = 9.5
        self.assertTrue(trace.finish()["timing_order_valid"])

    def test_first_occurrence_and_atomic_partial_reporting(self):
        values = iter((10.0, 10.5, 11.0))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trace.json"
            trace = voice.Trace(output, clock=lambda: next(values))
            trace.mark("capture_start")
            trace.mark("capture_start")
            self.assertEqual(trace.data["events_ms"]["capture_start"], 500)
            self.assertIn('"status": "incomplete"', output.read_text())
            self.assertFalse(output.with_suffix(".pending").exists())

    def test_private_or_misrecognized_transcript_is_refused_without_echo(self):
        expected = voice.FIXTURES["fact"][0]
        self.assertEqual(voice.public_transcript(expected.upper(), expected), expected.upper())
        with self.assertRaises(voice.models.TrialRefused) as error:
            voice.public_transcript("private secret captured", expected)
        self.assertNotIn("private secret", str(error.exception))
        self.assertEqual(error.exception.reason, "transcription_did_not_match_public_fixture")

    def test_transcript_gate_prevents_dispatch(self):
        trace = voice.Trace()
        with voice.observe_pipeline(voice_loop, trace, voice.FIXTURES["fact"][0], True):
            with self.assertRaises(voice.models.TrialRefused):
                voice_loop._timed_call({}, "stt", lambda: "unintended microphone content")
        self.assertNotIn("dispatch", trace.data["events_ms"])
        self.assertNotIn("unintended", str(trace.data))

    def test_buffer_observer_does_not_change_units_or_confuse_clause_with_sentence(self):
        clause = "Solar panels convert sunlight into electricity by using photovoltaic cells, "
        plain = voice_loop.StreamingSpeechBuffer()
        expected = plain.feed(clause)
        trace = voice.Trace()
        with voice.observe_pipeline(voice_loop, trace, "", True):
            observed = voice_loop.StreamingSpeechBuffer()
            self.assertEqual(observed.feed(clause), expected)
            self.assertNotIn("first_complete_sentence_ready", trace.data["events_ms"])
            observed.feed("which convert light into electrical energy. ", final=True)
            self.assertIn("first_complete_sentence_ready", trace.data["events_ms"])

    def test_safety_refuses_hot_start_current_pressure_low_memory_and_unknown(self):
        for sample in (point(temperature_c=55), point(ram_available_bytes=128),
                       point(measured_arm_hz=None), point(throttled={"currently_throttled": True})):
            self.assertIsNotNone(voice.models.pressure_reason(sample, starting=True))
        self.assertEqual(voice.models.pressure_reason(point(temperature_c=70)), "temperature_safety_margin")
        safe = point()
        safe["throttled"]["throttling_occurred"] = True
        self.assertIsNone(voice.models.pressure_reason(safe, starting=True))

    def test_monitor_cancellation_safety_and_deadline(self):
        process = MagicMock()
        process.poll.return_value = None
        for sample in (point(temperature_c=70), point(ram_available_bytes=1)):
            with self.assertRaises(voice.models.TrialRefused):
                voice.monitor(process, lambda: sample, lambda: None, {"telemetry": []}, sleep=lambda _: None)
        clock = iter((0, 61))
        with self.assertRaisesMessage(voice.models.TrialRefused, "bounded_cycle_timeout"):
            voice.monitor(process, point, lambda: None, {"telemetry": []}, clock=lambda: next(clock), sleep=lambda _: None)
        with self.assertRaises(KeyboardInterrupt):
            voice.monitor(process, MagicMock(side_effect=KeyboardInterrupt), lambda: None, {"telemetry": []})

    def test_residency_exact_tag_digest_context_and_single_model(self):
        with patch.object(voice.models, "inventory", return_value={voice.MODEL: {"usable": True, "digest": DIGEST}}):
            for residents in ([], [dict(RESIDENT, name="qwen3.5:4b-q4_K_M")], [RESIDENT, RESIDENT],
                              [dict(RESIDENT, digest="a" * 64)], [dict(RESIDENT, context_length=2048)]):
                with patch.object(voice.models, "metadata", return_value={"models": residents}):
                    with self.assertRaises(voice.models.TrialRefused):
                        voice.require_resident("http://127.0.0.1:11434", DIGEST)
            with patch.object(voice.models, "metadata", return_value={"models": [RESIDENT]}):
                self.assertEqual(voice.require_resident("http://127.0.0.1:11434", DIGEST), [RESIDENT])

    def test_no_nonlocal_python_socket_or_remote_endpoint(self):
        for endpoint in ("https://127.0.0.1", "http://example.com", "http://127.0.0.1/private", "http://user@localhost"):
            with self.assertRaises(ValueError):
                voice.models.local_host(endpoint)
        with voice.loopback_only(), socket.socket() as sock:
            with self.assertRaises(voice.models.TrialRefused):
                sock.connect(("192.0.2.1", 80))

    def test_configuration_is_request_process_only_and_does_not_change_audio(self):
        config = SimpleNamespace(VOICE_AUDIO_BACKEND="alsa", VOICE_PLAYBACK_GAIN=4,
                                 COMPANION_DISPLAY_ENABLED=True, VOICE_COMPACT_STYLE=False)
        voice.configure(config, "http://127.0.0.1:11434")
        self.assertEqual(config.OLLAMA_MODEL, "llama3.2:1b")
        self.assertEqual(config.VOICE_PLAYBACK_GAIN, 4)
        self.assertTrue(config.COMPANION_DISPLAY_ENABLED)
        self.assertFalse(config.VOICE_COMPACT_STYLE)
        self.assertFalse(config.DEVICE_CONTROL_ENABLED)
        self.assertFalse(config.ONLINE_RETRIEVAL_ENABLED)

    def test_cleanup_only_targets_owned_session(self):
        process = MagicMock(pid=1234)
        with patch.object(voice.models.os, "killpg", create=True) as kill, \
             patch.object(voice.models.signal, "SIGKILL", 9, create=True):
            voice.models.terminate(process)
        self.assertEqual(kill.call_args_list[0].args, (1234, signal_value("SIGTERM")))
        self.assertEqual(kill.call_count, 2)
        process.wait.assert_called()

    def test_quality_requires_human_review_for_all_eight_public_cases(self):
        self.assertEqual(len(voice.FIXTURES), 8)
        self.assertIn("Cycles per second", voice.FIXTURES["technical"][1])
        self.assertIn("never invent", voice.FIXTURES["unknown"][1])
        trace = voice.Trace()
        self.assertTrue(all(value is None for value in trace.data["human_review"].values()))
        self.assertFalse(trace.data["quality_accepted"])


def signal_value(name):
    import signal
    return getattr(signal, name)


@override_settings(VOICE_AUDIO_BACKEND="alsa", VOICE_RECORD_SECONDS=5,
                   VOICE_CAPTURE_VAD_ENABLED=False, VOICE_CAPTURE_VAD_MAX_SECONDS=10,
                   VOICE_CAPTURE_VAD_START_TIMEOUT_SECONDS=5,
                   STT_ENGINE="whisper_cpp", TTS_ENGINE="piper")
class IsolatedPhysicalPathTests(TestCase):
    def test_real_service_candidate_request_and_audio_observers_with_fake_hardware(self):
        self.exercise_physical_path(False)

    def test_production_qwen_uses_same_pipeline_without_diagnostic_controls(self):
        self.exercise_physical_path(True)

    def exercise_physical_path(self, production):
        """Exercise physical code path, real policy/RAG/memory orchestration, no devices."""
        import urllib.request
        from assistant.ai_engine import AIEngineResult, AIEngineStreamEvent, reset_engine
        from assistant.ai_engine.local import LocalLLMEngine
        expected, _ = voice.FIXTURES["fact"]
        answer = "New Delhi is the capital of India, and it lies north of the equator."
        model = voice.QWEN if production else voice.MODEL
        engine = LocalLLMEngine(host="http://127.0.0.1:11434", model=model)
        def generated(query, *a, **kw):
            engine._build_chat_request([{"role": "user", "content": query}], stream=True,
                                       num_predict=kw.get("num_predict"), model=model)
            yield AIEngineStreamEvent(type="delta", text=answer[:35])
            yield AIEngineStreamEvent(type="delta", text=answer[35:])
            yield AIEngineStreamEvent(type="done", result=AIEngineResult(text=answer, engine="local",
                model=model, metrics={"load_duration_ms": 1.3, "done_reason": "stop",
                                          "prompt_eval_count": 140, "prompt_eval_cached_count": 120}))
        engine.generate_stream = generated
        class Player:
            def __init__(self, *a, **kw): pass
            def start(self): pass
            def check(self): pass
            def write_silence(self, *a): pass
            def write_file(self, path, *, on_progress=None):
                if on_progress: on_progress(10, 10)
            def finish(self): pass
            def abort(self): pass
        tts, stt, display = MagicMock(), MagicMock(), MagicMock()
        tts.synthesize.return_value = b"fake WAV"
        stt.transcribe.return_value = expected
        trace = voice.Trace()
        with ExitStack() as stack:
            for name in ("record_audio_alsa", "normalize_audio", "prepare_alsa_playback"):
                stack.enter_context(patch.object(voice_loop, name))
            stack.enter_context(patch.object(voice_loop, "trim_pcm_edges", return_value=(0, 0)))
            stack.enter_context(patch.object(voice_loop, "PersistentAlsaPlayer", Player))
            stack.enter_context(patch.object(voice_loop, "get_stt_provider", return_value=stt))
            stack.enter_context(patch.object(voice_loop, "get_tts_provider", return_value=tts))
            stack.enter_context(patch.object(voice_loop, "get_companion_display", return_value=display))
            stack.enter_context(patch("assistant.ai_engine.get_engine", return_value=engine))
            stack.enter_context(patch("assistant.services.get_engine", return_value=engine))
            stack.enter_context(patch("scripts.benchmark_inference_ab.telemetry", return_value=point()))
            stack.enter_context(patch.object(voice, "require_resident", return_value=[RESIDENT]))
            stack.enter_context(redirect_stdout(io.StringIO()))
            result = voice.run_cycle(SimpleNamespace(synthetic=False, fixture="fact", host="http://127.0.0.1:11434", digest=DIGEST, production_qwen=production), trace)
        reset_engine()
        self.assertEqual(result["status"], "sample_complete_quality_pending")
        self.assertEqual(result["residency_label"], "warm")
        self.assertEqual(result["public_fixture_answer"], answer)
        if production:
            self.assertNotIn("seed", result["request_controls"]["options"])
            self.assertEqual(result["request_controls"]["keep_alive"], engine._keep_alive)
        else:
            self.assertLessEqual(result["request_controls"]["options"]["num_predict"], 48)
        self.assertEqual(result["request_controls"]["model"], model)
        self.assertEqual(result["prompt_cache"]["cached_tokens_reported"], 120)
        self.assertEqual(result["routing"]["route"], "local")
        self.assertEqual([x["state"] for x in result["display_calls"]],
                         ["LISTENING", "THINKING", "SPEAKING", "READY"])
        self.assertFalse(result["physical_latency_verified"])
        display.close.assert_called_once()


class QwenDemoGuardTests(SimpleTestCase):
    def test_production_configuration_preserves_all_generation_and_audio_controls(self):
        controls = dict(AI_ENGINE="local", OLLAMA_MODEL=voice.QWEN,
            OLLAMA_HOST="http://127.0.0.1:11434", OLLAMA_KEEP_ALIVE="24h",
            OLLAMA_TIMEOUT=120, OLLAMA_NUM_CTX=4096, OLLAMA_NUM_BATCH=128,
            STT_TIMEOUT=75, TTS_TIMEOUT=45, RAG_ENABLED=True,
            VOICE_CAPTURE_VAD_START_THRESHOLD=.02, VOICE_STREAM_FIRST_SENTENCE_MIN_CHARS=30)
        settings = SimpleNamespace(**controls)
        voice.configure(settings, controls["OLLAMA_HOST"], production=True)
        for key, value in controls.items():
            self.assertEqual(getattr(settings, key), value)
        self.assertFalse(settings.ONLINE_RETRIEVAL_ENABLED)
        self.assertFalse(settings.DEVICE_CONTROL_ENABLED)
        settings.OLLAMA_MODEL = "wrong-model"
        with self.assertRaises(voice.models.TrialRefused):
            voice.configure(settings, controls["OLLAMA_HOST"], production=True)

    def test_strict_start_gate_and_missing_telemetry_fail_closed(self):
        self.assertIsNone(voice.demo_start_reason(point(temperature_c=49.99)))
        for sample in (point(temperature_c=50), point(temperature_c=None),
                       point(ram_available_bytes=1), point(throttled={})):
            self.assertIsNotNone(voice.demo_start_reason(sample))

    def test_ready_cue_is_once_and_never_echoes_child_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.json"
            poll = voice.ready_cue(path)
            with redirect_stdout(io.StringIO()) as output:
                poll()
                path.write_text('{"events_ms":{"capture_ready":100},"secret":"never echo"}')
                poll()
                poll()
            self.assertEqual(output.getvalue().count("MICROPHONE READY"), 1)
            self.assertNotIn("never echo", output.getvalue())

    def test_monitor_preserves_worker_failure_before_residency_check(self):
        process = MagicMock()
        process.poll.side_effect = [None, 2]
        check = MagicMock(side_effect=AssertionError("must not assert residency after failure"))
        voice.monitor(process, point, check, {"telemetry": []}, sleep=lambda _: None)
        check.assert_not_called()

    def test_monitor_120_second_bound_and_pressure_during_cycle(self):
        process = MagicMock()
        process.poll.return_value = None
        values = iter((0, 120))
        with self.assertRaisesMessage(voice.models.TrialRefused, "bounded_cycle_timeout"):
            voice.monitor(process, point, lambda: None, {"telemetry": []},
                clock=lambda: next(values), timeout=120, sleep=lambda _: None)
        with self.assertRaises(ValueError):
            voice.monitor(process, point, lambda: None, {"telemetry": []}, timeout=121)


    def test_parent_abort_reaps_only_owned_worker_and_preserves_partial_trace(self):
        from contextlib import nullcontext
        import json
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            args = SimpleNamespace(production_qwen=True, synthetic=False,
                operator_live_trial=True, fixture="fact", host="http://127.0.0.1:11434", output=output)
            process = MagicMock(pid=1234)
            def spawn(command, **kwargs):
                child_dir = Path(command[command.index("--child-dir")+1])
                (child_dir / "trace.json").write_text(json.dumps({"status":"incomplete",
                    "events_ms":{"capture_start":1}, "quality_accepted":False}))
                self.assertIn("--production-qwen",command)
                self.assertTrue(kwargs["start_new_session"])
                return process
            with patch("scripts.validate_pi_performance.isolated_database", return_value=nullcontext()), \
                 patch.object(voice.sys,"platform","linux"), \
                 patch.object(voice.models,"acquire_trial_lock") as lock, \
                 patch.object(voice.models,"inventory",return_value={voice.QWEN:{}}), \
                 patch.object(voice.models,"verify_identity",return_value="d8b0f5e9760c"+"0"*52), \
                 patch.object(voice,"require_resident"), \
                 patch("scripts.benchmark_inference_ab.telemetry",return_value=point()), \
                 patch.object(voice.subprocess,"check_output",return_value="7dbf2ad1cb5512d5fcad302dd931964b15e9e002"), \
                 patch.object(voice.subprocess,"Popen",side_effect=spawn) as popen, \
                 patch.object(voice,"monitor",side_effect=voice.models.TrialRefused("temperature_safety_margin")), \
                 patch.object(voice.models,"terminate") as terminate, redirect_stdout(io.StringIO()):
                self.assertEqual(voice.run(args),2)
            report=json.loads(output.read_text())
            self.assertEqual(report["reason"],"temperature_safety_margin")
            self.assertEqual(report["cycle"]["events_ms"],{"capture_start":1})
            popen.assert_called_once()
            terminate.assert_called_once_with(process)
            lock.return_value.close.assert_called_once()

    def test_qwen_residency_pins_tag_digest_and_context(self):
        digest = "d8b0f5e9760c"+"0"*52
        resident = dict(name=voice.QWEN,digest=digest,context_length=4096)
        with patch.object(voice.models,"inventory",return_value={voice.QWEN:{"usable":True,"digest":digest}}):
            for items in ([resident],[],[dict(resident,name=voice.MODEL)],
                          [dict(resident,digest="a"*64)],[dict(resident,context_length=2048)],
                          [resident,resident]):
                with patch.object(voice.models,"metadata",return_value={"models":items}):
                    if items == [resident]:
                        self.assertEqual(voice.require_resident("http://127.0.0.1:11434",digest,voice.QWEN),items)
                    else:
                        with self.assertRaises(voice.models.TrialRefused):
                            voice.require_resident("http://127.0.0.1:11434",digest,voice.QWEN)
