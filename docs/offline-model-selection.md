# Offline model selection and short Pi validation

The existing harness now also has an explicitly requested two-request
[`--cold-warm` procedure](warm-llm-benchmark.md). Use that procedure to measure
resident Llama latency independently of cold initialization. The single-request
workflow below remains available; neither workflow changes production defaults.

2026-10-19. Accepted source baseline: `c4c5c442df0972ad177b038a4e9f7f4c540c5604`.
Feature branch: `codex/offline-model-selection`. Feature-only commit/push was
explicitly authorized for Pi testing; no merge or deployment is authorized.

No model has been selected as a proven faster primary. Keep the existing Qwen
4B configuration and quality reference until a replacement passes matched quality
and timing checks. No Pi inference was run for this work. No cloud, remote or
laptop inference is introduced. The new harness calls only a loopback Ollama
instance, uses real application routing/grounding and creates disposable data.

## Evidence and decision limits

User-supplied Qwen3.5 4B results: about 5 s before the first token, 2.7–4.0 output
tokens/s, normal answers 18–26 s, detailed answers 81–87 s. Model loading was
1–2 ms, already resident. Prompt-cache reuse remains unproven. The older Llama3.2
1B result (warm TTFT about 0.13 s, 8.76 tokens/s) used different requests/runtime
conditions. It justifies testing Llama first; it is not a comparable speedup.

No active cooler is installed. An earlier extended run reached 75.5 C and
stopped. Do **not** repeat the old multi-round inference/audio harness on this
uncooled Pi. A short sample can also abort or fail; that is useful safety evidence,
not a completed model comparison. The new harness refuses starts at >=55 C and
stops at >=70 C to leave margin below the existing 75 C guard. Neither policy nor
firmware thermal protection is changed.

A previous remote read-only inventory attempt failed. The user has since
confirmed the following Pi inventory (prefixes only, not full digests):

| Installed tag | User-confirmed digest prefix | Role |
|---|---|---|
| `qwen3.5:4b-q4_K_M` | `d8b0f5e9760c` | Existing quality reference |
| `llama3.2:1b` | `baf6a787fdff` | First short trial |
| `llama3.2:3b` | `a80c4f17acd5` | Installed; outside this milestone's candidate list |

The two smaller Qwen candidates were not reported installed; do not download
them. Latest user-supplied state: 46.1 C, current/latched flags `0x0`, available
RAM 3.0 GiB, Qwen4B resident at approximately 3.6 GB. These are a snapshot,
not a live preflight performed by this workspace. Runtime version, full digests,
new model timing and quality still require Pi validation. Each trial retrieves
the full digest locally and checks the user-confirmed prefix for Qwen4B/Llama1B;
an optional `--expected-digest` further pins a full digest or >=12 hex prefix.
An unexpected updated artifact is refused, not silently accepted.

## Implementation audit

| Path | Finding and implication |
|---|---|
| `assistant/ai_engine/__init__.py`, `local.py` | One Django-configured model; local `/api/chat` streaming; backend chat template/tokenization. `think=false`, context 4096, accepted generation budgets. No classifier LLM or automatic cloud fallback. Model residency already avoids most load overhead. |
| `assistant/execution_policy.py` | One configured model serves every resource profile and RAG. Deprecated lightweight/RAG model settings do not select extra models. Protective caps/refusals remain authoritative. A smaller primary can use this same factory without a second resident dependency; changing production selection is a later reviewed action. |
| `assistant/policy.py`, `services.py` | Request-scoped voice budgets 32/64/192; relevant recent context, explicit memory and local-document retrieval; authoritative capability registry controls routes. No prompt deletion, threshold changes, new retrieval model or larger global budget is justified here. |
| `knowledge_base/retrieval.py`, `conversations/memory.py` | Existing local lexical retrieval needs no embedding-model download. Smaller generation models must still use supplied evidence correctly. Harness fixtures test the actual service paths, rather than only pasting pretend RAG context into a raw prompt. |
| `assistant/voice/stt.py`, `factory.py` | Local whisper.cpp using installed binary/model. Existing no-speech and bounded subprocess behavior; provider object reuse does not prove the whisper executable/model remains resident between transcriptions. No new STT implementation/default is introduced. |
| `assistant/voice/tts.py`, `piper_worker.py` | Persistent spawn-isolated Piper worker, streamed synthesis and cancellation. Tiny readiness time is not a measurement of sentence synthesis cost. |
| `scripts/voice_loop.py` | Capture, normalization, STT, inference, sentence buffer, Piper and ALSA have distinct timings. Generation/TTS/playback overlap. Accepted buffering, PCM preparation, physical sound, UART/TFT commands and display states remain unchanged. |

Only a new script, tests and documentation/evidence are added in this branch.
Existing CPU tuning, compact style and 30-character complete-first-sentence
experiments remain opt-in; none is enabled in production. The short text harness
explicitly holds compact style off, CPU options unset, context 4096, temperature
0, seed 42 and think false for every candidate. It sends one 48-token maximum
request in the original `--run` mode, further bounded by the real resource policy.
Only explicit `--cold-warm` runs two short requests. Neither mode runs STT/Piper,
captures microphone input or plays audio.
Only benchmark requests use `keep_alive=60s`, equally for both models, instead of
inheriting the production 24h retention. This requests natural idle expiry and
may shorten the selected runner's residency; it is not an explicit unload call
and does not persist any production setting. No daemon restart/stop or model
deletion occurs. Do not resume production inference until the trial model expires.

## Candidates and quality risks

| Candidate (exact tag required) | Role / hypothesis | Risks to evaluate |
|---|---|---|
| `qwen3.5:4b-q4_K_M` | Existing quality reference and current primary | Known slow prefill/decode on supplied data; a short cap does not validate detailed answers. |
| `llama3.2:1b` | First speed candidate to test, based on older local evidence | Different family/template/quantization; smaller capacity may lose instruction following, grounding or technical precision. Do not presume old TTFT repeats. |
| `qwen3.5:2b-q4_K_M` | Intermediate capacity candidate | May offer a quality/latency compromise; same family does not establish equal cache behavior or Pi throughput. |
| `qwen3.5:0.8b-q8_0` | Smallest capacity candidate, higher weight precision | More quantization bits do not replace missing model capacity. Watch arithmetic, follow-up resolution and invented context. |

[Official Llama tags](https://ollama.com/library/llama3.2/tags) and
[official Qwen tags](https://ollama.com/library/qwen3.5/tags) describe available
registry artifacts, not this Pi's installed inventory. Aliases can change. Do not
substitute a nearby tag automatically. The harness records exact local tag, full
digest, family, parameter size, quantization and artifact bytes; absent, duplicate,
remote/cloud or unconfirmed non-GGUF entries are unusable. No pull/create/delete
operation exists. Different model tokenizers naturally produce different token
counts; compare identical semantic tasks, complete-answer time and quality as
well as tokens/s.

Artifact bytes are disk size, **not RAM consumption**. `/api/ps` `size`,
`size_vram`, `context_length`, sampled available RAM and swap are recorded. These
are Ollama allocation/residency observations, not isolated process RSS or a peak
RAM guarantee. Context cache, runtime, STT/Piper, OS and shared memory contribute
additional use. Determine headroom on the actual Pi; do not infer it from model
parameter count alone. The script refuses another resident model and never
evicts a production runner to make room. Residency/digest are checked before
setup, immediately before the inference request, during monitoring, and after
completion. Available RAM below 512 MiB aborts; missing RAM telemetry also aborts.
A Linux advisory lock serializes this harness's invocations; the empty lock file
in the temporary directory remains, with its kernel lock released on exit.
Operator workload must also be idle: this lock does not coordinate production
requests. External concurrency cannot be prevented without changing services;
if another runner appears, abort and wait for natural expiry rather than unloading
either runner. Do not treat that aborted attempt as a valid sample.

## Consolidated Pi validation procedure

1. Make the reviewed feature commit available through a
   separately approved process. Run as the normal application user on the Pi.
   Keep main, `.env`, services, firewall, models and physical settings unchanged.
   Do not run the older long A/B harness while uncooled. The accepted commit
   must be in local Git history; report HEAD, tracked-diff hash and script hash
   identify the exact reviewed source. No source is automatically deployed.
2. Use a new output folder for every command. First inventory only (metadata,
   no inference):

   ```bash
   ./venv/bin/python scripts/compare_offline_models.py --inventory \
     --output "$HOME/offline-inventory-$(date +%Y%m%d-%H%M%S)"
   ```

   Review `report.json`. A missing tag is **not tested**; do not download one as
   part of this procedure. Reconfirm full digests against the prefixes above.
3. Choose the reference and one **installed** candidate. Begin with Llama1B only
   if its exact tag/digest is confirmed. Idle interactive/wake/dashboard inference
   voluntarily; do not stop/reconfigure services through this script. Start below
   55 C, with no current voltage, frequency cap, throttle or soft-limit bits.
   Allow natural runner expiry/cooldown between model handovers; if another
   model remains resident the script refuses the trial. It does not unload it.
   **Qwen4B is currently resident, so the first Llama command must refuse now.**
   Wait for its natural expiry in an idle test window; with the existing 24h
   keep-alive this can take many hours. Do not use `ollama stop`, zero keep-alive,
   service restarts or a changed production configuration to bypass this boundary.
   If safe exclusive handover is impractical, defer comparison until an approved
   test window; do not change the residency check to obtain a result.
4. Run **one** brief fixture per invocation, with no shell loop. Example A:

   ```bash
   ./venv/bin/python scripts/compare_offline_models.py --run \
     --model qwen3.5:4b-q4_K_M --expected-digest d8b0f5e9760c --fixture fact \
     --output "$HOME/offline-A-fact-$(date +%Y%m%d-%H%M%S)"
   ```

   After cooling and safe model handover, example B:

   ```bash
   ./venv/bin/python scripts/compare_offline_models.py --run \
     --model llama3.2:1b --expected-digest baf6a787fdff --fixture fact \
     --output "$HOME/offline-B-fact-$(date +%Y%m%d-%H%M%S)"
   ```

   No automatic warm-up or repetition occurs. Each invocation has a 45-second
   child wall deadline including isolated setup. Socket timeout follows the
   existing service-policy setting in `--run`; the new `--cold-warm` explicitly
   caps both engine and service-policy socket timeouts at 20 seconds.
   The parent samples about every 0.5 s **plus telemetry cost**; bounded telemetry
   commands/read waits add response delay, so this is not a hard real-time
   temperature guarantee. It terminates only its own process session, closes the
   request and removes its own temporary database. Ollama daemon cancellation
   behavior must be confirmed on the Pi; after abort, stop testing and observe
   cooling. Never terminate/restart the daemon or disable thermal protection.
5. Inspect each report before another request. Missing temperature/throttle/actual
   ARM-frequency telemetry aborts. Current bits 0–3 abort; historical bits 16–19
   alone do not. Temperature >=70 C, power pressure, deadline, provider failure,
   missing metrics, inventory change or invalid grounding paths marks the report
   `aborted`. Partial results cannot become an accepted comparison. Cool naturally
   back below 55 C before considering another short request; no unattended loop.
6. Compare the same fixture's completed A/B reports offline:

   ```bash
   ./venv/bin/python scripts/compare_offline_models.py \
     --compare /path/to/A/report.json /path/to/B/report.json \
     --output "$HOME/offline-pair-fact-$(date +%Y%m%d-%H%M%S)"
   ```

   `invalid_pair` flags unequal prompts, generation limits, resource profiles,
   sampling/context controls, source, output modes, runtime, initial residency or
   start temperatures >3 C apart. Review all recorded conditions even when the
   comparator passes. Model/template/tokenizer differences are intentional.
   Cold versus warm labels must match too. Inspect load duration and context
   allocation; reject reload versus warm comparisons. No prompt-cache hit is
   inferred from a resident runner or a short load time.
   Memory/temperature/frequency can drift during generation; inspect the traces.
7. Spread the remaining fixed fixture pairs across separate cool sessions:
   `fact`, `reasoning`, `followup`, `technical`, `rag`, `memory`. Deterministic
   order is A-B for fixtures at even indices, B-A for odd indices. No random
   shuffle or automatic batch runner. The reference answers are in the
   script's fixed manifest (`--plan-only` exports it without network/inference).
   Short smoke coverage comprises six requests per model spread over sessions,
   **not** a thermally sustained or statistical performance certification.
8. Review every answer using the checklist below. After active cooling is
   installed and verified in a separate hardware task, extend testing to longer
   multi-step reasoning, multi-turn memory and detailed completeness, matched
   warm trials and sustained full voice cycles. Until then no production model
   switch, automatic download, audio-default change or near-instant claim.

### Measurement definitions

`first_text_token_ms` is the production engine's request-to-first nonempty text
content, excluding supplied status notices and reasoning tokens. It is not end
of speech-to-first-sound. `prompt_eval_duration_ms`, `eval_count`,
`prompt_eval_count`, `eval_duration_ms`, `tokens_per_sec`, `load_duration_ms` and
`done_reason` come from the backend. Missing cache counters remain missing.
The top-level `load` section separates backend load, prompt evaluation and decode
durations. `cold` means no selected model was resident before the request; `warm`
is an explicitly operational label for a matching resident context of 4096 and
reported load <=100 ms. Other resident results are `resident_reload_or_slow_load`;
missing load duration is `unknown`. The latter two labels cannot form an accepted
timing pair. The 100 ms boundary is a classification rule, not a measured latency
improvement or proof that every cache/runner setting matches. Even cold requests
can benefit from the OS page cache. Do not subtract backend load from TTFT and
present the remainder as a measured warm TTFT: warm timing needs its own sample.
`total_answer_s` includes the application's request preparation, routing,
retrieval/memory and stream completion/persistence, excluding disposable schema
setup. Parent wall timeout includes setup. A 48-token request is a shared maximum,
not a demand to produce exactly 48 tokens; real resource caps still win.

The [Ollama chat API](https://docs.ollama.com/api/chat) defines backend duration
and token fields; [running-model metadata](https://docs.ollama.com/api/ps) defines
residency size/context fields. All outputs use the same **text-only** mode. No
null-sink number is labeled audible latency. Comparing these times to earlier
full speech totals or differently sampled historical requests is invalid.

## Human quality review (required, not keyword scoring)

Score each category 0 (failed), 1 (partially met), 2 (fully met), with a short
justification citing the actual synthetic answer and task. Record in a separate
review alongside the immutable raw reports. `quality_accepted` stays false and
`manual_quality` null until human review; the script never auto-selects a model.

- **Accuracy:** facts, units, causality and arithmetic are correct. Reasoning
  fixture must explain why two of the three remaining balls are blue, not merely
  print a guessed fraction. Technical fixture must distinguish cycles from
  instructions and explain IPC/workload dependence.
- **Completeness:** every requested element is present. A length stop, abrupt
  ending, omitted comparison, unfinished explanation or filler consuming the
  budget fails completeness even if a keyword is present. Natural punctuation
  alone does not establish completion. `fact` must name New Delhi and correctly
  locate it north of the equator.
- **Instructions:** at most 25 words, concise direct answer, no unrequested
  reasoning dump, no pretend online search or physical actions. Report word-limit
  violations rather than deleting output to make it look compliant.
- **Context resolution:** follow-up must resolve RAM versus SSD from the supplied
  conversation. RAG must use the actual retrieved synthetic Lumen 3.3 V note and
  not invent chip, pin or capability specifications. The optional 5 V warning is
  supported; omission is not a failure when the question only asks voltage.
- **Memory:** recall Amber Finch from stored synthetic user history, not from a
  recent direct prompt or fabricated project details. Correct text alone is
  insufficient if `memory_used` is false; that sample is rejected by the harness.
- **Hallucinations and honesty:** no invented sources, retrieved facts, stored
  user details, capabilities or unsupported certainty. Any safety-relevant
  voltage/action fabrication is a hard failure, regardless of latency.
- **Natural delivery:** later physical voice validation must preserve complete
  first sentences, numbers, abbreviations and quotes; listen for awkward cuts,
  silence, repeated text or missing endings. This text benchmark cannot pass it.

Require full correctness/completeness/grounding/memory scores on these smoke
tasks, no hallucinations, and explicit reviewer acceptance before considering a
candidate. A small fixture set cannot prove general intelligence; expand after
cooling. Do not average a critical grounding failure away with fast fact answers.

## End-to-end latency analysis and next optimizations

| Stage | Existing evidence / mechanism | Recommendation and remaining measurement |
|---|---|---|
| Capture/end of utterance | Existing adaptive hands-free VAD and `record` timer; fixed-input path has microphone warm-up/record time | Measure spoken-end to capture-stop separately from user speaking time. Do not lower VAD silence/confirmation thresholds without noisy-room and clipping tests. |
| Normalize/Whisper | `normalize` and `stt` timers; local whisper.cpp subprocess | Measure with a fixed public consented WAV and transcription accuracy/word errors after cooling. Do not replace STT or cut audio based on unmeasured assumptions. |
| Prompt/prefill | User's ~5 s; prompt already modest and model resident | Smaller local primary is the highest-priority experiment. Stable prompt prefixes alone do not prove cache reuse. Preserve safety, RAG evidence and memory. |
| Decode | User's 2.7–4.0 tokens/s dominates long answers | Compare complete correct answers, not only TTFT. Lead with essential answer within existing budget; never silently drop requested facts or issue extra continuation calls. |
| First speech unit | Accepted 60/100 first phrase thresholds; opt-in complete-sentence threshold 30 available | Keep production unchanged. After cooling, compare identical generated text with/without the existing opt-in and listen. A one-sentence answer may gain little; smaller units can worsen gaps/prosody. |
| Piper/PCM/ALSA | Persistent Piper, bounded overlap; `first_tts_unit_ready`, `first_audio_ready`, `audio_prepare`, starvation and playback metrics | Existing warm readiness does not prove fast synthesis. Retain prefill, silence and accepted ALSA settings until measured audible-onset and underrun tests justify one change at a time. |
| TFT/device | Accepted display protocol and local action router | Verify accepted behavior through normal regression/manual acceptance, without changing firmware, wiring, enclosure or sending physical commands in this benchmark. |

Do not sum overlapping AI/TTS/playback stage durations. For later real voice
acceptance, record capture-end, STT-end, first model text, first complete unit,
first prepared PCM and actual speaker onset on a common time base. The existing
first-speech/PCM timestamps are software estimates; an external recording is
needed for audible onset. Keep input WAV, model, prompt, budget, source, thermal
state, speaker and sampling controls equal. Physical capture tests must remain
separate from privacy-preserving text smoke tests.

Recommended decision now: **no proven replacement primary**. Prioritize installed
Llama1B for the first short matched comparison. If it fails quality, report that
failure; the installed Llama3B could be a separately reviewed next candidate.
The smaller Qwen candidates remain unavailable for this milestone and must not
be installed automatically. Keep Qwen4B as reference.
If none passes, say so. Next hardware action is properly installed active cooling
and verified power/airflow in a separately approved task, followed by sustained
tests. Cooling may improve stability; it does not guarantee the required speed.
If the cooled Pi still misses latency/quality targets, consider a faster dedicated
**local/offline** appliance through a separate hardware decision, or explicitly
accept a narrower answer scope. No remote/laptop inference or cloud fallback.

Existing untrusted-network security release blockers remain in
`system-end-to-end-audit.md`; this work does not rewrite authentication.

## Windows results and protected scope

See `offline-model-windows-results.json` and `offline-model-windows-tests.log` for
the original `917695f` milestone's regression evidence: **1,036 tests run, 1,035 passed, one Linux-only
skip in 129.182 s**; **272 focused tests passed in 16.098 s**. Compilation and
the six-fixture offline plan passed. All 555 hashed protected/unrelated files
remained unchanged. The current evidence files also include the later cold/warm
extension's complete and targeted results, described in `warm-llm-benchmark.md`.
Tests exercise real service RAG/memory/follow-up
paths with synthetic mocked backend output, fail-closed telemetry, local-only
inventory/redirect handling, unequal-pair rejection and thermal-abort cleanup.
Mocked answers are **not** model quality evidence. Linux process-group termination,
real Ollama cancellation/metadata, thermal polling, allocation memory and physical
voice/device operation still require Pi validation. Production implementation and
defaults are unchanged. The original `917695f` milestone contained the six
implementation/test/documentation artifacts listed below. The subsequent
cold/warm extension's inventory is in `warm-llm-benchmark.md`.

```text
assistant/tests_offline_models.py
scripts/compare_offline_models.py
docs/inference-latency-optimization.md
docs/offline-model-selection.md
docs/offline-model-windows-results.json
docs/offline-model-windows-tests.log
```

The target is a substantial improvement in complete, correct offline conversation
and first audible speech, not merely a 1–2 second planning reduction. No such
improvement is established by this milestone's Windows tests or the short smoke
workload. Production model selection remains unchanged until matched Pi quality,
end-to-end timing and thermal stability support it.
