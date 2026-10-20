# Offline voice pipeline latency audit

Audited branch: `codex/offline-model-selection`. Source baseline:
`80f23f73105505e719afce0cc3b926a5d963d678`. This milestone changes this document only.
No production code, configuration, question bank, model selection, hardware, services or
unrelated files were changed. No inference, audio-device access, model download, deployment,
private conversation/database query or Pi connection was performed. Heavy and physical
voice tests remain paused until the fan is safely installed.

## Findings and decision

The strongest measured bottleneck is local LLM work: cold loading, Qwen prompt evaluation,
and generating enough words for a useful first speech unit. Capture endpointing adds a
separate delay before Whisper. Whisper then launches a fresh executable with its model for
each transcription. After first text, the pipeline still needs a safe speech boundary,
complete Piper WAV, output conversion and device submission before speech can be heard.
The configuration also deliberately inserts initial playback silence.

Piper model reuse, streamed LLM output, bounded producer/consumer queues and one ALSA player
per response **already exist**. Reimplementing those features would not establish a gain.
Resource report collection on a cache miss synchronously probes ancillary network/service
health; this is a credible tail-latency source with unknown Pi cost. TFT writes are
synchronous but bounded and do not wait for firmware acknowledgment.

Sub-two-second first **audible answer after human utterance end** is not supported by the
current evidence. Historical Qwen warm first-text/prefill delay alone exceeds two seconds.
A smaller warm model might permit selected cases, but the fast 1B result used substantial
prompt caching and gave an incorrect answer. Qwen remains the default. Instrumentation
should come first; all performance changes below are proposals for later approval.

## Evidence inventory

The committed documents summarize operator-supplied observations, not independently
reproduced device results. No raw per-trial Pi timing JSON is committed under `benchmarks/`;
its three tracked artifacts are the quality bank/review files. Newer timings supplied with
this request are identified separately. Do not add independent samples into a fabricated
microphone-to-speaker figure.

| Observation | Provenance | Supported conclusion and limit |
| --- | --- | --- |
| Llama 1B meaningful text 312.767 ms; full text 4.395 s; load 1.36 ms; prefill 308.8 ms; about 9 tokens/s; 120/136 prompt tokens cached | [Committed voice summary](offline-voice-validation.md#decision-and-evidence), [3B evaluation](llama3b-evaluation.md#what-the-supplied-1b-evidence-does-and-does-not-show) | Distinct-question resident sample with substantial cache reuse: 16 derived uncached tokens assuming backend total-count semantics. Answer confused clock cycles with instructions: quality failure. No speech timing. |
| Llama 3B short cold answer about 11.52 s, including about 8.90 s loading | Operator observation supplied with this audit; matching raw report absent from inspected tracked artifacts | Loading dominates that sample. Subtracting load does not establish warm latency. OS page cache and other conditions may affect loading; cause unknown. |
| Earlier 3B short answer 19.328 s, load 16.7035 s, meaningful text 17.948 s | [Committed failure analysis](cold-warm-failure-analysis.md#what-the-supplied-result-establishes) | Different trial from 11.52 s. Material cold-load variation; no STT/Piper/acoustic timing. |
| 3B about 6.53 generated tokens/s for a short answer | Operator evidence in earlier request, reiterated in this audit; raw report unavailable | Workload-specific decode rate, not guaranteed sustained throughput or speech rate. |
| Qwen warm first text about 5–5.5 s, about 2.7–4 generated tokens/s, full normal responses about 18–26 s | [Committed voice summary](offline-voice-validation.md#decision-and-evidence) | Historical Pi observations, not matched model/voice A/B trials. |
| Qwen 103–115 prompt tokens, 21–23 prompt tokens/s, 4.8–5.3 s prefill; warm load 1–2 ms; Piper ready 0.01–0.02 ms | [Committed inference audit](inference-latency-optimization.md#evidence-versus-hypotheses) | Prefill can dominate with modest context. Piper readiness is not synthesis time. Does not prove a cache defect. |
| Nine earlier distinct Qwen queries reported zero prompt-cache hits | [Committed inference audit](inference-latency-optimization.md#evidence-versus-hypotheses) | Do not assume the stable safety prefix is effectively reused. Does not prove every backend/version lacks caching, nor justify treating absent counters as zero. |
| Synthetic voice orchestration about 10.84 ms | Operator observation supplied with this audit; [Windows results](offline-voice-windows-results.json) record successful rechecks, not that exact duration | [Synthetic harness](../scripts/validate_offline_voice.py#L241) replaces STT/LLM/Piper/player. No actual engine/audio latency. |
| Cold failure 20.03 s, empty answer, reported expiry, warm not started; temperatures 42.5–44.1 C, final 44.4 C, current flags clear | [Committed failure analysis](cold-warm-failure-analysis.md) | Old parent masked child errors with residency assertion. Timing is consistent with old socket timeout, not proof of cause or normal expiry. Repair is already in this baseline. |
| Warm-up 1 s; fixed capture 5 s; VAD end quiet 0.8 s; playback silence 0.7 s | [Settings](../config/settings.py#L295), [loop config](../scripts/voice_loop.py#L125) | Code defaults, not measured Pi delays or verified deployed settings. Hands-free differs. |

## Source-grounded execution map

The physical companion uses `scripts/voice_loop.py`; browser uploads have separate paths.
The module calls `django.setup()`. `main()` validates config, constructs the display and
**warms Piper before prompting for a request**. Django startup, initial display settling and
successful Piper loading are outside the ordinary per-cycle timer. Direct callers of
`run_voice_cycle()` and diagnostic child processes need not receive this startup warm-up.

| Boundary | Source / function | Serial work and overlap |
| --- | --- | --- |
| Startup | [loop `main`](../scripts/voice_loop.py#L1733), [voice factory](../assistant/voice/factory.py#L44) | Providers/config/display; Piper warm completes before capture. No LLM warm call in this entry point. |
| Optional wake | [loop `run_hands_free`](../scripts/voice_loop.py#L1686), `OpenWakeWordProvider.wait_for_wake_word` | Wake provider warmed once; releases mic before request capture. No wake listener during inference/playback, no barge-in. Wake/cooldown is outside cycle timing. |
| Cycle begins | [`_run_voice_cycle`](../scripts/voice_loop.py#L1550) | Performance clock, temporary directory, meter and status setup. Interactive loop retains conversation ID. |
| Capture | [`record_audio` / `record_audio_alsa`](../scripts/voice_loop.py#L302), [`record_audio_with_capture_vad`](../scripts/voice_loop.py#L400), [hands-free capture](../assistant/voice/hands_free.py#L177) | Entire capture returns before normalization/STT. No transcription overlap with capture. |
| Conversion | [`normalize_audio`](../scripts/voice_loop.py#L589) | Synchronous ffmpeg, temporary 16 kHz mono signed-16-bit PCM WAV. Live ALSA extracts LEFT before mono conversion. |
| Transcription | [`WhisperCppProvider.transcribe`](../assistant/voice/stt.py#L70) | Cached provider object launches CLI with `-m`, `-f`, `-t`, `-nt` per call. No resident STT worker/incremental transcript. |
| Plan / status | [`plan_voice_response`](../assistant/policy.py#L404), `_run_voice_cycle` | Regex-based policy; THINKING/query display calls. Non-action planning already avoids unused capability DB probe. |
| Service starts | [`process_message_stream`](../assistant/services.py#L122) | Generator creation is lazy; consumption inside `speak_stream_response` triggers preparation. Streaming clock includes this preparation. |
| Preparation | [`_prepare_message`](../assistant/services.py#L221) | Conversation get/create, resource plan, recent history, user-row write, capability registry, routing/RAG, optional memory, instruction composition, engine lookup: serial before LLM. Direct grounded/action/refusal paths can skip LLM. |
| LLM | [`generate_stream`](../assistant/ai_engine/local.py#L202), `_build_chat_request` | System/history/user payload to configured Ollama. Load/prefill precede useful text. Deltas forwarded immediately, no user-inference retry. |
| Text producer | [`speak_stream_response`](../scripts/voice_loop.py#L1031), [`StreamingSpeechBuffer`](../scripts/voice_loop.py#L746) | Main thread filters leading status notices, prints text, packs safe units and enqueues them. |
| Preparer | `speak_stream_response.preparation_worker/prepare_audio` | One background worker: full unit WAV synthesis, save, native conversion and allowed edge trim, enqueue PCM. Overlaps LLM and earlier playback. |
| Piper process | [`PiperProvider._synthesize`](../assistant/voice/tts.py#L86), [`worker_main`](../assistant/voice/piper_worker.py#L20) | Spawned CPU voice reused across successful cycles/chunks, serialized by lock; IPC helper enables cancellation polling. Full WAV/base64 reply, no Piper-to-player PCM streaming. |
| Player | `speak_stream_response.playback_worker` | Takes first ready unit immediately, callbacks then startup. One ALSA process per response; overlaps later synthesis. No two-chunk fill requirement. |
| First buffer | [`PersistentAlsaPlayer.start/write_silence/write_file`](../assistant/voice/alsa_stream.py#L162) | Starts aplay, submits initial silence, then prepared speech PCM using bounded nonblocking writes. Accepted bytes are not DAC/acoustic onset. |
| Completion | service persistence; loop/player finally blocks | AI row and conversation update precede service done; short final units can wait for done. Drain, reap, thread/file cleanup, idle state. Piper remains after success, closes on failure/exit. |

```text
Serial: capture -> normalization -> Whisper -> service/resource/retrieval preparation

main thread: Ollama deltas -> notice filter -> speech buffer -> text queue (capacity 2)
                                                                    |
preparer:           resident Piper -> complete WAV -> conversion -> PCM queue (capacity 2)
                                                                    |
player:                           aplay start -> initial silence -> speech PCM -> drain
```

Queues bound memory/backpressure, not startup fill. Full queues can block the producer and
pause generator consumption. Python overlap does not eliminate CPU/RAM contention. Engine
client wall timers may include yield/backpressure time; backend durations are distinct.

### Capture modes and delay before Whisper

- **Fixed Enter-to-record:** `_record_audio_command` starts parecord/arecord, sleeps warm-up
  (1 s default), cues speech, then waits the fixed window (5 s) and terminates/reaps. The
  WAV includes warm-up samples; they are not explicitly removed. Programmed time is roughly
  warm-up plus window, excluding spawn/cleanup. Post-utterance delay is the **remaining**
  window, not always five seconds. No early endpoint.
- **Ordinary capture VAD:** raw pipe starts, then warm-up sleeps before pipe reads. Adaptive
  VAD uses 20 ms chunks, 0.5 s calibration, 60 ms start confirmation, 300 ms minimum voiced
  duration, 0.3 s pre-roll, 5 s start timeout including calibration, 10 s maximum, 0.8 s end
  quiet. Continuation evidence can briefly defer endpoint; maximum stays binding. These
  frame-duration rules are not acoustic timestamps. Unread-pipe warm-up can queue old audio
  or stall a high-bandwidth native source; actual backlog/XRUN/endpoint impact is unknown.
  ALSA has a watchdog; ordinary Pulse reads do not use that ALSA watchdog.
- **Hands-free:** `NativeRequestReader` actively drains exact 0.25 s warm-up frames,
  calibrates 0.25 s, then retains **all** request audio until endpoint or 6 s maximum.
  LEFT-channel RMS evidence and 0.9 s end quiet shorten capture; there is no admission/start
  gate discarding request audio. No evidence can mean the whole maximum. Selector reads have
  a wall deadline. Deprecated `VOICE_HANDS_FREE_VAD_MAX_START_THRESHOLD` does not control
  this new endpoint detector.
- **Injection:** audio-file skips capture but still normalizes/transcribes; text modes skip
  capture/normalization/STT. Their timings cannot validate omitted stages.

Warm-up precedes the speech cue: relevant to activation and clipping, but not automatically
post-utterance delay. End quiet and leftover fixed-window time are directly on that path.

### Prompts, retrieval, resource policies and configuration

`assistant/services.py::_build_generation_instruction` composes voice safety once;
`_generation_kwargs` supplies an empty `system_prompt` to avoid a duplicate text-chat prompt.
Voice brief/normal/detailed ceilings default to 32/64/192 tokens, clamped by resource plan
and relevant RAG cap. Healthy voice headroom 384 is not an instruction to generate 384
ordinary-answer tokens. Reduced/minimal/unknown caps remain binding. Lowering a cap does not
speed up first text automatically and may truncate the answer.

Ordinary voice queries already omit unrelated history and implicit document retrieval.
Reference questions retain up to two recent messages by default, bounded by service/policy;
explicit recall permits long-term USER memory. [Memory retrieval](../conversations/memory.py#L64)
scans latest 500 eligible older messages, projects up to 4,000 chars per candidate, transfers
at most 200 candidates, then selects up to two within a default 400-character context budget.
Explicit/local-intent RAG uses SQL filtering and [lexical scoring](../knowledge_base/retrieval.py#L65),
not a second embedding/LLM model. Batches are 200, but total matching candidate count is not
capped. Top-k 2 / context 1,200 characters do not bound SQL scan work. Capability registry
may check RAG availability even if retrieval is later skipped. Grounding must remain intact.

[Request resource policy](../assistant/execution_policy.py#L158) reuses the Resource Manager
cache. On a miss, [snapshot collection](../system/control_plane/resource_manager.py#L118)
serially collects resources, network, service health and provisioning DB state. CPU sampling
sleeps 0.1 s when supported; vcgencmd is bounded at 1 s, network commands at 1.5 s each.
[Service health](../system/control_plane/health.py#L117) serially checks Ollama, SearXNG and
MQTT with 1 s default timeouts each (maximum 2 s). Those are failure ceilings, not measured
normal costs. SearXNG/MQTT probes can occur for an offline informational query. Cache duration
is 5 s; no explicit shared Django cache is declared, so default local-memory cache is
process-local. Another process's dashboard probes do not guarantee a voice cache hit.
Long turns can outlast the 5 s TTL; actual per-turn hit rate still needs measurement.

Policy does not sleep waiting for cooling or auto-select a smaller model. ECO reduces
budgets; PROTECTIVE normally blocks local generation. The degraded-report fallback differs
and does not establish live safety. Production request-scoped policy is not the diagnostic
continuous monitor. Source thresholds 65/75/80 C must not be mistaken for the stricter
70 C trial abort/start gates; no threshold or safeguard change is proposed.

Configuration precedence, inspected statically without reading secrets:

1. Launcher/service process environment comes first. Committed Django/warm unit templates
   include EnvironmentFile; manual voice-loop environment is independent of running Gunicorn.
2. `config/settings.py` calls `load_dotenv()` at import. Installed python-dotenv source
   (`venv/Lib/site-packages/dotenv/main.py::load_dotenv`) defaults to `override=False`:
   existing environment wins, then discovered dotenv, then each settings expression's
   default. Discovery can walk ancestors. Project `.env` is absent here; Pi environment,
   ancestor dotenv contents and enabled service overrides were not inspected.
3. `.env.example` is not automatically loaded. Its illustrative values include Qwen, Pulse,
   fixed capture and these durations/budgets. Code engine defaults are mock/mock/mock;
   real use requires local/whisper_cpp/piper configuration. This does not imply Pi mocks.
   Some settings, including resource thresholds/cache seconds, are assigned constants rather
   than environment lookups; similarly named environment variables do not override those assignments.
4. Voice/hands-free config and provider factories parse Django settings. Provider/engine
   singletons are per-process: editing a file does not update an existing instance.
   CLI flags select input mode, not all inference options.
5. ResponsePlan selects style/history/output cap; service combines execution-plan/RAG caps.
   Factory explicitly passes settings to Ollama; supplied request model/token/timeout
   overrides win where applicable. Execution policy uses one configured model. Deprecated
   lightweight/RAG model settings do not select extra models.
6. Production context/keep-alive/thinking defaults are 4096/24h/off; optional CPU options
   unset. Policy timeout defaults from OLLAMA_TIMEOUT=120 s and clamps to 1–300 s.
   Diagnostic isolated children override model/options/timeouts: repaired 3B comparison
   sockets 25/20 s cold/warm, request monitor 30 s, session 60 s. These are not production
   timeout settings, and socket inactivity is not an absolute full-stream deadline.

Future traces should capture only allowlisted effective non-secret settings and provenance:
engine/model/digest, context/options, input mode, policy and context sizes, audio formats and
durations, cache age. Omit private prompts, transcripts, knowledge, SSIDs, credentials and
hardware identifiers. No environment dump or conversation export is needed.

### Speech buffering, output, status and errors

`StreamingSpeechBuffer.feed` defaults: first unit minimum 60 / target 100 chars, subsequent
target 180. Empty `VOICE_STREAM_FIRST_SENTENCE_MIN_CHARS` means complete short sentences can
wait for more text or final flush. Existing opt-in value 30 releases qualifying complete
sentences without reducing clause/word minima. Safe clause/word fallbacks already exist:
this is not always a complete-sentence wait. Numeric groups, URLs/links, abbreviations and
paired punctuation are protected. LeadingResourceNoticeFilter only holds a possible exact
resource-notice prefix; status filler is not an answer. Actual filter delay is unmeasured.

Piper already loads once at normal loop startup; successful cycles reuse its child.
`_synthesize` records reuse, readiness and exchange metrics. Exchange includes synthesis,
whole-WAV assembly, serialization and IPC; it is not pure inference time. Each unit is a
complete WAV, then complete prepared file; concurrent Piper jobs are deliberately absent.
Calls sharing a cached provider can wait on its lock. Reusing Piper again is not a new win.

ALSA ffmpeg runs per unit for gain/limiter and 48 kHz stereo S32_LE. Files are written and
allowed edges trimmed; first-unit natural leading audio is deliberately retained. Input
LEFT-only extraction remains necessary. The player submits injected leading silence once
per response, 0.7 s source default (example comments suggest 0.5 s for ALSA). Later gaps aim
for 0.12 s minus retained edges: continuity, not initial onset. Persistent aplay means across
chunks within a response, not across requests. Pulse uses paplay per unit and initial WAV
silence. None measures actual acoustic onset. Do not remove phoneme protection, limiting or
native conversion merely to reduce buffers.

[TFT transport](../assistant/display/serial_display.py#L71) uses persistent serial, synchronous
write/RLock, 50 ms write timeout, no flush or ACK wait. Constructor settling defaults to
1.5 s before ordinary requests; reconnect settling skips writes instead of sleeping.
Reconnect attempts occur on later updates after 5 s. Port open/lock contention has no
separate timing guarantee from the write timeout. [Audio meter](../assistant/display/audio_level.py#L16)
coalesces at 80 ms, unchanged-level keepalive 0.8 s. Callbacks execute on producer/player/
capture threads; trouble may cost time. No evidence makes TFT a main seconds-scale delay.
Successful serial writes do not establish visible pixels or acknowledged commands.

Stream failure sets the stop event, closes service iterator and joins workers. Cancellable
ffmpeg/paplay/Piper poll cancellation and terminate/kill/reap owned children; ALSA writes/
drain have deadlines. Capture/STT cleanup is bounded, but normalization and Whisper use
synchronous subprocess.run rather than streaming-audio's cancellation event. A blocking
Ollama read can delay propagation until read/timeout returns: main-loop generator close
cannot run while that loop is blocked in the read. Production socket timeout is not the
benchmark parent's absolute child deadline. Normal successful timing summaries can be
absent on errors, making partial instrumentation important.

No normal user-inference retry exists. Separate [warm management command](../assistant/management/commands/warm_ai_model.py#L28)
polls API readiness and retries one specific runner-start failure up to three warm attempts
under a shared budget. Its [oneshot service](../deployment/systemd/smart-ai-companion-model-warm.service)
declares restart-on-failure; actual installed/enabled status is unknown. This is startup
behavior, not a per-answer retry. Do not invoke it during the pause. Lowering timeouts
shortens failure waits, not successful generation.

[Browser endpoints](../assistant/views.py#L409) differ: transcription-only upload/conversion,
chat streaming and complete-WAV synthesis. Compatibility VoiceAPIView waits for full service
response then full TTS before returning base64. Browser upload/playback/network need separate
measurement; they are not the companion ALSA path ranked here.

## Existing timestamps and missing observability

| Field / instrumentation | Actual measured boundary | Limitation |
| --- | --- | --- |
| Loop record / normalize / stt | Capture including setup/waits/cleanup; conversion; entire Whisper CLI call | No true human utterance-end clock, Whisper load split or capture subphase timings. Provider lookup is outside timed transcribe call. |
| Loop ai_ttft | First non-whitespace presented answer from streaming entry after notice filter | Includes lazy service preparation; punctuation can count; not meaningful text or grammar. |
| Engine ttft_ms | First nonempty content from before urlopen | Whitespace/punctuation can count; excludes service prep; reported on completed stream. |
| Diagnostic llm_first_text | First alphanumeric engine delta | Operational criterion, not correctness/audio. |
| first_tts_text_ready / first_tts_unit_ready / first_tts_chars | First emitted unit before enqueue; length | Clause can precede complete sentence; queue wait/synthesis start not split. |
| Loop tts / audio_prepare | Accumulated preparer call durations | Overlap inference/playback; cannot sum into end-to-end latency. |
| Piper worker_reused / ready_ms / exchange_ms | Readiness and request/reply exchange | Provider lock wait outside ready_ms; exchange conflates synthesis/encoding/IPC. |
| first_audio_ready | First full WAV synthesized and output prepared | Before player queue/device startup. |
| first_speech / ai_to_first_speech | Before aplay startup and initial-silence submission | Not first accepted speech buffer or audible onset. Cycle-relative metric includes user speech/capture. |
| playback_starvation_ms / max / chunks | Ready-queue misses after prior units | Not device XRUN, sample-accurate underflow or initial latency. |
| ai_total / ai / generation_ms / execution_ms | Client stream/service elapsed, ai_total before final flush | Includes overlapping/yield backpressure; persistence before done can delay short final audio. |
| playback / total | ALSA lifetime including queue waits / cycle including temp cleanup | Not additive with tts/inference; normal summary may not print on failure. |
| Service policy_ms / history_ms / routing_ms / rag_ms / memory_ms / online_ms and prompt sizes | Preparation steps and counts | rag_ms nested in routing; history_ms is in metadata but omitted from safe performance-log allowlist. DB writes/registry have no dedicated timing. |
| Resource report collection_ms / policy_ms / total_ms | Cache-miss construction | Cached report retains old construction timing; no voice cache-hit/age/per-probe attribution. |
| Backend load/prompt/decode/counts/cache counters | Ollama completion metrics | Not voice durations; missing counters unknown; residency does not prove caching. |
| Diagnostic capture/STT start/end, dispatch, inference_request_ready, sentence, Piper WAV, PCM/drain/cycle | [Trace / observe_pipeline](../scripts/validate_offline_voice.py#L51) local wrappers | Trace writes add overhead. Speech-file PCM acceptance excludes injected silence but may include silence within WAV; not acoustic onset. |

Missing measurements to add in a later instrumentation-only change:

- Synchronized last human phoneme and first audible answer phoneme, device startup and idle
  noise; cannot derive this from aplay drain or unsynchronized phone timing.
- Capture open/first frame, drained warm-up/calibration end, cue/ready, detected onset/offset,
  frame versus processing-wall timestamps, stop/reap/save and actual file duration per mode.
- Input ffmpeg spawn/conversion/write; Whisper spawn/model-load/decode/cleanup using supported
  timing output with transcript-bearing stderr excluded.
- Resource hit/miss/age and each probe; DB/registry operations and dataset/candidate sizes;
  installed-model residency/context/allocation without submitting inference.
- First grammatical sentence versus first clause, queue wait, Piper lock/reuse/load,
  synthesis-first-sample/full-WAV, IPC, conversion/trim/file writes and ready-queue wait.
- aplay open, silence write, first speech-file write and first non-silent frame, device
  delay/XRUN, drain; TFT callback/lock/open/write time without payloads.
- Correlated temperature, current flags, frequency, available RAM and effective policy;
  original errors and partial checkpoints on every abort using one monotonic origin.

## Prioritized optimization backlog

Ranks estimate material opportunity conditional on the workload, not a measured complete
voice profile. Confidence is confidence in the source mechanism, not a promised speedup.

| Rank | Candidate / evidence | Impact and confidence | Effort / regression risk |
| --- | --- | --- | --- |
| B1 | Model cold load, Qwen prefill, useful-word decoding; seconds-scale text evidence | High potential when cold/prefill-bound; high source confidence; gain unknown | Medium / high for runtime/model changes |
| B2 | Fixed-window remainder or 0.8/0.9 s endpoint quiet before STT | Material conditional opportunity; high source confidence; actual gain unknown | Low–medium / medium clipping risk |
| B3 | Whisper fresh executable/model initialization, full-file STT | Potentially material; high fresh-process confidence, Pi cost unknown | High / high lifecycle/RAM risk |
| B4 | First-unit thresholds and complete first-unit synthesis | Potentially material at slow decoding; high source confidence; answer-dependent | Low for existing option / medium prosody risk |
| B5 | Leading silence and device startup | Explicit 0.7 s inserted audio by default; actual onset gain unknown | Low tuning effort / high clipping risk if removed |
| B6 | Resource snapshot miss, serial ancillary service/network checks | Could reach seconds on failing probes; normal cost unknown | Medium / high stale-safety risk |
| B7 | Per-unit WAV/base64/files/ffmpeg | Real work; materiality unknown | Medium–high / high format/audio risk |
| B8 | Remaining SQL/retrieval/setup/persistence | Already optimized in several paths; dataset-dependent unknown | Medium / medium–high grounding/state risk |
| B9 | Synchronous TFT/terminal callbacks | Bounded writes/coalescing already exist; contribution unknown | Medium / medium ordering/feedback risk |
| B10 | CPU contention and cancellation tail | Plausible scheduling contention, not established bottleneck | Medium–high / high thermal/stall risk |

### B1 — One-model residency and measured prefill diagnosis

**Source/current:** `assistant/ai_engine/local.py::generate_stream`, `_build_chat_request`,
`warm_model`, `warm_chat_prefix`; `assistant/execution_policy.py::build_execution_plan`;
`assistant/management/commands/warm_ai_model.py::Command.handle`. One configured Qwen model,
24h request keep-alive, existing warm/prefix APIs. Retention can fail due to pressure,
other clients or daemon lifecycle; prefix warming does not establish distinct-question caching.

**Change/benefit:** first add identity/load/cache observations, exclude conflicting residents
in trials. Later test supported runtime options or installed smaller candidates in isolation.
No second resident model or periodic warm loop. Magnitude **unknown**: reported 8.90/16.70 s
cold loads show scale, not a measured removable delta. Warm Qwen still has prefill cost.
**Quality/reliability/audio risks:** persistent model/KV competes with resident STT/Piper;
smaller models can lose reasoning, grounded recall and hallucination resistance; CPU contention
can starve audio. Safety/context/truncation protections must stay.
**Windows coverage:** single-model/execution policy, payload/cache-unknown, timeout/cancel,
cold-failure and RAG/memory tests. **Pi validation:** after cooling, exact digest/context/RAM,
cache counters for new questions, manually reviewed quality subsets and actual voice pipeline.
**Rollback/defaults:** request-local trials, revert to baseline options; no eviction/restart.
Qwen unchanged now; model, keep-alive or backend default changes require separate approval.

### B2 — Capture endpointing and ordinary-VAD warm-up drain

**Source/current:** `scripts/voice_loop.py::_record_audio_command`, `record_audio_with_capture_vad`;
`assistant/voice/capture_vad.py::AdaptiveCaptureVAD.feed`; `assistant/voice/hands_free.py::record_audio_hands_free/NativeRequestReader`.
Fixed capture waits the remainder; ordinary VAD sleeps unread; hands-free already drains
warm-up and retains all request audio without a start gate.
**Change/benefit:** trace frame/wall boundaries; later consider existing VAD for fixed-mode
use, actively draining ordinary-VAD warm-up, and evidence-based endpoint tuning. Do not
transplant a start gate into hands-free. Gain **unknown**; remainder varies and configured
quiet durations are not measured acoustic lag. Pre-cue warm-up reduction alone is not a
post-utterance improvement.
**Quality/reliability/audio risks:** lost quiet consonants, miscalibration, false endpoints
on pauses/noise, LEFT-only native S32_LE, stalled pipe/mic ownership, missing speech evidence.
**Windows coverage:** existing capture VAD/hands-free suites; exact/partial frames, short bursts,
no speech, bounds, watchdog, cancel/reap. **Pi validation:** after fan, noisy/quiet/soft/paused
speech, wake-to-request handoff, compare transcripts and first/last phonemes.
**Rollback/defaults:** retain current durations/mode as baseline, disabled flag for any new
algorithm; revert on clipping. Enabling VAD/timing changes affects production only on later approval.

### B3 — Resident Whisper feasibility; preserve already resident Piper

**Source/current:** `assistant/voice/stt.py::WhisperCppProvider.transcribe`,
`assistant/voice/factory.py::get_stt_provider`. Fresh model-taking CLI per transcription;
provider caching does not keep its model resident. OS caching may reduce initialization cost.
**Change/benefit:** measure load versus decode, then prototype one isolated resident STT
adapter only if the installed whisper.cpp version exposes a compatible supported interface.
This repository does not establish a particular server protocol's availability. Retain
explicit CLI selection, without automatic second transcription on failure. Gain **unknown**:
no Pi startup/decode split. Piper persistence is already implemented; inspect reuse metrics.
**Quality/reliability/audio risks:** joint resident RAM, leaks/stale replies, crashes,
model/thread/VAD equivalence, deadlines; preserve 16 kHz mono PCM input and STT accuracy.
**Windows coverage:** protocol IDs/bounds, one load per worker, serial calls, malformed input,
crash/cancel/timeout/shutdown and explicit fallback selection. **Pi validation:** cooled
identical public WAVs, transcription error rate, cold/warm time and peak RAM under full pipeline.
**Rollback/defaults:** disabled adapter/CLI baseline; stop only owned worker. STT default
change is a separate decision. No new Piper lifetime design is needed to obtain reuse.

### B4 — Earlier grammatically complete first speech

**Source/current:** `scripts/voice_loop.py::StreamingSpeechBuffer.feed/_first_fallback_boundary`,
`speak_stream_response`; `assistant/policy.py::plan_voice_response`. 60/100 first-unit packing
may hold short complete sentences until later text/done; safe clause fallback already exists.
**Change/benefit:** prepare a later opt-in trial of existing
`VOICE_STREAM_FIRST_SENTENCE_MIN_CHARS=30`. Preserve 60-character clause minimum, punctuation,
explicit detail/format priority, safety/grounding. Magnitude **unknown**: only helps when a
qualifying complete sentence arrives earlier; final one-sentence answers may gain little.
Do not lower token caps to produce an incomplete fast answer.
**Quality/reliability/audio risks:** short-unit prosody, more synthesis calls/gaps, numbers,
links/abbreviations/quotes, dangling grammar, speech already heard before a later model error.
**Windows coverage:** `tests_inference_latency.FirstSentenceLatencyTests`, voice-stream,
exact spoken-text reconstruction, notice filter, policy/RAG/recall/truncation. **Pi validation:**
after cooling, brief/normal/detailed/new/RAG/memory listening comparisons with sentence/PCM/
acoustic timestamps and factual review. **Rollback/defaults:** empty option restores packing;
can prepare tests with no new production code. Enabling option is a later behavior change.

### B5 — Initial playback silence against physical startup

**Source/current:** `scripts/voice_loop.py::speak_stream_response.playback_worker`,
`prepend_leading_silence`; `assistant/voice/alsa_stream.py::PersistentAlsaPlayer.start/write_silence`.
Injected silence precedes first unit; natural first-unit leading audio is protected.
**Change/benefit:** after cooling, record startup/first phoneme and trial one smaller silence
value. Cross-request persistent aplay is a separate architecture change. Reduction changes
submitted PCM duration exactly; **audible improvement unknown** because device buffering/
settling can absorb it. 0.7 s source default is that segment's opportunity ceiling, not a win.
**Quality/reliability/audio risks:** clipped first word, amplifier clicks/pops, distortion,
format mismatch; long-lived device ownership may block capture/wake/other users.
**Windows coverage:** exact silence frames, first-chunk edges, one player/response, Pulse
compatibility, cancel/broken pipe/drain. **Pi validation:** mandatory first-word listening after
idle/reopen/repeated cycles and mic handoff. **Rollback/defaults:** restore baseline silence/
lifecycle immediately on clipping. No routing, ALSA device or system changes; later enabling
new duration affects production behavior.

### B6 — Critical safety sampling versus ancillary health work

**Source/current:** `assistant/execution_policy.py::get_ai_execution_plan`;
`system/control_plane/resource_manager.py::get_resource_manager_report/collect_resource_snapshot`;
`system/control_plane/health.py::get_service_health`. Cache miss serializes ancillary checks
before an offline request can infer.
**Change/benefit:** measure hit/miss/age/probe latency. Later consider parallel bounded
ancillary checks or background snapshot with strict safety-age rules while retaining fresh
thermal/current flags/RAM and authoritative Ollama readiness. Do not blindly lengthen TTL.
Gain **unknown**; timeout ceilings can stack in failure cases, not evidence of normal delay.
**Quality/reliability/audio risks:** stale-safe decisions, incorrectly enabled generation,
thread storms/races, audio CPU competition. **Windows coverage:** resource-manager/execution
policy, TTL, concurrency/single-refresh, unavailable telemetry, thermal/power transition,
slow/dead service and identical block decisions. **Pi validation:** cooled offline/service-outage
and hit/miss tails, safety response and audio starvation. **Rollback/defaults:** collection
strategy flag, restore synchronous path on divergence. Thresholds/TTL stay unchanged; later
background scheduling would change production execution, not safety requirements.

### B7 — Validated conversion bypass and later PCM streaming

**Source/current:** `scripts/voice_loop.py::normalize_audio`,
`speak_stream_response.prepare_audio`, `prepare_alsa_playback`; `PiperProvider._exchange`;
`assistant/voice/piper_worker.py::worker_main`. All non-text inputs normalized, even compliant
WAVs; each TTS unit waits for full WAV/base64 reply and complete prepared PCM file.
**Change/benefit:** first test strict header/content validation for already compliant 16 kHz
mono signed-16-bit WAV bypass. Later separately prototype reusable conversion or framed
Piper PCM streaming only if profiling warrants it. Magnitude **unknown**. Native stereo
48 kHz S32_LE and browser compressed uploads still require conversion. Small JSON/file tweaks
have no measured priority over inference/endpoint work.
**Quality/reliability/audio risks:** invalid/truncated WAV, rate/channel/sign errors, loss of
LEFT-only input, changed gain/limiter, deadlocks/unbounded buffering/cancel, audible partial
synthesis before an eventual error. **Windows coverage:** valid/invalid format matrix, exact
frames/byte equivalence, IPC bounds/partial writes, queue cancellation, trim/gain/drain.
**Pi validation:** after fan, real voice/device formats, intelligibility, clipping, first phoneme
and gaps. **Rollback/defaults:** opt-in fast path with existing ffmpeg/full-WAV path retained;
restore previous conversion/IPC without device edits. Future enabled implementation changes
execution/audio behavior; none changes here.

### B8 — Measure SQL and persistence without losing grounding

**Source/current:** `assistant/services.py::_prepare_message/_persist_generated_response`;
`knowledge_base/retrieval.py::LexicalRetriever.retrieve`;
`conversations/memory.py::LexicalConversationMemoryRetriever.retrieve`. Serial applicable
retrieval/writes, AI persistence before done; already bounded/projection-optimized in places.
**Change/benefit:** per-operation/cardinality/query-plan tracing before index or immutable
document-version cache changes. Preserve rank/ties, expiry, permissions and memory corrections.
Persistence reordering only if shown to hold short final audio materially. Gain **unknown**.
**Quality/reliability/audio risks:** stale/deleted facts, changed relevance, incorrect recall,
lost rows, spoken content without canonical state; DB locks can delay producer/audio work.
**Windows coverage:** retrieval projection/ranking, RAG/memory/expiry, route/conversation isolation,
failure atomicity. **Pi validation:** cooled representative public datasets and query plans,
RAG/recall review. **Rollback/defaults:** optional cache/index flag and reversible migrations
if later needed; restore queries. No context-budget or retrieval-default change; never skip
required grounding to win timing.

### B9 — Bounded display/terminal observers, only if measured

**Source/current:** `scripts/voice_loop.py::_set_display_*`,
`speak_stream_response.present/caption`; `assistant/display/serial_display.py::_send`;
`assistant/display/audio_level.py::AudioLevelMeter.feed`. Synchronous callbacks/flush prints;
query/state/caption before first output; bounded serial writes and coalescing already present.
**Change/benefit:** time callbacks/terminal first; if material, one bounded coalescing display
queue preserving state/query/caption order. Gain **unknown**, likely below measured model costs.
Do not claim removal of 1.5 s per response: settling is already startup-only.
**Quality/reliability/audio risks:** stale SPEAKING/ERROR, missed captions/levels, competing
serial ownership; blocking/extra threads can starve audio. **Windows coverage:** display/
audio-level/stream ordering, failure isolation, overflow/reconnect/shutdown. **Pi validation:**
TFT ordering and audio continuity after cooling. **Rollback/defaults:** disable dispatcher,
restore synchronous provider; no firmware/protocol/wiring edits. Later scheduling change must
preserve feedback semantics; no present production-default change.

### B10 — CPU contention and cancellation tail

**Source/current:** `LocalLLMEngine.generate_stream/_build_chat_request`;
`assistant/voice/processes.py::run_cancellable`; `PiperProvider._check`, player `check`.
Ollama CPU options unset by default; Whisper threads 4. Overlapping workloads compete;
HTTP blocking read can defer cancellation.
**Change/benefit:** after cooling, one installed-backend-supported CPU option trial at a time,
record reloads and concurrent Piper cost. More threads/batch is not assumed faster. Separately
consider cancellable transport/whole-cycle deadlines for failure responsiveness, not as a
successful-inference speedup. Magnitude **unknown**.
**Quality/reliability/audio risks:** reloads, thermals/RAM, Piper starvation, ignored options,
truncated correct answers, partial audible output/leaked workers. **Windows coverage:** option
validation/payload consistency, blocked/slow-trickle read mocks, ownership/cancel/reap,
partial traces and guard priority. **Pi validation:** cooled guarded one-shot comparisons,
frequency/current flags/RAM and actual backend stream cancellation. **Rollback/defaults:**
unset options/transport flag; retain deadlines/safety. No governor, overclock or hardware
configuration proposals. Any deployed CPU or cancellation policy change needs separate review.

## Safe implementation phases for later approval

1. **Instrumentation/regression design while hardware work is paused.** Prepare opt-in
   metadata-only trace with one clock and the missing boundaries. Extend existing trace/
   monitors, checkpoint original errors, use public fixtures and counts/hashes rather than
   private text. Trace writes add overhead. Prepare Windows mocks for endpoint, sentence,
   format, resource-cache and cancellation candidates. No default changes or live trial.
2. **Small reversible software candidates.** After approval, develop validated normalization
   bypass and ordinary-VAD active-drain changes behind disabled flags, and prepare existing
   short-sentence option comparisons. Safe to develop/test on mocks; no claimed Pi gain.
   Do not tune silence/endpoint values without physical speech evidence.
3. **After safe fan mounting/readiness.** Verify cooling/power/routing through existing
   guarded procedures. Operator chooses one public trial at a time; baseline before one
   candidate. Exact digest/context/options, thermal/current pressure/RAM guards, no retries
   or competing models. Measure natural pauses/noise, real STT/Piper, ALSA first phoneme and
   TFT. Retain first-word intelligibility and immediate rollback of silence/endpoint options.
4. **Architecture/model decisions last.** Resident Whisper, Piper PCM streaming, aplay across
   requests, asynchronous health/display, CPU/model selection need lifecycle/quality evidence
   and cooled full-pipeline trials. Check RAG, corrected memory, detailed completeness,
   hallucination safeguards and failed/cancelled turns. Sustained thermal stability requires
   its own approved validation plan, not extrapolation from two short requests. Qwen stays
   default until separately approved evidence supports a different choice.

Future candidate acceptance: save baseline commit/config allowlist and public fixture IDs,
change one variable, compare matched load/cache/workload conditions, retain baseline path.
Require measured post-utterance **acoustic** gain without lost phonemes, worse grounding,
truncation, gaps, residency conflicts or weakened safety. Report median/tails only with a
justified sample set; a one-shot result is an observation. Revert candidate setting/flag or
its dedicated commit on failure. Do not combine hardware/service edits with software rollback.
No live trial command is recommended for immediate execution while the operator's pause holds.

## Is sub-two-second first audible response realistic?

For historically measured Qwen, **no supported path to the target is established**: its
roughly five-second warm prefill/first-text delay already exceeds the entire budget before
STT, useful sentence generation, synthesis and acoustic startup. Removing microseconds of
Python/provider readiness cannot fix that constraint. Newer 3B cold loading alone was
reported at 8.90 s; code-only orchestration cannot make that cold-load sample meet two seconds.

The 3B 6.53 tokens/s observation implies about 0.153 s per decoded token for that workload.
Two seconds accommodates only about 13 decoded tokens **if decoding were the only cost**.
This is an isolated capacity illustration, not end-to-end latency, tokens-to-words conversion
or prediction for another question. Actual available decoding budget is smaller with STT,
endpoint, prefill and audio. No correct 3B warm-distinct first-audible result is available.

1B's about 313 ms first text makes selected warm cases conceivable, but 120/136 cache reuse
and incorrect reasoning prevent generalization. Text is not a grammatical, correct synthesized
answer. Hardware startup, retained silence, sequential full-file STT, generation rate and
shared CPU/RAM constrain response. Fan installation enables safer evaluation and may help
avoid throttling; no unmeasured latency/thermal improvement is claimed. Correct local answers
have priority. Cloud/laptop inference substitution is outside scope.

## Documentation validation and publication scope

This milestone is documentation only. No tests or static-analysis artifacts were added to
the repository; no production regression run is required for changed execution paths.
Completed consistency checks: 40 relative source/doc links and anchors, 48 source symbols,
key constants, balanced code fences and whitespace passed. Final publication uses a one-file
change allowlist. The 1,145-test Windows result already
committed at the source baseline (one skipped) is evidence for the preceding repair, not
new audit tests or performance evidence. Only `docs/offline-voice-latency-audit.md` is included
in this audit commit. Production code/config, `.env`/`.env.example`, Qwen selection, bank,
audio/GPIO/firmware/systemd, main and unrelated untracked files are preserved.
