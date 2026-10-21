# Offline companion final release candidate

Starting revision: `7898c29aae15ef6619ff37357d3aee15d5ca1575`.
Feature branch only: `codex/offline-model-selection`. Hardware acceptance pending.

## Implementation

The previous ready lifecycle rejected 300 ms of RMS >= 0.02 even while draining
startup PCM. The supplied 15/15 and 24/29 above-threshold frame observations show
why that policy refused before calibration. They do not establish an electrical
or acoustic cause. Calibrating on the initial 0.06658 background produced the
reported 0.16845 onset threshold, above the observed 0.07044 waiting maximum.

`VOICE_CAPTURE_PREPARATION_POLICY=settled_fan` enables a reversible production
capture policy and implies the existing ready lifecycle. Default `legacy`
retains both the original production path and the old opt-in quiet-preparation
path. No persistent environment or production database change is needed.
The physical validator's `--settled-fan` enables the same production implementation
in its isolated, owned child and requires `VOICE_PLAYBACK_PROFILE=approved_loudness`.
It does not narrow normal application questions: only the validator applies the
strict public fixture gate before model dispatch.

Settled fan preparation continuously drains native ALSA PCM, using only normalized
left-channel frame RMS for decisions. It excludes at least 1.5 seconds of startup
(or the configured warm-up, if longer) from a rolling 1.0-second stability window.
The supplied startup decay motivates this separation; it is not proof of a
particular settling time on every assembled device. A window qualifies when:

- Its median is below the existing configured onset floor (normally 0.02).
- Its p90/p10 spread is <= 1.8, with a 0.0001 numerical denominator floor.
- The ratio between the two half-window medians is <= 1.35, using the same
  denominator floor. These explicit stability ratios are conservative release
  candidate tuning, covered by synthetic tests, not hardware-derived guarantees.

There is no requirement for zero noise or a run of absolutely quiet frames. The
six-audio-second preparation budget refuses persistent excessive or unstable noise
as `capture_preparation_unstable`; no retry or indefinite settling occurs. The
existing capture watchdog now accounts for this explicit six-second budget plus
configured calibration, start window, maximum utterance and one-second margin.
A stalled PCM pipe still kills only the owned capture process. The parent's
120-second deadline remains the outer bound, including inference and playback.

After settling, the existing 0.5-second median calibration must itself be stable
and within the same 1.35 ratio of the settled median, or readiness is refused as
`capture_calibration_unreliable`. Observable speech modulation, rising activity
and excessive floor levels therefore prevent readiness when detectable. The
preparation window contains at most 50 frame aggregates; existing bounded summary
statistics survive errors and cancellation without storing new raw audio.

The speech gate is reused without changing its onset, floor or resource settings:
`start=max(configured, min(1, 2.5*median_background+0.002))`, existing optional ceiling,
60 ms onset confirmation, hysteresis, 300 ms minimum speech, 300 ms native pre-roll,
0.8-second default endpoint pause tolerance and configured maximum duration.
For example, a synthetic 0.009 background produces 0.0245 onset, below the supplied
0.03134 expected-speech RMS. These are formula examples, not Pi accuracy results.
Frozen post-ready thresholds avoid learning speech as background. Modest fan
variation below onset does not activate; sufficiently large fan changes may still
look like speech to an energy gate.

No startup frames reach the request WAV. After readiness, native pre-roll and all
accepted speech frames remain byte-for-byte intact. Normalization still selects
the active left channel and converts to mono 16 kHz for Whisper.cpp base.en. No
silent right-channel mixing or fabricated PCM was added. Whisper, service, memory,
RAG, Qwen, sentence streaming, Piper and ALSA remain the existing production path.

Display initialization uses existing READY state plus PREPARING text (the current
firmware has no PREPARING state). The readiness callback sends LISTENING then
MICROPHONE READY before opening the full start window. Capture completion enters
THINKING before normalization/Whisper. The actual playback-start callback sends
SPEAKING; completion sends READY or the existing protective idle state. Existing
error/cancellation cleanup remains intact. Serial callback completion is not a TFT
pixel acknowledgement or acoustic synchronization measurement.

The actual streaming converter already passes the selected profile for each Piper
unit. `approved_loudness` replaces legacy gain with
`loudnorm=I=-16:TP=-2:LRA=7,volume=1.10,alimiter=limit=0.95:level=false`, then converts
to the existing 48 kHz stereo S32_LE output. Piper is already mono, so the approved
microphone-file FL pan is unnecessary here; mono is duplicated for the ALSA sink.
There is no stacked 4x gain. Existing chunk concatenation, silence, cancellation,
queues and continuous ALSA player are unchanged. Software tests verify filter
selection and streaming compatibility, not speaker loudness, acoustic clipping or
latency at one metre. Per-unit loudnorm can still have unit-dependent gain and
conversion cost; no unverified audio algorithm change was introduced.

## Validation and limits

Focused Windows suite: **278 tests passed**. Full Windows suite: **1285 tests,
OK, one skipped**. Full results are in
`release-windows-tests.log`; focused output is in `release-focused-tests.log`.
Both use mocks/synthetic signals and local temporary test data, not hardware,
cloud inference or installed model execution. Coverage includes startup decay,
fan-only variation, valid/short/interrupted speech, natural pauses and endpoint,
early preparation activity, persistent noise, calibration change, bounded timeout,
cancellation, native WAV bytes, left-only normalization, arbitrary user questions,
Whisper dispatch, streaming service/Piper, approved profile and display progression.
Existing tests retain public-fixture mismatch/empty/Whisper failure isolation,
resource gates, ownership, memory/RAG and legacy behavior coverage.

Energy statistics cannot reliably distinguish perfectly steady speech from steady
fan noise. Operators must remain silent until ready. Large fan changes after ready
can still activate an energy detector. Stability parameters and real one-metre
recognition need the supervised hardware acceptance below. The Whisper-side VAD runs after recording and cannot establish live readiness.
The wake-word engine also supports optional VAD assets, but installation of those
assets on the production Pi is not established by this evidence; introducing that
engine into request capture would add a new inference/dependency path. This release
reuses the existing lightweight capture gate instead. No new package or model was
downloaded. The physical source of elevated startup remains unknown.

## One supervised acceptance

Update the clean tracked feature checkout only:

```sh
cd "$HOME/apps/smart-ai-companion" && test "$(git branch --show-current)" = codex/offline-model-selection && git diff --quiet && git diff --cached --quiet && git fetch origin codex/offline-model-selection && git merge --ff-only origin/codex/offline-model-selection
```

With fan running and operator present, run exactly once. The report path must not
already exist. Do not restart services, unload Qwen, or automatically retry.

```sh
cd "$HOME/apps/smart-ai-companion" && test "$(git branch --show-current)" = codex/offline-model-selection && git diff --quiet && git diff --cached --quiet && OLLAMA_HOST=http://127.0.0.1:11434 VOICE_PLAYBACK_PROFILE=approved_loudness ./venv/bin/python scripts/validate_offline_voice.py --operator-live-trial --production-qwen --settled-fan --fixture fact --output "$HOME/final-release-acceptance.json"
```

Remain silent throughout PREPARING. After MICROPHONE READY, from approximately one
metre say exactly: **Name India's capital. Is it north or south of the equator?**
Do not add filler words; punctuation/case normalization is allowed, different words
are refused before Qwen. This strict gate belongs only to the validation utility.

Extract content-free stage evidence (the complete isolated report also retains the
approved public fixture answer for human quality review):

```sh
./venv/bin/python -c 'import json, pathlib; r=json.loads((pathlib.Path.home()/"final-release-acceptance.json").read_text()); c=r.get("cycle",{}); print(json.dumps({"status":r.get("status"),"reason":r.get("reason",c.get("reason")),"telemetry":r.get("telemetry"),"cycle":{k:c.get(k) for k in ("release_checks","capture_vad_summary","events_ms","durations_ms","display_calls","residency_label","ollama_metrics","voice_config","missing_events")}},indent=2))'
```

Required software result: `sample_complete_quality_pending`, every `release_checks`
value true, accepted silence endpoint, full expected event chain, correct strict
fixture transcription, one local matching Qwen request, warm residency and no
thermal/power/RAM/deadline failure. Resource guards retain below-50 C start,
>=70 C abort, full matching resident digest/context 4096, no extra resident model,
owned-process cleanup, loopback-only validation and isolated database/data.

Required operator result: complete speech understood at one metre, correct useful
answer (New Delhi; north of the equator), complete audible Piper output at the
approved volume without clipped/missing words, visibly coherent PREPARING,
LISTENING, THINKING, SPEAKING and READY, and a usable returned device. Software
completion alone is not physical acceptance. Any refusal, timeout, missing stage,
wrong answer, inaudibility or incorrect visible state fails acceptance. Report the
actual failed stage; do not run an automatic retry or switch models. Neither Pi
command nor a hardware trial was executed during Windows development.

## Changed files

- `assistant/voice/capture_vad.py`: bounded settled-fan preparation policy.
- `scripts/voice_loop.py`: policy integration, bounded watchdog and display lifecycle.
- `config/settings.py`: reversible process-specific policy selection, legacy default.
- `scripts/validate_offline_voice.py`: guarded flag/child propagation and release checks.
- `assistant/latency_trace.py`, `docs/voice-latency-trace.schema.json`: new stop category.
- `assistant/tests_release_candidate.py`: deterministic release regressions.
- `docs/final-release-candidate.md`: implementation, limits and acceptance procedure.
- `docs/release-focused-tests.log`, `docs/release-windows-tests.log`: test evidence.
