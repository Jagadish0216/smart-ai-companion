# Offline companion final release candidate

Speech-candidate acceptance baseline: `9d825fe9322de97321f712233a3eeae84c11f603`.
Feature branch only: `codex/offline-model-selection`. Hardware acceptance pending.

## Candidate continuity correction

The latest Pi trial reached ready at 4903.657 ms and confirmed a candidate at
8001.647 ms, but ended at 9878.206 ms with `start_timeout`, `accepted=false` and
zero final speech duration. Its floor was 0.0055126, relative onset 0.01,
continuation 0.008, waiting maximum 0.0197114 and spectral evidence count 27.
These are aggregate observations, not a waveform or a per-frame speech label.
Do not add all evidence frames across candidates and infer a valid utterance.

`AdaptiveCaptureVAD.feed` previously required spectral novelty both for onset and
continuation. `SpectralOnset.evidence` is a current/smoothed multiband change
heuristic, not a detector guaranteed to stay true during every vowel. Frames
above calibrated continuation energy could therefore count as silence whenever
spectral evidence was false. After 0.8 seconds of such gaps, a candidate with less
than 300 ms counted voice reset to waiting and zeroed per-candidate duration.
The original start deadline then remained active. This reset path explains the
combination of a lone `speech_detected`, zero final duration and start timeout;
the aggregate data cannot establish exactly which real syllables or intervals
lost evidence, or rule out other acoustic difficulties.

The correction changes only the opt-in relative candidate's continuation:

- A waiting candidate still requires consecutive multiband spectral evidence and
  energy above the relative start threshold for the existing onset confirmation.
  Stationary fan energy alone cannot start it.
- Once confirmed, energy >= the same relative start threshold counts as voiced
  without requiring renewed spectral novelty. Lower energy >= the existing
  relative continuation threshold still uses the unchanged 60 ms resume
  confirmation. This retains sustained vowels and connected speech while keeping
  isolated weak resumption spikes from accumulating voice.
- Dips below continuation do not count as voiced; the existing 0.8-second endpoint
  holds the candidate through brief syllable gaps and pauses. The minimum remains
  300 ms actually counted voice, not elapsed duration or time spent in pauses.
- An insufficient candidate resets and must pass spectral onset again. The
  original five-second start deadline is never renewed. Confirmed capture remains
  bounded by its configured maximum utterance duration, existing watchdog and
  guarded session deadline. No retries or duplicate assistant dispatch are added.

Preparation, spectral extraction/evidence, thresholds, native pre-roll, WAV bytes,
Whisper/Qwen/Piper, display protocol, speaker profile, resource guards and public
fixture matching are unchanged. Continuation after a false but confirmed onset
can still be fooled by sufficiently strong speech-like interference; this is not
semantic speech classification. The onset guard, voiced minimum, endpoint,
duration limits and strict validation fixture continue to limit that risk.
Physical recognition and audibility still require the supervised assembled Pi run.

### Privacy-safe candidate accounting

Existing child `capture_vad_summary` and the parent report's nested `cycle` summary
now include integer `candidates_started`, `candidates_rejected` and
`candidates_accepted`. Started means a confirmed onset (not every tentative spike).
Rejected means a confirmed candidate reset or reached maximum duration without
meeting the voiced minimum. Accepted increments once when a candidate crosses
that minimum. A pending/cancelled candidate can be started but neither rejected
nor accepted; counters never manufacture an acceptance on cancellation.
Final `speech_duration` still describes the current candidate, so a reset may
leave it zero while the aggregate rejection count remains available.

A lone `speech_detected` event is candidate onset, not success. Check accepted
capture, `minimum_speech_accepted`, the accepted silence endpoint, the full event
chain, model completion and software release checks. The existing validator still
requires human review before physical acceptance.

The capture exception path previously labelled every `VoiceLoopError` as
`capture_error` in preparation diagnostics, including ordinary `start_timeout`.
It now preserves an actual VAD stop category in `preparation.refusal_reason`
(the existing field name also covers post-ready capture refusal). Genuine capture
I/O/callback failures remain `capture_error`, and cancellation remains `cancelled`.
No exception text, transcript, waveform or per-frame speech/evidence history is
added to diagnostics. Counter storage is constant and capture bounds are unchanged.

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

### Fan-tolerance release correction

The supervised Pi trial processed all 300 frames and exhausted the six-second
budget without readiness: min 0.001562, median 0.005698, p90 0.029225, max 0.165333,
47 frames above 0.02. Healthy temperature, RAM, model residency and power checks
exclude those guards as the recorded refusal. This demonstrates that the previous
p90/p10 <= 1.8 and half-window ratio <= 1.35 policy rejected a fan-on input with a
substantial lower-energy background. These aggregates do not reconstruct temporal
order, identify an acoustic/electrical cause, or prove physical acceptance of this
correction.

`settled_fan_v2` keeps the same native left-channel path, startup drain of at least
1.5 seconds (or the configured longer warm-up), rolling 1.0-second/50-frame window,
six-audio-second preparation limit and owned capture watchdog. It replaces the
whole-window flatness test with a bounded lower-energy interval estimator:

1. Form complete five-frame (nominal 100 ms) intervals and take each median.
   A short loud transient cannot dominate an interval with a majority of real
   background frames. No peak maxima or extreme percentile ratios set the floor.
2. Take the median of the lower half of interval medians as a background seed.
   Require it to be >= 0.0001 normalized RMS and below the existing configured
   onset floor (normally 0.02). Digital-zero/near-zero and continuously excessive
   inputs therefore do not authorize readiness.
3. Require at least 70% of intervals to support the low-energy background: valid
   interval medians below the configured onset floor and <= max(2*seed, seed+0.002).
   Take the median of these supported intervals as the estimate. A rare quiet
   interval cannot authorize readiness during sustained speech-like activity.
   At least three complete intervals are required; normal calibration has five.

These general robust-statistics choices tolerate sparse peaks without treating
all 47 high frames as evidence that the fan background is unusable. The signal
has 253 frames below 0.02 and a 0.005698 median, so a low-energy estimate is justified;
it is not justified to require the upper tail to lie within 1.8x the minimum tail.
The tests deliberately vary synthetic temporal arrangements and amplitudes;
there is no special-case check for the supplied 300-frame distribution. Ratios
and support requirements remain explicit, bounded release tuning, not a claim
that aggregate RMS can identify all speech or fan behavior.

After preparation, the existing 0.5-second calibration uses exactly the same
interval estimator. It must retain valid majority background support and must not
rise above twice the settled estimate, or it refuses as
`capture_calibration_unreliable`. Downward startup decay is allowed. A transient
cannot inflate the frozen onset simply because it raised the raw calibration
median: the validated robust floor is installed through the existing threshold
formula before MICROPHONE READY. Real calibration samples remain intact; no fake
samples or silence are inserted. The raw median remains the legacy calibration
behavior when this policy is disabled.

If no valid background is found within six audio seconds, refusal remains
`capture_preparation_unstable`. Nonfinite/out-of-range RMS is refused explicitly;
zero, near-zero, continuously excessive input or insufficient representative
intervals never force readiness. Calibration still has its existing bounded
sample duration, and the existing watchdog plus 120-second parent deadline remain
in force. No additional model call, dependency, retry or unbounded data is added.
Diagnostics identify `settled_fan_v2` and its actual parameters in the existing
bounded preparation summary; raw audio and per-frame RMS are not exported.

### Speech detection release correction

The latest physical trial confirms preparation and MICROPHONE READY work, but
speech was not detected within the full five-second start window. Settled floor
was 0.00940344 and calibrated floor 0.01288575. The legacy formula
`max(configured, 2.5*background+0.002)` yielded 0.034214375 start and 0.01821790625
continuation; maximum waiting RMS was 0.020136. The onset inequality could never
pass for those reported frames. No corresponding Pi waveform is available in
this workspace or supplied attachments. The maximum RMS does not identify speech,
its spectrum, or the duration of any above-background activity.

A scalar RMS gate cannot reliably distinguish modest speech from a fan gain
change. The settled-fan production capture path now uses an additional bounded
spectral measure from existing NumPy (already a wake-word dependency). It needs
no new package, model, service or cloud inference:

- During post-drain calibration, analyze the native 20 ms/960-sample LEFT frame
  using a Hann window and real FFT. Subtract DC for feature extraction only;
  request PCM is unchanged. Accumulate six band powers with edges 200, 400, 800,
  1400, 2200, 3200 and 4800 Hz plus total power over 50–8000 Hz.
- Freeze the median background spectrum before ready. Select frames using the
  already validated robust RMS estimate, excluding levels above
  `max(2*background, background+0.002)` so loud calibration transients cannot
  dominate this path either. Require at least ten usable calibration frames.
- Require at least two bands to have >=2x background power AND >=1.8x their
  calibrated fraction of total power, in both the current frame and a rolling
  three-frame average. Per-band numerical floors are 0.5% of total background
  power. This rejects tested uniform fan gain changes and suppresses broadband
  FFT fluctuations. The current-frame requirement avoids prolonging speech into
  silence just because the average still contains speech.
- With valid spectral calibration, onset additionally requires RMS >=
  `max(0.5*configured_start, sqrt(1.5)*background)`; continuation uses
  `max(0.4*configured_start, sqrt(1.2)*background)`. These are relative POWER
  margins, corroborated by spectral shape, not a blind global threshold change.
  Spectral corroboration is required for onset, not continuously after a confirmed
  candidate (see the correction above). The original VAD still requires consecutive
  onset confirmation (normally 60 ms), 300 ms minimum voiced duration and existing
  hysteresis/resume confirmation,
  300 ms native pre-roll, natural pauses, configured endpoint and duration limits.
  No waiting budget is renewed and no phrase is force-accepted.

For the supplied calibrated floor, the relative thresholds are approximately
0.01578176 onset and 0.01411563 continuation. A frame at 0.020136 can therefore
qualify only with the corroborating spectral and temporal evidence. The tests use
both synthetic formant-bearing signals that qualify and stationary fan signals
at that same RMS that do not. This is a regression constraint, not an inference
that the supplied maximum was the operator's speech.

The preparation estimator and ready lifecycle are unchanged. Legacy capture never
constructs the FFT detector and keeps its original scalar behavior. Partial PCM
reads do not invent spectral evidence. If calibration has insufficient non-DC
spectral energy, the original gate remains active, explicitly reported as
`speech_onset.available=false`; readiness itself is not fabricated or forcibly
bypassed. `capture_vad_summary` retains legacy `start_threshold` and
`continue_threshold` for compatibility and adds the actual relative thresholds
when enabled, plus constant policy parameters and an aggregate evidence count.
At most 128 seven-power calibration records plus RMS scalars and three current
feature vectors are retained. No waveform, transcript, spectrum or per-frame
feature data is exported. Error/cancellation summaries retain these aggregate
fields through the existing child trace and parent report paths.

This is a lightweight heuristic, not a trained speech classifier. Spectrally
speech-like interference can still trigger it, and extremely weak speech or speech
whose spectrum is indistinguishable from fan noise may be rejected. Working
one-metre capture, recognition and acoustic quality must be confirmed on the
assembled Pi. No Windows mock result establishes that physical success.

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

Candidate correction focused Windows suite: **304 tests passed**. Full suite:
**1311 tests, OK, one skipped**. Full results are retained in `candidate-continuity-windows-tests.log`; focused output is in
`candidate-continuity-focused-tests.log`. Earlier speech-onset/fan-fix/release logs
remain historical. New deterministic envelopes cover continuous vowels without
spectral novelty, consonant/vowel alternation, quiet speech, natural gaps, fan-only
input, short bursts, false onset/rejection, later valid onset inside the original
deadline, accepted silence endpoint, maximum-duration refusal and exact native
pre-roll bytes. Existing tests cover cancellation and single STT/service dispatch.
Both use mocks/synthetic signals and local temporary test data, not hardware,
cloud inference or installed model execution. Coverage includes startup decay,
fan-only variation, valid/short/interrupted speech, natural pauses and endpoint,
early preparation activity, persistent noise, calibration change, bounded timeout,
cancellation, native WAV bytes, left-only normalization, arbitrary user questions,
Whisper dispatch, streaming service/Piper, approved profile and display progression.
Existing tests retain public-fixture mismatch/empty/Whisper failure isolation,
resource gates, ownership, memory/RAG and legacy behavior coverage.

Operators must remain silent until ready. Energy alone cannot reliably distinguish
fan changes from speech; the corroborated path reduces this ambiguity but does
not establish semantic speech detection in every acoustic environment. Interval-support parameters and real one-metre recognition need the supervised
hardware acceptance below. A fan that dominates most intervals or an ambiguous
steady speech-like signal remains a limitation of energy-only detection. The Whisper-side VAD runs after recording and cannot establish live readiness.
The wake-word engine also supports optional VAD assets, but installation of those
assets on the production Pi is not established by this evidence; introducing that
engine into request capture would add a new inference/dependency path. This release adds bounded NumPy spectral corroboration to the existing capture
gate instead of introducing a new model inference path. No new package or model was
downloaded. The physical source of elevated startup remains unknown.

## One supervised acceptance

Update the clean tracked feature checkout only:

```sh
cd "$HOME/apps/smart-ai-companion" && test "$(git branch --show-current)" = codex/offline-model-selection && git diff --quiet && git diff --cached --quiet && git fetch origin codex/offline-model-selection && git merge --ff-only origin/codex/offline-model-selection
```

With fan running and operator present, run exactly once. The report path must not
already exist. Do not restart services, unload Qwen, or automatically retry.

```sh
cd "$HOME/apps/smart-ai-companion" && test "$(git branch --show-current)" = codex/offline-model-selection && git diff --quiet && git diff --cached --quiet && OLLAMA_HOST=http://127.0.0.1:11434 VOICE_PLAYBACK_PROFILE=approved_loudness ./venv/bin/python scripts/validate_offline_voice.py --operator-live-trial --production-qwen --settled-fan --fixture fact --output "$HOME/candidate-acceptance.json"
```

Remain silent throughout PREPARING. After MICROPHONE READY, from approximately one
metre say exactly: **Name India's capital. Is it north or south of the equator?**
Do not add filler words; punctuation/case normalization is allowed, different words
are refused before Qwen. This strict gate belongs only to the validation utility.

Extract content-free stage evidence (the complete isolated report also retains the
approved public fixture answer for human quality review):

```sh
./venv/bin/python -c 'import json, pathlib; r=json.loads((pathlib.Path.home()/"candidate-acceptance.json").read_text()); c=r.get("cycle",{}); print(json.dumps({"status":r.get("status"),"reason":r.get("reason",c.get("reason")),"telemetry":r.get("telemetry"),"cycle":{k:c.get(k) for k in ("release_checks","capture_vad_summary","events_ms","durations_ms","display_calls","residency_label","ollama_metrics","voice_config","missing_events")}},indent=2))'
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

## Rollback

Keep the persistent environment unchanged. To select the existing legacy capture
path for an ordinary application process, use
`VOICE_CAPTURE_PREPARATION_POLICY=legacy` and disable the opt-in ready lifecycle.
For the guarded validator, replacing `--settled-fan` with `--ready-lifecycle` selects
the previous absolute-quiet lifecycle; it may reproduce the known fan refusal.
To restore the exact preceding release code, revert this correction's commit on
`codex/offline-model-selection` and fast-forward the Pi to that reviewed revert;
no model/service restart, database change or persistent environment edit is needed.

## Candidate correction changed files

- `assistant/voice/capture_vad.py`: post-confirmation energy continuity and
  constant-memory candidate counters; original timing and acceptance bounds.
- `scripts/voice_loop.py`: categorical VAD refusal and aggregate counters in summaries.
- `assistant/tests_speech_onset.py`: candidate continuity, rejection, deadlines,
  quiet/connected speech and native PCM regressions.
- `assistant/tests_stt_comparison_audio_profile.py`: integer counter/schema assertions.
- `docs/final-release-candidate.md`: source-based diagnosis and acceptance procedure.
- `docs/candidate-continuity-focused-tests.log`,
  `docs/candidate-continuity-windows-tests.log`: Windows evidence.
