"""Resolve installed assets and download them only on explicit setup."""

import importlib
from pathlib import Path
import tempfile
import urllib.request

from .base import WakeWordError


def load_engine():
    try:
        return importlib.import_module("openwakeword")
    except Exception as exc:
        raise WakeWordError(
            "openWakeWord is unavailable; install requirements.txt in the active environment. "
            "On Linux Python >=3.12 also run: python -m pip install --no-deps openwakeword==0.6.0 (see README)."
        ) from exc


def required_assets(config, engine):
    framework = config.inference_framework

    def asset(metadata):
        path, url = metadata["model_path"], metadata["download_url"]
        if framework == "onnx":
            path, url = path.replace(".tflite", ".onnx"), url.replace(".tflite", ".onnx")
        return Path(path), url

    assets = [asset(metadata) for metadata in engine.FEATURE_MODELS.values()]
    if config.vad_threshold > 0:
        assets.extend((Path(metadata["model_path"]), metadata["download_url"])
                      for metadata in engine.VAD_MODELS.values())
    if not config.model_path:
        if config.model not in engine.MODELS:
            raise WakeWordError(f"Built-in wake model is unavailable: {config.model}")
        assets.append(asset(engine.MODELS[config.model]))
    return assets


def validate_installed_assets(config, engine):
    if config.model_path and not Path(config.selected_model).is_file():
        raise WakeWordError(f"Custom wake model is not a file: {config.selected_model}")
    missing = [str(path) for path, _ in required_assets(config, engine)
               if not path.is_file() or not path.stat().st_size]
    if missing:
        raise WakeWordError("Wake model assets are missing; run python manage.py prepare_wake_word_model. Missing: " + ", ".join(missing))


def prepare_model(config):
    """Fetch selected/shared official assets atomically with bounded HTTP waits."""
    config.validate()
    engine = load_engine()
    if config.model_path and not Path(config.selected_model).is_file():
        raise WakeWordError(f"Custom wake model is not a file: {config.selected_model}")
    for path, url in required_assets(config, engine):
        if path.is_file() and path.stat().st_size:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with urllib.request.urlopen(url, timeout=30) as response, tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=path.name + ".", suffix=".part", delete=False,
            ) as output:
                temporary = Path(output.name)
                while True:
                    block = response.read(65536)
                    if not block:
                        break
                    output.write(block)
            if not temporary.stat().st_size:
                raise WakeWordError(f"Empty model download: {url}")
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    validate_installed_assets(config, engine)
