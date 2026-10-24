# Verified publication metadata

## Diagnosis

The deployed parser only consumed `publishedDate` / `published_date` and used
`datetime.fromisoformat`. SearXNG's `pubdate` field and RFC-style timestamps were
missed. Some search engines omit publication metadata entirely; this is valid
SearXNG behavior, not proof that a result is stale. The previous implementation
had no page-metadata verification path, so relevant undated hits were rejected.
The reported Pi output establishes missing parsed dates; without its raw JSON,
we cannot say which of its four results had omitted versus unsupported dates.

References: [SearXNG result fields](https://docs.searxng.org/dev/result_types/main/mainresult.html)
and [SearXNG date serialization](https://docs.searxng.org/_modules/searx/result_types/_base.html).

## Behavior

- Recognizes `publishedDate`, `pubdate`, `published_date`, `datePublished`, and
  `date_of_publication`. Accepts ISO 8601, RFC/email dates, SearXNG compact numeric
  offsets, and explicit month-name dates. Normalizes timezone-aware values to UTC;
  timezone-less values retain the previous UTC convention. Date-only values stay
  midnight, so they cannot manufacture intraday freshness. Relative phrases,
  numeric timestamps with ambiguous units and month/year-only values are refused.
- Conflicting publication days in one record/page are unverified. Same-day values
  of different precision choose the oldest value conservatively. Publication,
  modification and retrieval remain separate, with public publication provenance.
- Otherwise relevant undated results can use article publication meta tags,
  `time[itemprop=datePublished]`, or Article/NewsArticle/BlogPosting/TechArticle/Report
  JSON-LD `datePublished`. Modification metadata is never used for publication.
  Generic landing pages are not enriched, and JSON-LD explicitly naming another
  article URL is ignored.
  No scripts execute. No article body is added to model context.
- Incidental dates in snippets, URL dates and a year in a title do not prove
  publication. Explicit snippet `Published`/`Posted` ISO dates remain supported
  with provenance. Conflicting snippet publication dates abstain.
- Existing freshness thresholds, future-date rejection, topical checks, conflict
  abstention, prompt injection rules and Pi generation/resource budgets remain.
  Month/year query words are time constraints rather than relevance keywords;
  topical Raspberry/Pi matches therefore outrank generic date matches. Obvious
  promotional snippets are deprioritized, not enriched, and refused for grounding.

## Resource and network bounds

At most two pages, fetched serially, share a two-second enrichment sub-budget of
the existing total retrieval deadline (maximum eight seconds). Dated search
results require no page fetch. At most 128 KiB per page; only uncompressed HTML
or XHTML with HTTP 200 is parsed. Each operation is capped at one second and
remaining time; an absolute socket timer also interrupts slow response headers.
DNS lookup waits are bounded, with at most one outstanding resolver thread per
process; a late resolver cannot initiate a connection. Existing SearXNG endpoint
DNS/socket behavior is unchanged (the default endpoint is numeric loopback).

All resolved addresses must be public. Connections pin the validated IP, while
HTTPS certificate verification and SNI use the original hostname. Only HTTP(S)
and standard ports are allowed; credentials, local/private/reserved IPs and unsafe
URLs fail closed. Up to two redirects are allowed, each separately resolved and
validated before connection; HTTPS-to-HTTP downgrade is refused. No proxy,
authentication, cookies or captive-portal automation is used. HTTP sources lack
TLS authenticity; metadata remains a publisher assertion, not cryptographic
proof of the age or truth of each snippet claim.

One thread slot plus a nonblocking OS file lock serializes enrichment across
cooperating workers sharing a temp namespace. Separate service PrivateTmp
namespaces have separate locks. Contention skips enrichment rather than queues.
The process cache holds at most 64 URL records: publication metadata five minutes,
failed/undated metadata fifteen seconds. Cached timestamps are rechecked against
the unchanged freshness limits on every answer. Search cache/retries are unchanged.
No retries are added for article fetching; failure preserves undated status.
No page URLs/content, queries or exception details are added to diagnostics.

## Validation

Final workstation run: **505 tests passed in 33.158 seconds**. Django system
checks reported no issues; Git whitespace checks passed. Initial focused tests
also passed. All network/hardware behavior in these runs uses test doubles.

Exact changed files:

- `assistant/online/base.py`
- `assistant/online/dates.py`
- `assistant/online/enrichment.py`
- `assistant/online/grounding.py`
- `assistant/online/searxng.py`
- `assistant/tests_adaptive_online.py`
- `assistant/tests_online.py`
- `assistant/tests_publication_metadata.py`
- `assistant/views.py`
- `docs/publication-metadata-fix.md`

Factory and service orchestration were inspected and need no changes: enrichment
shares the retriever deadline and service evidence admission remains authoritative.

Use the workspace temp directory on Windows to avoid sandbox rename failures:

```powershell
$env:TMP = Join-Path (Get-Location).Path '.test-tmp'
$env:TEMP = $env:TMP
venv/Scripts/python.exe manage.py test assistant.tests_publication_metadata assistant.tests_adaptive_online assistant.tests_online assistant.tests_routing assistant.tests_streaming assistant.tests_execution_policy system.tests_resource_manager knowledge_base.tests_online_sync assistant.tests_policy assistant.tests_push_to_talk assistant.tests_voice_loop assistant.tests_devices assistant.tests_short_answer_thermal assistant.tests_voice_stream assistant.tests_display assistant.tests_offline_models --noinput
venv/Scripts/python.exe manage.py check
git diff --check
```

Tests mock networks and hardware. Real Pi publication extraction, physical PTT,
TFT, audio and p50/p95 timing are deployment verification tasks, not claims from
these tests. No Pi was accessed or modified for this change.

## Pi update and rollback (manual, after review)

This is a focused follow-up on the already deployed adaptive commit. Do not merge
the existing draft PR or pull unrelated branch history into the Pi. Record the
current deployment SHA, inspect status and stop if any local changes need
preservation. Replace `<fix-sha>` with this follow-up's reviewed commit:

```sh
cd /home/jagadish/apps/smart-ai-companion
git status --short
git rev-parse HEAD
git fetch origin codex/verified-publication-metadata
git cherry-pick <fix-sha>
venv/bin/python manage.py test assistant.tests_publication_metadata assistant.tests_adaptive_online assistant.tests_online assistant.tests_routing assistant.tests_streaming assistant.tests_execution_policy system.tests_resource_manager --noinput
venv/bin/python manage.py check
git rev-parse HEAD  # record the deployed cherry-pick SHA
```

If conflicts occur, stop and review; `git cherry-pick --abort` cancels only the
in-progress application. Never reset/stash/delete deployment changes. No new
configuration, migrations, models, dependencies or service unit edits are needed.
During an explicitly authorized maintenance window after checks pass:

```sh
sudo systemctl restart smart-ai-companion.service smart-ai-companion-voice.service
sudo systemctl --no-pager status smart-ai-companion.service smart-ai-companion-voice.service
```

Verify the example Raspberry Pi news query: source `published_at` should come
from search or article metadata, with `publication_source` identifying it.
Articles older than the existing two-day news limit must still be refused even
if they match October 2026. Test an undated page, repeated request/cache, provider
outage and local question. Verify PTT/TFT/audio/device behavior manually without
changing GPIO17, ESP32/UART, microphone or amplifier settings. Compare cold and
cached retrieval timings separately; no claim of measured Pi speedup is made.

Rollback on a clean checkout, using the recorded deployed cherry-pick SHA:

```sh
git revert <deployed-fix-sha>
venv/bin/python manage.py check
sudo systemctl restart smart-ai-companion.service smart-ai-companion-voice.service
```

Leave knowledge, databases, demo.env, Ollama models and application settings intact.
