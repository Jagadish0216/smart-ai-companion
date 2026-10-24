# Step 64: guarded Moonshine-only file benchmark

Baseline `f3fdf42679369242243e18c5a1cc5739d88ac3ee`, branch
`codex/offline-model-selection`. This change was verified with Windows mocks;
no Pi inference, installation, download, recording, playback or deployment ran.

## Behavior and scope

The observed Whisper-first trial completed but heated the Pi above the below-50 C
start gate. Refusing Moonshine at 51.6 C was expected safety behavior, not a failed
Moonshine inference. The new operator option `--run --engine moonshine` selects
exactly one Moonshine worker so it can be evaluated independently after cooling.
It neither requires Whisper paths nor launches/initializes a Whisper model.
Omit `--engine` for the original Whisper-then-Moonshine comparison and original
completion status. No automatic retry or cooling loop is added.

Operator selection is distinct from child execution: `--engine` accepts only
`moonshine`, while hidden `--worker-engine` accepts either worker implementation
and requires `--child-dir`. Mixed operator/child selectors and child `--run` are
rejected. The internal child protocol changed deliberately; operators should
never invoke hidden child flags to bypass parent guards. No `--run` still performs
the existing metadata-only compatibility check.

Production settings, provider factory, Whisper default/fallback, Qwen, VAD,
speaker profiles and audio routing are unchanged. Only the diagnostic script
has runtime changes. The candidate still requires package 0.1.5 and a distinct
isolated Python venv; file existence alone is not an integrity pass. All eight
English Small quantized_26_08_21 byte counts and CRC32C values must verify before
native Transcriber construction. No integrity bypass, downloader, microphone
helper, cloud adapter or new dependency is introduced.

All existing guards remain: initial CPU below 50 C, abort at observed 70 C,
512 MiB minimum available RAM, fail closed on missing required telemetry/current
power or throttle pressure, shared exclusive diagnostic lock, and sole resident
Qwen tag/full installed digest/context 4096 checks before, during and after
successful inference. No warm/reload/unload/restart or LLM generation is sent.
The worker deadline remains 120 seconds; polling sleep is 0.5 seconds plus bounded
telemetry/metadata operations. Only the owned fresh process group is killed/reaped.
Partial results/telemetry survive failure; there is no follow-on worker on failure.
This is sampled protection, not a hard realtime or kernel OOM guarantee.

The parent uses disposable database/media/cache state for existing resource
helpers. Child workers do not import Django or open production databases. Public
audio is copied to a temporary frozen snapshot; raw microphone data is not
exported or captured. Reports deliberately include the approved file's raw STT
recognition for accuracy review, so use only the existing public recording.
Python worker sockets are blocked and the native API uses local verified models;
this is not an OS network sandbox of arbitrary native code. Maintain the existing
offline setup; do not change network services to perform this diagnostic.

## Safe Pi update (operator only)

Run in the existing checkout. The branch/clean-tracked-file checks stop rather than
switching branches or overwriting local modifications. Untracked files are not
deleted; Git refuses a fast-forward if they obstruct it. No merge to main occurs.

```bash
cd "$HOME/apps/smart-ai-companion" &&
test "$(git branch --show-current)" = codex/offline-model-selection &&
git diff --quiet && git diff --cached --quiet &&
git fetch origin codex/offline-model-selection &&
git merge --ff-only origin/codex/offline-model-selection
```

Compare `git rev-parse HEAD` with the delivered SHA before the trial. If this is
not a fast-forward or tracked work exists, stop and review it; never force/reset.
Fetching code is separate from benchmark execution and downloads no models.

## One guarded Moonshine-only command (not executed here)

Allow the Pi to cool naturally below 50 C and confirm existing offline hardware/
power health. Qwen must already be resident at the matching tag/digest/context;
otherwise stop without warming it. Do not use the ordinary unguarded voice loop
or repeat the benchmark automatically. From the updated checkout:

```bash
./venv/bin/python scripts/compare_offline_stt.py --run --engine moonshine \
  --operator-approved-public-audio \
  --python "$HOME/moonshine-stt-venv/bin/python" \
  --moonshine-model "$HOME/.cache/moonshine_voice/download.moonshine.ai/model/small-streaming-en/quantized_26_08_21" \
  --audio "$HOME/mic-test-1m-whisper.wav" \
  --reference "Hello mic testing one two three this is smart AI companion" \
  --output "$HOME/moonshine-only-$(date +%Y%m%d-%H%M%S).json"
```

No Whisper model/binary argument is supplied. The existing file must be mono
16 kHz PCM16; stereo/native capture formats are refused, not silently mixed.
No new microphone or speaker operation is authorized by this command. The report
path must be new. Ctrl+C uses owned-worker cleanup; do not kill services/unload
Qwen. A refusal requires cause review and a new explicit operator decision, not
an unattended retry. Existing [benchmark methodology](offline-stt-audio-profile.md)
documents all shared timing, privacy and resource limitations.

## Report interpretation

| Field | Moonshine-only meaning |
| --- | --- |
| `run_mode` / `requested_engines` | `moonshine_only` / `["moonshine"]`, including on refusal. |
| Successful top-level `status` | `moonshine_complete_quality_pending`, never `comparison_complete_quality_pending`. Completion is not quality acceptance. |
| `samples` | At most one worker sample; an aborted/refused trial may have none. `result` contains raw final recognition, WER/S/D/I, timing, CPU, RSS and model fingerprints when available. |
| Historical reference | `historical_whisper_reference` is explicitly operator-reported, non-contemporaneous and not rerun. WER 45.45%, final text 3.55 s and peak 54.5 C are rounded supplied measurements; matching normalization/audio fingerprints were not independently verified. |
| Same-run default | `two_engine_comparison`, both requested engines and the existing `comparison_complete_quality_pending` status; no embedded historical-reference field. |
| Failure | `aborted` with safety/worker reason, available partial child result and telemetry. No retries or follow-on worker. Unknown/missing measurements remain unavailable. |

WER is calculated from the final text using the existing lowercase ASCII/punctuation
normalization and digit 0–10 word mapping. Inspect substitutions/deletions/insertions
and semantic mistakes as well as the ratio; one recording is not representative
quality acceptance. Do not claim improvement over 45.45% as a controlled same-run
comparison without verified identical recording/scoring conditions.

`model_load_seconds` measures Moonshine native construction, separately from
`transcription_seconds` (fast feeding/finalization). `end_to_final_text_seconds`
starts at worker entry, includes import/setup/fingerprinting/integrity/preprocessing,
and ends at final recognition readiness. These are file-processing measurements,
**not live speech endpoint latency**. No interim transcript dispatch is introduced.
Worker wall time also includes scoring/cleanup. File caches and previous runs can
affect timing; do not subtract unrelated durations to invent a loading measurement.

`peak_sampled_temperature_c`, `min_available_ram_bytes` and `peak_host_cpu_percent`
summarize sampled host telemetry. CPU seconds are process plus native-child CPU;
peak RSS is the largest single-process peak, not a summed Qwen+STT working set.
Qwen's full digest is reported and matching residency is verified throughout the
guard path; no Qwen request is used as a residency test. Sampled temperature may
miss between-sample peaks; inspect the full telemetry and guard reason. Historical
54.5 C is not the Moonshine trial's temperature or a prediction of its thermal load.

## Windows verification and rollback

Final Windows results: **56 focused tests, OK (0.857 s)**; full suite
**1246 tests, OK, one platform skip (40.083 s)**. These suite runtimes are
not Pi performance measurements. New deterministic tests cover single-only
scheduling, unchanged default order/status, CLI validation/internal worker routing,
temperature/RAM/missing-data refusals, Qwen mismatch, no retry/partial reports,
integrity failure before native loading, no factory access and unchanged production
selection/context/keep-alive. Existing tests cover CRC32C/size, timeouts/thermal
monitoring, process ownership, raw text scoring and offline behavior.

Files changed: `scripts/compare_offline_stt.py`, `assistant/tests_moonshine_only.py`,
this document, `docs/moonshine-only-focused-tests.log`,
`docs/moonshine-only-windows-tests.log`.

Rollback needs no configuration change: omit `--engine moonshine` to retain the
old two-engine workflow, or leave the diagnostic unused. A reviewed code revert
is preferable to resetting a shared checkout. Production factory/configuration,
`.env`, data, services, model residency and hardware require no rollback.


Commands (test TEMP/TMP redirected to workspace `.test-tmp`):

```powershell
venv/Scripts/python.exe manage.py test assistant.tests_moonshine_only assistant.tests_stt_comparison_audio_profile assistant.tests_offline_voice_validation --noinput
venv/Scripts/python.exe manage.py test --noinput
```

CLI help was also inspected without inference. The tracked runtime diff contains
only `scripts/compare_offline_stt.py`; configuration and factory files are unchanged.
