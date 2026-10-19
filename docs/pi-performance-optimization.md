# Pi performance investigation and acceptance preparation

2026-10-19. Accepted baseline: `f59da97d270ebe1157dac65e9862ec8ed77116cd`.
Feature branch: `system-performance-audit-v1`, finalized for controlled Pi testing
under the user's explicit commit/push authorization. **No new Pi measurement
or end-to-end speed improvement is claimed.** The architecture/status matrix and
prioritized correctness/security findings are in `system-end-to-end-audit.md`.

## Observed baseline and interpretation

These are the user's accepted Pi observations; this audit did not reproduce them.

| Metric | Reported Pi baseline | New Pi result |
|---|---:|---|
| First AI token | 5.272 s | Pending |
| First speech | 12.258 s | Pending |
| AI generation | 16.703 s | Pending |
| Total voice request | 23.914 s | Pending |
| Prepared playback queue starvation | approximately 806.928 ms | Pending |
| Perceived audio | clear, no stutter | Pending |

Do not sum overlapping generation, synthesis and playback stages. First speech
is currently timestamped before player startup/write, not at physical sound onset.
First AI token in the voice loop is the first speakable, notice-filtered content;
engine TTFT starts at the Ollama HTTP request. Compare each metric only with the
same origin. The apparent 6.986 s difference between reported first-token and
first-speech numbers cannot be assigned to Piper without common-clock evidence.

## Investigation across the latency path

| Area | Inspected behavior / risk | How acceptance separates it |
|---|---|---|
| Cold/warm model | Existing 24h keep-alive and explicit model + minimal voice-prefix warmer; never switches the configured Qwen model. OS pressure, restart or external Ollama clients can still change residency | `/api/ps` before the initial question; initial request separated from repeated warm questions; per-request load duration. Never force unload |
| Prefill/decode/cache | Engine already reads backend prompt/decode counts, durations and throughput; cached count is optional, absent is not zero | Preserve all allowlisted metrics in internal service completion metadata. Compare load, prompt eval, eval, TTFT and optional cache count independently |
| Prompt size | Voice has minimal safety, explicit document intent and reference-gated history; text uses richer capabilities. RAG aggregate default 1200 chars; online context default 6000 chars. Large individual history messages can still fill context | New input-message, input-character and system-character counts refer to actual sent messages. Counts are not token estimates; no prompt text logged |
| Response budgets | Brief/normal/detailed defaults 32/64/192; healthy voice ceiling 384, ECO/unknown/protective retain their own caps. No additional model call repairs a truncated answer | Record requested/effective token budget, resource profile, eval count, allowlisted stop reason and existing output-limit flag; listen/read for incomplete facts and steps |
| First complete TTS unit | Buffer waits for useful punctuation/minimum size, with fallback boundaries; short answers can wait for stream completion. Defaults first min 60, target 100 chars, later target 180 | Existing first-TTS-text-ready, first-audio-ready and AI-TTFT timings; harness derives token-to-first-unit using the same stream origin |
| Piper startup/synthesis | Spawned Python worker loads voice once; requests serialized; complete WAV returned through bounded framed IPC. No external binary per chunk | New `ready_ms`, `worker_reused`, `exchange_ms`, character/WAV sizes. Ready time includes startup/handshake; exchange includes synthesis + IPC, not pure model CPU time. First-request/worker-reuse comparison |
| Audio prep and drain | Each chunk writes WAV, ffmpeg converts PCM and conservative edge trimming runs. One aplay per response, not per chunk; final EOF drains | Existing preparation, playback, first-ready and chunk-count timings. Compare ALSA null and physical sink separately; never equate them |
| Queue starvation / silence | Two bounded queues (capacity two each), one synthesis/preparation worker, one playback worker. Gap padding accounts for retained quiet edges | Queue miss is producer readiness, not hardware underrun. Report total and maximum waits, listen for gaps/clicks and compare equal audio/sink mode |
| Callbacks/UART | RMS over 80 ms windows; bounded 256 amplitude summaries; coalesced LEVEL/keepalive, serial write timeout 50 ms and retry interval 5 s. Captions and LEVEL run from existing audio work, not an extra pacing thread | Existing mocked disconnect/observer-failure coverage; physical display and lost-USB checklist. No baud/protocol/firmware change |
| RAG and memory | RAG SQL substring candidates can scan a large corpus; existing heap bounds winners, not candidates. Memory bounds scan to 500 earlier USER messages, at most 200 candidates and 4000 chars per row | Synthetic 1000-chunk RAG timing, candidate projection A/B, 600-message memory timing; same filters/ranking. Production corpus timing still required before scaling/index changes |
| Online/freshness | SearXNG bounded query/results/response and timeout; snippet grounding and TTL; expired online chunks excluded. Persistence occurs before generation | Optional independent public-query retrieval; results/count/latency only, no saved content. Existing freshness/online routing/sync tests; no new online speed claim |
| Host pressure | Resource report cached 5 s; CPU sampling and health checks cost time on misses. Thermal/voltage protections can reduce budgets or block inference | CPU/RAM/swap/load/temp/throttle samples before each voice request and after the run. This samples sustained trends, not every thermal peak |
| Safe overlap | Inference, chunk synthesis/preparation and playback already overlap. Routing and memory must precede grounded inference; online persistence is serial | Preserve overlap, timeouts, cancellation and queue capacities. Deferred persistence, eager Piper warm-up or parallel telemetry remain proposals until measured |

## Implemented changes and trade-offs

1. **Project six required RAG columns.** `values_list` replaces full joined model
   hydration. Eligibility, substring filtering, batch size 200, scoring, rounded
   scores, heap ordering and returned metadata are unchanged. Less SQL transfer
   and fewer Python objects; the same corpus scan and tokenization remain. No
   schema/cache/index migration, result cap or ranking approximation is added.
2. **Scalar model diagnostics.** Actual input sizes and an allowlisted completion
   reason supplement existing Ollama metrics. The service carries a bounded
   metric dictionary internally and in existing stage logs; the public API's
   metadata allowlist is unchanged. Finalization also enforces scalar types,
   finite floats and fixed completion labels at the service logging boundary.
   No prompts, history, documents, credentials,
   arbitrary backend reason strings or reasoning are added to diagnostics.
3. **Piper timing split.** Two monotonic readings around readiness and exchange
   expose startup versus request work without changing IPC, worker ownership,
   concurrency, cancellation or timeout behavior. One small INFO record per
   successful chunk; logging can be disabled normally. No extra threads.
4. **Disposable acceptance harness.** A single Python executable script verifies
   an exact approved SHA and clean tracked files on Linux, checks dependencies,
   unit states and application service probes, runs all discovered tests in a
   separate process, then migrates only a temporary SQLite database. It uses a
   temporary media root and disables device actions/display and online writes
   for benchmark requests. It does not modify `.env`, personal DB/media, services
   or models. Temporary connections close before cleanup, including on Windows.
   Linux test subprocesses run in a new owned session; interruption/timeout
   terminates and reaps that test group, including spawned synthetic workers.
   Projection equivalence is checked explicitly even under Python `-O`.

The configured model, prompt instructions, response budgets, RAG grounding,
memory semantics, MQTT safety, speech boundaries, ALSA behavior and TFT protocol
are preserved. No firmware, wiring or enclosure file changed.

## Measured Windows result

Environment: Windows 10 platform build 26200, Python 3.10.2, Django 5.2.17,
existing venv. These are host microbenchmarks, **not Raspberry Pi performance**.

Method: isolated on-disk SQLite database with one indexed synthetic document and
1000 identical 640-character chunks. Compare accepted joined
model-hydration traversal against the new six-column traversal, including
identical per-row JSON hashing in both paths. Warm both paths, then take nine
alternating-order measurements per path, batches of 200. Row digests match.
Timing excludes scoring and inference; it isolates the changed candidate path.

| Measurement | Baseline | Optimized | Interpretation |
|---|---:|---:|---|
| Candidate traversal median, main recorded run | 42.490 ms | 12.850 ms | 69.8% less time for this narrow synthetic operation |
| Full optimized lexical retrieval median | unavailable | 51.432 ms | Seven repeats; no full-retrieval improvement claim |
| Bounded memory retrieval median | unchanged implementation | 8.536 ms | Seven repeats, 600 synthetic USER messages; not a production conversation |

OS caching/scheduling and hashing affect absolute numbers. Independent initial
and later harness checks showed the same direction; use the recorded primary
run above for comparisons. A finalization repeat measured **52.858 ms baseline,
13.672 ms optimized** for the same narrow candidate traversal. This is additional
Windows evidence, not Pi evidence. Raw samples and environment are in
`system-performance-windows-results.json`; the latest complete test output is in
`system-performance-windows-tests.log`.

Tests:

- Before application changes: complete Windows suite, **1002 tests in 34.150 s,
  OK, one Linux-only skip**.
- Focused model streaming/single-model and new diagnostic/projection regressions:
  **77 tests in 1.069 s, OK** (before the additional Piper diagnostic test).
- After application changes, through the isolated acceptance harness: complete
  Windows suite, **1006 tests in 53.310 s, OK, one Linux-only skip**. Includes
  resource policy, synthetic streaming, Piper recovery, capture/ALSA mocks,
  serial disconnect tolerance, RAG, memory, online routing and cancellation.
- Additional harness run: synthetic retrieval completed, temporary database
  cleanup succeeded, incorrect expected SHA rejected with exit 2 before output
  creation. Python compilation and `git diff --check` passed.
- Finalization after scalar-boundary and test-worker cleanup review: **1010
  tests run, 1009 passed, one Linux-only skip, 38.525 s, exit 0**. Targeted
  RAG/performance/validation/single-model run: **83 passed in 0.949 s**. Three
  mocked Linux test-group cleanup cases cover normal completion, timeout and
  Ctrl+C. An additional diagnostic test rejects nonnumeric/private metric values.

Suite wall times vary and include fixtures/process startup; their difference is
not a runtime performance result. Eight regression tests were added: three prompt
diagnostic tests, three validation process-cleanup tests, one cross-batch
ranking/eligibility/projection test, and one real spawned synthetic-Piper
diagnostic privacy/reuse test. Linux's real nonblocking
pipe test must execute on the Pi, not remain skipped. Real Piper, ONNX, serial
and MQTT packages were absent from this Windows venv; synthetic coverage does
not establish those dependencies work physically.

## Pi execution from the published feature commit

Obtain the approved full feature-commit SHA independently. The script refuses
a mismatched SHA, a tracked dirty Linux checkout, or missing accepted history.
It does not fetch, switch, pull, download, commit, push, restart or reconfigure.
Do not use the baseline SHA as the feature SHA. Run from the checked-out repo:

```bash
EXPECTED_COMMIT='REPLACE_WITH_APPROVED_40_CHARACTER_FEATURE_COMMIT_SHA'
./venv/bin/python scripts/validate_pi_performance.py \
  --expected-commit "$EXPECTED_COMMIT" \
  --output "$HOME/companion-audit-$(date +%Y%m%d-%H%M%S)" \
  --rounds 2 --play-audio --online
```

This runs all tests first (600 s subprocess limit), then one initial LOCAL
voice question followed by two rounds of four distinct LOCAL questions covering
brief, normal and detailed policy. Each request gets a new disposable
conversation. Real Piper is required. `--play-audio` uses the configured ALSA
speaker; omit it to use ALSA's null sink. Null playback is explicitly labelled
and cannot establish audible continuity or physical playback timing. Neither
mode uses the microphone, STT, wake word or TFT; these need the checklist below.
First-speech timing is software submission, not measured acoustic onset.

`--online` sends one fixed public query only when the online provider is
configured and measures retrieval separately, without inference or persistence.
Omit it for no requested search. Existing read-only health/connectivity probes
can still contact configured endpoints during normal resource-policy evaluation.
Unit names can vary; a successful oneshot warmer can be inactive, and SearXNG
may be containerized. Inspect unit states together with application probe
status. The separate dashboard HTTP probe uses the shipped unit's
`127.0.0.1:8000` listener; the generic health helper's Django READY entry refers
only to the current Python process. A missing STT asset is reported but cannot
be validated by text injection.

No model is forcibly unloaded. Initial residency and load metrics determine
whether the initial request actually includes a cold load; warm rounds are
labels for later requests, not assumptions that cache hits occurred. If the
model was already resident, a genuine cold observation remains pending. Avoid
concurrent UI/voice inference when collecting comparisons.

The script writes `report.md`, scalar `report.json` and synthetic `tests.log` in
a **new** output directory. It checkpoints after each voice request and reports
failure type/stage without copying arbitrary exception strings. Inference
retains existing socket and audio timeouts; the known slow-trickle transport
deadline limitation is not fixed here. Ctrl+C enters production cleanup; during
regressions it also terminates the script-owned Linux test session. Default
two rounds are bounded in question count; up to five rounds allow a longer
sustained-load comparison. Samples before/after questions can miss thermal peaks.

For a valid end-to-end comparison, repeat identical approved question sets,
resource settings, model, sink mode and ambient conditions across commits.
Report individual samples and per-policy medians; compare answer completeness
alongside load/prefill/decode, first-unit delay and physical audio. The built-in
candidate projection A/B needs no baseline deployment or production corpus.

Windows-only harness verification used `--offline-only`, which suppresses all
real inference, audio, service and network checks. It permits a dirty checkout
for reviewing uncommitted work and records tracked differences; it cannot grant
Linux/Pi acceptance. The real Linux inference branch has not been executed here.
Production safety review found no write path to existing `.env`, knowledge
documents, firmware, hardware settings or systemd configuration. All benchmark
ORM writes/migrations use temporary SQLite/media roots. The read-only dashboard
HTTP probe observes the real service; it never submits a production conversation
or command. Tests/inference consume CPU/RAM and optional speaker playback uses
the configured device; schedule a quiet testing window. Do not run the harness
as root or while another voice process is using the audio device. No service is
stopped automatically. Synthetic Linux cleanup tests do not establish actual
Pi process/audio recovery, which remains an acceptance check.

Endpoint-specific security findings and isolated characterization are in
`system-end-to-end-audit.md`. Current live anonymous status checks returned 200
for the dashboard and data-bearing views, 400 for chat without an ID, 404 for
inactive setup and 403 for protected mutation-route OPTIONS. No private bodies,
inference or commands were requested. Existing P1 anonymous data access/inference blocks an untrusted-
network release, not publication of this branch for controlled testing. Actual
current Pi firewall/proxy rules, DEBUG/secret configuration, remote Git SHA and
Internet exposure are unverified; normal setup isolation is consistent with the
observed setup 404. No broad authentication rewrite is included.

## Human acceptance checklist

- From the actual microphone, ask distinct brief/normal/detailed questions in
  quiet and typical room noise. Confirm complete first/last words, useful factual
  answers and request cancellation; record perceived speech onset separately.
- Exercise repeated wake words, no-speech timeout, quiet and distant speech,
  ordinary conversations without wake words, speaker feedback and Ctrl+C. Confirm
  microphone ownership releases and a later request works.
- Listen through several detailed responses for stutter, clicks, unnatural
  pauses, clipped phonemes and incomplete endings; correlate software queue
  waits with audible gaps instead of assuming causation.
- Confirm accepted TFT READY/LISTENING/THINKING/SPEAKING transitions, QUERY,
  MODE, caption pages, speech-linked LEVEL and silence/recovery. Disconnect and
  reconnect USB once; audio should continue and display recover on later updates.
- Ask explicit local-document facts and personal recall in a deliberate test
  conversation; verify grounding and no unrelated/conversation-crossing memory.
  These manual requests will use the real application and its normal persistence.
- If device actions are configured, manually verify allowlisted LED/temperature
  success and unavailable-device response. No automatic actuator commands here.
- Check warm repeated requests under sustained load for temperature/voltage
  flags, declining throughput, swap pressure and appropriate policy reduction.

## Prioritized remaining work

- **P1 before untrusted-network exposure:** decide appliance authentication and
  conversation ownership; verify production DEBUG/secret/transport settings.
- **P1 acceptance:** execute the approved feature commit on Pi Python 3.13 and
  the full physical checklist; retain report and answer-quality observations.
- **P2 performance:** attribute first-speech delay with the new Piper and prompt
  counters, then compare measured first-unit buffering alternatives only if
  answer quality and clear playback remain accepted. No lower buffer threshold
  or changed token cap is silently deployed.
- **P2 reliability:** independently implement/test a total transport deadline and
  cancellation during slow model reads, including trickle/disconnect fixtures.
- **P2 scaling:** measure full production-corpus retrieval; consider SQLite FTS
  only with equivalent exact-term relevance, freshness, deterministic ties and
  migration/backout tests. No vector/background model is proposed by default.
- **P3 scheduling:** measure telemetry cache misses, online persistence and Piper
  cold-start frequency before adding overlap, eager loading or extra threads.
- **P3 observability:** if needed, measure RMS/UART callback CPU time and acoustic
  onset with device instrumentation; software pipe timestamps remain estimates.

## Files changed

| File | Purpose |
|---|---|
| `knowledge_base/retrieval.py` | Six-column candidate projection |
| `assistant/ai_engine/local.py` | Scalar prompt sizes and allowlisted done reason |
| `assistant/services.py` | Preserve/log bounded internal Ollama metrics |
| `assistant/voice/tts.py` | Readiness/reuse/exchange timing diagnostics |
| `assistant/tests_performance_diagnostics.py` | Prompt diagnostic correctness/privacy |
| `assistant/tests_performance_validation.py` | Owned test-group completion/timeout/interruption cleanup |
| `knowledge_base/tests_retrieval_projection.py` | Ranking/freshness/metadata/batch/SQL regression |
| `assistant/tests_voice.py` | Spawned worker timing/reuse/privacy regression |
| `scripts/validate_pi_performance.py` | Consolidated disposable validation executable |
| `docs/system-end-to-end-audit.md` | Component matrix and code findings |
| `docs/pi-performance-optimization.md` | Evidence, methodology, acceptance and remaining work |
| `docs/system-performance-windows-results.json` | Recorded Windows measurements |
| `docs/system-performance-windows-tests.log` | Complete post-change Windows test log |

The user explicitly authorized staging these files, committing and pushing only
`system-performance-audit-v1`. Main is not merged or reset, and production
deployment is not changed. Unrelated/protected artifacts are excluded from the
commit; their preservation was checked by hashes (547 files).
