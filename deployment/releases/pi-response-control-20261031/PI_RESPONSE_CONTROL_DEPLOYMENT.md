# Smart AI Companion — coordinated Pi update

Prepared from the live-after-r2 snapshot created 31 October 2026. All 469 hashes
were verified. The Windows application tree and both running Pi services were
not changed by this work. No hardware, network or model operation was performed.

Release commit: `5faba735640fd294bec28b88aa824dfbc8a989bc`  
Branch: `codex/pi-response-control-20261031`  
Review: https://github.com/Jagadish0216/smart-ai-companion/tree/5faba735640fd294bec28b88aa824dfbc8a989bc/deployment/releases/pi-response-control-20261031

**Do not git pull this branch into production.** The Pi's HEAD
`645a066adf49e42ab0d16f0cd6195cd14ab16ed0` is not present on GitHub. The branch
records a tested release diff and tools; its application root follows GitHub's
older main. Compatibility is established with source hashes and a clean staged
application of the diff, not with merge ancestry.

## Delivered behavior

- The already-owned physical button cancels speech through InterruptControl:
  the player is signalled first, queued synthesis is discarded, pending Piper
  work is cancelled and the model HTTP stream is shut down where supported.
  Existing owners finish bounded cleanup before another capture. Fresh button
  press/release behavior prevents an interrupt press becoming a recording.
  READY is the firmware's existing idle state; TFT feedback is updated.
- General knowledge gets a local Gemma pass; only explicit freshness/evidence
  requirements, an objective local evidence gap, an initial epistemic refusal
  or a generation failure can trigger bounded online fallback. Private/RAG/
  document/offline paths and validated actions retain their constraints. Failed
  officeholder lookups may use clearly qualified last-known local knowledge;
  unavailable weather/prices/scores are not fabricated. Partial spoken answers
  are never replaced by a second answer. Provider selection and modules are
  unchanged, including the snapshot's original Gemini dispatch limitations.
- Compact normal identity, full project introductions, immediate first-unit
  launch instead of the fixed second-unit wait, and explicit prompt-preparation
  trace spans. Existing routing/TTFT/Piper/PCM metrics remain content-free.

Changed source: scripts/voice_loop.py; assistant/services.py, local_first.py,
identity.py, latency_trace.py, voice/interruption.py and ai_engine/local.py;
the trace schema; targeted/new tests and three existing fixture adjustments.
All 459 other snapshot files, provider modules, r2 worker-harness fixes,
configuration, firmware and service-unit configuration are preserved.

## Evidence

- Complete applicable discovery: **1,682 tests**, 1,681 passed, one native Unix
  pipe skip on Windows, zero failures/errors. Nineteen new regressions are included.
- Ten tests require absent snapshot assets. One POSIX deployment-shell fixture
  is unavailable on Windows; it runs on Linux. The exact list and complete
  results are versioned. These are not silently reported as passes.
- Django checks, hash compatibility, forward patch application and exact reverse
  rollback passed. Stale source/provider edits and new-file collisions were
  rejected. Original pre-image hashes were restored after rehearsal.
- Synthetic stalled-stream first-output median: about **824 ms → 3.5 ms**;
  normal voice system text: **963 → 557 characters**. This measures mocked
  pipeline behavior, not a Pi speech-speed improvement. Real Pi routing,
  Gemma TTFT, Piper timing and audible onset still require live measurement.
  report_trace.py summarizes actual opt-in traces; missing acoustic onset remains
  unknown. Faster first launch can expose later gaps on a slow Pi; existing
  starvation metrics remain available. No model-quality improvement is claimed.

No production service restart or source deployment has been performed. The
following is the single Git-based operator procedure; approval is still required
before its maintenance section. Keep the same Pi terminal for its variables.

## 1. Preflight, private rollback records, and staged validation

Run on the Pi as jagadish. This fetches objects without changing branches or
tracked source. It archives only the pinned release directory. No production
configuration or provider key is copied into the disposable test stage.

```bash
set -euo pipefail
umask 077
repo=/home/jagadish/apps/smart-ai-companion
revision=5faba735640fd294bec28b88aa824dfbc8a989bc
work=/tmp/pi-response-control-20261031
stage=/tmp/pi-response-control-stage-20261031
backup=/home/jagadish/pi-response-control-backup-20261031
release_path=deployment/releases/pi-response-control-20261031

test ! -e "$work"
test ! -e "$stage"
test ! -e "$backup"
install -d -m 700 "$work"
git -C "$repo" fetch --no-tags https://github.com/Jagadish0216/smart-ai-companion.git "$revision"
test "$(git -C "$repo" rev-parse FETCH_HEAD)" = "$revision"
git -C "$repo" archive --format=tar "$revision" "$release_path" | tar -xf - -C "$work"
package="$work/$release_path"

# Private records only; do not print or upload the diff/environment files.
git -C "$repo" status --porcelain=v1 > "$work/pre-existing-status.txt"
git -C "$repo" diff --binary HEAD > "$work/pre-existing-work.patch"
python3 "$package/deploy.py" check --project "$repo"
systemctl is-active --quiet smart-ai-companion.service smart-ai-companion-voice.service
systemctl show smart-ai-companion.service smart-ai-companion-voice.service   -p After -p Requires -p FragmentPath -p DropInPaths
python3 "$package/audit_pi.py" > "$work/read-only-preflight.txt"
python3 "$package/deploy.py" validate --project "$repo"   --stage "$stage" --python "$repo/venv/bin/python"
```

Review read-only-preflight.txt: the effective voice model must still be
`gemma4:e2b-it-q4_K_M`, required services/dependencies must be present, and no
unexpected UART owner should exist. Process startup environments can omit values
loaded later from dotenv; absent values need inspection, not configuration edits.
The validator requires all applicable tests to pass before writing its marker.
On Linux the actual Unix pipe and POSIX shell fixtures run. Stop on a compatibility,
validation or audit discrepancy; do not force the patch or delete old stages.

## 2. Approved maintenance window: apply and restart only web/voice

Do this only after approval and successful validation. No other service is
manually restarted. Existing units/providers own warm-up and dependencies.
No GPIO/UART/audio owner is started in parallel with the old voice loop.

```bash
set -euo pipefail
sudo systemctl stop smart-ai-companion-voice.service smart-ai-companion.service
if ! python3 "$package/deploy.py" apply --project "$repo"     --stage "$stage" --backup "$backup"; then
  sudo systemctl start smart-ai-companion.service smart-ai-companion-voice.service
  echo 'Application failed; do not force or retry the patch.' >&2
  exit 1
fi
cp -p "$work/pre-existing-status.txt" "$work/pre-existing-work.patch" "$backup/"
sudo systemctl start smart-ai-companion.service smart-ai-companion-voice.service
systemctl is-active --quiet smart-ai-companion.service smart-ai-companion-voice.service
python3 "$package/audit_pi.py" > "$work/post-deployment-audit.txt"
```

The new private backup contains original affected source, the exact reverse diff,
manifest and the previous tracked-work diff. No database migration, restore,
model replacement or environment edit is required; database/media/private
knowledge remain untouched. The Pi stays on its existing branch with its prior
modifications plus this diff. If start/health checks fail, use rollback below.
Do not start a second voice CLI to test while its service is active.

Live acceptance after the approved reload: hold/release to ask a question,
short-press during speech, verify immediate output stop then READY/TFT feedback,
release and ask the next question. Check a long response with queued Piper work,
project/document privacy, ordinary local knowledge, explicit offline requests,
current facts, provider failure and qualified officeholder fallback. Actual
speaker onset cannot be established by API or pipe-write timestamps alone.

## 3. Rollback in an approved maintenance window

Use the same package/backup, or restore the variables above in a new terminal.
This refuses later edits to affected files rather than overwriting them.

```bash
set -euo pipefail
sudo systemctl stop smart-ai-companion-voice.service smart-ai-companion.service
python3 "$package/deploy.py" rollback --project "$repo" --backup "$backup"
sudo systemctl start smart-ai-companion.service smart-ai-companion-voice.service
systemctl is-active --quiet smart-ai-companion.service smart-ai-companion-voice.service
```

No reset, clean, branch switch, merge, force push, hotspot activation, network-mode
restart, reboot or ESP32 firmware change is used. Rollback restores the original
r2 source and preserves prior uncommitted work. If its hash checks detect later
operator edits, stop; never force restoration over those changes.
