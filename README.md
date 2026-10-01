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
| `knowledge_base` | Local text/Markdown ingestion, chunk storage, and lexical retrieval |
| `system` | Device metrics, companion state, settings, logging |

### AI Engine Architecture

The assistant uses a pluggable engine system. Django never calls a specific LLM library directly.

```
ChatAPIView
    ↓
Optional AssistantResponsePolicy
    ↓  response mode + capability check + request budget
AssistantService.process_message()
    ↓
Deterministic route: LOCAL / LOCAL_RAG / ONLINE / ACTION / CLARIFICATION
    ↓
Optional local knowledge or online search-result retrieval
    ↓  request-scoped grounded context only on the selected route
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
- [`assistant/policy.py`](assistant/policy.py) — response modes, deterministic intent policy, and capability registry
- [`assistant/online/`](assistant/online/) — replaceable online retriever interface, SearXNG provider, and bounded grounding
- [`assistant/services.py`](assistant/services.py) — `AssistantService` orchestration

### Assistant Response Policy

The reusable policy layer classifies requests as `BRIEF`, `NORMAL`, `DETAILED`, `ACTION`, or `CLARIFICATION`. The standalone voice loop uses this classification to select response depth and a request-scoped Ollama generation budget without changing the global `OLLAMA_NUM_PREDICT=128` fallback. Browser and service calls retain the same public behavior while all LLM requests receive request-scoped capability grounding.

The capability registry reflects configured, implemented components such as conversation persistence, local generation, whisper.cpp STT, Piper TTS, local retrieval, and optional online retrieval. `LOCAL_RAG` is available only when RAG is enabled, its configuration is valid, and at least one chunk belongs to an indexed document. `ONLINE_RETRIEVAL` is available only when online retrieval is enabled, the local Ollama engine is selected, the provider is supported, credentials are present, and limits are valid. Sensors, camera understanding, ESP32/device control, movement, and reminders remain unavailable. Recognized requests for unavailable actions receive a natural grounded response; the assistant must not claim that an action or observation occurred.

### Local Knowledge / RAG

The v1 local knowledge pipeline is offline and dependency-free. It reuses the existing `Document` model, stores deterministic child chunks in SQLite, and retrieves them with normalized lexical term coverage. Database-side term filtering prevents every chunk body from being loaded into Python, and the retriever keeps only the configured top results in memory. This is appropriate for a modest Pi-hosted project knowledge base and does not load an embedding model per request.

Supported source formats are UTF-8 plain text (`.txt`) and Markdown (`.md` or `.markdown`). PDF, OCR, image extraction, semantic embeddings, and external vector databases are intentionally outside this milestone.

Run migrations, ingest a source, and enable retrieval:

```bash
python manage.py migrate
python manage.py ingest_knowledge path/to/project-notes.md
```

```env
RAG_ENABLED=true
AI_ENGINE=local
```

The command uses the resolved source path as its stable identifier by default. Re-ingesting the same source replaces its stored file and chunks instead of creating a second document. Use `--source-id stable-name` when the source may move:

```bash
python manage.py ingest_knowledge notes/relay-wiring.txt --source-id relay-wiring
```

At request time, the retriever returns up to `RAG_TOP_K` chunks whose score meets `RAG_MIN_RELEVANCE`. Useful chunks are added only to the internal request instruction; the user query and conversation history are unchanged. The LLM is told to prefer this trusted local material, avoid unsupported details, and not mention filenames or retrieval internals unless asked. If nothing meets the threshold, generation follows the existing non-RAG path.

`LOCAL_RAG` is reported unavailable when the local LLM is not selected, RAG is disabled, configuration is invalid, or no indexed chunks exist. This conservative empty-index behavior prevents the assistant from claiming it can search local knowledge before ingestion has succeeded.

The lexical baseline works best when the question and source share important terms. A future semantic retriever can implement the existing retriever interface without changing ingestion, `AssistantService`, the voice loop, or public APIs.

### Optional Online Retrieval

Online retrieval is disabled by default, so ordinary `LOCAL` and `LOCAL_RAG` requests remain offline. When the deterministic router selects `ONLINE` and the feature is usable, `AssistantService` asks the configured retriever for a small number of search-result snippets, adds a bounded request-only grounding instruction, and sends the original query to the existing local Ollama model. It does not run local RAG for an online route, download full webpages, use a cloud LLM, or persist retrieved material in conversation history.

The v1 provider is the open-source, self-hostable SearXNG Search API. It supplies structured titles, URLs, and snippets through one lightweight HTTP request and needs no browser runtime, API key, commercial search account, or additional Python dependency. Run SearXNG locally or on the LAN, enable JSON in its `search.formats` configuration, and configure the assistant with its base URL:

```env
AI_ENGINE=local
ONLINE_RETRIEVAL_ENABLED=true
ONLINE_PROVIDER=searxng
ONLINE_TIMEOUT_SECONDS=8
ONLINE_MAX_RESULTS=4
ONLINE_MAX_CONTEXT_CHARS=6000
SEARXNG_BASE_URL=http://127.0.0.1:8888
```

The capability remains unavailable when the feature is disabled, `AI_ENGINE` is not `local`, the provider is unsupported, the SearXNG base URL is invalid, or the configured limits are invalid. It does not perform a network health probe while building the capability registry. In the unavailable state, current-information requests keep the existing direct limitation response and do not invoke the LLM. Timeouts, connection failures, HTTP errors, malformed responses, and zero useful results return “I couldn't retrieve current information right now.” without asking the local model to guess.

Public SearXNG instances can be useful during development, but they are not recommended as a production dependency: JSON output is often disabled, instances may rate-limit requests, and instance behavior or availability can change without notice. A controlled local or LAN instance is the recommended Raspberry Pi deployment. Both HTTP and HTTPS base URLs are supported so a local instance such as `http://127.0.0.1:8888` works without TLS; credentials, query strings, fragments, unsupported schemes, malformed hosts, and invalid ports are rejected.

Retrieved snippets are treated as untrusted reference data. The request-scoped instruction tells the model to ignore embedded instructions, never execute retrieved commands, protect prompts and secrets, avoid unsupported current claims, and express uncertainty when sources conflict. Only allowlisted attribution fields are exposed through API metadata; voice output is told not to read URLs or metadata aloud. Search-result snippets are a fast, low-bandwidth v1 grounding source, but they can be incomplete and do not replace full-page research or source verification.

Online latency and local generation latency are separate: `online_latency_ms` measures the provider request, while `latency_ms` remains the local generation measurement. Total perceived latency is approximately their sum plus application overhead. On a Pi, network response time varies independently from Ollama CPU inference time; the provider uses one explicit timeout and no indefinite retries.

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
| `RAG_ENABLED` | `false` | Enable local retrieval with `AI_ENGINE=local` after knowledge has been ingested |
| `RAG_RETRIEVER` | `lexical` | Local retriever implementation; v1 supports `lexical` |
| `RAG_CHUNK_CHARS` | `1000` | Approximate source chunk size in characters |
| `RAG_CHUNK_OVERLAP_CHARS` | `150` | Approximate overlap between adjacent chunks |
| `RAG_TOP_K` | `4` | Maximum relevant chunks supplied to one request |
| `RAG_MIN_RELEVANCE` | `0.5` | Minimum lexical query-term coverage score from 0 to 1 |
| `ONLINE_RETRIEVAL_ENABLED` | `false` | Enable online snippets only for requests routed to `ONLINE` |
| `ONLINE_PROVIDER` | `searxng` | Online retriever implementation; v1 supports `searxng` |
| `ONLINE_TIMEOUT_SECONDS` | `8` | Timeout for the single provider HTTP request |
| `ONLINE_MAX_RESULTS` | `4` | Maximum useful search-result snippets retained per request (1–20) |
| `ONLINE_MAX_CONTEXT_CHARS` | `6000` | Maximum retrieved-context characters sent to the local model |
| `SEARXNG_BASE_URL` | `http://127.0.0.1:8888` | Valid HTTP(S) base URL for the self-hosted SearXNG instance |
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
| `VOICE_CAPTURE_VAD_ENABLED` | `false` | Enable speech-driven capture using a lightweight live RMS gate |
| `VOICE_CAPTURE_VAD_START_THRESHOLD` | `0.02` | Normalized PCM RMS level (0–1) that starts speech capture |
| `VOICE_CAPTURE_VAD_SILENCE_SECONDS` | `0.8` | Trailing silence that ends speech-driven capture |
| `VOICE_CAPTURE_VAD_MAX_SECONDS` | `10` | Maximum speech capture duration after speech starts |
| `VOICE_CAPTURE_VAD_START_TIMEOUT_SECONDS` | `5` | Maximum time to wait for speech after `Speak now...` |
| `VOICE_LEADING_SILENCE_SECONDS` | `0.7` | Silence prepended before Bluetooth playback |
| `VOICE_TTS_CHUNK_CHARS` | `300` | Approximate sentence-aware Piper chunk size |
| `VOICE_TTS_MAX_TOTAL_CHARS` | `0` | Optional spoken-response safety limit; `0` speaks the full sanitized response |
| `VOICE_LLM_BRIEF_NUM_PREDICT` | `96` | Request-scoped Ollama budget for brief voice responses |
| `VOICE_LLM_NORMAL_NUM_PREDICT` | `160` | Request-scoped Ollama budget for normal voice responses |
| `VOICE_LLM_DETAILED_NUM_PREDICT` | `384` | Request-scoped Ollama budget for detailed voice responses |

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
VOICE_CAPTURE_VAD_ENABLED=false
VOICE_CAPTURE_VAD_START_THRESHOLD=0.02
VOICE_CAPTURE_VAD_SILENCE_SECONDS=0.8
VOICE_CAPTURE_VAD_MAX_SECONDS=10
VOICE_CAPTURE_VAD_START_TIMEOUT_SECONDS=5
VOICE_LEADING_SILENCE_SECONDS=0.7
VOICE_TTS_CHUNK_CHARS=300
VOICE_TTS_MAX_TOTAL_CHARS=0
VOICE_LLM_BRIEF_NUM_PREDICT=96
VOICE_LLM_NORMAL_NUM_PREDICT=160
VOICE_LLM_DETAILED_NUM_PREDICT=384
```

Copy the exact source returned by `pactl list short sources` into `VOICE_INPUT_SOURCE`, then run:

```bash
python scripts/voice_loop.py
```

Press Enter to record one cycle, wait for `Speak now...`, or type `q` and press Enter to exit. By default, the microphone remains open for the warm-up plus the full `VOICE_RECORD_SECONDS` window, preserving the original fixed-duration behavior. Set `VOICE_CAPTURE_VAD_ENABLED=true` to wait for speech and stop after trailing silence instead. This live mode uses a lightweight PCM RMS gate and keeps 300 ms of pre-roll to avoid clipping the first word; whisper.cpp Silero VAD remains a separate downstream validation option. The `record` timing includes the complete microphone-open operation, and live mode also prints the captured audio duration.

The voice loop applies the assistant response policy before generation. Brief requests stay direct, normal requests remain concise but sufficient, and explicit requests for detail, steps, examples, or comparisons can use the larger detailed budget and produce complete answers. Ambiguous commands ask for clarification. Recognized actions that require an unavailable sensor, device, camera, retrieval source, or scheduler are answered honestly instead of being presented as completed.

The terminal and conversation retain the original AI response. The TTS copy has Markdown removed and is split at sentence boundaries into approximately `VOICE_TTS_CHUNK_CHARS` characters. All chunks are synthesized and played sequentially, so detailed answers are spoken in full by default. Set `VOICE_TTS_MAX_TOTAL_CHARS` to a nonzero value only when an explicit safety limit is needed; truncation then occurs at a complete sentence boundary. Bluetooth leading silence is applied only to the first chunk. The timing summary totals synthesis and playback across every chunk, and the loop also reports the number of TTS chunks. The loop uses `parecord`, `ffmpeg`, and `paplay`; wake-word detection is not implemented.

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

The suite includes local knowledge chunking/ingestion/retrieval tests plus mocked Ollama, voice-provider, and standalone voice-loop coverage. Tests run without real Ollama, Whisper, Piper, or PulseAudio services. Run only the RAG coverage with:

```bash
python manage.py test knowledge_base.tests knowledge_base.tests_rag
```

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
| Local RAG Pipeline | ✅ Implemented (text/Markdown + lexical retrieval) |
| Voice Input (STT - whisper.cpp) | ✅ Implemented (API & Browser Mic) |
| Voice Output (TTS - Piper) | ✅ Implemented (API & Browser Playback) |
| Online Retrieval | ✅ Optional SearXNG snippets + local Ollama generation |
| Real Hardware Sensors/Mics | ⬜ Not implemented |
| Raspberry Pi Deployment | ✅ Deployed (Django + local LLM) |

### Important Notes

- The local LLM runs on **CPU only** (Raspberry Pi 5 has no GPU). Inference speed is limited by ARM CPU performance.
- Local RAG uses lexical matching rather than semantic embeddings, so sources and questions should share meaningful terminology.
- Only UTF-8 text and Markdown are indexed in v1; PDF/OCR support is not included.
- Online retrieval is opt-in and used only for `ONLINE` routes; all answer generation remains local through Ollama.
- The system prompt establishes the companion persona but the model's behavior depends on its training.

## Future Integration Plan

1. **Mock AI** ✅
2. **Local LLM** ✅
3. **Voice Pipeline** ✅ (Browser UI -> STT -> LLM -> TTS -> Browser Audio)
4. **Physical Hardware I/O** ← add Pi-connected microphone and speaker
5. **Local LLM + lexical RAG** ✅
6. **Local LLM + RAG + Online Retrieval** ✅ (deterministic router + optional SearXNG snippets)
