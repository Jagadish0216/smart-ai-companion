# Adaptive factual answering

Dynamic public facts select ONLINE without requiring a freshness adjective:
news, weather, prices, scores, rankings, releases, schedules, availability,
service/flight status and current office holders. Greetings, definitions,
historical questions, device actions and explicit personal/project documents
stay local. Explicit web requests authorize public search; explicit offline
requests take precedence. An offline request for current facts returns a
clearly labeled inability to verify them, rather than a guessed answer.

`online_allowed` means permission to **attempt** retrieval, not proof of Internet
access. NetworkManager FULL/LIMITED/UNKNOWN/OFFLINE still informs network health
and provisioning; it no longer vetoes configured retrieval. Thermal, memory,
generation/model budgets and hardware interfaces retain their existing rules.
Resource protection can still stop generation before retrieval.

## Search bounds and health

- At most two query attempts: retry a transient connection/timeout/502/503/504,
  or remove conversational filler after an empty result. Never retry auth,
  rate-limit, malformed JSON or portal responses within that request.
- All attempts share `min(ONLINE_TIMEOUT_SECONDS, 8)` seconds, with at most
  three seconds per connection/read operation. Streaming body reads reset their
  socket deadline and stop at 1 MB. OS hostname resolution can exceed urllib's
  socket timeout; the default numeric loopback endpoint avoids that DNS step.
- Successful, meaningful external search results validate search health. An
  empty search without recent health performs one IANA example-domain search;
  meaningful IANA results are required. A reachable local socket, empty JSON,
  or unresponsive external engines alone do not establish external connectivity.
  The optional canary shares the same deadline (maximum three HTTP calls total).
- Positive health lasts 30 seconds; failures suppress new calls for 10 seconds.
  Recovery is automatic after the cooldown. No background probe on local questions.
  HTML portals and cross-origin redirects fail closed. Authentication and network
  access controls are never automated or bypassed. A configured provider is a
  trust boundary: its JSON cannot cryptographically prove external connectivity.
- Two concurrent retrieval slots per process; excess work fails promptly instead
  of queueing. Process-local FIFO caches hold at most 64 query digests and eight
  provider-health records. Keys contain no plaintext query. Cache results live
  15 seconds for price/score/status/traffic/stock/live queries, 30 seconds for
  weather/news/today/now, otherwise 120 seconds. Healthy empty results last five
  seconds. Each worker/voice process has its own cache; restarting clears it.

Results reject malformed records, unsafe/credential URLs, private literal IPs,
duplicates and near-empty text. Scripts, styles, controls and forged source
delimiters are stripped. At most 100 raw entries are inspected; retained snippets
are bounded to 900 characters and titles to 180. Existing result-count settings
remain supported; interactive grounding uses at most four snippets and 3600
characters, even when larger limits are configured. No new dependency/model.

## Evidence and limitations

Admission checks topical term overlap and publication time (or an explicit ISO
date in the snippet). Retrieval time is never substituted for publication time.
Undated, future-dated or stale evidence cannot support a current answer. Maximum
ages: news/recent events two days; office holders/rankings/schedules/availability
seven days; weather three hours; prices/scores/status/live/traffic/stocks fifteen
minutes; other facts thirty days. These are conservative admission bounds,
not assurances that an observed fact remains true. Missing precise timestamps
often means abstention for live values. Provider timestamps are untrusted metadata.

Explicit conflicting role, price or score statements trigger direct abstention.
General semantic disagreement still relies on the grounding instruction; this
is not a general fact verifier. The local model must cite only supported snippet
facts, preserve source/date attribution, express uncertainty for conflicting or
incomplete material, and never present itself as a cloud LLM. Existing prompt
injection instructions and weather/news-specific safety rules remain. Current
answers omit chat/memory history so earlier claims cannot substitute for evidence.
Source metadata exposes publication/retrieval dates and URLs, never snippet bodies.

Search unavailable, successful search with no relevant current evidence, explicit
conflicts, explicit offline requests and resource protection have separate fallback
reasons. Failure responses do not ask Ollama to invent current facts. Timeless
local explanations, local documents and device operations retain their paths.

## Diagnostics and configuration

Both chat and stream completion metadata expose `route`, `online_attempted`,
`online_success`, `online_results`, `online_latency_ms`, `fallback_reason`,
`generation_ms`, `execution_ms` and, when available, `request_ttft_ms`.
`online_attempted` means the retrieval pipeline was invoked, including a cache
hit/cooldown; `online_success` means search completed with validated health,
even if evidence admission subsequently rejects all results. `online_results`
counts snippets actually grounded, not raw hits. `ttft_ms` remains backend timing;
`request_ttft_ms` includes preparation/search (observed first model delta for
streams, backend TTFT plus measured preparation for nonstreaming calls).

Performance logs use an allowlist of numeric timings, route, counts and reason
codes. They exclude query/conversation text, credentials, source text, URLs and
exception details. Existing conversation persistence is unchanged.

No operational configuration change is required. Existing opt-in settings:

```dotenv
ONLINE_RETRIEVAL_ENABLED=true
ONLINE_PROVIDER=searxng
SEARXNG_BASE_URL=http://127.0.0.1:8888
ONLINE_TIMEOUT_SECONDS=8
ONLINE_MAX_RESULTS=4
ONLINE_MAX_CONTEXT_CHARS=6000
```

The interactive context cap is 3600 even with the existing 6000 setting. For a
tighter retrieval budget, set `ONLINE_TIMEOUT_SECONDS=4`; this can increase
abstentions on slow upstream engines. Offline remains the default when disabled.
No database migration, model change, driver change or systemd edit is required.
Do not overwrite `.env`, `/etc/smart-ai-companion/demo.env`, knowledge or models.

## Validation and Pi deployment

Run on the workstation (PowerShell); the local temp path avoids Windows sandbox
rename restrictions encountered by existing thermal tests:

```powershell
$env:TMP = Join-Path (Get-Location).Path '.test-tmp'
$env:TEMP = $env:TMP
venv/Scripts/python.exe manage.py test assistant.tests_adaptive_online assistant.tests_online assistant.tests_routing assistant.tests_streaming assistant.tests_execution_policy system.tests_resource_manager knowledge_base.tests_online_sync assistant.tests_policy assistant.tests_push_to_talk assistant.tests_voice_loop assistant.tests_devices assistant.tests_short_answer_thermal assistant.tests_voice_stream --noinput
venv/Scripts/python.exe manage.py check
git diff --check
```

Tests mock external networking, Ollama and physical interfaces. They verify both
service paths, voice response plans, FULL and UNKNOWN reports, portals, outages,
timeouts, retries, cache bounds/expiry/recovery, stale/irrelevant/malformed evidence,
explicit conflicts, privacy and offline requests. They do not prove physical
GPIO17, UART/TFT, microphone/amplifier operation or real Pi latency/connectivity.
No changes were deployed to the Pi during development.

Workstation validation on 2026-10-24: all 428 tests in the command above passed
in 26.035 seconds; Django checks reported no issues and `git diff --check` passed.
The initial default Windows temp-directory rename failures were resolved by
using the workspace temp directory shown above.

After review, transfer the focused commit through the normal remote or a bundle
(`git bundle create adaptive-online.bundle HEAD^..HEAD` on the workstation). On
the Pi, first inspect `git status --short` and stop if it is dirty. Record
`git rev-parse HEAD` for reference. Fetch the bundle, then apply only the focused
commit; conflicts require review, never a reset:

```sh
cd /home/jagadish/apps/smart-ai-companion
git status --short
git fetch /path/to/adaptive-online.bundle HEAD
git cherry-pick FETCH_HEAD
venv/bin/python manage.py test assistant.tests_adaptive_online assistant.tests_online assistant.tests_routing assistant.tests_streaming assistant.tests_execution_policy system.tests_resource_manager --noinput
venv/bin/python manage.py check
git rev-parse HEAD  # record the deployed fix SHA for rollback
sudo systemctl restart smart-ai-companion.service smart-ai-companion-voice.service
sudo systemctl --no-pager status smart-ai-companion.service smart-ai-companion-voice.service
```

Restart only after tests/checks pass. Keep the existing models, PTT service
arguments, GPIO17, ESP32 UART/TFT, STT and audio configuration. Inspect effective
service environment locally without copying credentials into logs; the voice
unit's existing root-owned demo environment may override `.env`. Authenticate to
the college network manually through its normal flow if needed.

Verify text and PTT: a greeting/recursion question (no online attempt), an explicit
local-note question (no public search), CEO/news/weather (dated evidence or clear
abstention), repeated public query (cache), and provider unavailable/portal cases
(clear failure, no invented values). Verify one LED/sensor command, TFT updates,
PTT press/release, microphone capture and amplifier playback. Do not disrupt a
shared network to simulate failure; use controlled mocked tests or a separate
test process with an unavailable provider endpoint. UNKNOWN with a working
provider must still attempt retrieval.

For latency, sample at least 30 comparable requests per category before/after:
local, cold search, repeated search, search failure and PTT. Record only the safe
metadata above. Compare median and nearest-rank p95 of `execution_ms` and
`request_ttft_ms`, with the same warmed model, resource profile and network.
Collect cold and cached samples separately; do not infer Pi p50/p95 from mocked
Windows timings. Physical latency measurements remain a deployment check.

Rollback code without discarding local changes or resetting data:

```sh
git status --short  # stop and preserve any local work first
git revert <recorded-deployed-fix-sha>
venv/bin/python manage.py check
sudo systemctl restart smart-ai-companion.service smart-ai-companion-voice.service
```

If optional `.env` settings were changed, restore only those specific values from
the recorded previous configuration. A restart clears all in-memory search health
and evidence caches. No data/model rollback or database reset is needed.
