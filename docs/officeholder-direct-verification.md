# Current officeholder direct verification

Supported questions reuse an unexpired, previously verified SQLite snapshot first
in both assistant transports. The retriever next checks its short process cache,
rerunning officeholder freshness and conflict admission. On a miss it verifies the
known official HTTPS profile before SearXNG discovery. Only the existing Telangana
Chief Minister and India Prime Minister adapters qualify; the allowlist is unchanged.

Direct verification uses the existing secure fetcher, visible-page extraction,
official final-URL check, metadata cache, negative cache, backoff and fetch lock.
An official profile is a candidate until its fetched text supports a current claim.
Search outages do not suppress independent official verification, and a successful
profile fetch does not mark SearXNG healthy. Failed verification uses existing bounded
discovery when at least 150 ms remains. Direct and fallback share the original
`min(ONLINE_TIMEOUT_SECONDS, 8)` total deadline, four page attempts, and two seconds
of page work, with at most 1.25 seconds per current-page fetch. Search time consumes
the total deadline; it does not reset the remaining page-work allowance.

Missing, ambiguous, expired, redirected-to-unscoped or conflicting evidence leads
to abstention. Internal conflicting claim sentences and conflicting sources are
checked before result-count truncation. Conflicting answers never enter the result
snapshot cache or persistence; the existing raw-page metadata cache is unchanged.
Existing persistence bounds expiry by actual verification time. Neither process
cache nor metadata cache hits manufacture a new verification time. Offline answers
retain the explicit previous verification date and `not a live check` label.

Safe response metadata and request logs report separate integer millisecond fields:

| Field | Scope |
| --- | --- |
| `cache_ms` | Existing snapshot/process cache lookup |
| `direct_fetch_ms` | Direct official verification pipeline, including existing metadata-cache lookup |
| `fallback_ms` | Complete search fallback after unsuccessful direct verification |
| `search_ms` | Search calls only; included within fallback time for supported offices |
| `enrichment_ms` | Search-path page enrichment only; included within fallback time |

SQLite and process result-cache hits report zero direct-fetch, search, fallback and
enrichment time. Metadata cache reuse retains its original `verified_at`; the
pipeline timing is not proof that HTTP occurred. These nested timings should not
be added together as independent elapsed stages.

The clean-export validation passed 302 affected tests and Django checks under
normal and test settings. Validation uses fictional officeholders, mocked
transport/clock inputs, temporary files and an in-memory database. The export
excluded unrelated local working-tree edits. No new Pi end-to-end latency measurements were
made. User-provided standalone Pi measurements are not full-request benchmarks.
No API endpoint, LLM, audio, UI, schema, environment or service configuration changed.

## Safe Pi cherry-pick

Run from the existing Pi repository during the normal maintenance workflow. The
reported Pi baseline is `755547d9551ca68f7a2a3745ae8c93988f0c702b`; that Pi-local
object is not available in the development checkout, so the baseline and patch
compatibility must be checked on the Pi. Use the exact delivered commit, not all
commits on the branch. Untracked knowledge files may remain; Git will stop if an
untracked file would be overwritten. Preserve all local work and knowledge.

Replace the placeholder with the exact SHA in the delivery message. Each failure
stops this subshell; do not continue past a failed check or cherry-pick.

```bash
(
set -eu
BASELINE=755547d9551ca68f7a2a3745ae8c93988f0c702b
FIX_SHA='<exact delivered commit SHA>'
test "$(git rev-parse HEAD)" = "$BASELINE"
git diff --quiet
git diff --cached --quiet
test ! -e "$(git rev-parse --git-path CHERRY_PICK_HEAD)"
test ! -e "$(git rev-parse --git-path MERGE_HEAD)"
git fetch origin fix/officeholder-direct-verification
test "$(git rev-parse FETCH_HEAD)" = "$FIX_SHA"
git show --stat --oneline "$FIX_SHA"
git cherry-pick "$FIX_SHA"
printf 'Record this Pi commit for rollback: '
git rev-parse HEAD
venv/bin/python manage.py test \
  assistant.tests_current_information assistant.tests_search_selection \
  assistant.tests_publication_metadata assistant.tests_online \
  assistant.tests_adaptive_online assistant.tests_streaming \
  assistant.tests_routing assistant.tests_automatic_offline_rag \
  assistant.tests_execution_policy assistant.tests_policy \
  knowledge_base.tests_online_sync knowledge_base.tests_retrieval_projection \
  knowledge_base.tests_rag --settings=config.test_settings --noinput
venv/bin/python manage.py check --settings=config.test_settings
venv/bin/python manage.py check
)
```

On cherry-pick conflicts, stop and run `git cherry-pick --abort`. Do not reset,
clean, stash, merge the whole branch, or overwrite knowledge. On validation failure,
leave running services untouched and investigate or revert the recorded Pi commit.
The cherry-picked Pi SHA will differ from the source SHA. Roll back on a clean
tracked worktree with `git revert <recorded-Pi-commit>`, then rerun checks.

Only after successful validation should the existing application processes load
the change through their normal controlled reload/restart procedure. No migration,
model download, audio change or unrelated service restart is required. Confirm
direct, cached, fallback and offline behavior from the existing response/log
diagnostics; any later Pi latency measurements must be reported separately.
