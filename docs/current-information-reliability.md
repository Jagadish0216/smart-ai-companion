# Current information reliability

The router recognizes chief ministers, current actor/film activity, regional and
OTT releases, AI developments and current affairs. Historical, definitional,
personal/project and explicit offline requests retain their routing priorities.
Stable factual questions still use the existing lexical retriever; its admission
threshold, scoring, SQL candidate query and model budgets are unchanged. Manual
retrieval still executes one SQL query. Selected saved online chunks additionally
carry synchronization and expiry labels; synchronization is not publication or
necessarily page verification.

## Evidence and request bounds

Officeholder evidence requires a fetched, HTTPS official current profile. The
explicitly scoped adapters currently cover Telangana's Chief Minister profile and
India's Prime Minister profile. Government news archives, old appointment reports,
search titles and snippets cannot establish the present officeholder. Profile
adapters recognize adjacent name/office labels and the current PM profile's
opening identification, including its `Know the PM` marker. A historical news
report with the same wording is not accepted. Other offices without a scoped
adapter abstain rather than borrow an old news claim.

Entertainment results require relevant claim-bearing text from a fetched page
and a recent publication date under the existing age rules. Project announcements,
rumours, denials, reported release schedules and reported completed releases are
labelled separately. A report is attributed as a report; it is not independent
confirmation that a scheduled release occurred. Same-film incompatible ISO release
dates and explicit incompatible officeholder claims cause abstention. This is
bounded pattern detection, not a comprehensive contradiction detector.

Publication metadata supports additional publisher meta names and common timestamp
formats. JSON-LD event `startDate`, modification dates, release catalog dates and
actual page verification times remain distinct from publication dates. Neither
retrieval time, a title's year, nor a future event date establishes publication.
Missing, stale, future-dated or conflicting publication dates remain insufficient
for current entertainment/news claims. Official current-state profiles are a
separate evidence category, not a relaxed news freshness threshold.

The live Telangana page exceeded the old 128 KiB cap. Responses now have a finite
256 KiB cap; visible text retained for metadata caching is at most 12,000 characters
per page and selected claim text at most 1,200. Titles, scripts, styles, navigation
elements and iframe bodies are excluded from claim extraction. No scripts execute.
Existing DNS/IP pinning, public-address checks, redirect validation, HTTPS
downgrade rejection, content-type checks, negative caching and 403/429 backoff stay
in force. Enrichment retains a shared two-second budget, four attempts, and the
existing cross-process nonblocking fetch lock. Scoped current queries get up to
1.25 seconds per fetch inside that budget. Search retains its existing maximum
eight-second deadline and bounded retries. Discovery uses intent-appropriate
alternatives; film queries no longer get a software `release notes` suffix.

## Cache and diagnostics

Successful verified snapshots reuse the existing SQLite online documents,
expiry fields and local synchronization files. Small provenance records preserve
actual publication, modification, event and verification timestamps across lexical
chunk boundaries. Reads are bounded to 16,000 bytes and checked against the stored
content hash. No schema migration is required. Legacy undated snippets are never
upgraded to verified pages. Unverified current claims and conflicting current
results are refused by synchronization.

Verified snapshot expiry is the smaller of the configured TTL and five minutes
after actual verification. Exact-query cache reads rerun evidence admission and
conflict checks and never extend expiry or verification time. Existing short
process search caching remains in place. Cached offline responses explicitly quote
previously verified evidence with its actual date and a `not a live check` label;
expired or unverifiable content is not offered as current. Damaged or unavailable
cache storage does not block an eligible online attempt.

Safe response/log diagnostics add `search_ms`, `enrichment_ms`,
`evidence_selection_ms`, `cache_ms` and `cache_hit` beside existing generation and
total/request timings. A cache hit records no new search/enrichment time; a SQLite
hit does not claim a new online attempt or fresh provider connectivity. Logs do
not add query text, article text, credentials or conversation content.

## Validation and deployment limits

Focused tests cover routing, real profile structures using fictional names,
dated and undated evidence, four regional film-release categories, rumour/denial
classification, conflicts, expiry, altered cached files, cache failures, request
bounds and both assistant transports. Existing retrieval, SSRF/redirect, provider,
streaming and resource-policy regression tests also run. Tests use synthetic
documents and temporary/in-memory storage, not production knowledge.
The final clean-export run passed 286 affected tests. Django checks passed under
normal and test settings, and Git whitespace checks passed. Unrelated local
working-tree edits were excluded from that clean-export validation.

Two read-only public profile checks from the development workstation fetched and
admitted the Telangana and India profiles without manufacturing publication dates
(329 ms and 156 ms in that run). These are not Pi latency measurements, SearXNG
end-to-end results or live entertainment verification. No Pi was accessed and no
model inference benchmark ran. Paywalls, bot challenges, unavailable engines,
changed markup, pages over the response cap, slow sources and missing publication
metadata can still require abstention. The strict office adapter list and exact
query cache matching intentionally favor correctness over coverage.

Deploy only this reviewed commit by cherry-picking onto Pi baseline
`bb6c0957736ed1852215bcf590539702f1b1e548`, with tracked changes clean. Untracked
knowledge is allowed. Fetch the fix branch, verify the full SHA, cherry-pick only
that SHA, run affected tests with `--settings=config.test_settings` and Django
checks. On conflicts stop and use `git cherry-pick --abort`; never reset, clean or
stash deployment work. There are no environment, model, token-limit, database
migration or service configuration changes. Long-lived services adopt code on
their next normal reload; this procedure does not restart them. Record the Pi's
cherry-picked commit and roll back with `git revert <recorded-Pi-commit>`.
