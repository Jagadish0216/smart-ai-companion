# Testing and evidence

Run tests in a virtual environment containing the required dependencies:

```sh
python manage.py test --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
python scripts/repository_audit.py --output .audit-local
python -m pip check
```

This main baseline has no `config.test_settings` module. Do not copy commands
from later feature branches without checking that their settings exist here.
Tests use mocked model/audio/hardware paths and a temporary Django database.
Keep tests away from the live Pi's environment and source; a desktop run is not
permission to operate deployed hardware.

Coverage includes chat/streaming, local model request budgets, voice capture,
interruption, Piper lifecycle, display, MQTT, resources, memory, document ingestion,
online retrieval, network setup and dashboard behavior. Existing test modules and
fixtures are preserved. See [actual verification results](REPOSITORY_CLEANUP_VERIFICATION.md)
for baseline versus final outcomes and environment limitations.

The audit script enumerates tracked files, hashes exact duplicates, finds textual
references, parses Python and produces value-redacted secret candidates. Its
reference heuristic does not prove a file is unused; imports, templates, firmware,
CAD includes and independent entry points need human review. The baseline reports
are retained under [repository-audit](repository-audit).

`scripts/browser_voice_check.cjs` and `scripts/dashboard_browser_check.cjs` provide
additional browser checks but require a browser/tool runtime. Existing screenshot
acceptance evidence is historical; no fresh physical microphone, mobile browser,
live-Pi or model-quality result is claimed in this cleanup. Native audio, ARM64
installation, firmware compilation and physical printing need their own environments.

Current advisory scanning is separate from `pip check`, which only checks installed
dependency consistency. No dependency is certified vulnerability-free by a passing
import, unit test or metadata check.

## Hosted software CI

[Software CI](../.github/workflows/ci.yml) runs on PRs targeting main, main pushes
and manual dispatch. Ubuntu 24.04 / Python 3.11, immutable official Action SHAs,
read-only repository permission, no persisted checkout credential and a 20-minute
timeout keep it focused. It updates pip/setuptools, installs full requirements,
checks dependency consistency, Django, migrations, Python syntax and maintained
local Markdown targets, then runs the complete Django-discovered suite.

`config.ci_settings` selects mock engines, disables optional retrieval, display,
device control and wake operation, and uses an in-memory database. Tests can
override settings with simulated providers. Dependency acquisition needs network
access; weights, microphone, Pi, secrets, services and deployment are not part of
CI. Python 3.11 Linux supplies openWakeWord's declared TFLite dependency normally.
The separate Linux >=3.12 ONNX/no-deps workaround is not the CI profile and can
still fail pip metadata consistency for the unused TFLite requirement; it is not
fully validated by CI.

Use `DJANGO_SETTINGS_MODULE=config.ci_settings` for the same checks locally.
`python scripts/check_repository.py` checks maintained file links and Python
syntax without imports or network calls. Historical generated-distribution links,
external URLs and anchors are outside its scope. The new export/privacy and
appliance-profile tests run in normal discovery without blanket exclusions.

Checksum-verified actionlint 1.7.12 checks workflow syntax. Only an actual
completed GitHub run establishes hosted success; see the final-polish verification
addendum for the run URL/status and any Linux-specific corrections.
