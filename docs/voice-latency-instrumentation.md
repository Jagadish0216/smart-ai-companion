# Step 45: opt-in offline voice latency instrumentation

Source/audit baseline: `94c1446027693f6e3d5b71a894a98d97b61e99bd` and
[offline-voice-latency-audit.md](offline-voice-latency-audit.md).
This is observation only: capture/VAD, Whisper CLI, Piper worker protocol/voice,
model selection, prompt/context/grounding, buffering, leading silence, audio formats,
ALSA routing, GPIO/firmware and resource thresholds retain their existing behavior.
No Pi inference, physical audio tests, download, deployment or hardware change occurred.

## Enable and disable

Disabled is the default. No environment/settings files were added or edited. The existing
voice entry point accepts one explicit option:

```sh
./venv/bin/python scripts/voice_loop.py --latency-trace-dir "$HOME/voice-latency-step45"
```

**Do not execute this now.** Follow the gated future procedure below after fan assembly and
operator approval. The option only records; it neither initiates a benchmark nor installs
a safety monitor. Ordinary Enter-to-record behavior, and the separate opt-in hands-free
mode when explicitly requested/configured, are unchanged. Startup warms the existing Piper
provider as before. No new model-residency HTTP calls, inference retries or telemetry probes
were added. Existing resource samples are observed when collected; a cache hit does not
force a new safety sample for tracing.

Omit the option on the next invocation to disable. Ctrl+C retains existing cancellation and
cleanup; a handled error/cancellation finalizes the partial trace. In an embedded caller,
`assistant.latency_trace.configure(None)` immediately stops export/new automatic cycles and
closes the old writer after queued reports; an in-flight trace may finish in bounded memory.
The application can use `with latency_trace.cycle():` through the existing run_voice_cycle
entry point; explicit Trace contexts are for tests. No web endpoint exposes trace activation.

Trace output requires a **dedicated, private, local, single-process-owned directory**.
Existing terminal/application logging is unchanged and can print transcripts/answers; it is
not a content-free trace. Never export console logs from a private real interaction as part
of this procedure. Use only public fixture speech for validation. Existing transient audio
files remain part of the normal pipeline and are not copied, retained or exported by tracing.

## Privacy boundary and bounded overhead

The recorder never stores function arguments, microphone bytes, WAV/PCM, transcript text,
prompts, questions, answers, histories, document contents, paths, credentials, environment
values or exception messages. Fields and event names are explicit allowlists. Numeric fields
must be finite and nonnegative; categorical/boolean fields have fixed accepted values.
No user text hash is collected. Exception classification inspects bounded exception types/
causes, not messages. Lanes use fixed labels rather than OS thread names or identities.
The only identifier is an independent random cycle UUID, not conversation/user/device ID.

- Disabled hooks do a context lookup and call the original operation, without a trace clock,
  filesystem work, writer thread or context-copy submission. Hooks still have small Python
  call/branch overhead; disabled does not mean mathematically zero overhead.
- Enabled hooks read monotonic_ns and use a nonblocking lock. Busy lock drops the event,
  rather than delaying an audio thread. Per trace: at most 512 events, 16 occurrences per
  event name, fixed-key counts; once-only events avoid repeated clock reads after acceptance.
- No per-event disk writes. Finalization makes a bounded in-memory report and uses put_nowait
  into a queue of two reports. Full queue drops the report. One daemon writer serializes
  JSON off the audio/producer path; at most one report is being written in addition to two queued.
- Eight ring files `cycle-0.json` through `cycle-7.json`, each at most 128 KiB, plus one fixed
  `cycle.pending` (at most 128 KiB): at most 1,152 KiB of recorder payload in the dedicated
  directory, excluding filesystem metadata and unrelated preexisting files. Existing slots
  are replaced by age; no files outside those recorder slots/pending are deleted.
- Event cap, queue overflow, oversize reports and writer errors have counters. Completed
  reports include writer counters as observed when written; a failed last write may leave
  only in-process counters, not a disk error report. Saturation/partial traces cannot prove
  a boundary absent or a workload fast.
- Normal CLI shutdown waits at most one second for queued writes; no join waits for a stalled
  filesystem. Directory creation is explicit setup before capture. Storage writes themselves
  can block the writer on a bad filesystem without blocking voice work.
- No flush-on-every-event or crash-durable guarantee. Handled exceptions/cancellation retain
  available events. Forced process kill, power loss, filesystem failure, queue overflow or
  shutdown exceeding the flush budget can lose the current/queued trace. Background failures
  do not change voice success/error outcomes. Use a local healthy filesystem.

These bounds describe the recorder, not a newly imposed duration limit on normal voice
sessions. No claim is made that Windows hook timings predict Raspberry Pi overhead.

## JSON schema, clocks and interpretation

Machine schema: [voice-latency-trace.schema.json](voice-latency-trace.schema.json).
Example: [voice-latency-trace.synthetic.json](voice-latency-trace.synthetic.json), explicitly
`synthetic=true`, with invented logical-clock times. It executes no engine or device and
is not a benchmark, expected Pi timeline or new end-to-end latency figure.

One `cycle_id` and monotonic_ns origin cover the whole voice cycle, including failure cleanup.
Events use `at_ms` relative to that origin. The two existing audio jobs receive separate copied
ContextVar contexts carrying the same Trace. Lanes are main, audio_prepare, audio_playback
(or a safe other label). There is no process/clock correlation inside native Whisper/Piper:
parent-side call boundaries describe what is actually observed.

Each span has `<name>_start` and `<name>_end`, on the same lane. Match starts/ends with a
per-lane stack, including nested spans. Capped/dropped/unwound events can leave an unmatched
span; never manufacture an end. Repeated spans require occurrence-order pairing, not all
starts to the first end. Different lanes overlap; nested spans such as routing/RAG and
preparation/conversion also overlap. **Never sum them into end-to-end latency.** Use whole
`elapsed_ms` or subtract two explicitly identified timestamps from this same cycle.

Top-level fields: schema_version, cycle_id, clock, synthetic, status, elapsed_ms, events,
dropped_events, missing_events, residency, acoustic_onset, cache_accounting,
cache_counter_semantics, interpretation, and optional storage counters. Status is ok, error,
timeout or cancelled after completion (running for an in-memory unfinished trace).
`missing_events` lists selected first boundaries not observed, not a blanket failure verdict:
text input/direct answers/fixed capture legitimately omit some events. dropped_events and
unmatched spans must be considered before drawing conclusions. Cycle end can itself be
capped; top-level status/elapsed still describe finalization.

`ollama_complete` contains only already available backend scalar timings/counts and a fixed
completion-reason category. Missing cached-token counters remain **absent and unknown**,
never zero. cache_accounting is reported_consistent only when integer total/cached counters
satisfy 0 <= cached <= total; otherwise unknown_or_invalid. This is structural consistency,
not proof of backend semantics or of uncached inference. No derived uncached performance
claim is made. No load duration is inferred by subtraction. Actual residency is explicitly
unknown_not_probed: the production voice path has no existing trustworthy /api/ps snapshot.
Load duration is retained as backend evidence without turning it into a cold/warm claim.

## Timestamp definitions and limitations

| Events / span | Actual observation | Limitation |
| --- | --- | --- |
| cycle_start / cycle_end, elapsed_ms | Around run_voice_cycle, including existing error/display/Piper cleanup | Excludes CLI/Django/Piper startup warm before a cycle, wake waiting and post-cycle disk writer time. Not human-finished-speaking latency. |
| cycle_config | Selected fixed/VAD/hands_free/audio_file/text mode and ALSA/Pulse category | No device/source/path identifiers. |
| capture_open | Around Popen or NativeRequestReader.start | Process creation/control setup, not proof hardware is physically ready. Failed creation can lack end. |
| capture_first_frame | First bytes returned from existing raw pipe reads, including warm-up where read | Not sample acquisition timestamp; ordinary VAD may read queued audio after sleeping. Fixed file capture has no safe first-frame hook and leaves it missing. |
| capture_ready / calibration_end | Existing cue and threshold/calibration completion transitions | No inserted delays; absent calibration event in fixed/nonadaptive paths is legitimate. |
| speech_detected / endpoint_detected | Existing detector state/stop reason, after its normal decision | Algorithmic hint, not acoustic ground truth. No new VAD gate/threshold. Hands-free retains every frame as before. |
| capture / capture_cleanup | Capture call including file close/validation and existing reap helper | Does not separate each syscall or sample-level duration. |
| normalize | Existing whole ffmpeg normalization call | No separate ffmpeg model/startup/substep timing. Input format/channel behavior unchanged. |
| stt_cli | Existing Whisper provider call through CLI completion/error and subprocess.run cleanup | Includes validation, executable/model load, decode and cleanup; those cannot be split accurately without altering/reading native execution. No stderr/transcript export. |
| service_prepare, conversation_prepare, history, user_persist, response_persist | Existing application preparation/DB function boundaries | No SQL/row text/IDs captured. Some early return/error paths leave manual spans unmatched. |
| policy, resource_cache_hit/miss, resource_snapshot | Existing execution policy and actual cache branch / snapshot construction | Cache hit does not run ancillary probes. Tracing does not change TTL or refresh cadence. |
| resource_metrics / resource_state | Existing metric collection and available temperature/RAM/current pressure flags | Aggregates only, not process RSS, no added sampler. Unknown/malformed fields omitted. |
| network_checks / ancillary_checks / provisioning_check | Existing network/service-health/provisioning calls | Network status/SSID/passwords/service details not retained. Ancillary checks are aggregate, not individual service probes. |
| resource_policy / route | Fixed profile, generation-allowed boolean and route category | Direct/refusal/grounded routes may legitimately avoid LLM events. |
| routing / rag / memory | Existing router and real retriever calls | Nested timing, no contents/results/history exported. Optional retrieval skips stay skips. |
| ollama_request_ready | Immediately before existing HTTP-stream dispatch clock | Includes no probe; excludes earlier prompt/service work. |
| llm_first_alphanumeric | First content delta containing an alphanumeric character | Whitespace/punctuation-only deltas excluded; not semantic correctness or a sentence. |
| ollama_complete | Existing final backend metrics available | load/prefill/decode are backend-reported, not invented client subspans. No done event on an incomplete stream. |
| first_complete_sentence | Buffer first recognizes a safe nonempty sentence-boundary candidate under existing grouping/abbreviation rules | Can precede release of a short held sentence. Syntactic boundary, not a grammar/meaning oracle. No new splitting. |
| first_speech_unit | First existing unit selected for queueing | May be a safe clause before a complete sentence; content/character counts not stored. |
| text_queue_wait / audio_queue_wait | Entry/return around existing queue receive/get logic | Includes immediate gets and EOF markers. Not a measurement of ALSA XRUNs or producer put backpressure; caps may omit late occurrences. |
| tts / piper_state / piper_ready / piper_exchange | Whole existing synthesis call, pre-readiness state, readiness call and IPC round trip | reused is prior ready state, not proof worker survived; exchange combines synthesis/WAV/IPC. No internal child first-sample/load split. Provider lock wait only appears within whole tts, not separate. Errors can leave ready/exchange without end. |
| audio_prepare / playback_conversion / first_audio_ready | Existing full-unit prep, native ffmpeg conversion and first prepared result | Nested spans, complete WAV/file buffering preserved. No PCM data saved in trace. |
| player_start / leading_silence | Existing aplay startup and submission of configured silence | Submission may buffer ahead; leading silence frames are never reduced. |
| first_speech_pcm_accepted | Immediately after a successful os.write while inside speech-file submission | Initial injected silence excluded. The speech file can contain natural silence; pipe acceptance is neither device receipt nor DAC/acoustic onset. Partial writes handled by existing code. |
| speech_file / drain | Existing PCM file writing and aplay EOF/drain/exit | File writes may complete ahead of sound; Pulse path lacks these ALSA-specific markers. |
| display_callback / serial_write | Existing best-effort display helpers and serialized _send duration | Includes lock/open/skip/write path, not firmware ACK or pixels; no protocol payload or text captured. |
| stream_error and span outcomes | Fixed error/timeout/cancel categories where actual exceptions are observable | No messages. Service translation may make final cycle category generic error; a typed underlying timeout can be retained on stream_error. Message-only Piper errors remain generic rather than inspecting private text. |

Acoustic onset, human utterance end, true VAD end delay, driver-buffer depth and visible TFT
transitions remain unobserved. Obtain them later with synchronized external recording.
Do not label existing legacy first_speech timing or first PCM acceptance as audible onset.

## Future consolidated Pi validation procedure — not executed

1. After safely mounting the fan, obtain operator approval and confirm baseline checkout,
   working audio/display setup, power and cooled idle state. Keep Qwen default. Use the
   existing safety criteria: start below 50 C, healthy current flags/RAM, no conflicting
   model workload, stop at 70 C or sooner on pressure. This tracing option does not enforce
   those conditions or relax production resource policy. Do not turn the pause into an
   unattended benchmark, warm loop, model eviction or restart.
2. Use one ordinary interactive session with the flag shown below, preserve all existing
   capture/audio settings and speak **one public fixture** after the cue. End normally with
   q, or cancel immediately on pressure/failure. No automatic retry. Do not use --text to
   claim STT or microphone timing. No hands-free loop is needed for this first validation.
3. Inspect the latest cycle JSON for cycle ID, status, cap/drop/storage counters, missing
   events, cache conditions, captured backend metrics and lane overlap. Confirm no private
   payloads. Reuse existing guarded telemetry/procedures for safety supervision; do not
   invent another monitor or treat trace completion as thermal acceptance.
4. Compare only this same cycle's named software boundaries. Separately correlate external
   acoustic evidence if measuring utterance-end-to-answer-onset. Do not infer missing STT
   or residency details. A future disabled-versus-enabled public comparison for actual
   observer overhead requires its own cooled operator approval; never infer Pi overhead
   from the Windows mock suite.
5. Disable by omitting the flag, retain only approved public JSON, and make no optimization
   or production default decision until bottleneck measurements and quality review agree.

Consolidated session command, **for later execution only**, from the updated checkout with
its existing Pi environment (one manual recording; q exits):

```sh
./venv/bin/python scripts/voice_loop.py --latency-trace-dir "$HOME/voice-latency-step45"
```

If unattended automatic cutoff is required for that validation, defer it until the existing
operator-approved guarded procedure can supervise the session; this milestone deliberately
does not alter the existing inference/audio diagnostic harness or create a new monitor.

## File-change inventory

Every source change below is an observational hook or lifecycle wrapper; existing operation
arguments, return values, thresholds, audio byte construction and failure ownership are
preserved. No new dependency is required.

| File | Change |
| --- | --- |
| `assistant/latency_trace.py` | Content-free collector, context propagation, typed failure categories, fixed event/field allowlists, bounded asynchronous ring writer and opt-in lifecycle. |
| `scripts/voice_loop.py` | CLI flag, cycle scope, capture transitions, first sentence/unit markers, queue boundaries, existing synthesis/preparation spans and two-worker context propagation. |
| `assistant/voice/stt.py` | Span around existing complete Whisper provider call; no native STT implementation change. |
| `assistant/voice/tts.py` | Existing readiness/reuse/IPC boundary markers; no worker, voice, synthesis or protocol change. |
| `assistant/voice/hands_free.py` | Existing capture/open/read/calibration/detection/endpoint/cleanup markers. |
| `assistant/voice/alsa_stream.py` | Existing player/silence/file/drain spans and successful speech-file pipe-write marker. |
| `assistant/ai_engine/local.py` | Existing request-ready, first alphanumeric content and allowlisted backend completion metrics. |
| `assistant/services.py` | Existing preparation/history/persistence and typed stream-error observations; safe policy/route categories. |
| `assistant/execution_policy.py` | Existing plan-call span. |
| `assistant/routing.py` | Existing router-call span. |
| `knowledge_base/retrieval.py` | Existing lexical RAG-call span. |
| `conversations/memory.py` | Existing memory-retrieval-call span. |
| `system/control_plane/resource_manager.py` | Existing cache branch/snapshot/provisioning and available aggregate resource observations. |
| `system/control_plane/resources.py` | Existing resource collection span. |
| `system/control_plane/network.py` | Existing network-status collection span; no extra network operation. |
| `system/control_plane/health.py` | Existing ancillary-health collection span. |
| `assistant/display/serial_display.py` | Existing serial-send span, with no payload collection or protocol change. |
| `assistant/tests_latency_trace.py` | Deterministic mock/clock, privacy, lifecycle/storage, overlap, service/RAG/memory, cache semantics, schema and enabled audio compatibility tests. |
| `docs/voice-latency-instrumentation.md` | This report and future operator procedure. |
| `docs/voice-latency-trace.schema.json` | Structured JSON report schema. |
| `docs/voice-latency-trace.synthetic.json` | Explicit invented-clock, hardware-free schema example. |
| `docs/latency-tracing-focused-tests.log` | Windows focused regression output; synthetic fixtures/canaries only. |
| `docs/latency-tracing-windows-tests.log` | Full Windows regression output; not Pi performance evidence. |

No changes to `.env`, `.env.example`, `config/settings.py`, Piper child implementation,
Whisper CLI invocation/options, capture thresholds, stream buffer limits, playback silence,
ALSA devices/routes, ESP32/GPIO/firmware, deployment/systemd, resource thresholds, default
Qwen selection, 100-question bank or audit baseline document are included. Existing unrelated
untracked files are excluded from the commit.

## Windows verification evidence

All new tests use public invented fixtures, fake devices/backends or an artificial monotonic
clock; no physical microphone, speaker, Ollama inference, Whisper model or Piper voice model
was exercised. Real process/audio mechanics in the inherited suite remain mocked as before.

- New trace module: 20 tests. Includes disabled no-clock/no-writer behavior, fixed privacy
  allowlists, unknown/invalid/zero/positive cache counters, errors/timeouts/cancellation,
  missing boundaries, nonblocking event/queue saturation, rotation/write failure, copied
  thread contexts and overlapping intervals, first-text versus held sentence, fake full
  pipeline, resource-cache hit, actual service RAG/memory/persistence equivalence, typed
  service error translation, schema/example consistency, observer failure isolation and
  the existing bit-exact ALSA test executed with tracing enabled.
- Focused command: `python manage.py test assistant.tests_latency_trace assistant.tests_voice_loop assistant.tests_alsa_stream assistant.tests_capture_vad assistant.tests_inference_latency --noinput`.
  Result: **157 tests, OK, one platform skip**, 3.951 s.
- Full command: `python manage.py test --noinput`.
  Result: **1,165 tests, OK, one platform skip**, 39.913 s.
- Logs: `latency-tracing-focused-tests.log`, `latency-tracing-windows-tests.log`. Printed
  question/answer canaries in those logs are deliberately invented test strings and existing
  console behavior, not fields in exported traces. No private real conversation was used.
- Overhead checks are deterministic: 10,000 repeated once-only attempts use only origin plus
  one accepted-event clock read; saturation caps event count/JSON size; a locked collector
  drops instead of waiting and a full report queue rejects immediately. These are operation/
  storage bounds, not Raspberry Pi microsecond measurements or a claimed latency improvement.
- Standalone CLI help exposes the flag without touching devices. Python syntax, JSON/example
  structural constraints, relative doc links, the complete 23-file allowlist and protected
  source/config/bank/deployment diffs were checked. No new dependency/install is needed;
  jsonschema is not installed here, so schema checks used explicit structural/allowlist
  assertions rather than claiming a third-party validator run.
