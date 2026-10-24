# Piper spawned-worker cleanup regression

The CLI cleanup test previously allowed five seconds for cold Python spawn and
the worker's ready frame. `main()` calls `warm()` before processing input, and a
warm-up failure returns 1. The test mocked `print` globally and discarded captured
stderr, hiding the reason behind `1 != 0`.

The isolated original test passes on the development Windows host. The reported
Pi duration (about 5.7 seconds) is consistent with startup deadline exhaustion,
but the original assertion does not establish that cause. ARM64 cold-start timing
versus an IPC failure must be distinguished using the new diagnostics on the Pi;
neither an import failure nor a protocol failure has been demonstrated.

The cleanup test now uses the existing production default of 30 seconds for cold
readiness. Only after the actual ready frame arrives does it select five seconds
for synthesis. Both successful and failed assistant streams still must close the
connection and reap the spawned process and streaming workers. No sleeps or
unbounded waits were added. A virtual-clock regression covers six-second startup
followed by successful synthesis without slowing the test by six seconds.

Controlled IPC errors now report readiness/synthesis phase and child exit code in
the exception and warning log. They never include IPC payloads, voice paths,
arbitrary child exception messages or spoken text. Assertion failures also show
the CLI's mocked stderr prints. Production spawn, timeout defaults, cancellation,
termination and kill deadlines are unchanged.

Validation on Windows: 832 selected voice/intelligence tests passed both in the
working tree and in a clean export excluding unrelated local edits. Django system
checks and `git diff --check` passed. The final strengthened cleanup assertions
were also rerun with the focused CLI and persistent-worker lifecycle tests.

For deployment, fetch `fix/piper-worker-reliability` and cherry-pick only its final
focused commit onto the existing `dd52fcba418fa25c9af7d6e6c262b6e8ce8b728b`
checkout. Require clean tracked files; preserve untracked files including
`knowledge/`. Stop on conflicts with `git cherry-pick --abort`; never reset or
clean. Run the voice and intelligence suites, Django checks and whitespace checks
before restarting the existing application and voice services. Afterwards run
`venv/bin/python manage.py verify_intelligence --warm-voice`.

Record the resulting cherry-picked SHA. Roll back with `git revert <that-SHA>`,
rerun the checks and restart the same services. A cherry-picked SHA differs from
the source commit SHA. No Pi execution or physical audio validation is performed
by the development tests.
