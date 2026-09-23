import os
import time
import django
import subprocess

# Set up Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
# We force local providers to test the actual binaries
os.environ['STT_ENGINE'] = 'whisper_cpp'
os.environ['TTS_ENGINE'] = 'piper'
os.environ['AI_ENGINE'] = 'local'
django.setup()

from assistant.voice.factory import get_stt_provider, get_tts_provider
from assistant.services import AssistantService

def run_smoke_test():
    print("=== Smart AI Companion Voice Smoke Test ===")

    # 1. Create a dummy 16kHz WAV file using ffmpeg (1 second of silence)
    print("Generating dummy audio file for testing...")
    dummy_wav = "dummy_test.wav"
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=16000:cl=mono', '-t', '1', '-c:a', 'pcm_s16le', dummy_wav
    ], capture_output=True, check=True)

    stt = get_stt_provider()
    tts = get_tts_provider()

    print("\n--- STT Baseline ---")
    stt_start = time.time()
    try:
        text = stt.transcribe(dummy_wav)
        stt_latency = time.time() - stt_start
        print(f"Transcription: {text}")
        print(f"Latency: {stt_latency:.2f}s")
    except Exception as e:
        print(f"STT Failed: {e}")
        text = "Hello" # Fallback for next steps

    print("\n--- LLM Baseline ---")
    llm_start = time.time()
    try:
        conv, response, meta, error = AssistantService.process_message(text)
        llm_latency = time.time() - llm_start
        if error:
            print(f"LLM Error: {error}")
            response = "Error processing message."
        else:
            print(f"Response: {response}")
            print(f"Latency: {llm_latency:.2f}s (Engine reported: {meta.get('latency_ms', 0)}ms)")
    except Exception as e:
        print(f"LLM Failed: {e}")
        response = "Failed"

    print("\n--- TTS Baseline ---")
    tts_start = time.time()
    try:
        audio = tts.synthesize(response)
        tts_latency = time.time() - tts_start
        print(f"Audio generated: {len(audio)} bytes")
        print(f"Latency: {tts_latency:.2f}s")
    except Exception as e:
        print(f"TTS Failed: {e}")

    print("\n=== Cleanup ===")
    if os.path.exists(dummy_wav):
        os.remove(dummy_wav)
    print("Done.")

if __name__ == "__main__":
    run_smoke_test()
