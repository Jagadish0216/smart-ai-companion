# Llama 3.2 3B: guarded offline evaluation extension

This feature extends `10e1060260d86af60df20ce64b68cd3af38bcb40`. It permits
`llama3.2:3b` in the existing inventory/single-trial harness and quality evaluator,
and adds explicit report pairs for 1B/Qwen, 3B/Qwen and 3B/1B. No inference is
automatically run, and **Qwen4B remains the production default/reference**.

## Identity and fair settings

| Role | Exact installed tag | Required digest prefix |
|---|---|---|
| Candidate | `llama3.2:1b` | `baf6a787fdff` |
| Candidate | `llama3.2:3b` | `a80c4f17acd5` |
| Reference | `qwen3.5:4b-q4_K_M` | `d8b0f5e9760c` |

These are user-confirmed prefixes, not full digests independently read from the
Pi in this workspace. The runner retrieves the complete local 64-hex digest and
checks the prefix before starting and immediately before inference, rejecting
changed or non-local artifacts. It records actual GGUF format, parameter and
quantization metadata, artifact bytes and Ollama-reported resident `size` and
`size_vram`, along with system available RAM. Do not infer a quantization from
the `3b` tag, or call Ollama allocation an independently measured RSS/PSS value.
Actual quantization/size and performance still require the Pi report.

All benchmark artifacts are byte-for-byte preserved: the 100-question bank,
expected answers, weights, rubrics, thresholds and 25-question reviewer subset.
No new easier question or 3B-specific prompt has been introduced. CS01 remains:

> How do CPU clock cycles per second differ from instructions executed per second?

The unchanged intermediate ceiling is **160 tokens**. Seed 42, temperature 0,
context 4096, thinking off, omitted CPU overrides, system/style instructions,
resource policy and existing request-local 60 s idle expiry are unchanged. The
quality-only normal/RAG ceilings remain 192; these do not override the requested
CS01 ceiling or reduced/protective effective caps. Service/socket timeout stays
40 s and the sampled whole-workload monitor stays 60 s. No timeout or keep-alive
configuration was changed to accommodate 3B. Production `.env`, default model,
services, audio, firmware and GPIO remain untouched.

Each CS01 invocation selects one model and generates one answer. The established
multi-turn interface remains available for later selected scenarios; it is not
enabled by this command and never batches the bank. Existing 1B/Qwen tuple
positions and CLI behavior are preserved. Additional smaller Qwen tags in the
legacy inventory allowlist remain unchanged; no download is attempted.

## What the supplied 1B evidence does and does not show

The earlier warm-distinct sample had first text 312.767 ms, full text 4.395 s,
roughly 9 tokens/s and 120/136 cached prompt tokens, but incorrectly equated
clock frequency with instruction execution rate. It is a cached, incorrect text
sample, not proof of fast correct conversation.

The newer user-supplied CS01 sample reported 3.87 s loading, 3.84 s prefill,
19.02 s decoding, 160 generated tokens, truncation, maximum observed 65°C and
an aborted overall status. The excerpt again equated one cycle with one
instruction. These observations have not been independently reproduced here.

**Established from the supplied counts:** the generated count reached the
unchanged 160-token ceiling, which the accepted harness conservatively marks
truncated. The visible factual error is independently a quality concern; extra
tokens would not make that already incorrect statement correct.

**Source-supported explanation, requiring the raw report to confirm:** an
incomplete/truncated child exits nonzero; the parent can therefore label the
overall trial aborted even when temperature is below 70°C. An aborted status
alone is not proof of a thermal cutoff. Current pressure flags, abort reason,
effective cap, `done_reason`, raw output and timestamps must be inspected.

**Unproven hypotheses:** unnecessary verbosity, repetition or inadequate
planning may have consumed the allowance, but the supplied excerpt/counts cannot
separate those causes. Do not claim verbosity as established, diagnose a model
reload from load time alone, or combine these conditions with the earlier cached
warm sample as a matched run. Retain partial evidence and the correctness
concern, but exclude the aborted/truncated trial from successful complete-answer
coverage and latency comparisons. Do not silently raise the limit or rewrite
CS01. If meaningful complete answers cannot fit the unchanged safe protocol,
record that constraint and defer further experiments.

## Single operator-controlled Pi procedure

Do not execute the full bank or a three-model loop. The Pi has no active cooler.
Use the reviewed feature checkout/venv, arrange a quiet request window and:

1. Optionally inspect inventory read-only with the accepted harness:
   `python scripts/compare_offline_models.py --inventory --output /tmp/3b-inventory-01`.
   If a different model is resident, stop. Allow natural configured expiry; never
   force unload, restart Ollama or change idle/service settings. If the other
   model never expires, defer. The diagnostic lock does not stop production
   application requests.
2. Require a start **below 55°C**, no current power/throttle pressure and sufficient
   RAM. Cold loading requires available RAM ≥1.5 × the actual installed artifact
   bytes +512 MiB; during execution at least 512 MiB must remain. A resident 3B
   model must match the verified digest and context 4096. Unknown mandatory
   telemetry or artifact size refuses the relevant trial. Historical throttling
   alone is not current pressure. Loading 3B cold adds real initialization cost
   and thermal load; do not subtract that cost and call it measured warm speed.
3. Choose a new output directory and run **one** CS01 trial:

   ```bash
   python scripts/evaluate_offline_quality.py --question CS01 --model llama3.2:3b --output /tmp/quality-llama3b-CS01-01
   ```

4. Stop the session after this trial. The monitor aborts at 70°C, current
   undervoltage, frequency capping, throttling, soft temperature pressure, low RAM
   or deadline. Checks are sampled and local metadata calls add bounded latency;
   this is not a hard real-time thermal guarantee. Never disable Pi protections
   or retry repeatedly to obtain completion. Cleanup targets only the owned
   client process group, not Ollama or any other service; server cancellation
   may lag client termination. Raw/partial and aborted reports are retained.
5. Review `report.json`, full output and provided public messages. Generate an
   independent worksheet without inference:
   `python scripts/report_offline_quality.py --worksheet /tmp/quality-llama3b-CS01-01/report.json --output /tmp/quality-llama3b-CS01-review.json`.
   Complete the manual rubric/flags/rationales. Do not treat the model's own
   assessment, a keyword or a successful HTTP request as a correctness verdict.

For the other CS01 trials on **separate cool occasions**, keep every control
unchanged and use only `--model llama3.2:1b` with a new 1B output directory or
`--model qwen3.5:4b-q4_K_M` with a new reference output directory. Preserve all
attempts. No command here downloads, switches production or loads two models
simultaneously. Inference is guarded to local Ollama on Linux Raspberry Pi 5.

## Acceptance and later workload

A complete CS01 response must distinguish cycles/s from completed instructions/s,
relate them through workload/architecture-dependent average IPC, and explicitly
avoid a universal one-cycle/one-instruction claim. Valid variations and the
35/20/15/15/10/5 weighted rubric remain those in the unchanged bank. An answer
that repeats the 1B error fails factual/technical review even if it is faster.
Truncation or an aborted/invalid workload is not a successful complete answer.

Inspect first meaningful text, total service-answer time, load, prefill, decode,
token counts/throughput, actual cached tokens, exact identity/quantization,
resident allocation, sampled temperature/frequency/RAM and current flags
separately. A warm label does not imply cache reuse, and first text does not
measure complete or audible output. Diagnostic report writes/probes add overhead.

Named pairs use the existing rules: unique predeclared trials, same question and
bank, system/user/style/setup hashes, source commit/runner hash, generation and
effective policy/context controls, Ollama version, cold/warm class, known zero
prompt reuse and starting temperatures within 3°C. Unknown or unequal conditions
yield explicit reasons and no matched ratio. Real model tokenization/templates,
memory footprint and generated lengths differ and remain visible. A missing 1B
trial does not block a valid 3B/Qwen pair; a valid old 1B/Qwen pair still works.

After a safe, complete and correct initial trial, choose individual later IDs on
separate cool sessions: CS02/CS08 (technical), RE03/RE04/RE08 (reasoning), RM01/RM02
(RAG present/missing), RM05/RM06/RM08 (memory present/missing/corrected), HU01/HU04
(uncertainty/evidence), and reviewer AI03/AI05/AI08. This is a manual review plan,
not a batch script. Retain actual preceding turns for explicitly selected
multi-turn scenarios. Do not lower retrieval thresholds or change rubrics if an
upstream source is missed; inspect exactly what evidence reached the model.

Production selection remains pending until representative independent quality
coverage meets ≥85 overall, ≥80 in every major category, zero critical safety
failures, reliable RAG/memory/context, and a material matched latency advantage.
One good CS01 answer cannot establish those gates. Neither greater capacity nor
a smaller parameter count proves a measured quality or speed advantage. **No
switch to Llama 3B is recommended without valid Pi evidence.**

## Windows validation

Full suite: **1,107 run, 1,106 passed, one Linux-only pipe test skipped**,
106.381 s. Broad focused model/quality/voice/policy set: **396 run, 395 passed,
one skipped**, 14.565 s. Initial model/quality-specific set: **67 passed**.
All **11 added tests passed**, including a real service/policy request with fake
3B inference, identity/quantization metadata, residency/context refusal, RAM and
thermal guards, old two-model compatibility, independent 3B/Qwen pairing and
explicit three-way pairs. Unequal controls/cache/load or truncation yield no
matched ratios. The Windows CLI accepted the 3B tag and refused inference before
a model process started. Mock timings/answers/sizes do not measure the Pi model.

All 558 protected/unrelated files matched their original hashes and inventory:
the original 555 plus all three existing benchmark artifacts. The question bank,
reviewer subset and initial pending template remain byte-for-byte unchanged.
Source hashes, exact file inventory and scalar results are recorded in
`llama3b-windows-results.json`; the complete regression output is in
`llama3b-windows-tests.log` (trailing console whitespace normalized).

No Pi inference, actual 3B digest/quantization/size verification, model download,
service change, main merge or deployment occurred during this implementation.
Full Pi identity checks, all actual 3B answers/scores and matched latency/thermal
evidence remain operator validation work.
