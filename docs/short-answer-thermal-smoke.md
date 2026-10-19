# Single short-answer thermal smoke test

This is a direct, entirely local Ollama text test for the installed
`llama3.2:3b` (required digest prefix `a80c4f17acd5`). It does not select a
production model or exercise RAG, memory, STT, TTS, ALSA or TFT. The previous
user-reported CS01 trial started at 44.4°C and stopped at 70°C; that motivates
a smaller workload, without relaxing thermal protection.

From the reviewed feature checkout and its Python environment on the Pi, run
exactly once, using a new output directory:

```bash
python scripts/test_short_answer_thermal.py --output /tmp/short-3b-01
```

The sole user message is "What is the capital of India? Answer in one short
sentence." No expected answer is supplied to the model. Request controls are
context 4096, temperature 0, seed 42, maximum 32 generated tokens, thinking off,
streaming on and request-local 60-second idle expiry. There is one request,
no warm-up, retries, model downloads, forced unloads or service changes.
No production settings or question-bank files are edited.

## Guards and cancellation

- Linux Raspberry Pi 5 only; Windows refuses before model access/inference.
- Exact local GGUF identity from inventory; pin the complete digest and verify
  again immediately before inference and after completion. Record actual
  quantization and artifact size, rather than assuming them from the tag.
- Only the selected matching model may be resident, with context 4096. If
  another model is resident, defer or wait for its natural expiry. Never evict it.
- Starting temperature must be **strictly below 50°C** in parent and worker.
  Existing telemetry validation and RAM guards remain: cold loading needs
  1.5 times artifact bytes plus 512 MiB available; at least 512 MiB during use.
- Reuse the existing monitor, with **30 seconds** from child launch monitoring
  rather than its default 60 seconds. The HTTP socket timeout is 20 seconds;
  metadata calls have existing 3-second timeouts. No timeout is increased.
- Abort at **70°C**, current undervoltage, frequency capping, throttling, soft
  temperature pressure, unavailable required telemetry, low RAM, conflicting
  residency or deadline. Historical flags alone do not establish current pressure.
- Monitoring is sampled at roughly 0.5-second intervals plus probe time. It is
  not a hard real-time guarantee. Preflight/probes and bounded process cleanup
  add time outside the monitoring deadline. Pi hardware protections stay active.
- Only the owned client process group is terminated/reaped. Ollama may take
  additional time to cancel server work; no server restart/unload is attempted.
  A diagnostic lock prevents concurrent harness trials, but cannot prevent an
  unrelated application request. Arrange a quiet window and stop after one trial.

The parent and worker use disposable database/bootstrap contexts for telemetry;
the inference itself calls loopback Ollama directly, without the application
database or conversation pipeline. Proxies and redirects are disabled. Temporary
client state is removed; the requested output directory is retained.

## Report extraction and review

The exit code is 0 only for a completed, untruncated response containing the
expected factual phrase and no safety failure; otherwise it is 2. Inspect:

```bash
python -m json.tool /tmp/short-3b-01/report.json
```

`verified_digest` and `installed.details` record identity/quantization;
`installed.artifact_bytes` records model artifact size. `resident_before` records
Ollama allocation (not independent process RSS). `telemetry` retains temperature,
available RAM, CPU clock and current/historical flags, including aborted trials.
Top-level initial/maximum temperatures summarize these samples.

`sample` retains full or checkpointed partial `answer`, the exact request,
`first_meaningful_text_ms`, `total_response_seconds`, raw Ollama `metrics`
(durations in nanoseconds), converted `metrics_ms`, token counts and
`tokens_per_second`. `load.label` distinguishes cold from operationally warm
(matching residency/context and reported load at most 100 ms). Warm is not proof
of prompt-cache reuse; cached counts are recorded only if the backend supplies them.
Timing starts at the HTTP request, excluding identity preflight; no loading time
is subtracted to invent a warm measurement. Missing completion metrics invalidate
completion. Abrupt termination may leave duration/backend metrics unknown, but
every received text event is checkpointed atomically before the next event.

`stream_done`, `metrics.done_reason`, `complete`, `truncated` and
`expected_answer_present` describe the sample. A response hitting 32 tokens is
potentially truncated even with a correct phrase and a stop reason. A partial or
aborted sample never sets top-level `success`. The phrase check is evidence only:
manually verify that it states New Delhi correctly, contains no contradiction or
hallucination, ends naturally and follows the one-short-sentence instruction.
The status explicitly retains pending manual review.

A passing smoke test establishes only that this small factual workload completed
within observed guards on that occasion. It does not demonstrate overall model
accuracy, representative conversation latency, sustained thermal stability,
audible response time or a speedup over another model. No production model switch
is justified by this result. No Pi inference was run during Windows validation.

## Windows regression evidence

Full suite: **1,116 run, 1,115 passed, one Linux-only test skipped**, 68.158 s.
Focused suite: **62 passed**, 2.023 s, including all nine new tests. Existing
monitor callers retain their 60-second default. The Windows command refused
inference before Ollama access. Protected-file hashes (558) and benchmark files
remain unchanged. Scalar results/source hashes are in
short-answer-thermal-results.json; full output is in short-answer-thermal-tests.log.
All actual Pi quality, thermal and latency conclusions remain pending.
