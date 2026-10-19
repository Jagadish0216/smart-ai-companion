# Separately measured cold and warm local inference

Feature branch: `codex/offline-model-selection`; implementation parent:
`917695fbdd7a182e6fae93fbbafe21b30345845b`. No Pi inference or deployment was
performed while implementing this extension. Production model selection,
Whisper, Piper, ALSA, RAG/memory, device/TFT behavior, firmware and services remain
unchanged. No cloud inference, model download, explicit unload or daemon restart.

## Evidence and scope

The user supplied this completed Llama1B **cold** sample, not a warm measurement:

| Metric | User-reported value |
|---|---:|
| Digest prefix | `baf6a787fdff` |
| Load | 17,433.33 ms |
| Prompt evaluation | 2,407.88 ms / 142 tokens |
| Decode | 1,860.19 ms / 17 tokens / 9.1 tokens/s |
| First text | 19,844 ms |
| Full answer | 21.919 s |

Answer: “The capital of India is New Delhi. It is north of the equator.” These
facts satisfy the fixed factual question; raw timing files were not regenerated
by this workspace. The earlier Qwen warm reference was about 5–5.5 s first text
and 2.7–4 tokens/s. A Qwen reload previously exceeded its timeout around 36 s.
Neither subtracting Llama's load from its cold result nor comparing cold Llama
with warm Qwen establishes warm Llama latency. Even its reported cold prefill
alone exceeded two seconds; the target remains unproven.

## One consolidated command on the reviewed Pi checkout

Keep the application/wake/dashboard inference idle. Wait for **all** models to
expire naturally; do not use `ollama stop`, zero keep-alive, changed services or
production configuration. Start below 55 C with healthy power/throttle flags
and at least 512 MiB available RAM. The script rechecks these conditions, so the
state supplied previously is not assumed current.

```bash
./venv/bin/python scripts/compare_offline_models.py --cold-warm \
  --model llama3.2:1b --expected-digest baf6a787fdff --fixture fact \
  --output "$HOME/llama1b-cold-warm-$(date +%Y%m%d-%H%M%S)"
```

This runs **exactly two** requests, with no retry, third confirmation request or
unattended loop. Read `report.json` in the new output directory. Do not repeat
the benchmark while hot. It may abort safely rather than produce a valid pair.
The original `--run`, `--inventory`, `--plan-only` and `--compare` modes remain
compatible. `--cold-warm` permits only the fixed `fact` fixture; legacy
`--compare` is for single-request model reports, not this two-phase report.

## Sequence and controls

1. Acquire the existing Linux advisory benchmark lock. Verify the exact installed
   local GGUF tag, full digest and expected prefix. Refuse any resident model,
   including the selected one. No production model is evicted to make room.
2. Create disposable SQLite/media/cache state before Django initialization and
   seed only public synthetic fixtures. Run one independently timed cold factual
   request. Check identity and empty residency again immediately before HTTP
   inference. A failed, truncated or incomplete cold initialization prevents warm
   inference; its partial report remains invalid.
3. Save the completed cold sample. Confirm the selected model alone is still
   resident, its full digest matches, and context is 4096. Start a fresh isolated
   application request/database for the warm measurement, with no conversation
   history from the cold answer. Check identity/residency/context again immediately
   before HTTP inference. No cold load time is subtracted to synthesize warm data.
4. Record warm completion and validate the pair. Message hash, model, sampling
   parameters, output limit, thinking, keep-alive, routing and resource profile
   must match. Expiry, another resident model, changed context, slow reload,
   truncation, missing metrics, cache uncertainty or changed evaluated prompt
   work invalidates the result. No model is selected automatically.

Both requests use the same exact factual question and safety/style instructions,
seed 42, temperature 0, context 4096, `think=false`, request-local keep-alive 60 s
and maximum 48 generated tokens (resource guards may reduce this; unequal limits
invalidate the pair). Separate child processes use the same interpreter/source.
Their disposable schema setup is outside request timing but inside each child's
deadline. Each phase has a 45-second child wall deadline and an explicit
20-second socket/policy timeout; these are not relaxed to accommodate a slow Qwen
reload. The service policy overrides the engine timeout, so this mode explicitly
caps both. The legacy single-request mode retains its previous configured policy
timeout. The two children have a combined 90-second target wall budget, plus
bounded metadata/monitor and cleanup overhead. Deadline enforcement occurs at
the next poll and is checked again after exit; it is not a real-time interrupt.
There is no long inference loop.

The below-55 C requirement applies to benchmark/cold start. The immediate warm
request must remain below 70 C; it does not wait hot for cooling or reload a model
after expiry. Both phases continuously retain current-pressure and RAM guards.
Current voltage, frequency-cap, throttle or soft-limit flags abort; historical
flags alone do not. Missing required temperature/frequency/RAM/throttle telemetry
also aborts. Sampling takes about 0.5 s plus bounded telemetry/API work, so this
is not a real-time guarantee against brief thermal excursions. On an uncooled Pi,
stop testing after an abort and observe cooling.

Timeout, interruption or safety failure terminates only the harness's owned
child process group, reaps it and removes its temporary database/audio-free
working directory. It never kills Ollama. Server-side stream cancellation and
natural expiry still need verification on the installed backend. Keep production
inference idle until the trial runner expires; the advisory lock cannot block
other applications from submitting a concurrent request.

## Reading measurements and cache evidence

Each `samples` entry has its own phase, status, response, human correctness
reference/review field, request hash/options, resource profile, load section,
resident metadata and telemetry. Backend metrics separately retain model load,
prompt evaluation, decoding, generated/prompt/cached token counts and tokens/s.
Available RAM and Ollama residency allocation are not peak process RSS.

`first_text_token_ms` retains the original engine timing for compatibility.
`first_meaningful_text_ms` is independently timestamped from immediately before
HTTP inference to the first **model** text delta containing an alphanumeric
character. Whitespace-only/punctuation-only deltas and application status notices
do not start it. This is an operational text criterion, not semantic proof that
the answer is useful. Pre-inference identity probes are outside that clock.
`total_answer_s` includes application preparation, identity checks, inference
and response persistence, excluding disposable schema setup. No metric measures
STT completion, Piper synthesis, ALSA acceptance or actual speaker onset here.

`cold` means no model was resident before the request. `warm` requires matching
resident identity/context and backend load <=100 ms, using the existing operational
classification. A higher load is `resident_reload_or_slow_load`; missing load
is `unknown`. Neither can be accepted as the warm phase. Very brief unobserved
runner changes cannot be ruled out solely by sampled API metadata; inspect the
actual runtime diagnostics during physical validation. OS page cache can benefit
cold loading even with an empty `/api/ps` list.

**Warm residency is not prompt-cache reuse.** This exact-question replay is
deliberately vulnerable to cache reuse, which must not be hidden:

- Positive `prompt_eval_cached_count` in either sample invalidates an uncached
  conversational-speed claim (`prompt_cache_reuse`).
- Absent, malformed or negative cache counters are unknown, not zero. Both actual
  requests are retained, but the pair is invalid (`prompt_cache_unverified`).
- Different evaluated prompt-token counts invalidate comparability even when
  both counters report zero (`evaluated_prompt_work_changed`).
- No guessed cache-disable option, daemon restart, artificial prompt padding or
  third inference is used to force a favorable result. An older backend may make
  this strict uncached comparison impossible; report that limitation explicitly.

Only a completed, matching pair with reported zero cache hits and equal positive
prompt work can set `valid=true`. `uncached_warm_under_two_seconds` then reflects
the **measured** warm meaningful-text time, not a subtraction. On any failure it
remains null, the overall status is `invalid_cold_warm`, and the CLI exits nonzero.
Already-completed cold data and failed warm phase/telemetry remain in the report.
The report is written after each phase and on handled failure/interrupt; forcibly
killing the parent or losing power cannot guarantee a final write, so any remaining
`incomplete` status must be rejected.

Even a technically valid pair stays `quality_accepted=false` and
`cold_warm_complete_quality_pending`. Review both full answers against the exact
facts (New Delhi; north of the equator), instruction compliance, contradictions,
hallucinations and natural endings. No keyword match or fast first token can
certify correctness. The broader [model quality checklist](offline-model-selection.md)
still applies before selecting a primary model.

## Windows evidence and remaining physical validation

Current results and the complete test log are in `offline-model-windows-results.json`
and `offline-model-windows-tests.log`: **1,046 run, 1,045 passed, one Linux-only
skip in 61.493 s**; **282 targeted tests passed in 8.021 s**. All 21 harness
tests (11 existing and 10 new) passed separately in 0.526 s. Compilation,
legacy `--plan-only`, diff whitespace and protected-artifact checks passed.
All 555 hashed protected/unrelated files were preserved. Tests use synthetic timing/counter data;
they do not establish that Llama on this Pi meets two seconds. Focused coverage
includes sequencing, expiry, wrong resident identity/context, failed cold loading,
thermal/deadline cancellation, partial reporting and cache/reload invalidation;
existing single-request fixture/routing tests continue to run.

On hardware, verify exact installed runtime/digest, empty initial residency,
unchanged context, both independent timers, actual telemetry, stream cancellation,
natural expiry, cache-counter semantics and both answers. A repeated fact question
is not a representative conversation: after cooling is installed, separately
validate diverse uncached questions, RAG, memory and complete end-to-end speech.
The objective remains substantially faster, correct offline conversation; no
sub-two-second assistant, model speedup or production-switch recommendation is
established by these Windows tests or by arithmetic on the earlier cold sample.

Intended extension files:

```text
assistant/tests_offline_models.py
assistant/tests_warm_benchmark.py
scripts/compare_offline_models.py
docs/offline-model-selection.md
docs/warm-llm-benchmark.md
docs/offline-model-windows-results.json
docs/offline-model-windows-tests.log
```
