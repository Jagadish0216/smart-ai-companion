#!/usr/bin/env python3
"""Generate synthetic integration-test input with the configured TTS provider.

This does not exercise or validate real microphone capture or capture VAD.
"""

import argparse
import io
import os
from pathlib import Path
import sys
import wave


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from assistant.voice.factory import get_tts_provider  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", required=True, help="Text to synthesize as test speech.")
    parser.add_argument("--output", required=True, type=Path, help="Destination WAV file (replaces an existing file).")
    args = parser.parse_args(argv)
    text = args.text.strip()
    if not text:
        parser.error("--text must not be empty")
    output = args.output.expanduser()
    provider = None
    try:
        provider = get_tts_provider()
        audio = provider.synthesize(text)
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if wav.getnframes() == 0:
                raise ValueError("TTS returned a WAV with no audio frames.")
        output.write_bytes(audio)
    except Exception as exc:
        print(f"Test audio generation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if provider is not None:
            provider.close()
    print(f"Test audio: {output} (configured TTS: {settings.TTS_ENGINE})")
    print("Synthetic integration input only; not a microphone/VAD validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
