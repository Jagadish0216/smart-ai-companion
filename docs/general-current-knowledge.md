# General current knowledge retrieval

The current-information path keeps offline lexical RAG, the officeholder direct
profile adapters, Qwen, audio, UI, resource policy and safe HTTP transport intact.
There are no migrations, new services, downloads, embeddings or external APIs.

## Discovery, evidence and bounds

Discovery strips conversational framing, retains user names and requested dates,
and expands Tollywood/Telugu, Kollywood/Tamil, Mollywood/Malayalam and Bollywood/Hindi
industry terms. Actor projects receive production-announcement hints; film releases
receive release-schedule hints. General reports receive announcement/report hints;
software release queries retain release-note hints. No actor, film or fact is invented.
Explicit today/headline requests use a day search filter; this-week requests use a
week filter. A search filter is a discovery hint, never evidence of publication.

There are at most two SearXNG calls per retrieval, including retries and alternative
discovery, with no unrelated connectivity canary. Empty results abstain and do not
mark external search healthy. HTTP reachability alone does not establish evidence.
The original total deadline remains `min(ONLINE_TIMEOUT_SECONDS, 8)` seconds.
Direct officeholder and fallback page work retain four attempts and two seconds
in total, existing per-page caps, locks, negative caching and publisher backoff.
An alternative search cannot renew the page-work allowance or the total deadline.
Already encountered URLs do not trigger repeated fetches through another query.

Extraction ignores conversational filler, recognizes AI/technology synonyms, and
preserves name anchors. A short, fetched regional-cinema heading can qualify its
following release claim; another language heading ends that context. Search titles
do not supply missing body evidence. Subscription prompts and generic current-affairs
overviews are insufficient. Dated, relevant search snippets retain the existing
grounded-summary behavior; they never qualify for the direct-answer path.

Publication parsing additionally accepts full month/day dates without commas and
explicit `Published`/`Published on` labels in the first 1,200 visible characters.
Publication metadata and visible labels must agree on their day. Titles, URLs,
modification dates, event dates and relative strings such as "2 hours ago" cannot
manufacture publication dates. Existing age windows and verification checks remain;
fetched current reports must also have an actual verification within five minutes.
Explicit release dates outside a requested today/this-week window are excluded.

Report status distinguishes reported confirmations, schedules, announcements,
production updates, released events, rumours, denials and outdated schedules. "Confirmed"
describes what a report says; it is not independent certification of the event.
An old schedule is never proof that its event occurred. Provenance includes the
new optional `claim_status` field; older snapshot files remain readable. Existing
`entertainment_status` labels remain compatible; the general status separates
reported confirmation from scheduling.
Comparable explicit date disagreements and date-specific denials cause abstention
before deduplication or source-count truncation. Free-form contradiction detection
remains deliberately limited; the grounding instruction also requires uncertainty.

SQLite snapshots remain the first service-level lookup. Process cache hits rerun
freshness/conflict admission. No-evidence results have a short five-second cache.
All caches retain actual verification timestamps and existing expiry bounds. Offline
answers quote only eligible previously verified evidence, with its date and the
explicit `not a live check` label. Tests never use the production knowledge database.

## Narrow direct answers

Exact questions such as `When will Example Movie release?` or
`When is Example Probe scheduled to launch?` can return an attributed date statement
without Qwen generation. The accepted relation must name the exact subject, state a
full calendar date, and match the requested release/launch/announcement and tense.
Each admitted source must have fetched HTTPS body evidence, an article publication
date, an actual verification within five minutes, and no uncertain, denied or
conflicting claim. The answer quotes the explicit relation and includes publication
and verification dates. Past schedules and future claimed completions are rejected.
Response metadata/request logs expose `source_supported_direct` for this path.

Broader project lists, release roundups, news summaries, explanations, partial
matches and rumour discussions continue through grounded generation. Unsupported
date-question wording also keeps the model path. This intentionally trades breadth
for safe, inexpensive factual answers. Existing resource restrictions still apply.

## Changed files and validation

| Files | Change |
| --- | --- |
| `assistant/online/intent.py` | Query formulation, aliases and discovery filters |
| `assistant/online/facts.py` | Comparable explicit date claims and direct-answer admission |
| `assistant/online/current_sources.py` | Regional context, report extraction and status |
| `assistant/online/searxng.py` | Two-call discovery, shared budgets and cache admission |
| `assistant/online/selection.py` | Matching normalized source/query terms |
| `assistant/online/enrichment.py`, `dates.py` | Visible publication labels and narrow factual body verification |
| `assistant/online/grounding.py` | Freshness, date windows, conflicts and status instructions |
| `assistant/online/base.py`, `knowledge_base/online_sync.py` | Optional report-status provenance |
| `assistant/routing.py`, `assistant/services.py` | Future-date question routing and shared direct-answer path |
| `assistant/fixtures/current_reports.json`, `assistant/tests_general_current.py` | Synthetic general-topic fixtures and mocked acquisition/service tests |
| `assistant/tests_online.py`, `assistant/tests_adaptive_online.py` | Intent-only empty discovery, negative-cache and current-date fixtures |
| `assistant/tests_current_information.py` | Regional this-week fixture uses a date inside the requested week |
| `docs/general-current-knowledge.md` | Behavior, limitations and deployment procedure |

Automated tests use fictional reports, generated relative fixture dates, mocked
requests/clocks, temporary media and an in-memory database. They demonstrate code
behavior, not real-world publisher accessibility, truth or Pi latency. No Raspberry
Pi was accessed. Real sites can still be unavailable, paywalled, slow, larger than
the response cap, missing dates, multilingual beyond the English adapters, or have
unsupported markup. Ambiguous and unsupported claims may still abstain. Broad
questions still incur Qwen generation costs. No new end-to-end latency is claimed.

The final clean-export run passed 442 relevant tests, including officeholder,
offline RAG, streaming, browser/streamed voice and resource-policy regressions.
Django checks passed with normal and test settings; Git whitespace checks passed.
The export excluded unrelated local working-tree edits. Production `.env` and the
knowledge database were not modified.

## Safe single-commit Pi deployment

Use the exact delivered commit SHA, not every commit on this branch. Run from the
existing Pi repository during maintenance. The baseline below is supplied by the
user; it must match the Pi before applying the patch. This procedure makes no
changes to `.env`, knowledge files, models or service configuration.

Replace the placeholder with the delivered SHA. Every failure stops the subshell.
Tracked work must be clean; untracked knowledge may remain. Git stops rather than
overwriting an obstructing untracked file.

```bash
(
set -eu
BASELINE=6fd398c08b10b593580b64fe9899174947d58b13
FIX_SHA='<exact delivered commit SHA>'
test "$(git rev-parse HEAD)" = "$BASELINE"
git diff --quiet
git diff --cached --quiet
test ! -e "$(git rev-parse --git-path CHERRY_PICK_HEAD)"
test ! -e "$(git rev-parse --git-path MERGE_HEAD)"
git fetch origin fix/general-current-knowledge
test "$(git rev-parse FETCH_HEAD)" = "$FIX_SHA"
git show --stat --oneline "$FIX_SHA"
git cherry-pick "$FIX_SHA"
printf 'Record this Pi commit for rollback: '
git rev-parse HEAD
venv/bin/python manage.py test \
  assistant.tests_general_current assistant.tests_current_information \
  assistant.tests_search_selection assistant.tests_publication_metadata \
  assistant.tests_online assistant.tests_adaptive_online assistant.tests_routing \
  assistant.tests_streaming assistant.tests_automatic_offline_rag \
  assistant.tests_execution_policy assistant.tests_policy \
  assistant.tests_browser_voice assistant.tests_voice_stream \
  knowledge_base.tests_online_sync knowledge_base.tests_retrieval_projection \
  knowledge_base.tests_rag system.tests_resource_manager \
  --settings=config.test_settings --noinput
venv/bin/python manage.py check --settings=config.test_settings
venv/bin/python manage.py check
)
```

On conflicts stop and run `git cherry-pick --abort`. Do not reset, clean, stash or
merge the entire branch. On failed validation leave running services untouched;
investigate or revert the recorded Pi commit using `git revert <recorded-Pi-commit>`
from a clean tracked worktree. The Pi cherry-pick SHA will differ from the source
SHA. Only after successful checks should the application load the patch through
its normal controlled reload/restart procedure. No database migration or unrelated
service restart is needed. Report any later real Pi measurements separately.
