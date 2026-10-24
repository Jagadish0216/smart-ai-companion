# Offline voice latency and quality validation

## Decision and evidence

Qwen3.5 4B remains the production quality reference and default. This milestone
adds diagnostics only; no production Python, settings, audio, firmware or display
protocol is changed. Llama3.2 1B is selected only in a disposable child process.
There is no cloud or remote-inference fallback and no model download.

The supplied Pi observation is **312.767 ms first meaningful text**, **4.395 s
complete text**, **1.36 ms load**, **308.8 ms prompt evaluation**, roughly **9
tokens/s**, with **120 cached out of 136 prompt tokens**. It is a partially cached
technical sample, not evidence of fully uncached conversational speed or audible
latency. Its answer confused clock cycles with instructions: **quality failure**.
Llama is not yet an acceptable primary recommendation. Qwen's supplied warm text
reference remains roughly 5–5.5 s first text, 2.7–4 tokens/s and 18–26 s normal
responses. These are not matched voice A/B results.

Targets, all still requiring physical validation: warm meaningful text <2 s;
first audible speech after STT ideally <3 s; short spoken answer ideally <8 s;
correct, grounded, fully offline responses and safe thermal behavior. A completed
diagnostic report never automatically accepts quality or physical latency.

## Architecture audit

The accepted pipeline in `scripts/voice_loop.py` is capture → normalize → local
Whisper.cpp → voice response policy → `AssistantService.process_message_stream`
→ local Ollama → `StreamingSpeechBuffer` → persistent Piper worker → audio
preparation → bounded PCM queue → persistent ALSA player. The service supplies
local lexical RAG, conversation memory and context with the same routing and
grounding rules. TFT LISTENING/THINKING/SPEAKING/idle, mode, query, captions and
amplitude calls follow the accepted production flow.

* Capture: fixed recording includes input warm-up; configured capture VAD can
  stop after silence. Hands-free has its own wake/capture loop. Default source
  values in code include 1 s input warm-up, 5 s fixed capture, or 0.8 s VAD silence;
  these are **code defaults**, not a reading of production `.env`. A fixed
  recording can dominate perceived latency before STT even begins.
* Whisper.cpp: the cached provider invokes a bounded local executable per
  transcription. A cached provider object does not establish a resident Whisper
  model. Its stage includes executable/model initialization and transcription.
* Service: resource policy, RAG and memory occur before/around inference. One
  configured LLM serves plain, RAG and memory requests; no second model is needed.
* Streaming: first text is observed directly at the engine's alphanumeric delta,
  excluding service status notices. Existing `ai_ttft` is presentation timing and
  may differ. Neither is microphone-to-audible latency.
* Buffering: the accepted first unit uses minimum 60/target 100 characters,
  later target 180 (unless configured otherwise). It may release a safe clause
  before a complete sentence, or hold a short complete sentence until final text.
  The existing earlier-sentence option is preserved, not enabled by this tool.
* Piper: one cached, spawned worker serializes synthesis. Readiness is not audio
  generation. This single-cycle child starts its own provider; its first WAV may
  include worker/voice initialization, unlike a long-running warmed voice loop.
* ALSA: gain, leading silence, trimming, inter-unit silence, devices, PCM format,
  caption progression and drain behavior are unchanged. Queue starvation is
  already measured. `first_speech` is recorded before actual PCM writes, so it
  must not be relabeled as audible onset.
* TFT: callbacks precede and accompany streaming playback. A successful serial
  write is not evidence of a visible pixel transition or firmware acknowledgment.

## Harness and exact timing definitions

`scripts/validate_offline_voice.py` reuses `voice_loop.run_voice_cycle`, the
existing isolated-database bootstrap, model identity checks, telemetry, pressure
guard and owned-process-group cleanup. It does not replace the existing text
model harness or inference A/B tool. Instrumentation wraps calls only inside the
test child. Production imports and defaults are untouched.

All event times are milliseconds from the child's `time.monotonic()` origin.
Initialization before capture is therefore visible as an offset. Trace snapshots
are bounded (first events only) and atomically replaced under a thread lock.
Observation/file writes add overhead; do not treat this as a zero-overhead trace.

| Report field | Observable boundary and limitation |
|---|---|
| `capture_start/end` | Entry/return of the accepted recording call, including configured warm-up, VAD wait or fixed duration, and finalization. Not the exact first/last human speech sample. |
| `stt_start/end` | Entry/return of real local Whisper transcription after normalization. |
| `dispatch` | Service generator begins consumption, before policy/service preparation completes. |
| `inference_request_ready` | Model/telemetry probes and request construction complete; just before engine network dispatch. |
| `llm_first_text` | First engine delta containing an alphanumeric character; excludes service notices; no claim of semantic correctness. |
| `first_complete_sentence_ready` | Accepted speech buffer first emits a unit containing a complete sentence. Conservative punctuation/group rules apply; a clause may have reached Piper earlier. |
| `piper_first_audio` | First complete WAV returned from synthesis, before PCM conversion. Not the first sample generated inside Piper. |
| `alsa_first_pcm_accepted` | First successful pipe write of prepared speech PCM, observed via the existing byte-progress callback. Initial injected silence is excluded, but prepared speech WAV may itself contain silence. Not DAC or audible onset. |
| `alsa_drain_end` | Successful `aplay` EOF/drain/exit. Software end-of-playback evidence only. |
| `cycle_end` | Production cycle returns, including temporary audio cleanup and idle state call. |
| `display_calls` | Intended state value and call-entry time. `display_enabled` identifies disabled/no-op providers; no captions, queries, port or credentials are saved. |

Durations explicitly subtract the named stage boundaries: capture, STT,
dispatch-to-first-text, STT-to-PCM, STT-to-drain, and capture-to-cycle-end.
Legacy `first_speech`, `first_audio_ready`, `ai_to_first_speech`, starvation and
other scalar timings retain their names/units in a separate object. Ollama load,
prefill, decoding, token counts and cache counters remain separate backend
metrics. A resident model is labeled warm only with the exact resident context
and reported load ≤100 ms; a reload/unknown load invalidates the sample.
Prompt-cache reuse is accounted separately and is never called general uncached
warm speed. Missing cache counters remain unknown.

Missing required events or impossible timing order produces an incomplete
report. Exceptions, cancellation or pressure produce an aborted report with a
fixed reason and any available partial trace. A length-limited answer is flagged
for truncation review, never certified complete. There are no inferred acoustic
values: wake detection (not invoked), human utterance end, actual VAD delay,
audible start/end and visible TFT transition are explicitly unobservable here.
Obtain those from an external synchronized recorder/logic/acoustic setup; a
stopwatch or unsynchronized phone video supports only a coarse manual estimate.
Do not upload or retain private mic recordings to fill these gaps.

## Isolation, safety and privacy

The parent and child use fresh SQLite/media/cache directories. Only public
synthetic notes/history are seeded: Lumen voltage and Amber Finch codename, or
RAM/SSD context. RAG and memory exercise the real service against these fixtures;
they never read deployed SQLite conversations or knowledge files. The candidate
model is exactly `llama3.2:1b`, full installed digest pinned by prefix
`baf6a787fdff`, local GGUF only, with exactly one resident model and context 4096.
Recheck identity immediately before inference and residency throughout/after.
No alternate model, model pull, unload, restart or service configuration action
is issued. Request-local idle expiry is 60 s; normal Ollama expiry is left intact.

Live mode requires Linux and the explicit `--operator-live-trial` switch, real
configured Whisper.cpp, Piper and ALSA. It runs **one** selected public question,
at most **48 generated tokens** (a smaller protective budget is respected), seed
42, temperature 0, context 4096, thinking off. CPU overrides are omitted. Existing
voice buffering and audio settings remain configured values. These test controls,
fresh Piper provider, seeded context and lack of wake loop are unequal conditions
versus an unrestricted production conversation; report them when comparing.

Require temperature **below 55°C** both before starting and before inference,
available RAM **at least 512 MiB**, valid measured ARM clock and all current
power/throttle booleans false. Refuse at **70°C** during the cycle. Historical
throttling bits alone do not trigger refusal. Telemetry records current and
historical decoded flags, temperature, RAM, requested/measured clock and load.
Missing mandatory telemetry refuses the trial. This RAM floor applies with the
candidate already resident; it is not permission to load a second model.

Parent monitoring targets 0.5 s between samples; local metadata/telemetry I/O
adds time (metadata calls have 3 s timeouts). The 60 s cycle deadline and thermal
cutoff are **sampled best effort**, not hard real-time guarantees. STT, inference
socket/service policy and Piper have 15 s bounds; capture settings above 10 s
are refused. A server may briefly continue a cancelled inference after socket
closure; client termination is not proof of instant Ollama GPU/CPU cancellation.
The Pi's own thermal protection stays enabled. Stop manually on unexpected heat,
noise, instability or power trouble. Do not repeat unattended on an uncooled Pi.

The parent terminates/reaps only its new child process group, including Piper,
Whisper and audio descendants, and removes its exact owned temporary directory
even on abort. SIGTERM allows normal pipeline cleanup; bounded SIGKILL is a last
resort. Hard OS power loss cannot guarantee cleanup. Reports never overwrite an
existing path. Parent output and child errors exclude exception details; raw mic
audio is temporary, and stdout/stderr (which normally include transcripts) are
discarded. If normalized STT differs from the selected public fixture, refuse
before dispatch and do not report the transcript. Only the fixed question and
its isolated generated answer are retained for human quality review.

All configured Python network targets are loopback; proxies and redirects are
disabled and Python sockets reject non-loopback addresses. No online retrieval,
online persistence or device commands run. Local binaries and installed models
remain trusted dependencies; this is not an OS network sandbox. For stronger
offline evidence, the operator may disconnect Internet connectivity without
altering firewall configuration. No `.env`, firmware, systemd, GPIO, audio-device
settings or production database writes are performed. The read-only configured
local model/voice files are used normally. No new model is installed.

## One consolidated operator procedure (uncooled Pi)

1. Use the reviewed feature checkout/venv. Arrange an exclusive, quiet microphone,
   speaker and TFT window: no other interactive request or voice loop should use
   the devices. Do not restart services or change production settings. Inspect
   hardware connections visually; use the accepted mic → Whisper → Llama → Piper
   → MAX98357A/speaker and TFT setup. Do not run the long inference A/B loop.
2. Read inventory with `python scripts/compare_offline_models.py --inventory --output /tmp/voice-inventory-01`.
   If Qwen is resident, **stop**. Wait for its natural configured idle expiry;
   do not force unload. If it never expires, defer the trial. To make Llama
   resident when **no model is resident**, an operator may separately use the
   existing guarded single text trial:
   `python scripts/compare_offline_models.py --run --model llama3.2:1b --fixture reasoning --expected-digest baf6a787fdff --output /tmp/llama-voice-warmup-01`.
   Inspect `/tmp/llama-voice-warmup-01/report.json`. This is a separately measured cold initialization, not part of the voice
   result. Do not run if another model is present; inspect its report and allow
   cooling. If residency expires before a safe cool start, defer rather than loop.
   The initializer uses a different question from the fact voice trial; shared
   prefix caching is still possible and must be reported from backend counters.
3. Confirm <55°C, no current pressure, sufficient RAM and Llama resident with
   context 4096. The tool checks these again. Choose a **new** output filename.
   Start the single physical trial:

   ```bash
   python scripts/validate_offline_voice.py --operator-live-trial --fixture fact --output /tmp/offline-voice-fact-01.json
   ```

4. When TFT enters LISTENING, wait the configured capture warm-up, then speak
   only the printed public question. No wake word is used. The tool suppresses
   normal transcript output. If display is disabled/unavailable or capture timing
   is unclear, cancel rather than capture private speech. Do not change capture
   settings to make the benchmark pass. STT disagreement causes a safe refusal.
5. Inspect status, load/cache counters, stage durations, temperature/flags and
   missing events. Listen to the naturalness/completeness and watch TFT state
   synchronization. Fill human review fields manually in a separate review copy,
   with an explanation for each pass/fail/N/A; do not set acceptance just because
   timestamps exist. Acoustic onset/end remain unverified without synchronized
   external measurement. Leave any unavailable measurement blank.
6. End the session after this first trial. Review evidence before selecting
   another fixture on another cool, operator-controlled occasion. Do not loop
   all eight on the uncooled Pi. After cooling is installed, compare Qwen and
   Llama in separate natural-expiry sessions with identical fixture, capture
   mode, audio settings, context/budget, temperature conditions and measurement
   definitions. Report cache/load/provider-warmth and resource-policy differences.

For hardware-free orchestration only:
`python scripts/validate_offline_voice.py --synthetic --output /tmp/voice-synthetic-01.json`.
It uses the fact fixture and real stream coordinator with fake capture/STT,
service deltas, TTS/preparation and player. Its timings are never Pi performance
or physical audio evidence.

## Quality review checklist

Use the eight CLI fixtures individually. Complete each report's human fields:
accuracy, completeness, relevance, hallucinations absent, grounding, memory/context,
truncation absent, natural first sentence, natural audio and TFT synchronization.
Use pass/fail/N/A **with written reasons**. The reference is a rubric, not keyword
scoring. Compare the full answer and what was actually spoken; a useful first
clause does not compensate for a wrong or unfinished explanation.

| Fixture | Required interpretation / failure examples |
|---|---|
| fact | New Delhi, north of equator, both requested facts; no unrelated claims. |
| technical | Clock frequency is cycles/s; instruction throughput depends on IPC/workload. Equating instructions and cycles fails even if fast. |
| reasoning | Two blue and one red remain: fraction 2/3, with explanation. |
| followup | Resolve seeded RAM/SSD discussion: SSD retains data without power, RAM is volatile. |
| rag | Use retrieved Lumen note: 3.3 V, no direct 5 V signal; verify `rag_used` and review actual grounding, no invented specifications. |
| memory | Retrieve Amber Finch from seeded older conversation; verify `memory_used`, no extra invented history. |
| unknown | Note contains no board serial number. Admit unknown; inventing any serial fails. |
| concise | Jupiter, one short natural complete sentence, no irrelevant detail. |

The followup/memory context is seeded and synthetic, not a measured earlier spoken
turn; genuine multi-turn voice retention needs a later operator session. Route
flags alone do not establish correct use of retrieved knowledge. Any hallucination,
incorrect core fact or materially truncated answer prevents recommending the
candidate, regardless of speed. Keep Qwen for quality until the candidate passes
all categories and representative later voice trials.

## Recommendations, in order

| Priority | Action after measurement | Expected latency opportunity | Risk / quality impact |
|---|---|---|---|
| 1 | Fit an appropriate active cooler, check power delivery using accepted hardware guidance before sustained experiments | Thermal stability, not an asserted speed gain; enables valid repeated measurements | Physical work requires separate approval; never disable guards. |
| 2 | Evaluate resident 1B across the rubric; keep 4B as reference | Largest observed text-speed opportunity, not yet a matched voice gain | High quality risk demonstrated by cycles/instructions error. No production switch now. |
| 3 | Measure capture wait and Whisper process/model initialization first | Potentially seconds before service dispatch if fixed capture dominates | VAD changes risk clipped/false speech; STT persistence adds memory/process complexity. No change without mic evidence. |
| 4 | Evaluate existing opt-in earlier complete-sentence release on the same accepted audio path | Reduces post-token buffering if that stage dominates | Premature/unnatural speech or missed context; retain safe sentence rules and listen manually. |
| 5 | Assess persistent Piper warmth and PCM preparation/queue starvation | Likely smaller after warm-up; measure before optimizing | Worker lifecycle/resource tradeoff; preserve ALSA gain/silence and TFT synchronization. |

No measured end-to-end Pi speedup, under-2-second general conversation, under-3-second
audible onset or under-8-second spoken completion is claimed here. Physical mic,
Whisper, synthesis, amplifier/speaker acoustics, wake/VAD ground truth and visible
TFT behavior remain operator validation work. Existing access-control findings
remain separate in `system-end-to-end-audit.md`; this diagnostic does not fix or
reclassify those release risks.

## Windows release evidence

Full suite: **1,069 run, 1,068 passed, one Linux-only pipe test skipped**, 103.518 s.
Focused voice/inference/model-policy set: **358 run, 357 passed, one skipped**,
13.245 s. All **15 new diagnostic tests passed**, including real service/policy
execution with fake devices. Three final separate-process synthetic smoke trials
completed with all required events and valid timing order. No physical providers
or Pi inference were exercised. The 555 protected/unrelated files matched both
their original hashes and inventory. See `offline-voice-windows-results.json`
for source hashes and limitations, and `offline-voice-windows-tests.log` for the
complete regression output.

One earlier Windows standalone synthetic attempt aborted after first WAV return
with a generic cycle failure; its cause was not identified. Immediate and
instrumented rechecks, followed by three independent runs, passed. Earlier
system-temp sandbox paths also produced permission errors; final validation used
an owned workspace TEMP/TMP directory. These are diagnostic/environment
limitations, not Pi performance evidence. An aborted report must be investigated
rather than counted as a successful sample or used to justify repeated live
inference on the uncooled Pi.
