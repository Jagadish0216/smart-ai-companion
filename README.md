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
| `knowledge_base` | Local text/Markdown/PDF ingestion, document management, chunk storage, and lexical retrieval |
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
- [`assistant/devices/`](assistant/devices/) — transport-independent device commands plus the MQTT controller
- [`assistant/services.py`](assistant/services.py) — `AssistantService` orchestration

### Assistant Response Policy

The reusable policy layer classifies requests as `BRIEF`, `NORMAL`, `DETAILED`, `ACTION`, or `CLARIFICATION`. The standalone voice loop uses this classification to select response depth and a request-scoped Ollama generation budget without changing the global `OLLAMA_NUM_PREDICT=128` fallback. Browser and service calls without a voice plan retain the same behavior while all LLM requests receive request-scoped capability grounding.

Resource scheduling uses sustained five-minute load divided by logical CPU count, memory, storage, temperature and active throttle flags. Instantaneous utilization remains visible: a busy 100% sample with load 1.1 on four cores (normalized 0.275) is not CPU pressure. Configurable normalized-load thresholds remain 1.0 for CAUTION and 1.5 for CONSTRAINED; either selects ECO. Missing sustained-load telemetry is UNKNOWN, not inferred pressure from one sample. Utilization thresholds remain diagnostic busy indicators; PERFORMANCE still requires low instantaneous utilization and ample headroom.

Temperature at 65 C remains diagnostic CAUTION with `TEMPERATURE_WARM`, but below 75 C does not alone select ECO. At 75 C the profile is ECO; at 80 C it is PROTECTIVE. Active throttle flags still select PROTECTIVE and block generation. Latched historical flags remain diagnostic `THROTTLING_HISTORY`. Genuine memory/storage pressure still reduces the profile. Thus CPU 100%, load 1.1/4, RAM 63%, temperature 73.8 C and throttle 0x0 remain BALANCED/NORMAL without a reduced-resource notice, while subsystem health truthfully reports warmth. BALANCED/PERFORMANCE/ECO use only `OLLAMA_MODEL` (default `qwen3.5:4b-q4_K_M`); profiles reduce output/history budgets, never select another model.

Voice plans default to no implicit RAG for generic world-knowledge questions, even if local documents match. Explicit local-document/project intent enables retrieval. Known request wrappers are removed deterministically: "What does our knowledge base say about edge computing?" retrieves "edge computing"; "According to my notes, how is ESP32 relay wiring connected?" retrieves "ESP32 relay wiring connected". The complete original question still goes to the model and database. Lexical scoring is unchanged; implicit text-chat queries are unchanged. Optional `retrieval_query` metadata contains only the user-derived retrieval topic, never document contents; it is not logged, including on errors.

Voice standalone topical requests omit recent turns and long-term recall. Deterministic deictic follow-ups (including "Why?" and "What about its GPIO pins?") and explicit conversational references receive up to `VOICE_RECENT_MESSAGES=2`, capped at four and further bounded by normal history/resource limits. Explicit user-fact recall enables Conversation Memory v2's existing lexical retrieval. Text chat retains its four-message recent window and existing memory behavior. Additive diagnostics report `recent_history_used`, `recent_history_messages` and `recent_history_chars`, separately from older-memory metrics.

The capability registry reflects configured, implemented components such as conversation persistence, local generation, whisper.cpp STT, Piper TTS, local retrieval, optional online retrieval, and optional ESP32 MQTT control. `LOCAL_RAG` is available only when RAG is enabled, its configuration is valid, and at least one chunk belongs to an indexed document. `ONLINE_RETRIEVAL` is available only when online retrieval is enabled, the local Ollama engine is selected, the provider URL is valid, and limits are valid. `DEVICE_CONTROL` and `ENVIRONMENT_SENSING` are available only when the MQTT transport, broker settings, and ESP32 device ID are explicitly configured. Camera understanding, movement, and reminders remain unavailable. Recognized requests for unavailable actions receive a natural grounded response; the assistant must not claim that an action or observation occurred.

### Conversation Memory v2

Memory is scoped to the selected conversation, not a global user/profile memory. It reuses the existing SQLite `Message` rows without schema changes, embeddings, a vector database, a second LLM summarizer, network requests or background services. Retrieval-based memory keeps recall deterministic and inexpensive while retaining original user statements instead of model-generated summaries that can introduce facts.

Recent context is captured before saving the current user query and remains chronological. Its limit is `min(CONVERSATION_RECENT_MESSAGES, execution_plan.context_message_limit)` (default four; Resource Manager can reduce it, never increase it). Older factual memory uses USER-authored messages only, excluding the current row, recent message IDs, exact repeated recent text and identical historical queries. AI-authored text remains eligible for normal recent history, not long-term memory.

Contentless deictic follow-ups such as "Tell me about it", "How does that work" and "What does it do" skip long-term lexical retrieval before any SQLite candidate scan. An explicit pronoun-plus-generic-words rule prevents weak terms such as "about" from injecting unrelated old statements. Recent history still supplies the antecedent; meaningful topical queries (including GPIO/user-fact recall) retain the existing bounded memory retrieval and metrics.

`conversations/memory.py` provides a replaceable lexical retriever. It considers at most the latest 500 eligible older USER rows, applies database-side term filtering, transfers at most 200 candidate excerpts of 4000 characters each, then ranks by distinct query-term coverage. Common conversational stop words are removed; identifiers such as `GPIO18`/`GPIO 18`, `ESP32`, `llama3.2`, hyphenated project names and filenames are supported. Default relevance is 0.5; stronger lexical matches win, with newer timestamps/IDs breaking ties. Up to two unique messages share a 400-character content budget. This is lexical recall, not semantic search: facts beyond the bounded scan horizon, synonyms or facts only appearing after the 4000-character excerpt may not be recalled. Set TOP_K or MAX_CHARS to zero to disable long-term context.

Web/text SYSTEM content stays capability grounding → response policy → document RAG → online grounding. Voice instead uses compact invariant safety → voice policy → relevant request context, without a runtime catalogue. Retrieved memory never enters SYSTEM content: selected older statements are replayed as USER history, oldest → newest, followed by chronological bounded recent turns and the current user query supplied separately. Normal LOCAL SYSTEM content is byte-for-byte identical with or without memory when other request policies match, but cross-query prefix-cache reuse is not guaranteed. Current corrections are the latest USER turn; newer memories follow older ones; document-grounded RAG instructions remain SYSTEM-level and take precedence over USER memory. Failure logs a safe warning and continues with recent context without changing routing, model selection or output budgets.

Recall stays local to SQLite and the existing local generation path; no raw memory text, prompt or message list is exposed in diagnostics. Existing conversation API access controls are unchanged. Normal API responses and streaming done events add `memory_used`, `memory_messages`, `memory_chars` (selected content only) and `memory_ms`; logs use counts/timing only. Deleting a conversation removes its messages through the existing cascade, so there is no separate memory cache to purge.

At defaults, memory adds at most 400 history-content characters across two USER turns, plus the model's normal chat-template delimiters; it adds zero SYSTEM characters or custom memory rules. The four-turn recent window keeps dynamic Pi prefill smaller. **Raspberry Pi validation of this history-native fix is still required**: compare no-memory warm LOCAL and accepted LOCAL_RAG baselines against older GPIO/project-name recall, record `memory_ms`, backend/client TTFT, total duration and grounding accuracy, and check ECO/PROTECTIVE behavior. History still needs prefill, but memory no longer changes the reusable SYSTEM content; no new hardware latency result is claimed here.

### Local Knowledge / RAG

The local knowledge pipeline reuses `Document`, `KnowledgeChunk`, and `KnowledgeIngestionService`. It stores deterministic chunks in SQLite and retrieves them with normalized lexical term coverage. Database-side term filtering prevents every chunk body from being loaded into Python. No embedding model or vector database is involved.

Supported formats are UTF-8 plain text (`.txt`), Markdown (`.md`/`.markdown`), and text-layer PDFs. PDF text extraction uses the lightweight local `pypdf` package. Scanned/image-only PDFs and encrypted PDFs return an ERROR document with a useful diagnostic; no OCR is performed. Uploads are limited to 10 MiB. PDF extraction additionally limits page count to 200, each decoded page content stream to 2 MiB, and extracted text to two million characters. These guardrails reduce typical Pi memory pressure, but a hostile PDF parser input is not a sandbox: document writes require a trusted administrator.

The `/knowledge/` page provides real file selection, drag/drop, upload progress, indexing state, reindexing and confirmed deletion. It retains the existing dashboard theme and describes the pipeline as Document Upload → Text Extraction → Chunking → Lexical Retrieval → Local AI Grounding. Sign in through the administrator link to manage files. List/read access follows the existing public dashboard; writes use staff session authentication and CSRF.

Document APIs:

| Operation | Endpoint | Result |
|---|---|---|
| List | `GET /api/knowledge/documents/` | `{"documents":[...]}` |
| Upload | `POST /api/knowledge/documents/` | Multipart `file`; 201 with `{"document":...}` on success |
| Reindex | `POST /api/knowledge/documents/<id>/reindex/` | 200 with updated document |
| Delete | `DELETE /api/knowledge/documents/<id>/` | 204; removes stored file, document and child chunks |

Send `X-CSRFToken` with each write after loading `/knowledge/`. Metadata includes ID, safe basename, MIME type, byte size, status, chunk count, upload/index timestamps and a safe error message; stored paths and source identifiers are excluded. Unsupported extensions are rejected before storage (400), oversized uploads return 413, and extraction/indexing failures return 422 with a retained ERROR document. Reindexing reuses the stored file and replaces chunks in a transaction; failed replacement preserves old chunks but marks the document ERROR so stale content is not retrieved. Uploads with the same basename remain separate records and use unique storage directories.

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

At request time, the retriever returns up to `RAG_TOP_K` chunks whose score meets `RAG_MIN_RELEVANCE`. Higher-scoring chunks retain priority with deterministic ties, and aggregate chunk text is capped at `RAG_MAX_CONTEXT_CHARS` (1200 by default; instruction/source labels add a small fixed overhead). Pi defaults are 700-character chunks, 100-character overlap, and two retrieved results. Existing documents need reindexing to adopt the new chunk sizes.

Ordinary `LOCAL_RAG` answers are capped at `RAG_NUM_PREDICT=64` output tokens, further limited by any smaller request/resource budget. Requests classified as `DETAILED` by the existing response policy retain their normal request/resource budget. Normal `LOCAL` budgets are unchanged. RAG instructions require context-only answers without unsupported elaboration, one concise sentence for directly supported ordinary facts, and an explicit insufficient-knowledge response when needed. Detailed answers still require strict grounding but may use multiple sentences. This is a single streaming generation call, not a verification or summarization stage; model adherence still needs Pi quality validation. Generation metadata (including streaming start/done) exposes the actual `num_predict`; `max_output_tokens` remains the resource-policy ceiling.

`LOCAL_RAG`, LOCAL, ONLINE synthesis and memory-backed replies all use the same configured Qwen model. No inventory request or alternate-model selection occurs in request scheduling. Deprecated `AI_LIGHTWEIGHT_MODEL` and `RAG_MODEL` settings are parsed for compatibility but ignored. Resource safety, output/context budgets, timeout, capability grounding and factual RAG constraints remain enforced.

Useful chunks are added only to the internal request instruction; user query and history remain unchanged. The shorter RAG instruction still treats local knowledge as factual reference data, ignores embedded instructions, prohibits invented missing facts, and hides retrieval details unless asked. No useful match follows the normal non-RAG path.

Generic LOCAL voice uses one complete SYSTEM message: compact identity/factual/uncertainty/action-result safety plus short spoken style. It never enumerates devices, unavailable subsystems or project-specific hardware, and does not insert diagnostics/resource notices into the prompt. The local engine's trusted request-scoped SYSTEM override avoids repeating its web/text base prompt; default engine callers and web grounding are unchanged. ACTION receives only the requested capability (hardware actions retain deterministic supplied-result responses with no LLM); LOCAL_RAG still receives document grounding/context, and ONLINE still receives its existing online evidence/safety instruction. Follow-ups get bounded history and a small reference-resolution instruction; standalone questions still get zero history.

Measured under the same local AI/Whisper/Piper/indexed-RAG fixture with online/device control disabled, complete generic NORMAL voice SYSTEM content decreased from 1297 to 443 characters (66% smaller; roughly 324 to 111 tokens at four characters/token). Its invariant safety is 261 characters and voice style is 180, plus a two-character separator. These exclude query/chat-template tokens and are not Qwen tokenizer measurements or new Pi latency results. BRIEF/NORMAL/DETAILED defaults are 32/64/192 output tokens: one direct sentence preferably <=15 words; normally 1-2 short sentences around 15-35 words; or explicitly requested detail/steps/examples/four short sentences. Explicit format overrides brevity, answers stop when complete, and smaller resource-policy budgets still win.

`warm_ai_model` primarily establishes single-model residency, then pre-evaluates only the 261-character generic voice safety instruction. It no longer collects a boot-time capability catalogue or resource report. Optional RAG prewarming remains removed; old `RAG_PREWARM_*` values are ignored. API readiness retries and the single overall timeout remain, as does systemd's 120-second startup limit. Both stages use the same 4096-token context and non-thinking controls; keep-alive defaults to 24h. An explicit diagnostic `--model` override still warms only that one requested model. Cross-query prefix-cache reuse is not promised; latency improvement comes from smaller uncached prompts/output budgets.

Historical Llama baseline: warm 3B LOCAL TTFT was ~836 ms; 3B LOCAL_RAG TTFT was ~9.5 seconds despite ~2 ms lexical retrieval. With dedicated Pi power, the latest compact Qwen benchmark decoded 94 tokens in 27.647 seconds (~3.4 tokens/s); throttle flags stayed 0x0 even at 73.8 C. Production previously used 256-272 prompt tokens and 15-17 seconds of prefill. Exact repeats cached, but different queries did not reliably reuse the prefix. Rebenchmark the smaller production voice path below; Windows tests establish correctness, not a new Pi latency figure.

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

### ESP32 Companion Display USB Serial Bridge

The optional physical face uses Raspberry Pi → USB/CP2102 UART → existing ESP32 companion-display firmware → TFT. `assistant/display/` supplies the firmware-state enum, no-op controller, settings-driven factory and persistent pyserial transport. This is separate from MQTT actions/sensing: it does not alter routing, models, RAG, memory, response policy or display firmware. `scripts/voice_loop.py` creates one controller in `main()`, reuses it across cycles and closes it on exit. Django imports do not open hardware; only the voice loop and explicit manual command create a controller.

Install the updated requirements (`pyserial==3.5`). Development defaults keep the bridge disabled:

```ini
COMPANION_DISPLAY_ENABLED=false
COMPANION_DISPLAY_PORT=
COMPANION_DISPLAY_BAUD=115200
COMPANION_DISPLAY_STARTUP_DELAY_SECONDS=1.5
```

On the deployed Pi, enable support and set **your device's** stable `/dev/serial/by-id/...` path instead of `/dev/ttyUSB0`, which can change with enumeration. For example, the validated CP2102 adapter uses:

```ini
COMPANION_DISPLAY_ENABLED=true
COMPANION_DISPLAY_PORT=/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0
```

This is an example, not a universal identifier or source-code default. Check `ls -l /dev/serial/by-id/` on your Pi and ensure the runtime user belongs to `dialout`. Use one process to own the adapter; stop the voice loop and other serial monitors before manual tests. For a service, configure serial-device permissions in that service's deployment policy if needed; existing unit security restrictions are not changed by this milestone.

The supported states exactly match firmware: `BOOTING`, `SETUP`, `CONNECTING`, `READY`, `LISTENING`, `THINKING`, `SPEAKING`, `OFFLINE`, `ECO`, `PROTECTIVE`, `ERROR`. The controller also supports `set_text()`/`clear_text()`: labels become a single printable ASCII line, capped at the firmware's 40 characters; blank text clears the label. Application code never builds raw protocol commands.

Voice lifecycle: initialization → READY; immediately before capture → LISTENING (through recording, normalization and STT); valid transcript, immediately before AI → THINKING; immediately before first audio playback → SPEAKING exactly once, not at TTS synthesis; successful final playback → ECO/PROTECTIVE for those exact metadata profiles, otherwise READY. Recording/STT/AI/TTS/playback or unexpected cycle failures → ERROR, retained until the next attempt starts LISTENING. Display failures never turn voice success into an error.

The port stays open across transitions. Writes are locked and newline-terminated, with a 50 ms write timeout, non-blocking read configuration and **no acknowledgment wait or flush**. DTR/RTS are set inactive before opening, with no per-state reset toggles. Some OS/drivers may still glitch reset lines on open; see the [pyserial API documentation](https://pyserial.readthedocs.io/en/latest/pyserial_api.html#serial.Serial.open). A configurable startup delay (0–10 seconds) runs once at initial connection, before READY, not on every state change.

Missing ports, permissions, unplugging and failed/partial writes produce concise safe warnings without user-visible tracebacks or raw text. Failed connections are closed; a later command may reconnect after a five-second cooldown, with no tight retry loop. Reconnection settling is non-blocking: commands during the configured delay are skipped, and a later update sends the latest state after settling. There is no background retry service, stale-state replay or delivery guarantee while disconnected. Initial setup errors also fall back safely; disabled/misconfigured development machines need no hardware.

Manual Pi validation (each command opens once, waits for startup settling, sends and closes):

```bash
python -m pip install -r requirements.txt
ls -l /dev/serial/by-id/
python manage.py companion_display_state READY
python manage.py companion_display_state LISTENING
python manage.py companion_display_state THINKING
python manage.py companion_display_state SPEAKING
python manage.py companion_display_state ERROR
python scripts/voice_loop.py
```

Unknown states are rejected. Manual success means bytes were sent, **not** that firmware acknowledged or the TFT was visually verified. Disabled support and transport failure return clear command errors. Physically verify stage order, first-playback timing, ERROR recovery on the next attempt, ECO/PROTECTIVE idle states, unplug/replug recovery across later updates, and unchanged AI/voice timing on the Pi. Automated tests mock serial/audio and require neither ESP32 nor microphone; no new hardware acceptance result is claimed.

### ESP32 MQTT Actions and Temperature

The first physical-device milestone uses a local Mosquitto broker and a small versioned JSON request/response protocol. The `ACTION` route maps only explicit LED commands and temperature reads to a transport-independent `DeviceController`; the MQTT implementation is kept out of `AssistantService`. Successful acknowledgments produce deterministic replies such as “The LED is on.” or “The current temperature is 28.4 °C.” without invoking the LLM. A timeout, broker failure, malformed/mismatched response, or ESP32 rejection never produces a success claim.

Install Mosquitto on the Raspberry Pi, keep it bound only to localhost or the trusted LAN, and enable the integration in `.env`:

```env
DEVICE_CONTROL_ENABLED=true
DEVICE_TRANSPORT=mqtt
MQTT_HOST=127.0.0.1
MQTT_PORT=1883
MQTT_KEEPALIVE=30
MQTT_COMMAND_TIMEOUT_SECONDS=5
MQTT_TOPIC_PREFIX=smart-companion
ESP32_DEVICE_ID=companion-esp32-01
```

Version 1 uses these device-specific topics, constructed centrally from the validated device ID:

```text
smart-companion/{device_id}/command
smart-companion/{device_id}/response
smart-companion/{device_id}/telemetry
```

An LED command and its correlated acknowledgment look like:

```json
{"version":1,"request_id":"<uuid>","action":"set_led","params":{"state":true}}
{"version":1,"request_id":"<uuid>","ok":true,"action":"set_led","result":{"state":true}}
```

A temperature request uses `"action":"read_temperature"` with empty parameters and returns `result.temperature_c`. The backend subscribes before publishing, waits only for `MQTT_COMMAND_TIMEOUT_SECONDS`, and accepts a response only when its version, request ID, action, result types, LED state, and DHT11 temperature range of 0–50 °C are valid. Raw MQTT payloads and internal topics are never persisted or exposed through API metadata.

The Arduino example is in [`firmware/esp32_mqtt_companion/`](firmware/esp32_mqtt_companion/). Install the ESP32 board support plus the `PubSubClient`, `ArduinoJson`, and `DHT sensor library` Arduino libraries. Copy `secrets.example.h` to the Git-ignored `secrets.h`, then set Wi-Fi, broker, and device-ID values. The sketch reconnects after Wi-Fi or broker loss and uses configurable pins near its top.

Recommended low-voltage wiring:

- External LED: configured `LED_PIN` → 220–330 Ω resistor → LED anode; LED cathode → GND. Adjust `LED_ACTIVE_HIGH` if the board circuit is active-low. Do not assume GPIO 2 is available.
- DHT11: VCC → ESP32 3.3 V, GND → ESP32 GND, DATA → GPIO 4 by default (the configured `DHT_PIN`). A common 3-pin DHT11 module normally includes the required pull-up. A bare 4-pin DHT11 sensor may require an external pull-up resistor of approximately 4.7–10 kΩ between DATA and 3.3 V; check the specific sensor or module rather than assuming its form factor.

After flashing the firmware, use `mosquitto_sub -v -t 'smart-companion/#'` on the trusted Pi/LAN to inspect the protocol, then ask “Turn the LED on”, “Turn the LED off”, and “What is the temperature?” through the assistant. Automated backend tests use fakes and require no broker or hardware.

Safety: this milestone is for a low-voltage LED and DHT11 only. Do not connect mains voltage, relays, motors, or other high-current loads. The v1 broker assumes a trusted isolated localhost/LAN deployment; do not expose Mosquitto to the public internet. Broker authentication/TLS can be added in a later security milestone.

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

### First-boot Wi-Fi provisioning

The Raspberry Pi appliance can start a secure `SmartCompanion-XXXX` recovery network when no saved Wi-Fi is usable. A dedicated local `/setup/` flow switches the single `wlan0` radio from AP mode to the selected client network, verifies LAN connectivity without requiring Internet access, and restores the AP after failure. The stable product address is always `http://smart-ai-companion.local:8000/`.

See [First-boot Wi-Fi setup](docs/first_boot_setup.md) for the state machine, required `SETUP_AP_PASSWORD`, NetworkManager/Avahi assumptions, boot unit installation, recovery commands, and development safety guidance.

### 2. Production Deployment (Gunicorn & WhiteNoise)

For deployment on the Raspberry Pi, use Gunicorn instead of the development server. WhiteNoise handles static files automatically.

```bash
pip install gunicorn
python manage.py collectstatic
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --worker-class gthread --workers 1 --threads 4 --timeout 180
```

The repository unit at `deployment/systemd/smart-ai-companion.service` uses one
`gthread` worker with four threads. This retains a single application process on
the memory-constrained appliance while allowing status and resource requests to
run during a long streaming response.

### 3. Raspberry Pi + Ollama (For Real LLM)

#### Install Ollama on the Pi

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama --version
```

#### Pull the Model

```bash
ollama pull qwen3.5:4b-q4_K_M
ollama list
```

#### Test Directly

```bash
curl http://localhost:11434/api/chat -d '{"model":"qwen3.5:4b-q4_K_M","messages":[{"role":"user","content":"What is an ESP32?"}],"stream":false,"think":false,"keep_alive":"24h","options":{"num_ctx":4096,"num_predict":96}}'
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
OLLAMA_MODEL=qwen3.5:4b-q4_K_M
OLLAMA_THINK=false
OLLAMA_NUM_CTX=4096
OLLAMA_KEEP_ALIVE=24h
OLLAMA_NUM_PREDICT=128
CHAT_REQUEST_TIMEOUT_SECONDS=30
CHAT_STREAM_IDLE_TIMEOUT_SECONDS=45
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
| `OLLAMA_MODEL` | `qwen3.5:4b-q4_K_M` | Sole runtime model identifier |
| `AI_LIGHTWEIGHT_MODEL` | `OLLAMA_MODEL` | Deprecated and ignored for runtime selection |
| `OLLAMA_THINK` | `false` | Top-level thinking control for Ollama chat requests |
| `OLLAMA_NUM_CTX` | `4096` | Context window sent inside options for generation and residency/prefix warmup |
| `OLLAMA_TIMEOUT` | `120` | Request timeout (seconds) |
| `OLLAMA_KEEP_ALIVE` | `24h` | How long Ollama keeps the one model loaded after a request |
| `OLLAMA_NUM_PREDICT` | `128` | Maximum number of tokens Ollama generates per response |
| `CHAT_REQUEST_TIMEOUT_SECONDS` | `30` | Minimum timeout while waiting for initial stream activity; it is raised above the backend timeout when needed |
| `CHAT_STREAM_IDLE_TIMEOUT_SECONDS` | `45` | Browser idle timeout reset whenever streamed bytes arrive; not a hard completion deadline |
| `CONVERSATION_RECENT_MESSAGES` | `4` | Recent chat rows; capped further by the resource policy |
| `VOICE_RECENT_MESSAGES` | `2` | Follow-up voice rows only; capped at four, chat-window and resource limits |
| `CONVERSATION_MEMORY_TOP_K` | `2` | Maximum older USER excerpts; zero disables long-term context |
| `CONVERSATION_MEMORY_MAX_CHARS` | `400` | Aggregate older-message history content character budget |
| `CONVERSATION_MEMORY_MIN_RELEVANCE` | `0.5` | Minimum distinct lexical query-term coverage |
| `RAG_ENABLED` | `false` | Enable local retrieval with `AI_ENGINE=local` after knowledge has been ingested |
| `RAG_RETRIEVER` | `lexical` | Local retriever implementation; v1 supports `lexical` |
| `RAG_MODEL` | `OLLAMA_MODEL` | Deprecated and ignored for runtime selection |
| `RAG_CHUNK_CHARS` | `700` | Approximate source chunk size in characters |
| `RAG_CHUNK_OVERLAP_CHARS` | `100` | Approximate overlap between adjacent chunks |
| `RAG_TOP_K` | `2` | Maximum relevant chunks supplied to one request |
| `RAG_MAX_CONTEXT_CHARS` | `1200` | Maximum aggregate retrieved chunk content |
| `RAG_NUM_PREDICT` | `64` | Ordinary LOCAL_RAG output token cap; DETAILED requests use their normal resource-bounded budget |
| `RAG_MIN_RELEVANCE` | `0.5` | Minimum lexical query-term coverage score from 0 to 1 |
| `ONLINE_RETRIEVAL_ENABLED` | `false` | Enable online snippets only for requests routed to `ONLINE` |
| `ONLINE_PROVIDER` | `searxng` | Online retriever implementation; v1 supports `searxng` |
| `ONLINE_TIMEOUT_SECONDS` | `8` | Timeout for the single provider HTTP request |
| `ONLINE_MAX_RESULTS` | `4` | Maximum useful search-result snippets retained per request (1–20) |
| `ONLINE_MAX_CONTEXT_CHARS` | `6000` | Maximum retrieved-context characters sent to the local model |
| `SEARXNG_BASE_URL` | `http://127.0.0.1:8888` | Valid HTTP(S) base URL for the self-hosted SearXNG instance |
| `DEVICE_CONTROL_ENABLED` | `false` | Enable ESP32 LED control and DHT11 reads |
| `DEVICE_TRANSPORT` | `mqtt` | Device transport; v1 supports `mqtt` |
| `MQTT_HOST` | `127.0.0.1` | Local/LAN Mosquitto hostname or IP address |
| `MQTT_PORT` | `1883` | Mosquitto TCP port |
| `MQTT_KEEPALIVE` | `30` | MQTT keepalive interval in seconds |
| `MQTT_COMMAND_TIMEOUT_SECONDS` | `5` | Total bounded command/acknowledgment timeout |
| `MQTT_TOPIC_PREFIX` | `smart-companion` | Valid configurable namespace prepended to device topics |
| `ESP32_DEVICE_ID` | _(empty)_ | Required safe identifier used to construct device topics |
| `STT_ENGINE` | `mock` | Speech-to-Text: `mock` or `whisper_cpp` |
| `STT_WHISPER_BIN` | `/opt/whisper.cpp/main` | Path to whisper.cpp binary |
| `STT_WHISPER_MODEL` | `/opt/whisper.cpp/models/ggml-base.en.bin` | Path to whisper.cpp model |
| `STT_WHISPER_THREADS` | `4` | Positive integer; explicit whisper-cli `-t` value, with or without Silero VAD |
| `STT_VAD_ENABLED` | `false` | Enable whisper.cpp Silero VAD preprocessing |
| `STT_VAD_MODEL` | _(empty)_ | Path to the Silero VAD model; required when VAD is enabled |
| `STT_VAD_THRESHOLD` | `0.5` | Speech-detection probability threshold |
| `STT_VAD_MIN_SPEECH_MS` | `250` | Minimum accepted speech-segment duration |
| `STT_VAD_MIN_SILENCE_MS` | `700` | Silence duration used to split speech segments |
| `STT_VAD_SPEECH_PAD_MS` | `100` | Padding added around detected speech segments |
| `TTS_ENGINE` | `mock` | Text-to-Speech: `mock` or `piper` |
| `TTS_PIPER_BIN` | `/opt/piper/piper` | Legacy compatibility setting; not executed by the persistent Python worker |
| `TTS_PIPER_VOICE` | `/opt/piper/en_US-lessac-medium.onnx` | Path to Piper ONNX model |
| `TTS_TIMEOUT` | `30` | Overall operation budget including lock wait, lazy load and synthesis/IPC; also bounds warm-up |
| `VOICE_AUDIO_BACKEND` | `pulse` | Standalone audio backend: `alsa` for Pi I2S, `pulse` for optional development audio |
| `VOICE_ALSA_CAPTURE_DEVICE` | `hw:CARD=sndrpigooglevoi,DEV=0` | Stable named ALSA capture device (not a numeric card index) |
| `VOICE_ALSA_PLAYBACK_DEVICE` | `hw:CARD=sndrpigooglevoi,DEV=0` | Stable named ALSA speaker device |
| `VOICE_PLAYBACK_GAIN` | `4.0` | Positive, finite ALSA speaker gain before a 0.98 limiter |
| `VOICE_INPUT_SOURCE` | _(required for pulse only)_ | PipeWire/PulseAudio microphone source used by the standalone voice loop |
| `VOICE_RECORD_SECONDS` | `5` | Speech window after microphone warm-up for each standalone voice cycle |
| `VOICE_INPUT_WARMUP_SECONDS` | `1.0` | Delay after opening the microphone before prompting the user to speak |
| `VOICE_CAPTURE_VAD_ENABLED` | `false` | Enable speech-driven capture using a lightweight live RMS gate |
| `VOICE_CAPTURE_VAD_START_THRESHOLD` | `0.02` | Normalized PCM RMS level (0–1) that starts speech capture |
| `VOICE_CAPTURE_VAD_SILENCE_SECONDS` | `0.8` | Trailing silence that ends speech-driven capture |
| `VOICE_CAPTURE_VAD_MAX_SECONDS` | `10` | Maximum speech capture duration after speech starts |
| `VOICE_CAPTURE_VAD_START_TIMEOUT_SECONDS` | `5` | Maximum time to wait for speech after `Speak now...` |
| `VOICE_LEADING_SILENCE_SECONDS` | `0.7` | Digital silence on the first playback chunk only; set `0.5` for Pi I2S |
| `VOICE_STREAM_TTS_TARGET_CHARS` | `180` | Center target for sentence coalescing and long-text word-boundary fallback |
| `VOICE_STREAM_FIRST_CHUNK_MIN_CHARS` | `60` | Minimum sanitized first-unit characters while streaming (final short answers still flush); must be positive |
| `VOICE_STREAM_FIRST_CHUNK_TARGET_CHARS` | `100` | First-clause target (must be at least the minimum); never a forced word cut |
| `VOICE_STREAM_INTER_CHUNK_SILENCE_SECONDS` | `0.12` | Persistent ALSA boundary pause target, counting retained Piper edge silence; finite range `0`–`1` |
| `VOICE_TTS_CHUNK_CHARS` | `300` | Legacy batch speech-helper chunk size; streaming uses the setting above |
| `VOICE_TTS_MAX_TOTAL_CHARS` | `0` | Legacy batch speech-helper safety limit; streaming speaks the full answer without truncation |
| `VOICE_LLM_BRIEF_NUM_PREDICT` | `32` | Request-scoped Ollama budget for brief voice responses |
| `VOICE_LLM_NORMAL_NUM_PREDICT` | `64` | Request-scoped Ollama budget for normal voice responses |
| `VOICE_LLM_DETAILED_NUM_PREDICT` | `192` | Request-scoped Ollama budget for detailed voice responses |
| `AI_GENERATION_NORMAL_VOICE_NUM_PREDICT` | `384` | Voice-only healthy NORMAL resource ceiling; capped at 512, never raises ECO/PROTECTIVE limits |

## Standalone Raspberry Pi Voice Loop

The standalone loop reuses the configured Whisper, AI engine, and Piper providers; it does not change the browser voice API. Raspberry Pi production audio uses the INMP441 microphone and MAX98357A speaker amplifier directly through ALSA. PulseAudio/PipeWire (including Bluetooth earbuds) remains an optional development backend and the default for backwards compatibility.

For the verified Pi 5 I2S setup (`dtparam=i2s=on`, `dtoverlay=googlevoicehat-soundcard`), install `alsa-utils` and `ffmpeg` if needed. The INMP441 L/R pin is grounded, so only LEFT contains microphone audio. Use these deployment values in `.env`:

```env
VOICE_AUDIO_BACKEND=alsa
VOICE_ALSA_CAPTURE_DEVICE=hw:CARD=sndrpigooglevoi,DEV=0
VOICE_ALSA_PLAYBACK_DEVICE=hw:CARD=sndrpigooglevoi,DEV=0
VOICE_PLAYBACK_GAIN=4.0
VOICE_LEADING_SILENCE_SECONDS=0.5
VOICE_STREAM_INTER_CHUNK_SILENCE_SECONDS=0.12
```

ALSA capture uses native 48 kHz stereo S32_LE, then FFmpeg explicitly selects LEFT with `pan=mono|c0=FL` and produces mono 16 kHz `pcm_s16le` for Whisper. It does not average the silent RIGHT channel into speech. Each Piper chunk is converted to headerless 48 kHz stereo S32_LE with `volume=4.0,alimiter=limit=0.98:level=false` (the gain is unchanged/configurable). A single persistent `aplay` owns the named ALSA device for the entire streamed response. Numeric card indices are intentionally not used. Capture and preparation commands are:

```bash
arecord -D hw:CARD=sndrpigooglevoi,DEV=0 -c 2 -r 48000 -f S32_LE -t wav recorded.wav
ffmpeg -y -i recorded.wav -af 'pan=mono|c0=FL' -ar 16000 -ac 1 -c:a pcm_s16le normalized.wav
ffmpeg -y -i stream-playback-001.wav -af 'volume=4.0,alimiter=limit=0.98:level=false' -ar 48000 -ac 2 -c:a pcm_s32le -f s32le stream-speaker-source-001.pcm
aplay -D hw:CARD=sndrpigooglevoi,DEV=0 -t raw -f S32_LE -r 48000 -c 2
```

The loop trims each source PCM into `stream-speaker-001.pcm`, etc., and writes those files plus controlled silence into the same `aplay` stdin; it never writes WAV headers to that stream. EOF closes stdin after the last chunk, then the loop waits for device drain and verifies the exit code before returning the TFT to idle. ALSA nonblocking pipe writes use at most 64 KiB at a time, so device backpressure can be cancelled without a third worker. stderr is file-backed, avoiding a pipe-reader thread/deadlock. Playback has one 300-second response-lifetime bound; errors/cancellation terminate, kill if necessary, and reap the child. Pulse retains its existing per-chunk `paplay` behavior; legacy batch WAV helpers remain available.

The loop bounds fixed capture in Python (warm-up plus `VOICE_RECORD_SECONDS`, followed by terminate/kill and reap), rather than adding `-d` to `arecord`. Optional live capture uses the same native format with `-t raw`, computes RMS directly from signed 32-bit LEFT samples, and writes a native WAV for the same final normalization. No per-chunk FFmpeg process is used for VAD. A wall-clock watchdog also kills a stalled ALSA pipe; normal pre-roll, start timeout, silence stop, and maximum speech duration remain in effect.

Whisper uses an explicit, configurable `STT_WHISPER_THREADS=4`, with or without Silero VAD. Invalid/non-positive thread values fail with `STTError` before transcription launches; timeout/error handling is otherwise unchanged. For the validated Pi 5, base.en is the recommended current deployment model: set `STT_WHISPER_MODEL` in the Pi's `.env` to its actual installed base.en path. No deployment-specific home path or model switch is introduced into portable source defaults. Supplied Pi base.en measurements were approximately 3.48/2.50/2.02 seconds with 2/3/4 threads, with accurate transcripts and throttle flags 0x0; these are prior physical results, not a new benchmark of this patch.

Optional Silero VAD runs inside whisper.cpp after the fixed-duration recording. To enable it, set `STT_VAD_ENABLED=true` and copy the exact Silero model path on the Pi into `STT_VAD_MODEL`; disabling it removes only the VAD arguments, not the explicit thread setting. Microphone capture/VAD implementation is unchanged.

```env
STT_WHISPER_THREADS=4
STT_VAD_ENABLED=false
STT_VAD_MODEL=
STT_VAD_THRESHOLD=0.5
STT_VAD_MIN_SPEECH_MS=250
STT_VAD_MIN_SILENCE_MS=700
STT_VAD_SPEECH_PAD_MS=100
```

For optional Pulse development audio only, find the microphone source and add it to `.env`:

```bash
pactl list short sources
```

```env
VOICE_AUDIO_BACKEND=pulse
VOICE_INPUT_SOURCE=
VOICE_LEADING_SILENCE_SECONDS=0.7
```

Copy the exact source returned by `pactl list short sources` into `VOICE_INPUT_SOURCE`. ALSA does not require that setting. Shared voice-loop tuning (keep the Pi's `0.5` silence setting above):

```env
VOICE_RECORD_SECONDS=5
VOICE_INPUT_WARMUP_SECONDS=1.0
VOICE_CAPTURE_VAD_ENABLED=false
VOICE_CAPTURE_VAD_START_THRESHOLD=0.02
VOICE_CAPTURE_VAD_SILENCE_SECONDS=0.8
VOICE_CAPTURE_VAD_MAX_SECONDS=10
VOICE_CAPTURE_VAD_START_TIMEOUT_SECONDS=5
VOICE_TTS_CHUNK_CHARS=300
VOICE_TTS_MAX_TOTAL_CHARS=0
VOICE_STREAM_TTS_TARGET_CHARS=180
VOICE_STREAM_FIRST_CHUNK_MIN_CHARS=60
VOICE_STREAM_FIRST_CHUNK_TARGET_CHARS=100
VOICE_LLM_BRIEF_NUM_PREDICT=32
VOICE_LLM_NORMAL_NUM_PREDICT=64
VOICE_LLM_DETAILED_NUM_PREDICT=192
```

With the chosen backend configured, run:

```bash
python scripts/voice_loop.py
```

Press Enter to record one cycle, wait for `Speak now...`, or type `q` and press Enter to exit. By default, the microphone remains open for the warm-up plus the full `VOICE_RECORD_SECONDS` window, preserving the original fixed-duration behavior. Set `VOICE_CAPTURE_VAD_ENABLED=true` to wait for speech and stop after trailing silence instead. This live mode uses a lightweight PCM RMS gate and keeps 300 ms of pre-roll to avoid clipping the first word; whisper.cpp Silero VAD remains a separate downstream validation option. The `record` timing includes the complete microphone-open operation, and live mode also prints the captured audio duration.

#### Persistent Piper initialization and cleanup

`PiperProvider` owns a dedicated `multiprocessing.get_context("spawn")` Python worker. The child imports `piper.voice.PiperVoice`, loads the configured `TTS_PIPER_VOICE` once with `use_cuda=False`, and reuses it across chunks and voice cycles. Native ONNX inference never runs in the voice-loop process. No voice, gain/limiter, PCM conversion, silence, edge trimming or persistent ALSA architecture changes are made.

The existing `synthesize(text)` and `synthesize_cancellable(text, cancel_event)` APIs still return complete standard WAV bytes. The internal duplex Connection carries bounded, framed JSON byte messages (not external pickle input): `ready`, `synthesize(request_id, text)`, `result(request_id, base64 WAV)`, safe error codes, and `shutdown`/`closed`. Request IDs increase monotonically and are validated by both sides. Invalid/stale/malformed replies or incomplete WAVs fail without audio reuse. A provider lock serializes requests; cancelled waiters do not interrupt someone else's active synthesis.

The parent checks cancellation, worker death and the monotonic deadline at intervals of at most 50 ms. Blocking sends/receives, including partial WAV frames, are isolated in an IPC helper thread so native/IPC stalls do not trap the deadline owner. Cancellation, timeout or protocol/crash failure stops/reaps the worker, closes handles and clears state; a later request can start a fresh worker, never consume old audio. The overall `TTS_TIMEOUT` includes lock wait, readiness/model loading and synthesis/IPC rather than giving each stage a fresh budget. Cleanup can add bounded terminate/kill/reap time after that deadline.

`warm()` explicitly starts/loads the voice. Standalone `voice_loop.py` calls it once before accepting the first interaction in microphone, `--text`, `--audio-file` and `--text-mode` modes, outside all per-cycle timings. `--help` does not initialize/warm speech. A failed warm-up reports a startup error, closes the display/provider and does not begin a cycle. After a later worker failure, lazy recovery can include model load in that recovery request's timing. All existing first-text/audio/speech, total-TTS, preparation and starvation metrics retain their meanings.

`close()` is idempotent and asks an idle child to shut down gracefully (0.5-second grace), then uses terminate/join (2 seconds) and kill/join (2 seconds) as needed. IPC helper joins are also bounded. Explicit cleanup runs on q/EOF/Ctrl+C, single-input completion, stream failure and fixture utility exit; display cleanup remains independent. Cached-provider reset closes its worker before dropping the cache. A weak-reference atexit fallback and daemon-child process cleanup provide additional exit safety, not a substitute for explicit close. `close()` tears down the model but permits a later explicit/lazy restart on the reusable provider.

Compatibility: `TTS_PIPER_BIN` and the constructor's `binary_path` argument remain accepted, but the persistent backend does not execute that CLI. The **same Python environment as the application must have the already-used `piper-tts` 1.8 package**; a Piper CLI installed only in another environment is insufficient. No new requirement/dependency is added here. Keep the existing Lessac medium `.onnx` path and adjacent voice configuration in the Pi's deployment `.env`; no Jagadish-specific source default is introduced. Package/model load failures produce safe, clear errors without logging request text or native exception details.

To validate on the Pi, keep existing settings and run `python scripts/voice_loop.py --text-mode`: the startup warm-up should occur once, followed by repeated text requests without repeated model loads. Compare `first_audio_ready - first_tts_text_ready`, `tts` and playback starvation with the previous measurements. Also test an injected response cancellation and normal q exit, checking that neither the Piper worker nor aplay remains after exit. Existing supplied Pi API timings (roughly 0.7-0.9 seconds per synthesis after a 1.855-second load, versus 2.9-3.2 seconds for fresh CLI processes) motivate this change; they are not new acceptance measurements of this implementation.

#### Development input injection (no microphone required)

The input options are mutually exclusive. Without any option, the existing physical microphone/VAD mode is unchanged:

```bash
python scripts/voice_loop.py --audio-file /tmp/test.wav
python scripts/voice_loop.py --text "What is an ESP32?"
python scripts/voice_loop.py --text-mode
```

`--audio-file` runs once: prints `Input audio: <path>`, normalizes the supplied audio through the existing ffmpeg helper to 16 kHz mono signed 16-bit PCM WAV, calls the configured Whisper provider, and prints `You: <transcription>`. It never records with the microphone, `arecord` or capture VAD. Arbitrary input audio uses normal mono conversion rather than the INMP441-specific left-channel extraction. The source file is not rewritten/deleted; the normalized temporary copy is cleaned up. Provider-side Whisper configuration is unchanged.

`--text` runs once: prints `Injected text: <query>` and bypasses capture, VAD, normalization and STT completely. `--text-mode` prompts `[text, q=quit] > ` repeatedly, ignores empty lines, and retains `conversation_id` across successful turns so `Tell me about it.` uses the existing follow-up policy/history. Query case is preserved; `q` quits and EOF exits. A failed turn reports its error without discarding the previous successful conversation. Conversation memory implementation is unchanged.

All injected inputs converge on the same response policy, `AssistantService.process_message_stream()`, Piper/TTS workers, persistent ALSA playback and THINKING/SPEAKING/READY lifecycle (or existing ECO/PROTECTIVE states). They do not create an alternate generation or speech implementation. Normal Assistant output and timing continue; bypassed stages are absent from Timing instead of reporting invented capture/STT durations. Existing cancellation, subprocess reaping, worker/temporary-file cleanup and display closure remain in effect. One-shot failures exit nonzero, and Ctrl+C during a one-shot cycle exits 130. Injection skips the microphone-source requirement but retains output configuration validation; no `.env` changes are needed.

Generate repeatable speech inputs using the already configured TTS provider:

```bash
python scripts/make_voice_test_audio.py --text "What is an ESP32?" --output /tmp/esp32.wav
python scripts/voice_loop.py --audio-file /tmp/esp32.wav
```

Use the existing `TTS_ENGINE=piper` configuration for actual synthetic speech. The utility calls the configured provider once, validates its WAV output, and saves those bytes without playback or another dependency. The destination directory must already exist; the named output file is replaced on success. A mock TTS provider only produces its mock WAV, not genuine speech. This tests Piper-generated input → Whisper → Qwen → Piper output, **not real microphone quality, capture VAD, acoustics or recognition of human speech**.

Exact Pi validation commands (using the existing deployed environment and audio/display settings):

```bash
cd /home/jagadish/apps/smart-ai-companion
source venv/bin/activate
python scripts/voice_loop.py --help
python scripts/voice_loop.py --text "What is an ESP32?"
python scripts/voice_loop.py --text-mode
```

In text mode, enter `Explain how Raspberry Pi works.`, then `Tell me about it.`, then `q`. Next generate/replay synthetic inputs:

```bash
python scripts/make_voice_test_audio.py --text "What is an ESP32?" --output /tmp/esp32.wav
python scripts/voice_loop.py --audio-file /tmp/esp32.wav
python scripts/make_voice_test_audio.py --text "Explain how Raspberry Pi works." --output /tmp/pi.wav
python scripts/voice_loop.py --audio-file /tmp/pi.wav
pgrep -a aplay
```

Verify transcription prints only in audio-file mode, normal streamed output/timing and speaker audio appear in both, TFT transitions remain correct, and no `aplay` child remains after each command has finished (when no other audio session is running). Also interrupt one injected response with Ctrl+C and repeat the process check. Optionally run `python scripts/voice_loop.py` afterward to confirm the existing microphone entry point is still available; the synthetic runs alone do not validate it.

The voice loop applies the assistant response policy before generation. Brief requests stay direct, normal requests remain concise but sufficient, and explicit requests for detail, steps, examples, or comparisons can use the larger detailed budget and produce complete answers. Ambiguous commands ask for clarification. Recognized actions that require an unavailable sensor, device, camera, retrieval source, or scheduler are answered honestly instead of being presented as completed.

The voice loop consumes `AssistantService.process_message_stream()` directly: `start` selects the conversation ID, `delta` feeds a sentence-aware speech buffer, `done` flushes the final speech remainder and supplies the canonical response/metadata, and `error` cancels audio and reports ERROR. Routing, grounding, memory, model selection, response budgets and persistence remain owned by the service; there is no additional AI call or browser endpoint change. The service still persists the complete canonical response, including any resource notices.

Voice-only policy asks for one direct short sentence (preferably <=15 words) in BRIEF mode, and normally 1-2 short sentences (roughly 15-35 words) in NORMAL mode. Background, examples and caveats belong only when needed to answer the request. Explicit detail, steps, examples and comparison requests remain conversational DETAILED answers; explicit formatting/length overrides the defaults (four sentences still means four). A plain "explain how X works" stays NORMAL. Avoid stock openings and project/internal tangents. History still resolves pronouns, follow-ups and user-provided facts, but must not redirect a standalone named-topic question to unrelated earlier project discussion. These are generation instructions, not a second summarization call or a text-chat policy change; actual model adherence needs Pi listening validation.

The terminal prints `Assistant: ` once, then prints actual answer deltas immediately with `flush=True`, preserving their spaces/newlines and Markdown. It finishes the line at `done` (or on failure) and never reprints the full final answer. Terminal presentation runs on the producer; neither audio worker performs terminal writes or waits for them. Known initial resource notices appear once per distinct notice as `Status: ...`, not as assistant text or speech. A small leading-only filter handles notices split across deltas and repeated initial notices; it does not delete matching text inside an actual answer. Genuine direct answers, including resource-unavailability explanations, remain visible and spoken.

The service adds one resource prefix before engine deltas and verifies the final response against that prefix plus the engine result. Thus a second copy inside the canonical answer is model-generated, not a second service insertion. Voice presentation suppresses these initial repeats without rewriting persisted history or web output; fixing their underlying model/history cause is outside this audio change.

Before the first speech unit is emitted, completed sentences below `VOICE_STREAM_FIRST_CHUNK_MIN_CHARS=60` sanitized characters are held and combined with following text. A final short answer such as "The answer is 4." still flushes at `done`. First-unit priority is: (1) a completed sentence of at least 60 characters within `VOICE_STREAM_FIRST_CHUNK_TARGET_CHARS=100`; (2) the latest available safe comma/semicolon/colon clause within target +20; (3) the latest available natural phrase boundary before a complete `because`, `due to`, `while`, `but`, `so`, `which` or `that`, with the same minimum/grace bounds; (4) once 100 spoken characters are available, the latest safe whitespace boundary at/before target, or the earliest available safe boundary within the 20-character grace window. No timer, extra model call or global target reduction is introduced.

First-unit candidates never cut words, Markdown links (including incomplete label/URL arrivals), parentheses/bracket/brace/quote groups, bare URLs or formatted numbers. Whitespace/phrase candidates back up before obvious dangling articles/conjunctions/prepositions when an earlier useful boundary exists. Safety and the minimum beat the size target: a long protected group or indivisible word may require a larger first unit or holding until completion, rather than cutting it or emitting a tiny prefix. A safe long completed sentence remains usable if no smaller candidate exists. Common titles/dotted abbreviations remain conservatively excluded as sentence boundaries. These are deterministic already-available-text heuristics, not a general linguistic parser; delta sizes can affect which safe candidates are available.

For example, `Solar panels convert sunlight into electricity by using photovoltaic cells,` can start Piper while the rest of the sentence is still being generated. The minimum counts actual sanitized speech, not URL/Markdown syntax or resource notices. Later short completed sentences coalesce into units around the existing `VOICE_STREAM_TTS_TARGET_CHARS=180`: normally 120-220 characters, with punctuation retained inside the same Piper input for natural prosody. The buffer uses bounded character-based holding, no sleep or timer; final short leftovers flush at `done`. Long unpunctuated text retains the safe word-boundary fallback. Text order, Markdown sanitization and closing punctuation remain supported. The legacy batch chunk/total limits do not truncate streaming answers. Terminal deltas print immediately, independently of speech readiness.

Only at completion, after a unit containing a complete sentence has already been submitted to playback, speech may omit a clearly dangling final fragment (for example, ending in "a Linux-based", "the", or "and"). An unpunctuated answer is not automatically incomplete; a sole short answer and useful unpunctuated facts remain spoken. This conservative heuristic is presentation-only: terminal and persisted canonical text are never rewritten.

Earlier Pi diagnostics confirmed dangling responses at `eval_count=96` and `num_predict=96` under ECO. Healthy BALANCED/PERFORMANCE voice requests retain a separate bounded resource ceiling, `AI_GENERATION_NORMAL_VOICE_NUM_PREDICT=384` (hard maximum 512). Actual BRIEF/NORMAL/DETAILED defaults are now 32/64/192; BRIEF requests one direct sentence preferably <=15 words, and NORMAL prefers 1-2 short sentences around 15-35 words. Explicit three-or-more-sentence requests select DETAILED headroom, not a two-sentence limit. Text requests retain their existing NORMAL default of 128. ECO REDUCED 96, PROTECTIVE MINIMAL 48, policy-unavailable and safety-unknown reductions remain unchanged; blocked generation stays blocked. Ordinary LOCAL_RAG still applies its 64-token cap, while detailed RAG remains resource-bounded. Shorter style instructions reduce truncation risk but cannot guarantee arbitrary explicit formats finish within their budget.

Final generation metadata adds `eval_count` and `output_limit_reached = eval_count >= num_predict` only when the engine supplies a nonnegative integer token count. Otherwise both are absent, not fabricated. This flags probable token exhaustion, not linguistic completeness; canonical output is persisted truthfully even if cut off. Closing a generated stream after its validated, persisted `done` event no longer logs an incomplete client disconnect; abandoning it before `done` still does. No additional inference stage, ALSA/Piper change, STT or model-TTFT improvement is claimed.

The main thread consumes generation and performs service persistence. The audio pipeline has exactly two workers:

```text
AI deltas -> sentence buffer -> text queue (2)
    -> one Piper/FFmpeg/PCM-edge preparation worker -> ready-audio queue (2)
    -> one persistent ALSA aplay worker (or sequential Pulse paplay)
```

Preparation of chunk N+1 overlaps playback of N while generation continues. Prepared ALSA items contain unique raw file paths and edge-silence counts, not response-long audio bytes. Each chunk uses unique temporary filenames, so preparation cannot overwrite a file in use. PCM scan/copy/play writes are bounded to small blocks, with no unbounded response audio in RAM. Speech packing uses the first-unit and bounded coalescing rules above. Queue backpressure bounds pending work. There is never more than one Piper/FFmpeg preparation process or one playback process active.

Independent Piper synthesis may add edge silence even when the next chunk is ready. ALSA preparation conservatively scans 10 ms native PCM windows at a very low -80 dBFS peak threshold. It removes only excess quiet edge windows and retains a 30 ms guard beside detected speech. First-chunk leading audio is left intact; its excessive trailing silence and later chunks' leading/trailing excess are controlled. All-quiet chunks and interior pauses remain intact. Every above-threshold sample is copied unchanged; very faint below-threshold phonemes cannot be identified with certainty, which is why guard padding and Pi listening validation remain important. No real Piper recording was supplied in this checkout, so actual Pi edge levels/phonemes have not been measured here.

At a boundary, the player inserts only `max(0, target - previous retained trailing - next retained leading)` frames of zero-valued PCM. The default target is 0.12 seconds; 10 ms edge-window resolution means actual quiet boundaries can be slightly longer. Retained Piper silence is counted, not doubled, and a natural pause remains even with both chunks already ready. The initial configured 0.5 seconds is generated as native zero PCM exactly once before chunk 1. No silence or pause is implemented using `sleep()`.

On AI/TTS/preparation/playback failure, shared cancellation discards queued work, cancels/reaps active Piper/FFmpeg/aplay/paplay processes, unblocks both queues through cancellation-aware waits, closes the service generator, and joins both workers before temporary files are removed. The original failure is retained instead of being replaced by secondary cancellation errors. An audio failure is surfaced when the bounded service stream next yields or times out. The next interaction starts at LISTENING.

The display changes to SPEAKING exactly once, after preparation and immediately before opening the persistent player/writing initial silence, remains SPEAKING throughout the response and final device drain, then returns to READY/ECO/PROTECTIVE. Pulse still prepends its first-chunk WAV silence before playback. ALSA uses `arecord`, `ffmpeg`, and `aplay`; Pulse retains `parecord`, `ffmpeg`, and `paplay`. Wake-word detection is not implemented.

Timing values are seconds, printed to millisecond precision:

- `record`, `normalize`, `stt`: existing capture, normalization and transcription durations.
- `ai_ttft`: AI stream consumption start to first actual answer text, excluding resource notices.
- `first_tts_chars`: sanitized character count of the first unit actually dispatched to Piper, including its punctuation, excluding resource notices/Markdown syntax.
- `first_tts_text_ready`: AI stream consumption start to the buffer's first speakable text-unit emission, before enqueue/synthesis.
- `first_tts_unit_ready`: backward-compatible alias of `first_tts_text_ready`; it does **not** mean synthesized audio is ready.
- `first_audio_ready`: AI stream consumption start to completion of the first Piper synthesis and audio preparation (including ALSA conversion/edge trimming), immediately before the ready-audio enqueue. Compare it with text readiness and `ai_to_first_speech` to separate buffering, synthesis/preparation and playback-start delays.
- `ai_to_first_speech`: AI stream consumption start to first playback launch, after display setup.
- `first_speech`: voice-cycle start to that same playback launch (includes capture/STT).
- `ai_total`: stream consumption start to `done`, including service preparation and queue backpressure; `ai` remains an alias.
- `tts`: accumulated Piper synthesis time.
- `audio_prepare`: accumulated WAV saving/ALSA raw conversion/edge trimming time, excluding queue waits and playback (Pulse includes first-chunk WAV silence).
- `playback`: ALSA persistent-player lifetime from first launch through PCM handling, readiness waits and final drain/reap; includes intentional PCM silence, not FFmpeg preparation. Pulse retains summed playback-command durations.
- `playback_chunks`: number of prepared chunks fully submitted to playback.
- `num_predict`: actual request generation budget from the service's final metadata, when supplied; not a new or lowered limit.
- `playback_starvation_ms`: accumulated readiness wait after one chunk is consumed and before another arrives; an already-ready chunk contributes exactly zero. First preparation, intentional PCM pauses and final EOF-marker wait are excluded.
- `max_playback_starvation_ms`: largest such readiness wait in the response.
- `total`: complete voice-cycle wall time through final playback and cleanup.

Playback-launch metrics exclude the following 0.5-second first-chunk digital silence and do not measure speaker-driver/acoustic latency. Stages overlap, so their sum is not the wall-clock total. For sufficiently long streamed responses, compare `ai_to_first_speech < ai_total` to confirm speech began before AI completion.

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

The suite includes local knowledge chunking/ingestion/retrieval tests plus mocked Ollama, voice-provider, and standalone voice-loop coverage. Tests run without real Ollama, Whisper, Piper, PulseAudio services, or ALSA audio hardware. Focused voice/display tests: `python manage.py test assistant.tests_voice_loop assistant.tests_voice_stream assistant.tests_alsa_stream assistant.tests_alsa_audio assistant.tests_display assistant.tests_voice`. Persistent pipe tests also use harmless Python child processes, not sound hardware. Run only the RAG coverage with:

```bash
python manage.py test knowledge_base.tests knowledge_base.tests_rag
```

## Streaming, Prewarming, and Latency Diagnostics

Text chat uses `POST /api/assistant/chat/stream/` with CSRF protection and
`application/x-ndjson`. The original `POST /api/assistant/chat/` JSON endpoint
is retained for API clients and the voice path. Every stream line is one JSON
object with one of these stable event shapes:

```json
{"type":"start","conversation_id":12,"direct":false,"effective_model":"qwen3.5:4b-q4_K_M","route":"local"}
{"type":"delta","text":"Edge"}
{"type":"done","conversation_id":12,"response":"Edge computing ...","ttft_ms":180,"generation_ms":8050,"execution_ms":8075}
{"type":"error","error":"generation_error","detail":"The local AI service is currently unavailable or unreachable.","partial":true}
```

The browser buffers incomplete NDJSON lines, updates one pending AI bubble with
`textContent`, and resets an idle timer as bytes arrive. A successful generated
answer is accumulated and persisted as exactly one AI message. If generation
fails after a partial response, the UI labels it interrupted and the backend
does not persist that incomplete AI text as a completed answer.

Run the explicit warm-up after Ollama starts:

```bash
python manage.py warm_ai_model
python manage.py warm_ai_model --model qwen3.5:4b-q4_K_M --timeout 90
```

The sole default target is `OLLAMA_MODEL`; no alternate model is warmed.
`deployment/systemd/smart-ai-companion-model-warm.service`
weakly depends on the standard Ollama installer unit, `ollama.service`. The app
unit wants and starts after the warm unit, but does not require it, so a temporary
Ollama failure is visible in `systemctl`/`journalctl` without preventing the
dashboard from starting.

Install the reviewed unit templates on the Pi with:

```bash
sudo install -o root -g root -m 0644 deployment/systemd/smart-ai-companion-model-warm.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deployment/systemd/smart-ai-companion.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable smart-ai-companion-model-warm.service smart-ai-companion.service
sudo systemctl restart smart-ai-companion.service
systemctl status --no-pager ollama.service smart-ai-companion-model-warm.service smart-ai-companion.service
journalctl -u smart-ai-companion-model-warm.service -u smart-ai-companion.service -b --no-pager
ollama ps
```

If Ollama was installed under a differently named unit, adjust only the weak
`Wants=`/`After=` references after confirming the real unit with
`systemctl list-unit-files | grep -i ollama`.

Structured logs report safe stage timings for resource policy, routing, local
RAG, online retrieval, prompt preparation, time to first token, generation, and
total execution. Prompts, document contents, conversation history, and secrets
are not logged. Ollama counts/durations and calculated token throughput remain
internal structured metrics except the safe output-count/limit fields documented above.

Ollama requests use top-level `think=false` and `options.num_ctx=4096`, rather than relying on model defaults. These controls also apply to prefix warmup; residency loading uses the same context size. See [Ollama thinking controls](https://docs.ollama.com/capabilities/thinking) and [context parameters](https://docs.ollama.com/modelfile#valid-parameters-and-values).

## Raspberry Pi Benchmark Results

Measured on Raspberry Pi 5 (8 GB), Debian 13 ARM64, Python 3.13.5,
Django 5.2.17, and Ollama 0.34.2:

| Model / state / budget | TTFT | Total | Load | Generation | Output rate |
|---|---:|---:|---:|---:|---:|
| `llama3.2:3b`, cold, 64 max tokens | 27.05 s | 36.94 s | 25.74 s | 9.89 s | 6.07 tok/s |
| `llama3.2:3b`, warm, 48 max tokens | 0.18 s | 8.05 s | 0.00 s | 7.87 s | 6.10 tok/s |
| `llama3.2:1b`, cold, 64 max tokens | 18.51 s | 25.28 s | 17.69 s | 6.77 s | 8.71 tok/s |
| `llama3.2:1b`, warm, 48 max tokens | 0.13 s | 5.60 s | 0.01 s | 5.48 s | 8.76 tok/s |

With both models resident, measured memory was 5.1 GiB used, 2.8 GiB
available, and no swap, with approximately 2.9 GB for 3B and 1.8 GB for 1B.
The 48-token samples truncated mid-answer, so normal mode remains at a quality-
preserving budget. Streaming improves perceived latency by exposing warm TTFT;
total completion time remains bounded by Raspberry Pi CPU generation speed.

For an acceptance run, execute one cold and three warm prompts at the normal
budget, record client-observed first-delta time and final time, compare the final
NDJSON metadata with Ollama metrics in the journal, verify `load_duration` is
near zero after warm-up, then repeat under ECO pressure to confirm reduced budgets on the same
configured Qwen model, without switching or duplicate residency. Also issue `/api/system/resource-manager/` requests
during generation to confirm the threaded worker remains responsive.

### Dedicated-power voice acceptance procedure

In `/home/jagadish/apps/smart-ai-companion/.env`, change only the two voice budgets below; verify the other five existing values. Do not change audio, display, MQTT, RAG, capture or networking settings:

```env
VOICE_LLM_BRIEF_NUM_PREDICT=32
VOICE_LLM_NORMAL_NUM_PREDICT=64
VOICE_LLM_DETAILED_NUM_PREDICT=192
OLLAMA_MODEL=qwen3.5:4b-q4_K_M
OLLAMA_KEEP_ALIVE=24h
OLLAMA_THINK=false
OLLAMA_NUM_CTX=4096
```

After transferring this reviewed working-tree change to the Pi (no commit/push is performed by this task), stop any interactive voice loop and activate the existing environment. Restart Django so it reloads code/settings, then explicitly rerun warm-up:

```bash
cd /home/jagadish/apps/smart-ai-companion
source venv/bin/activate
sudo systemctl restart smart-ai-companion.service
python manage.py warm_ai_model --timeout 90
ollama ps
vcgencmd get_throttled
```

Run this exact in-process voice-generation benchmark. It uses the actual response plan, service, streaming engine, prompt construction and metadata; it does not fake output or add an inference call. Each query starts a fresh conversation (saved as ordinary benchmark history). It reports the complete SYSTEM size and actual Ollama tokens/cache/prefill/decode metrics, without dumping internal prompts. First-delta/total times cover the service path, not microphone/STT/TTS, HTTP transport or acoustic latency.

```bash
python manage.py shell <<'PY'
import json
import time
from unittest.mock import patch
from assistant.ai_engine import get_engine
from assistant.policy import AssistantResponsePolicy
from assistant.services import AssistantService

engine = get_engine()
build_request = engine._build_chat_request
generate_stream = engine.generate_stream
diagnostics = {}

def observed_request(*args, **kwargs):
    request = build_request(*args, **kwargs)
    body = json.loads(request.data)
    diagnostics['system_chars'] = len(body['messages'][0]['content'])
    return request

def observed_stream(*args, **kwargs):
    for event in generate_stream(*args, **kwargs):
        if event.type == 'done':
            diagnostics.update(event.result.metrics)
        yield event

queries = [
    'What is an ESP32?',
    'Explain how Raspberry Pi works.',
    'What is MQTT?',
    'Briefly explain RAM.',
    'Explain edge computing in four short sentences.',
]
with patch.object(engine, '_build_chat_request', observed_request), patch.object(engine, 'generate_stream', observed_stream):
    for query in queries:
        diagnostics.clear()
        started = time.perf_counter()
        first = None
        for event in AssistantService.process_message_stream(query, response_plan=AssistantResponsePolicy.plan_voice_response(query)):
            if event['type'] == 'delta' and event['text'].strip() and first is None:
                first = round((time.perf_counter() - started) * 1000)
            if event['type'] == 'error':
                raise RuntimeError(event)
            if event['type'] == 'done':
                fields = ('conversation_id', 'route', 'effective_model', 'num_predict', 'resource_profile', 'generation_budget',
                          'ttft_ms', 'generation_ms', 'recent_history_messages', 'memory_used', 'output_limit_reached')
                report = {key: event.get(key) for key in fields}
                report.update(diagnostics)
                report.update(query=query, service_first_delta_ms=first,
                              total_ms=round((time.perf_counter() - started) * 1000), response=event['response'])
                print(json.dumps(report, sort_keys=True), flush=True)
PY
```

Use different questions, not just exact repeats. NORMAL should report SYSTEM 443 characters, budget 64, zero recent history, Qwen only, and no unrelated hardware limitations; BRIEF should report 32 and four-sentence DETAILED 192 unless genuine resource pressure reduces it. Treat actual `prompt_eval_count` as authoritative; the four-characters/token approximation excludes the query and chat template. Record uncached (`prompt_eval_cached_count=0`, when supplied) results as well as cached ones. Confirm model load is near zero after residency warm-up; do not require cross-query cache hits.

During generation, inspect the Resource Manager dashboard or poll it from another terminal:

```bash
watch -n 2 curl -fsS http://127.0.0.1:8000/api/system/resource-manager/
```

With the physical-like healthy snapshot, expect BALANCED/NORMAL even at CPU 100%/73.8 C, diagnostic `TEMPERATURE_WARM`, and no reduced-resource notice. Genuine normalized load >=1.0 or temperature >=75 C should still select ECO; active throttling or >=80 C remain PROTECTIVE. Do not deliberately overheat the Pi or induce undervoltage for this benchmark. After the run, check power/temperature and then run the unmodified physical voice loop to measure STT, first speech, Piper/ALSA gaps and TFT states:

```bash
vcgencmd get_throttled
vcgencmd measure_temp
journalctl -k -b --no-pager
python scripts/voice_loop.py
```

Ask the same distinct standalone questions, an actual supported device action, a relevant local-document question and a configured online question. Verify factual/direct answers, supplied-result grounding, concise speech, one playback stream per response, and existing TFT state transitions. Review voice-loop Timing lines separately from the generation-only measurements above.

## Current Implementation Status

| Component | Status |
|---|---|
| Control Center UI | ✅ Implemented |
| Mock AI Engine | ✅ Implemented |
| Local LLM Engine (Ollama) | ✅ Implemented (uses /api/chat with system prompt) |
| Qwen3.5 4B Q4_K_M single model | ✅ Resource-bounded local integration |
| Conversation Persistence | ✅ Implemented |
| Document Upload | ✅ Implemented |
| Device Metrics (simulated) | ✅ Implemented |
| Companion State Control | ✅ Implemented |
| Local RAG Pipeline | ✅ Text/Markdown/PDF + bounded lexical retrieval and lightweight synthesis |
| Voice Input (STT - whisper.cpp) | ✅ Implemented (API & Browser Mic) |
| Voice Output (TTS - Piper) | ✅ Implemented (API & Browser Playback) |
| Online Retrieval | ✅ Optional SearXNG snippets + local Ollama generation |
| ESP32 LED Control | ✅ Optional MQTT request/acknowledgment integration |
| DHT11 Temperature | ✅ Optional MQTT request/response integration |
| Other Hardware Sensors/Mics | ⬜ Not implemented |
| Raspberry Pi Deployment | ✅ Deployed (Django + local LLM) |

### Important Notes

- The local LLM runs on **CPU only** (Raspberry Pi 5 has no GPU). Inference speed is limited by ARM CPU performance.
- Local RAG uses lexical matching rather than semantic embeddings, so sources and questions should share meaningful terminology.
- Text-layer PDFs are supported; OCR and scanned/image-only PDF extraction are not included.
- Online retrieval is opt-in and used only for `ONLINE` routes; all answer generation remains local through Ollama.
- The system prompt establishes the companion persona but the model's behavior depends on its training.

## Future Integration Plan

1. **Mock AI** ✅
2. **Local LLM** ✅
3. **Voice Pipeline** ✅ (Browser UI -> STT -> LLM -> TTS -> Browser Audio)
4. **Physical Hardware I/O** ← add Pi-connected microphone and speaker
5. **Local LLM + lexical RAG** ✅
6. **Local LLM + RAG + Online Retrieval** ✅ (deterministic router + optional SearXNG snippets)
7. **ESP32 MQTT LED + DHT11** ✅ (safe low-voltage action milestone)
