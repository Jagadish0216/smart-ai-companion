# Integrated knowledge release

Development base: `30743607fdcae68ef81991f1e2610eec957879bc`.
Required Pi base: `d2cbfa954e6717a7cdd792962d737651ce5b0525`, on
`feature/adaptive-online-retrieval`. The Pi history contains cherry-picks;
the installer checks changed-file blobs against the development base and
cherry-picks the single reviewed release. It reports both source and Pi SHAs.

## Answering and provenance

The shared `AssistantService` handles text, NDJSON streaming and voice callers.
Eligible general factual questions prefer online retrieval when the existing
resource policy permits it. Explicit document selection overrides network mode.
Private/local-document requests and social/action requests keep their existing
routes. No new connectivity probes or model downloads are introduced.

`knowledge_mode` accepts `online_preferred`, `offline_only`, or `online_only`.
`document_ids: [id]` selects files; `[]` means all local/manual references and
uploads; omission means automatic sources. These optional fields are accepted
by both chat endpoints. The Knowledge page has the controls and document chat.
`KNOWLEDGE_MODE` supplies the shared default for hardware voice. Spoken explicit
offline/online requests override that default. Browser preference is stored
locally and sent on each Knowledge-page question.

Verified evidence is archived transactionally beside the normalized online
Document. EvidenceVersion preserves text, hash, source metadata, publication,
retrieval, verification and expiry times. EvidenceQuery associates normalized
questions and subsequent historical uses with a conversation. Matching supports
lexical paraphrases and recent-user-question pronoun resolution; it is not a
general semantic embedding engine. Fresh variations still pass the existing
current-evidence admission rules. Verification timestamps are never advanced
by offline reads.

States distinguish verified/fresh, historical, superseded and unverified data.
Replacing a source retains prior versions; TTL expiry never deletes versions.
Offline reuse quotes dated source evidence rather than asserting it is current.
Known comparable conflicts are flagged, and source reports remain separate.
General contradiction detection is conservative, not a universal fact checker.
Unverified legacy snippets remain in the original store but are not promoted
to verified archival facts. Migration 0006 copies only checksummed, explicitly
verified old snapshots. Missing/corrupt old files remain untouched for review.

The archive defaults to a 128 MiB content budget and stops admitting new versions
when full instead of deleting history. The limit can be configured using
`KNOWLEDGE_ARCHIVE_MAX_BYTES` (maximum 1 GiB). Individual evidence is bounded to
6,000 characters; historical retrieval examines at most 500 matching candidates
and returns at most three excerpts. The existing lexical document retrieval,
Ollama model, generation budgets and thermal protections remain in use.

## Documents and the reference library

Supported uploads remain UTF-8 TXT/Markdown and PDFs with a text layer, at most
10 MiB/200 PDF pages. OCR is not provided. PDF chunks now retain page numbers.
Reindex older PDFs to add those references. Existing staff-only mutation,
session authentication, CSRF, file validation and safe filename handling remain.

The versioned `knowledge_library/manifest.json` includes substantive, unmodified
reference text, content/license hashes, attribution, upstream version, source
URL and acquisition timestamp. Currently bundled: Python tutorial chapters,
socket/TLS/SQLite references, MicroPython ESP32 and GPIO, and Linux networking
and kernel self-protection documentation, plus scikit-learn introduction and
clustering references. The bundled licenses apply to their
respective source files; source formatting is intentionally retained.

Run `python manage.py bootstrap_knowledge` or use the staff-only dashboard action
to initialize/resume indexing. Matching hashes skip reindexing. Each source has
independent status and error reporting, and previous good documents survive
failed replacements. `python scripts/acquire_reference_bundle.py` can restore
missing pinned materials, with a 120-second total budget, individual request
timeouts, no redirects and one-second request spacing. It does not crawl or
silently change pinned source versions. Future reference updates require a
reviewed manifest/version/hash change.

**Coverage limitation:** the manifest explicitly lists unpopulated source areas:
general science, geography/history, India/civic facts, space, general academic
material, entertainment and dated developments through 2026. Some candidate
publishers require source-specific redistribution review. An acquisition timestamp in 2026 does not establish
publication or factual currency through 2026. This is a useful populated
technical starter library, not the requested comprehensive 2026 encyclopedia.

## Bounded refresh

The installer adds a lightweight systemd oneshot and hourly timer, with jitter,
25% CPU quota, 256 MiB memory limit, low scheduling priority and a 90-second
deadline. Each pass refreshes at most three saved queries. It defers when the
resource report is unknown/constrained or a recent voice state is active.
`KNOWLEDGE_REFRESH_SECONDS` bounds the cadence to 5 minutes–24 hours. The common
maintenance lock prevents overlapping scheduled passes/deployment. No Ollama
inference occurs. Failed retrieval preserves old evidence and timestamps.
The dashboard exposes last successful refresh, next eligibility and failures.

## Deployment

Fetch the release branch, obtain its reviewed full SHA, and export the script
without switching the Pi checkout first:

```bash
cd ~/apps/smart-ai-companion
git fetch origin feature/complete-knowledge-system
RELEASE_SHA=<reviewed-full-source-SHA>
git show "$RELEASE_SHA:scripts/deploy_complete_knowledge.sh" > /tmp/deploy_complete_knowledge.sh
RELEASE_SHA="$RELEASE_SHA" bash /tmp/deploy_complete_knowledge.sh
```

The script refuses an unexpected Pi/model/OS baseline, branch, changed tracked
files, changed release-path baseline content, protected-storage changes or
insufficient space. It stops application writers, makes a SQLite backup using
SQLite's backup API, checks integrity, and copies media, knowledge and `.env`
with SHA-256 checksums before migrations. Models and environment are preserved.
It runs the full isolated regression suite once, applies migrations, indexes
the real bundle, collects assets, installs the bounded timer, restarts services
and runs the read-only live acceptance checks. Any critical failure is nonzero
and attempts data/code rollback. No `reset --hard`, `clean`, or production tests.

The standalone consolidated check is:

```bash
bash scripts/verify_complete_knowledge.sh
```

Synthetic tests use temporary SQLite/media, mock retrieval/model transports and
a separate-process restart probe. Live diagnostics use actual configured search
and source verification, report library/service/resource status and latency,
and perform no production conversation/evidence writes or inference. A live
publisher failure is separate from synthetic success and fails runtime acceptance.

## Rollback after a successful deployment

Use the backup directory printed by the installer. Stop writers and preserve
any later production changes before rollback. The helper saves the displaced
database as a separate `knowledge-rollback-recovery-*.sqlite3`; later user rows
remain recoverable there and require reconciliation if you return to the old
schema. Existing/new upload files and `.env` are never overwritten or deleted.

```bash
sudo systemctl stop smart-ai-companion-knowledge.timer smart-ai-companion-voice.service smart-ai-companion.service
venv/bin/python scripts/knowledge_backup.py restore "$PWD" "$BACKUP"
git revert --no-edit "$(cat "$BACKUP/deployed.sha")"
venv/bin/python manage.py collectstatic --noinput
sudo systemctl start smart-ai-companion.service smart-ai-companion-voice.service
```

Stop if any rollback command fails. Do not restart against a mismatched schema.
The backup/restore path is tested with a real schema alteration and new uploads.
Actual Pi systemd, microphone, speaker, thermal and publisher acceptance must
still run on the device. This Windows development environment cannot establish
those hardware results. Browser preview access was attempted but the browser
tool could not reach the local preview; API/template checks remain automated.
