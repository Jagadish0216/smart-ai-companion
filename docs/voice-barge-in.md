# Opt-in responsive speech and interruption

Baseline: `ec2f80e126ebf5096ed2aecdd4aabecb4ff8e22a`, feature branch
`codex/offline-model-selection`. The baseline's two successful physical hands-free
cycles remain the hardware evidence. This change has Windows mock coverage only.
No Pi inference, hardware trial, model/package installation, service action or
persistent configuration change was performed.

## Controls and latency

Original `--hands-free` and Enter-to-record modes remain available unchanged.
Add `--barge-in` for operator cancellation during SPEAKING: Enter, or `s` then
Enter, stops the answer; `n` then Enter stops and captures a new request. Speak
after LISTENING for the keyboard handoff. Ctrl+C exits and cleans up owned children.
Use a foreground interactive terminal for keyboard controls.

`--voice-barge-in` implies keyboard interruption and adds experimental simultaneous
OpenWakeWord listening during playback. Say **Hey Jarvis, stop/cancel**, or
**Hey Jarvis** followed by a new question. A confirmed interrupt wake stops output
before Whisper classifies the command. Exact stop/cancel transcripts dispatch no
AI request; other transcripts follow the existing production service. The wake
microphone is closed and its monitor joined before request capture opens ALSA.

`--responsive` releases even a short complete first sentence, such as
“Guido van Rossum.”, once punctuation has its existing safe lookahead. Previously
the hands-free 20-character minimum could hold that sentence until more text or
EOF. Later packing, bounded queues, one Piper preparer and one persistent player
stay unchanged; this avoids introducing many tiny later synthesis jobs. All
canonical answer text and model settings stay unchanged. Existing first-clause
fallbacks, warmed providers and safe streaming remain in use.

Historical Pi averages supplied by the operator are 13.6 s to first speech,
18.3 s overall, ~1.9 s Whisper, ~4.9 s Qwen TTFT and one 1.26 s starvation gap.
The new mock proves earlier scheduling for a short sentence, with identical text.
It proves no Pi speedup. Under 10 s remains a target, not a result. There is no
new timer, capture threshold, shortened silence or speculative model change.
Existing first-speech timing is software submission timing, not acoustic onset.

## Cancellation and context boundary

`assistant/voice/interruption.py` supplies a cycle-local controller only inside
the opted-in hands-free response. One shared cancellation event reaches Piper's
cancellable synthesis, ffmpeg, bounded queues and persistent ALSA writes/drain.
The active player's existing abort terminates, kills if necessary and reaps its
owned aplay child. Queued response audio is discarded when joined workers exit;
temporary files are removed before the next request. No second response/player
starts while old workers remain active.
An interrupted Piper provider is also explicitly closed after its worker joins;
its normal lazy initialization remains available for the next request. Unconfirmed
player/Piper cleanup halts the loop rather than risking a second owned stream.

`LocalLLMEngine.generate_stream` registers the active urllib HTTP socket for
`shutdown(SHUT_RDWR)`, waking an in-progress response-body read on cancellation.
The socket context and service generator explicitly close afterward. No Ollama
process, model residency or keep-alive setting is touched. This uses CPython's
HTTPResponse `fp.raw._sock` transport; interruptible mode refuses a response whose
transport cannot expose this cancellation hook. Initial HTTP connect/header
waiting retains its existing bounded timeout. The keyboard and wake monitors
start during playback, when streamed body text is already available.

The service defers its single AI history insertion in interruptible mode until
the player successfully drains and the monitor closes. Interrupted/failed answers
are discarded from history, including answers whose generation finished before
playback was interrupted. The user message and conversation ID survive; the next
question reuses that conversation. No claim is made that a partial answer was
fully spoken. Requests outside this scope keep their normal persistence timing.

The TFT uses existing READY/LISTENING/THINKING/SPEAKING states. Interruption
clears the speaking level/animation, shows INTERRUPTED, then listens for a new
question or returns to wake standby. No new firmware protocol is required.

## Duplex, echo and handoff limits

**Pi concurrent ALSA operation and acoustic echo rejection are unverified.** The
existing capture and playback commands name different directions; that does not
establish duplex support or room reliability. Windows mocks cannot settle either.
The opt-in monitor opens only after aplay starts submitting PCM. A busy capture
device, failed detector or failed handoff prints an explicit keyboard fallback
message. One monitor attempt occurs per answer, without a rapid reopen loop.
An unconfirmed microphone cleanup is fatal before another capture can start.

False-trigger protections are two consecutive 80 ms scores at least 0.85 (or the
configured threshold if higher), the existing model/VAD gate, fresh wake state,
and speaker-reference rejection. The guard compares demeaned incoming audio with
a bounded two-second playback reference at 2 kHz; absolute normalized correlation
at or above 0.35 is rejected. Missing/silent reference fails closed. A spoken unit
containing “Jarvis” is blocked, including a two-second tail into the next unit.
This may intentionally miss a user's interruption in that unit; use the keyboard.
This is conservative correlation rejection, **not full acoustic echo cancellation**.
Room reverberation, nonlinear speakers, buffering delay and speech mixed with echo
can cause false negatives or positives. Physical acceptance must test both.

On interrupt detection, the same microphone continues retaining opening words
while owned workers stop. The retained native PCM, including the wake tail, has
a hard two-second cap (768,000 bytes) and stays in memory until the existing
temporary request WAV/Whisper normalization path consumes it. Exceeding the cap
refuses the handoff, stops the old answer and requires a fresh wake; it never
silently drops the opening. The microphone is then released before capture opens.
ALSA close/reopen creates an unavoidable device transition: continuity across that
gap and the complete opening question require physical verification. If words
are lost, fail acceptance and use keyboard `n` followed by speech after LISTENING.
Normal wake tails remain capped at 320 ms outside interruption mode. Request
capture still has its existing bounded endpoint/fixed-duration fallback.

Memory bounds: two pending text units, two prepared audio units, one pending AI
history entry, one two-second handoff, two seconds of numeric reference, at most
two monitor threads per cycle. Reference PCM and handoff audio are never exported
or included in logs/repr. Existing temporary audio cleanup remains in force.
The reference observer does not write a recording or add a dependency.

## Safe update and one supervised acceptance session

Update only a clean tracked feature checkout; no resets, restarts or installs:

```sh
cd "$HOME/apps/smart-ai-companion" && test "$(git branch --show-current)" = codex/offline-model-selection && git diff --quiet && git diff --cached --quiet && git fetch origin codex/offline-model-selection && git merge --ff-only origin/codex/offline-model-selection && VOICE_WAKE_WORD_MODEL_PATH= ./venv/bin/python manage.py prepare_wake_word_model --model hey_jarvis --check-only
```

Keep the Pi supervised. Confirm temperature below 50 C, healthy current power/
throttle flags, adequate RAM, the existing resident Qwen3.5 4B and no other
microphone owner. Retain the 70 C operator cutoff and existing resource policies.
Do not unload/restart Qwen or change routing, models, .env or services. Missing
wake assets require stopping and following the explicit operator-approved asset
requirements in [hands-free-product.md](hands-free-product.md); do not auto-download.

One foreground session below is additionally bounded to three minutes. Use only
public demonstration questions, and stop on any failure rather than retrying
automatically. `timeout` signals this foreground process group and has a final
owned-session kill bound if cleanup hangs.

```sh
cd "$HOME/apps/smart-ai-companion" && timeout --signal=INT --kill-after=20s 180s env AI_ENGINE=local ONLINE_RETRIEVAL_ENABLED=false OLLAMA_HOST=http://127.0.0.1:11434 VOICE_AUDIO_BACKEND=alsa VOICE_CAPTURE_VAD_ENABLED=false VOICE_CAPTURE_READY_LIFECYCLE=false VOICE_CAPTURE_PREPARATION_POLICY=legacy VOICE_WAKE_WORD_ENABLED=true VOICE_WAKE_WORD_MODEL=hey_jarvis VOICE_WAKE_WORD_MODEL_PATH= VOICE_WAKE_WORD_TIMEOUT_SECONDS=0 VOICE_WAKE_WORD_COOLDOWN_SECONDS=0.75 VOICE_PLAYBACK_PROFILE=approved_loudness VOICE_HANDS_FREE_MAX_SECONDS=10 ./venv/bin/python scripts/voice_loop.py --hands-free --voice-barge-in --responsive
```

1. Wake with Hey Jarvis and ask: “Explain how solar panels generate electricity
   and describe the main components.” Confirm complete capture, Whisper, Qwen,
   Piper, clear approved-volume speech and TFT states. Remain quiet briefly during
   playback: any self-trigger fails acceptance.
2. During the answer, say “Hey Jarvis, stop.” Verify actual speaker output stops
   completely, INTERRUPTED appears, stop-only transcription sends no new Qwen
   request, and READY/standby returns without stale audio. Use Enter if voice
   interruption fails; record voice acceptance as failed, even if keyboard works.
3. Wake again and ask “Who invented Python?” During a longer response if one is
   produced, say “Hey Jarvis, when did he create it?” Verify opening words survive,
   old playback stops, LISTENING/Whisper runs once, the conversation ID remains,
   exactly one new Qwen answer plays and TFT returns to standby. If the answer is
   too short for interruption, use the contextual follow-up at standby; do not
   claim that this proves a voice handoff.
4. Ctrl+C ends the session. Confirm all owned microphone/player/Piper children
   release. Check no obsolete audio resumes and no new extra model is resident.
   Missing words, duplicated dispatch, overlap, false triggers, unsafe resource
   state or unreleased devices fail acceptance. No automatic physical retries.

For a deliberate operator-fallback trial, omit `--voice-barge-in` and add
`--barge-in`; Enter/s then Enter stops, n then Enter captures after LISTENING.
Rollback of the feature requires only omitting `--barge-in`, `--voice-barge-in`
and `--responsive`, retaining the original `--hands-free` command. Enter-to-record
also remains available by omitting `--hands-free`. To roll back code, with a clean
feature checkout run `git switch --detach ec2f80e126ebf5096ed2aecdd4aabecb4ff8e22a`;
this does not reset or modify main. No deployment/hardware acceptance is claimed.

## Windows evidence and changed files

Focused: **366 tests, OK**. Full: **1,347 tests, OK, one skipped**.
Full-suite results are recorded in `barge-in-full-tests.log`; focused evidence
is `barge-in-focused-tests.log`.
They cover mocked blocked HTTP cancellation, in-flight
Piper cancellation, active ALSA abort, stale-queue discard, producer closure,
no overlapping response handoff, exact stop commands, history commit/discard,
context preservation, score/echo guards, bounded audio handoff, failed cleanup,
duplex fallback and earlier complete-sentence scheduling. Existing audio,
display, capture, safety and strict-validator tests remain in the full suite.

Implementation: `assistant/ai_engine/local.py`, `assistant/services.py`,
`assistant/voice/interruption.py`, `assistant/voice/alsa_stream.py`,
`assistant/voice/hands_free.py`, `assistant/voice/wakeword/audio.py`,
`assistant/voice/wakeword/base.py`,
`assistant/voice/wakeword/openwakeword_provider.py`, `scripts/voice_loop.py`.
Tests: `assistant/tests_interruption.py`. Documentation/evidence: `README.md`,
this document, `docs/barge-in-focused-tests.log`, `docs/barge-in-full-tests.log`.
