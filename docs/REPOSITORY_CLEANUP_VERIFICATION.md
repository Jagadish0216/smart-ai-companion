# Repository cleanup verification

Executed 1 November 2026 on Windows, Python 3.10. Commands run from the isolated
cleanup worktree unless noted. No SSH connection or hardware operation occurred.

## Baseline and test environment

| Command | Actual outcome |
|---|---|
| `git status --short`, `git branch -avv`, `git log`, `git ls-files` | Found dirty feature checkout and preserved it; selected isolated main baseline. |
| `git fetch origin`, `git ls-remote origin refs/heads/codex/pi-response-control-20261031` | Retrieved release refs; main remained f59da97; pinned release tip 2e9b9e9. |
| Connected GitHub repository metadata and open-issue search | Public repository, default main, push permission available; zero open results at inspection. |
| `python manage.py test --settings=config.test_settings --noinput` | FAILED before discovery: that settings module does not exist on main. Not used again. |
| `python manage.py test --noinput` | 982 discovered; FAILED with missing pypdf module and Windows temp rename permission error; one skip. Global interpreter was the wrong dependency environment. |
| `..\venv\Scripts\python.exe manage.py test --noinput` in sandbox | 1,002 tests, one error (wake-model temporary rename permission), one skip. |
| Same command outside sandbox restrictions, before dependency changes | **1,002 tests, 1,001 passed, one skipped, zero errors/failures**, 43.301 seconds. |
| Same command after documentation/hygiene changes, before dependency updates | **1,002 tests, 1,001 passed, one skipped**, 40.207 seconds. |

No application source fix was needed for the temporary rename: the code already
closes the file before replacement. The unrestricted baseline distinguishes an
environmental filesystem denial from a code regression. The skipped native Unix
pipe test does not constitute a pass on Windows.

## Static, configuration and engineering checks

- `python scripts/repository_audit.py --output .audit-local`: enumerated all
  637 tracked baseline files (16,302,735 bytes); parsed 177 Python files with
  zero syntax errors; generated inventory, duplicate hashes, branch ancestry and
  value-redacted security candidates. This is AST parsing, not a hardware import test.
- `python manage.py check` and the project-venv equivalent: no issues.
- `python manage.py makemigrations --check --dry-run`: no changes detected.
- `python manage.py check --deploy`: six warnings, not a clean deployment check:
  W004 HSTS, W008 HTTPS redirect, W009 insecure development key, W012 secure
  session cookie, W016 secure CSRF cookie, W018 DEBUG. No live .env was used.
- Global `python -m pip check`: failed on unrelated global mediapipe/protobuf
  mismatch. Project `..\venv\Scripts\python.exe -m pip check`: no broken requirements.
- Direct PowerShell OpenSCAD launch first failed in the .com wrapper. A relative
  output through openscad.exe returned zero but did not export; this was **not**
  accepted as success. Retried with absolute input/output paths through Python
  subprocess: exit zero, assembly.csg exists, no stderr diagnostics.
  Command equivalent: `openscad.exe -o ABSOLUTE_AUDIT_PATH/assembly.csg ABSOLUTE_REPO_PATH/hardware/enclosure/companion_enclosure.scad`.
  This verifies parse/CSG export, not CGAL, full mesh validation or print fit.
- `git diff --check`: no whitespace errors before delivery.
- Relative Markdown file-link validation covers README and newly written guides;
  historical reference file links were rebased when preserved. External links
  use the verified canonical repository, existing refs and files; no fabricated
  demonstration URL is present. Arbitrary legacy engineering URLs are not certified.

## Advisory scan and targeted corrections

pip-audit was absent initially. It was installed with
`..\venv\Scripts\python.exe -m pip install --target .audit-tools pip-audit`,
without changing the existing project environment. With PYTHONPATH pointing to
that ignored directory:

```text
python -m pip_audit -r requirements.txt --no-deps --disable-pip --format json
```

The first run rejected `python-dotenv>=1.0` because --no-deps requires exact pins.
An ignored audit-only copy substituted installed python-dotenv 1.2.3. The scan
then found 21 known advisories in DRF 3.18.1 and pypdf 6.19.0. IDs/fixed versions
are checked in. The declared pins were changed to 3.18.2 and 6.21.4 respectively.

Packages were installed with `--no-deps --target .audit-runtime` and imported
successfully as **3.18.2 / 6.21.4** using an explicit PYTHONPATH. The full suite
was then run with the same override to test the actual updated packages; see
the final results recorded below. Neither the original venv nor the Pi was upgraded.
This scan covers Windows/Python-3.10-applicable direct requirements only; no
transitive or Linux-conditional dependency safety claim is made.

## Not run

Real Ollama/Gemma inference and weights download; acoustic STT/TTS; physical button
interruptions; UART/TFT visual acceptance; MQTT hardware/sensors; Pi CPU/thermal
measurements; live services/network/hotspot operations; fresh browser/mobile
acceptance; firmware compile (Arduino CLI/toolchain unavailable); full CAD CGAL
manufacturing rebuild; fresh ARM64 installation; full-history secret scanning;
complete transitive/Linux/native-tool vulnerability scanning.

Historical acceptance/test logs are preserved evidence from other releases and
are not counted as cleanup tests. No deployment, social post or PR merge occurred.

## Final results

- Updated DRF 3.18.2 and pypdf 6.21.4: full suite **1,002 tests, 1,001
  passed, one native Unix pipe skip, zero failures/errors**, 33.431 seconds.
- Same scoped pip-audit command against the updated audit-only manifest: **no
  known vulnerabilities in nine applicable direct requirements**. The manifest
  uses dotenv 1.2.3; the source range remains unchanged. This is not a complete
  dependency-tree clearance.
- 55 local Markdown file links checked across README/new uppercase-named guides
  and preserved reference: **zero missing targets**.
- Two embedded screenshots visually inspected: authentic mock-mode UI captures,
  explicitly qualified. No physical device image substituted.
- Source/test/migration/firmware/CAD/service files remain intact. Only requirements,
  README, ignore/attributes, documentation and the read-only audit utility changed.
- No file deletion, deployment, branch deletion, history rewrite or merge.
