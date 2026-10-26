# Assistant document chat and login release

Branch: `feature/assistant-document-chat-ui`. Development parent: `3074360`.
The Pi baseline supplied for deployment is `ab63b2e44c723fe96fec06f208e8b3746c585fee`.
That object is unavailable in this local checkout; compatibility with its file contents is not asserted.

## Changes

- Main Assistant uploads PDF/TXT/Markdown using the existing staff-only document API, shows upload progress and synchronous indexing state/errors, lists stored documents, and supports multiple selections and removable chips.
- Scope is saved per conversation in browser session storage. New chat resets it; existing conversation selection survives reload in the same browser session. Deselection returns to existing automatic routing. Scope is not synchronized across browsers/devices.
- Both chat transports validate and forward `document_ids` and `knowledge_mode`. Browser voice transcribes into the existing composer and shares that scope; hardware voice omits the new optional arguments and retains its configured behavior.
- Application default leaves existing routing/settings intact. Online Preferred uses existing online-first/fallback routing. Offline Only prevents online transports and identifies saved evidence for current questions. Online Only requires online retrieval and skips the local verified-evidence cache. Explicit document selection takes priority in every mode.
- Reuses the existing lexical retriever and resource-limited `AssistantService`; selected IDs filter candidates in SQL before top-k scoring. No second RAG implementation or model is introduced.
- PDF extraction retains page boundaries and chunks each page separately. Nullable page metadata means old indexed PDFs have no invented page numbers. An explicit reindex in Memory is required to obtain page citations for previously indexed PDFs; deployment does not reindex them.
- Source cards show real filenames/page numbers, safe HTTP(S) links, verification dates and saved/offline status when backend metadata supplies them. Citation metadata is saved on completed messages and restored with history. Existing message rows remain valid.
- Login uses existing Django authentication, CSRF, sessions and safe redirects, with dashboard tokens, responsive dark styling, labeled fields, accessible failure messages and a show/hide password control.

## Validation (Windows, 26 October 2026)

- `venv/Scripts/python.exe scripts/accept_document_chat_ui.py`: **486 tests passed**, Django checks passed, no missing migrations, JavaScript syntax checks passed. All tests use `config.test_settings`, isolated test databases and mocked inference/transports. Hardware voice regression tests use synthetic audio.
- `venv/Scripts/python.exe scripts/accept_document_chat_ui.py --browser-only`: real Chrome/Playwright acceptance against a synthetic local server. Exercises PDF upload/index/select, grounded mocked answers and real page citations, follow-up, multiple documents, reload/history, deselection, source modes, new chat, mocked browser microphone/STT, citation injection/unsafe links, login password control, and 390/768/1440px layouts. Screenshots are in `docs/screenshots/assistant-document-chat-ui/`.
- Bash deployment syntax and `git diff --check` passed.
- Additional broad probing found the existing failure `assistant.tests_single_model.ExplicitRagIntegrationTests.test_normalized_topic_retrieves_lexical_match_and_uses_qwen_once`: it expects generic edge-computing questions to bypass automatic RAG. The same assertion fails against a clean archive of parent `3074360` (13 tests, 1 failure). The consolidated suite covers current automatic RAG behavior plus `tests_rag_v2`; it does not include that conflicting legacy module. This is a pre-existing test inconsistency, not a green result for the entire repository suite.

Browser prerequisites: Node and Playwright; optionally set `CHROME_PATH` to installed Chrome, and `NODE_PATH` if Playwright is provided by a bundled runtime. `--browser` runs both Python and browser acceptance. The fixture server creates a temporary SQLite database, synthetic administrator and temporary upload directory; it never reads or copies production data. Port 8017 must be free.

## Pi deployment

Review the release commit and copy its full SHA from the final delivery. From the repository on the Pi:

```bash
REVIEWED_COMMIT=<full-reviewed-sha> bash scripts/deploy_document_chat_ui.sh
```

The script itself must be obtained from the reviewed commit before deployment (for example, `git fetch origin feature/assistant-document-chat-ui`, then `git show <full-reviewed-sha>:scripts/deploy_document_chat_ui.sh > /tmp/deploy_document_chat_ui.sh`, and execute that path with `REVIEWED_COMMIT` set). Do not switch the Pi to the development branch before running it.

The script verifies the exact supplied baseline, clean tracked files, reviewed branch HEAD, one non-merge commit, an allowlist of release paths, and patch applicability. It permits and preserves untracked knowledge. It creates a consistent SQLite backup under `~/smart-companion-backups/`, applies only the reviewed commit, checks that exactly the two required additive migrations are pending, runs isolated mocked acceptance tests, and stops/starts only `smart-ai-companion.service`. The hardware voice service continues running with its unchanged default path. No model pulls, environment edits, document reindexing, database replacement, cleanup/reset or user/password changes occur.

Required migrations: nullable `KnowledgeChunk.page_number` and nullable `Message.source_metadata` with empty application defaults. The nullable message field lets the already-running hardware voice process continue inserting messages with its older model definition. Unrelated pending migrations cause a stop. Failure after applying the release triggers a code revert; additive columns can remain, and an interrupted web service is started after the revert. The backup is retained for operator-controlled recovery; it is never automatically restored over newer conversations.

Unverified: execution on the actual Pi baseline, live Qwen grounding quality/latency, live external retrieval/provider behavior, physical microphone/speaker and hardware voice runtime. Browser STT/TTS and model responses were mocked. This release prepares deployment; it does not deploy to the Pi.
