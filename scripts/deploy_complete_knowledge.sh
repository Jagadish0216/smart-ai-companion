#!/usr/bin/env bash
# Run the reviewed copy of this script from outside the Pi checkout.
set -Eeuo pipefail
ROOT="${COMPANION_ROOT:-$HOME/apps/smart-ai-companion}"
cd "$ROOT"
BASELINE=d2cbfa954e6717a7cdd792962d737651ce5b0525
SOURCE_BASELINE=30743607fdcae68ef81991f1e2610eec957879bc
: "${RELEASE_SHA:?Supply the reviewed exact 40-character release SHA}"
[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo 'FAIL: invalid release SHA'; exit 1; }
[[ "$(uname -m)" == aarch64 && -r /proc/device-tree/model ]] || { echo 'FAIL: Raspberry Pi runtime required'; exit 1; }
grep -q 'Raspberry Pi 5' /proc/device-tree/model
grep -q 'VERSION_ID="13"' /etc/os-release
[[ "$(git rev-parse HEAD)" == "$BASELINE" ]] || { echo 'FAIL: unexpected Pi baseline'; exit 1; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo 'FAIL: tracked changes; preserve and review'; exit 1; }
git cat-file -e "$RELEASE_SHA^{commit}"
[[ "$(git rev-parse "$RELEASE_SHA^")" == "$SOURCE_BASELINE" ]] || { echo 'FAIL: expected one integrated release commit'; exit 1; }
[[ "$(git branch --show-current)" == feature/adaptive-online-retrieval ]] || { echo 'FAIL: unexpected Pi branch'; exit 1; }
# Pi history is cherry-picked. Verify touched baseline blobs rather than ancestry.
python3 - "$SOURCE_BASELINE" "$RELEASE_SHA" <<'PYCODE'
import subprocess, sys
base, release = sys.argv[1:]
paths = subprocess.check_output(['git','diff','--name-only','-z',base,release]).split(b'\0')
for path in filter(None,paths):
    name=path.decode()
    if name.split('/')[0] in {'.env','db.sqlite3','media','knowledge','models','venv'}:
        raise SystemExit('Release touches protected storage')
    def blob(ref):
        p=subprocess.run(['git','rev-parse',ref+':'+name],capture_output=True)
        return p.stdout.strip() if p.returncode==0 else None
    if blob(base)!=blob('HEAD'):
        raise SystemExit('Unexpected Pi baseline content: '+name)
PYCODE
PY="$ROOT/venv/bin/python"
[[ -x "$PY" && -f db.sqlite3 ]]
exec 9>"$ROOT/.knowledge-maintenance.lock"
flock -n 9 || { echo 'FAIL: maintenance already running'; exit 1; }
BACKUP="$(dirname "$ROOT")/knowledge-backup-$(date -u +%Y%m%dT%H%M%SZ)"
STAGING=$(mktemp -d)
git archive "$RELEASE_SHA" scripts/knowledge_backup.py | tar -x -C "$STAGING"
maintenance=0
deployed=0
recover() {
    rc=$?
    if (( rc != 0 )); then
        echo "FAIL: deployment stopped (exit $rc); backup: $BACKUP"
        if (( maintenance )); then
            sudo systemctl stop smart-ai-companion-knowledge.timer || true
            sudo systemctl stop smart-ai-companion-voice.service smart-ai-companion.service || true
            if [[ -f "$BACKUP/backup.json" ]]; then
                "$PY" "$STAGING/scripts/knowledge_backup.py" restore "$ROOT" "$BACKUP" || { echo 'FAIL: automatic data recovery failed; services remain stopped'; exit 1; }
            fi
            if (( deployed )); then
                git revert --no-edit "$deployed_sha" || { echo 'FAIL: code rollback failed; services remain stopped'; exit 1; }
            fi
            sudo systemctl start smart-ai-companion.service smart-ai-companion-voice.service || true
        fi
    fi
    # The staging directory contains only the exported helper; retain on failure.
    if (( rc == 0 )); then rm -rf -- "$STAGING"; fi
    exit "$rc"
}
trap recover EXIT
maintenance=1
sudo systemctl stop smart-ai-companion-voice.service smart-ai-companion.service
"$PY" "$STAGING/scripts/knowledge_backup.py" backup "$ROOT" "$BACKUP"
printf '%s\n' "$BASELINE" > "$BACKUP/baseline.sha"
printf '%s\n' "$RELEASE_SHA" > "$BACKUP/release.sha"
if ! git cherry-pick "$RELEASE_SHA"; then
    git cherry-pick --abort
    exit 1
fi
deployed=1
deployed_sha=$(git rev-parse HEAD)
# Verify that every release path now has the reviewed bytes.
"$PY" - "$SOURCE_BASELINE" "$RELEASE_SHA" <<'PYCODE'
import subprocess,sys
paths=subprocess.check_output(['git','diff','--name-only','-z',sys.argv[1],sys.argv[2]]).split(b'\0')
for path in filter(None,paths):
    result=subprocess.run(['git','diff','--quiet',sys.argv[2],'HEAD','--',path.decode()])
    if result.returncode: raise SystemExit('Deployed release file mismatch')
PYCODE
printf '%s\n' "$deployed_sha" > "$BACKUP/deployed.sha"
"$PY" manage.py check
"$PY" manage.py makemigrations --check --dry-run
# Tests use the explicit in-memory DB and synthetic transports, never production SQLite.
timeout 1200 "$PY" manage.py test --settings=config.test_settings --noinput
"$PY" manage.py migrate --noinput
"$PY" manage.py bootstrap_knowledge
"$PY" manage.py collectstatic --noinput
"$PY" - "$ROOT" "$(id -un)" "$STAGING/knowledge.service" <<'PYCODE'
from pathlib import Path
import re,sys
root,user,target=sys.argv[1:]
if not re.fullmatch(r'/[A-Za-z0-9_./-]+', root) or not re.fullmatch(r'[A-Za-z0-9_-]+', user):
    raise SystemExit('Unsupported systemd path/user encoding')
text=Path('deployment/systemd/smart-ai-companion-knowledge.service').read_text()
Path(target).write_text(text.replace('@ROOT@',root).replace('@USER@',user))
PYCODE
sudo install -m 644 "$STAGING/knowledge.service" /etc/systemd/system/smart-ai-companion-knowledge.service
sudo install -m 644 deployment/systemd/smart-ai-companion-knowledge.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable smart-ai-companion-knowledge.timer
sudo systemctl restart smart-ai-companion.service smart-ai-companion-voice.service
sleep 5
systemctl is-active --quiet smart-ai-companion.service
systemctl is-active --quiet smart-ai-companion-voice.service
bash scripts/verify_complete_knowledge.sh --runtime-only
sudo systemctl start smart-ai-companion-knowledge.timer
maintenance=0
echo "PASS: reviewed release $RELEASE_SHA; Pi commit $deployed_sha; backup $BACKUP"
echo 'Hardware microphone/speaker acceptance and broad dated publisher coverage still require Pi-side review.'
