#!/usr/bin/env bash
# Manual, reviewed Pi maintenance action. Never called by application services.
set -euo pipefail
cd /home/jagadish/apps/smart-ai-companion
: "${REVIEWED_COMMIT:?Set REVIEWED_COMMIT to the reviewed full 40-character SHA}"
[[ "$REVIEWED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid reviewed SHA'; exit 1; }
[[ "$(git rev-parse --short=7 HEAD)" == bf8a08a ]] || { echo 'Unexpected baseline; stop for review'; exit 1; }
[[ "$(git branch --show-current)" == feature/adaptive-online-retrieval ]] || { echo 'Unexpected deployment branch'; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo 'Preserve local changes before deployment; nothing was stashed or reset'; exit 1; }
BASELINE=$(git rev-parse HEAD)
knowledge_signature() {
  venv/bin/python - <<'PY'
import hashlib, pathlib
h=hashlib.sha256()
root=pathlib.Path('knowledge')
if root.exists():
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            raise SystemExit('Knowledge symlink requires manual review')
        if p.is_file():
            s=p.stat();h.update(str((str(p),s.st_size,s.st_mtime_ns)).encode())
print(h.hexdigest())
PY
}
KNOWLEDGE_BEFORE=$(knowledge_signature)
git fetch origin feature/intelligence-reliability
[[ "$(git rev-parse FETCH_HEAD)" == "$REVIEWED_COMMIT" ]] || { echo 'Remote head differs from reviewed commit'; exit 1; }
# Ensure only reviewed application/test/document/script paths are in this commit.
venv/bin/python - "$REVIEWED_COMMIT" <<'PY'
import re, subprocess, sys
sha=sys.argv[1]
parents=subprocess.check_output(['git','rev-list','--parents','-n','1',sha],text=True).split()
if len(parents)!=2:raise SystemExit('Expected one focused non-merge commit')
paths=subprocess.check_output(['git','diff-tree','--no-commit-id','--name-only','-r',sha],text=True).splitlines()
allowed=r'^(assistant/(routing.py|services.py|views.py|online/[^/]+\.py|tests[^/]*\.py|management/commands/verify_intelligence.py)|knowledge_base/(services.py|tests[^/]*\.py)|conversations/tests[^/]*\.py|scripts/deploy_intelligence_reliability.sh|docs/intelligence-reliability.md)$'
if not paths or any(not re.fullmatch(allowed,p) for p in paths):raise SystemExit('Unexpected changed path; manual review required')
PY
git cherry-pick "$REVIEWED_COMMIT"
DEPLOYED=$(git rev-parse HEAD)
printf 'Baseline: %s\nDeployed SHA (record for rollback): %s\n' "$BASELINE" "$DEPLOYED"
venv/bin/python manage.py test assistant.tests_intelligence_reliability assistant.tests_search_selection assistant.tests_publication_metadata assistant.tests_adaptive_online assistant.tests_online assistant.tests_routing assistant.tests_streaming assistant.tests_execution_policy system.tests_resource_manager knowledge_base.tests_rag knowledge_base.tests_online_sync conversations.tests_memory assistant.tests_policy assistant.tests_push_to_talk assistant.tests_voice_loop assistant.tests_devices assistant.tests_voice_stream assistant.tests_display assistant.tests_offline_models --noinput
venv/bin/python manage.py check
[[ "$(knowledge_signature)" == "$KNOWLEDGE_BEFORE" ]] || { echo 'Knowledge metadata changed: stop for review'; exit 1; }
# All checks above must pass before either service is restarted.
sudo systemctl restart smart-ai-companion.service smart-ai-companion-voice.service
systemctl is-active --quiet smart-ai-companion.service
systemctl is-active --quiet smart-ai-companion-voice.service
sudo systemctl --no-pager status smart-ai-companion.service smart-ai-companion-voice.service
venv/bin/python manage.py verify_intelligence --warm-voice
[[ "$(knowledge_signature)" == "$KNOWLEDGE_BEFORE" ]] || { echo 'Knowledge metadata changed during verification'; exit 1; }
printf 'Verification completed. Review answers and evidence; abstention is not proof of answer quality.\nRollback: git revert %s\n' "$DEPLOYED"
