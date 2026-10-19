# Comprehensive offline quality evaluation, v1

## Current decision

**Keep Qwen3.5 4B as the production reference/default. No replacement is
recommended.** This change prepares evaluation infrastructure; it does not run
Pi inference, download a model or change production settings. Windows tests of
fake answers establish harness behavior, not intelligence or latency.

The existing Llama technical sample was fast but confused cycles with
instructions. Its substantial prefix-cache reuse and incorrect answer prevent a
general speed or quality conclusion. The new bank explicitly tests that error
and harder faculty questions. `benchmarks/offline_quality_pending_v1.json` is
the honest initial report: zero tested questions, no quality mean and pending
acceptance. It contains no fabricated model scores.

## Versioned workload

The source is `benchmarks/offline_quality_v1.json`, schema 1, bank version 1.0.0.
The fixed 100 unique IDs are grouped as follows:

| Category | IDs | Count |
|---|---|---:|
| General knowledge | GK01–GK10 | 10 |
| Science and mathematics | SM01–SM10 | 10 |
| Computer science/programming | CS01–CS10 | 10 |
| IoT/ESP32/sensors/embedded systems | IO01–IO10 | 10 |
| Pi/Linux/networking | LN01–LN10 | 10 |
| AI/ML/LLMs/edge computing | AI01–AI10 | 10 |
| Logical/numerical/multi-step reasoning | RE01–RE10 | 10 |
| Actual multi-turn context | MT01–MT08 | 8 |
| Local RAG and older conversation memory | RM01–RM08 | 8 |
| Hallucination resistance/uncertainty | HU01–HU06 | 6 |
| Everyday conversation | EV01–EV04 | 4 |
| Safety-conscious device guidance | SF01–SF04 | 4 |

Each record has exact public question text, category, difficulty, expected facts
or reasoning, accepted alternatives, explicit failures, setup prerequisites,
sequence membership, answer style/completeness, safety flag, token ceiling and
question-specific 0/3/5 anchors for all six review criteria. Easy, intermediate and
difficult questions include numerical traps, uncertain evidence, serial/MQTT
semantics, CPU throughput, quantization/capacity, local versus edge computation,
model loading versus prompt caching, RAG versus pretrained knowledge, speech
pipeline boundaries, privacy and failures.

`benchmarks/offline_quality_reviewer_v1.md` lists the **25 reviewer questions**,
selected by the bank's `reviewer_ids`. They are part of the 100, not extra
observations. Architecture, offline verification, hardware and speech limitations,
security/privacy, retrieval, reasoning, benchmark methodology and future
tradeoffs are represented. Skeptical follow-ups use actual model answers.

Freeze v1 once real trials begin. Preserve its contents with reports. Bank hashes
use UTF-8 canonical JSON (sorted keys, compact separators, unescaped Unicode),
so Windows/Pi line endings and formatting do not change identity. They prevent
combining revised questions/rubrics unnoticed. Any substantive revision
needs a new version and separate results. Do not tune prompts to memorize this
bank or report it as an unseen test set. Later acceptance should also include
independently authored held-out questions and real operator sessions. The bank
is broad but finite and does not establish all-domain reliability.

Technical reference provenance is recorded in `source_notes`, including official
[Raspberry Pi GPIO guidance](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html),
[Python list semantics](https://docs.python.org/3/tutorial/datastructures.html),
[Ollama usage metrics](https://docs.ollama.com/api/usage), and the accepted
repository architecture. Synthetic Lumen facts are test data, not physical board
specifications. Recheck installed-runtime counter semantics on the Pi.

## Runner and isolation

`scripts/evaluate_offline_quality.py` is a separate quality interface over the
accepted diagnostics. It imports model inventory/digest/residency checks,
pressure guards, locking, load/cache accounting and process-group cleanup from
`compare_offline_models.py`; database bootstrap from
`validate_pi_performance.py`; and loopback/configuration/monitoring helpers from
`validate_offline_voice.py`. Existing benchmark interfaces and production code
are unchanged. No second LLM or model-loader implementation is added.

Only these exact installed local GGUF tags are allowed:

* `llama3.2:1b`, prefix `baf6a787fdff`.
* `qwen3.5:4b-q4_K_M`, prefix `d8b0f5e9760c`.
* `llama3.2:3b`, prefix `a80c4f17acd5`.

The [3B trial procedure and current regression evidence](llama3b-evaluation.md)
extend the original two-model workflow. The versioned bank and reviewer subset
remain unchanged. The initial pending v1 JSON is historical; generating a new
empty report now lists all three models as pending.

Record the full verified digest; recheck immediately before every inference and
check residency while monitoring and afterwards. Conflicting/multiple residents
or a changed resident context refuse the workload. A cold request requires no
resident model; an initially warm request and second scenario turn require the
same resident model with context 4096. Loading is just the ordinary selected
Ollama request, never a pull, restart, forced unload or alternate-model fallback.

Inference is guarded in both parent and child to **Linux Raspberry Pi 5** using
the device-tree model. Windows and other Linux hosts may validate/list the bank
and process saved reports, but cannot use this interface for model inference.
No cloud, laptop or remote backend is permitted. Only loopback HTTP hosts are
accepted; proxies/redirects are disabled and Python sockets reject non-loopback
connections. This is an application guard, not an OS network sandbox. Installed
Ollama/local dependencies remain trusted. Core offline operation still needs
operator evidence with external connectivity unavailable; no firewall changes
are performed by this tool.

The parent/child use fresh temporary SQLite/media/cache storage before Django
initializes. Public fixtures alone are inserted. Existing `.env`, deployed
SQLite, personal histories, knowledge files, model inventory, services, audio,
firmware, display protocol, wiring and GPIO settings are not written. Online
retrieval/persistence, device actions and display output are disabled **only in
the quality child**. No microphone, Piper or ALSA is invoked. Spoken clarity is
judged from text; this is not a physical voice-latency benchmark.

The child uses actual `AssistantService.process_message_stream`, routing,
resource policy, lexical retrieval and persistence. It does not force an answer
when the real service returns a grounded refusal. Such a direct-policy response
is labeled `inference_requested=false`, can be inspected as assistant behavior,
and receives no LLM quality/completion credit. Routing failures are visible, not
silently bypassed to obtain a model answer.

### Sequences and known context

Select one ID. Ordinary questions run once. Selecting MT02, MT04, MT06 or MT08
runs its first turn and then the selected follow-up, **at most two requests**.
First-turn selection runs only that first turn. The service persists the actual
first user message and generated answer; the second turn reads those actual
rows. Even an erroneous answer is retained. No evaluator reference or canned
correct assistant answer is inserted. A truncated, failed or invalid first turn
stops the scenario before the follow-up.

RAG uses one isolated indexed Lumen document. The missing serial-number question
has no serial field in that document. An adversarial variant appends an unsafe
instruction to test treating retrieved text as evidence rather than authority.
Memory fixtures contain only synthetic USER facts and unrelated filler, with
older retrieval forced by zero recent-history allowance. They test a present
codename, missing codename, provenance and a later correction. A new database
per invocation prevents a missing-memory test inheriting another trial's answer.
These are deliberate fixture prerequisites, not claims about personal memory.

Only question text, generic answering/style instructions, legitimate retrieved
fixture content and actual prior turns may reach inference. Expected answers,
alternatives, failures, completeness targets, human rubrics and prerequisite
descriptions remain evaluator-only metadata. Tests inject canaries into those
fields and inspect actual service-built requests, including real RAG retrieval.
Reports retain those exact **public isolated messages** for evidence review;
this is not a facility to export production prompts or private histories.
Longer skeptical questions may miss lexical relevance thresholds. That is an
upstream retrieval limitation to report, not a reason to lower thresholds or
attribute missing evidence solely to model intelligence. Inspect actual messages
and route flags before judging whether the model ignored evidence or never
received it.

## Matched settings and bounds

All questions use seed 42, temperature 0, context 4096, thinking off, CPU tuning
omitted and request-local idle expiry 60 s. Budgets are 96 tokens for easy,
160 for intermediate, 192 for difficult and 128 per multi-turn turn. There is no
forced 25-word answer rule. An explicit one-sentence question retains its style.

For this **quality-only** child, the normal policy ceiling and normal RAG style
ceiling are 192, allowing requested complex answers instead of the production
RAG default's 64-token style cap. ECO/reduced and protective ceilings remain
configured values and may shrink an answer budget. Policy settings, actual
effective budget, profile, context limit, request controls and actual system/user
prompt hashes are recorded. These text-quality settings differ from production
voice budgeting; do not use them to claim unchanged production voice speed.

Starting temperature must be **below 55°C**, including the first pre-inference
check after initialization. Abort at **70°C** or current undervoltage, frequency
capping, throttling or soft temperature-limit pressure. Historical flags alone
do not trigger refusal. Require valid temperature/current flags/measured clock
and at least 512 MiB available RAM while running. Before a cold load, require
available RAM ≥1.5 × installed artifact bytes +512 MiB, a conservative allowance
that is not a guarantee of actual model RAM consumption. Unknown artifact size
refuses a cold load. Already resident models still require the runtime RAM floor.

One invocation has the existing monitor's **60-second sampled workload bound**,
including migrations, loading and any preceding scenario turn. Service/socket
timeouts are 40 s. Metadata calls are bounded at 3 s; telemetry and checks add
to the nominal 0.5 s polling interval. These are sampled best-effort safety
bounds, not hard real-time guarantees. A second turn remains in the same short
workload and must stay below the running cutoff. It never weakens guards to
finish a difficult response. The Pi's own thermal protection remains enabled.

Long Qwen answers or cold reloads may not finish under these uncooled bounds.
Record an incomplete result and defer to a cooled, separately approved protocol;
do not shorten only the reference's answer or increase limits to obtain a score.
Never run the 100 questions, reviewer panel, shell loops or repeated model
switches unattended on the uncooled Pi.

Child partial raw deltas are saved atomically as they arrive. On failure,
interruption or pressure the parent retains the latest available partial report,
terminates/reaps only its owned child process group, and removes the exact owned
temporary directory. It never terminates Ollama or other services. Server-side
inference cancellation may lag client termination; killing a client is not proof
that backend work stops instantly. Hard power loss cannot guarantee cleanup.
Choose new output directories: existing reports are never overwritten.

## Recorded measurements

Reports identify commit, runner/bank hashes, bank version, trial/sample UUIDs,
selected workload, exact model/full digest, artifact details and Ollama version.
Every generated answer retains complete raw engine text and service response,
not a shortened preview. Partial output remains partial; it is not assigned an
invented completion time or zero cache count.

Telemetry, identity probes and per-delta partial-report writes add diagnostic
overhead. Application elapsed times include that work; backend prefill/decoding
durations remain separate. Use the same harness on both models, disclose output
length and storage conditions, and do not equate these text trials with production
voice latency or compare them directly to older differently instrumented runs.

Per turn: resident identity/context/model size before/after; independent request
timing; alphanumeric engine first-text time from post-probe request readiness;
application first-text time from service dispatch; complete service-answer time;
load, prompt-evaluation and decoding durations; prompt/cached/generated token
counts; generation throughput; cache accounting; requested/effective budgets;
truncation and route/RAG/memory/context flags. Timings use monotonic clocks, not
subtraction of cold loading to manufacture warm inference.

Warm is an operational label: matching resident context and reported load ≤100
ms. A resident reload/unknown load invalidates the timing sample. Model warmth
does not imply prompt-cache reuse. Cache counters remain unknown when missing;
derived uncached tokens assume the installed backend's reported total semantics.

Timestamped temperature, requested/measured CPU clock, available RAM, current and
historical flags are retained. Whole-workload and per-turn summaries give maximum
**observed** temperature, minimum available RAM, frequency range and current
pressure observations. These are sampled observations, not proof no brief event
occurred between samples. Model resident size is separate from system RAM.

`done_reason=length` **or reaching the effective token ceiling** marks truncation,
even if an ending sounds fluent. Truncated, aborted, invalid, missing-timing and
direct-policy outputs are excluded from valid complete-model latency/coverage.
Retain them for failure and truncation review. Do not count untested questions as
failures or passes; report tested/reviewed/complete coverage explicitly.

## Independent human review

Create a worksheet from each raw report with `report_offline_quality.py`.
It copies the exact answer plus question-specific criteria, alternatives and
failure anchors. Review entries bind to trial/sample IDs, question, model/full
digest, bank hash and hashes of both raw engine and service answers. Mismatched
or duplicated bindings are refused rather than silently grading another answer.
The public-message snapshot is also bound to the review and copied into the
worksheet so a reviewer can see exactly which evidence and prior answers reached
the model.
Keep raw reports immutable; complete a separate review file and retain rationale.

| Criterion | Weight |
|---|---:|
| Factual correctness | 35% |
| Completeness | 20% |
| Reasoning and technical precision | 15% |
| Grounding/hallucination resistance | 15% |
| Relevance/instruction-following | 10% |
| Spoken-answer clarity from text | 5% |

Use scores 0–5: 0 absent/incorrect/misleading; 1 severe failures; 2 substantial
errors/omissions; 3 partly satisfactory with material gaps; 4 sound with minor
omissions; 5 fully meets that question's criteria. Fractional scores are allowed.
Weighted total = Σ(weight × score/5), maximum 100. All six scores need written
criterion rationales, plus an overall rationale, reviewer identity, role and an
explicit independent-assessment declaration. Missing, boolean, out-of-range or
nonfinite scores remain pending. No keyword matcher or model self-grade is used.

Record explicit booleans for task success, hallucination, critical safety
failure, fabricated citation and fabricated memory. Any flagged failure needs
details identifying the claim and evidence. Judge a citation against a real
source, memory against actual provided turns, and RAG against retrieved evidence.
Do not require one exact wording where an accepted alternative is correct.
Read the actual preceding answers for follow-ups, including mistakes. A fast,
confident wrong answer fails. Audio naturalness still requires the separate
physical voice procedure; text readability cannot establish spoken delivery.

Recommended practice: independent faculty/human review without seeing latency
first, a second reviewer for technical/safety disputes, and a retained adjudication
record. The tool can require a declaration but cannot prove independence or
reviewer expertise. No external evaluator is contacted automatically. Do not
send private data or call cloud models to complete this offline evaluation.

## Reports and acceptance

The report tool groups full digests separately and reports category/difficulty
means, reviewer-panel score, observed reviewed-question mean, coverage, critical
failures, hallucination count, context/RAG/memory task-success rates, truncation
rate and cold/warm latency distributions split again by cache-reuse cohort.
Means weight unique question means equally; repeated question reviews first
average within that ID. Denominators are explicit. Pending questions contribute
no invented score. A direct policy response receives no model quality credit.
Task-success rates are reviewed **turn/trial** rates, not a claim that a whole
multi-turn scenario passed when only one turn did.

Valid paired timing requires unique predeclared trials, identical bank/scenario,
question/setup/instruction/system/user hashes, options, effective policy/context
limits, Ollama version, the same source commit/runner hash, same cold/warm class,
known zero prompt reuse on both, and pre-inference temperatures within 3°C. Any unequal/unknown condition is
listed and yields no ratio. Multiple trials require a deliberate predeclared
pair rather than best-of-many selection. Cold and warm are separate. Cached
observations remain visible but do not become uncached conversational evidence.
Different weights/templates/tokenizers, token counts, resident memory and actual
prior generated answers are inherent model differences and must be reviewed.
First-text and complete-answer ratios are reported separately; neither certifies
quality or audible voice latency.

Pairs name `candidate_model` and `reference_model` explicitly. Each supplied
Llama candidate is compared independently with Qwen4B; when both Llamas are
supplied, a 3B-versus-1B pair is also reported. A missing/duplicate/invalid trial
blocks only its relevant pair, not a valid old 1B-versus-Qwen comparison. Pairing
saved reports never loads models or chooses a production model.

Provisional gates remain: overall ≥85/100, every category ≥80/100, zero critical
safety failures, reliable grounded RAG/memory/context, and a material matched
latency advantage. Full 100-question complete nontruncated independently reviewed
LLM coverage is required before declaring full-bank score gates passed. An
observed critical safety failure or fabricated evidence fails its gate immediately;
no observed failures in a small sample does not prove safety. Reliability and
material latency require independent full-coverage adjudication; the software
does not invent a universal percentage meaning "material" or auto-recommend
replacement. Latency eligibility does not imply an answer passed quality.

These are provisional engineering gates, not statistical proof of open-domain
accuracy. Keep missing measurements pending and retain every failed/aborted
attempt so reporting cannot quietly select only successes. Existing production
access-control findings remain separate; no authentication rewrite is included.

## Consolidated Pi procedure: one selected question

1. Use the reviewed feature checkout and its Python environment. No service
   restart, deployment or `.env` edit is needed. Validate locally with:
   `python scripts/evaluate_offline_quality.py --validate-bank`.
2. Inspect installed tags/digests/residents with the accepted read-only inventory:
   `python scripts/compare_offline_models.py --inventory --output /tmp/quality-inventory-01`.
   If another model is resident, stop and allow natural idle expiry. Do not force
   unload or start both. If expiry never occurs, defer the trial. Arrange a quiet
   request window; the diagnostic lock coordinates harnesses, not the production
   application. Do not stop/restart production services to manufacture a condition.
3. Check <55°C, adequate RAM and no active pressure. Select **one** question and a
   new output directory. The first suggested trial intentionally retests the
   known technical misconception, not an easy memorized factual question:

   ```bash
   python scripts/evaluate_offline_quality.py --question CS01 --model llama3.2:1b --output /tmp/quality-llama-CS01-01
   ```

4. Read `report.json`: any refusal, abort, truncation or invalid sample is not a
   complete pass. End the inference session and allow cooling. Do not loop other
   questions. On a later cool operator-controlled session, use the identical ID
   and controls for Qwen by changing only `--model qwen3.5:4b-q4_K_M` and the new
   output directory. Match warm/cold and cache conditions; if they differ, retain
   both results but do not call them a matched speedup. Longer reference answers
   may require cooling before fair completion is feasible.
5. On either the Pi or Windows, create a separate offline worksheet:

   ```bash
   python scripts/report_offline_quality.py --worksheet /tmp/quality-llama-CS01-01/report.json --output /tmp/quality-llama-CS01-review.json
   ```

   Complete every score/rationale/flag/independence field manually. Preserve the
   original raw report. To inspect a contextual follow-up on a **different** cool
   occasion, select MT06; it runs actual MT05 then MT06, not a reference preload.
6. Generate a report from explicit saved files, with no inference:

   ```bash
   python scripts/report_offline_quality.py --reports /tmp/quality-llama-CS01-01/report.json --reviews /tmp/quality-llama-CS01-review.json --output /tmp/quality-summary-01.json
   ```

   Add the separately measured Qwen report and completed worksheet explicitly
   when available. Empty `--reports` produces a fully pending template. Grow the
   evaluated set gradually under safe conditions; do not execute the full bank
   unattended. After cooling, plan broader matched sessions and held-out checks.

Remaining physical evidence: all actual answers/scores, representative matched
latencies, safe sustained thermal behavior, installed counter semantics, actual
offline operation, and the separate mic/STT/TTS/speaker/TFT experience. This
milestone establishes none of those from Windows tests alone.

## Initial v1 Windows regression evidence (10e1060)

Final full suite: **1,096 run, 1,095 passed, one Linux-only pipe test skipped**,
36.518 s. Focused quality/model/voice/policy set: **385 run, 384 passed, one
skipped**, 8.104 s. All **27 new evaluation tests passed**. They cover exact bank
counts/panel/schema, evaluator-canary exclusion, actual-turn retention, real
synthetic RAG and older-memory retrieval, missing context, model identity and
residency refusal, pressure/timeout/cancellation, parent-owned cleanup and
partial reports, independent scoring, bindings, coverage and matched-condition
refusal and canonical bank identity across Windows/Pi line endings. A standalone Windows invocation also confirmed refusal before a model
process started. No Pi inference or physical audio was exercised.

All 555 protected/unrelated files matched original hashes and inventory. The
complete Windows test log is `offline-quality-windows-tests.log`; scalar results,
source hashes, exact ten-file inventory and limitations are in
`offline-quality-windows-results.json`. Production implementation/defaults and
accepted benchmark interfaces are unchanged. No main merge or deployment occurs.
