# Final physical integration release

Source parent: `b74d07c8b80bfd55e36b9fdc9eb2deede0c7c6e5`.
Required Pi baseline: `0c795b125e704463d7ca77cbaf9807d6996319c1`.
Branch: `codex/fix-display-fixture-lifecycle`. This corrected replacement retains the same exact source parent and all runtime changes from candidate `9c7de863ea8d187c3f23b9f1b5f2cff8fbdd6774`. The original candidate branch is retained. No schema or firmware changes.

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

Validation for the corrected candidate: 1,652 isolated regression tests passed (one skipped); migration drift and JavaScript syntax checks passed, and real headless Chrome acceptance passed. These checks cover PDF upload/index/selection, scoped follow-ups, citation history, online/offline controls, new chat, login, responsive rendering and browser voice. Existing persistent offline evidence, named discovery, missing/ambiguous files, reindex invalidation and hardware scope regressions remain included. Added tests cover firmware contract/ACKs, disabled-display reproduction, nonblocking bounded queue, reconnect, snapshot invariants, historical throttle decoding, incomplete-run failure, telemetry privacy and service restoration after timeout/interruption.

Live SSH rejected authentication during this task. No Pi deployment, real serial ownership/firmware/rendering, real Qwen speedup, audible Piper playback, physical microphone/Whisper or acoustic onset has been verified for this release. Run the file-backed acceptance before microphone acceptance. The hardware definition of done remains pending those device observations.


## Display fixture regression correction

The Pi reported 1,651 tests with 3 failures and 8 errors on candidate 9c7de86. Inspection found `serial_interaction_probe()` registering display close and serial-patch restoration with method-level `addCleanup()`. Repeated subtests therefore left the first controller's ownership file locked, preventing later controllers on the same simulated port from connecting. Each patch also remained nested until the end of the method.

Before editing, the 28 lifecycle tests were run on Windows/Python 3.10 with only the display module's OS object reporting POSIX and an `fcntl.flock` adapter using real nonblocking Windows byte-range locks. Lock files were private temporary files; serial, inference and audio stayed mocked. This exercised the unchanged production ownership branch without globally changing os.name. It reproduced exactly 3 failures and 8 errors. Pi tracebacks were not supplied, so this matches the entire reported count and suspected test-class pattern; native Pi rerun/logs must confirm the exact device-side failure identities. The reproduced cases were:

- query lifecycle: text and hands-free cases failed after normal captured input held the lock;
- route labels: all eight cases following local raised IndexError because no commands were delivered;
- direct response: local_rag following online failed because MODE KNOWLEDGE was absent.

The corrected probe is a context manager. Every caller scopes it inside its subtest; `finally` closes the controller before the serial patch is restored, including assertion/exception exits. Original protocol, route, caption ordering and playback assertions are retained. One regression method additionally verifies close-once, ownership release, patch restoration and successful reuse after an exceptional fixture body. No production transport, exclusivity, queue, model, RAG, voice runtime, configuration, migration or firmware code differs from 9c7de86.

The same OS-backed-lock diagnostic now passes all 52 display tests, as does the ordinary focused display run. This is a protocol/fixture reproduction, not native Linux or Pi hardware acceptance. Correction validation completed on Windows, Python 3.10.2, Django 5.2.17: focused display 52/52; OS-backed-lock display diagnostic 52/52; complete regression 1,652 tests, zero failures/errors, one skip. The complete suite includes existing physical-document RAG, persistent offline evidence, voice/streaming, display and integrated hardware protocol tests. Django system checks and migration drift checks passed; no migrations were added or changed. Native Pi Python 3.13 remains unverified.

Deployment review: the corrected candidate is intentionally one replacement release commit directly on b74d07c, preserving the original script's exact single-parent rule. The script fetches codex/fix-display-fixture-lifecycle and adds only assistant/tests_display.py to the existing allowlist. The exact Pi baseline, reviewed RELEASE_SHA, changed-path baseline blob equality, patch applicability, protected migration bytes, disposable staging regressions, maintenance lock, SQLite/upload backup, zero-migration policy, service restoration and code-only revert safeguards remain enforced. Do not use the old script copy: its branch/allowlist would reject this candidate. No deployment was run.

Remaining Pi verification: run the focused display suite and complete suite with Python 3.13 on an isolated exported source tree using config.test_settings. Then run Django/migration drift checks in that isolated tree. Verify the existing deployment preflight against untouched Pi 0c795b1 before separately authorizing deployment. Real /dev/ttyAMA0 ownership, asynchronous TFT lifecycle/ACKs/rendering, Qwen/Piper/speaker and microphone acceptance remain device checks; no UART console, systemd, .env, firmware or model changes are required by this fixture correction.


The extracted deployment preflight was also executed in a disposable local Git clone. It accepted the corrected candidate on an equivalent b74d07c source tree, and rejected (1) a commit with 9c7de86 as its direct parent, (2) an unexpected release path, and (3) baseline drift in assistant/services.py. Each check left the fixture checkout unchanged. Bash syntax passed. The actual Pi 0c795b1 object/tree is not present locally; actual Pi baseline compatibility remains enforced by the unchanged exact HEAD and blob checks when the separately authorized deployment script is run. No backup, cherry-pick, rollback or service commands were executed against production during this review; backup helper regressions passed in the full isolated suite.

For native Pi verification, run from a disposable export of this reviewed candidate (not the production checkout), with the existing Pi virtual environment interpreter:

```bash
/path/to/existing/venv/bin/python manage.py test assistant.tests_display --settings=config.test_settings --noinput
/path/to/existing/venv/bin/python manage.py test --settings=config.test_settings --noinput
/path/to/existing/venv/bin/python manage.py check --settings=config.test_settings
/path/to/existing/venv/bin/python manage.py makemigrations --check --dry-run --settings=config.test_settings
```

Expected: 52 focused display tests and 1,652 complete tests with zero failures/errors (platform-dependent skips may differ), system check without issues, and no migration changes. These commands create disposable test databases and do not constitute hardware acceptance or authorize deployment.
