# End-to-end intelligence reliability

## Root causes and scope

The service enabled implicit RAG for ordinary text questions. Any lexical match
could select local_rag and attach a strict source-only instruction, including the
instruction to refuse when documents were insufficient. That is appropriate for
document questions, but inappropriate for a general Raspberry Pi explanation.
The full runtime capability inventory also increased general-question prefill.

The online path treated an undated technical reference much like undated news.
Generic page publication metadata could not represent the separate release date
on an authoritative software catalog. The official Raspberry Pi catalog contains
explicit Release date and Debian version tables, including separate Legacy and
Desktop sections: [official catalog](https://www.raspberrypi.com/software/operating-systems/).

The reported Pi baseline `bf8a08a` was not present in fetched workstation history.
No claim of clean cherry-pick compatibility or real Pi answer quality is made.
The deployment script checks that baseline on the Pi and stops on conflicts.

## Routing and grounding

- Service requests no longer opt into implicit RAG. General questions use LOCAL,
  without searching documents or attaching irrelevant snippets. The existing
  QueryRouter explicit opt-in interface remains available for legacy callers.
- Explicit project/document/personal-knowledge requests still retrieve local
  evidence. Document-scoped requests remain strict. Project explanation requests
  may add a clearly labeled general explanation, while missing project details
  remain unknown. General knowledge cannot establish project wiring/settings.
- General local prompts use the compact invariant safety instruction rather than
  the full capability inventory. Capability/action paths retain their registry;
  actions and sensor claims still require actual results. Conversation memory,
  response formats, model selection and generation budgets remain intact.

Expected behavior:

| Request | Route | Expected response behavior |
| --- | --- | --- |
| Explain how a Raspberry Pi 5 works | local | General Linux/computer explanation; no document refusal |
| According to my notes, which GPIO did we use? | local_rag/action | Only supported document facts, otherwise unknown |
| In our project, how does the ESP32 work? | local_rag when evidence exists | Supported project facts, then labeled general explanation if needed |
| Latest Raspberry Pi OS version | online | Verified official catalog fields, or qualified dated evidence/abstention |
| Latest Raspberry Pi news today | online | Relevant evidence within two days, otherwise clear abstention |

## Intent-specific online evidence

`evidence_category` distinguishes technical references, release/version requests
and current facts. Explicit web wrappers are stripped before classifying the
topic, so "search the web for current CEO" cannot bypass current-fact checks.

Undated **technical** references require an actually accessible checked HTML page
and support timeless explanations only. They cannot establish current claims.
Future/conflicting metadata and known inaccessible URLs remain refused.

For Raspberry Pi OS **release** requests, a fixed official catalog candidate is
prioritized after a successful search. The existing SSRF-safe fetch parses the
visible, explicitly labeled primary image tables. It records a release date and
Debian-version evidence separately from page publication and verification times.
Navigation, Legacy and Desktop entries cannot be merged into the primary facts;
ambiguous primary dates/versions abstain. The Debian version is never relabeled
as a separate Raspberry Pi OS version number.

An older release may still be the currently listed release. Acceptance therefore
requires the official HTTPS catalog URL, successful page verification within five
minutes, an explicit non-future release date and bounded extracted evidence.
This does not weaken news freshness and does not substitute fetch time for
publication. A current catalog cannot prove which version was latest on a past
date: explicit historical as-of questions abstain from that snapshot.

**News/live/current** rules remain: news two days, weather three hours, prices,
scores/status fifteen minutes, and existing role/schedule limits. An OS release
date from eighteen days ago does not become today's news. Arbitrary undated
snippets and cached model memory cannot establish current facts.

Publication, release, modification, retrieval and page-verification timestamps
have distinct meanings. Public source metadata adds `release_at` and `verified_at`;
article bodies are not exposed. Grounding still treats all source content as
untrusted data and requires snippet support and uncertainty about disagreements.

## Network and Pi latency

Candidate count (20), final source/context bounds (four/3600 characters), search
retry limits, SSRF protection, redirect validation, DNS pinning, response-size
limits, failure backoff and concurrency locking are preserved. There is no extra
page-fetch allowance: the official catalog uses one of the existing four attempts.
It receives up to 1.25 seconds of the existing two-second enrichment budget;
other articles retain 650 ms per attempt. Total retrieval remains at most the
configured capped eight-second network budget (existing provider DNS caveat still
applies for hostname endpoints). No crawler or new dependency/model is introduced.
Verified catalog metadata caches for up to five minutes and is age-checked again.

The known removable latency costs are document retrieval, irrelevant RAG context
and the system inventory for generic text. Voice already uses compact safety and
normally 64 output tokens. Qwen requests already default to `think=false`;
[Ollama documents the thinking control](https://github.com/ollama/ollama/blob/main/docs/capabilities/thinking.mdx).
There is insufficient Pi timing data to attribute the reported 21 seconds to
loading, prefill, thinking or token throughput. No real speedup is claimed.

The opt-in `verify_intelligence --warm-voice` command runs all three exact public
questions plus a streaming voice-plan repetition of the general question. It
prints route, answer, source provenance, evidence status/fallback, wall time,
generation/search/TTFT metrics, token budget, configured thinking flag and Ollama
load/prompt/eval timing and throughput. Compare load_duration_ms (residency),
prompt_eval_duration_ms/input_chars (prefill), and eval_duration_ms/eval_count
(decode). A repeated request is a comparison, not proof the model stayed resident.

The command refuses benchmarks unless resource policy is healthy BALANCED or
PERFORMANCE and permits generation. It does not synthesize audio or operate GPIO,
TFT, ESP32 or microphone. It creates normal conversation rows, disables automatic
online knowledge ingestion for verification, and never clears any data. Abstention
is displayed honestly, not counted as a successful current answer. The manual
command's environment may differ from service-specific demo.env overrides; inspect
that distinction locally without printing credentials. Do not overwrite settings.

## Regression validation

Workstation result: **665 tests passed in 43.693 seconds**; Django and whitespace
checks passed. Bash `-n` validated deployment-script syntax without execution.
This is the complete relevant module set below, not every test in the repository.

Changed implementation files: `assistant/routing.py`, `assistant/services.py`,
`knowledge_base/services.py`, `assistant/online/base.py`,
`assistant/online/evidence_policy.py`, `assistant/online/enrichment.py`,
`assistant/online/grounding.py`, `assistant/online/searxng.py`, `assistant/views.py`,
`assistant/management/commands/verify_intelligence.py`, and
`scripts/deploy_intelligence_reliability.sh`. Regression additions/updates are in
`assistant/tests_intelligence_reliability.py`, `assistant/tests.py`,
`assistant/tests_policy.py`, `assistant/tests_routing.py`,
`assistant/tests_single_model.py`, and `knowledge_base/tests_rag.py`, plus this guide.

Use a writable temp directory on Windows, then run the full relevant modules:

```powershell
$env:TMP = Join-Path (Get-Location).Path '.test-tmp'
$env:TEMP = $env:TMP
venv/Scripts/python.exe manage.py test assistant.tests_intelligence_reliability assistant.tests_search_selection assistant.tests_publication_metadata assistant.tests_adaptive_online assistant.tests_online assistant.tests_routing assistant.tests_streaming assistant.tests_execution_policy system.tests_resource_manager knowledge_base.tests_rag knowledge_base.tests_online_sync conversations.tests_memory assistant.tests_policy assistant.tests_push_to_talk assistant.tests_voice_loop assistant.tests_devices assistant.tests_voice_stream assistant.tests_display assistant.tests_offline_models assistant.tests_short_answer_thermal assistant.tests_single_model assistant.tests --noinput
venv/Scripts/python.exe manage.py check
git diff --check
```

Tests use network/model/hardware doubles, including both response transports,
irrelevant RAG, strict document versus explanatory project prompts, official
HTML table extraction, release/date integrity, historical snapshots, undated
technical versus current facts, outages, SSRF/redirects, cache/concurrency, memory,
offline models, voice/PTT/display/devices and resource budgets. Real Pi latency,
audio and physical interfaces remain manual checks. No Pi was accessed here.

## One paste-once Pi entry point

The reviewed commit contains `scripts/deploy_intelligence_reliability.sh`. Set
the exact full fix SHA below (provided in the completion report), then paste the
whole block once on the Pi. It fetches and executes that immutable reviewed script,
not a moving remote script or unrelated branch history:

```sh
bash <<'DEPLOY'
set -euo pipefail
cd /home/jagadish/apps/smart-ai-companion
FIX='<reviewed-full-fix-sha>'
[[ "$(git rev-parse --short=7 HEAD)" == bf8a08a ]]
[[ -z "$(git status --porcelain)" ]]
git fetch origin feature/intelligence-reliability
[[ "$(git rev-parse FETCH_HEAD)" == "$FIX" ]]
export REVIEWED_COMMIT="$FIX"
git show "$FIX:scripts/deploy_intelligence_reliability.sh" | bash
DEPLOY
```

The script checks baseline/branch, clean status, reviewed SHA, focused allowed
paths and a knowledge-file metadata signature. It cherry-picks only the fix,
prints the resulting deployment SHA, runs tests/checks before restarting the two
existing services, checks service health, and executes all three questions plus
the streaming voice comparison. It stops on any failure; it does not reset,
stash, migrate, download/delete models or modify hardware/settings. Review answers
and evidence status rather than treating successful command exit as proof of
factual correctness. `knowledge/` is never edited; unexpected changes stop the
script. Tests create a separate test database; verification adds normal rows to
the application database, with no resets. No service operation was executed here.

If a cherry-pick conflicts, stop and review; `git cherry-pick --abort` cancels only
that in-progress application. If tests fail after cherry-pick, the fix remains
applied and no restart is performed; use the printed SHA for reviewed rollback.

Rollback on a clean Pi checkout, substituting the printed deployed SHA:

```sh
git status --short  # stop and preserve changes if dirty
git revert <printed-deployed-sha>
venv/bin/python manage.py check
sudo systemctl restart smart-ai-companion.service smart-ai-companion-voice.service
systemctl is-active --quiet smart-ai-companion.service
systemctl is-active --quiet smart-ai-companion-voice.service
```

Existing knowledge sync, saved-document retrieval and timestamp provenance remain
compatible with later offline-model/verification work. There is no knowledge-store
redesign or new persistence schema, and stored information is never labeled live.
