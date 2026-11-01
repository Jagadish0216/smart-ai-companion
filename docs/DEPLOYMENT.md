# Raspberry Pi deployment and source provenance

The owner reports a completed, assembled and submitted Raspberry Pi 5 project.
This repository audit did not connect to the Pi or modify files, services,
networking, audio, GPIO or firmware there.

## Portfolio baseline

The cleanup branch starts at published `main`:
`f59da97d270ebe1157dac65e9862ec8ed77116cd`. This preserves the integrated
TFT release and its regression suite. It is a reproducible public software
baseline, **not a claim that main equals the final deployed source**.

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
