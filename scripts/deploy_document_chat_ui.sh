#!/usr/bin/env bash
# Run manually on the Pi. Supply the reviewed branch HEAD as a full SHA.
set -euo pipefail
cd "${COMPANION_ROOT:-/home/jagadish/apps/smart-ai-companion}"
: "${REVIEWED_COMMIT:?Supply the full reviewed branch commit SHA}"
[[ "$REVIEWED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || exit 1
baseline=ab63b2e44c723fe96fec06f208e8b3746c585fee
[[ "$(git rev-parse HEAD)" == "$baseline" ]] || { echo 'Unexpected Pi baseline; no changes made.'; exit 1; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo 'Tracked edits exist; preserve and review them.'; exit 1; }
[[ -x venv/bin/python ]] || { echo 'Expected existing venv'; exit 1; }
systemctl is-active --quiet smart-ai-companion.service
git fetch origin feature/assistant-document-chat-ui
[[ "$(git rev-parse FETCH_HEAD)" == "$REVIEWED_COMMIT" ]] || { echo 'Branch HEAD differs from reviewed commit'; exit 1; }
# Only the one reviewed non-merge commit is applied, never the development branch history.
venv/bin/python - "$REVIEWED_COMMIT" <<'PY'
import subprocess, sys
sha = sys.argv[1]
parents = subprocess.check_output(['git','rev-list','--parents','-n','1',sha], text=True).split()
if len(parents) != 2:
    raise SystemExit('Expected one non-merge release commit')
allowed = {
 'assistant/serializers.py', 'assistant/services.py', 'assistant/views.py', 'assistant/tests_document_chat_ui.py',
 'conversations/models.py', 'conversations/migrations/0002_message_source_metadata.py',
 'knowledge_base/models.py', 'knowledge_base/retrieval.py', 'knowledge_base/services.py',
 'knowledge_base/migrations/0005_knowledgechunk_page_number.py',
 'static/css/assistant.css', 'static/css/login.css', 'static/js/assistant-documents.js',
 'static/js/assistant-ui.js', 'static/js/login.js', 'templates/dashboard/assistant.html',
 'templates/registration/demo_login.html', 'scripts/deploy_document_chat_ui.sh',
 'scripts/accept_document_chat_ui.py', 'scripts/preview_document_chat_ui.py',
 'scripts/test_document_chat_browser.cjs', 'docs/assistant-document-chat-ui.md',
}
paths = subprocess.check_output(['git','diff-tree','--no-commit-id','--name-only','-r',sha], text=True).splitlines()
if not paths or any(p not in allowed and not p.startswith('docs/screenshots/assistant-document-chat-ui/') for p in paths):
    raise SystemExit('Unexpected release paths; stop for review')
patch = subprocess.check_output(['git','diff',sha+'^',sha,'--binary'])
# Check compatibility before touching code or database. Never reset or clean local assets.
subprocess.run(['git','apply','--check'], input=patch, check=True)
PY
# Snapshot mutable user assets by metadata. The database is backed up separately.
asset_signature() {
venv/bin/python - <<'PY'
import hashlib, pathlib
from django.conf import settings
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
h = hashlib.sha256()
roots = {pathlib.Path('knowledge'), pathlib.Path('.env'), pathlib.Path('demo.env'), pathlib.Path(settings.MEDIA_ROOT)}
for root in sorted(roots):
    for p in [root] + (sorted(root.rglob('*')) if root.is_dir() else []):
        if p.exists():
            s=p.stat(); h.update(str((str(p),s.st_size,s.st_mtime_ns)).encode())
print(h.hexdigest())
PY
}
before=$(asset_signature)
backup_dir="${HOME}/smart-companion-backups/document-chat-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"
# SQLite's backup API gives a consistent snapshot, including concurrent WAL writes.
venv/bin/python - "$backup_dir" <<'PY'
import os, pathlib, sqlite3, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
from django.conf import settings
config = settings.DATABASES['default']
if config['ENGINE'] != 'django.db.backends.sqlite3':
    raise SystemExit('This deployment requires the existing SQLite configuration')
source = sqlite3.connect(pathlib.Path(config['NAME']).resolve().as_uri()+'?mode=ro', uri=True)
with sqlite3.connect(str(pathlib.Path(sys.argv[1])/'database.sqlite3')) as target:
    source.backup(target)
source.close()
PY
git cherry-pick "$REVIEWED_COMMIT"
deployed=$(git rev-parse HEAD)
web_stopped=false
rollback_failure() {
    result=$?
    if [[ "$result" != 0 ]]; then
        echo 'Deployment failed; reverting the release code. Additive fields, if applied, remain safe.'
        git revert --no-edit "$deployed" || { echo 'Code rollback needs manual review'; exit "$result"; }
        if [[ "$web_stopped" == true ]]; then sudo systemctl start smart-ai-companion.service; fi
    fi
    exit "$result"
}
trap rollback_failure EXIT
# Refuse unrelated pending migrations. These two additive fields are required for real page citations/history.
venv/bin/python - <<'PY'
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
executor=MigrationExecutor(connection)
plan=executor.migration_plan(executor.loader.graph.leaf_nodes())
expected={('knowledge_base','0005_knowledgechunk_page_number'),('conversations','0002_message_source_metadata')}
if {(m.app_label,m.name) for m,backwards in plan} != expected or any(backwards for m,backwards in plan):
    raise SystemExit('Unexpected migration plan; code applied but services not restarted. Review before proceeding.')
PY
# Run only mocked tests in an in-memory test DB. No model pulls or hardware capture.
venv/bin/python manage.py test assistant.tests_document_chat_ui knowledge_base.tests_documents assistant.tests_streaming assistant.tests_browser_voice conversations.tests_memory --settings=config.test_settings --noinput
venv/bin/python manage.py check
venv/bin/python manage.py makemigrations --check --dry-run
sudo systemctl stop smart-ai-companion.service
web_stopped=true
venv/bin/python manage.py migrate conversations 0002 --noinput
venv/bin/python manage.py migrate knowledge_base 0005 --noinput
venv/bin/python manage.py migrate --check
venv/bin/python manage.py collectstatic --noinput
[[ "$(asset_signature)" == "$before" ]] || { echo 'Protected assets changed; stop for review'; exit 1; }
sudo systemctl start smart-ai-companion.service
systemctl is-active --quiet smart-ai-companion.service
trap - EXIT
printf 'Deployed SHA: %s\nDatabase backup: %s\nVoice service kept running; no document reindex or model/config changes.\n' "$(git rev-parse HEAD)" "$backup_dir"
printf 'Rollback code with git revert %s and restart web only. Additive DB fields may remain.\n' "$(git rev-parse HEAD)"
