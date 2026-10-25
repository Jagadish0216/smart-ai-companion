# Pi regression audio settings isolation

## Verified cause

Both failed tests call `VoiceLoopConfig.from_settings()` and previously set
`VOICE_AUDIO_BACKEND="pulse"` and a dummy input source without setting the
playback profile. The supervisor loaded `.env` followed by
`/etc/smart-ai-companion/demo.env` for tests as well as benchmarking. The latter
repository example selects ALSA and `approved_loudness`.

The exact reproduced interaction is:

| Setting | Loaded simulated Pi value | Value seen by the old tests |
|---|---|---|
| `VOICE_AUDIO_BACKEND` | `alsa` | `pulse` (test override) |
| `VOICE_PLAYBACK_PROFILE` | `approved_loudness` | `approved_loudness` (inherited) |
| `VOICE_INPUT_SOURCE` | empty | `source` (test override) |
| `VOICE_CAPTURE_VAD_ENABLED` | false | false (inherited) |
| `VOICE_CAPTURE_READY_LIFECYCLE` | false | false (inherited) |
| `VOICE_CAPTURE_PREPARATION_POLICY` | `legacy` | `legacy` (inherited) |

This reproduces both exact ALSA-path errors. First-clause settings were explicitly
65/110 characters, and the other test selected a 220-character TTS target; neither
failure was caused by chunk validation. The traceback proves a non-legacy profile
was inherited; it cannot distinguish `approved_loudness` from `bounded_gain` on
the actual Pi without reading its configuration. Both are covered by regressions.
Production's requirement that non-legacy speaker profiles use ALSA is correct.

## Focused changes

`VOICE_LOOP_TEST_SETTINGS` in `assistant/test_support.py` explicitly supplies every
setting read by `VoiceLoopConfig.from_settings`: backend, source/devices, gain,
profile, timing, chunk/sentence limits, VAD and calibrated-readiness settings.
The two tests apply that baseline before their test-specific limits. Their
original valid/invalid limit assertions remain, with additional Pulse/legacy and
readiness assertions. Nested overrides restore the caller's production settings.

`config.test_settings` imports ordinary Django settings then overrides voice and
wake defaults, mock engine selection, disabled device/display/online/RAG defaults,
and an in-memory SQLite database. It modifies neither environment variables nor
configuration files. Base settings may still read `.env`; explicit test settings
take precedence. Test cases continue to opt into individual capabilities using
their existing overrides/mocks. No production validation, driver or playback code
is changed.

The supervisor's `python_test` launches the regression process with explicit
`--settings=config.test_settings`, without loading the appliance demo EnvironmentFile.
Runtime Django checks, the 18-request quick benchmark and source verification keep
the existing `python_user` loader and the actual Pi `.env`/demo.env configuration.
The test profile is selected only for the test/check child process; it never
leaks into the benchmark or changes the default model.

New regressions run the actual two tests beneath inherited non-legacy profiles
and calibrated capture settings, check valid legacy/Pulse and all ALSA profiles,
and verify Pulse/non-legacy combinations are still rejected. Fresh Python
processes confirm the same production environment produces isolated Pulse/legacy
settings in the test profile and actual ALSA/approved-loudness settings at runtime.
Read-only deployment patch detection is exercised against a temporary Git
repository for absent, original-ancestor and equivalent-cherry-pick states. These
tests never execute sudo, runuser or systemctl.

## Deployment and recovery

Use the reviewed commit's updated `scripts/deploy_benchmark_timeouts.sh` through
the sudo supervisor as the existing checkout owner. It requires
`feature/adaptive-online-retrieval` at
`53fca5636879d21d6ba8ea4d69182e6ab4af8c28`, clean tracked files and the two services
initially active. Untracked `knowledge/` and other files are allowed. It never
stashes, resets, cleans, runs deployment migrations, edits environment files or
touches model files. Test database setup occurs only in memory.

The changed-path allowlist admits only this focused test/supervisor fix. The fix
is cherry-picked once and a local rollback tag recorded. An existing equivalent
cherry-pick directly atop the required baseline is reused on a rerun, including
its existing matching rollback tag. Additional unrelated deployment commits or
unexpected tag targets stop for review rather than being overwritten.

Voice is stopped before voice tests, and the existing privileged EXIT/INT/TERM
supervisor restores both services on failure or interruption without relying on
a still-valid sudo timestamp, and verifies both units are active with bounded
checks. It checks the isolated test profile and actual
runtime Django settings separately. Only after tests/checks pass does it stop the
app, run the 18-request quick benchmark using runtime settings, restore services,
and report source acquisition and a measured model summary. Timeout budgets,
sequential execution, progress, incremental JSONL and benchmark cleanup are unchanged.

The supervisor prints the report location and exact rollback command. Revert only
the printed local tag to undo this fix, then run Django checks and restart the two
services. A cherry-pick conflict stops before service changes; use
`git cherry-pick --abort` and review manually. Shell traps cannot run after SIGKILL
or power loss; service health must be checked manually then. Failed service starts
are explicitly reported, not described as successful recovery.

The deployment command is prepared only; no Pi access, service restart, physical
audio test or real model benchmark is performed during development validation.

## Validation

All runs used a clean export excluding unrelated local edits:

- Original 864-test suite, ordinary `config.settings`: passed.
- Same 864 tests with simulated production ALSA/approved-loudness environment:
  passed, without selecting the isolated test profile.
- All 870 tests (including six new regressions), `config.test_settings` under
  that production audio environment: passed.
- Django checks for both settings modules and Bash syntax checks: passed.

The original two failures were reproduced before the fix with that same audio
environment. No test was disabled, skipped or weakened. The repository's default
knowledge-sync persistence setting is retained in the isolated test profile;
online retrieval remains off unless a test explicitly enables it.
