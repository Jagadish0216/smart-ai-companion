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
| `assistant` | Chat API, AI engine abstraction, inference orchestration |
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

### 2. Raspberry Pi + Ollama (For Real LLM)

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

### 3. Switch to Local LLM

Edit your `.env` file:

```env
AI_ENGINE=local
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.2:1b
```

Then restart Django:

```bash
python manage.py runserver
```

The Assistant will now use the real local LLM for inference.

### 4. Switch Back to Mock

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

Current test suite: **46 tests** including mocked Ollama integration tests.

All tests run without a real Ollama server (HTTP calls are mocked).

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
| Voice Input (STT) | ⬜ Not implemented |
| Voice Output (TTS) | ⬜ Not implemented |
| Online Retrieval | ⬜ Not implemented |
| Real Hardware Sensors | ⬜ Not implemented |
| Raspberry Pi Deployment | ⬜ Not deployed yet |

### Important Notes

- The local LLM runs on **CPU only** (Raspberry Pi 5 has no GPU). Inference speed is limited by ARM CPU performance.
- RAG (Retrieval-Augmented Generation) is **not implemented**. The LLM answers from its training data only.
- STT (Speech-to-Text) and TTS (Text-to-Speech) are **not implemented**.
- Online/web retrieval is **not implemented**. All inference is offline.
- The system prompt establishes the companion persona but the model's behavior depends on its training.

## Future Integration Plan

1. **Mock AI** ✅
2. **Local LLM** ← current (Ollama + Llama 3.2 1B)
3. **Local LLM + RAG** ← add vector store + document processing
4. **Local LLM + RAG + Online Retrieval** ← add query router
5. **Voice Pipeline** ← add STT/TTS engines
