# Step 66: calibrated microphone readiness lifecycle

Baseline `1e0386b5fcda7f6142f665487d80b55141271e32`, branch
`codex/offline-model-selection`. This is an **opt-in correction for supervised
physical validation**, not physical acceptance. No Pi inference, microphone
capture, package/model installation, service restart, firmware change, production
data write or `.env` edit was performed here. Reliability at 1 metre remains to be
established on assembled hardware.

## Confirmed evidence versus inference

The operator's original recording replay reports floor .0457428885, start
.1163572212, continuation .0633964717 and maximum waiting RMS .0392295279, ending
in start_timeout. The median-based formula explains why later waiting audio did
not cross that start threshold. Moving the same recording after one second of
digital silence changes the calibration median to zero, start to .02 and permits
detection at approximately 1.06 s. This strongly supports **calibration-window
contamination in that replay**. It does not identify every live failure's cause,
prove microphone sensitivity, or justify adding silence to production audio.

The delayed replay ends with accepted speech but no endpoint. A file ending is
not equivalent to the live VAD receiving sufficient end silence. Detection,
minimum voiced acceptance, endpoint, STT success and public-fixture matching are
separate conditions. `transcription_did_not_match_public_fixture` occurs after
capture/STT; it does not mean VAD failed to start. The strict gate is unchanged.

Source findings before this correction:

- `voice_loop._run_voice_cycle` sends TFT LISTENING before recording begins.
  LISTENING therefore is not proof calibration has finished.
- `record_audio_with_capture_vad` opens arecord (configured device, stereo 48 kHz
  S32_LE) and sleeps for warm-up without reading stdout. Initial PCM can remain
  queued; calibration then consumes the first read frames, not necessarily the
  room's current wall-clock samples. Actual live buffering/overrun was not measured.
- Left-channel RMS is used consistently for native VAD, and left-channel
  normalization feeds mono 16 kHz STT. No routing change is indicated.
- Calibration is a median of the initial configured audio-duration window.
  Thresholds freeze afterward, including during the wait. There is no ongoing
  threshold ratchet. A lone transient is median-rejected, but sustained speech or
  noise can contaminate the initial window.
- The old capture-ready mark is after calibration, not before it. However the
  standalone TFT remains LISTENING throughout preparation, and the guarded parent
  polls the ready mark at .5 s plus telemetry/metadata cost. Its terminal cue can
  arrive later than internal readiness.
- The old audio-duration start timeout includes calibration and rejected bursts.
  With .5 s calibration, the nominal 5 s budget leaves at most 4.5 s after it,
  before considering late cue delivery. Consecutive confirmation can also reset
  across brief dips. These are source findings, not measured Pi timing losses.

Possible live causes include early speech, stale buffered startup PCM, sustained
room noise, later speech below the frozen threshold, interrupted confirmation,
short utterances, missing endpoints, slow callbacks or late cue delivery. No new
hardware measurement proves which combination affected a particular trial.

## Targeted correction, disabled by default

New `VOICE_CAPTURE_READY_LIFECYCLE=false` preserves legacy behavior by default.
No existing numeric threshold or duration default changes. The guarded
`--ready-lifecycle` flag enables the setting in the isolated child only. It
requires `--production-qwen --operator-live-trial`; the configured capture must
already be ALSA with enabled adaptive VAD. It refuses other configurations rather
than enabling VAD, changing devices or weakening safeguards. Direct use of the
capture function also rejects the new mode on non-ALSA backends before opening
any process, retaining the ALSA watchdog requirement.

Corrected execution sequence:

1. Parent shows the approved public question and PREPARING/keep-quiet instruction.
   The existing TFT enters LISTENING with caption `PREPARING - keep quiet`.
   No new firmware/state enum is needed; LISTENING alone is still not the cue.
2. Capture opens the existing arecord device/format. First-frame timing is recorded.
   The configured 1 s warm-up is actively **read/drained**, rather than a sleep
   with unread pipe data. These real preparation frames are not request PCM.
3. Sustained preparation RMS at/above the configured onset floor for the existing
   minimum-voiced duration (normally .3 s) refuses as `capture_preparation_activity`.
   This flags non-quiet activity, not proven speech. Brief transients do not by
   themselves meet this sustained rule. The operator is explicitly told to remain
   quiet; early speech is not silently treated as an authorized request or retried.
4. Calibration then consumes its own configured .5 s of actual frames. If its
   median is at/above the configured baseline onset floor (normally .02), readiness
   is refused as `capture_calibration_unreliable`. This conservative policy can
   also refuse a noisy room; it does not classify the source of the sound or
   guarantee that a lower median contains no speech. Thresholds are never lowered
   or replaced by digital silence. There is one preparation attempt, not a quiet
   search/recalibration loop.
5. After usable calibration, the existing TFT text callback attempts
   `MICROPHONE READY`. The guarded child writes **only a fixed public cue** to a
   duplicated descriptor inherited from its parent terminal, then closes it.
   All child console/native logs remain suppressed. The parent polling cue is
   disabled in this mode, preventing duplicate/late polling announcements. The
   descriptor is not a broadcast or application messaging channel.
6. Only after the ready callback and cue write return does
   `AdaptiveCaptureVAD.open_start_window()` establish the wait origin. The same
   5 s audio-duration budget now excludes calibration and cue callback work.
   This does not prove five exact wall-clock seconds on hardware: device backlog,
   CPU scheduling, transport/display latency and stalled I/O remain observable
   limitations. Active warm-up draining removes the deliberate initial unread
   sleep, not every possible kernel/device buffer.
7. Existing 60 ms start confirmation, .3 s minimum voiced duration, hysteresis,
   .8 s endpoint silence, 10 s maximum speech capture and .3 s pre-roll apply.
   Original speech/pre-roll samples are retained unchanged after preparation;
   no fabricated silence is appended. End silence or maximum duration must
   complete valid capture. Short bursts/silence still reject or time out.
8. Final audio is normalized from left to mono16, then Whisper runs. The public
   fixture gate executes before policy/service dispatch or query display. A
   mismatch, empty/no-speech result or STT error cannot trigger Qwen. Approved
   speech proceeds through existing THINKING -> SPEAKING -> READY, Piper, ALSA,
   RAG/memory and response policy.

The capture watchdog includes the separate calibration budget in the corrected
mode: warm-up + calibration + start timeout + maximum speech + 1 s grace
(normally 17.5 s). This also bounds a stalled ALSA read by killing/reaping the
owned arecord. The existing parent 120 s overall trial limit covers setup,
callbacks, STT/LLM/TTS/playback and owned-group cleanup. This is not a hard realtime
deadline; callback/output failures abort rather than silently opening a window.
No new capture thread, automatic retry or indefinite wait for quiet is added.

## Readiness is observable, not an acoustic/pixel measurement

Guarded reports retain first-occurrence monotonic timestamps for:

- capture_open_start/end (process creation, not proof of hardware stabilization);
- capture_first_frame, capture_warmup_start/end;
- calibration_start/end, capture_ready;
- ready_display_callback_returned, microphone_ready_emitted;
- speech_start_window_open, speech_detected, minimum_speech_accepted;
- endpoint_detected, capture_end, STT and existing downstream stages.

`capture_vad_summary` retains numeric floor/start/continuation/max-wait RMS,
voiced/capture/VAD elapsed durations, accepted flag, warm-up audio seconds,
start-window origin on the VAD audio clock, and stop reason. Warm-up audio time
and VAD audio time are distinct; monotonic event offsets are cycle-relative.
`ready_emitted_before_speech_window` verifies software ordering, while
`ready_display_evidence` explicitly states callback-only visibility. The caption
callback returning does not prove TFT pixels or firmware ACK. Terminal write
success is not an acoustic cue or proof the operator has seen it. Keep the
invocation on a visible, responsive operator terminal, not a slow/unattended pipe.

Categorical preparation refusals are also printed by the parent. No private audio,
unmatched transcript, per-frame samples, secret or arbitrary exception detail
crosses the cue descriptor or enters reports. Existing strict public transcript
gating and partial-failure handling remain. Generic opt-in latency trace/schema
accept the added bounded lifecycle events; storage/event limits are unchanged.

The Django browser path is separate: `templates/dashboard/assistant.html` obtains
browser media with getUserMedia/MediaRecorder; its Listening state describes that
browser stream. `assistant/views.py` accepts uploaded audio for transcription and
service dispatch. The Pi CLI's capture/calibration events do not currently stream
to that browser UI. No fabricated Django microphone-ready event or global event
broadcast is introduced. ESP32 text uses the existing best-effort serial protocol,
without firmware changes. The separate hands-free `NativeRequestReader` already
has its own draining/ready lifecycle and is not redesigned by this opt-in change.

## Deterministic evidence and remaining physical checks

Tests use synthetic left PCM and a contrived RMS envelope matching the reported
aggregate original/delayed replay values, including 1.06 s onset, 5.12 s voiced
duration and no endpoint for the delayed case. This is **not** reconstruction of
the real waveform or measurement of ALSA startup, device buffering or display delay.
Legacy Step 65 replay remains the default so historical comparisons remain valid.

With analysis-only `--ready-lifecycle`, replay shares the new preparation policy
and actual VAD start-window method. It consumes warm-up frames before calibration
and reports refusal/incomplete state honestly. An original recording that starts
with speech can now refuse during preparation; that is not a claimed improvement
in its transcript. Even the one-second-delayed file need not pass the new mode:
its speech may overlap the additional calibration interval after drained warm-up.
No recorded file is altered. A future valid live trial requires silence through
the actual new ready cue, not guessing from a prerecorded offset.

The Windows environment's inspected safe settings were pulse, capture VAD disabled,
with baseline numerical values .02/true/.5 s/60 ms/.3 s/5 s/.8 s/10 s/1 s. Those are
not Pi environment measurements. The Pi verification below reports its own
current values without changing overrides. Do not turn this into a threshold
reduction or enable the new lifecycle globally before physical acceptance.

## Operator procedure (not executed here)

Update a tracked-clean feature checkout without deleting unrelated/untracked work:

```bash
cd "$HOME/apps/smart-ai-companion" &&
test "$(git branch --show-current)" = codex/offline-model-selection &&
git diff --quiet && git diff --cached --quiet &&
git fetch origin codex/offline-model-selection &&
git merge --ff-only origin/codex/offline-model-selection
```

Compare HEAD with the delivered SHA. One read-only existing-recording verification:

```bash
./venv/bin/python scripts/whisper_audio_diagnostic.py \
  --operator-approved-public-audio --audio "$HOME/mic-test-1m.wav" --ready-lifecycle
```

This prints analysis/current numeric settings and simulated corrected replay,
without devices, inference, conversion, Ollama requests or persistent reports.
Omitting the flag retains the legacy replay used for the supplied evidence.

For **one separately supervised** live trial, first confirm fan/power health,
natural cooling below 50 C, existing ALSA/adaptive capture-VAD configuration,
visible operator terminal, and sole matching resident Qwen (digest prefix
d8b0f5e9760c, full installed digest, context 4096). Avoid competing capture,
display or inference activity; do not stop unrelated services to make it work.

```bash
./venv/bin/python scripts/validate_offline_voice.py \
  --operator-live-trial --production-qwen --ready-lifecycle --fixture fact \
  --output "$HOME/capture-ready-$(date +%Y%m%d-%H%M%S).json"
```

Remain quiet while PREPARING is shown. Only after the standalone fixed
**MICROPHONE READY** line, say the displayed public question exactly, at about
1 metre: “Name India's capital. Is it north or south of the equator?” Then stop
speaking and allow endpoint silence. LISTENING by itself is not permission to speak.
There is one capture and at most one model request; no automatic repeat. If noise
or early speech refuses preparation, inspect the reason and room conditions;
another trial requires an explicit operator decision after reviewing/cooling.

Acceptance requires software order calibration_end <= microphone_ready_emitted <=
speech_start_window_open < speech_detected; accepted minimum speech and a real
endpoint; complete approved STT; no premature Qwen dispatch; factual complete answer;
actual TFT preparation/ready states and real speaker response; healthy thermal/RAM/
power telemetry throughout. Physical cue visibility, natural 1-metre onset,
short/natural pauses and cancellation still require supervised hardware checks.
A fixture mismatch after successful VAD is an STT/gating result, not permission
to weaken matching. No physical pass is claimed from mock success.

All existing guards remain: below-50 C startup, existing pre-inference gate,
sampled >=70 C cutoff, required telemetry/power/throttle/low-memory checks,
exclusive lock, resident Qwen checks, unchanged 24 h keep-alive and 120 s session
limit. No warm/reload/unload/restart command is added. Killing the client does not
prove immediate backend cancellation. Ctrl+C targets only the owned session;
allow resource/backend activity to settle and inspect the partial report.
Never use an unguarded ordinary voice loop as a test fallback.

Rollback: omit `--ready-lifecycle`; the old path remains the default. If explicitly
set for a process, unset `VOICE_CAPTURE_READY_LIFECYCLE`/set false next time. No
`.env` was edited. Leave the diagnostic unused or apply a reviewed code revert;
do not reset shared/unrelated work. No model/package/database/firmware/hardware
or approved speaker-profile rollback is needed.

## Verification and changed files

Final Windows results: **175 focused tests, OK (3.408 s)**; full suite
**1269 tests, OK, one platform skip (37.300 s)**. One preceding full run
failed the existing trace-store rotation test (two write errors versus one
expected injected error); it passed immediately in isolation and on the full
rerun, without a trace-store implementation change. The cause of that intermittent
write error is not established. Focused tests cover known replay aggregates, usable
calibration, sustained preparation activity, transients, interrupted confirmations,
short speech, pauses/endpoints, a full post-calibration start budget, active
warm-up draining, original left PCM/pre-roll preservation, readiness/callback/FD
ordering, missing cue/PCM, timeout/cancellation, parent descriptor ownership and
unsupported backend refusal. Existing suites retain fixture privacy/gating,
Qwen/resource/process safeguards, provider/streaming and schema compatibility.

Files: `assistant/voice/capture_vad.py`, `scripts/voice_loop.py`,
`config/settings.py` (new default-off setting only),
`scripts/validate_offline_voice.py`, `scripts/whisper_audio_diagnostic.py`,
`assistant/latency_trace.py`, `docs/voice-latency-trace.schema.json`,
`assistant/tests_capture_readiness.py`, this document,
`docs/capture-ready-focused-tests.log`, `docs/capture-ready-windows-tests.log`.


Verification commands (TEMP/TMP redirected to workspace `.test-tmp`):

```powershell
venv/Scripts/python.exe manage.py test assistant.tests_capture_readiness assistant.tests_capture_vad assistant.tests_offline_voice_validation assistant.tests_whisper_audio_diagnostic assistant.tests_voice_loop assistant.tests_latency_trace --noinput
venv/Scripts/python.exe manage.py test --noinput
```
