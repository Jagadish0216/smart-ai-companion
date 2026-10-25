#!/usr/bin/env bash
# Reviewed manual Pi maintenance supervisor. Run as root with DEPLOY_USER set;
# git, tests and benchmark always run as that original non-root user.
set -euo pipefail
: "${REVIEWED_COMMIT:?Set the reviewed full SHA}"
: "${DEPLOY_USER:?Set the existing Pi checkout owner}"
[[ $EUID == 0 && "$DEPLOY_USER" != root ]] || { echo 'Use the documented sudo supervisor'; exit 1; }
[[ "$REVIEWED_COMMIT" =~ ^[0-9a-f]{40}$ ]]
id "$DEPLOY_USER" >/dev/null
cd /home/jagadish/apps/smart-ai-companion
as_user() { runuser -u "$DEPLOY_USER" -- "$@"; }
python_test() {
  # Do not load the appliance's demo.env into the regression process. This
  # explicit profile supplies deterministic audio settings and an in-memory DB.
  as_user venv/bin/python manage.py test --settings=config.test_settings "$@"
}
patch_already_applied() {
  # A Pi cherry-pick has a different SHA; recognize stable patch equivalence too.
  if as_user git merge-base --is-ancestor "$REVIEWED_COMMIT" HEAD; then
    return 0
  fi
  [[ "$(as_user git cherry HEAD "$REVIEWED_COMMIT" "${REVIEWED_COMMIT}^")" == "- $REVIEWED_COMMIT" ]]
}
python_user() {
  # Match the existing voice unit's EnvironmentFile order without sourcing shell
  # code, printing credentials, editing settings or creating root-owned DB files.
  as_user venv/bin/python -c '
import os,sys
from pathlib import Path
from dotenv import dotenv_values
for name in (".env", "/etc/smart-ai-companion/demo.env"):
    if Path(name).is_file():
        os.environ.update({k:v for k,v in dotenv_values(name,interpolate=False).items() if v is not None})
os.execv(sys.executable,[sys.executable,*sys.argv[1:]])
' "$@"
}
[[ "$(as_user git branch --show-current)" == feature/adaptive-online-retrieval ]] || { echo 'Unexpected branch'; exit 1; }
[[ -z "$(as_user git status --porcelain --untracked-files=no)" ]] || { echo 'Tracked changes exist; nothing discarded'; exit 1; }
# Untracked knowledge and assets are allowed; no reset, stash, clean or migration.
as_user venv/bin/python - "$REVIEWED_COMMIT" <<'PY'
import subprocess, sys
sha=sys.argv[1]
allowed={'assistant/test_support.py','assistant/tests_voice_stream.py',
         'assistant/tests_pi_deployment_isolation.py','config/test_settings.py',
         'docs/pi-deployment-test-isolation.md','scripts/deploy_benchmark_timeouts.sh'}
if len(subprocess.check_output(['git','rev-list','--parents','-n','1',sha],text=True).split())!=2:
    raise SystemExit('Expected one non-merge fix commit')
paths=set(subprocess.check_output(['git','diff-tree','--no-commit-id','--name-only','-r',sha],text=True).splitlines())
if not paths or not paths<=allowed:raise SystemExit('Unexpected changed paths')
PY
apply_fix=true
if patch_already_applied; then
  apply_fix=false
  echo 'Reviewed fix already applied; reuse it and rerun validation/quick comparison without another cherry-pick.'
  [[ "$(as_user git rev-parse HEAD^)" == 53fca5636879d21d6ba8ea4d69182e6ab4af8c28 ]] || { echo 'Additional deployment history exists; stop for review'; exit 1; }
else
  [[ "$(as_user git rev-parse HEAD)" == 53fca5636879d21d6ba8ea4d69182e6ab4af8c28 ]] || { echo 'Unexpected baseline; stop for review'; exit 1; }
fi
systemctl is-active --quiet smart-ai-companion.service
systemctl is-active --quiet smart-ai-companion-voice.service
signature() {
as_user venv/bin/python - <<'PY'
import hashlib,pathlib
h=hashlib.sha256()
for root in map(pathlib.Path,('knowledge','demo.env','.env','/etc/smart-ai-companion/demo.env')):
    for p in [root]+(sorted(root.rglob('*')) if root.is_dir() else []):
        if p.exists() or p.is_symlink():
            s=p.lstat();h.update(str((str(p),s.st_size,s.st_mtime_ns)).encode())
print(h.hexdigest())
PY
}
before=$(signature)
if [[ "$apply_fix" == true ]]; then
  as_user git cherry-pick "$REVIEWED_COMMIT"
fi
tag=pi-test-isolation-${REVIEWED_COMMIT:0:12}
if as_user git rev-parse --verify --quiet "refs/tags/$tag" >/dev/null; then
  [[ "$(as_user git rev-parse "$tag^{commit}")" == "$(as_user git rev-parse HEAD)" ]] || { echo 'Rollback tag points elsewhere; stop for review'; exit 1; }
else
  as_user git tag "$tag" HEAD
fi
printf 'Applied: %s\nRollback tag: %s\n' "$(as_user git rev-parse HEAD)" "$tag"
restore_services() {
  status=$?
  trap - EXIT INT TERM
  # This supervisor already holds privileges: restoration cannot be blocked by
  # an expired sudo timestamp after a slow benchmark or failed Ollama cleanup.
  if ! timeout 120s systemctl start smart-ai-companion.service smart-ai-companion-voice.service ||
     ! timeout 10s systemctl is-active --quiet smart-ai-companion.service ||
     ! timeout 10s systemctl is-active --quiet smart-ai-companion-voice.service; then
    echo 'SERVICE RESTORATION FAILED: inspect systemctl status and journalctl locally.' >&2
    [[ $status != 0 ]] || status=1
  fi
  exit "$status"
}
trap restore_services EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Voice must release its existing microphone lock before voice-related tests.
timeout 120s systemctl stop smart-ai-companion-voice.service
python_test \
 assistant.tests_pi_deployment_isolation \
 assistant.tests_benchmark_timeouts assistant.tests_model_source_reliability \
 assistant.tests_voice assistant.tests_voice_loop assistant.tests_voice_stream \
 assistant.tests_push_to_talk assistant.tests_hands_free assistant.tests_interruption \
 assistant.tests_wakeword assistant.tests_display assistant.tests_devices assistant.tests_offline_models \
 assistant.tests_intelligence_reliability assistant.tests_search_selection assistant.tests_publication_metadata \
 assistant.tests_adaptive_online assistant.tests_online assistant.tests_routing assistant.tests_streaming \
 assistant.tests_execution_policy system.tests_resource_manager knowledge_base.tests_rag \
 knowledge_base.tests_online_sync conversations.tests_memory assistant.tests_policy \
 assistant.tests_short_answer_thermal assistant.tests_single_model assistant.tests --noinput
as_user venv/bin/python manage.py check --settings=config.test_settings
python_user manage.py check
as_user git diff --check
[[ "$(signature)" == "$before" ]] || { echo 'Protected assets changed; stop'; exit 1; }
timeout 120s systemctl stop smart-ai-companion.service
logdir=$(as_user mktemp -d /tmp/smart-companion-quick.XXXXXX)
printf 'Incremental benchmark report: %s/quick.jsonl\n' "$logdir"
# Eighteen requests only. Never start the full 84-request benchmark automatically.
python_user -u manage.py benchmark_models --quick --output "$logdir/quick.jsonl"
timeout 120s systemctl start smart-ai-companion.service smart-ai-companion-voice.service
systemctl is-active --quiet smart-ai-companion.service
systemctl is-active --quiet smart-ai-companion-voice.service
python_user manage.py verify_online_sources
[[ "$(signature)" == "$before" ]] || { echo 'Protected assets changed; stop'; exit 1; }
as_user venv/bin/python - "$logdir/quick.jsonl" <<'PY'
import json,sys
rows=[json.loads(line) for line in open(sys.argv[1])]
summary=next(r for r in reversed(rows) if r.get('type')=='summary')
for m in summary['models']:
    print(f"{m['model']}: warm {m['warm_p50_ms']}ms; TTFT {m['warm_ttft_p50_ms']}ms; "
          f"{m['warm_tokens_per_sec']} tokens/s; factual checks {m['rubric_passes']}/{m['samples']}")
print('Quick candidate for manual review:',summary['candidate_for_manual_review'] or 'none passed all checks')
print('Default model unchanged. Review saved answers before choosing a model.')
PY
printf 'Report: %s/quick.jsonl\nRollback: git revert --no-edit %s && venv/bin/python manage.py check && sudo systemctl restart smart-ai-companion.service smart-ai-companion-voice.service\n' "$logdir" "$tag"
# EXIT trap idempotently confirms service restoration on successful exit too.
