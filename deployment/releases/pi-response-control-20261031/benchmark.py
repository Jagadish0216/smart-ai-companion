"""Synthetic first-output comparison; never loads a model or sound device."""
import os
from pathlib import Path
import sys
import time
import statistics
import json
from contextlib import redirect_stdout
from dataclasses import replace
from threading import Event
import io


def main():
    root = Path(sys.argv[1]).resolve()
    os.chdir(root); sys.path.insert(0, str(root))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.test_settings'
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    import django
    django.setup()
    from assistant.tests_voice_stream import StreamingAudioTests, FIRST_SENTENCE, SECOND_SENTENCE
    from assistant import latency_trace as latency
    from assistant.ai_engine.local import LocalLLMEngine
    from assistant.policy import VOICE_SAFETY_INSTRUCTION, AssistantResponsePolicy
    first_output = []; first_ready = []; first_launch = []
    for _ in range(3):
        fixture = StreamingAudioTests(); fixture.setUp()
        played = Event(); began = time.perf_counter()
        def playback(*args, **kwargs):
            if not played.is_set():
                first_output.append((time.perf_counter()-began)*1000)
                played.set()
        fixture.play_audio.side_effect = playback
        trace = latency.Trace(synthetic=True)
        def events():
            yield {'type': 'start', 'conversation_id': 42}
            yield {'type': 'delta', 'text': FIRST_SENTENCE+' '}
            if not played.wait(2):
                raise RuntimeError('Synthetic pipeline failed to launch output before EOF.')
            yield {'type': 'delta', 'text': SECOND_SENTENCE}
            yield {'type': 'done', 'conversation_id': 42, 'response': FIRST_SENTENCE+' '+SECOND_SENTENCE}
        try:
            with latency.cycle(trace), redirect_stdout(io.StringIO()):
                fixture.speak(events(), replace(fixture.config, voice_refinements=True))
            first_ready.append(next(e['at_ms'] for e in trace.events if e['name']=='first_audio_ready'))
            first_launch.append(next(e['at_ms'] for e in trace.events if e['name']=='first_playback_launch'))
        finally:
            fixture.doCleanups()
    query = 'Explain recursion.'
    engine = LocalLLMEngine(model='gemma4:e2b-it-q4_K_M')
    instruction = VOICE_SAFETY_INSTRUCTION+'\n\n'+AssistantResponsePolicy.plan_voice_response(query).system_instruction
    began = time.perf_counter()
    for _ in range(100):
        messages = engine._build_messages(query, [], instruction, system_prompt='')
    prompt_ms = (time.perf_counter()-began)*10
    print(json.dumps({'synthetic': True, 'samples': 3,
        'first_output_call_ms': first_output, 'median_first_output_call_ms': statistics.median(first_output),
        'first_audio_ready_ms': first_ready, 'first_playback_launch_ms': first_launch,
        'normal_system_chars': len(messages[0]['content']), 'prompt_build_cpu_mean_ms': prompt_ms,
        'ollama_ttft_ms': None, 'real_piper_synthesis_ms': None, 'acoustic_onset_ms': None,
        'limits': 'Mock model/audio. Output-call time is neither device PCM acceptance nor audible speech. No Pi speedup is claimed.'}, indent=2))


if __name__ == '__main__':
    main()
