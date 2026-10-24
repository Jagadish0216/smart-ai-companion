"""Offline candidate evaluation and explicit, hash-bound Hey Companion approval.

Reports are operator evidence, not a cryptographic proof of the training phrase
or recording labels. No model, training job or acceptance is shipped here.
"""
from dataclasses import replace
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import time
import wave

from .base import WakeWordConfig, WakeWordError
from .models import load_engine, required_assets, validate_installed_assets

PHRASE = 'Hey Companion'
PROFILE = 'companion-serial-v1'


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            result.update(block)
    return result.hexdigest()


def candidate_config(model, threshold=.6):
    if not model or Path(model).name != 'hey_companion.onnx' or not Path(model).is_file():
        raise WakeWordError('Provide the genuinely trained local hey_companion.onnx; use GPIO17 push-to-talk meanwhile.')
    config = WakeWordConfig(enabled=True, model='hey_companion', model_path=str(Path(model).resolve()),
                            threshold=float(threshold), vad_threshold=.5, cooldown_seconds=1.5)
    config.validate()
    return config


def fingerprint(config, engine):
    validate_installed_assets(config, engine)
    model_hash = digest(config.selected_model)
    # A renamed installed built-in is not a custom-trained model.
    for metadata in engine.MODELS.values():
        for suffix in ('.onnx', '.tflite'):
            path = Path(metadata['model_path']).with_suffix(suffix)
            if path.is_file() and digest(path) == model_hash:
                raise WakeWordError('A built-in wake model was substituted; train the exact phrase instead.')
    return {'model_sha256': model_hash,
            'assets_sha256': sorted(digest(path) for path, _url in required_assets(config, engine)),
            'openwakeword_version': version('openwakeword'), 'onnxruntime_version': version('onnxruntime')}


def read_record(path):
    if not path or not Path(path).is_file() or Path(path).stat().st_size > 65536:
        raise WakeWordError('A bounded local model validation/training record is required; activation stays disabled.')
    try:
        record = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(record, dict):
            raise ValueError()
        return record
    except (ValueError, OSError) as exc:
        raise WakeWordError('Invalid local model validation/training record.') from exc


def passing_metrics(metrics):
    try:
        numbers = [metrics[key] for key in ('positives', 'recall', 'negative_hours', 'false_activations_per_hour', 'p95_frame_seconds')]
        if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in numbers):
            return False
        return (metrics['positives'] >= 40 and .9 <= metrics['recall'] <= 1 and
                metrics['negative_hours'] >= 5 and 0 <= metrics['false_activations_per_hour'] <= .2 and
                0 <= metrics['p95_frame_seconds'] <= .08)
    except (KeyError, TypeError):
        return False


def verify_approval(config):
    try:
        report = read_record(config.validation_report)
        if (report.get('profile') != PROFILE or report.get('phrase') != PHRASE or
                report.get('accepted_on_pi') is not True or not passing_metrics(report.get('metrics')) or
                report.get('threshold') != config.threshold or config.vad_threshold != .5 or
                config.cooldown_seconds < 1.5 or config.model != 'hey_companion' or
                Path(config.model_path).name != 'hey_companion.onnx'):
            raise WakeWordError('Hey Companion has not passed the required Pi validation profile.')
        if report.get('runtime') != fingerprint(config, load_engine()):
            raise WakeWordError('Model/runtime assets changed since validation; revalidate before activation.')
    except WakeWordError:
        raise
    except Exception as exc:
        raise WakeWordError('Cannot verify Hey Companion approval; use GPIO17 push-to-talk.') from exc


def approved_config(model, report_path):
    report = read_record(report_path)
    config = replace(candidate_config(model, report.get('threshold', .6)), validation_report=str(report_path))
    verify_approval(config)
    return config


def reset_model(model):
    model.reset()
    model.vad.reset_states()
    model.vad.prediction_buffer.clear()


def score(model, frame):
    import numpy as np
    scores = model.predict(np.frombuffer(frame, dtype='<i2'))
    if set(scores) != {'hey_companion'}:
        raise WakeWordError('Expected one compatible hey_companion output from the openWakeWord classifier.')
    value = float(scores['hey_companion'])
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise WakeWordError('Wake classifier returned an invalid probability.')
    return value


def evaluate(config, corpus, training_path, *, accept_pi=False):
    """Stream private held-out 16 kHz mono PCM16 WAVs, never download or record."""
    engine = load_engine()
    runtime = fingerprint(config, engine)
    training = read_record(training_path)
    if (training.get('phrase') != PHRASE or training.get('model_sha256') != runtime['model_sha256'] or
            not isinstance(training.get('training_source'), str) or not training['training_source'].strip() or
            training.get('held_out_real_recordings') is not True):
        raise WakeWordError('Training provenance must identify the exact phrase/hash and independent real holdout recordings.')
    model = engine.Model(wakeword_models=[config.selected_model], inference_framework='onnx',
                         vad_threshold=config.vad_threshold, ncpu=1)
    # Real model loading plus prediction checks ONNX/runtime compatibility, not a filename.
    score(model, bytes(2560))
    hits = positives = false_hits = negative_frames = 0
    timings = []
    corpus_hashes = set()
    for category in ('positive', 'negative'):
        files = sorted((Path(corpus) / category).glob('*.wav'))
        if not files:
            raise WakeWordError('Corpus needs positive/ and negative/ WAVs; private recordings must stay outside Git.')
        for path in files:
            audio_hash = digest(path)
            if audio_hash in corpus_hashes:
                raise WakeWordError('Duplicate holdout audio is not independent validation data.')
            corpus_hashes.add(audio_hash)
            reset_model(model)
            consecutive = cooldown = detections = frames = 0
            with wave.open(str(path), 'rb') as audio:
                if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getcomptype()) != (16000, 1, 2, 'NONE'):
                    raise WakeWordError('Holdout WAVs must be mono 16 kHz PCM16, normalized from the Pi LEFT channel.')
                while True:
                    frame = audio.readframes(1280)
                    if not frame:
                        break
                    frames += len(frame) // 2
                    if cooldown:
                        cooldown -= 1
                        continue
                    start = time.monotonic()
                    value = score(model, frame.ljust(2560, b'\0'))
                    timings.append(time.monotonic() - start)
                    consecutive = consecutive + 1 if value >= config.threshold else 0
                    if consecutive >= 2:
                        detections += 1
                        consecutive = 0
                        cooldown = math.ceil(config.cooldown_seconds / .08)
                        reset_model(model)
            if not frames:
                raise WakeWordError('Empty holdout audio is not validation data.')
            if category == 'positive':
                positives += 1
                hits += bool(detections)
            else:
                false_hits += detections
                negative_frames += frames
    hours = negative_frames / 16000 / 3600
    metrics = {'positives': positives, 'recall': hits / positives, 'negative_hours': hours,
               'false_activations_per_hour': false_hits / hours,
               'p95_frame_seconds': sorted(timings)[math.ceil(len(timings) * .95) - 1]}
    return {'profile': PROFILE, 'phrase': PHRASE, 'runtime': runtime, 'threshold': config.threshold,
            'training_record_sha256': digest(training_path),
            'corpus_sha256': hashlib.sha256(''.join(sorted(corpus_hashes)).encode()).hexdigest(),
            'metrics': metrics, 'accepted_on_pi': bool(accept_pi and passing_metrics(metrics))}
