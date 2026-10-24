# Voice interruption and delivery corrections

These changes extend `3c2c78245eaa03341747eb450c8f770851a6cf3e` on
`codex/offline-model-selection`. They are opt-in. Acoustic voice interruption
has **failed the supplied Pi trials and has not been revalidated**. Windows
tests establish software behavior only. Do not report this change as physical
acceptance or assume that a duplex audio device guarantees intelligible speech
over the MAX98357A speaker.

## Findings and changes

* Capture: the existing relative detector caps onset RMS at 0.08 and continuation
  below 0.076. A fan floor above that continuation threshold can continually
  reset quiet time. Its 0.012 onset minimum can also miss soft speech in a quiet
  room. Both conditions are reproduced with synthetic RMS sequences. The actual
  supplied sessions lack RMS data, so neither is proven to be their cause.
  `--voice-refinements` opts into thresholds derived from the existing standby
  background (onset max(0.002, 1.8*floor+0.001), continuation
  max(0.001, 1.25*floor+0.0005), with normalized bounds). No calibration learns
  from the opening question, and no pre-roll or recorded frames are discarded.
  The 0.9-second endpoint quiet requirement, 0.3-second voice-evidence minimum,
  maximum recording duration, unknown-floor fallback and native left channel
  are preserved. Noise-like or uncertain speech may intentionally reach the cap.
  Quiet reset count, longest quiet interval and accumulated voiced duration help
  explain that fallback. No fixture validator is a production dependency.
* Gaps: the old later-unit packer can hold a complete sentence until 120 characters
  or more arrive; slow Qwen production therefore can withhold work from Piper.
  One Piper preparer and model generation already run concurrently, so adding
  parallel Piper/model processes would not repair that wait safely. The opt-in
  packer releases complete later sentences from 60 characters, retaining the
  existing protected boundaries and word order. A first-audio lookahead waits
  at most 0.8 seconds for another ready item, never for response completion.
  Text/audio queues remain two items each. This can trade up to 0.8 seconds of
  startup latency for reserve audio; it cannot guarantee continuous audio if
  generation plus synthesis remains slower than playback.
* Pacing: opted-in ordinary word-boundary splits receive zero added silence.
  Comma/semicolon/colon boundaries have a 0.35-second budget and sentence ends
  0.18 seconds. Retained PCM quiet and an actual queue wait reduce that budget.
  Piper's internal punctuation timing is preserved. No pause is added to conceal
  a multi-second underrun, and punctuation is not stripped from spoken text.
* Interruption: the current monitor starts after PCM submission, which includes
  leading silence. It requires two consecutive 80-ms scores >= max(0.85,
  configured threshold), the existing VAD, and an echo guard. A missing/silent
  reference fails closed; an output unit containing Jarvis blocks detection,
  including the existing two-second tail. The reference follows pipe acceptance,
  **not an acoustic/device clock**, and can lead actual output. Correlation
  rejection is not AEC; room echo and mixing may still reject a real user.
  These architectural limits cannot be diagnosed conclusively from the supplied
  timing-only logs. Thresholds, VAD and echo protection are unchanged.
* Cancellation: in the opted-in refinement path the cancelling thread signals the
  owned aplay child before HTTP shutdown callbacks, rather than waiting for the
  playback worker's next polling point. The sole playback worker retains
  terminate/kill/wait/reap and descriptor ownership. Numeric acknowledgement
  reports child reaping latency, **not measured speaker silence**. Existing
  shared cancellation reaches Piper, ffmpeg, pending text and prepared audio;
  workers join before another capture. The two-second bounded handoff, deferred
  answer persistence, conversation ID and TFT interruption states are preserved.

No `.env`, packages, models, Ollama residency/context, gain, loudness profile,
services, firmware, wiring or document artifacts are changed by this work.

## Reading the diagnostics

`--voice-diagnostics` prints one numeric-only interruption summary per response.
It is bounded by a fixed field allowlist, not a per-frame log. Useful fields:

| Field | Interpretation |
| --- | --- |
| monitor_started / monitor_ready | Attempt scheduled / first PCM received; Popen alone is not readiness |
| monitor_start_ms / monitor_failed / monitor_ended | First-frame delay, failed attempt, monitor exit |
| frames / max_mic_rms | Detector-frame count and maximum native-left energy; intelligibility remains unmeasured |
| max_score / confirmations | Maximum wake score and consecutive permitted count |
| echo_rejections | Above-threshold frames rejected by the guard |
| reference_ready / reference_missing / reference_silent | Reference presence versus missing/silent guard refusals |
| reference_text_blocked / max_correlation / reference_age_ms | Explicit Jarvis protection, correlation, age since accepted PCM; age does not measure acoustic alignment |
| mic_silent | A demeaned incoming frame has negligible energy; distinct from missing output reference |
| cancel_requested / output_reaped / cancel_ack_ms | Detection/cancel reached control, owned player reaped, software cancellation latency |

Absent counters mean the relevant path was not observed, rather than zero failures.
No raw microphone PCM, transcript, prompt or response content enters these
diagnostic summaries or latency reports. Existing interactive transcript output
is unchanged; do not redirect a private demonstration's terminal transcript into
an evidence log. Use public questions and retain only numeric diagnostic lines.

Combine with `--latency-trace-dir /tmp/smart-ai-voice-timing` for the existing
bounded store: eight files, 128 KiB each, 512 events per cycle, at most 16 per
event name (dropped events are counted). New events are `text_unit_ready`,
`audio_unit_ready`, `audio_unit_dequeued`, and `punctuation_pause`. They carry
unit/character counts, Piper duration, audio-preparation duration, PCM duration,
text queue age, ready queue age and intentional silence. Existing text queue
wait, ffmpeg `playback_conversion`, PCM `speech_file`, pipe-acceptance and
`drain` spans distinguish the remaining stages. The final EOF marker is not a
starvation event. Queue-wait timing can overlap already-buffered ALSA audio;
`playback_starvation_ms` is a supply-queue wait, not a microphone measurement of
audible silence. Correlate it with actual listening during acceptance.

## Supplied physical comparison

| Trial | Capture | AI total | First speech | Queue starvation | Total |
| --- | --- | --- | --- | --- | --- |
| Session 1 short | 10.00 s, max_duration | 19.785 s | 25.874 s | 5,400 ms | 44.707 s |
| Session 1 detailed | 9.94 s | 83.921 s | 27.550 s | 20,358 ms cumulative; 5,375 ms maximum | 96.459 s |
| Session 2 | 8.80 s | 19.914 s | 24.711 s | 2,945 ms | Not supplied |

The known-good ec2f80e baseline averaged about 13.6 seconds to first speech.
Every supplied recent voice-stop attempt failed. New Pi measurements are pending;
no improvement percentage, audible-gap result or successful voice-stop result is
inferred from the automated suite. Collect the same metrics in the supervised
session and compare alongside the stage traces, not just total AI duration.

## Deployment and one supervised acceptance session

Stop the existing foreground voice loop with Ctrl+C before updating. The exact
revision-pinned update command is supplied with the completion message. Use the
existing feature checkout and venv, require clean tracked files, fetch only this
feature branch, and fast-forward to that revision. Do not reset a branch, install
anything, restart services or run experiments automatically.

Before the single supervised session, manually confirm healthy resource status,
temperature below 50 C, acceptable power/throttle flags, adequate RAM, the
resident approved Qwen3.5 4B Q4_K_M with context 4096, local base.en/Piper/Hey
Jarvis assets, the existing approved output profile and no other microphone
owner. Retain the existing 70 C operator cutoff and thermal/resource guards.
Missing assets or uncertain ownership are reasons to stop, not download/retry.

Run this once in a foreground terminal (five-minute bound, no persisted audio):

```sh
cd "$HOME/apps/smart-ai-companion" && timeout --signal=INT --kill-after=20s 300s env AI_ENGINE=local ONLINE_RETRIEVAL_ENABLED=false OLLAMA_HOST=http://127.0.0.1:11434 VOICE_AUDIO_BACKEND=alsa VOICE_CAPTURE_VAD_ENABLED=false VOICE_CAPTURE_READY_LIFECYCLE=false VOICE_CAPTURE_PREPARATION_POLICY=legacy VOICE_WAKE_WORD_ENABLED=true VOICE_WAKE_WORD_MODEL=hey_jarvis VOICE_WAKE_WORD_MODEL_PATH= VOICE_WAKE_WORD_TIMEOUT_SECONDS=0 VOICE_WAKE_WORD_COOLDOWN_SECONDS=0.75 VOICE_PLAYBACK_PROFILE=approved_loudness VOICE_HANDS_FREE_MAX_SECONDS=10 ./venv/bin/python scripts/voice_loop.py --hands-free --voice-barge-in --responsive --voice-refinements --voice-diagnostics --latency-trace-dir /tmp/smart-ai-voice-timing
```

1. Say Hey Jarvis, wait for LISTENING, then ask “Who invented Python?” Check the
   first and last words, capture stop reason, first-speech time, complete audio
   and TFT transitions. Include one natural pause shorter than 0.9 seconds in a
   subsequent public question. Excessive capture waiting fails the latency goal.
2. Ask “Explain how solar panels generate electricity and describe the main
   components.” Listen quietly through several text units before interrupting.
   Compare real gaps with numeric queue waits, Piper, conversion and text-unit
   times. Any self-trigger or unexplained multi-second gap fails acceptance.
3. During playback, say “Hey Jarvis, stop.” Require prompt complete audible
   silence, INTERRUPTED then standby, no stale audio and no new model request
   for the stop-only command. Check detection and output-reaped counters. If
   voice stop fails, use Enter/s or Ctrl+C immediately and end this trial; record
   failure and the precise observed diagnostic stage. Do not lower protection
   or repeat automatically.
4. If the preceding checks pass, wake again with a public question, then interrupt
   a sufficiently long answer with “Hey Jarvis, when was it invented?” Confirm
   complete opening words, one new request, retained conversation, no overlapped
   capture/Piper/model workers, and synchronized TFT states. A too-short answer
   does not prove interruption; stop and report that criterion as untested.
5. End with Ctrl+C; confirm owned audio children released, no old audio resumes
   and resource health remains acceptable. Record observed stop-to-silence time
   separately from software child-reap acknowledgement. Any lost word, cleanup
   failure, self-trigger, overlap or thermal concern fails acceptance.

If diagnostics show monitor failure/no frames, the blocker is capture startup or
duplex ownership. Frames with low scores indicate the wake/VAD/acoustic path;
high scores with echo refusals indicate guard/reference/acoustic mixing. Two
confirmations without cancel acknowledgement indicate cancellation/cleanup.
Nonempty reference and low age still do not establish correct acoustic alignment.
If reliable echo-resistant speech interruption cannot pass this session, retain
keyboard interruption; a verified duplex/AEC architecture would be separate work.

## Immediate fallback and code rollback

Ctrl+C stops the experiment. To keep keyboard interruption, use the same command
with `--barge-in` replacing `--voice-barge-in` and omit `--voice-refinements`,
`--voice-diagnostics`, `--responsive` and the trace option. Keyboard Enter/s stops;
`n` then Enter captures after LISTENING. To return to original hands-free behavior,
omit all interruption/refinement/responsive/trace flags. Enter-to-record remains
available by also omitting `--hands-free`.

For the known-good code rollback, after stopping the foreground loop:

```sh
cd "$HOME/apps/smart-ai-companion" && git diff --quiet && git diff --cached --quiet && git switch --detach ec2f80e126ebf5096ed2aecdd4aabecb4ff8e22a
```

Run the previously successful original hands-free command, without the new flags.
This changes no main branch, services or configuration. Returning to the feature
checkout later uses `git switch codex/offline-model-selection` with a clean tree.

## Automated verification

Focused suite: 273 tests passed, one skipped. Full suite: 1,362 tests passed,
one skipped. The skip is the existing platform-dependent case. The tests include
the earlier interruption, echo, ALSA cleanup, stale-queue, stop-only, handoff,
context/persistence, TFT and original hands-free regressions. New deterministic
cases exercise a high fan floor, quiet speech, natural pauses, long/no-speech
fallbacks, complete PCM retention, diagnostic score/guard/confirmation counters,
duplex-start failure, immediate player signalling, punctuation silence, earlier
later-sentence scheduling, ordered chunks, bounded prebuffering and content-free
per-unit timings. These are not physical Pi timing or acoustic measurements.
