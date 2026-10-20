# Step 50: Qwen prompt-processing investigation

Repository/branch: `Jagadish0216/smart-ai-companion`, `codex/offline-model-selection`.
Starting commit: `d66dce8881bb987654b6c79e92b043af480352d7`.

## Decision

**No new production prompt edit is justified by the available evidence.** The real voice
service already avoids duplicate text-chat safety, uses an invariant voice safety prefix,
and omits irrelevant history/implicit RAG for standalone questions. The apparent repeated
accuracy/grounding language has different obligations; shortening it would change semantics.
The safe result here is stronger end-to-end payload regression coverage and a ranked,
source-grounded validation plan, not a claim of reduced Pi latency.

Only tests/documentation change. Qwen 4B remains configured, context/resource policies and
all prompt bytes stay unchanged. No Pi/cloud inference, model download/switch, extra resident
LLM, warm loop, forced unload/restart, hardware/audio/GPIO/firmware change, deployment or main
merge was performed. `.env`, `.env.example` and all production source remain unchanged.

## Confirmed evidence and its limits

The operator supplied one correct factual warm Qwen direct-request result:

| Metric | Supplied value |
| --- | ---: |
| Status | sample_complete_quality_pending |
| Resident before / after | yes / yes |
| Load | 1.46 ms |
| Prompt evaluation | 6,015.16 ms |
| Prompt tokens / reported cached tokens | 140 / 0 |
| Decode | 3,970.69 ms |
| Generated tokens | 15 |
| First meaningful text | 6,024.944 ms |
| Complete response | 10.2545 s |
| Maximum observed temperature | 61.7 C |
| Recorded throttling | none |

Backend prompt evaluation almost reaches the entire first-text interval while reported load
is negligible. This identifies **backend prefill**, rather than cold loading or Python string
assembly alone, as the principal pre-text cost in that sample. Derived throughput from these
same counters is about 23.27 prompt tokens/s and 3.78 output tokens/s; these are workload-specific
ratios, not forecasts or measured optimization gains. Do not add overlapping application,
backend, Piper and playback durations to construct an end-to-end voice figure.

A correct factual answer is not a representative reasoning/RAG/memory/quality pass. No
before/after Pi test was performed and no token or timing savings were measured. No recorded
throttling does not establish thermal stability across longer sessions or rule out other
frequency, contention or backend conditions. Exact installed Ollama build, rendered template,
complete direct request and pre-request runner details were not supplied with this task.

## Actual production voice prompt path

The physical loop calls `AssistantResponsePolicy.plan_voice_response`, then the actual
Django `AssistantService.process_message_stream`. The new tests exercise that real service
with a real `LocalLLMEngine` and mocked HTTP replies, rather than bypassing prompt construction.
Mock-reply timings/token counts are test data, not newly observed Pi values.

| Stage | Source | Actual behavior |
| --- | --- | --- |
| Voice classification/style | [plan_voice_response](../assistant/policy.py#L404) | Regex modes: brief/normal/detailed/action/clarification. Existing base says speak directly, no filler/headings, stop when answered, explicit format priority; mode adds length/detail guidance. Default ceilings 32/64/192, later policy-clamped. |
| Conversation/resource plan | [_prepare_message](../assistant/services.py#L224) | Get/create conversation, cached resource plan, bounded history, current USER persistence, authoritative capability registry, routing, optional retrieval/memory. All occur before network inference. |
| Ordinary query context | `ResponsePlan.recent_history_limit/allow_implicit_rag/allow_long_term_memory` | Standalone voice topics default to no old history, no implicit RAG, no long-term recall. This omission is already implemented; do not claim it as a new optimization. |
| Follow-up context | service `_get_conversation_history`, voice reference rules | Actual recent USER/AI turns resolve references; default two, bounded by service/resource limits. History stays in message roles rather than being injected as SYSTEM facts. |
| Memory | `conversations/memory.py` and `build_memory_history` | Relevant older USER statements only, excluding current/recent duplicates; bounded candidates/budget; chronological replay preserves correction order. It is not a full conversation export. |
| RAG | router, `LexicalRetriever`, [select_rag_context](../knowledge_base/services.py#L335) | Local lexical retrieval, not another resident LLM. Deterministic score/source ordering, top-k and aggregate character budget. Manual and saved-online source rules differ. |
| Safety and grounding composition | [_build_generation_instruction](../assistant/services.py#L470) | Voice safety, optional requested-capability status, caller/style instructions, applicable RAG rules/facts, optional RAG-specific instruction, and online evidence only when that route is used. |
| Generic safety suppression | [_generation_kwargs](../assistant/services.py#L503) | For a real local voice engine, sets system_prompt to empty string. This prevents adding LocalLLMEngine's generic text-chat SYSTEM_PROMPT on top of voice safety. |
| HTTP messages | [_build_messages](../assistant/ai_engine/local.py#L316) | One SYSTEM message, chronological relevant USER/AI history, then exact current USER query. Backend applies its own template/tokenization. |
| Request controls | [_build_chat_request](../assistant/ai_engine/local.py#L419) | Configured Qwen, stream, request keep-alive, thinking flag, context and effective output ceiling; optional CPU controls only when configured. No tool/image payload, guessed cache key or extra inference request. |

### Instructions that must not be treated as disposable duplication

The voice prompt retains these logical blocks:

```text
VOICE_SAFETY_INSTRUCTION

existing request-specific / voice-style instructions

applicable capability, RAG or online context (only when required, in the source-defined order)
```

The actual composition puts a requested-capability description directly after safety when
present, before caller/style and grounding blocks. This diagram is a logical inventory, not
a backend-rendered prompt or tokenizer output. See the composition function for exact order.

[VOICE_SAFETY_INSTRUCTION](../assistant/policy.py#L332) requires accuracy, uncertainty,
non-invention of facts/relationships, no prompt/reasoning disclosure, and action/sensor/document/
live-lookup claims only from explicit results. These are distinct from style instructions.
Normal style asks roughly 15–35 words; explicit detail and format requests override defaults.
A shorter phrase such as 'be concise' is not proven equivalent to all those obligations.

[build_rag_instruction](../knowledge_base/services.py#L308) adds concrete restrictions:
reference data is not instructions; no unsupported explanations/background; no invented
relationships between separate facts; say evidence is insufficient; hide retrieval metadata
unless asked. Saved-online references additionally require ignoring embedded commands and
not exposing credentials/secrets. The raw grounded facts, numeric units and negative constraints
are essential, not an oversized duplicate of generic safety. Different local sources legitimately
change the system suffix. Caller-supplied instructions are retained and not blanket-deduplicated.

Default voice plans have no duplicate rag_system_instruction. Text-chat requests still use
their separate generic prompt/capability inventory, by design; changing them is out of scope.
Online retrieval is disabled in these test fixtures; no cloud inference/fallback is introduced.
An enabled live-information route has additional evidence/freshness/security semantics and
cannot be optimized by removing its date/evidence context. Saved online RAG here is already
local stored material, not a network call.

### Proven redundancy and already absent work

- Voice safety appears once in tested real payloads. Generic SYSTEM_PROMPT is suppressed for
  voice. Ordinary prompts do not include the entire capability inventory. Existing single-model
  regressions bound normal fixture instructions below 450 Python characters; that is not a
  backend token count, template size or an argument for removing mandatory rules.
- No cycle UUID, conversation ID, telemetry value, random nonce or wall-clock timestamp is
  inserted into ordinary LOCAL system messages by the tracing/prompt code.
- Same-mode standalone questions have byte-identical SYSTEM content; user questions differ.
  Brief/normal/detailed modes and actual evidence/reference instructions legitimately differ.
- `_prepare_message` selects RAG context, and `build_rag_instruction` selects that bounded
  context again. The new fixture proves idempotence under the configured budget. This is
  duplicated Python selection work, **not duplicated source text or instructions in the
  outgoing message**. RAG defaults are only two selected chunks. No measured preparation cost
  justifies a new bypass API/refactor here, and it cannot explain this plain factual prefill.
- History limit zero already yields an empty Django queryset without executing a history
  SELECT. Resource snapshot ancillary probes can contribute to service preparation on cache
  miss, as recorded in Step 45; they do not account for a separately reported six-second
  backend prompt_eval_duration. No TTL/safety-refresh change is made to hide that overhead.

## Direct benchmark versus production application

The supplied result summary does not identify its command, JSON messages, hashes or effective
policy. A 140-token count and status name cannot identify the application prompt. In particular,
'direct' may mean text-only rather than bypassing the service. Do not infer a rendered prompt
or copy that count onto production voice requests.

| Existing path | Prompt/controls | Comparison limit |
| --- | --- | --- |
| Production `voice_loop` / AssistantService | Actual policy, selected context, one voice safety prefix, configured Qwen; normal keep-alive 24h default; effective mode/resource cap | No diagnostic-only 25-word suffix or fixed seed/temperature is added by the normal engine. Pi effective settings remain uninspected. |
| `compare_offline_models.py::child` | Calls the real service with voice plan, but adds 'Answer in at most 25 words.', requests <=48 tokens, seed 42 / temperature 0 / context 4096, thinking off and 60s keep-alive; seeds isolated public context | Not byte-identical to ordinary production; uses diagnostic policy/options/identity probes. This runner can emit the supplied status string, which alone does not prove it was the runner used. |
| `test_short_answer_thermal.py::infer` | Bare USER-only short-sentence request, no Django prompt/context; model hardcoded to Llama 3B, 32-token ceiling | Cannot be asserted to be the Qwen measurement without evidence of a different runner. Model template still adds backend-defined structure. |
| `benchmark_inference_ab.py` | Service/voice prompt logic with labelled candidate controls; multiple fixtures/arms | Extended A/B is not recommended for the requested future single request or an uncooled session. |

Stable JSON message text is not a view of the model's rendered GGUF/Ollama chat template.
Character counts are not token counts. Actual template/parameters/system metadata and backend
version require separately approved read-only inspection; this milestone does not fabricate them.

## Prefix caching: what is established and what remains unknown

**Established:** the supplied request reported zero cached tokens while the model was resident
and load was 1.46 ms. Python sends ordinary /api/chat messages, an invariant safety prefix,
no fabricated cache key and no client instruction to disable caching. Keeping the model
resident is not the same as reusing its prompt state. Local model-inventory caching in the
engine is unrelated to prompt caching.

[Startup warm_chat_prefix](../assistant/ai_engine/local.py#L562) submits the safety instruction
with a 'Ready.' user message and one output token. Production adds mode/style and applicable
context; the entire SYSTEM content is not identical to that warm-up. Tests verify the shared
leading safety and the legitimate additional suffix. That difference can affect available
prefix work, but **does not prove it causes all zero hits** or that safety-only warming is a
backend defect. No recurring warm-up request, fake conversation history or cache workaround
is added. Changing modes/RAG/history may legitimately invalidate parts of an evaluated prefix.

**Not established:** why this installed Qwen/backend produced zero. Missing exact prior
requests, backend build, rendered template, runner/context history and cache-counter semantics
prevent attributing the result to architecture, a runtime bug, unsupported reuse, prefix
mismatch or cache eviction. Prior audits mentioned upstream architecture/runtime hypotheses;
they are not proof for this installed artifact and are not used to justify a backend patch,
upgrade, new parameter, model switch or weaker safety here. Distinct 1B cache observations
cannot establish Qwen's behavior. Tokenizer/template overhead is likewise not quantified here.

Cache metrics in [engine logging](../assistant/ai_engine/local.py#L369), safe service metadata,
Step 45 tracing and [diagnostic accounting](../scripts/compare_offline_models.py#L78) retain
backend-reported counters. Absent is unknown, not zero; malformed/impossible counters cannot
justify an uncached claim. Derived total-minus-cached values depend on the installed backend
meaning of total tokens. Tests check absent, valid zero, positive, negative, boolean and
impossible cached values. Never infer hits just from a fast repeat or repeated characters.
A single new warm request cannot establish reusable-cache behavior for representative questions;
that would require a separately approved, matched validation plan rather than automatic retries.

## Ranked candidates and implementation decision

Value/risk are conditional judgments, not measured gains. All magnitude estimates are **unknown**
unless explicitly identified as already measured. No row claims a new Pi speed improvement.

| Priority | Candidate / exact area | Expected value and evidence | Risk / decision |
| --- | --- | --- | --- |
| 1 | Record exact installed backend/template/context and real service controls/counters; `LocalLLMEngine.generate_stream`, existing diagnostics | Highest diagnostic value: resident sample's 6,015 ms prefill needs backend/workload attribution | Low with read-only metadata/public fixtures. Use existing guards; no inference now. Does not itself speed prefill. |
| 2 | Retain existing byte-stable voice safety/style construction; `_generation_kwargs`, `_build_generation_instruction`, `_build_messages` | Proven elimination of generic safety duplication and irrelevant ordinary history is already present | Low; strengthened by new regression tests. No further token saving measured. |
| 3 | Runtime-supported prefix reuse investigation on unchanged Qwen | Potentially material if this build can reuse relevant prompt state; zero-hit symptom is measured | Backend changes medium/high risk; support/semantics unknown. Do not implement guessed flags, upgrades or architecture workarounds. |
| 4 | Remove only a future demonstrated duplicate originating from a trusted caller | Could reduce actual evaluated prompt tokens if identical obligations are truly redundant | No such default production duplicate found. Require exact before/after prompt obligation tests and quality review; no blanket text deduplication. |
| 5 | Reuse already-selected RAG context or profile remaining synchronous preparation; `select_rag_context`, resource snapshot | Repeated selection proven, prompt bytes unchanged; benefit confined to preparation and currently unmeasured | Low/medium refactor or freshness risk; defer unless timing shows material cost. Never weaken context bounds, ranking, expiry or safety sampling. |
| 6 | Existing optional compact style, smaller context or history limits | Could reduce some prompt text/workloads | Not semantically equivalent: may remove word-count/needed-fact guidance, detail or genuine conversation evidence. Keep default false/context4096 and existing limits. Document rather than enable. |
| Reject | Delete RAG/security/uncertainty constraints, strip legitimate corrections/follow-ups, pad prefixes, force model resets or repeated warm loops | No valid quality-preserving improvement established | Conflicts with task requirements; not implemented. |

No lower output ceiling, model switch, extra model request, speculative caching flag, synchronous
preparation removal or prompt rewriting is shipped. Any future actual optimization must
compare identical factual/safety requirements, grounded facts/units/corrections, completed
responses, controls and cache conditions, then show real Pi improvement. Assertions of payload
invariants are useful evidence; they are not an answer-quality benchmark pass for a changed model.

## Future single guarded Pi validation — do not execute now

Purpose: collect one correctly labelled **service-based text** observation, not prove a
speedup or cache reuse. No optimization is deployed in this milestone, so there is no new
candidate whose measured improvement can be claimed.

After cooling hardware is ready and a separate operator approval:

1. Confirm healthy current power/throttle flags, adequate available RAM and temperature
   below 50 C (stricter operator gate than this existing harness's below-55 C start).
   Keep all other inference idle. Confirm the existing Qwen alone is already resident,
   exact full digest matches the installed pinned artifact, context is 4096 and options
   match the intended diagnostic. If absent/changed/hot, **defer**; do not prewarm, switch,
   evict, restart, retry or extend timeouts to manufacture a warm result.
2. Save only safe read-only version/digest/context/expiry/template-parameter hashes and
   allowlisted effective controls. Do not dump environment, private history or raw model
   template/system contents. Use public fixture content exclusively.
3. Run exactly one existing guarded `--run` invocation below, with no automatic repeat.
   It uses the existing diagnostic lock, isolated application state, bounded local metadata
   requests, sampled pressure checks, 70 C abort, RAM/current-throttle rejection and owned
   process termination. The existing parent wall deadline is 45 seconds. No new monitor,
   relaxed timeout or continuous warm loop is introduced.
4. Read actual resident-before/after, digest/context/options, backend load/prefill/decode,
   prompt/cache counters, first alphanumeric text and full answer; inspect abort reason and
   thermal/pressure telemetry. Accept a warm interpretation only with matching resident
   controls and negligible recorded load. A runner can change after manual precheck; do
   not hide reload/abort or subtract load to invent a warm result. Review factual correctness,
   completion/truncation manually. No whole-voice/acoustic claim follows from this text test.
5. Retain exact public fixture/message hashes and controls when comparing with the supplied
   summary. Different system instructions, modes, prompt tokens, output length, cache state
   or runtime mean the results are not a matched optimization comparison. One run cannot
   determine an effective prefix cache across distinct questions. Do not retry a failed/hot
   trial; report the limitation and wait for separate approval.

Single future command from the reviewed Pi checkout/environment:

```sh
./venv/bin/python scripts/compare_offline_models.py --run \
  --model qwen3.5:4b-q4_K_M --expected-digest d8b0f5e9760c --fixture fact \
  --output "$HOME/qwen-prefill-step50-$(date +%Y%m%d-%H%M%S)"
```

This command is **not** ordinary production speech: it uses the diagnostic 25-word style
suffix, token/sampling controls, public fixture and request-local 60s retention documented
above. That retention is not a daemon stop or forced unload, but can affect later natural
expiry; obtain approval for the diagnostic controls. If these controls are unsuitable, defer
rather than silently alter a guard or equate it to the original unknown direct payload.
`--run` itself permits cold inference: the pre-existing-residency precheck is therefore
mandatory for the proposed warm observation; load/residency evidence must still be verified
in the result. No `--cold-warm`, `--warm-distinct`, extended A/B or additional request is
part of this procedure. The one request cannot establish repeatable/cache-independent speed.

## Windows regression evidence and full changed-file inventory

The new tests use actual service preparation and LocalLLMEngine payload construction, but
replace HTTP/model output with invented public responses. Supplied-like scalar counters in
mock events are deliberate test fixtures, not recorded Pi telemetry. No native STT/Piper,
microphone or speaker test was performed. Existing voice streaming/Piper regression tests
use their established fake providers/processes.

Sixteen new tests cover:

- Exact normal voice SYSTEM composition, one identity/safety instruction, no generic prompt
  duplicate, stable same-mode prefix and differing final USER question.
- Brief/normal/detailed format/safety requirements and unchanged model/context/caps/options.
- Relevant recent-turn follow-up roles, corrected-memory chronological USER placement,
  actual local RAG facts/units/negative constraints, saved-online reference/secret protection,
  and absent evidence without fabricated source blocks.
- No LLM calls for unsupported actions or protective refusal; one request per generated
  answer and no implicit warm/cache request.
- Missing/valid-zero/positive/invalid cache counters; stable message text without rendered
  prompt claims; warm safety prefix versus full policy distinction.
- Idempotent bounded RAG selection without duplicated prompt content; caller-specific
  instructions preserved rather than stripped; reduced-resource cap remains binding.

Publication allowlist (four files):

| File | Change |
| --- | --- |
| `assistant/tests_qwen_prompt_processing.py` | New deterministic service/payload/context/safety/cache regressions. |
| `docs/qwen-prompt-processing-step50.md` | Evidence, source trace, diagnosis limits, ranked candidates, future guarded validation and this inventory. |
| `docs/qwen-prompt-focused-tests.log` | Focused Windows regression output, invented fixtures only. |
| `docs/qwen-prompt-windows-tests.log` | Full Windows regression output; not performance evidence. |

Focused command:

```text
python manage.py test assistant.tests_qwen_prompt_processing assistant.tests_single_model assistant.tests_rag_v2 assistant.tests_streaming assistant.tests_voice_stream assistant.tests_piper_support conversations.tests_memory knowledge_base.tests_retrieval_projection --noinput
```

Result: **243 tests, OK**, 6.065 s.
Full command: `python manage.py test --noinput` — **1,196 tests, OK, one platform skip**,
116.223 s. Tests used a workspace-local TEMP/TMP under the Windows sandbox.
Documentation links/function anchors, syntax, whitespace, publication allowlist and zero
diffs in existing production/config/bank/deployment files were checked. All pre-existing
unrelated untracked files are excluded from publication.

There is no activation/rollback for a new runtime optimization because none was shipped.
Reverting this test/documentation commit affects no production behavior. Existing optional
compact-style/CPU controls remain opt-in and unchanged; no new recommendation to enable them
is implied. Next decisions require actual matched Pi prefill and quality measurements;
Windows timing and assertions cannot establish a Qwen latency improvement or its cache cause.
