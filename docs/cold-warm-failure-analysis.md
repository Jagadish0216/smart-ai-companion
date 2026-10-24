# Cold/warm failure analysis and bounded timeout repair

## What the supplied result establishes

The operator reported an aborted cold request lasting 20.03 seconds, no answer or completion
metrics, no warm request, initial temperature 42.5 C and peak 44.1 C, final temperature
44.4 C, no current power/throttle flags, and an empty resident list afterwards.
The prior successful short answer took 19.328 seconds, including 16.7035 seconds of loading
and first meaningful text at 17.948 seconds. These are supplied observations, not new trials.

At starting commit `d6b289e776a2bbfdca3cbd3e8a388bfbb0ed9b3f`, the parent in
`scripts/compare_safe_warm_response.py` called required warm residency before checking
`process.returncode` or reading the saved child's failure. The standalone parent in
`scripts/test_short_answer_thermal.py` had the same precedence error. An empty `/api/ps`
therefore caused `premature_model_expiry` to mask an already failed cold request.
The shared stream used `opener.open(..., timeout=20)`. The worker wrapper also replaced
non-TrialRefused exceptions with `worker_failed_or_cancelled`, discarding exception detail.

The 20.03-second timing is consistent with that socket timeout, but does not prove a socket
timeout occurred. The old checkpoint/report lacks the needed exception evidence. Nor does
an empty resident list prove that Llama completed loading and then expired normally.
The backend could have been loading, stalled, failed, or cancelled; distinguishing those
requires retained exception details and, where available, operator-inspected backend logs.
There is no evidence of the thermal cutoff triggering in the supplied observations.

## Corrected precedence

The parent now reads the checkpoint and worker return code immediately after monitor exit,
classifies the actual child outcome, and preserves that failure before any successful-completion
identity/residency checks. Final telemetry is retained if available, with secondary pressure or
telemetry errors recorded without replacing the child failure. Existing active monitor safety
or deadline aborts retain priority because the parent actively stopped the trial. Termination,
checkpoint rereading and partial-answer retention still run in `finally`.

Only a complete, untruncated request with successful worker exit qualifies for post-request
required residency. Missing or unreadable checkpoints are worker failures. Warm request two
cannot follow any cold failure, incomplete answer, or factual-screen rejection. The standalone
short-answer parent also checks the child's result before required post-request residency.

The stream checkpoints direct and URLError-wrapped socket timeouts explicitly, including
exception type/detail, actual elapsed duration, partial text and any metrics already received.
The outer worker wrapper preserves this diagnosis rather than replacing it.

| Reason | Meaning |
| --- | --- |
| `cold_load_timeout` | Observed socket inactivity timeout during the cold phase; loading as the underlying cause remains unproven. |
| `http_socket_timeout` | Observed socket timeout in warm or standalone/default phase. |
| `premature_model_expiry` | Required residency absent at warm preflight or after an otherwise completed request; not proof of normal expiry. |
| `worker_failure` | Unhandled worker error, abnormal exit, or missing/malformed checkpoint. |
| `worker_cancelled` | Worker received cancellation; parent safety/deadline diagnosis remains primary when applicable. |
| `incomplete_answer` | Child returned without a valid complete, untruncated answer. Partial text is retained. |
| `temperature_safety_margin` | Existing runtime monitor observed 70 C or above. |
| `below_50_cool_start_required` / `warm_deferred_thermal` | Start/pre-warm thermal gate refused work. |
| `request_deadline` | Existing 30-second per-request monitor budget expired before the session deadline. |
| `session_deadline` | Remaining 60-second session budget expired; includes setup time. |
| `factual_screen_failed` | Complete answer failed the simple factual heuristic; manual review is still required for passing answers. |

Power, throttling, RAM and identity failures keep their existing distinct safety reasons.

## Timeout policy and rationale

The comparison alone now gives a cold socket operation 25 seconds instead of 20; warm stays
at 20. The shared standalone diagnostic's default remains 20 seconds. The previous 19.328-second
complete answer left only 0.672 seconds against 20 seconds if the server delayed its response
until completion, or 2.052 seconds against the observed first text. A 25-second socket limit
adds 5 seconds for ordinary variation, offering approximately 5.67 seconds beyond that observed
complete answer without making any performance promise. HTTP headers and stream chunks may
arrive separately, so this comparison is rationale for headroom, not an exact model of socket
inactivity or proof of the failed request's cause.

The existing 30-second monitored request bound is unchanged, including worker startup and
preflight after spawning. The HTTP limit must be positive and below 30 seconds. The session
budget stays 60 seconds, with each monitor capped by remaining session time. Existing metadata
request timeouts, polling intervals and cleanup mean these are monitored execution budgets,
not an instantaneous hard wall-clock guarantee for final report writing or backend unload.
No retries, adaptive extensions, cooling waits or additional inference requests are introduced.

The 70 C cutoff, below-50 C parent/cold-worker/warm-worker starts, strict below-55 C pre-warm
check, conservative cold-load RAM allowance, power/throttle rejection, exact digest/context
checks, local-only HTTP, diagnostic lock, cancellation and process-group termination remain.
The colder worker start can still defer a warm phase even if the parent was below 55 C.
No timeout setting can override these safety gates.

## Future operator-initiated trial

From the updated `codex/offline-model-selection` checkout with the Pi environment active,
run this single command only when ready for a new controlled trial:

```sh
python scripts/compare_safe_warm_response.py --output "benchmarks/safe-warm-$(date +%Y%m%d-%H%M%S)"
```

The command performs all built-in preflight and runtime guards. It will refuse resident conflicts
rather than unload anything. It sends at most two fixed requests, never retries, writes partial
checkpoints and a final report, and does not change hardware or production configuration.
No Pi inference, unload, restart, or repeated trial was executed during this repair.

## Windows regression evidence

- Focused command: `python manage.py test assistant.tests_cold_warm_failure assistant.tests_safe_warm_response assistant.tests_short_answer_thermal --noinput`
- Result: 38 tests passed, including the empty-answer/20.03-second failure followed by absent
  residency, wrapped/direct socket timeouts, partial stream retention, failed/incomplete worker
  precedence, genuine post-completion residency refusal, thermal/resource gates, deadlines,
  unchanged request/session limits and no retry/warm-after-failure behavior.
- Full command: `python manage.py test --noinput`
- Result: 1,145 tests, OK, one skipped. Tests used a workspace-local temporary directory under
  the Windows sandbox; all inference was mocked.
- Evidence: `cold-warm-failure-focused-tests.log` and `cold-warm-failure-windows-tests.log`.
