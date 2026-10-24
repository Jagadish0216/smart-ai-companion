#!/usr/bin/env python3
"""Opt-in public recording comparison. No capture, playback, LLM or downloads."""
from __future__ import annotations

import argparse
from array import array
import base64
from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import tempfile
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REFERENCE = "Hello mic testing one two three this is smart AI companion"
FILES = ("adapter.ort", "cross_kv.ort", "decoder_kv.ort", "encoder.ort",
         "frontend.model.ort", "frontend.weights.ort", "streaming_config.json", "tokenizer.bin")
# Official quantized_26_08_21 registry at upstream 234f60faa0eb388b01cdf7e60aca232af37aefda.
MODEL_MANIFEST = dict(zip(FILES, ((2870368, "XlWjTg=="), (5356536, "5ySK8g=="),
    (81878600, "Rn/VUA=="), (44148576, "41D2jQ=="), (26944, "WW2/2g=="),
    (7769464, "9pROuQ=="), (512, "dPbFiw=="), (249974, "B7s10Q=="))))


def verify_moonshine_files(path):
    for name, (size, _) in MODEL_MANIFEST.items():
        if not (path/name).is_file() or (path/name).stat().st_size != size:
            raise ValueError("reviewed_small_streaming_english_files_required")
    import google_crc32c  # Already a declared base dependency, isolated worker only.
    for name, (_, expected) in MODEL_MANIFEST.items():
        checksum = google_crc32c.Checksum()
        with (path/name).open("rb") as source:
            for block in iter(lambda: source.read(1024*1024), b""):
                checksum.update(block)
        if base64.b64encode(checksum.digest()).decode("ascii") != expected:
            raise ValueError("moonshine_model_integrity_mismatch")


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024*1024), b""):
            digest.update(block)
    return dict(bytes=path.stat().st_size, sha256=digest.hexdigest())


def words(text):
    numbers = dict(zip(map(str, range(11)), "zero one two three four five six seven eight nine ten".split()))
    return [numbers.get(w, w) for w in re.findall(r"[a-z0-9]+", text.lower())]


def word_errors(reference, transcript):
    """Levenshtein alignment; ties prefer substitution, then deletion, insertion."""
    a, b = words(reference), words(transcript)
    if not a or max(len(a), len(b)) > 256:
        raise ValueError("bounded_nonempty_reference_required")
    rows = [[(0, 0, j) for j in range(len(b)+1)]]
    for i, source in enumerate(a, 1):
        row = [(0, i, 0)]
        for j, target in enumerate(b, 1):
            if source == target:
                row.append(rows[-1][j-1])
            else:
                s, d, ins = rows[-1][j-1]
                candidates = [(s+1, d, ins)]
                s, d, ins = rows[-1][j]
                candidates.append((s, d+1, ins))
                s, d, ins = row[-1]
                candidates.append((s, d, ins+1))
                row.append(min(candidates, key=sum))
        rows.append(row)
    s, d, ins = rows[-1][-1]
    return dict(wer=(s+d+ins)/len(a), substitutions=s, deletions=d, insertions=ins,
                reference_words=len(a), normalization="ascii_lower_punctuation_split_digits_0_to_10")


def read_audio(path):
    if path.stat().st_size > 2_000_000:
        raise ValueError("audio_too_large")
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 16000, "NONE"):
            raise ValueError("existing_normalized_16khz_mono_pcm16_required")
        if not 0 < wav.getnframes() <= 30*16000:
            raise ValueError("audio_duration_outside_30_second_limit")
        pcm = wav.readframes(wav.getnframes())
        if len(pcm) != wav.getnframes()*2:
            raise ValueError("truncated_audio")
    data = array("h", pcm)
    if sys.byteorder != "little":
        data.byteswap()
    return [sample/32768 for sample in data]


@contextmanager
def no_network():
    """Block Python network sockets; explicit local native models never fetch assets."""
    from unittest.mock import patch
    def refused(*args, **kwargs):
        raise RuntimeError("network_forbidden")
    with patch.object(socket.socket, "connect", refused), patch.object(socket.socket, "connect_ex", refused):
        yield


def moonshine_transcribe(path, samples):
    # No download helper, MicTranscriber, diarization or recommended-model selection.
    for name in FILES:
        if not (path / name).is_file():
            raise ValueError("local_small_streaming_files_missing")
    if importlib.metadata.version("moonshine-voice") != "0.1.5":
        raise ValueError("reviewed_moonshine_version_0_1_5_required")
    verify_moonshine_files(path)
    from moonshine_voice import Transcriber, ModelArch
    started = time.monotonic()
    transcriber = Transcriber(model_path=str(path), model_arch=ModelArch.SMALL_STREAMING)
    loaded = time.monotonic()
    try:
        transcriber.start()
        for offset in range(0, len(samples), 1600):
            transcriber.add_audio(samples[offset:offset+1600], 16000)
        final = transcriber.stop()
        finished = time.monotonic()
        if final is None or any(not line.is_complete for line in final.lines):
            raise ValueError("final_transcript_unavailable")
        text = " ".join(line.text for line in final.lines)
        if len(text) > 8192:
            raise ValueError("transcript_too_large")
        return dict(raw_transcription=text, model_load_seconds=loaded-started,
                    transcription_seconds=finished-loaded, final_ready_seconds=finished-started,
                    final_ready_at=finished,
                    timing_definition="fast_file_feed_100ms_chunks_not_real_time_capture",
                    transcription_includes_model_load=False)
    finally:
        transcriber.close()


def whisper_transcribe(binary, model, audio, directory):
    if model.name != "ggml-base.en.bin" or not model.is_file() or not binary.is_file():
        raise ValueError("explicit_existing_whisper_base_en_required")
    # Native diagnostics are temporary only and never echoed or exported.
    stdout, stderr = directory / "stdout", directory / "stderr"
    started = time.monotonic()
    with stdout.open("wb") as out, stderr.open("wb") as err:
        result = subprocess.run([str(binary), "-m", str(model), "-f", str(audio), "-t", "4", "-nt"],
                                stdout=out, stderr=err, timeout=90, check=False)
    elapsed = time.monotonic()-started
    if result.returncode or stdout.stat().st_size > 8192 or stderr.stat().st_size > 65536:
        raise ValueError("whisper_failed_or_output_limit")
    text = stdout.read_text(encoding="utf-8").strip()
    match = re.search(r"load time\s*=\s*([0-9.]+)\s*ms", stderr.read_text(encoding="utf-8", errors="replace"))
    return dict(raw_transcription=text, model_load_seconds=float(match[1])/1000 if match else None,
                transcription_seconds=elapsed, final_ready_seconds=elapsed,
                final_ready_at=started+elapsed,
                timing_definition="whisper_cli_wall_including_load; load_only_if_native_counter_present",
                transcription_includes_model_load=True)


def child(args):
    result = {"status": "aborted", "engine": args.worker_engine}
    started, cpu = time.monotonic(), time.process_time()
    try:
        if args.worker_engine == "moonshine" and (sys.prefix == sys.base_prefix or sys.prefix == args.baseline_prefix):
            raise ValueError("isolated_candidate_environment_required")
        audio = args.audio
        if getattr(args, "audio_variant", None):
            from scripts.whisper_audio_diagnostic import prepare_variant, analyze, filter_chain
            before = analyze(audio)
            result["audio_processing"] = dict(variant=args.audio_variant, source=before,
                filter_chain=filter_chain(args.audio_variant,before["peak"],before["channels"]))
            try:
                audio, result["audio_processing"] = prepare_variant(audio, args.child_dir, args.audio_variant)
            except Exception:
                result["reason"] = "audio_preparation_failed"
                raise
        samples = read_audio(audio)
        with no_network():
            if args.worker_engine == "moonshine":
                result["model_files"] = {name: fingerprint(args.moonshine_model/name) for name in FILES}
                measured = moonshine_transcribe(args.moonshine_model, samples)
            else:
                result["model_file"] = fingerprint(args.whisper_model)
                measured = whisper_transcribe(args.whisper_bin, args.whisper_model, audio, args.child_dir)
        final_at = measured.pop("final_ready_at")
        result.update(measured, end_to_final_text_seconds=final_at-started,
                      accuracy=word_errors(args.reference, measured["raw_transcription"]), status="sample_complete")
    except BaseException as exc:
        result.update(reason="cancelled" if isinstance(exc, KeyboardInterrupt) else result.get("reason", "stt_execution_failed"))
    finally:
        import resource
        own, children = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
        result.update(worker_wall_seconds=time.monotonic()-started,
            worker_cpu_seconds=time.process_time()-cpu+children.ru_utime+children.ru_stime,
            peak_process_rss_kib=max(own.ru_maxrss, children.ru_maxrss),
            rss_definition="max_single_process_native_rusage_not_simultaneous_tree_sum")
        (args.child_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return 0 if result["status"] == "sample_complete" else 2


def compatibility():
    """Read-only local metadata; no native import, model load or internet access."""
    try:
        version = importlib.metadata.version("moonshine-voice")
    except importlib.metadata.PackageNotFoundError:
        version = None
    return dict(python=platform.python_version(), machine=platform.machine(), libc=platform.libc_ver(),
                moonshine_installed=version, inference_run=False,
                next_step="separate_approval_for_isolated_dependencies_and_model_files")


def run(args, *, whisper_variant=None):
    from scripts import compare_offline_models as models
    from scripts.validate_offline_voice import demo_start_reason, require_resident, QWEN, monitor
    from scripts.validate_pi_performance import isolated_database
    single = getattr(args, "engine", None) == "moonshine"
    engines = ("whisper",) if whisper_variant else (("moonshine",) if single else ("whisper", "moonshine"))
    mode = "whisper_audio_variant" if whisper_variant else ("moonshine_only" if single else "two_engine_comparison")
    validate_audio = read_audio
    if whisper_variant:
        from scripts.whisper_audio_diagnostic import load_left
        validate_audio = load_left
    report = dict(run_mode=mode, audio_variant=whisper_variant,
                  requested_engines=list(engines), status="aborted", reference=args.reference, samples=[], telemetry=[], audio_sha256=None,
                  llm_requests=0, offline="local_models_python_network_blocked_native_code_not_os_sandboxed")
    report["whisper_controls"] = None if single else dict(threads=4, native_vad=False, timestamps=False)
    if single or whisper_variant:
        report["historical_whisper_reference"] = dict(
            source="operator_reported_non_contemporaneous_not_rerun", wer=.4545,
            final_text_seconds=3.55, peak_sampled_temperature_c=54.5,
            normalization_and_audio_fingerprint_verified=False,
            interpretation="Historical context only; not a completed same-run comparison.")
    report["moonshine_controls"] = dict(architecture="SMALL_STREAMING", version="0.1.5",
        manifest="english_quantized_26_08_21", feed_block_seconds=.1, realtime_pacing=False)
    if whisper_variant:
        report["moonshine_controls"] = None
    lock = None
    try:
        if sys.platform != "linux" or not args.operator_approved_public_audio:
            raise models.TrialRefused("linux_and_operator_approved_public_audio_required")
        if not whisper_variant and args.python.absolute() == Path(sys.executable).absolute():
            raise models.TrialRefused("separate_candidate_python_required")
        validate_audio(args.audio)
        word_errors(args.reference, "")  # Bound reference before any worker is spawned.
        report["audio_sha256"] = hashlib.sha256(args.audio.read_bytes()).hexdigest()
        lock = models.acquire_trial_lock()
        with isolated_database("stt-monitor-") as isolated:
            from scripts.benchmark_inference_ab import telemetry
            snapshot = isolated / "public-audio.wav"
            snapshot.write_bytes(args.audio.read_bytes())
            validate_audio(snapshot)
            report["audio_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            digest = models.verify_identity(QWEN, models.inventory(args.host)[QWEN], models.CONFIRMED_PREFIXES[QWEN])
            report["qwen_digest"] = digest
            for engine in engines:
                python = args.python if engine == "moonshine" else Path(sys.executable)
                point = telemetry()
                report["telemetry"].append(point)
                reason = demo_start_reason(point)
                if reason:
                    raise models.TrialRefused(reason)
                require_resident(args.host, digest, model=QWEN)
                sample = dict(engine=engine, telemetry=[], status="incomplete")
                report["samples"].append(sample)
                with tempfile.TemporaryDirectory(prefix="stt-worker-") as directory:
                    directory = Path(directory)
                    command = [str(python), str(Path(__file__).resolve()), "--child-dir", str(directory),
                               "--worker-engine", engine, "--audio", str(snapshot.resolve())]
                    if engine == "moonshine":
                        command += ["--moonshine-model", str(args.moonshine_model.resolve())]
                    if whisper_variant:
                        command += ["--audio-variant", whisper_variant]
                    if engine == "whisper":
                        command += ["--whisper-bin", str(args.whisper_bin.resolve()),
                                    "--whisper-model", str(args.whisper_model.resolve())]
                    command += ["--baseline-prefix", sys.prefix]
                    command += ["--reference", args.reference]
                    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                               cwd=ROOT, start_new_session=True)
                    try:
                        monitor(process, telemetry, lambda: require_resident(args.host, digest, model=QWEN), sample, timeout=120)
                    finally:
                        models.terminate(process)
                        path = directory / "result.json"
                        if path.is_file() and path.stat().st_size <= 32768:
                            sample["result"] = json.loads(path.read_text(encoding="utf-8"))
                    if process.returncode != 0 or sample.get("result", {}).get("status") != "sample_complete":
                        raise models.TrialRefused("stt_worker_failed_no_follow_on_trial")
                    require_resident(args.host, digest, model=QWEN)
                    final = telemetry()
                    sample["telemetry"].append(final)
                    reason = models.pressure_reason(final)
                    if reason:
                        raise models.TrialRefused(reason)
                    sample["status"] = "sample_complete_quality_pending"
            report["status"] = ("whisper_variant_complete_quality_pending" if whisper_variant else
                                "moonshine_complete_quality_pending" if single else "comparison_complete_quality_pending")
    except BaseException as exc:
        report["reason"] = exc.reason if isinstance(exc, models.TrialRefused) else "comparison_failed_or_cancelled"
    finally:
        if lock:
            lock.close()
        for sample in report["samples"]:
            points = sample["telemetry"]
            for field, key, aggregate in (("temperature_c", "peak_sampled_temperature_c", max),
                                          ("ram_available_bytes", "min_available_ram_bytes", min),
                                          ("cpu_percent", "peak_host_cpu_percent", max)):
                values = [p[field] for p in points if type(p.get(field)) in (int, float)]
                sample[key] = aggregate(values) if values else None
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2)
    return 0 if report["status"] in ("comparison_complete_quality_pending", "moonshine_complete_quality_pending", "whisper_variant_complete_quality_pending") else 2


def main(argv=None):
    from scripts.compare_offline_models import local_host
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--engine", choices=("moonshine",), help="Run Moonshine only; omission compares Whisper then Moonshine")
    parser.add_argument("--operator-approved-public-audio", action="store_true")
    parser.add_argument("--audio", type=Path, default=Path("/home/jagadish/mic-test-1m-whisper.wav"))
    parser.add_argument("--reference", default=REFERENCE, help="Approved public reference only; maximum 256 words")
    parser.add_argument("--python", type=Path)
    parser.add_argument("--whisper-bin", type=Path)
    parser.add_argument("--whisper-model", type=Path)
    parser.add_argument("--moonshine-model", type=Path)
    parser.add_argument("--host", type=local_host, default="http://127.0.0.1:11434")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--audio-variant", choices=("baseline", "normalize", "highpass"), help=argparse.SUPPRESS)
    parser.add_argument("--child-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-engine", choices=("whisper", "moonshine"), help=argparse.SUPPRESS)
    parser.add_argument("--baseline-prefix", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.audio_variant and not (args.child_dir and args.worker_engine == "whisper"):
        parser.error("Audio variant is internal to a guarded Whisper worker")
    if args.child_dir:
        if not args.worker_engine or args.engine or args.run:
            parser.error("Child execution requires --worker-engine and cannot use operator --engine/--run")
        return child(args)
    if args.worker_engine:
        parser.error("--worker-engine is internal and requires --child-dir")
    if not args.run:
        print(json.dumps(compatibility(), indent=2))
        return 0
    if not all((args.python, args.moonshine_model, args.output)):
        parser.error("Explicit separate Python, local Moonshine model and new output path are required")
    if args.engine is None and not all((args.whisper_bin, args.whisper_model)):
        parser.error("Default comparison requires explicit Whisper binary and model")
    if args.output.exists():
        parser.error("Report must be new")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
