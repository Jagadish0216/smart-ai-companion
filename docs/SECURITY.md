# Security boundaries and verification

## Configuration profiles

| Profile | Selection | Intended use |
|---|---|---|
| Development | Existing `config.settings` default | Loopback/mock development; DEBUG and fallback key are unsafe for shared deployment. |
| Trusted local appliance | Opt in with `DJANGO_SETTINGS_MODULE=config.appliance_settings` | DEBUG off, required non-default 50+ character secret, explicit non-wildcard hosts; HTTP/hotspot remains usable. |
| Public Internet | Not supplied as a supported profile | Requires a separate access-control, TLS, proxy and provisioning-exposure design and acceptance review. |

The opt-in appliance module changes no GPIO/audio/network/service configuration
and is not installed on the live Pi. Generate a random private secret as described
in [SETUP.md](SETUP.md); do not reuse a test fixture. `ALLOWED_HOSTS` validates
HTTP hosts, not users or networks. DEBUG=False does not add authentication.

Local HTTP defaults leave secure cookies, HTTPS redirect and HSTS off. Those
four deploy warnings are expected for this limited profile. Only
`COMPANION_HTTPS=true` turns on the redirect and secure session/CSRF cookies;
HSTS remains zero unless `COMPANION_HSTS_SECONDS` is explicitly set. Review a
working TLS endpoint first. No proxy-header trust is added. Do not select
HTTPS-only behavior on the HTTP hotspot. HSTS preload/subdomains stay disabled.

## Route review

| Surface | Existing boundary and remaining risk |
|---|---|
| Dashboard, conversations/history, metrics, logs, network reads | Public/default permissions; operational information is visible to reachable clients. |
| Chat and combined voice APIs | Default DRF access permits anonymous use; session CSRF is not anonymous-user authorization. Requests consume resources; configured MQTT actions may be invoked through chat policy. |
| Streaming chat, browser transcribe-only and synthesis | Explicit CSRF decorators; these do not establish user authentication or resource quotas. |
| Document list | Public metadata; filenames/titles can be private information on shared networks. |
| Document upload/reindex/delete/sync | Staff session authentication and CSRF; trusted-admin upload remains necessary despite PDF guardrails. |
| Normal Wi-Fi connect | Staff session authentication; network reads/scans have broader visibility. |
| Setup Wi-Fi connect | Explicit CSRF plus active setup-mode gating; intentionally available without normal login during provisioning. |
| Companion dashboard control | Public/default permissions; current controller reports simulated actions, distinct from acknowledged MQTT hardware actions. |

Evidence: `assistant/views.py`, `knowledge_base/views.py`, `system/views.py` and
`system/middleware.py`. Existing tests cover staff/CSRF/setup gates. No broad
authentication redesign was silently applied. Gunicorn binds all interfaces;
restrict reachability through an isolated trusted LAN/firewall or a reviewed
authenticated proxy before shared access. Never expose port 8000, Ollama, MQTT
or provisioning directly to the Internet. A proxy must cover APIs and history
as well as the dashboard; proxy authentication alone does not resolve every CSRF
or resource-exhaustion concern.

## Secrets and history

The tracked-tree heuristic was repeated after fetching main; new source was also
scanned in a tracked/nonignored-source snapshot with gitleaks. No confirmed active
credential is reported. No environment/database/user-content file was copied from
the Pi.

Gitleaks 8.30.1 was downloaded from its official release and checksum-verified.
`gitleaks git --redact=100 --log-opts=--all` covered all locally fetched refs
(156 reachable commits; scanner reported 69 scanned diff commits). One private-key
marker occurs in `knowledge_library/bundled/python-ssl.txt` at feature commit
`4df6dcb3366d991a53b4d35ec68917665f570973`. Context review confirms a Python SSL
documentation PEM illustration containing ellipsis, not usable private-key
material. It is not in current main. No rule suppressing private keys was added.
Reports retain only rule/path/commit metadata, never matched values.

This cannot prove absence in unreachable/pruned objects, unfetched refs, private
files or arbitrary personal source text. A future confirmed active secret requires
owner-managed rotation; deleting a file is insufficient. No credential rotation
or history rewrite occurred.

## Dependency scope

Existing DRF 3.18.2 and pypdf 6.21.4 pins remain unchanged. Official PyPI metadata
was checked; a newer DRF exists, but the scan did not justify another application
upgrade. Django stays on 5.2 rather than switching major releases. Python 3.10
Windows was freshly installed; CI uses Python 3.11 Linux for normal
openWakeWord/TFLite resolution.

The exact fresh Windows site-packages path was audited including transitive
dependencies. The initial 37-distribution scan reported 23 advisory entries only
in bootstrap pip 21.2.4 and setuptools 58.1.0; some IDs are duplicate aliases.
Only disposable environment tooling was upgraded to pip 26.2.2 and setuptools
84.2.1. CI updates installer tooling before installing unchanged requirements.
Final scan results are in the verification addendum. No existing environment was
upgraded. An earlier `--local` scan with scanner PYTHONPATH covered only 29
distributions; it was superseded by explicit `--path` scanning.

ARM64, Linux >=3.12 conditionals, Ollama/weights, whisper.cpp, Piper, Arduino/
TFT_eSPI and OS packages are separate scopes. No tree is claimed universally
vulnerability-free; requirement ranges can resolve differently later.

Primary references: [Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/),
[gitleaks](https://github.com/gitleaks/gitleaks),
[pip-audit](https://github.com/pypa/pip-audit).
