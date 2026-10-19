# Smart AI Companion system audit

Audit date: 2026-10-19. Accepted baseline: `f59da97d270ebe1157dac65e9862ec8ed77116cd`.
Branch: `system-performance-audit-v1`. Inspection precedes production changes.

## Evidence and status matrix

IMPLEMENTED means an executable code path was inspected. AUTOMATED-TESTED means
the Windows baseline suite passed, not that external hardware operated.
HARDWARE-VALIDATED below is the user's accepted Pi/TFT report, not a new observation.
PARTIAL identifies a material limit. NOT IMPLEMENTED is used only for absent paths.

| Component | Status | Implementation and automated evidence | Physical evidence / limits |
|---|---|---|---|
| Django dashboard, REST, streamed chat | IMPLEMENTED; AUTOMATED-TESTED; PARTIAL | `dashboard/views.py`, `assistant/views.py`, serializers; dashboard, browser voice and streaming suites | No browser or deployed-device session run in this audit; some LAN endpoints lack access control |
| Assistant orchestration and persistence | IMPLEMENTED; AUTOMATED-TESTED | `assistant/services.py`; assistant, streaming, execution-policy suites | User reports accepted Pi suite; SQLite writes remain sequential |
| Deterministic routing, capability safety | IMPLEMENTED; AUTOMATED-TESTED | `routing.py`, `policy.py`; routing/policy/single-model tests | Classification uses explicit patterns, not general intent understanding |
| Resource response budget | IMPLEMENTED; AUTOMATED-TESTED | `execution_policy.py`; resource-manager/execution-policy tests | Profile caps can shorten detailed output; do not increase them without thermal evidence |
| Ollama | IMPLEMENTED; AUTOMATED-TESTED; HARDWARE-VALIDATED | `ai_engine/local.py`; single-model/streaming tests | Accepted Qwen CPU execution; no new Pi throughput measurement |
| Local ingestion and lexical RAG | IMPLEMENTED; AUTOMATED-TESTED; PARTIAL | `knowledge_base/services.py`, `retrieval.py`; ingestion/RAG/v2 suites | SQL substring candidates followed by Python token scoring; no semantic/vector search, no scanned-PDF OCR |
| Conversation memory | IMPLEMENTED; AUTOMATED-TESTED | `conversations/memory.py`; memory/single-model suites | Same-conversation earlier USER statements only; bounded scan, recent-message exclusions; voice recall is explicitly gated |
| SearXNG and freshness | IMPLEMENTED; AUTOMATED-TESTED; PARTIAL | `online/searxng.py`, grounding, `knowledge_base/online_sync.py`; online/sync suites | Snippets, not full-page verification; expiry prevents stale online knowledge from local retrieval; provider/network not exercised here |
| whisper.cpp STT | IMPLEMENTED; AUTOMATED-TESTED; HARDWARE-VALIDATED | `voice/stt.py`; voice/capture tests mock executable | Reported accepted Pi speech chain; binary launched per transcription; real noise/accent coverage unverified |
| ALSA capture and VAD | IMPLEMENTED; AUTOMATED-TESTED; HARDWARE-VALIDATED | `scripts/voice_loop.py`, `capture_vad.py`; capture/ALSA tests | Real microphone calibration and cutoffs require observation |
| Piper persistent worker | IMPLEMENTED; AUTOMATED-TESTED; HARDWARE-VALIDATED | `voice/tts.py`, `piper_worker.py`; synthetic spawned-worker recovery/cancellation tests | Real voice loading/synthesis accepted by user; synthetic worker timings do not measure real Piper |
| PCM preparation, persistent ALSA playback | IMPLEMENTED; AUTOMATED-TESTED; HARDWARE-VALIDATED | `voice/alsa_stream.py`, voice-loop workers; ALSA/voice-stream suites | Accepted clear audio; pipe acceptance and queue starvation are not DAC timestamps or proven audible underruns |
| Wake word / hands free | IMPLEMENTED; AUTOMATED-TESTED; PARTIAL | `voice/wakeword/`, `hands_free.py`; wake/hands-free suites | Always-on detection, false triggers, recovery and feedback in the room need live acceptance |
| ESP32 TFT serial | IMPLEMENTED; AUTOMATED-TESTED; HARDWARE-VALIDATED | `display/serial_display.py`, audio-level meter; display/audio-level suites | User accepted READY/THINKING/SPEAKING, captions, LEVEL, QUERY/MODE and recovery. Firmware unchanged |
| MQTT device actions | IMPLEMENTED; AUTOMATED-TESTED; PARTIAL | `devices/intents.py`, `mqtt.py`; device tests | Only allowlisted LED and temperature actions; request IDs, bounded payloads, nonretained commands. Physical actuator/sensor operation not established by TFT acceptance |
| Host telemetry / resource manager | IMPLEMENTED; AUTOMATED-TESTED; PARTIAL | `system/control_plane/resources.py`, `resource_manager.py`; resource tests | Read-only CPU/RAM/thermal/throttle policy; sustained loaded Pi samples still needed |
| systemd / startup / provisioning | IMPLEMENTED; AUTOMATED-TESTED; PARTIAL | `deployment/systemd/`, provisioning/network modules; provisioning tests | Unit paths/user are deployment-specific; no service restart or boot tested here |
| Authentication and configuration | IMPLEMENTED; AUTOMATED-TESTED; PARTIAL | Django session/admin checks for Wi-Fi connection; setup isolation middleware; provisioning/API tests | Default DEBUG/dev secret, chat/history/dashboard exposure and LAN trust require deployment review |
| Camera understanding, movement, reminders/timers | NOT IMPLEMENTED | `CapabilityRegistry.from_settings` explicitly marks these unavailable; policy tests cover refusals | No physical or scheduling support implied by conversation responses |
| Scanned-document OCR / semantic vector retrieval | NOT IMPLEMENTED | PDF ingestion explicitly rejects empty extracted text; retriever factory supports lexical only | No background embedding model added |
| Full physical end-to-end acceptance in this audit | NOT IMPLEMENTED | Windows has no attached Pi microphone/speaker/TFT | Prepared validation script and human checklist; no remote changes |

## Findings from baseline inspection

1. **P1: LAN trust is a security boundary, not user isolation.** Chat API reads
   conversation IDs without per-user ownership checks; dashboard views have no
   login mixin. Network configuration has explicit admin protection, but that
   does not protect chat/history. Deployment binds port 8000 to all interfaces.
   Before any untrusted network exposure, require authentication/authorization,
   production secret, DEBUG false and deployment-specific TLS/access control.
   Changing accepted appliance access behavior requires a separate design decision.
2. **P2: lexical RAG hydrates unnecessary rows/columns.** Each candidate joins all
   Document metadata and instantiates Document and KnowledgeChunk objects, while
   scoring needs only six scalar columns. A projection preserves eligibility,
   scores, ties and freshness with less transfer and Python object work. No
   candidate cap or scoring change is justified here.
3. **P2: latency evidence needs decomposition.** Existing Ollama counts/durations,
   stage timings, first TTS text/audio and starvation metrics are useful. Missing
   prompt character counts and separate Piper readiness/exchange timings prevent
   attributing the first-speech delay. Add only scalar diagnostics.
4. **P2: queue starvation is not audible starvation.** The playback metric times
   a miss in the prepared-audio queue after a chunk was written, while ALSA may
   still contain buffered sound. The reported 806.928 ms and clear audio are
   compatible. Changing buffering/silence to optimize that number alone is unsafe.
5. **P2: timeout/cancellation limits.** urllib socket timeouts bound inactivity,
   not total streaming wall time; a slow trickle can exceed the nominal request
   timeout. Voice workers check cancellation, but an active blocking model read
   can defer generator cleanup until it returns. Propose a separately tested
   transport deadline before changing this accepted path.
6. **P2: quality bounds.** Default voice token budgets are brief 32, normal 64,
   detailed 192, subject to resource caps; RAG concise cap is 64. Output-limit
   metadata and unfinished-tail handling need quality review on real answers.
   History is message-count bounded, not a combined token budget; large stored
   messages can consume the 4096 context. Do not silently truncate memory.
7. **P3: telemetry cost.** A resource-manager cache miss collects network,
   services and host state sequentially; CPU sampling alone sleeps 100 ms and
   service checks have separate bounded timeouts. Healthy cached calls should be
   compared with cache misses before introducing parallel probes/threads.
8. **P3: expected sequential work.** STT must precede routing. Routing/RAG and
   memory must precede grounded inference. Piper and playback already overlap
   inference via two bounded queues and two workers. Online persistence is
   synchronous before generation; consider deferral only after durability and
   freshness tests. No extra background model is warranted.
9. **P3: compatibility paths.** `select_rag_model` and inventory-provider arguments
   remain deprecated compatibility interfaces; model switching is absent from
   active policy. Piper's binary-path field is legacy: the worker imports the
   Python Piper package. Keep these compatible rather than deleting them here.
10. **P2: concurrent request pressure.** The systemd web unit uses one gthread
    worker with four threads. Long streamed generations occupy thread slots and
    can contend for the same CPU-only Ollama backend with the standalone voice
    loop. There is no inspected shared inference admission queue across these
    processes. Measure concurrent dashboard/voice behavior before changing
    workers or allowing more parallel inference on the Pi.
    The generic service-health helper also marks Django READY in-process; that
    does not independently establish Gunicorn's listener or dashboard HTTP
    availability. The acceptance harness probes the listener separately.
11. **P3: storage failure atomicity.** `DeleteDocumentAPIView` deletes the stored
    file inside a database transaction, before transaction commit. A later DB
    failure could restore the row without restoring its file. Upload/replacement
    paths have explicit cleanup handling; deletion recovery needs its own
    failure-injection design and tests, outside performance changes.

## Access-control release review

These findings exist at the accepted baseline and are unchanged by this feature.
Source evidence: `assistant/views.py`, `dashboard/views.py`,
`templates/dashboard/settings.html`, `system/views.py`, `knowledge_base/views.py`,
`system/middleware.py`, and the installed DRF defaults (AllowAny, no throttles).
Session authentication alone does not require login. CSRF tokens protect browser
request provenance; an anonymous direct client can fetch its own CSRF cookie.

| Priority | Endpoint / method | Impact | Exploitation prerequisites | Current-deployment determination |
|---|---|---|---|---|
| P1 | `GET /api/assistant/chat/?conversation_id=...`; `GET /conversations/`, `/assistant/` | Read another conversation's text; enumerate titles and IDs; no ownership check | Reachable normal-mode application and an existing conversation; no login or CSRF needed for GET | Isolated payload exposure confirmed; live anonymous chat GET without ID returned 400 and conversations HEAD returned 200. No real messages or IDs downloaded |
| P1 | `POST /api/assistant/chat/`, `/api/assistant/chat/stream/`, `/api/assistant/voice/transcribe/` | Append to arbitrary known conversations, consume inference/storage; allowlisted LED/temperature actions if MQTT control is configured and reachable | Network reachability; configured provider for inference; device-control setting/broker/device for physical actions. Stream requires anonymously obtainable CSRF. Legacy chat/full-voice endpoints have no explicit anonymous CSRF enforcement | Code path confirmed; isolated chat POST reached a mocked service anonymously without CSRF. Live inference/writes deliberately not tested; current Pi device-control configuration unknown |
| P1 when secrets are stored there; otherwise P2 | `GET /settings/` | Renders every SystemSetting value in plaintext | Reachable normal-mode application; sensitive values must actually be in that table. This does **not** establish `.env` disclosure | Live anonymous HEAD 200; synthetic table value exposed in isolated fixture. Real contents deliberately not read; not a confirmed Pi credential leak |
| P2 (P1 if logs contain secrets) | `GET /logs/`, `/api/system/logs/` | Read operational messages and anything previously stored in those logs | Reachable normal-mode application; sensitive content must exist | Live API HEAD 200; synthetic log exposed; real log content not fetched |
| P2 | `GET /knowledge/`, `/api/knowledge/documents/` | Read document metadata, titles, source URLs and saved sync-query text; not a demonstrated raw-file download | Reachable normal-mode application with stored documents | Live API HEAD 200; synthetic metadata exposed; private Pi documents not read |
| P2 | `POST /api/assistant/voice/transcribe-only/`, `/api/assistant/voice/synthesize/` | Consume STT/Piper CPU; endpoints have input bounds/timeouts but no authentication or request-rate policy | Reachability, working provider, anonymous CSRF cookie/token | Explicit CSRF decorators verified in code; provider resource exhaustion not attempted |
| P2 | `POST /api/system/companion/control/` | Modify simulated dashboard state and add command rows | Reachable normal-mode application, no login or anonymous CSRF token required | Synthetic POST succeeded. Controller is explicitly `MockDeviceController`; **not** the accepted UART TFT or physical brightness/state control |
| P2/P3 depending on shared-network trust | `GET /api/system/network/`, `/api/system/network/wifi/scan/`, `/api/system/metrics/`, `/api/system/resource-manager/`, `/api/system/services/`, `/api/system/companion/state/`, `/api/system/setup/handoff-status/`; related dashboard pages | Network/host/service/state reconnaissance, including connected SSID in handoff status | Reachable application; handoff status deliberately exists outside setup; most surfaces blocked by setup isolation when active | Inspected source; no private telemetry copied from Pi |
| Intended provisioning boundary; P1 if setup is exposed to untrusted peers | `GET /setup/`, `/api/system/setup/status/`, `/api/system/setup/wifi/scan/`; `POST /api/system/setup/wifi/connect/` | During active setup, a reachable peer can attempt Wi-Fi provisioning | Active SETUP_AP/FAILED state (CONNECTING narrows paths), reachability/AP access; valid anonymous CSRF for POST and cooldown. NORMAL_MODE rejects setup | Existing state/CSRF/cooldown controls verified; historical Pi presentation evidence reports setup 404 outside setup. Current AP state unknown |
| Protected boundary (no unauthenticated bypass identified) | `POST /api/system/network/wifi/connect/`; `POST /api/knowledge/documents/`; `POST .../reindex/`, `.../sync/`, `/api/knowledge/sync-stale/`; `DELETE /api/knowledge/documents/<id>/` | Wi-Fi change and document mutations require staff session; admin UI requires login | Staff login and valid session CSRF for writes | Live anonymous OPTIONS to Wi-Fi connect and document 0 reindex returned 403; isolated anonymous Wi-Fi/upload POSTs returned 403; declarations inspected for other mutations |

**Live evidence and limits:** the shipped unit binds `0.0.0.0:8000`. That permits
non-loopback connections subject to real interfaces/firewall/proxy rules;
ALLOWED_HOSTS is not authentication (a direct client can send an allowed Host).
Historical `LIVE_PI_RESPONSIVE_ACCEPTANCE.md` records HTTP access on
`smart-ai-companion.local:8000` and unavailable SSH, at an older dashboard
revision. It does not establish today's revision, firewall, reverse proxy,
DEBUG/SECRET_KEY overrides, Internet port forwarding, or AP password policy.
Initial sandboxed probes failed with URLError. Retrying outside restricted
networking reached the current Pi: anonymous dashboard HEAD 200, chat GET
without an ID 400, setup HEAD 404. Additional anonymous HEAD requests to
`/conversations/`, `/settings/`, `/api/system/logs/`, and
`/api/knowledge/documents/` all returned 200. Anonymous OPTIONS to Wi-Fi connect
and document 0 reindex returned 403. The user confirmed this hostname and powered-on
application, and explicitly limited live work to non-destructive status checks.
No personal response bodies were downloaded, and no live inference, commands,
configuration or firewall writes were submitted. Requests used no login, cookies
or authorization headers. No authentication or firewall was bypassed.

**Exploitability conclusion:** the missing application authorization is
demonstrated in NORMAL_MODE using isolated synthetic data. An untrusted client
that can reach the current Pi running this code can exploit the applicable
endpoints; authentication at a verified upstream boundary could prevent it.
The **current** Pi admits anonymous requests from this host to data-bearing
surfaces, so a blanket upstream login boundary is absent on these tested paths.
Together with the source and isolated tests, treat the baseline P1 exposure as
applicable to reachable peers; the actual private-data payload was deliberately
not exercised live. Current remote Git SHA, object-specific changes outside the
inspected source, Internet reachability and access from other network segments
remain unverified. No current Pi credential leak,
arbitrary command execution, firmware control or unbounded actuator capability
was demonstrated.

**Release blockers:** no newly introduced P0/critical vulnerability was found in
the reviewed performance diff. Existing P1 data access and anonymous inference
are blockers for public, guest-LAN or other untrusted-network release until an
access boundary is verified or separately fixed. They do not prevent committing
and publishing this feature for controlled Pi testing. Treat the current Pi's
broader network boundary as an unresolved deployment check, and perform benchmarks only
in the operator's controlled environment. No authentication rewrite or access
behavior change is included in this performance milestone.

## Final RAG and diagnostics review

Compared every intended implementation line with the exact accepted baseline.
Only six-column projection changes retrieval behavior at the ORM boundary:
INDEXED eligibility, manual/fresh-ONLINE rules, OR substring candidate filter,
tokenization, distinct-concept rule, relevance comparison **before rounding**,
four-decimal score, top-k heap, and final `(-score, document_id, chunk_index,
chunk_id)` order all remain identical. The unique `(document, chunk_index)`
constraint preserves stable source order. No candidate cap changes recall.
Iterator batches remain 200, QuerySet result caching is avoided, and the heap
retains at most configured top_k results. Memory is proportional to a batch,
one chunk's token counts and top_k, not the corpus; SQL scan cost and unusually
large stored chunks remain existing limits.

New diagnostic payloads are bounded dictionaries of counts/durations/booleans
and fixed completion labels. Actual-message character counts cannot carry text.
Piper logs only readiness/exchange durations, reuse, character and WAV sizes.
The service additionally rejects unexpected text/objects, bool-as-numeric values
and nonfinite floats at its metrics boundary. Public API metadata still omits
the internal Ollama dictionary. Existing application logs and metadata are not
claimed to have undergone a general privacy rewrite.

## Validation record

Baseline Windows: Python 3.10.2 project venv; `manage.py test --noinput
--verbosity=1`: 1,002 tests in 34.150 s, OK, one Linux-only skip. This measures
regression correctness, not Pi performance. Final results and change inventory
are recorded in `pi-performance-optimization.md`.

Finalization rerun: **1010 discovered tests, 1009 passed, one Linux-only skip,
38.525 s, exit 0**. Targeted RAG, single-model, projection, diagnostics and
validation cleanup: **83 tests passed in 0.949 s**. Isolated characterization
also demonstrated anonymous synthetic reads/writes and protected mutation 403s
with CSRF checking enabled; it never contacted live hardware.

No firmware, wiring, enclosure, production database, knowledge files, credentials
or hardware configuration were edited. The user subsequently authorized an
explicit-file commit and push of `system-performance-audit-v1` for Pi testing.
Merging main and changing the production deployment remain outside this milestone.
