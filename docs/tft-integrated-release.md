# TFT Phase B + V2.2 integrated release candidate

Status: **Windows regression/build checks pass; Linux validation is pending.**
This is not a fully accepted integrated release until the complete Raspberry Pi
Linux regression run passes.

## Provenance and hardware acceptance

- Branch: `tft-modern-face-v2`.
- Starting HEAD: `d9d7f390efb15d92d2354e808cd76fcebac9f7e3`.
- Main baseline: `160aa8583d0f2222c1add1cc3857bc79c03c233b`.
- Phase B parent: `de80468ad9bdb4f1c6920925e2080a71707d229d`.

The starting branch already contains Phase B audio LEVEL followed by V2.2
firmware. No cherry-pick, rebase, reset, duplicate integration or merge was used.
Main and `tft-audio-level-v1` are preserved.

Owner-reported physical acceptance on October 19, 2026: READY, THINKING and
SPEAKING animations; smooth LEVEL-driven speaking; clear audio; QUERY, MODE and
captions; return to READY without flicker. That acceptance remains valid for the
unchanged production code. It does not substitute for Linux suite validation.

## Investigation and changes

The reported Pi run used Phase B commit `de80468`, Python 3.13 / Debian 13:
434 tests, five failures and eleven errors; a subsequent focused Phase B run
passed 128 tests. Exact Pi tracebacks were not available during this review, so
the original sixteen results cannot all be assigned a confirmed root cause.
Current complete Windows discovery contains 1,002 tests.

| Area | Reproduced evidence / disposition |
|---|---|
| Wake configuration | Four fixture assertions fail with enabled wake-word, a custom model path and a non-default cooldown. Tests expected defaults while reading deployment settings. Fixed by class-scoped explicit wake settings; method-level configuration/error cases still override them. Production settings are untouched. |
| RAG database isolation | With `AI_ENGINE=local` and `RAG_ENABLED=true`, the real response policy probes indexed knowledge from a `SimpleTestCase`. Reproduced in `CaptureReadyCallbackTests.test_real_interactive_voice_cycle_preserves_accepted_vad_timing`. Isolated only the unrelated availability probe. The hands-free production-cycle fixture has the same dependency and now isolates it too. Both tests explicitly exercise local/RAG-enabled settings without granting database access or mocking the full response policy. |
| Windows hands-free stall | NumPy was missing from the local virtual environment. The fake capture counter advanced only after converter construction; the mocked cooldown let the real retry loop repeat the import failure indefinitely. Installed NumPy 2.2.6 locally and added a dependency preflight before that test enters the retry loop. No production retry/audio changes. |
| Windows model download fixture | One sandbox run failed an atomic temporary-file rename with `WinError 5`. Using a task-owned writable TEMP/TMP directory resolved it. No storage/model-download implementation change. |
| Piper timeouts / worker exit | Real spawned synthetic-worker lifecycle, SIGINT/EOF, crash, cancellation, timeout, recovery and voice-main cleanup tests pass on Windows. These exercise the production provider with fake voices. The reported Pi failures are not reproduced or confirmed as production regressions here. Production timeouts, IPC and worker behavior were not changed; Python 3.13 / Linux results remain required. |
| Integrated production behavior | Reviewed LEVEL validation, callback failure isolation, microphone RMS reuse, bounded playback summaries, reset/keepalive, Future timeout handling, serial command/lifecycle compatibility, firmware local rendering and buffer bounds. No verified production defect was found in this review. Hardware-accepted production files remain byte-for-byte unchanged. |

Files changed for this release preparation:

- `assistant/test_support.py`: explicit wake configuration fixture values.
- `assistant/tests_wakeword.py`: scope default settings, isolate the RAG
  capability probe and fail promptly when the DSP dependency is absent.
- `assistant/tests_capture_vad.py`: isolate the capability probe in the real
  lifecycle test and assert that it is exercised with local/RAG enabled.
- This release report. No firmware, audio, display provider or runtime settings
  file is changed by release preparation.

## Verification

Windows: Python 3.10.2, existing project venv, Django 5.2.17. NumPy 2.2.6 was
installed only into that venv as a missing prerequisite; no requirements or
deployment configuration was rewritten. A task-owned temporary directory was
used to avoid the sandbox's rename restriction.

- Complete suite: `python manage.py test --noinput --verbosity=2`.
- Final complete-suite run: 1,002 tests in 28.161 s,
  1,001 passed, one Linux-only real pipe test skipped, zero failures/errors.
- Enabled-profile isolation run: 64 tests passed in 0.750 s, with local AI,
  RAG, wake-word enabled, model `alexa`, custom path, threshold 0.7, VAD 0 and
  cooldown 1.2. These are deliberately non-default fixture inputs, not a claim
  to reproduce the Pi's exact configuration.
- Expanded audio/display regression selection under that enabled profile:
  434 tests in 16.942 s, 433 passed, the same Windows-only pipe skip,
  zero failures/errors. This includes spawned Piper and voice-main cleanup.
- Firmware compile passed for `esp32:esp32:esp32doit-devkit-v1`, ESP32 core
  3.3.12: 317,008 bytes flash (24%); 23,708 bytes static RAM (7%). Sprite pixels
  allocate another 77,382 bytes at startup. No upload or wiring change.
- Protected production/configuration/artifact hashes and main/audio branch refs
  were checked. Unrelated untracked ZIP, audit log, screenshots and review diff
  remain outside the commit. Whitespace checks pass.

## One consolidated Raspberry Pi Linux regression command

Run from the repository root on this feature branch using the Pi's existing
activated application virtual environment and installed prerequisites. Keep the
deployment configuration enabled: do not disable RAG or wake-word to hide
failures. This runs complete discovery, not only the historical 434-test
selection. On Linux the real nonblocking-pipe test must run rather than skip.

The command writes failing test names and tracebacks to one log, prints the
summary and returns the test exit status. A passing run leaves the failure log
empty. If setup fails before unittest can report test failures, the log retains
that startup diagnostic instead. It does not change configuration or firmware.

```bash
python - <<'PY'
from pathlib import Path
import re
import subprocess
import sys

run = subprocess.run(
    [sys.executable, "manage.py", "test", "--noinput", "--verbosity=0"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
blocks = re.findall(
    r"(?ms)^={70}\n(?:ERROR|FAIL):.*?(?=^={70}\n|^-{70}\nRan |\Z)",
    run.stdout,
)
log = Path("/tmp/tft-integrated-linux-failures.log")
log.write_text("\n".join(blocks) if blocks else
               (run.stdout if run.returncode else ""), encoding="utf-8")
summary = re.search(r"(?m)^Ran .*", run.stdout)
print(summary.group(0) if summary else "Test run did not finish normally.")
print(f"exit={run.returncode}; failure diagnostics: {log}")
sys.exit(run.returncode)
PY
```

## Release gate and remaining risks

Ready for the complete Pi Linux regression gate; **not fully accepted yet**.
Require exit status zero on the final pushed feature-branch revision, review
unexpected skips, and retain the summary/revision. If Piper or lifecycle failures
remain, use the single log above to identify their actual failing operations and
tracebacks before adjusting production behavior. Windows synthetic workers do
not validate Piper/ONNX native packages, actual Pi scheduling or Python 3.13
process/signal behavior. The physical audio/display acceptance covers the stated
hardware scenarios, not every malformed serial input or allocation fallback.

Existing Phase B limits still apply: fixed-duration capture and legacy/Pulse
playback paths do not provide live speaker LEVEL; buffered ALSA pipe acceptance
is an estimate of physical playback time. Those accepted implementation limits
are not expanded or changed here. No merge to main or replacement flashing is
part of this release preparation.
