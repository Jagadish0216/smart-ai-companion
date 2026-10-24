#!/usr/bin/env bash
# Manual Pi maintenance workflow. Never invoked by either running service.
set -euo pipefail
cd /home/jagadish/apps/smart-ai-companion
: "${REVIEWED_COMMIT:?Supply the reviewed full commit SHA}"
[[ "$REVIEWED_COMMIT" =~ ^[0-9a-f]{40}$ ]]
[[ "$(git rev-parse HEAD)" == 8d1672fa71b18c00247eba0e59c41f78aaa0784f ]] || { echo 'Unexpected baseline'; exit 1; }
[[ "$(git branch --show-current)" == feature/adaptive-online-retrieval ]] || { echo 'Unexpected branch'; exit 1; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo 'Tracked edits exist; preserve and review'; exit 1; }
# Untracked knowledge and other assets are allowed and never cleaned/stashed.
systemctl is-active --quiet smart-ai-companion.service
systemctl is-active --quiet smart-ai-companion-voice.service
venv/bin/python - "$REVIEWED_COMMIT" <<'PY'
import subprocess, sys
allowed = {
 'assistant/model_benchmark.py', 'assistant/technical_facts.py', 'assistant/services.py',
 'assistant/tests_model_source_reliability.py', 'assistant/tests_online.py',
 'assistant/online/base.py', 'assistant/online/searxng.py', 'assistant/online/enrichment.py',
 'assistant/management/commands/benchmark_models.py',
 'assistant/management/commands/verify_online_sources.py',
 'docs/model-and-source-reliability.md', 'scripts/deploy_model_source_reliability.sh',
}
sha=sys.argv[1]
if len(subprocess.check_output(['git','rev-list','--parents','-n','1',sha],text=True).split())!=2:
    raise SystemExit('Expected a single non-merge commit')
paths=set(subprocess.check_output(['git','diff-tree','--no-commit-id','--name-only','-r',sha],text=True).splitlines())
if not paths or not paths <= allowed: raise SystemExit('Unexpected commit paths; stop for review')
PY
asset_signature() {
venv/bin/python - <<'PY'
import hashlib, pathlib
h=hashlib.sha256()
paths=[pathlib.Path('knowledge'),pathlib.Path('demo.env'),pathlib.Path('.env')]
for root in paths:
    items=[root]+(sorted(root.rglob('*')) if root.is_dir() else [])
    for p in items:
        if p.exists() or p.is_symlink():
            s=p.lstat();h.update(str((str(p),s.st_size,s.st_mtime_ns)).encode())
print(h.hexdigest())
PY
}
before=$(asset_signature)
git cherry-pick "$REVIEWED_COMMIT"
deployed=$(git rev-parse HEAD)
rollback_tag=model-source-deployed-${REVIEWED_COMMIT:0:12}
git tag "$rollback_tag" "$deployed"
printf 'Deployed SHA: %s\nRollback tag: %s\n' "$deployed" "$rollback_tag"
venv/bin/python manage.py test \
 assistant.tests_model_source_reliability assistant.tests_voice assistant.tests_voice_loop \
 assistant.tests_voice_stream assistant.tests_push_to_talk assistant.tests_hands_free \
 assistant.tests_interruption assistant.tests_wakeword assistant.tests_display assistant.tests_devices \
 assistant.tests_offline_models assistant.tests_intelligence_reliability assistant.tests_search_selection \
 assistant.tests_publication_metadata assistant.tests_adaptive_online assistant.tests_online \
 assistant.tests_routing assistant.tests_streaming assistant.tests_execution_policy \
 system.tests_resource_manager knowledge_base.tests_rag knowledge_base.tests_online_sync \
 conversations.tests_memory assistant.tests_policy assistant.tests_short_answer_thermal \
 assistant.tests_single_model assistant.tests --noinput
venv/bin/python manage.py check
git diff --check
[[ "$(asset_signature)" == "$before" ]] || { echo 'Protected assets changed; stop'; exit 1; }
# Tests above use synthetic audio/GPIO. Never contend for the live microphone.
# Only now enter a bounded-model maintenance window; restore services on failure.
restore_services() { sudo systemctl start smart-ai-companion.service smart-ai-companion-voice.service; }
trap restore_services EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
sudo systemctl stop smart-ai-companion-voice.service smart-ai-companion.service
# The command checks service inactivity and takes the existing microphone lock.
# Avoid web/CLI requests during this maintenance window. No models are pulled.
logdir=$(mktemp -d /tmp/smart-companion-model-benchmark.XXXXXX)
venv/bin/python manage.py benchmark_models --repeats 1 | tee "$logdir/models.jsonl"
sudo systemctl restart smart-ai-companion.service smart-ai-companion-voice.service
systemctl is-active --quiet smart-ai-companion.service
systemctl is-active --quiet smart-ai-companion-voice.service
trap - EXIT
venv/bin/python manage.py verify_online_sources | tee "$logdir/sources.jsonl"
[[ "$(asset_signature)" == "$before" ]] || { echo 'Protected assets changed; stop'; exit 1; }
printf 'Results: %s\nNo permanent model setting changed. Inspect answer rubrics and source admissions.\n' "$logdir"
printf 'Rollback: git revert --no-edit %s && venv/bin/python manage.py check && sudo systemctl restart smart-ai-companion.service smart-ai-companion-voice.service\n' "$rollback_tag"
