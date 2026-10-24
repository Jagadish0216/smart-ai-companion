# Step 51 public transcript classification repair

Baseline: `8ed3c52d76c4d43c3e2168b8ec7d8e8e5e0acc47` on
`codex/offline-model-selection`. No Pi inference or hardware test was performed.

## Root cause and limits of the reported evidence

In [`observe_pipeline.timed`](../scripts/validate_offline_voice.py#L172), successful Whisper
execution records `stt_end` and then applies `public_transcript` before returning
text to the production voice loop. A nonmatching result raises the fixed
`TrialRefused("transcription_did_not_match_public_fixture")`. This is deliberate:
unapproved text must never reach service dispatch, display-query handling,
conversation persistence or Ollama.

In the production [STT exception handler](../scripts/voice_loop.py#L1679), the STT call is inside a broad
`except Exception`. It raises `VoiceLoopError("Transcription failed: ...") from exc`.
Consequently the validator child receives a VoiceLoopError with TrialRefused in
its explicit `__cause__`, rather than a top-level TrialRefused. Before this repair,
`child` recognized only top-level refusals; its fallback `failure_kind` recognizes
timeout/cancellation but not TrialRefused. The mismatch therefore became generic
`cycle_error`, although the public fixture safety gate worked and dispatch stopped.

The supplied trace (speech detected, recording complete, `stt_end`, no dispatch)
is consistent with refusal after successful STT. It does not reveal the words or
prove that the actual trial returned a nonempty mismatch rather than empty/no-speech
output. No transcript is requested or reconstructed to diagnose that distinction.

## Minimal diagnostic-only repair

[`cycle_failure_reason`](../scripts/validate_offline_voice.py#L124) walks at most eight explicit exception causes and preserves
a categorical TrialRefused reason through the production wrapper. Existing capture
start-timeout and generic error/timeout/cancellation fallbacks remain. It never
serializes exception messages, traceback text or the cause objects.

The STT observer records distinct evidence at the boundary where it is available:

| Evidence | Report reason | `stt_end` |
| --- | --- | --- |
| Nonempty speech fails unchanged public normalization/matching | `transcription_did_not_match_public_fixture` | Present |
| Blank/whitespace or existing production no-speech placeholder | `transcription_empty_or_no_speech` | Present |
| STT operation raises an execution error | `whisper_execution_failed` | Absent |
| STT operation raises a recognized timeout | `whisper_timeout` | Absent |
| Cancellation | Existing `cycle_cancelled` | Absent if during STT |

The empty/no-speech check reuses `voice_loop.is_no_speech_transcription`; it does
not interpret arbitrary mismatched speech as silence. No error-message substring
matching is used. An execution failure category identifies a failing STT operation,
not a proven underlying binary/device/model cause. Existing direct refusals remain
refusals instead of being relabeled as Whisper execution errors.

Public matching still occurs before production policy/AI dispatch. Exact and
punctuation/case-normalized matches follow the existing accepted path. Mismatches,
empty results and failed STT are refused; no retry or fallback inference is added.
Unapproved text is never printed, stored in the trace, persisted to conversations,
passed to TFT query display or sent to Ollama. Child console suppression remains
in place. Approved public fixture text continues to be handled as before.

Only the diagnostic script changes runtime behavior. `voice_loop.py`, Qwen/default
model settings, request options, keep-alive, resource policies, data isolation,
VAD/capture settings, audio routing, 70 C cutoff, below-50 C startup and 120-second
trial deadline are unchanged. No `.env`, production database, model process,
service, package, firmware or unrelated file is modified.

## Regression evidence

New deterministic fixtures traverse the actual production `run_voice_cycle` STT
wrapper with mocked recording/normalization/Whisper, then test the child classifier.
They cover exact match, punctuation-normalized match, mismatched speech, whitespace,
no-speech marker, Whisper execution failure, Whisper timeout, cancellation and
unrelated-error fallbacks. Mismatch/error fixtures use synthetic canaries and verify
that console output and the saved partial JSON never contain them. They also
assert policy/dispatch does not occur for refused STT, and that successful-STT
versus execution-failure event boundaries remain distinct. Accepted fixtures stop
at a deliberate fake policy boundary; the existing full-pipeline exact-public
fixture test still covers service/Piper/ALSA/TFT orchestration with fake hardware.

Windows results: **91 focused tests, OK (3.581 s)**; complete suite
**1222 tests, OK, one platform skip (56.716 s)**. These are Windows mock
regression runtimes, not Pi performance measurements.

Commands (workspace-local temporary test storage):

```powershell
venv/Scripts/python.exe manage.py test assistant.tests_offline_voice_validation assistant.tests_voice_loop assistant.tests_demo_readiness --noinput
venv/Scripts/python.exe manage.py test --noinput
```

Changed files: `scripts/validate_offline_voice.py`,
`assistant/tests_offline_voice_validation.py`, this root-cause note,
`docs/demo-classification-focused-tests.log`, and
`docs/demo-classification-windows-tests.log`.

The existing [Step 51 guarded procedure](demo-readiness-step51.md) remains valid;
this repair does not authorize or execute another Pi trial. The old report cannot
be retrospectively reclassified as a proven mismatch. A future separately approved
single trial will preserve the categorical reason without retaining unapproved
transcript text. No device performance improvement or physical acceptance is claimed.
