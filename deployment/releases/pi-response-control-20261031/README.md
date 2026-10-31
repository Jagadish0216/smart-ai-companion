# Pi response control, selective fallback and latency — 31 October 2026

This is a **release-artifact branch**, not a branch to merge/pull into production.
GitHub does not contain the Pi's recorded HEAD. The release diff is built against
the authoritative 469-file snapshot, with its uncommitted r2/provider changes.
The Windows application branch was neither used as the implementation base nor
overwritten. A pinned Git fetch/archive procedure is supplied with this release.

Pi provenance: `feature/adaptive-online-retrieval`, HEAD
`645a066adf49e42ab0d16f0cd6195cd14ab16ed0`, snapshot created
`2026-10-31T05:07:45.014190+00:00`. Every supplied source hash was verified.
`manifest.json` guards the exact affected pre-images, absent new-file paths,
and all 459 unchanged source files. No clean working tree is required.

## Coordinated implementation

1. Physical speech stop: the existing InterruptControl polls the existing
   TalkButton handle during output. It does not claim GPIO, open UART, capture
   audio or create another player. Recording/release processing stay unchanged.
   A fresh stable 20 ms press cancels output; a press held before output requires
   release first. The press signals the existing player before shutting down
   the model HTTP stream. The shared cancellation event stops pending Piper
   work and bounded queues; existing owners reap their processes/threads before
   another capture. Abort callbacks are idempotent. Late callbacks are cancelled
   immediately, and failed cleanup refuses the next interaction. READY is the
   existing firmware's idle state; the TFT gets an interrupted/hold-button label.

2. Local-first: ordinary general knowledge makes one normal local Gemma pass.
   An explicit epistemic refusal or engine failure before any answer is delivered
   can make one online fallback. A small leading-refusal gate holds only a
   possible refusal; ordinary factual prose streams immediately. Partial answers
   are never silently replaced by another answer. Explicit source-verification
   requests with objectively missing local evidence may retrieve immediately.
   Confidence scores are not used. Private/document requests and explicit
   offline mode cannot use this fallback. Current-information freshness rules,
   validated actions and document sessions remain in place.

   On a failed officeholder lookup, existing saved evidence retains priority.
   Otherwise the same local model may supply **last-known background**, prefixed
   with “not live-verified; officeholders can change.” That is not verification
   of who currently holds office. Unsupported URLs/citations/as-of dates and
   verification claims are discarded; unknown answers remain unknown. Weather,
   prices and scores do not get invented parametric live answers. Strict
   `online_only` requests and conflicting evidence continue to abstain.

3. Latency: normal system identity is compact; full Batch 6/university/Pi details
   are added for introduction questions. The normal model, context limit, resource
   caps and voice length budgets are preserved. The first prepared complete unit
   no longer waits up to 0.8 seconds for a second unit. The same bounded queues
   overlap subsequent preparation and playback. Prompt preparation now has explicit
   trace spans; existing route, Ollama TTFT, Piper exchange/synthesis and speech
   PCM markers remain content-free. Delivery context propagates into existing
   audio workers even with tracing disabled. Trace schema names were updated.

All provider modules/factory, credential settings, RAG/online persistence, GPIO
configuration, UART/display owners, service units, ESP32 firmware and model
configuration are unchanged. The r2 synthetic-worker harness fix is preserved.
The existing Gemini implementation remains present, with its original dispatch
limitations; this release does not switch or rewrite provider selection.

## Tests and measurement evidence

The complete snapshot suite was first run before editing to identify missing
assets and host limitations. Release verification discovers the complete suite,
excluding only ten tests whose systemd/polkit/environment-example/firmware/library
assets are absent from the ZIP. On Windows one POSIX deployment-shell fixture
is additionally unavailable, and the existing Unix-pipe test has its native skip.
Those two POSIX tests run on Linux where supported. The exact exclusions are in
`unavailable-tests.json`; the physical-button/cancellation/Piper tests are included.

New regression coverage includes simultaneous button cancellation of playback,
pending synthesis and generation; fresh-press races; idempotent/late cleanup;
context propagation; local-first generation; bounded failure/refusal fallback;
private/offline isolation; one persisted user message; partial-stream behavior;
qualified officeholder background; and early first-unit launch. Existing
retrieval-only matrices now request explicit online-only mode so they still
assert strict source failure/conflict behavior. A dated cache-retry fixture was
made fresh in both its publication and content dates; production TTL rules were
not relaxed. The legacy resource-notice mock includes the request's new fields.

The authoritative final counts/status are in `reports/test-report.json`, with
installer/rollback verification in `reports/verification.json`. Validation uses
the existing venv, in-memory SQLite, disposable output outside the source tree,
blocked sockets and guards against escaped hardware/service commands. It installs
nothing. Windows tests use a writable non-OneDrive temporary area because atomic
report-file renames intermittently failed inside synced/sandbox temporary paths.
No production implementation or timeout was altered to hide those host errors.

Measured synthetic benchmark (`reports/latency-before.json` and
`reports/latency-after.json`): a model stream deliberately stalls after the first
sentence until output starts. Three samples measured the removed prebuffer on
the critical path, approximately **824 ms → 3.5 ms median first output call**.
Normal voice system content was **963 → 557 characters**. This is a mocked
pipeline result, not Pi/Gemma/Piper speed, PCM device latency or audible onset.
Prompt-building CPU timings are recorded too; no CPU-builder speedup is claimed.

Real Ollama TTFT, real Piper synthesis and acoustic onset were not measured
offline. `report_trace.py TRACE_DIRECTORY` summarizes actual opt-in production
traces for routing, prompt preparation, TTFT, synthesis, first unit and PCM
acceptance. Missing values remain null. Audible onset requires synchronized
acoustic measurement; a pipe write is not proof of sound. Existing trace opt-in
is `--latency-trace-dir`; this release does not edit the service unit to enable it
or launch another voice loop alongside the running service.

Earlier first-unit playback may reveal inter-unit gaps on a slow Pi; existing
starvation metrics and bounded queues allow that tradeoff to be measured. Model
quality is not benchmarked by synthetic tests. Live button/TFT/audio acceptance
and the Pi's full Linux regression gate remain necessary before acceptance.

## Deployment contract

Do not run `git pull`, switch the Pi branch, cherry-pick onto the dirty tree,
reset/clean, force-apply, or replace whole source files. Fetch a **pinned release
commit**, archive only `deployment/releases/pi-response-control-20261031`, and
run `deploy.py check` against the current Pi source. Any drift fails closed.
The release branch's application root follows GitHub main and is not the Pi
application; only the checked release diff is a deployment payload.

`deploy.py validate --stage NEW_DIRECTORY` copies only reviewed source outside
production, applies the diff there, runs the complete applicable suite/Django
checks, verifies all post-images and creates a success marker. It does not copy
.env, databases, private knowledge, media or Git metadata. No source changes,
service operations or hardware actions run against production during validation.

Only in an approved maintenance window, stop the existing voice and web units,
then run `deploy.py apply --stage VALIDATED_DIRECTORY --backup NEW_BACKUP` and
start those two units again. A stopped voice service releases the sole GPIO,
UART and audio owners before new code loads. The apply command saves affected
original source and the reverse diff privately, validates current hashes again,
and applies only the atomic Git diff. No migrations or package/model installs
are required. Ollama, STT, Mosquitto and network-mode are not manually restarted.

Rollback stops the same two units and runs `deploy.py rollback --backup BACKUP`,
then starts them again. It checks exact affected post-images and refuses later
edits, restoring the original r2 working-tree bytes and all its uncommitted work.
The manager itself never stops/starts services or changes networking. No hotspot,
reboot, firmware or model-replacement command is part of this procedure.

The pinned, copyable preflight/backup/validation/restart/rollback commands are in
the accompanying `PI_RESPONSE_CONTROL_DEPLOYMENT.md` report. `audit_pi.py` supplies
read-only installed-unit/listener/environment/UART/capability information without
dumping secrets. Review installed unit dependencies and the configured Gemma model
before applying; source snapshots cannot prove live process or hardware state.
