# Smart AI Companion

**Offline-Based Smart AI Companion for Intelligent Edge Automation**

A Raspberry Pi 5-based offline-first AI companion. The Django web application serves as the **Smart Companion Control Center** for monitoring, controlling, and interacting with the physical companion device.

## Architecture

- **Backend**: Python 3.10+, Django 5.2, Django REST Framework
- **Database**: SQLite (edge-device friendly)
- **Frontend**: HTML5, Vanilla CSS, Vanilla JavaScript
- **API**: RESTful JSON endpoints
- **AI Engine**: Pluggable backend — mock (dev) or local LLM (Ollama + Llama 3.2)

### Django Apps

| App | Purpose |
|---|---|
| `dashboard` | Control center UI templates and views |
| `assistant` | Chat API, Voice API, AI engine abstraction, STT/TTS providers |
| `conversations` | Chat history persistence (Conversation + Message models) |
| `knowledge_base` | Document upload and RAG metadata (foundation only) |
| `system` | Device metrics, companion state, settings, logging |

### AI Engine Architecture

The assistant uses a pluggable engine system. Django never calls a specific LLM library directly.

```
ChatAPIView
    ↓
AssistantService.process_message()
    ↓
get_engine()  ← reads AI_ENGINE from settings
    ↓
┌──────────────────────────────────────────┐
│ MockAIEngine     → keyword responses     │  (default, no deps)
│ LocalLLMEngine   → Ollama /api/chat      │  (requires Ollama)
│ (future engines)                         │
└──────────────────────────────────────────┘
    ↓
AIEngineResult (dataclass)
    ↓
AssistantService persists response + metadata
    ↓
API: {conversation_id, response, engine, model, mode}
```

**Key files:**

- [`assistant/ai_engine/base.py`](assistant/ai_engine/base.py) — `AIEngine` ABC + `AIEngineResult` dataclass
- [`assistant/ai_engine/mock.py`](assistant/ai_engine/mock.py) — `MockAIEngine` (development/testing)
- [`assistant/ai_engine/local.py`](assistant/ai_engine/local.py) — `LocalLLMEngine` (Ollama + Llama 3.2)
- [`assistant/ai_engine/__init__.py`](assistant/ai_engine/__init__.py) — `get_engine()` factory
- [`assistant/services.py`](assistant/services.py) — `AssistantService` orchestration

## Setup Instructions

### 1. Django Application (Development Machine or Pi)

```bash
git clone <repo-url>
cd smart-ai-companion
python -m venv venv

# Windows:
.\venv\Scripts\activate
# Linux/Mac/Pi:
source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver
```

Navigate to `http://127.0.0.1:8000`. By default, `AI_ENGINE=mock` — no external services needed.

### 2. Production Deployment (Gunicorn & WhiteNoise)

For deployment on the Raspberry Pi, use Gunicorn instead of the development server. WhiteNoise handles static files automatically.

```bash
pip install gunicorn
python manage.py collectstatic
gunicorn config.wsgi:application --bind 0.0.0.0:8000
```

### 3. Raspberry Pi + Ollama (For Real LLM)

#### Install Ollama on the Pi

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama --version
```

#### Pull the Model

```bash
ollama pull llama3.2:1b
ollama list
```

#### Test Directly

```bash
ollama run llama3.2:1b "Hello, what can you do?"
```

#### Or use the setup script

```bash
chmod +x scripts/pi_setup.sh
./scripts/pi_setup.sh
```

This collects hardware info, installs Ollama, pulls the model, runs CLI tests, and benchmarks.

### 4. Switch to Local LLM

Edit your `.env` file:

```env
AI_ENGINE=local
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.2:1b
OLLAMA_KEEP_ALIVE=30m
OLLAMA_NUM_PREDICT=128
CHAT_REQUEST_TIMEOUT_SECONDS=30
```

Then restart Django:

```bash
python manage.py runserver
```

The Assistant will now use the real local LLM for inference.

### 5. Switch Back to Mock

```env
AI_ENGINE=mock
```

Mock mode requires no external services and is useful for frontend development.

## AI Engine Configuration

| Env Variable | Default | Description |
|---|---|---|
| `AI_ENGINE` | `mock` | Engine selection: `mock` or `local` |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `llama3.2:1b` | Model identifier |
| `OLLAMA_TIMEOUT` | `120` | Request timeout (seconds) |
| `OLLAMA_KEEP_ALIVE` | `30m` | How long Ollama keeps the model loaded after a request |
| `OLLAMA_NUM_PREDICT` | `128` | Maximum number of tokens Ollama generates per response |
| `CHAT_REQUEST_TIMEOUT_SECONDS` | `30` | Browser timeout for chat POST requests |
| `STT_ENGINE` | `mock` | Speech-to-Text: `mock` or `whisper_cpp` |
| `STT_WHISPER_BIN` | `/opt/whisper.cpp/main` | Path to whisper.cpp binary |
| `STT_WHISPER_MODEL` | `/opt/whisper.cpp/models/ggml-base.en.bin` | Path to whisper.cpp model |
| `STT_VAD_ENABLED` | `false` | Enable whisper.cpp Silero VAD preprocessing |
| `STT_VAD_MODEL` | _(empty)_ | Path to the Silero VAD model; required when VAD is enabled |
| `STT_VAD_THRESHOLD` | `0.5` | Speech-detection probability threshold |
| `STT_VAD_MIN_SPEECH_MS` | `250` | Minimum accepted speech-segment duration |
| `STT_VAD_MIN_SILENCE_MS` | `700` | Silence duration used to split speech segments |
| `STT_VAD_SPEECH_PAD_MS` | `100` | Padding added around detected speech segments |
| `TTS_ENGINE` | `mock` | Text-to-Speech: `mock` or `piper` |
| `TTS_PIPER_BIN` | `/opt/piper/piper` | Path to Piper binary |
| `TTS_PIPER_VOICE` | `/opt/piper/en_US-lessac-medium.onnx` | Path to Piper ONNX model |
| `VOICE_INPUT_SOURCE` | _(required)_ | PipeWire/PulseAudio microphone source used by the standalone voice loop |
| `VOICE_RECORD_SECONDS` | `5` | Speech window after microphone warm-up for each standalone voice cycle |
| `VOICE_INPUT_WARMUP_SECONDS` | `1.0` | Delay after opening the Bluetooth microphone before prompting the user to speak |
| `VOICE_LEADING_SILENCE_SECONDS` | `0.7` | Silence prepended before Bluetooth playback |
| `VOICE_MAX_SPEECH_CHARS` | `500` | Maximum sanitized AI-response characters spoken by the standalone loop |

## Standalone Raspberry Pi Voice Loop

The standalone loop reuses the configured Whisper, AI engine, and Piper providers. It records from a named PipeWire/PulseAudio source and plays through the current default sink; it does not change the browser voice API.

Optional Silero VAD runs inside whisper.cpp after the fixed-duration recording. To enable it, set `STT_VAD_ENABLED=true` and copy the exact Silero model path on the Pi into `STT_VAD_MODEL`; leave it disabled to preserve the original Whisper command and behavior.

```env
STT_VAD_ENABLED=false
STT_VAD_MODEL=
STT_VAD_THRESHOLD=0.5
STT_VAD_MIN_SPEECH_MS=250
STT_VAD_MIN_SILENCE_MS=700
STT_VAD_SPEECH_PAD_MS=100
```

Find the Realme Buds microphone source and add it to `.env`:

```bash
pactl list short sources
```

```env
VOICE_INPUT_SOURCE=
VOICE_RECORD_SECONDS=5
VOICE_INPUT_WARMUP_SECONDS=1.0
VOICE_LEADING_SILENCE_SECONDS=0.7
VOICE_MAX_SPEECH_CHARS=500
```

Copy the exact source returned by `pactl list short sources` into `VOICE_INPUT_SOURCE`, then run:

```bash
python scripts/voice_loop.py
```

Press Enter to record one cycle, wait for `Speak now...`, or type `q` and press Enter to exit. The microphone remains open for the warm-up plus the full speech window, and the reported `record` timing includes both. The terminal and conversation retain the original AI response; only the TTS copy has Markdown removed and is limited by `VOICE_MAX_SPEECH_CHARS`. A per-stage timing summary is printed after each successful cycle. The loop uses `parecord`, `ffmpeg`, and `paplay`; wake-word detection is not implemented, and recording remains fixed-duration even when STT VAD is enabled.

## Error Handling

- **Mock mode** (`AI_ENGINE=mock`): Always works, no external dependencies.
- **Local mode** (`AI_ENGINE=local`):
  - If Ollama is not running → returns "AI engine unavailable" error
  - If model is not pulled → returns "model not found, run `ollama pull`" error
  - If inference fails → returns clean error, never crashes Django
  - **Never silently falls back to mock** — errors are reported honestly

## Testing

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py test
```

The suite includes mocked Ollama, voice-provider, and standalone voice-loop coverage. Tests run without real Ollama, Whisper, Piper, or PulseAudio services.

## Raspberry Pi Benchmark Results

> **Placeholder**: Run `scripts/pi_setup.sh` on the Pi to collect real measurements.
> Results will be recorded here after the Pi is connected and tested.

| Metric | Value |
|---|---|
| Pi Model | _pending_ |
| RAM | _pending_ |
| OS | _pending_ |
| Ollama Version | _pending_ |
| Model | `llama3.2:1b` |
| Model Size | _pending_ |
| Avg Response Time | _pending_ |
| Tokens/sec | _pending_ |
| RAM Usage (inference) | _pending_ |
| Temperature (idle) | _pending_ |
| Temperature (inference) | _pending_ |

## Current Implementation Status

| Component | Status |
|---|---|
| Control Center UI | ✅ Implemented |
| Mock AI Engine | ✅ Implemented |
| Local LLM Engine (Ollama) | ✅ Implemented (uses /api/chat with system prompt) |
| Llama 3.2 1B Integration | ✅ Architecture ready, pending Pi connection |
| Conversation Persistence | ✅ Implemented |
| Document Upload | ✅ Implemented |
| Device Metrics (simulated) | ✅ Implemented |
| Companion State Control | ✅ Implemented |
| RAG Pipeline | ⬜ Not implemented |
| Voice Input (STT - whisper.cpp) | ✅ Implemented (API & Browser Mic) |
| Voice Output (TTS - Piper) | ✅ Implemented (API & Browser Playback) |
| Online Retrieval | ⬜ Not implemented |
| Real Hardware Sensors/Mics | ⬜ Not implemented |
| Raspberry Pi Deployment | ✅ Deployed (Django + local LLM) |

### Important Notes

- The local LLM runs on **CPU only** (Raspberry Pi 5 has no GPU). Inference speed is limited by ARM CPU performance.
- RAG (Retrieval-Augmented Generation) is **not implemented**. The LLM answers from its training data only.
- Online/web retrieval is **not implemented**. All inference is offline.
- The system prompt establishes the companion persona but the model's behavior depends on its training.

## Future Integration Plan

1. **Mock AI** ✅
2. **Local LLM** ✅
3. **Voice Pipeline** ✅ (Browser UI -> STT -> LLM -> TTS -> Browser Audio)
4. **Physical Hardware I/O** ← add Pi-connected microphone and speaker
5. **Local LLM + RAG** ← add vector store + document processing
6. **Local LLM + RAG + Online Retrieval** ← add query router
