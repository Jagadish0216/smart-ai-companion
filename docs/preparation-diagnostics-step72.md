# Step 72: preparation refusal measurements

Baseline: `1f590a817773005e816079f20de103ae08b58d2d`.

## Diagnosis

`QuietCapturePreparation.consume_warmup` in `assistant/voice/capture_vad.py`
drains real ALSA frames and refuses when consecutive frame RMS is **at or above**
`vad.configured_start` for `vad.min_speech_seconds`. The existing production
values are 0.02 and 0.30 seconds. This rule is unchanged. At nominal 20 ms frames,
the reported 0.30 and 0.64 warm-up audio seconds correspond to 15 and 32 processed
frames. They support a sustained threshold crossing, but do not identify its
source, amplitude distribution, or whether it was a startup artifact or ambient
sound. The new trial must measure that before any algorithm change.

In `scripts/voice_loop.py::record_audio_with_capture_vad`, warm-up frames bypass
`vad.feed`. A warm-up refusal therefore precedes calibration: zero `noise_floor`
and `max_wait_rms` are unpopulated measurements, not evidence of silence. The
approximately 128 ms warm-up-to-first-PCM delay measures delivery/startup; it
does not establish the origin of the sound or its recording time.

The same function previously emitted `endpoint_detected` for preparation refusal,
calibration refusal and start timeout. It now emits `capture_stopped` for VAD stop
reasons. `endpoint_detected` is reserved for accepted speech ending in the VAD
silence endpoint; a maximum-duration stop is a limit, not a silence endpoint.
`observe_pipeline` in `scripts/validate_offline_voice.py` takes categorical stop
reasons from `capture_stopped`, preserving `capture_preparation_activity` and
`capture_calibration_unreliable`. Cleanup does not emit an endpoint.

## Report fields and bounds

Enable with the existing explicit `--ready-lifecycle` guarded-validator option;
the normal production configuration remains unchanged. No new recordings,
transcripts, content, per-frame exports, or inference calls are added. Diagnostics
are `cycle.capture_vad_summary.preparation` in the parent JSON, and
`capture_vad_summary.preparation` in the child trace. The capture `finally` callback
preserves them on refusal, cancellation and capture error; the parent's existing
finally path imports the child trace on failure. A forcibly killed process cannot
guarantee a final child snapshot.

- `warmup_frames`: all processed warm-up frames, including the refusing frame.
- `warmup_rms_min/max/median/p90`: normalized left-channel RMS aggregates. Median
  uses the middle value(s); p90 uses nearest rank, ceil(0.9*n), indexed from one.
  Empty statistics are null. Min/max are streaming across every processed frame.
- `above_threshold_frames`, `above_threshold_audio_seconds`: count and sum of
  actual frame durations at **>=** `activity_threshold` (the configured onset
  floor, not the later adaptive threshold).
- `longest_above_threshold_audio_seconds`: longest uninterrupted qualifying run;
  one below-threshold frame resets the run, exactly as the existing gate does.
- `first_pcm_to_activity_refusal_ms`: monotonic wall time from receipt of the
  first nonempty PCM frame to the activity refusal decision; null otherwise.
  This differs from warm-up audio duration when buffered PCM drains quickly.
- `startup_frames_before_activity_decision`: frames preceding the decision frame
  (14 or 31 in the nominal synthetic reproductions); null without that decision.
- `calibration_started`: true only when a real post-warm-up frame reaches the
  calibration feed, rather than merely when the calibration-start event is marked.
- `refusal_reason`: preparation_activity, calibration_unreliable, cancelled,
  capture_error, or null. Cancellation/error categories are observational and
  do not replace the validator's existing failure classification.
- `rms_statistics_capacity`: 4096. At most this many RMS scalars are retained in
  memory, plus scalar counters. No PCM is added to diagnostic storage. If the
  count exceeds capacity, `rms_statistics_complete` is false and median/p90 are
  null rather than misleading partial-prefix statistics. Export size is constant;
  sorting happens only for the final summary, never in the frame loop.

All thresholds, warm-up time, calibration and readiness gates, native audio,
Qwen residency, thermal/power/RAM policies, 120-second session deadline, ownership,
and strict public fixture matching remain in force. No automatic retries.

## Windows verification

Focused command: `venv/Scripts/python.exe manage.py test assistant.tests_capture_readiness assistant.tests_capture_vad assistant.tests_offline_voice_validation assistant.tests_latency_trace assistant.tests_voice_loop assistant.tests_whisper_audio_diagnostic --noinput`.
Full command: `venv/Scripts/python.exe manage.py test --noinput`.
Focused: **178 tests passed**. Full: **1272 tests passed, one skipped**.
Results are retained in `step72-focused-tests.log` and `step72-windows-tests.log`.
All audio/recognizer/model/display behavior in these tests is synthetic or mocked;
Windows timing is not Pi performance. Tests cover both supplied audio-duration
patterns, short startup bursts, intermittent activity, clean preparation, bounded
statistics, calibration refusal, cancellation, accepted endpoint ordering,
categorical failure reporting and parent propagation.

## Later operator-supervised Pi validation (not executed here)

Update only a clean tracked feature checkout; no service restart or configuration change:

```sh
cd "$HOME/apps/smart-ai-companion" && test "$(git branch --show-current)" = codex/offline-model-selection && git diff --quiet && git diff --cached --quiet && git fetch origin codex/offline-model-selection && git merge --ff-only origin/codex/offline-model-selection
```

Run once with assembled hardware and an operator present. The existing validator
must accept the resident production Qwen identity/context, below-50 C start,
RAM, power and process guards. It retains its thermal cutoff and deadline.
Keep quiet during preparation. Speak only after MICROPHONE READY, using exactly
the displayed public fixture: "Name India's capital. Is it north or south of the
equator?" A refusal ends the trial; do not automatically repeat it.

```sh
cd "$HOME/apps/smart-ai-companion" && test "$(git branch --show-current)" = codex/offline-model-selection && git diff --quiet && git diff --cached --quiet && ./venv/bin/python scripts/validate_offline_voice.py --operator-live-trial --production-qwen --ready-lifecycle --fixture fact --output "$HOME/preparation-diagnostics-$(date +%Y%m%d-%H%M%S).json"
```

Inspect aggregates and event ordering first. This change does not establish a
physical cause or propose a new activity threshold.

## Changed-file inventory

- `assistant/voice/capture_vad.py`: bounded preparation aggregates.
- `scripts/voice_loop.py`: monotonic decision timing, final summary, endpoint semantics.
- `scripts/validate_offline_voice.py`: stopped-event categorical tracing.
- `assistant/latency_trace.py` and `docs/voice-latency-trace.schema.json`: event allowlist.
- `assistant/tests_capture_readiness.py`: deterministic regression and propagation tests.
- `docs/preparation-diagnostics-step72.md`: diagnosis, definitions and operator procedure.
- `docs/step72-focused-tests.log` and `docs/step72-windows-tests.log`: Windows evidence.
