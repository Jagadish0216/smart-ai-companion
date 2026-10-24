# Bounded search evidence selection

## Root cause

The old `_search()` sanitized up to 100 raw results and ranked them, then applied
`max_results` (normally four). Enrichment could only inspect those four, with two
attempts. Publication availability was favored without distinguishing stale dates,
and metadata-free relevant articles further down the search results were lost.
An official landing page and a dead article could therefore crowd out useful
articles. HTTP 200 from SearXNG never established article availability or freshness.

The Pi report confirms empty publication fields and one 404. No Pi access was
performed for this fix. Its reported baseline `32c0d56` is not available in this
workstation's fetched history, so cherry-pick compatibility must be checked on
the Pi rather than claimed here.

## Algorithm

1. Sanitize/deduplicate up to 100 raw results. Canonicalize tracking parameters
   and fragments for article identity. Keep a ranked pool of at most **20** through
   verification, instead of truncating to four first.
2. Rank topical overlap, informative/non-promotional snippets, article paths,
   admissible recent dates, then primary-publisher hints. Stale/future/conflicting
   dates do not receive a date-availability advantage over promising undated
   articles. The Raspberry Pi official domain is a limited tie-break for Raspberry
   Pi topics, not a universal trust score or freshness proof. Original position is
   only a stable tie-break. URL date strings never establish publication time.
3. Skip known failed URLs and generic landing/category/search paths. News landing
   pages cannot pass news evidence admission even if metadata contains a date.
   Visit the first promising article per hostname before a second from that host.
4. Allow at most **four article attempts**, serially, each capped at **650 ms**,
   sharing the unchanged **two-second** enrichment budget and existing request
   deadline. Fast 404s allow another candidate within that budget. Do not fetch
   already dated results; stop when enough admissible evidence is present.
5. Treat 404, 403, 429, transport failures, HTML error/challenge titles and HTML
   refresh pages as failed verification. Never follow HTML/script redirects.
   HTTP redirects retain the previous per-hop SSRF/public-IP checks, DNS pinning,
   HTTPS verification, downgrade rejection and maximum two redirects.
6. Re-rank the verified candidate pool. Prefer evidence passing the unchanged
   freshness admission, then select up to configured `max_results` (default four).
   Use a soft cap of two results per normalized hostname, backfilling only if
   other hosts cannot fill the selection. Unknown/undated candidates can remain
   as retrieval results for diagnostics, but cannot support current claims.

Existing source/date provenance and prompt-injection rules remain. Publication,
modification and retrieval dates remain separate. News still requires evidence
within two days; an October article older than that must be refused for latest
news even when the URL or title matches the query.

`accessibility` in source metadata is explicit:

- `unchecked`: search metadata alone, or verification skipped due to contention,
  budget, landing-page status or host backoff. Never a claim of a checked URL.
- `available`: successful HTML fetch/metadata parsing, including cached checks.
  This does not establish recent publication; undated HTML still fails admission.
- `unavailable`: fetch/validation failed. These results cannot pass admission and
  are excluded from final selection while the negative cache is valid.

Accessibility reflects the last check/cache state, not a guarantee that a URL
remains reachable. Search-dated sources avoid unnecessary page requests and
therefore retain `unchecked` unless this pipeline actually checks the page.

## Bounds and caches

No timeout increase, new dependency, model, driver or environment setting.
Configured provider endpoints must return JSON directly: provider redirects now
fail closed instead of letting urllib multiply network calls and timeouts.
Retrieval remains capped at `min(ONLINE_TIMEOUT_SECONDS, 8)` seconds, with the
existing SearXNG per-operation bounds. The default numeric loopback endpoint
avoids the existing provider-host OS DNS timeout limitation. Enrichment DNS waits
and sockets use the remaining per-attempt deadline; body size stays 128 KiB.

For non-empty search: at most two SearXNG calls and four article attempts. Each
article can make one initial request plus two safe redirects: **14 HTTP requests
is the conservative maximum**, all under shared deadlines. The usual successful
path uses one search plus zero to four article requests. Empty search can instead
use one optional health canary, at most three SearXNG calls, with no article fetch.
These are network budgets; bounded parsing/ranking and unchanged Ollama generation
add to end-to-end latency. No real Pi p50/p95 improvement is claimed.

Search result TTLs remain 15/30/120 seconds by freshness category, five seconds
for healthy empty results, and ten seconds for provider failure cooldown.
Metadata cache remains 64 records: five minutes for publication metadata,
15 seconds for failed/undated checks. A bounded 32-host backoff cache suppresses
new fetches for 60 seconds after 403/429; no authentication, protection bypass or
retry is attempted. Cache freshness is checked again during evidence admission.

The existing nonblocking thread slot and OS lock prevent queued/excess parallel
enrichment among workers sharing a temp namespace. PrivateTmp namespaces and
process caches remain separate. The candidate pool is bounded to roughly 18 KiB
of snippet text (20 x 900 characters); model evidence remains at most four
sources/3600 characters. No changes to thermal/memory inference protections,
Ollama, STT, PTT GPIO17, ESP32 UART/TFT, microphone or amplifier configuration.

## Tests

Final workstation validation: **515 tests passed in 9.730 seconds**; Django system
checks and Git whitespace checks passed. This is the complete relevant module
set below, not a claim that every test elsewhere in the repository was run.

Changed files: `assistant/online/base.py`, `assistant/online/enrichment.py`,
`assistant/online/grounding.py`, `assistant/online/searxng.py`,
`assistant/online/selection.py`, `assistant/views.py`,
`assistant/tests_adaptive_online.py`, `assistant/tests_online.py`,
`assistant/tests_publication_metadata.py`, `assistant/tests_search_selection.py`,
`assistant/tests_streaming.py`, `knowledge_base/tests_online_sync.py`, and this guide.
Existing transport tests now mock the explicit no-redirect search transport.

Run the complete relevant modules, using a writable temp directory on Windows:

```powershell
$env:TMP = Join-Path (Get-Location).Path '.test-tmp'
$env:TEMP = $env:TMP
venv/Scripts/python.exe manage.py test assistant.tests_search_selection assistant.tests_publication_metadata assistant.tests_adaptive_online assistant.tests_online assistant.tests_routing assistant.tests_streaming assistant.tests_execution_policy system.tests_resource_manager knowledge_base.tests_online_sync assistant.tests_policy assistant.tests_push_to_talk assistant.tests_voice_loop assistant.tests_devices assistant.tests_short_answer_thermal assistant.tests_voice_stream assistant.tests_display assistant.tests_offline_models --noinput
venv/Scripts/python.exe manage.py check
git diff --check
```

Tests cover 28-result input, useful articles below position four, landing pages,
tracking duplicates, promotions, source diversity, dead links, HTML 200 errors,
undated/stale/future evidence, rate backoff, shared verification budgets,
concurrent/cache behavior and existing SSRF/redirect, provider-outage, streaming,
voice, offline-model, display/device and resource-policy regressions. Networks
and hardware are mocked; physical Pi operation and latency need deployment checks.

## Manual Pi update from reported `32c0d56`

Do not deploy automatically. On the Pi during review, first confirm its baseline
and stop if the checkout has any changes. Preserve local deployment assets;
do not reset, stash, pull unrelated history, or replace configuration.

```sh
cd /home/jagadish/apps/smart-ai-companion
git status --short              # stop if dirty
git rev-parse --short HEAD      # confirm expected 32c0d56
git fetch origin feature/search-evidence-ranking
git show --stat FETCH_HEAD
git cherry-pick FETCH_HEAD      # applies only the focused follow-up commit
venv/bin/python manage.py test assistant.tests_search_selection assistant.tests_publication_metadata assistant.tests_adaptive_online assistant.tests_online assistant.tests_routing assistant.tests_streaming assistant.tests_execution_policy system.tests_resource_manager --noinput
venv/bin/python manage.py check
git rev-parse HEAD              # record deployed cherry-pick SHA for rollback
```

If conflicts occur, stop for review; `git cherry-pick --abort` cancels the
in-progress application. The earlier publication-metadata implementation must be
present. No migrations, model changes or new settings are required. Only after
tests pass and a service restart is separately authorized:

```sh
sudo systemctl restart smart-ai-companion.service smart-ai-companion-voice.service
sudo systemctl --no-pager status smart-ai-companion.service smart-ai-companion-voice.service
```

Verify the example query, source date/provenance/accessibility fields and admitted
counts. Verify 404 and genuinely undated results never become recent evidence;
repeat a query to exercise caches. Check local questions, PTT/TFT/audio/device
operation and resource status without changing hardware settings. Compare cold,
cached and failed retrieval timings separately on the Pi.

Rollback on a clean checkout:

```sh
git revert <recorded-deployed-cherry-pick-sha>
venv/bin/python manage.py check
sudo systemctl restart smart-ai-companion.service smart-ai-companion-voice.service
```

Keep knowledge, databases, demo.env, Ollama models and existing deployment changes.
