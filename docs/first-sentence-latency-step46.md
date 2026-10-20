# Step 46: first-sentence release and Qwen residency investigation

Branch: `codex/offline-model-selection`; starting commit:
`52641c94faf557f6c59d8af6d7f5dd5d2639c162`.

## Outcome and scope

**Use the existing opt-in option; no production implementation change is justified.**
`VOICE_STREAM_FIRST_SENTENCE_MIN_CHARS=30` already releases eligible complete short first
sentences before stream completion while retaining the existing phrase/word thresholds.
The new deterministic tests demonstrate that behavior, text preservation, cross-thread Piper
start, failure/cancellation handling and unchanged service grounding/memory. Source defaults,
settings files, model selection, token ceilings, trace schema, capture, Whisper/Piper/ALSA,
firmware, routing, resource thresholds and systemd are untouched.

This work adds tests and documentation only. No Pi inference, audio-device testing, model
download, hardware change, Ollama restart/stop, recurring warm request, deployment or main
merge occurred. The supplied physical trace is operator evidence, not a new measurement here.

## Supplied trace: what the 2.2-second gap means

| Boundary | Time from cycle origin |
| --- | ---: |
| Capture completed | 3.881 s |
| Whisper completed | 7.577 s |
| Ollama request ready | 7.859 s |
| First alphanumeric text | 58.726 s |
| First complete-sentence candidate | 59.384 s |
| Ollama completed | 61.559 s |
| First speech unit released | 61.581 s |
| First Piper synthesis completed | 62.080 s |
| First audio ready | 62.214 s |
| First speech-file PCM accepted | 62.220 s |
| Total cycle | 65.112 s |

Differences below subtract boundaries from this same supplied cycle, not unrelated trials:

- Sentence candidate to first unit: **2.197 s**.
- Candidate to Ollama completion: **2.175 s**; completion to first unit: **22 ms**.
- First text to sentence candidate: **658 ms**: an earlier sentence policy cannot eliminate
  the time needed for that candidate to arrive.
- Request ready to first text: **50.867 s**, overwhelmingly larger than the buffering gap.
- Released unit to first Piper completion: **499 ms**, including any queue/scheduling and
  synthesis work; synthesis-start timestamp was not supplied, so this is not pure Piper time.
- Piper completion to prepared audio: **134 ms**; prepared audio to PCM acceptance: **6 ms**.

Backend observations are kept separate: **46,069 ms load**, **4,789 ms prompt evaluation**,
**2,837 ms decode**, 108 prompt tokens, zero reported cached prompt tokens, 10 output tokens.
Do not add overlapping model, synthesis and playback spans to obtain a new end-to-end figure.
Qwen was visible in `ollama ps` afterwards; that does not establish pre-request residency.
A final temperature of 47.2 C without continuous telemetry does not certify peak temperature,
current flags throughout the trial, safe sustained operation, or absence of thermal pressure.

### Source-supported explanation

1. [StreamingSpeechBuffer initialization](../scripts/voice_loop.py#L779) uses the first-phrase
   minimum (60 by default) as the complete-first-sentence minimum when the optional setting
   is absent. First target is 100; later target is 180.
2. [feed](../scripts/voice_loop.py#L883) recognizes safe punctuation with look-ahead, abbreviation,
   numeric/link and group protection. Step 45 marks `first_complete_sentence` **when a nonempty
   sentence-boundary candidate is recognized**, before applying the length-based release rule.
   It does not mean that a speech unit has been released or grammar/facts human-verified.
3. A candidate below the effective sentence minimum is held. Existing clause/word fallbacks
   still have their own 60/100 constraints; lowering the sentence minimum does not lower those.
4. [speak_stream_response](../scripts/voice_loop.py#L1059) marks `first_speech_unit` before the
   first enqueue. A validated service `done` triggers `present('', final=True)`, flushing a
   remaining short answer. This is why baseline short sentences can wait until completion.
   It is a size/stream-completion rule, **not a fixed 2.2-second timer**. The preparer starts
   Piper as soon as it receives a unit; it does not require the text/audio queues to fill.
5. The first-unit marker precedes queue insertion; the supplied pre-release gap cannot be
   explained as Piper queue waiting or ALSA leading silence. Deliberate look-ahead/group
   safety was already satisfied when the sentence-candidate marker appeared.

The trace is consistent with a short candidate held below the effective minimum and flushed
at `done`. The final 22 ms can include service persistence, completion handling, display
callbacks, scanning and scheduling; this trace excerpt cannot attribute each component.

**Limit of the device diagnosis:** neither the actual spoken sentence length nor the effective
first-sentence setting was supplied. Ten generated tokens are not a character count. The
source proves the holding mechanism and deterministic tests reproduce the observed ordering,
but cannot prove that this particular answer is 30–59 characters or that the Pi used an empty
option. An unusually slow processing/scheduling interval is not excluded by the excerpt.
Verify the effective allowlisted setting and sanitized character count locally using a public
fixture rather than exporting a private answer. No new content collection is introduced.

## Baseline versus opt-in behavior

| Condition | Baseline: unset/empty (60-character minimum) | Existing opt-in: 30 |
| --- | --- | --- |
| Complete 30–59-character first sentence plus safe look-ahead | Usually held until more text or final flush | Released at that safe candidate |
| Complete sentence shorter than 30 | Held until sufficient additional text or final flush | Same hold; no guarantee every tiny answer starts earlier |
| Sentence punctuation at delta end without look-ahead | Held pending closure/next delta or finalization | Same safety wait |
| Abbreviation, decimal/date, URL/link or unclosed quote/group | Existing protected boundaries | Same protection |
| Short incomplete/dangling clause | Does not qualify as an early complete sentence | Sentence-only setting does not lower clause/word minima |
| Long response / later units | Existing clause/word/180-character packing | Same later-unit rules; first short sentence can be separate |
| Empty, interrupted, mismatched or failed stream | Existing error/cleanup contract | Same error/cleanup; already-played audio cannot be recalled |
| Final unpunctuated/truncated tail | Existing flush/played-complete-sentence omission policy | Same policy/code; no added completion, retry or deleted canonical text |

A safe boundary is an existing conservative syntactic heuristic, not a general grammar or
truth oracle. This option must not be advertised as guaranteeing correctness of arbitrary
model text. It introduces no earlier incomplete-clause shortcut. Existing final unpunctuated
handling and omission of obviously dangling tails after complete-sentence playback remain;
therefore do not claim every truncated response is fully spoken or complete. The tests verify
exact canonical-to-spoken reconstruction for complete responses (after existing Markdown
sanitization) and separately verify the intentional incomplete-tail policy. No duplicate or
skipped complete-response words are introduced.

### Could the recorded gap realistically shrink?

**Yes, conditionally.** For a public answer matching the 30–59-character eligible case, the
existing option can start Piper when the candidate appears rather than waiting for the
remaining stream. The controlled replay uses a substitute public RAM sentence at the supplied
59.384/61.559/61.581 s logical boundaries and moves its release to the candidate boundary.
Those are deliberately simulated times, not a new Pi result and not the private original text.

For the actual recorded answer, eligibility is unverified. A sentence shorter than 30, absent
look-ahead, or already-qualified sentence delayed for another reason might gain little.
Earlier synthesis can overlap the remaining model stream, but CPU competition can change its
runtime. Do not promise a 2.197 s reduction in first PCM or audible onset without measurement.

The 2.175 s after the sentence candidate coincides with the remainder of an unfinished model
stream; backend decode was 2.837 s. Continued generation, stop handling or final event delivery
may account for that interval; the excerpt does not isolate those components or extra tokens.
Earlier speech can hide part of that remaining work, not accelerate decoding. It cannot remove
46 s loading, nearly 4.8 s prefill, pre-candidate decoding, Whisper, conversion, device startup
or required safety delays. PCM acceptance remains a pipe-write event, **not acoustic onset**.

## Activation and rollback (later operator approval)

Existing [VoiceLoopConfig.from_settings](../scripts/voice_loop.py#L159) reads the option once
at process startup; validation accepts 20 through the first-chunk minimum (60 by default).
The chosen demonstration value is 30. No new flag, config file or implementation is added.
A one-process environment prefix takes precedence over dotenv's non-overriding load; the
sample files and long-running production process are not edited.

For later use only, from the reviewed Pi checkout with its existing environment:

```sh
VOICE_STREAM_FIRST_SENTENCE_MIN_CHARS=30 ./venv/bin/python scripts/voice_loop.py --latency-trace-dir "$HOME/voice-step46"
```

This is a normal interactive loop, **not an automatic thermal guard**. Do not run it now or
leave it unattended. Speak one public fixture after the cue, then q to exit; no test loop,
retry, rewarming or simultaneous model trial. Review first-word clarity, prosody, number/name
pronunciation, any extra gaps and complete factual/grounded answer quality. More/smaller units
can worsen prosody or contention, so a Windows demonstration is not approval to deploy it.

Rollback: exit that manual session and launch without the prefix. If another inherited
configuration already sets this option, explicitly use
`VOICE_STREAM_FIRST_SENTENCE_MIN_CHARS=''` for the session to restore legacy packing.
Rollback does not mean merely editing an environment after the process has cached its config.
Do not edit `.env`, reload services, change token caps, alter playback silence, or switch models.

**Short thermally guarded recommendation for later approval:** after safe cooling assembly,
start below 50 C with healthy current power/throttle flags, adequate RAM and no competing model
work. Use the existing operator-approved continuous thermal/resource supervision; abort at
70 C or sooner on pressure and retain the partial trace. If that supervision is unavailable,
defer the trial. The tracing flag itself provides no cutoff. One public opt-in cycle is
sufficient for initial boundary/listening review; any baseline comparison is a separate cooled,
operator-approved trial. A final temperature alone cannot validate safety.

## Qwen keep-alive/startup audit (code only)

| Source | Finding | Meaning / limit |
| --- | --- | --- |
| `config/settings.py` | Qwen 4B default, keep-alive 24h, context 4096 | Defaults, not inspected Pi effective values. `.env`/launch environment can differ. |
| `assistant/ai_engine/__init__.py::get_engine` | Explicitly passes Django keep-alive/model/context into cached engine | No omitted keep-alive in this production construction. Per-process singleton may retain old config; no live mutation assumed. |
| [LocalLLMEngine constructor](../assistant/ai_engine/local.py#L67), [chat payload](../assistant/ai_engine/local.py#L419) | Chat sends the configured keep-alive every request | No normal zero-retention/unload request found. 24h is requested retention, not guaranteed residency. |
| [warm_model](../assistant/ai_engine/local.py#L525), [warm_chat_prefix](../assistant/ai_engine/local.py#L562) | Explicit model warm and prefix chat use the same engine keep-alive/context | Mock payload tests confirm consistency, not backend retention or useful new-question cache reuse. |
| [warm command](../assistant/management/commands/warm_ai_model.py#L71) | Waits for API, warms one configured model then safety prefix, bounded budget; retries only a specific runner-start error | Existing startup mechanism, not run here. Cannot assume success without status/log evidence. |
| [warm unit](../deployment/systemd/smart-ai-companion-model-warm.service) | Type=oneshot, default --timeout 90, Restart=on-failure, no periodic timer specified | Unit file presence does not prove installed/enabled/successful. No RemainAfterExit: inactive after success need not mean failure. No recurring warm request added. |
| [Django unit](../deployment/systemd/smart-ai-companion.service) | Wants/After warm service | Ordering/weak dependency, not proof warm succeeded or model is still resident. Manual voice-loop invocation has no such unit dependency. |
| `scripts/voice_loop.py::main` | Warms Piper before input, not Ollama | A manual voice session can legitimately make a cold first LLM request. No self-healing warm loop is added. |
| `scripts/compare_offline_models.py::child` | Sets request-local 60s keep-alive and allows Qwen selection | A diagnostic using the same Qwen runner requests shorter idle retention. Whether/when it replaces existing expiry must be verified on the installed backend. |
| `scripts/evaluate_offline_quality.py::child`, `scripts/validate_offline_voice.py::configure` | Quality child reuses the diagnostic configure helper, then selects requested model; keep-alive 60s. Voice diagnostic itself selects Llama 1B | Quality evaluation can select Qwen with 60s retention; do not confuse this with a production default change. |
| `scripts/test_short_answer_thermal.py`, `scripts/compare_safe_warm_response.py` | Llama 3B diagnostic uses 60s request-local retention | Different model diagnostic; not proof of Qwen expiry. No explicit eviction by these scripts. |

No demonstrated production keep-alive omission or automatic unload defect was found. The
source exposes plausible lifecycle/config differences, not a proven cause of the 46,069 ms
load. Other possibilities requiring evidence include never completing initial warming, earlier
operator-approved unload, daemon/runner restart, memory pressure, competing clients/models,
changed context/options forcing a runner reload, and effective launch settings differing from
expected values. Neither operating-system pressure nor a backend reload is proven by this trace.
The subsequent resident Qwen observation is consistent with successful loading during the
request and does not establish earlier residency or its later duration. Zero prompt-cache hits
are separate from model residence; startup prefix warming is not useful-cache proof.

### Safe future verification, without changing lifecycle

After approval, inspect read-only **before** the chosen single trial: precise tag/digest,
context/allocation/expiry and timestamp from existing local residency API/ollama ps, effective
allowlisted model/keep-alive/context options, warm-unit result/exit status and boot-relative
startup history. Check existing Ollama runner logs for load/reload/pressure/exit evidence and
correlate prior diagnostic retention requests and any known manual unloads. Inspect only safe
status fields; do not export raw environment dumps, private prompts or transcript-bearing logs.

Observe again after completion without submitting another generation request. Record backend
load/cache metrics separately and retain continuous thermal/current-flag/RAM observations.
Use expiry/timestamps and the installed backend version to evaluate the 60s diagnostic-retention
hypothesis; do not infer it merely because a model was absent. Do not stop/restart Ollama,
force multiple models resident, add periodic warm-ups, edit units/env or change Qwen defaults.

## Windows validation and complete changed-file inventory

All new streams/texts are invented public fixtures. Timing replay is an artificial monotonic
clock; concurrency tests use Events to establish ordering, not wall-time speed assertions.
Fake TTS/player callbacks execute no actual speech synthesis or playback. The option leaves
source safety guards, retrieval, memory, truncation and token budgets byte-for-byte unchanged.

Four files only:

| File | Purpose |
| --- | --- |
| `assistant/tests_first_sentence_release.py` | Fifteen new deterministic tests: baseline/opt-in replay, tiny answers, closure/look-ahead, dangling partial clauses, names/technical abbreviations, numeric/date/URL fragmentation, long-stream reconstruction, settings rollback, safety text, real producer/preparer ordering, inherited error/cancel/truncation contracts under opt-in, actual service RAG/memory/persistence equality, fake chat/warm retention payload consistency, and short-answer audio cancellation after early release on model error or interrupt. |
| `docs/first-sentence-latency-step46.md` | This diagnosis, residency audit, activation/rollback and later validation recommendation. |
| `docs/first-sentence-focused-tests.log` | Focused Windows regression evidence. |
| `docs/first-sentence-windows-tests.log` | Full Windows regression evidence. |

- Focused: `python manage.py test assistant.tests_first_sentence_release assistant.tests_inference_latency assistant.tests_voice_stream assistant.tests_latency_trace assistant.tests_single_model conversations.tests_memory --noinput` — **200 tests, OK**, 4.797 s.
- Full: `python manage.py test --noinput` — **1,180 tests, OK, one platform skip**, 35.526 s.
- Syntax, relative source/doc links/line anchors, diff whitespace and four-file publication
  allowlist checked. Production source/config/units/bank/audit/instrumentation remain unchanged.
  Existing unrelated untracked files are excluded. Only the requested feature branch is pushed.

These are Windows regression results, not Raspberry Pi timing or physical audio-quality
results. The milestone demonstrates a pre-existing option; it does not claim an observed
Pi latency improvement or that the original unknown short answer qualifies for the threshold.
