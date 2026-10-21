# Offline Hey Jarvis hands-free product

Baseline: `7f9fff84bccd0f69ab90b687418f062409137164`.
Feature branch: `codex/offline-model-selection`. Hardware acceptance pending.

## Product behavior

The proven Enter-to-record path, native stereo 48 kHz S32_LE capture, active LEFT
Whisper normalization, base.en, local Qwen3.5 4B/context/residency, memory/RAG,
Piper, approved loudness and existing TFT protocol are preserved. Hands-free is
explicitly selected with `--hands-free`; no persistent environment, firmware,
installed package/model, database migration or service modification is performed.
Ordinary conversations still use the normal application persistence and safeguards.

Standby uses the existing persistent OpenWakeWord model on CPU and one owned
arecord. The shipped configuration selects pretrained **Hey Jarvis**. A working
Hey Companion phrase requires a compatible trained model; none is claimed here.
The provider closes/reaps its wake arecord in finally before returning detection.
Wake heard text is the visible acknowledgement. Request ALSA opens next; LISTENING
and Listening... text precede the first read. A bounded 320 ms tail from wake
capture is handed to the request in memory (hidden from detection repr/logs) and
released after the cycle instead of remaining retained through standby.
Every fresh native request frame is retained immediately. The previous half-second
post-wake discard/calibration is bypassed in CLI hands-free mode, protecting
opening words. Existing direct recorder callers retain the previous behavior.

The two exclusive ALSA processes still have a physical close/open gap; a memory
tail cannot reconstruct audio spoken while the device was closed. For acceptance,
use the LISTENING cue before speaking the question. Zero lost opening words on
real hardware must be confirmed; synthetic mocks cannot establish gapless capture.
The tail may include the end of the wake phrase. A transcript consisting solely
of Hey Jarvis/Jarvis returns to standby without an AI request. Arbitrary questions
and questions mentioning Jarvis remain normal application input.

A bounded history of at most 64 prior wake-chunk RMS values provides a lower-half
median background when at least five chunks are available. The current triggering
chunk is excluded. Request endpoint hints use a relative background threshold,
hysteresis, a 100 ms evidence window and 300 ms minimum voiced energy, then the
configured 0.9-second quiet interval. No hint can reject/reset/remove request PCM.
Unknown background or uncertain speech uses the reliable fixed-duration fallback.
The default maximum remains six seconds; the supervised command below selects
ten seconds, and the validated maximum can be configured up to 30. A question
longer than that cap can be truncated; do not speak past the selected window.
Endpoint accuracy in fan noise still requires the assembled microphone test.

The unchanged normalized mono 16 kHz audio goes to Whisper, then the existing
assistant service and Qwen. Complete first sentences of at least 20 characters can
start Piper early in hands-free mode, reusing the existing tested first-sentence
option. An explicit configured first-sentence policy is respected; ordinary modes
are unchanged. Existing sentence/long-unit safeguards, text integrity, bounded
queues, single Piper worker and continuous ALSA player are retained. No overlapping
response playback, new LLM request, model switch, load/unload or speculative response
is added. The approved profile replaces legacy gain; it is not multiplied by 4x.

The wake provider is not called during request capture, Whisper, Qwen, Piper or
speaker playback. Only after full drain and cooldown does its history reset and
standby resume. Conversation IDs carry across successive successful wake cycles.
READY is the normal idle state; existing resource-policy ECO/PROTECTIVE states
remain possible. Standby text says Say Hey Jarvis. TFT callback success does not
prove visible pixels, and ALSA submission/drain does not prove acoustic audibility.

A hardware or voice-cycle failure is never automatically replayed as an inference
request. A fresh wake is needed for another request. Transient contention uses
1 and 2 second backoffs; three consecutive failures stop with ERROR and actionable
fallback guidance. No-speech results simply return to standby, without AI/TTS.
Ctrl+C releases owned wake/request/player children, Piper and display. No global
process killing, service restart or device reconfiguration is performed.

Existing assistant resource/execution policies still gate generation, including
thermal/power/RAM protection. No thresholds, model residency settings or resource
policies are changed. This ordinary product loop is not the single-trial physical
validator and does not replace its strict fixture or session rules. Use its
existing guard tests/procedures for guarded benchmark work; do not introduce a
fixed public-question restriction into normal assistant conversations.

## Deployment and existing-asset check

On a healthy Pi, stop any competing interactive microphone process using its own
normal shutdown. Do not restart Ollama, unload Qwen or alter services/configuration.
Run only against a clean tracked feature checkout:

```sh
cd "$HOME/apps/smart-ai-companion" && test "$(git branch --show-current)" = codex/offline-model-selection && git diff --quiet && git diff --cached --quiet && git fetch origin codex/offline-model-selection && git merge --ff-only origin/codex/offline-model-selection && VOICE_WAKE_WORD_MODEL_PATH= ./venv/bin/python manage.py prepare_wake_word_model --model hey_jarvis --check-only
```

The check-only operation imports the existing engine and verifies installed,
nonempty assets. It never downloads, prepares assets, opens ALSA, runs a model or
changes configuration. Missing assets are a startup failure with exact paths and
an installation command, not an automatic download. Readiness still depends on
runtime model initialization in the product process.

If assets are missing, stop and obtain operator approval before the explicit
installation: `VOICE_WAKE_WORD_MODEL_PATH= ./venv/bin/python manage.py prepare_wake_word_model --model hey_jarvis`.
It requires installed OpenWakeWord 0.6.0 and the ONNX feature models, Hey Jarvis
model and optional configured Silero VAD asset. The command enumerates exact
missing paths. If OpenWakeWord itself is absent on Pi Linux Python >=3.12,
the repository dependency requirement is `./venv/bin/python -m pip install -r requirements.txt`
and `./venv/bin/python -m pip install --no-deps openwakeword==0.6.0`, only after
confirming the missing dependency and obtaining operator approval. Do not reinstall
working packages or models. None of these installation commands was run here.

## One supervised physical acceptance procedure

Before starting, confirm healthy temperature (below 50 C for this supervised
start), current power/throttle flags, adequate RAM and the existing matching
resident Qwen/context 4096. Keep cooling active. No second microphone owner or
extra resident LLM should be running. Retain the normal operator thermal abort
practice at 70 C. The existing application resource policies remain active.

Launch once in the foreground; all settings below apply only to this process:

```sh
cd "$HOME/apps/smart-ai-companion" && env AI_ENGINE=local ONLINE_RETRIEVAL_ENABLED=false OLLAMA_HOST=http://127.0.0.1:11434 VOICE_AUDIO_BACKEND=alsa VOICE_CAPTURE_VAD_ENABLED=false VOICE_CAPTURE_READY_LIFECYCLE=false VOICE_CAPTURE_PREPARATION_POLICY=legacy VOICE_WAKE_WORD_ENABLED=true VOICE_WAKE_WORD_MODEL=hey_jarvis VOICE_WAKE_WORD_MODEL_PATH= VOICE_WAKE_WORD_TIMEOUT_SECONDS=0 VOICE_WAKE_WORD_COOLDOWN_SECONDS=0.75 VOICE_PLAYBACK_PROFILE=approved_loudness VOICE_HANDS_FREE_MAX_SECONDS=10 ./venv/bin/python scripts/voice_loop.py --hands-free
```

1. Wait for standby/Say Hey Jarvis. Say **Hey Jarvis** from the intended distance.
2. Confirm visible Wake heard then LISTENING/Listening... acknowledgement. After
   LISTENING, ask naturally: **What is the capital of India?** Keep within ten
   seconds and stop speaking normally. No Enter key is needed.
3. Confirm the complete opening words and question reach Whisper, THINKING follows
   capture, one useful Qwen answer plays clearly through Piper at the approved
   volume, SPEAKING accompanies playback, then idle/standby returns. Verify no
   early endpoint removes words or trailing speech and no chunks overlap/repeat.
4. Without restarting, say **Hey Jarvis** again, wait for LISTENING and ask:
   **Is it north or south of the equator?** Verify retained context and a complete
   correct audible answer, followed by standby. Wake detection must not respond
   to the assistant's own speech or resume before playback drains.
5. Press Ctrl+C. Confirm process-owned microphone/player resources release and
   the display/provider shutdown completes. Any missing stage, lost opening word,
   truncation, false self-trigger, inaudibility, wrong context or unhealthy resource
   state fails acceptance. Stop at the failed stage; do not run rapid retries.

The operator's approximately 21-second first-speech latency is historical Pi
evidence from fixed recording. No new Pi timing or hardware acceptance is claimed.
Expected latency opportunities are removing the post-wake discard, early endpoint
when confident and the complete-first-sentence option; unknown/noisy endpoint
conditions deliberately retain the full fixed cap. Measure actual improvements
only on the supervised assembled device after function is confirmed.

Immediate Enter-to-record fallback, using the verified fixed path:

```sh
env VOICE_CAPTURE_VAD_ENABLED=false VOICE_CAPTURE_READY_LIFECYCLE=false VOICE_CAPTURE_PREPARATION_POLICY=legacy VOICE_PLAYBACK_PROFILE=approved_loudness ./venv/bin/python scripts/voice_loop.py
```

Omitting --hands-free bypasses wake configuration/assets entirely. All commands
leave persistent .env, firmware and services unchanged.

## Windows verification and inventory

Focused: **405 tests passed**. Full suite: **1321 tests, OK, one skipped**.
Full results are in `hands-free-windows-tests.log`;
focused evidence is `hands-free-focused-tests.log`. Tests are synthetic/mocked,
not Pi performance or hardware acceptance. Coverage includes two real runtime
cycles with mocked wake engine/STT/Qwen/Piper/ALSA/TFT, nonoverlapping ownership,
context, immediate frame retention and exact WAV bytes, endpoint/fixed fallback,
no-speech/wake-only handling, bounded recovery, cancellation, asset checking with
no downloads, approved playback and existing resource/strict-validator regressions.

Changed implementation: `assistant/voice/hands_free.py`,
`assistant/voice/wakeword/base.py`, `assistant/voice/wakeword/openwakeword_provider.py`,
`assistant/voice/wakeword/models.py`, `assistant/management/commands/prepare_wake_word_model.py`,
`scripts/voice_loop.py`. Tests: `assistant/tests_hands_free.py`,
`assistant/tests_wakeword.py`, `assistant/tests_display.py`. Documentation/evidence: `README.md`, this document,
`docs/hands-free-focused-tests.log`, `docs/hands-free-windows-tests.log`.
