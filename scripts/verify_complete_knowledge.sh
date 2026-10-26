#!/usr/bin/env bash
set -Eeuo pipefail
cd "${COMPANION_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PY="${PYTHON:-venv/bin/python}"
LOG=$(mktemp -d /tmp/complete-knowledge-check.XXXXXX)
result=0
if [[ "${1:-}" != --runtime-only ]]; then
    if timeout 180 "$PY" manage.py test knowledge_base.tests_complete_knowledge --settings=config.test_settings --noinput >"$LOG/synthetic.log" 2>&1; then
        echo 'PASS: synthetic persistence/offline/paraphrase/freshness/document/library integration'
    else echo "FAIL: synthetic integration ($LOG/synthetic.log)"; result=1; fi
else
    echo 'Synthetic integration: not rerun (deployment runs full regression separately).'
fi
if timeout 45 "$PY" manage.py verify_complete_knowledge >"$LOG/runtime.json" 2>&1; then
    echo 'PASS: read-only runtime, indexed library, services and live retrieval'
else echo "FAIL: runtime or live publisher coverage ($LOG/runtime.json)"; result=1; fi
cat "$LOG/runtime.json"
echo "Consolidated report: $LOG; exit=$result. Synthetic success is not live publisher coverage."
exit "$result"
