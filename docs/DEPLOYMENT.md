# Raspberry Pi deployment and source provenance

The owner reports a completed, assembled and submitted Raspberry Pi 5 project.
This repository audit did not connect to the Pi or modify files, services,
networking, audio, GPIO or firmware there.

## Portfolio baseline

The first cleanup branch started at published `main`:
`f59da97d270ebe1157dac65e9862ec8ed77116cd`. This preserves the integrated
TFT release and its regression suite. It is a reproducible public software
baseline, **not a claim that main equals the final deployed source**.

PR #2 subsequently merged that cleanup as `23e84ffe3fafd21448848df52aaa12ced4dde1a5`.
The final-polish branch was created from this freshly fetched revision. That
merge preserved the application root; it did not synchronize the Pi snapshot.

Remote release branch `codex/pi-response-control-20261031` ends at
`2e9b9e9333890934906e8284f862304dc35c68ef`. It adds 14 release-package files
to main, rather than installing the patched code in the application root.
The response-control package was introduced by
`5faba735640fd294bec28b88aa824dfbc8a989bc`.

- [Pinned response-control release](https://github.com/Jagadish0216/smart-ai-companion/tree/5faba735640fd294bec28b88aa824dfbc8a989bc/deployment/releases/pi-response-control-20261031)
- [Pinned deployment and rollback procedure](https://github.com/Jagadish0216/smart-ai-companion/blob/2e9b9e9333890934906e8284f862304dc35c68ef/deployment/releases/pi-response-control-20261031/PI_RESPONSE_CONTROL_DEPLOYMENT.md)

Its manifest names snapshot HEAD `645a066adf49e42ab0d16f0cd6195cd14ab16ed0`
on `feature/adaptive-online-retrieval`, with per-file pre/post hashes. This is
not main's application tree. Do not apply that diff to main merely because
the release branch descends from main. The source snapshot and provider dispatch
differ; the release documentation explicitly prohibits pulling the branch into
production. Release test counts are historical package evidence, not tests run
by this cleanup.

The owner also reports a later minimal `assistant/services.py` hotfix and local
RAG configuration changes. Their exact current deployed bytes have not been
verified here. The dirty original Windows checkout is preserved, but cannot be
treated as a verified Pi export. Safe synchronization needs a private source
snapshot/hash manifest and an explicit compatibility review, followed by tests
against that snapshot. No feature branches were blindly merged.

### Read-only source export for later reconciliation

The current comparison found **zero matching preimages** among the release's ten
existing-file preconditions: four differ from main and six are missing. Two
additional paths are new release files. The snapshot Git object `645a066` is not
available in the fetched repository. The manifest describes 469 snapshot files;
its preservation hashes do not provide the missing source bytes. Therefore no
voice-interruption/streaming patch was transplanted into the application root.
See the [machine-readable comparison](repository-audit/source-sync-comparison.json).

After reviewing the utility, the owner can copy only
`scripts/export_pi_source.py` onto the Pi without pulling or replacing the app.
Run it locally with the existing Python 3.10+ interpreter, writing to a new file
outside the application tree (the parent directory must already exist):

```sh
python3 /path/to/reviewed/export_pi_source.py \
  --root /home/jagadish/apps/smart-ai-companion \
  --output /home/jagadish/pi-source-review-20261101.zip
```

This utility does not invoke SSH, systemctl, network setup, models or hardware.
It reads Git HEAD/status and allowlisted tracked/nonignored untracked source,
including working-tree edits, and writes an exclusive private ZIP with SHA-256
hashes and `MANIFEST.json`. It excludes dotfiles/environment files, databases,
logs, user content, model binaries, credentials/private-named files and unrelated
paths; rejects source symlinks, high-confidence credential patterns and hardcoded
production credential-like assignments; and refuses output inside the source
tree or overwriting an archive. Only selected source paths appear in status;
excluded filenames and local remote URLs are not exposed.

It is a review tool, not proof that arbitrary source text contains no personal
data. Pause source editing during capture; hashes describe exported bytes, not
an atomic filesystem snapshot. Review the archive privately and scan it before
sharing. Do not upload it blindly to GitHub. Ignored source is not exported and
may need a separate private review. Runtime `.env` values are intentionally absent:
provide only selected nonsecret facts (model tag, RAG on/off, engine names)
separately rather than an environment dump.

Reconciliation after export:

1. Verify the printed archive SHA-256 and every manifest file hash without applying
   anything to the Pi. Check HEAD and selected working-tree status.
2. Compare exported paths against pinned main and release pre/post hashes. Account
   separately for the later `services.py` hotfix and configuration facts.
3. Stage compatible complete changes on another development branch; resolve
   provider, interruption and streaming differences from actual source bytes.
4. Run full regression tests and targeted response-control tests with mocks.
5. Request separate approval for physical acceptance/deployment; retain the
   existing pinned rollback package. No automatic live synchronization is provided.

## Existing appliance layout

- Application: `/home/jagadish/apps/smart-ai-companion`
- Web: `smart-ai-companion.service`
- Standalone physical voice: `smart-ai-companion-voice.service`
- Inference: `ollama.service` (external service)

Templates are in [deployment/systemd](../deployment/systemd). The web unit uses
Gunicorn with one gthread worker, four threads and a 180-second timeout; it binds
all interfaces. Paths and the `jagadish` account are appliance-specific examples.
Network mode/model-warm dependencies also exist. Review them before installation;
do not enable them on a development machine.

For a new, separately approved installation, configure the private environment,
create the database with `python manage.py migrate`, collect assets with
`python manage.py collectstatic --noinput`, validate with
`python manage.py check --deploy`, and review templates against actual device
ownership and permissions. Gunicorn runs on Linux, not native Windows.
This document intentionally performs no live service operations.

Use [first-boot provisioning](first_boot_setup.md) for NetworkManager/Avahi and
hotspot requirements, including a unique `SETUP_AP_PASSWORD`. Never reuse sample
credentials. Keep serial adapter selection and audio device configuration tied
to the actual hardware. See [SETUP.md](SETUP.md) for model overrides and security.

## Rollback of portfolio changes

Review and merge through a pull request only. Before merge, main is unaffected.
After a future merge, revert the cleanup commits using a new reviewable commit;
do not reset published history. This documentation-only release cannot serve
as a deployment rollback for the separate Pi response-control hotfix.
