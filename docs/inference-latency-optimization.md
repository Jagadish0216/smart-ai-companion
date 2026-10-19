# Local inference latency: investigation and controlled trials

For the currently uncooled Pi, use the newer
[`offline-model-selection.md`](offline-model-selection.md) procedure and its
single-request harness. Do not run this document's extended multi-round A/B
while cooling is unavailable. Production defaults remain unchanged.

2026-10-19. Working branch: `system-performance-audit-v1`; validated comparison
baseline: `3bb84a9ca6987f6fd11df366be97aabbdef929b8`. Finalization and feature-only
publication were explicitly authorized; Pi deployment remains a separate step.
No Pi deployment, model download/replacement, service restart, firmware, wiring,
ALSA defaults, TFT command or authentication change was performed.

## Evidence versus hypotheses

The following Pi measurements were supplied by the user. Raw device reports and
the exact Ollama build were not supplied in this turn; this investigation did
not repeat inference on the Pi.

| Observation | Supported conclusion | What is not established |
|---|---|---|
| Load 1–2 ms; warm Piper ready 0.01–0.02 ms | Repeated model/voice loading is not the principal cost of these warm requests | Cold boot latency or the duration of real Piper synthesis itself |
| 103–115 prompt tokens, 21–23 prefill tokens/s, 4.8–5.3 s prefill | Prefill accounts for about five seconds before useful generation | That Python prompt assembly or HTTP overhead accounts for those seconds |
| 2.7–4.0 output tokens/s | Decode throughput dominates long answers | A safe CPU-option change will necessarily improve this backend |
| Nine distinct queries report zero cache hits | Do not budget latency assuming cache reuse | That all versions/backends lack cache, or that an absent counter means zero |
| Brief about 13 s; normal about 18–26 s | Decode and speech-unit availability remain important after prefill | That playback starvation equals audible gaps |
| Detailed 81–87 s, 192 output tokens, `done_reason=length` | A real generation ceiling was reached; answer completeness needs review | That every length stop is factually wrong, or that silently extending every answer preserves responsiveness |
| Historical throttle/frequency-cap flags; RAM available, no swap | A power/thermal event occurred since boot; memory pressure was not evidenced | Sustained *current* throttling during each request, or proof that cooling alone will remove all latency |

Arithmetic consistency check, not a benchmark: 192 tokens at 2.7–4.0 tokens/s
requires roughly 48–71 seconds of decode alone. That is compatible with the
reported detailed-request duration after prefill and speech/drain costs. Do not
add overlapped generation, TTS and playback durations to estimate total time.

## Request, tokenizer and cache inspection

`LocalLLMEngine` sends `/api/chat`, `think=false`, `num_ctx=4096`, a finite
request-specific `num_predict`, and the existing 24h keep-alive. It does not
tokenize locally, submit images/tools, request reasoning, or perform an extra
model-inventory call on every generation. Chat templates and tokenization belong
to the installed Ollama/model. Changing to raw completion or a hand-written
template would risk tokenizer/stop-token correctness and is not implemented.

Voice service calls already set `system_prompt=""`, avoiding the generic text
system prompt a second time. The remaining instruction contains invariant
accuracy/action/grounding safety, voice style, mode and any necessary supplied
context. Ordinary voice requests omit unrelated old history and implicit RAG;
explicit recall/document questions retain those paths. Online date/freshness
context is request-specific for correctness and must not be stripped for caching.
The 103–115-token input is already modest, not evidence of a huge RAG prompt.

Mode changes, supplied history, RAG and online evidence legitimately change
input. The leading voice safety text remains invariant. No random timestamp,
request ID or varying telemetry is inserted into ordinary LOCAL prompts.
Nevertheless, a stable prefix is **not proof of useful cache reuse**. Qwen's
[official 4B model card](https://huggingface.co/Qwen/Qwen3.5-4B) describes a hybrid
Gated DeltaNet/attention layout; cache behavior must be checked on the actual
runtime. An [upstream CPU-cache issue](https://github.com/ollama/ollama/issues/14780)
reports similar symptoms, but is not proof of the same cause on this Pi/build.
No cache workaround, prompt padding, fake conversation history, persistent
cache key, warm-up loop or backend patch is deployed.

The A/B records installed model digest/quantization, Ollama version, model
architecture/context metadata, and hashes of its template/parameters. Raw model
system/template content is not saved. Inspect the first labelled warm-up and
the immediately repeated brief fixture, then distinct fixtures. Keep absent
`prompt_eval_cached_count` absent. Require a supplied positive counter or reduced
actual evaluated work on repeatable identical requests before claiming reuse;
timing alone is vulnerable to frequency/load noise. The old Pi CPU path may not
emit the newer counter. Do not change tokenization based on a guessed diagnosis.

## Implemented changes and approval boundaries

| Change | Justification | Default behavior / risk |
|---|---|---|
| Lazy capability registry during voice planning | Non-action planning constructed a capability registry it never used, including a possible SQLite RAG availability query; service constructed its authoritative registry again | Default safe optimization: removes the unused check. Actual service routing/grounding and action availability checks remain intact. This is a small Python/SQL improvement, **not a five-second prefill fix** |
| Optional `OLLAMA_NUM_THREAD` and `OLLAMA_NUM_BATCH` | Exposes reproducible CPU experiments through normal chat and warm paths | Both default unset: original backend defaults preserved. Explicit integers validated (threads 1–64, batch 1–2048). No automatic tuning or extra model |
| Optional `VOICE_STREAM_FIRST_SENTENCE_MIN_CHARS` | Existing policy can hold a complete short first sentence until more text/completion merely because it is under the 60-character phrase minimum | Empty default preserves accepted packing. Trial value 30 releases only a complete safe sentence; clause/word minima remain 60/100. Bounds 20 through configured first-chunk minimum. More TTS units can worsen prosody/gaps; listening approval required |
| Optional `VOICE_COMPACT_STYLE` | Some style instructions can be shortened without deleting the separately applied safety/grounding policy | False default. Candidate changes style only; explicit format priority remains. Generated quality has **not** been certified. Do not enable production-wide without A/B acceptance |
| Consolidated `benchmark_inference_ab.py` | Exact, reversible, source-pinned comparison rather than different questions or Windows timing claims | Only user-invoked, installed models; no systemd/config/model mutations. Uses temporary baseline source and isolated SQLite/media |

The warm management command receives the same optional CPU settings as chat,
and empty model warming preserves them too. Inference and audio retain their
existing timeouts/cancellation. The known slow-trickle socket deadline limitation
remains: cancellation can wait for an active read to return. No additional
inference retry/continuation is introduced.

The [Ollama API source](https://github.com/ollama/ollama/blob/main/api/types.go)
defines `num_thread` and `num_batch` as runner options. Changing them can reload
the runner. Invalid values fail before network access; backend HTTP/stream
errors abort the trial without a fallback configuration. These are supported
upstream parameter names, not proof that every installed backend honors them:
Ollama does not expose effective thread/batch values through the inspected API.
Verify support against the recorded installed version/runner diagnostics before
accepting a CPU trial; silently ignored options cannot establish a tuning win.
Changing options can reload
a runner; record load time and discard labelled warm-ups. Start with 4 versus 3
threads, then a separate batch 128 versus 256 comparison. These are hypotheses,
not recommended new defaults. More threads/bigger batches can compete with
Piper and audio or worsen thermals. Do not change parallel-request count, affinity,
governor, overclock, flash attention or KV quantization without separate evidence.
Context 2048 can be tried separately for this short synthetic suite; it does not
reduce the number of actual prompt tokens and can truncate legitimate long
RAG/history. Production context remains 4096.

## First audible speech and detailed-answer quality

The existing buffer protects abbreviations, formatted numbers, URLs, brackets,
quotes and incomplete words. Later units pack near 180 characters; accepted
first-phrase defaults are minimum 60/target 100. The opt-in short-sentence rule
does not turn arbitrary 30-character substrings into speech. Tests reconstruct
the exact spoken text and retain protection/packing rules. It may help normal
answers whose first complete sentence arrives early; a one-sentence brief answer
with punctuation only at completion may see little benefit. It cannot remove
the measured prefill delay or guarantee natural Piper prosody.

For detailed requests, keep the default 192 cap until quality/time comparisons
are reviewed. Preferred strategy: lead with the answer and structure the
requested essential points so the response can finish within its budget, while
honouring explicit format requests. Test concise style separately. If required
detail cannot fit, offer a user-requested continuation or an explicitly slower
longer answer rather than silently cutting facts or issuing a hidden inference
call. `--profile detail-budget --detail-budget 256` is an isolated trial; existing
ECO/unknown/protective caps still win. At current measured decode speed, more
tokens cost substantial time and heat; no global cap increase is implemented.
Do not declare an answer complete merely because it ends with punctuation.

Quality acceptance for **every** measured fixture and both arms:

- Correct brief fact, no filler or invented extensions.
- RAM/storage and solar explanations contain necessary distinctions/causality.
- Detailed answer covers fetch, decode, execute and a coherent example; no
  unfinished last step, unsupported claims or missing requested parts.
- Lumen answer uses only the supplied 3.3-volt fact, without adding processor or
  hardware relationships not present in the synthetic note.
- Recall returns Amber Finch only from the synthetic conversation; no added
  personal claims or cross-conversation inference.
- Reject factual/grounding regression, missing requested detail, unnatural cuts,
  new clicks/stutter or material worsening in perceived pauses even if TTFT falls.

Keyword screens in JSON are smoke checks, **not factual-quality grades**. Human
quality and acoustic-onset fields start null. No LLM judge/background model is
used. Preserve raw synthetic answers so reviewers can judge completeness and
length stops. Real documents and personal conversations are never copied into
the benchmark.

## Smaller-model comparison (not a replacement decision)

The [official Ollama tag registry](https://ollama.com/library/qwen3.5/tags)
currently lists the following artifacts. These are approximate **download
sizes, not measured RAM requirements**; aliases and hashes can change.

| Candidate | Registry artifact size | Expected trade-off (hypothesis) |
|---|---:|---|
| Existing `qwen3.5:4b-q4_K_M` | about 3.3 GB | Accepted quality; measured CPU latency is the baseline |
| `qwen3.5:2b-q4_K_M` | about 1.9 GB | Smaller same-family Q4 candidate; potentially less CPU/weight traffic, with increased reasoning, instruction and grounded-detail errors |
| `qwen3.5:0.8b-q8_0` | about 1.0 GB | Small capacity bound; quantization differs as well as capacity. Potential latency benefit, larger quality risk; not an equivalent 4B Q4 replacement |

Do not assume an official `0.8b-q4_K_M` tag exists; it was not listed in the
checked registry. Avoid `latest`, MLX (Apple-specific backend), and MTP/draft
variants for this controlled Pi experiment. Runtime RAM also needs model state,
context/recurrent buffers, projector/runtime allocations, Piper, Django and OS.
The benchmark records `/api/ps` resident allocation and available RAM/swap; inspect
actual installed digests and metadata instead of estimating total memory from
parameter count alone. No absolute speed or RAM saving is promised.

Models must already be installed through a separately approved process. The
script never calls pull/create/delete or changes `.env`. Models must be local
GGUF artifacts; remote/cloud aliases are rejected before
inference. Resource/policy refusal responses are rejected as timing samples.
Explicit alternative
selection affects only the benchmark process's requests; normal app configuration
stays on the accepted model. Loading an alternative or changing CPU/context
options can alter Ollama residency/cache and trigger reloads. Use a quiet test
window; the script does not forcibly unload shared models or restart the daemon.
Afterwards ordinary app requests still select the original configured model.

## Reproducible consolidated Pi comparison

After a separately approved feature commit is available on the Pi, use its exact
full SHA. Real execution requires Linux and clean tracked candidate files. It
reads baseline `3bb84a9` from local Git history into a disposable source archive;
it does not switch, reset or modify either checkout or main. It uses the same
interpreter and inherited configuration for both versions, overrides only
benchmark settings in memory, and redirects all ORM writes to a disposable
database/media directory before Django setup. No production HTTP API mutation
or private-data fetch is used. Read-only normal health/connectivity probes may
run during resource-policy evaluation.

Run from the candidate repository, as the ordinary application user, with
interactive/wake/dashboard inference idle. The invocation below changes only
the first-sentence trial; substitute `defaults`, `compact`, `cpu` or
`detail-budget` for separate comparisons. Each call creates a new report folder.

```bash
FEATURE_SHA='REPLACE_WITH_APPROVED_FULL_CANDIDATE_SHA'
./venv/bin/python scripts/benchmark_inference_ab.py \
  --expected-commit "$FEATURE_SHA" --profile first-sentence \
  --rounds 2 --context 4096 --threads 4 --batch 128 --play-audio \
  --output "$HOME/inference-ab-sentence-$(date +%Y%m%d-%H%M%S)"
```

Controlled variations, one per new run:

- `--profile defaults`: source-version A/B with accepted prompts/audio settings.
- `--profile compact`: only candidate style changes; safety/evidence unchanged.
- `--profile cpu --threads 4 --candidate-threads 3`: one thread-count change;
  retain the same explicit batch/context/model. Alternatively change only batch
  using `--batch 128 --candidate-batch 256`. Parser rejects changing both at once.
- `--profile detail-budget --detail-budget 256`: only candidate detailed token
  request changes; compare effective resource-capped budget as well as quality.
- `--profile defaults --candidate-model qwen3.5:2b-q4_K_M`: explicit **installed**
  model alternative; all common request options unchanged. Do not combine model
  substitution with a style/segmentation/CPU trial.

The fixed six queries cover brief, two normal, detailed, synthetic RAG and
synthetic recall. Each arm block has one labelled brief warm-up, then the same
six fixtures; two rounds use **A-B / B-A** order. Piper warms outside measured
requests. Defaults are a fixed seed 42 and temperature 0 in **both** arms;
these experimental controls differ from unconstrained production sampling, so
do not compare absolute times directly to the earlier nine-query run. Seed 42
is reproducibility assistance, not a guarantee of identical outputs on different
models/backends. Fixture hash, request hash/options, source hashes, effective
budget and resource profile are recorded. No independent inference classifier
or quality judge is called. Expect this full comparison to take several minutes.

Ten-second inter-block cooldown is configurable from 0–60 seconds. Start arms at
similar measured temperatures; repeat after proper cooling if they differ.
Current active power/throttle flags or temperature >=75 C invalidate/stop a block;
historical flags alone do not. A bounded telemetry observer samples during
inference/playback about every two seconds (up to 180 records/request), recording
CPU utilization, requested sysfs frequency, actual `vcgencmd measure_clock arm`,
temperature, separate current/latched throttle flags and RAM/swap. The observer
adds small common measurement overhead and can miss brief events. It never
writes frequency/governor/fan settings. Resource guards are not bypassed.

`comparison.md` summarizes per-fixture medians; `comparison.json` and per-block
JSON retain samples, options, budgets, answers and telemetry. Partial/aborted
runs also retain an explicit condition-difference inventory for each paired
round/fixture: request hashes/options, effective limits, route/resource profile,
model metadata, voice configuration/sink and starting temperature/frequency/flags.
Intentional profile differences are reported too; missing telemetry is unknown,
not evidence of equality. No automatic quality or temperature-match acceptance
is implied. Partial/aborted
runs are marked incomplete. Compare matching profiles/budgets before attributing
time differences. Evaluate TTFT, prefill counts/speed, decode throughput, total
duration, TTS availability, first PCM submission, output completeness and thermal
trend together. No latency win is accepted at the expense of factual quality.

**Audible onset limitation:** software `first_speech` is the existing submission
estimate; `first_pcm_submitted` timestamps the first accepted prepared PCM bytes.
Neither is sound onset at the speaker: leading silence, retained quiet PCM and
device buffering still apply. For actual first audible speech, synchronize an
external recording of the terminal START marker and speaker and enter
`observed_audible_onset_s`; retain measurement uncertainty. The script does not
commandeer the accepted microphone. Omitting `--play-audio` uses ALSA null and is
explicitly labelled as having no physical sound. Do not report null-sink times
as physical playback improvements.

Offline `--plan-only` can run on this uncommitted Windows checkout. It validates
the disposable baseline snapshot and payloads without model inventory, inference,
audio, schema migration or network. It does **not** grant Pi acceptance. Real
blocks have a bounded 30-minute process-session deadline and cleanup; individual
inference retains its existing socket timeout. Safety checks during a blocking
model read can wait until that read returns. Stop a failed block and review its
partial report instead of accepting it as a fast sample.

## Thermal investigation and recommendations

Inspected `resources._throttling`, `_classify_throttling` and
`evaluate_resource_policy`: bits 0–3 are current voltage/frequency/throttle/soft
temperature status; bits 16–19 are history. Current flags select CRITICAL and
PROTECTIVE; history is CAUTION for diagnostics and explicitly excluded from
current scheduling pressure. Existing tests cover each historical flag,
historical+current combinations, warm versus critical temperature and policy
budgets. No thermal-policy change was justified or made. New benchmark tests
also verify history alone does not stop the comparison.

Use measured frequency and current bits to establish whether active throttling
coincides with slow decode. Latched bits cannot date the event by themselves.
Inspect fan/heatsink contact, airflow and power delivery at stock settings.
Raspberry Pi's [hardware documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html)
recommends active cooling for performance and the 27 W supply for Pi 5.
Check existing cooling/power before purchasing or changing anything; no hardware,
fan curve, firmware, PSU wiring, governor or thermal limit was modified here.
Three CPU threads may leave Piper/audio headroom but can also reduce throughput;
only a matched sustained test can decide. Do not increase concurrency or disable
thermal/voltage protections to improve a benchmark number.

## Security and remaining acceptance

Existing endpoint-specific P1 findings remain in `system-end-to-end-audit.md`.
Anonymous data/inference access is a blocker for untrusted-network release;
current wider firewall/Internet exposure and secret contents are unverified.
This performance milestone contains no authentication rewrite and does not
retest live private data, submit remote device commands or alter network policy.

Required next acceptance: run the pinned Pi A/B after separate commit approval,
review all synthetic answers and listen to physical speech, then select (or
reject) each experiment independently. Keep the accepted Qwen model and current
speech defaults until those results support a change. New raw Pi speedup and
quality results remain **pending**.

Windows regression evidence is recorded in `inference-latency-windows-results.json`
and `inference-latency-windows-tests.log`. Windows tests/synthetic payload-size
checks do not measure ARM prefill/decode or natural Piper speech quality.

## Completed Windows checks and files

- Final full suite through the existing isolated acceptance harness: **1025 run,
  1024 passed, one Linux-only skip, 44.827 seconds, exit 0**. An earlier full
  run before the cloud-model safety test also passed (1023 run, one skip).
- Voice buffer, VAD, policy and single-model focused run: **203 passed in
  3.484 seconds**.
- Final focused inference, routing, online, grounding/RAG, memory, streaming,
  single-model and resource-policy run: **363 passed in 7.389 seconds**.
  This includes the new unequal-condition reporting regression test.
- Live baseline snapshot vs candidate offline payload check: all six default
  synthetic requests match exactly. The first-sentence trial also changes no
  model payload. The CPU trial changes only requested thread count across all
  six fixtures. Compact trial input character counts changed from
  `[455, 490, 486, 525, 504, 531]` to `[386, 396, 392, 447, 410, 437]`.
  These are characters, not measured tokens or latency savings.
- An isolated seeded-database check using mocked inference confirms the exact
  benchmark RAG fixture actually routes `local_rag`, and the recall fixture
  actually uses long-term conversation memory. No real model/quality result is
  inferred from those mocked responses.
- Latest harness plan-only smoke produced a completed consolidated report;
  Python compilation and diff whitespace checks passed. No real Ollama/Piper
  inference branch or Linux telemetry/process cleanup was executed on Windows.

Implementation files: `assistant/ai_engine/local.py`, its factory,
`assistant/management/commands/warm_ai_model.py`, `assistant/policy.py`,
`config/settings.py`, and `scripts/voice_loop.py`. Tests:
`assistant/tests_inference_latency.py`, `assistant/tests_inference_benchmark.py`,
and the existing capture-VAD fixture (now asserts normal planning performs no
redundant RAG availability probe). Harness: `scripts/benchmark_inference_ab.py`.
Documentation/evidence: this document, the link from
`pi-performance-optimization.md`, and the two new Windows evidence files.

Only the 14 files listed below are intended for this feature commit. Main,
unrelated/protected artifacts, production configuration and device behavior are
preserved. No Pi performance improvement is claimed.

```text
assistant/ai_engine/__init__.py
assistant/ai_engine/local.py
assistant/management/commands/warm_ai_model.py
assistant/policy.py
assistant/tests_capture_vad.py
assistant/tests_inference_benchmark.py
assistant/tests_inference_latency.py
config/settings.py
docs/inference-latency-optimization.md
docs/inference-latency-windows-results.json
docs/inference-latency-windows-tests.log
docs/pi-performance-optimization.md
scripts/benchmark_inference_ab.py
scripts/voice_loop.py
```
