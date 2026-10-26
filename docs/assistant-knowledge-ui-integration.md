# Assistant UI compatibility integration

This is a compatibility port of UI release `cbceea2bfb42c7531bd4531e28c3d62352e202bb`, based directly on integrated knowledge source `4df6dcb3366d991a53b4d35ec68917665f570973`.

Branch: `codex/assistant-knowledge-ui-integration`.
Required Pi baseline: `ab63b2e44c723fe96fec06f208e8b3746c585fee` (the supplied cherry-picked equivalent of the source baseline).

## Reconciliation

- Ported the completed main Assistant upload, multiple-document selection/chips, per-conversation browser-session scope, source controls, safe citations/history and browser voice scope, along with the themed Django login. No visual redesign.
- Retained the newer `knowledge_base/retrieval.py`, `knowledge_base/services.py`, archive, pipeline, library, refresh commands, dashboard and API implementations byte-for-byte. Their existing scoped lexical retrieval and real PDF page indexing supply the UI; no second implementation was added.
- Retained both `knowledge_base/0005_knowledgechunk_page_number_evidenceversion_and_more` and `0006_preserve_verified_snapshots` unchanged. There is no short-name `0005_knowledgechunk_page_number` migration.
- The only new migration is `conversations/0002_message_source_metadata`, depending on `conversations/0001_initial`. It adds a nullable JSON field with an empty application default; existing messages and older voice-process inserts remain valid.
- Shared Assistant service changes only persist citation metadata and keep unrelated conversation/model claims outside explicitly selected document grounding. Existing online-first decisions, historical evidence reuse, automatic persistence, offline fallbacks and hardware defaults remain intact. Both existing chat transports retain their integrated request contract.
- Main Assistant omits `document_ids` when deselected. This is essential: in the integrated API, `document_ids: []` already means all local manual documents for the Memory dashboard. That existing meaning is preserved. Uploaded/manual sources are selectable; online snapshots retain automatic archive retrieval.
- Source cards retain actual page numbers, verification dates and `FRESH`/`HISTORICAL`/`SUPERSEDED` metadata. Text is rendered through DOM text nodes and links accept only HTTP(S) without embedded credentials.

## Validation

On Windows, using the existing Python environment and `config.test_settings`:

- Full integrated regression: **1,606 tests, OK (one existing skip)**. No test modules were excluded. The added migration transition test also passed separately.
- Final consolidated acceptance: **508 tests, OK**, including the schema transition test. Main Assistant and complete-knowledge acceptance covers: PDF upload/index, selection and document follow-ups, page citations, saved citation history, automatic-source restoration, online/offline modes, expired immutable evidence reuse through streaming, Memory all-local scope, library/dashboard, login/CSRF/redirects, and safe older voice inserts.
- Migration transition acceptance starts at the existing knowledge `0006` plus conversations `0001`, creates message/document/page/evidence/query fixtures, and verifies that the only pending migration is conversations `0002`. After applying it, all fixture data and page numbers remain unchanged.
- Real Chrome/Playwright acceptance passed against a temporary fixture server: document uploads/multiple selection, follow-up, reload/history, deselection, modes, new chat, mocked browser microphone/transcription/TTS, unsafe citation payloads, login show/hide controls, and desktop/tablet/mobile layouts. Screenshots: `docs/screenshots/assistant-knowledge-ui-integration/`.
- Django checks, migration drift checks, JavaScript syntax, Bash syntax and authored-file whitespace checks passed.

The first full run inside the Windows sandbox encountered temporary-file rename and temporary-Git permission failures in existing tests. The unchanged suite passed when rerun outside those restrictions, still using isolated test databases and mocked model/network transports.

Reproduce with the existing environment:

```bash
venv/bin/python scripts/accept_document_chat_ui.py --full
venv/bin/python scripts/accept_document_chat_ui.py --browser-only
```

Use `venv/Scripts/python.exe` on Windows. Browser checks require Node, Playwright and a free local port 8017; `NODE_PATH` and `CHROME_PATH` may select the installed tooling. The preview server creates synthetic credentials and temporary SQLite/media. It never opens or copies the live database.

## Deployment and rollback

Use only `scripts/deploy_assistant_knowledge_ui.sh` for this integration. Obtain its reviewed bytes outside the Pi checkout:

```bash
git fetch origin codex/assistant-knowledge-ui-integration
RELEASE_SHA=<exact-integrated-release-sha-from-delivery>
git show "$RELEASE_SHA:scripts/deploy_assistant_knowledge_ui.sh" > /tmp/deploy_assistant_knowledge_ui.sh
RELEASE_SHA="$RELEASE_SHA" bash /tmp/deploy_assistant_knowledge_ui.sh
```

The script requires the exact Pi baseline, a clean tracked tree, the exact reviewed branch HEAD and a single release commit with parent `4df6dcb`. It compares changed-file baseline blobs and all existing knowledge migration blobs with the Pi checkout, checks an allowlist, and dry-checks patch applicability before applying code.

It holds the existing knowledge-maintenance lock and validates an exported copy of the release against isolated databases before pausing services. During the bounded backup/schema window it pauses the web service and the voice service only if voice was already active, then restores exactly those services without changing their configuration. Existing refresh services/timers and bundled knowledge remain unchanged.

The existing tested `scripts/knowledge_backup.py` creates a real SQLite `backup()` snapshot with `integrity_check`, copies all `media/` and untracked `knowledge/` files plus the environment backup, and records SHA-256 checksums under a new sibling backup directory with restricted permissions. The script fails closed if SQLite/media use nonstandard paths or storage contains symlinks; it never silently omits custom storage. No `.env` content is edited or printed.

Only conversations `0002_message_source_metadata` may be pending; integrated knowledge migrations `0005`/`0006` must already be applied. The script migrates only that conversations target, collects static files, verifies backup/upload/environment checksums and starts the previously active services. It does not bootstrap/reindex documents, run knowledge refresh, pull models, modify Ollama, replace SQLite or alter voice configuration.

On failure after applying code, the script stops writers, reverts only its release commit, restores static files and starts the same services. The additive nullable column remains compatible. Automatic rollback never restores an older database over newer conversations or evidence and never deletes uploaded files. The consistent backup remains available for explicit operator-controlled disaster recovery. A code-revert failure leaves services stopped for review.

## Runtime limits

No Pi deployment was executed. The exact Pi baseline content/applicability is checked by the script on the device, not asserted from the desktop checkout. Live Qwen grounding/latency, external publisher/network behavior, physical audio and hardware voice acceptance remain unverified. Browser inference/audio transports were mocked; the persistent SQLite/offline pipeline and real bundled-library indexing were exercised synthetically.
