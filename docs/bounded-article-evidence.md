# Bounded article evidence and candidate priority

The response cap remains **262,144 bytes plus one overflow-detection byte**. No
additional download, dependency, environment setting, database migration, model,
hardware, audio or service configuration is introduced.

## Partial response contract

Normal responses keep the existing extraction path. A detected byte overflow can
instead inspect only the capped prefix using incremental UTF-8/HTML parsing. This
never treats an unread document tail as inspected or the response as complete.
Partial evidence requires all of the following:

- HTTPS and the existing safe, pinned, hostname-verified transport.
- One fully closed metadata head and one fully closed, balanced `article` subtree.
- At least one complete, visible paragraph/list-item body block, beyond headings.
- Explicit canonical/OG page identity matching the final fetched URL, including its
  query, and exactly one matching Article JSON-LD record with a publication date.
- Consistent publication metadata and visible publication labels in the inspected
  scope. Matching metadata declarations later in the read prefix also constrain it.
- Relevant claim-bearing text accepted by the existing query-specific extractor,
  freshness checks and contradiction handling. Explicit internal date conflicts in
  the bounded article are rejected before shortening the evidence text.

Missing dates cannot be filled from search snippets, URL/title dates or retrieval
time. Wrong-page, duplicate/ambiguous Article metadata, truncated scopes, invalid
UTF-8, malformed JSON, unbalanced markup, observed restrictions and conflicting
dates reject the partial path. Script, navigation, advertising, related-content,
hidden and aside text cannot supply partial body claims. No scripts execute.

The partial parser has limits of 8,000 callbacks, 64 nesting levels, 32,768 JSON
characters per block, 100 traversed JSON nodes and 12,000 visible article characters.
It checks the original page deadline between 8 KiB feeds and during callbacks.
It never flushes a truncated tag/entity into visible evidence. The existing four
fetch attempts, two seconds of shared page work, per-page caps and total request
deadline remain in effect. Parsing consumes that same allowance.

Only byte-cap overflow is eligible for this recovery. A genuine timeout,
connection error, early EOF with declared bytes missing, 403/429, anti-bot page,
unsupported compression/type or unsafe redirect is never rescued this way.
Existing DNS/public-address checks, address pinning, TLS hostname checks, redirect
validation, HTTPS downgrade rejection, negative caching, locks and 403/429 backoff
remain. Oversized HTTP pages are ineligible for partial evidence. No allowlist or
publisher restriction is bypassed.

`partial_response` is carried in source provenance and existing SQLite snapshot
files, preserving actual verification and expiry timestamps. Grounded context
states that the HTML response is partial and its unread tail is unknown. Offline
cache answers remain explicitly dated, previously verified evidence, not live
checks. Partial metadata admission is rechecked on metadata-cache reuse.
Officeholder profiles and OS catalogs require their existing complete response
path; partial bodies cannot establish those special evidence kinds. Narrow
Qwen-free date answers also require a complete response. Their existing path for
complete evidence is preserved.

## Candidate priority and diagnostics

For current entertainment/general reporting queries, relevant recent dated
articles rank ahead of undated article candidates, stale reports, reference pages,
biographies/filmographies, social feeds, generic listings and Q&A/forum pages.
Ranking dates are discovery hints, never evidence admission. Within each priority
tier, enrichment retains host diversity. When promising relevant article
candidates exist, lower-value alternatives are deferred without spending fetch
attempts on them. This is query-specific prioritization, not a global source ban
or an assertion that a publisher is trustworthy. Timeless reference queries keep
their existing source treatment. Official profile priority remains intact.

Internal retriever `article_diagnostics` contains bounded counters for candidate
classes, decisions/defer reasons, fetched candidate classes, complete/partial/cache
outcomes and fixed failure reasons. `Article candidate prioritization` log records
carry the same counters. `Article fetch completed` records carry fixed reason codes,
elapsed milliseconds, byte counts, redirect hops and HTTP status where available.
Examples include `partial_publication_missing`, `partial_publication_conflict`,
`partial_incomplete_scope`, `partial_token_limit`, `http_403`, `http_429`,
`unsupported_compression`, `unsafe_redirect`, `incomplete_response` and `timeout`.
No URL, hostname, query, title, body text, credential or exception message is added
to these diagnostic records. Result-cache hits report a cache outcome rather than
replaying old fetch counts. Existing timing fields remain unchanged.

## Changed files and validation

| Files | Purpose |
| --- | --- |
| `assistant/online/partial_html.py` | Strict bounded partial-article parser |
| `assistant/online/enrichment.py` | Overflow handling, cache safeguards, EOF checks and safe fetch/decision diagnostics |
| `assistant/online/selection.py` | Query-specific reporting priority and fetch order |
| `assistant/online/searxng.py` | Shared aggregate article diagnostics |
| `assistant/online/base.py`, `knowledge_base/online_sync.py` | Optional partial-response provenance and snapshot reload |
| `assistant/online/grounding.py`, `assistant/online/facts.py` | Partial admission/context and complete-response direct-answer requirement |
| `assistant/fixtures/bounded_article.html`, `assistant/tests_bounded_article.py` | Synthetic HTML and mocked transport/security/ranking/cache regressions |
| `docs/bounded-article-evidence.md` | Contract, limitations and deployment |

Tests use synthetic HTML, mocked network transports, temporary media and an
in-memory database. They establish code behavior, not live Filmibeat, iDreamPost or
other publisher compatibility. No Raspberry Pi was accessed and no new Pi latency
measurement was made. Production knowledge files and `.env` were not modified.

The clean-export validation passed **460 affected tests**, including the existing
officeholder, general knowledge, offline RAG, streaming, voice and resource-policy
regressions. Django checks passed under normal and test settings, and Git whitespace
checks passed. Unrelated local working-tree edits were excluded from the export.

Large pages whose evidence starts beyond the cap remain unsupported. Sites without
a closed article element, explicit matching structured metadata, dated text or
supported markup may abstain. Dense/malformed markup can hit the parser limits.
The unread tail may contain information this bounded extraction cannot know;
partial evidence supports only the inspected closed article scope. Contradiction
detection remains bounded pattern detection. Parsing and diagnostics add finite
CPU/logging work within existing request budgets; tests do not prove Pi throughput
or latency parity. Real-device validation is still needed before compatibility or
performance claims.

## Safe single-commit Pi deployment

From the existing Pi repository, use the exact delivered SHA in place of the
placeholder. The supplied baseline must match before applying this single patch;
it is not available as an object in the development clone. Each failure stops the
subshell. Preserve untracked knowledge; Git will stop if an untracked file obstructs
the cherry-pick. Do not merge all branch history.

```bash
(
set -eu
BASELINE=c9f6316ac98a9dd7c4445565f74f9c29e6ccdcb3
FIX_SHA='<exact delivered commit SHA>'
test "$(git rev-parse HEAD)" = "$BASELINE"
git diff --quiet
git diff --cached --quiet
test ! -e "$(git rev-parse --git-path CHERRY_PICK_HEAD)"
test ! -e "$(git rev-parse --git-path MERGE_HEAD)"
git fetch origin fix/bounded-article-evidence
test "$(git rev-parse FETCH_HEAD)" = "$FIX_SHA"
git show --stat --oneline "$FIX_SHA"
git cherry-pick "$FIX_SHA"
printf 'Record this Pi commit for rollback: '
git rev-parse HEAD
venv/bin/python manage.py test \
  assistant.tests_bounded_article assistant.tests_general_current \
  assistant.tests_current_information assistant.tests_search_selection \
  assistant.tests_publication_metadata assistant.tests_online \
  assistant.tests_adaptive_online assistant.tests_streaming assistant.tests_routing \
  assistant.tests_automatic_offline_rag assistant.tests_execution_policy \
  assistant.tests_policy assistant.tests_browser_voice assistant.tests_voice_stream \
  knowledge_base.tests_online_sync knowledge_base.tests_retrieval_projection \
  knowledge_base.tests_rag system.tests_resource_manager \
  --settings=config.test_settings --noinput
venv/bin/python manage.py check --settings=config.test_settings
venv/bin/python manage.py check
)
```

On conflicts stop and run `git cherry-pick --abort`. Do not reset, clean, stash,
overwrite knowledge or change `.env`. On failed validation leave running services
untouched and investigate or revert the recorded Pi commit with
`git revert <recorded-Pi-commit>` on a clean tracked worktree. The cherry-picked Pi
SHA differs from the delivered source SHA. After checks pass, adopt the code using
the application's normal controlled reload/restart procedure. No migration or
unrelated service restart is needed. Report any later live compatibility and Pi
performance checks separately.
