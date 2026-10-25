# Bounded benchmark management and quick comparison

The previous helper used a ten-second socket timeout for `/api/ps` and both empty
`/api/generate` unload/load requests. Its unload residency loop started another
ten-second budget after the HTTP call, rather than sharing a deadline. Installed
inventory also used the engine's five-second inventory timeout. Cold CPU model
load/restore can exceed these limits. Raw timeouts reached a generic CLI message,
so the old Pi report cannot identify which operation actually failed.

More seriously, unload, sampler and residency-restore errors in `finally` could
replace a generation failure or interruption. Completed stdout samples were not
explicitly flushed. These are confirmed code defects; no new Pi timings are
claimed by the development tests.

## Independent finite budgets

| Operation | Limit |
|---|---|
| Installed inventory and ordinary `/api/ps` | 20 seconds each |
| Unload HTTP command | 60 seconds |
| Entire unload including residency polling | 90 seconds shared |
| Previous model residency restoration/load | 180 seconds |
| Generation including cold model load | Existing 120 seconds or shorter resource-policy timeout |
| Resource plan collection | 15 seconds; at most one outstanding daemon collector |
| systemd state query | 5 seconds per unit |
| Stream HTTP watchdog / management response body | Remaining request budget |
| Sampler/watchdog thread joins | 2 seconds each |
| `vcgencmd` throttling probe | 2 seconds; diagnostic records timeout/unavailable |
| Deployment service stop/start | 120 seconds per call |

HTTP connection/header socket waits remain finite; body watchdogs close the
socket at the remaining deadline. Unload polling passes only the time remaining
to each residency request. No retry storm or indefinite polling is added.
Resource collection cannot queue additional workers behind a stuck collector.
Existing CPU/memory/thermal policy, sampled 75C/384MiB stops, throttling checks and
the normal user's nonblocking microphone ownership lock stay in force. Failed
systemd units are quiescent, but missing/active/transitional units are rejected.

## Diagnostics and retained results

Progress JSON identifies `operation`, `model`, `case`, `phase`, repeat and mode.
Failure JSON adds elapsed milliseconds and a safe category such as `timeout`,
`resource_protection`, `connection_failure`, `validation_failure` or `interrupted`.
Raw exception bodies, host URLs, credentials and private prompts are not copied.
The fixed public benchmark prompts and answers remain in sample rows.

Cleanup diagnostics use `cleanup_error`. A cleanup failure never replaces an
existing primary failure. If cleanup alone fails, the command exits nonzero and
does not recommend a model. Failed unload prevents loading the previous model
on top of uncertain residency. Interruptions skip optional residency reload.

```bash
venv/bin/python -u manage.py benchmark_models --quick --output /tmp/quick-new.jsonl
```

Both services must be quiescent. Every stdout event is flushed. `--output` creates
a new report (never overwrites), flushes and fsyncs every event, so completed
samples survive later failures. An incomplete summary has no recommended model.
The supervisor runs git/tests/benchmark as the existing user and uses root only
for service supervision, avoiding expired sudo credentials during restoration.
Its EXIT/INT/TERM handling restores services after test failure, Ollama timeout,
interruption or unexpected command failure. SIGKILL/power loss cannot run shell
traps; check/start the services manually in those cases. Failed restoration is
reported explicitly rather than treated as success.

Management commands read the existing `.env` then
`/etc/smart-ai-companion/demo.env` in the voice unit's order, without executing
those files as shell code or printing values. Resource checks refuse a mock
engine configuration rather than benchmarking with mock resource advice.

## Quick versus full

Quick runs all three installed models sequentially: a Raspberry Pi CPU question
in general mode, MQTT and ESP32 questions in concise spoken mode. Each has cold
and warm trials: **18 inference requests per repeat**. Full mode remains available
without `--quick`: **84 requests per repeat**. Neither mode downloads/deletes a
model or changes its permanent default. Cold means Ollama-unloaded, not a flushed
Linux filesystem cache. Voice quality is a text rubric, not physical audio testing.

The final summary lists warm median wall time, TTFT, tokens/second and factual
rubric passes. It offers a candidate for manual review only after all three
models complete and the factual/spoken checks pass. Quick mode is a screening
comparison, not comprehensive validation or production p95 measurement. Review
the saved answers before choosing a model; the script never changes the default
and never automatically starts a full benchmark.

## Manual Pi deployment

Use the final reviewed commit's `scripts/deploy_benchmark_timeouts.sh` through
the documented sudo supervisor, with `DEPLOY_USER` set to the existing checkout
owner. It requires baseline `afef8395f25d127e9572eb8ee6289e908f814e9f` on
`feature/adaptive-online-retrieval`. Clean tracked files are required, untracked
`knowledge/` is allowed, and environment/knowledge metadata is checked. No clean,
reset, stash, migrations, hardware reconfiguration or environment writes occur.
The focused commit is cherry-picked; conflicts stop before services are changed.
Use `git cherry-pick --abort` and review conflicts rather than discarding edits.

After applying the fix, the supervisor stops voice before synthetic voice tests,
runs regression/Django/whitespace checks, then stops the app for quick comparison.
It restores both services even if Ollama cleanup fails. The script prints the
report path, concise measured summary, and rollback tag. Roll back as the normal
checkout owner with `git revert --no-edit <printed-tag>`, run Django checks, then
restart the two services. Online acquisition/routing and existing intelligence
fixes are not changed by this commit. No Pi access or physical test is performed
by development validation.

Validation: 864 selected regression tests passed in a clean export excluding
unrelated working-tree edits, including 16 new timeout/cleanup/report tests and
the existing voice/intelligence/online suites. Django checks, whitespace checks
and Bash syntax validation passed. Actual Pi timing, model rankings, service
restoration and physical audio were not exercised on the development machine.
