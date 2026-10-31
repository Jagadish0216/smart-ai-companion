"""Summarize content-free production traces without inferring acoustic onset."""
import argparse
import json
from pathlib import Path


def summarize(data):
    events = data.get('events', [])
    def first(name):
        return next((e['at_ms'] for e in events if e.get('name') == name), None)
    def duration(start, end):
        a, b = first(start), first(end)
        return round(b-a, 3) if a is not None and b is not None and b >= a else None
    return {'cycle_id': data.get('cycle_id'), 'synthetic': data.get('synthetic'),
        'routing_ms': duration('routing_start', 'routing_end'),
        'prompt_prepare_ms': duration('prompt_prepare_start', 'prompt_prepare_end'),
        'ollama_ttft_ms': duration('ollama_request_ready', 'llm_first_alphanumeric'),
        'first_piper_synthesis_ms': duration('tts_start', 'tts_end'),
        'first_speech_unit_ms': first('first_speech_unit'),
        'first_audio_ready_ms': first('first_audio_ready'),
        'first_playback_launch_ms': first('first_playback_launch'),
        'first_speech_pcm_accepted_ms': first('first_speech_pcm_accepted'),
        'acoustic_onset_ms': None,
        'limits': 'PCM pipe acceptance and launch are not audible onset. Missing events remain unknown.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps([summarize(json.loads(p.read_text()))
                      for p in sorted(args.directory.glob('cycle-*.json'))], indent=2))
