#!/usr/bin/env bash
# Run the reviewed script from outside the live checkout. No model/config changes.
set -Eeuo pipefail
ROOT="${COMPANION_ROOT:-$HOME/apps/smart-ai-companion}"
cd "$ROOT"
BASELINE=ab63b2e44c723fe96fec06f208e8b3746c585fee
SOURCE_BASELINE=4df6dcb3366d991a53b4d35ec68917665f570973
: "${RELEASE_SHA:?Supply the exact reviewed 40-character compatibility release SHA}"
[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]] || exit 1
[[ "$(git rev-parse HEAD)" == "$BASELINE" ]] || { echo 'Unexpected Pi baseline; nothing changed'; exit 1; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo 'Tracked edits exist; preserve and review'; exit 1; }
PY="$ROOT/venv/bin/python"
[[ -x "$PY" ]]
git fetch origin codex/assistant-knowledge-ui-integration
[[ "$(git rev-parse FETCH_HEAD)" == "$RELEASE_SHA" ]] || { echo 'Branch HEAD differs from reviewed SHA'; exit 1; }
"$PY" - "$SOURCE_BASELINE" "$RELEASE_SHA" <<'PYCODE'
import subprocess, sys
base, release = sys.argv[1:]
parents = subprocess.check_output(['git','rev-list','--parents','-n','1',release], text=True).split()
if parents != [release, base]:
    raise SystemExit('Expected one non-merge commit based exactly on integrated source 4df6dcb')
allowed = {'assistant/services.py','assistant/views.py','assistant/tests_document_chat_ui.py',
 'conversations/models.py','conversations/migrations/0002_message_source_metadata.py',
 'static/css/assistant.css','static/css/login.css','static/js/assistant-documents.js',
 'static/js/assistant-ui.js','static/js/login.js','templates/dashboard/assistant.html',
 'templates/registration/demo_login.html','scripts/accept_document_chat_ui.py',
 'scripts/preview_document_chat_ui.py','scripts/test_document_chat_browser.cjs',
 'scripts/deploy_assistant_knowledge_ui.sh','docs/assistant-knowledge-ui-integration.md','.gitattributes'}
paths = subprocess.check_output(['git','diff','--name-only','-z',base,release]).decode().split('\0')
for name in filter(None, paths):
    if name not in allowed and not name.startswith('docs/screenshots/assistant-knowledge-ui-integration/'):
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
STAGING=$(mktemp -d /tmp/assistant-knowledge-ui.XXXXXX)
git archive "$RELEASE_SHA" | tar -x -C "$STAGING"
# Validate exported release bytes with disposable DB/media before pausing any service.
(cd "$STAGING" && timeout 1200 "$PY" manage.py test assistant.tests_document_chat_ui knowledge_base.tests_complete_knowledge knowledge_base.tests_documents knowledge_base.tests_rag knowledge_base.tests_online_sync assistant.tests_streaming assistant.tests_browser_voice conversations.tests_memory --settings=config.test_settings --noinput)
(cd "$STAGING" && "$PY" manage.py check --settings=config.test_settings && "$PY" manage.py makemigrations --check --dry-run --settings=config.test_settings)
BACKUP="$(dirname "$ROOT")/assistant-ui-backup-$(date -u +%Y%m%dT%H%M%SZ)"
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
if [(m.app_label,m.name,backwards) for m,backwards in plan] != [('conversations','0002_message_source_metadata',False)]:
    raise SystemExit('Unexpected pending migrations; only Message.source_metadata is permitted')
required = {('knowledge_base','0005_knowledgechunk_page_number_evidenceversion_and_more'),('knowledge_base','0006_preserve_verified_snapshots')}
if not required <= set(executor.loader.applied_migrations):
    raise SystemExit('Integrated knowledge migrations are not already applied')
PYCODE
"$PY" manage.py migrate conversations 0002_message_source_metadata --noinput
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
printf 'No knowledge migrations, reindex, refresh, model pulls or configuration changes. Existing refresh timer retained.\n'
printf 'Code rollback: git revert --no-edit %s; venv/bin/python manage.py collectstatic --noinput; restart web.\n' "$deployed_sha"
printf 'Keep the nullable metadata column on rollback. Never replace the database over newer conversations.\n'
