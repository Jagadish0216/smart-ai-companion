# Persistent physical voice document RAG

Source parent: `9c31fe478f769041f026bc6e15878e542bba1b53`.
Exact required Pi baseline: `6f7221c66702bcf251f1d44c1cf87daaa665ce0c`.
Branch: `codex/persistent-physical-voice-rag`.

## Production path and persistence

The existing systemd unit runs `scripts/voice_loop.py --push-to-talk --responsive --voice-refinements`. The USB microphone capture/Whisper transcript now supplies a trusted hardware session key to the same `AssistantService.process_message_stream` used previously. Browser APIs do not accept/forward this key. No browser, session storage, manually provided document IDs, second document database or second model is involved.

`Document.normalized_title` is populated from the filename (Unicode normalization, case folding, word separators and extension removal). Existing filename, upload time, status, ID and chunks remain in their current models. Compound type/title and type/upload-time indexes support exact/range/date matching. The metadata backfill reads no files and does not reindex chunks. Online evidence versions, queries, reference library and earlier migrations remain intact.

The resolver recognizes named reports/notes/PDFs, common speech variations such as `Rasberry Pie` and `Agile nots`, and upload-date qualifiers. `Last week` means the previous calendar week in the application's configured timezone. Exact/title-prefix matching uses indexed comparisons; edit-distance fallback examines at most 128 metadata rows, with strict token support and a runner-up margin. Overflow/ambiguity prompts for an exact filename instead of choosing from a truncated list. At most three short choices are spoken; an ordinal or a filename can resolve the clarification. Missing, unindexed, empty and deleted documents never borrow unrelated evidence. Filename matching is deliberately conservative; semantic nicknames/acronyms outside these rules may require clarification.

A `HardwareDocumentScope` row stores the trusted session key, conversation/document pointers, process token, pending choices, indexing timestamp, continuation cursor and expiry. Idle scope expires after 1,800 seconds (Django setting `PHYSICAL_DOCUMENT_SCOPE_SECONDS`, bounded to 60..3,600). `clear the document selection`, `new conversation`, selecting another file, a clear topic change or a wholly unrelated question clears/replaces it. Index timestamp changes invalidate the continuation cursor. Different hardware keys and browser conversations do not share this scope.

A new hardware process gets a new process token. Old scope is not automatically reattached after restart/reboot; name the file again. Uploaded documents and their index remain discoverable indefinitely. Explicit follow-up language after expired scope asks for the filename rather than inventing an answer. This avoids binding an unrelated new conversation to an old document.

Selected questions use the existing filtered `LexicalRetriever`, including a bounded known plural-heading variant when needed. Summaries/continuation use a bounded sequential overview method in that same retriever and the existing `select_rag_context` budget. The cursor follows excerpts actually supplied and commits with delivered speech. `Continue` moves through indexed sections; it is not a claim of exhaustive whole-document summarization or a replay of ungenerated text. Named topical questions can retrieve any relevant chunk independently of the cursor. Ordinary current-information questions retain online-first routing and permanent evidence/offline fallback.

## Completion and latency behavior

Physical document answers retain invariant voice safety and the existing RAG evidence formatter, with compact spoken style instead of duplicate generic capability/history instructions. The unchanged Qwen model remains `qwen3.5:4b-q4_K_M`. The initial physical RAG answer is capped at the existing 64-token RAG budget, or a smaller configured/resource-policy limit. No token limit, resource protection or model residency setting is raised; no model downloads, fan or hardware changes are introduced.

A physical-RAG-only sentence gate emits complete sentences during generation. On `done_reason=length`, it discards an incomplete tail and offers continuation; if no complete answer exists, it says the response was cut short. Stream deltas, final response, stored answer and TTS agree. It does not run a second inference call to repair truncation. Complete short sentences may reach the existing bounded Piper/playback queue before model EOF. Existing generic/browser streaming remains unchanged.

The PTT interruption boundary defers citation metadata with the answer and commits both only after successful audio delivery. Interrupted/failed speech does not create a falsely delivered AI answer or advance its section cursor.

Timing is opt-in and content-free. Added spans distinguish named-document resolution, filtered SQLite retrieval and RAG context construction. Existing spans cover capture/VAD, normalization, Whisper CLI/server, model prompt evaluation/decode/first token, Piper/audio preparation and drain. Playback-launch, first PCM accepted and spoken completion markers distinguish application handoff from unmeasured acoustic onset. `assistant.voice.rag_metrics.summarize` reports missing measurements as null; stages overlap and must not be summed as an end-to-end duration. Requested output budget and supplied context characters are reported separately from backend output tokens.

## Measurements and validation

Desktop measurements were captured before changing performance behavior. `physical-voice-rag-baseline.json` uses the original source path with a real disposable SQLite database and mocked model. Its named request produced **zero document sources**, so its approximately 53.8 ms service median is not a valid speed comparator for the implemented grounded path.

The same grounded fixture before prompt compaction had **1,519 system characters**, versus **913** after compaction (39.9% fewer), retaining the same excerpt, safety instruction and output budget. These are character counts, not Qwen tokenizer/prefill measurements. The redundant pre-bind scope write was removed; the continuation cursor's delivery commit is intentional. Recorded intermediate and final simulated reports are included for audit. Windows timing quantization, file-backed SQLite commits, mocked inference and changing functionality prevent a Pi speedup claim. In particular, real Qwen/STT/TTS time, thermal/memory impact and the dominant Pi stage have **not** been established here.

Validation uses `config.test_settings`, disposable databases/media and mocked inference/network/audio. Coverage includes days-later uploads, an actual separate-process SQLite restart, transcription variation, ambiguity/ordinals/filenames, scope expiry/process reset/session isolation, topic changes, deletion/reindexing, empty/unindexed files, insufficient evidence, transcript-to-TTS, canonical complete-sentence streaming, PTT deferred citations/cursors, indexed query plans, bounded query/prompt budgets and separate timing stages. Existing browser Assistant, Memory, offline evidence, library, login and hardware voice regressions are included.

- Full integrated regression: **1,638 tests passed, one existing skip**.
- **31 physical-RAG focused tests** and existing Chrome/Playwright Assistant/login acceptance pass. Migration drift checks and Bash/Python syntax checks pass.
- No production data, `.env`, administrator account, Ollama installation, service unit or voice configuration was changed during testing.

Reproduce synthetic acceptance with `venv/bin/python scripts/accept_document_chat_ui.py --full`; add `--browser` for the optional local browser fixture (Node/Playwright required). `scripts/benchmark_physical_rag.py --physical` recreates the disposable measurement fixture. None of these synthetic results constitutes physical Pi acceptance.

## Safe Pi deployment

Only two new migrations are needed: `knowledge_base/0007_document_normalized_title_and_more` and `conversations/0003_hardwaredocumentscope`. The former adds nullable normalized metadata/indexes and backfills metadata; the latter adds the hardware session table. Existing knowledge `0005`/`0006` and conversations `0002` are required, not rewritten. Nullable additive columns keep code rollback compatible with older document/message inserts.

Obtain the reviewed script outside the checkout, using the exact SHA from delivery:

```bash
git fetch origin codex/persistent-physical-voice-rag
RELEASE_SHA=<exact-reviewed-release-sha>
git show "$RELEASE_SHA:scripts/deploy_physical_document_rag.sh" > /tmp/deploy_physical_document_rag.sh
RELEASE_SHA="$RELEASE_SHA" bash /tmp/deploy_physical_document_rag.sh
```

The script verifies exact Pi HEAD, exact source parent, reviewed branch HEAD, baseline blobs for changed files and all existing knowledge migrations, a release-path allowlist, clean tracked files and patch applicability. It validates an exported release against isolated tests before the service pause. It holds the existing knowledge-maintenance lock and uses the existing tested SQLite backup helper, including `backup()`, database integrity checks, upload/untracked-knowledge copies and checksums, with `.env` backed up but never changed or printed. Nonstandard storage paths and symlinks fail closed.

Only the two expected migrations may be pending. The script stops the web service and voice service only if voice was already active, applies the reviewed single commit and these migrations, collects static files, checks protected-asset hashes and restores exactly the previously active services. The active-service list, source/deployed SHA and consistent database/upload backup are retained together. No bootstrap, reindex, knowledge refresh, model pull or service configuration installation is performed.

Failure triggers code/static rollback while writers are stopped, then restores those services. Rollback first clears only the new ephemeral hardware-scope foreign-key links, so the older ORM can still delete documents/conversations. The additive columns/table then remain safe and unused by old code. Automatic rollback never replaces SQLite over newer conversations/evidence or deletes uploaded files. Backups are retained for explicit disaster recovery. A code rollback failure leaves services stopped for review.

For a manual rollback after successful deployment, use the retained backup path printed by the script. Clear only transient scope links while the new models are still loaded, before reverting code:

```bash
BACKUP=<retained-backup-directory>
mapfile -t services < "$BACKUP/active-services.txt"
sudo systemctl stop "${services[@]}"
venv/bin/python manage.py shell -c "from conversations.models import HardwareDocumentScope; HardwareDocumentScope.objects.update(document=None, conversation=None, document_indexed_at=None, pending_document_ids=[], chunk_cursor=-1)"
git revert --no-edit "$(cat "$BACKUP/deployed.sha")"
venv/bin/python manage.py collectstatic --noinput
sudo systemctl start "${services[@]}"
```

## Consolidated physical acceptance (must run on the device)

SSH authentication was unavailable from this desktop session. **No Pi deployment, live Qwen quality/latency, physical microphone/speaker acceptance or thermal improvement is claimed.**

After deployment, this single command exercises actual USB capture, configured Whisper, existing Qwen, Piper and speaker over a temporary consistent snapshot of production SQLite:

```bash
venv/bin/python scripts/accept_physical_document_rag.py --microphone --manage-voice-service --turns 2 --output /tmp/physical-rag-$(date +%s).json
```

The explicit service-management flag pauses/restores an already active voice service. Without it the tool refuses microphone contention. It uses the existing per-user microphone lock, reads the same `.env` and `/etc/smart-ai-companion/demo.env` overlay without changing either, warms/reuses its Piper provider and matches responsive/refinement behavior. Capture is the configured timed/VAD microphone cycle rather than a GPIO hold; GPIO timing still requires normal button acceptance. Wait through the printed warm-up, then say a named document question and a natural follow-up.

The tool requires the actual local Qwen/Whisper/Piper settings, disables online retrieval/persistence, migrates only its private copied database, uses temporary media, and writes a new report only under the system temporary directory. It never writes production SQLite, documents, scope, history or evidence. The JSON includes stage timings, actual output budget/backend counters when reported, document/page citations, temperature, available memory and throttle flags before/after. It restores the voice service even if the test/report fails. Reports exclude transcript/source excerpt text. Human listening and checking the answer against the cited PDF are still required; successful playback and citations alone do not establish factual quality or acoustic onset latency.
