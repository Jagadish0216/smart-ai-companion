# Final physical integration release

Source parent: `b74d07c8b80bfd55e36b9fdc9eb2deede0c7c6e5`.
Required Pi baseline: `0c795b125e704463d7ca77cbaf9807d6996319c1`.
Branch: `codex/final-physical-integration`. No schema or firmware changes.

## Architecture and TFT finding

The systemd voice unit runs `scripts/voice_loop.py --push-to-talk --responsive --voice-refinements`, with the existing repository `.env` and `/etc/smart-ai-companion/demo.env`. The loop resolves persistent indexed documents through AssistantService and HardwareDocumentScope, streams Qwen through LocalLLMEngine, and passes complete speech units to Piper and the configured audio backend. Display query/mode updates follow stream routing; captions use the actual speech presentation units. THINKING precedes generation, SPEAKING accompanies playback, and normal completion returns to READY (thermal ECO/PROTECTIVE states remain supported).

The previous acceptance script explicitly disabled the display and passed no instance to the voice loop. Successful terminal text therefore could not clear an existing TFT ERROR. In the checked-in ESP32 V2.2 source, ERROR defaults to "Let's try again"; every STATE command resets status text, and READY clears query/mode. A protocol-level firmware model reproduces the disabled-display failure and verifies that a wired THINKING/query/KNOWLEDGE/SPEAKING/caption/READY lifecycle clears it. This is a supported explanation of the previous test's gap, not a diagnosis of the live board's firmware or USB connection.

The Python bridge now uses a bounded background queue in the actual voice service. Audio/capture callbacks enqueue updates without serial waits; level updates coalesce and overflow/reconnect replays the latest desired state. The POSIX transport holds a per-user device lock and requests serial exclusivity. One worker reads V2.2 acknowledgments. HELP confirms supported commands, not firmware identity or pixels. Legacy acknowledgments have no sequence IDs, so late replies after timeouts cannot prove exact delivery; reports mark that assurance PARTIAL. Production capture excludes command text. The acceptance report deliberately includes query/caption text and must be treated as private.

Firmware limits remain ASCII status 40 bytes, query 80 bytes, newline framing, 96-byte input buffer, bounded serial parsing. Firmware is unchanged and must not be flashed as part of deployment. A live board observation remains necessary to verify rendering, timing and the physical connection.

## Performance evidence

The user's live baseline used `covid-19-pandemic.md`, document 32, existing `qwen3.5:4b-q4_K_M`, and a requested 64-token budget:

| Metric | Turn 1 | Turn 2 |
|---|---:|---:|
| Document resolution | 5.94 ms | 11.11 ms |
| SQLite retrieval | 1.56 ms | 1.64 ms |
| Prompt evaluation | 16.94 s | 15.20 s |
| Generation | 17.95 s | 19.69 s |
| First playback launch | 29.24 s | 30.68 s |
| Total cycle | 44.56 s | 46.72 s |

These are user-reported measurements, not runs performed for this release. Prompt evaluation plus decode totals 34.89 seconds in both turns. Retrieval optimization cannot explain that delay. Requested output budget is not actual output token count. No throughput or thermal cause is inferred without actual counters. At 69.2 C, `0x80000` records a historical soft-temperature event; it does not establish current throttling.

Existing Qwen think=false, context 4096, residency policy, relevant chunk selection and CPU options are preserved. New content-free telemetry records actual request controls, hidden-thinking character count (never its content), actual prompt/output/cache counters and load/evaluation duration where Ollama provides them. The hardware tool adds /api/ps residency, Ollama version, available RAM, load averages, process niceness, temperature and individually decoded current/historical throttle flags. Existing trace events capture first token, Piper readiness, playback launch, buffering and complete cycle. These distinguish prefill, decode, load, CPU interference and audio scheduling without guessing. Cache counters absent from the backend remain unknown. /api/ps and load samples are observations, not a full scheduler profile.

The supported optimization here removes synchronous serial work from audio callbacks. Protocol tests verify that isolation; no Pi speedup is claimed. The continuation prompt now requests the next supplied section directly instead of inviting a permission-only answer; existing cursor/grounding logic remains intact. Model/context/token budget, power limits and hardware are unchanged. Live runtime tuning and a comparable before/after remain pending SSH access.

## Safe deployment

Copy the reviewed `scripts/deploy_final_physical_integration.sh` outside the live checkout. Set RELEASE_SHA to the exact reviewed source commit, then run that external file on the Pi:

```bash
RELEASE_SHA=<exact-reviewed-40-character-SHA> bash /tmp/deploy_final_physical_integration.sh
```

The script refuses a different Pi HEAD, tracked edits, a release with a different direct parent, unexpected changed paths or differing baseline blobs. It fetches only the integration branch and tests an exported staging checkout with disposable test DB/media before stopping writers. It takes the existing knowledge maintenance lock, records active application services, creates a real SQLite backup with integrity checks and hash manifests for uploads/knowledge/config, then cherry-picks the reviewed source. Existing migrations must already be applied: knowledge 0005/0006/0007 and conversations 0002/0003. No migrations are run; unexpected pending migrations fail closed. No reindex, refresh, model pull or configuration edits occur.

Failure after application triggers code revert, static rebuild and restoration of previously active services. Database, evidence, accounts, hardware scope and uploads are preserved; automatic rollback never overwrites SQLite with an older backup. Backup paths and the resulting Pi cherry-pick SHA are printed. For later manual code rollback: stop services recorded in the backup's active-services.txt, revert its deployed.sha, collectstatic, restart those services. Stop if writers cannot be stopped or code revert fails. The backup is for explicit disaster recovery, not routine rollback over newer conversations. Keep untracked knowledge and lock files.

## Real microphone-free acceptance

Use the real file entrypoint, after deployment, from the existing Pi virtual environment. Do not run Piper from stdin. Existing display configuration must already be enabled and correct. `fuser` must be available to establish exclusive handover; unknown owners are never killed. Only previously active named companion services are stopped/restored. Extra display-owning companion units can be named with --display-owner-unit.

```bash
venv/bin/python scripts/accept_integrated_hardware.py \
  --question 'Summarize covid-19-pandemic.md' \
  --question 'Continue explaining that document' \
  --expect-file covid-19-pandemic.md \
  --manage-services --timeout 600 \
  --confirm-visual --confirm-answers \
  --output /tmp/final-hardware-run-1.json
```

The tool opens production SQLite read-only, takes a consistent backup, verifies integrity and row counts for documents/chunks/evidence/conversations/messages/accounts, and uses only the private snapshot and a unique scope key. No migrations or production writes occur. Indexed chunks are used without touching original uploads. Real Qwen/Piper/speaker/display are required. Online retrieval/persistence is disabled only inside the disposable offline worker. Each response records source IDs/pages/chunks, response text, latency, runtime and display evidence. SIGTERM/interrupt/timeout terminate the worker process group before restoration; restoration defers further terminal signals. The previous service states are checked afterward. Reports are new private files under /tmp; private snapshots are removed.

Expected output is `PASS: /tmp/...` (exit 0) only when all automated checks and requested operator observations pass. Without visual and answer/acoustic confirmation, expect `PARTIAL` (exit 2). Failure is exit 1. JSON separates RAG, Qwen, playback handoff, serial writes, firmware ACKs, lifecycle, visible rendering, answer review, microphone and service restoration. Playback launch is an audio onset proxy, not measured sound. Operator observations are labeled as such. Microphone/Whisper remain PARTIAL even when text injection passes.

Repeat with another new /tmp path and `--baseline /tmp/final-hardware-run-1.json` for a comparison. Matching question/model/context/data/citations/runtime controls are required; synthetic or mismatched runs are reference-only. Compare actual prompt/cache/load/eval counts, first-token and playback/cycle times, /api/ps, RAM/load/temperature before making tuning decisions. A pair of runs alone does not prove causation. Use the same questions and retained source on the original release to establish a genuine before/after; two runs of the new release measure repeatability only.

## Validation and remaining limits

Validation: 1,650 isolated regression tests passed (one skipped); migration drift and JavaScript syntax checks passed, and real headless Chrome acceptance passed. These checks cover PDF upload/index/selection, scoped follow-ups, citation history, online/offline controls, new chat, login, responsive rendering and browser voice. Existing persistent offline evidence, named discovery, missing/ambiguous files, reindex invalidation and hardware scope regressions remain included. Added tests cover firmware contract/ACKs, disabled-display reproduction, nonblocking bounded queue, reconnect, snapshot invariants, historical throttle decoding, incomplete-run failure, telemetry privacy and service restoration after timeout/interruption.

Live SSH rejected authentication during this task. No Pi deployment, real serial ownership/firmware/rendering, real Qwen speedup, audible Piper playback, physical microphone/Whisper or acoustic onset has been verified for this release. Run the file-backed acceptance before microphone acceptance. The hardware definition of done remains pending those device observations.
