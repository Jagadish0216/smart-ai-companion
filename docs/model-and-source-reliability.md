# Model performance and source reliability

No active model, environment, database, knowledge file, hardware driver or service
configuration is changed. Model selection remains manual and resource protections
continue to apply. This upgrade does not claim a faster model without Pi data.

## Controlled benchmark

In a maintenance window with both application services inactive, run:

```bash
venv/bin/python manage.py benchmark_models --repeats 1 > /tmp/models.jsonl
```

The command refuses active/unknown service state, acquires the existing microphone
ownership lock without opening audio, and checks all three models are installed.
It unloads each model between cold trials, verifies residency, then runs the same
prompt warm. Cold means Ollama-unloaded, not flushed Linux disk/page caches. Warm
also benefits from Ollama prompt caching. Models run sequentially. Nothing is
downloaded or deleted. A previous resident model is restored with a request-scoped
five-minute keep-alive only if resource policy still allows it; configured defaults
are never written. Avoid web/CLI inference during this maintenance window.

Seven fixed public questions cover general science, coding, MQTT, Pi CPU/RP1,
ESP32 and conversation. Each gets general and concise spoken instructions, with
cold/warm pairs for each of the three models: 84 requests per repeat. Allow tens
of minutes on the Pi. `--repeats 2` or `3` improves sampling. All models receive
the same question/instruction pairs, seed 42, temperature 0, thinking off, context
2048, four threads and batch 128. Output caps are 96 general/64 voice tokens,
further limited by existing resource policy; actual settings are in each row.

JSONL includes TTFT, generation wall time (excluding monitoring/health checks),
Ollama prompt/output token counts, tokens/second, prefill/load timings, sampled
peak sum of Ollama server/runner RSS, temperature and throttling bits. RSS is
sampled every 250ms; it is not exact peak PSS and shared pages can be counted
twice. Missing telemetry is null, never a fabricated zero. A socket watchdog caps
each generation at 120 seconds or the shorter policy timeout. Temperature >=75C,
available memory <384MiB, current throttling, or a nonhealthy resource policy
stops the run. The sampler can interrupt a blocked streaming read.

Each fixed answer is saved for manual inspection. Pattern-based factual and
spoken-length checks are screening rubrics, not proof of complete correctness or
audible speech quality. No microphone, Piper playback or TFT is exercised. Summary
rows provide nearest-rank warm p95 and median p50 across fixed questions/modes;
small samples are not production p95 estimates. A candidate is proposed only if
all rubric/voice checks pass; select it only after reviewing Pi measurements,
answers and memory headroom. No default model is changed automatically.

## Factual instructions

A compact, reviewed offline public reference is injected only into relevant
general Raspberry Pi 5 CPU/specification/explanation requests: BCM2712,
quad-core Cortex-A76 at 2.4GHz, VideoCore VII, and RP1's distinct I/O role.
Source: https://www.raspberrypi.com/documentation/computers/processors.html
This reference never establishes the user's installed board, wiring, project
files or actions. Existing document-only/project grounding remains in force.
Ordinary explanations stay offline. Other specification questions receive one
compact uncertainty instruction, without a universal refusal or large inventory.

## Online acquisition

News searches request SearXNG's `news` category and `day` range, letting configured
news engines supply their publication metadata. Enable date-capable news engines
in the existing SearXNG configuration if none are available; no search engine
configuration or authentication is changed by this patch.

When nonempty results fail admission, at most one extra discovery search runs
inside the original eight-second budget. Pi OS release fallback targets official
Raspberry Pi news release notes, preserving the question's requested date. Other
releases get a release-notes reformulation; news gets a headlines reformulation.
There are no fallback retry loops. Both enrichment passes share a two-second
page-fetch deadline and four-attempt cap; the first reserves time for the second. Existing DNS pinning,
SSRF/redirect restrictions, bounded page bodies and script rejection stay intact.
403/429 sources retain their backoff and are not bypassed. Failed fallback search
preserves the original results and honest abstention. Short bounded caches remain.

No freshness threshold is loosened. A recent dated announcement may support its
explicit release facts, but does not prove a different Debian version or an
undocumented latest image identifier. Debian version, OS release date, page
publication and modification dates remain distinct. If the official catalog is
blocked and discovered release notes are old, undated or insufficient, abstention
is still correct. This patch cannot guarantee a particular external site's access.

Metadata separates provider reachability, results retrieved, page sources
verified, unavailable/access-denied sources, fresh evidence admitted and existing
`online_used` (evidence supplied to generation). `online_success` stays false when
all evidence is rejected. These fields cannot prove the model internally used
every supplied fact. Safe performance logs contain counts, not private questions.

```bash
venv/bin/python manage.py verify_online_sources
```

This emits release/news diagnostics for today's date without model generation,
conversation creation or knowledge persistence. `--require-evidence` exits nonzero
if either case lacks answerable evidence. Otherwise abstention is reported in JSON
and must not be interpreted as successful online answering.

## Deployment and rollback

After fetching the reviewed full commit SHA, execute its
`scripts/deploy_model_source_reliability.sh` with `REVIEWED_COMMIT` set. It requires
baseline `8d1672fa71b18c00247eba0e59c41f78aaa0784f` on
`feature/adaptive-online-retrieval` and clean tracked files. Untracked knowledge
and other assets are preserved. No stash/reset/clean is used; abort a cherry-pick
conflict with `git cherry-pick --abort` and review manually.

The script checks the commit's changed-path allowlist, applies only that commit,
records a local rollback tag, runs synthetic regression tests and Django checks
while existing services remain active, and checks protected asset metadata.
Only after checks pass does it stop services for an exclusive benchmark window.
An exit trap restores both services if benchmarking fails. It restarts services
after benchmarking, then reports online source acquisition. Test failures never
restart services. Runtime SQLite files are never reset; normal service activity
may still write its existing database. Configuration/knowledge metadata is checked
before and after; installed models are never deleted. Actual Pi baseline patch
compatibility and physical functionality require validation on the Pi.

Rollback using the printed local tag: `git revert --no-edit <tag>`, run
`venv/bin/python manage.py check`, then restart the two existing services. If
benchmarking or external acquisition fails, retain the logs and do not infer a
speed or evidence-quality improvement from the development tests alone.

API metric definitions: https://github.com/ollama/ollama/blob/main/docs/api.md

Development validation: 848 selected voice, hardware-mock, intelligence, online,
offline and resource-policy tests passed in a clean export excluding unrelated
local edits. Django checks, whitespace checks and Bash syntax checks passed.
Sixteen new tests cover discovery/caching, blocked-source diagnostics, strict
freshness rejection, both assistant paths, offline references and benchmark
streaming/settings/serialization. These are not physical Pi benchmark results.

SearXNG search parameters: https://github.com/searxng/searxng/blob/master/docs/dev/search_api.rst
