> PR #2 verification retained below. The final-polish addendum records the later main baseline, CI and security checks.

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

## Final-polish verification — 1 November 2026

Baseline fetched: `23e84ffe3fafd21448848df52aaa12ced4dde1a5`. New isolated
worktree/branch: `.portfolio-polish`, `portfolio/final-polish-and-ci`. The previous
owner checkout and first cleanup worktree were preserved. No Pi/SSH operation.

| Executed check | Actual result |
|---|---|
| Unchanged merged-main full suite using the previously validated package overrides | 1,002 tests, 1,001 passed, one Unix-pipe platform skip; 68.258 s. |
| Fresh `.venv`: `python -m pip install -r requirements.txt` | Complete Windows/Python-3.10 install, including openWakeWord/native Python dependencies; no weights downloaded. |
| `python manage.py check`; `python manage.py makemigrations --check --dry-run` with CI settings | No issues; no migrations needed. |
| `python -m pip check` in fresh environment | No broken requirements before and after installer-tool updates. |
| `DJANGO_SETTINGS_MODULE=config.ci_settings python manage.py test --noinput` in fresh Windows environment | 1,009 tests, 1,008 passed, one native Unix skip; zero failures/errors; 57.991 s. |
| `python -m unittest tests.test_repository_tools -v` after the final trace-schema allowance | Seven export/privacy/profile tests passed; 2.763 s. |
| `actionlint .github/workflows/ci.yml` (1.7.12, release checksum verified) | Exit 0; workflow syntax/expressions valid. |
| GitHub-hosted Software CI, Ubuntu 24.04 / Python 3.11.17 | **Successful actual run:** [41285167432](https://github.com/Jagadish0216/smart-ai-companion/actions/runs/41285167432), head 3bbb006. Full suite **1,009 passed, no skips**, 19.511 s; all required steps passed. |
| `python scripts/check_repository.py` | 184 Python files parsed; maintained local links resolve. Final exact link count is in PR validation (documentation additions change the count). |
| Opt-in appliance `manage.py check --deploy` with a generated private test key | DEBUG/key warnings removed; four expected HTTP warnings remain (W004/W008/W012/W016). No warning suppression or live config change. |
| OpenSCAD absolute-path CSG export for optimized assembly | Exit 0; entire regenerated CSG text identical after CRLF/LF normalization. No mesh or hardware acceptance claimed. |
| Local source-export rehearsal, outside source root | 247 source files exported; ZIP/manifest hashes generated. Regression tests independently verify source preservation, exclusions, modified/untracked inclusion, schema export, symlink/credential refusal and overwrite protection. No device source was captured. |
| `git diff --check` | No whitespace errors. |

The hosted result above is pinned evidence for its tested commit, not an assertion
that every future PR revision passed. The PR checks show validation of the final
head separately. Workflow triggers: main pushes, PRs targeting main and dispatch;
contents:read only, immutable Actions, no persisted credential, no deployment.

### Failures investigated, not hidden

- Initial new tests inside the sandbox could not create temporary Git fixture
  configuration. The unrestricted run passed; no Git test was blanket-skipped.
- One test attempt used the fresh venv before installation finished and lacked
  Django. Rerun in the complete environment passed. The final 1,009-test run used
  the completed fresh install, not that incomplete attempt.
- Initial current-tree gitleaks found three long dummy test secrets. Tests now
  generate ephemeral keys at runtime; repeat current-source scan is clear. No
  scanner suppression was added.
- The initial scanner-PYTHONPATH `--local` advisory scan covered only 29 packages.
  It was replaced with explicit site-packages path scanning to cover all 37.

### Security and advisory commands

`gitleaks git --redact=100 --log-opts=--all --report-format=json` scanned all
locally fetched refs: 156 reachable commits; gitleaks reported 69 diff commits.
Its one historical PEM placeholder is reviewed in [SECURITY.md](SECURITY.md).
`gitleaks dir` against a tracked/nonignored-source-only snapshot then passed
with zero findings after the dummy-fixture change. Reports never print raw matches.

Using the isolated pip-audit installation, the exact runtime scope was:

```text
python -m pip_audit --path .venv/Lib/site-packages --format json
```

The 37-distribution Windows scan initially found 23 entries in old venv bootstrap
pip/setuptools (including duplicate aliases), none in the application pins.
`python -m pip install --upgrade pip setuptools` changed only that disposable
venv to pip 26.2.2 / setuptools 84.2.1. The repeat exact-path scan reported
**no known vulnerabilities**. CI refreshes its installer tooling similarly.
ARM64/Linux-conditional/native/model/firmware advisories remain outside this
Windows scope. Application requirements are unchanged from merged main.

### Source and hardware limits

Release manifest comparison uses canonical main Git-blob SHA-256 values, not
Windows checkout line endings. Zero of ten required existing-file hashes match
(four different, six missing); two release files are new. The snapshot commit
object is unavailable. See [comparison](repository-audit/source-sync-comparison.json)
and [export/reconciliation procedure](DEPLOYMENT.md).

Not run: live Pi inference/audio/button/TFT/MQTT/thermal/network/hotspot validation,
physical source export, fresh mobile/browser acceptance, firmware build (toolchain
unavailable), complete CAD manufacturing/CGAL rebuild, physical print-fit testing,
ARM64 package installation, unreachable/unfetched Git-object scans or complete
native/OS/platform advisory coverage. No hardware behavior, live service, network,
firmware or model selection was modified. No merge or social publication.

### Final checker regression

Final review caught `docs/media/README.md` being hidden by the pre-existing
`media/` ignore rule. Narrow documentation exceptions now include it. The link
checker also verifies that a destination is tracked or nonignored source, so an
ignored local file cannot falsely satisfy a public Markdown link. A regression
reproduces this case. The final focused suite is **eight passing tests** (2.986 s),
including trace-schema export. The earlier seven-test/1,009-total measurements
above remain pinned historical results; final-head totals are in the PR.

### Completed final code validation

At code head `7c46ccc16b5e825bdc83f96906c1f760919125e6`, actual hosted run
[41285566998](https://github.com/Jagadish0216/smart-ai-companion/actions/runs/41285566998)
passed **all 1,010 tests with zero skips**, 17.024 s. Every CI step passed.
Windows final suite: **1,010 tests, 1,009 passed, one Unix-pipe skip**, 38.892 s.
The checker passed **184 Python files and 70 local links**; the final tracked-source
gitleaks scan had zero findings. No application pins changed. Later evidence-only
documentation is checked separately by PR CI; this run pins the tested code.

Final documentation review also corrected Windows code-page corruption of Unicode
year ranges/arrows introduced during this polish. Explicit UTF-8 reads/writes
restore those characters; no application behavior changed.
