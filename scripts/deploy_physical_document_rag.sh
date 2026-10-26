#!/usr/bin/env bash
# Run the reviewed script from outside the live checkout. No model/config changes.
set -Eeuo pipefail
ROOT="${COMPANION_ROOT:-$HOME/apps/smart-ai-companion}"
cd "$ROOT"
BASELINE=6f7221c66702bcf251f1d44c1cf87daaa665ce0c
SOURCE_BASELINE=9c31fe478f769041f026bc6e15878e542bba1b53
: "${RELEASE_SHA:?Supply the exact reviewed 40-character compatibility release SHA}"
[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]] || exit 1
[[ "$(git rev-parse HEAD)" == "$BASELINE" ]] || { echo 'Unexpected Pi baseline; nothing changed'; exit 1; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo 'Tracked edits exist; preserve and review'; exit 1; }
PY="$ROOT/venv/bin/python"
[[ -x "$PY" ]]
git fetch origin codex/persistent-physical-voice-rag
[[ "$(git rev-parse FETCH_HEAD)" == "$RELEASE_SHA" ]] || { echo 'Branch HEAD differs from reviewed SHA'; exit 1; }
"$PY" - "$SOURCE_BASELINE" "$RELEASE_SHA" <<'PYCODE'
import subprocess, sys
base, release = sys.argv[1:]
parents = subprocess.check_output(['git','rev-list','--parents','-n','1',release], text=True).split()
if parents != [release, base]:
    raise SystemExit('Expected one non-merge commit based exactly on integrated source 9c31fe4')
allowed = {'docs/physical-voice-rag-before-compact-prompt.json','docs/physical-voice-rag-simulated.json', 'assistant/tests_physical_document_rag.py', 'knowledge_base/document_names.py', 'knowledge_base/models.py', 'scripts/benchmark_physical_rag.py', 'scripts/accept_physical_document_rag.py', 'assistant/voice/rag_metrics.py', 'assistant/voice/interruption.py', 'scripts/deploy_physical_document_rag.sh', 'config/test_settings.py', 'scripts/accept_document_chat_ui.py', 'assistant/views.py', 'assistant/latency_trace.py', 'knowledge_base/migrations/0007_document_normalized_title_and_more.py', 'docs/physical-voice-rag-baseline.json', 'conversations/models.py', 'assistant/voice/sentence_answer.py', 'scripts/voice_loop.py', 'docs/voice-latency-trace.schema.json', 'knowledge_base/retrieval.py', 'assistant/tests_document_chat_ui.py', '.gitattributes', 'conversations/migrations/0003_hardwaredocumentscope.py', 'docs/physical-voice-rag.md', 'docs/physical-voice-rag-before-scope-write.json', 'assistant/voice/document_scope.py', 'assistant/services.py'}
paths = subprocess.check_output(['git','diff','--name-only','-z',base,release]).decode().split('\0')
for name in filter(None, paths):
    if name not in allowed:
        raise SystemExit('Unexpected release path: '+name)
    def blob(ref):
        result = subprocess.run(['git','rev-parse',ref+':'+name],capture_output=True)
        return result.stdout.strip() if result.returncode == 0 else None
    if blob(base) != blob('HEAD'):
        raise SystemExit('Pi baseline differs on release path: '+name)
# Existing knowledge migrations must match the reviewed integrated source exactly.
for name in subprocess.check_output(['git','ls-tree','-r','--name-only',base,'knowledge_base/migrations']).decode().splitlines():
    if blob(base) != blob('HEAD'):
        raise SystemExit('Unexpected knowledge migration bytes: '+name)
patch = subprocess.check_output(['git','diff',base,release,'--binary'])
subprocess.run(['git','apply','--check'],input=patch,check=True)
PYCODE
# Match the existing tested backup helper's storage contract; fail closed on custom layouts.
"$PY" - "$ROOT" <<'PYCODE'
import os, sys
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
from django.conf import settings
root = Path(sys.argv[1]).resolve()
db = settings.DATABASES['default']
if db['ENGINE'] != 'django.db.backends.sqlite3' or Path(db['NAME']).resolve() != root/'db.sqlite3':
    raise SystemExit('Backup requires the existing repository SQLite path; no changes made')
if Path(settings.MEDIA_ROOT).resolve() != root/'media':
    raise SystemExit('Custom media location requires separately reviewed backup; no changes made')
PYCODE
exec 9>"$ROOT/.knowledge-maintenance.lock"
flock -n 9 || { echo 'Knowledge maintenance is active; retry after it completes'; exit 1; }
STAGING=$(mktemp -d /tmp/physical-document-rag.XXXXXX)
git archive "$RELEASE_SHA" | tar -x -C "$STAGING"
# Validate exported release bytes with disposable DB/media before pausing any service.
(cd "$STAGING" && timeout 1200 "$PY" manage.py test assistant.tests_physical_document_rag assistant.tests_document_chat_ui knowledge_base.tests_complete_knowledge knowledge_base.tests_documents knowledge_base.tests_rag knowledge_base.tests_online_sync assistant.tests_streaming assistant.tests_browser_voice conversations.tests_memory --settings=config.test_settings --noinput)
(cd "$STAGING" && "$PY" manage.py check --settings=config.test_settings && "$PY" manage.py makemigrations --check --dry-run --settings=config.test_settings)
BACKUP="$(dirname "$ROOT")/physical-rag-backup-$(date -u +%Y%m%dT%H%M%SZ)"
services=(smart-ai-companion.service)
systemctl is-active --quiet smart-ai-companion.service
if systemctl is-active --quiet smart-ai-companion-voice.service; then services+=(smart-ai-companion-voice.service); fi
maintenance=0
deployed=0
recover() {
    rc=$?
    if (( rc != 0 )); then
        echo "Deployment failed; retained backup: $BACKUP; staging: $STAGING"
        if (( maintenance )); then
            sudo systemctl stop "${services[@]}" || { echo 'Cannot stop writers for rollback; manual review required'; exit "$rc"; }
        fi
        if (( deployed )); then
            # Older ORM models do not know the new scope foreign keys. Reset only
            # ephemeral scope links before reverting, so old document/history
            # deletion stays compatible. Never alter documents or evidence.
            "$PY" - "$ROOT/db.sqlite3" <<'PYROLLBACK'
import sqlite3, sys
from pathlib import Path
with sqlite3.connect(Path(sys.argv[1]).resolve().as_uri()+'?mode=rw',uri=True) as database:
    if database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversations_hardwaredocumentscope'").fetchone():
        database.execute("UPDATE conversations_hardwaredocumentscope SET document_id=NULL, conversation_id=NULL, document_indexed_at=NULL, pending_document_ids='[]', chunk_cursor=-1")
PYROLLBACK
            git revert --no-edit "$deployed_sha" || { echo 'Code rollback failed; services remain stopped'; exit "$rc"; }
            "$PY" manage.py collectstatic --noinput || { echo 'Static rollback needs review; services remain stopped'; exit "$rc"; }
        fi
        # Never overwrite SQLite or uploads on automatic rollback. The one nullable
        # column is backward compatible; conversations/evidence remain intact.
        if (( maintenance )); then sudo systemctl start "${services[@]}"; fi
    fi
    exit "$rc"
}
trap recover EXIT
maintenance=1
sudo systemctl stop "${services[@]}"
# The existing helper uses SQLite backup(), integrity_check, upload copies and SHA-256 manifests.
"$PY" "$STAGING/scripts/knowledge_backup.py" backup "$ROOT" "$BACKUP"
printf '%s\n' "${services[@]}" > "$BACKUP/active-services.txt"
printf '%s\n' "$BASELINE" > "$BACKUP/baseline.sha"
printf '%s\n' "$RELEASE_SHA" > "$BACKUP/release.sha"
if ! git cherry-pick "$RELEASE_SHA"; then git cherry-pick --abort; exit 1; fi
deployed=1
deployed_sha=$(git rev-parse HEAD)
printf '%s\n' "$deployed_sha" > "$BACKUP/deployed.sha"
"$PY" - <<'PYCODE'
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
executor = MigrationExecutor(connection)
plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
if {(m.app_label,m.name,backwards) for m,backwards in plan} != {('knowledge_base','0007_document_normalized_title_and_more',False),('conversations','0003_hardwaredocumentscope',False)}:
    raise SystemExit('Unexpected pending migrations; only document metadata and hardware scope migrations are permitted')
required = {('conversations','0002_message_source_metadata'),('knowledge_base','0005_knowledgechunk_page_number_evidenceversion_and_more'),('knowledge_base','0006_preserve_verified_snapshots')}
if not required <= set(executor.loader.applied_migrations):
    raise SystemExit('Integrated knowledge migrations are not already applied')
PYCODE
"$PY" manage.py migrate conversations 0003_hardwaredocumentscope --noinput
"$PY" manage.py migrate --check
"$PY" manage.py check
"$PY" manage.py collectstatic --noinput
"$PY" - "$ROOT" "$BACKUP" <<'PYCODE'
import json, sys
from pathlib import Path
from scripts.knowledge_backup import digest
root, backup = map(Path, sys.argv[1:])
manifest = json.loads((backup/'backup.json').read_text())
for name, expected in manifest['hashes'].items():
    if digest(backup/name) != expected:
        raise SystemExit('Backup integrity changed: '+name)
    if name != 'db.sqlite3' and digest(root/name) != expected:
        raise SystemExit('Protected asset changed: '+name)
PYCODE
sudo systemctl start "${services[@]}"
for service in "${services[@]}"; do systemctl is-active --quiet "$service"; done
maintenance=0
trap - EXIT
printf 'PASS: source release %s; Pi commit %s; backup %s\n' "$RELEASE_SHA" "$deployed_sha" "$BACKUP"
printf 'Only normalized metadata/backfill and hardware scope migrations applied; no reindex, refresh, model pulls or configuration changes. Existing refresh timer retained.\n'
printf 'Manual rollback: follow docs/physical-voice-rag.md; stop recorded services, clear scope links, revert %s, collect static files, restore services.\n' "$deployed_sha"
printf 'Keep additive nullable metadata columns and the unused scope table on rollback. Never replace the database over newer conversations.\n'
