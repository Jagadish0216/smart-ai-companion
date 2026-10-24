# Safe short cold/warm comparison

Run once on the uncooled Raspberry Pi 5, from this checkout with its Python environment active:

```sh
python scripts/compare_safe_warm_response.py --output "benchmarks/safe-warm-$(date +%Y%m%d-%H%M%S)"
```

No new Pi trial was run for this repair. Windows tests use mocked inference only.
The operator-reported failed trial is analyzed in [cold-warm-failure-analysis.md](cold-warm-failure-analysis.md).
The operator must let any resident model expire naturally and cool the Pi before starting.
The diagnostic lock excludes other accepted diagnostic sessions. No downloads, retries,
eviction, restart, production setting changes or benchmark-bank changes occur.

The parent starts below 50 C and preserves the artifact-size cold-load RAM margin.
Each worker also starts below 50 C (including the warm worker, a conservative additional
restriction). The parent refuses the warm phase at 55 C or above without waiting or retrying.
A worker thermal refusal is preserved as `warm_deferred_thermal`. Both requests use the
existing 30-second monitored bound; the session has a 60-second budget, including setup.
The cold HTTP socket timeout is 25 seconds; warm remains 20 seconds. These are socket
inactivity limits, not full-request deadlines. The parent monitor enforces elapsed limits
even if socket activity continues. Neither timeout is automatically extended or retried.
Existing monitoring samples every 0.5 seconds and terminates the process group on
70 C, current power/throttle flags, insufficient RAM, unknown telemetry, identity/context
changes or deadlines. Sampling is observational and cannot guarantee instantaneous cutoff.
Metadata calls retain their existing network timeouts; finalization and group reaping can
extend wall time beyond the inference budget. Keep-alive is request-local, 60 seconds.
The cold worker requires no resident model; the warm worker requires the same full digest
and context 4096. The worker rechecks both immediately before sending. Only the two fixed
questions are allowed; both use seed 42, temperature 0, context 4096, 32 tokens, thinking off.
An incomplete, truncated, failed, aborted or factual-screen-failing cold answer prevents
request two. Factual screens are simple heuristics, and do not replace manual review.

`report.json` is the version 1 report schema:

- `status`, `abort_reason`, `installed`, `verified_digest`, request/session limits,
  `http_timeout_seconds`, `manual_quality_review_required`, `ratios`, and `interpretation` describe the session.
- `phases` contains separate cold/warm records: `resident_before`, `resident_after`,
  initial/maximum observed temperature, full `telemetry`, `status`, `abort_reason`,
  `worker_returncode` when observed after monitor completion, `load`, and `sample`.
  Optional `final_pressure_reason` or `final_telemetry_error` retain secondary observations.
- Installed metadata retains exact quantization and artifact bytes; resident metadata
  retains backend size/size_vram allocation and context. Telemetry retains available RAM,
  temperature, clock and current power/throttle observations.
- Failed samples additionally retain `abort_reason`, `error_type`, `error_detail`, and
  `timeout_scope` for socket timeouts. Both sample and report record the timeout policy.
- Each sample retains the exact request, raw answer, completion reason, truncation,
  prompt/generation counts, load/prompt/decode durations, first meaningful text latency,
  complete-answer duration, throughput, prompt cache counters, hashes and factual review.
- Question hashes use UTF-8 SHA-256; message hashes use sorted-key JSON UTF-8 SHA-256.
  The backend-rendered prompt hash is explicitly unavailable. No explicit system prompt
  or history is sent; the model template may share cached prefixes.
- Missing cache counters remain unknown. Derived uncached counts assume the backend's
  total-token semantics and must be checked against the installed backend. Conditional
  cold/warm latency ratios require complete phases, verified cold/warm load classification
  and known cache accounting. Compare the reported token workloads before interpreting them.

Cold/warm JSON checkpoints retain partial text and backend metrics after cancellation;
the final report retains parent telemetry after abort. Output directories must be new.
A successful run still requires manual factual review. Distinct questions do not eliminate
shared template caching. The pair can show observed residency-associated response latency
under its recorded cache/workload conditions. It cannot isolate a causal cache-independent
speedup, measure voice latency, establish overall model quality, or demonstrate sustained
thermal stability on an uncooled Pi.
