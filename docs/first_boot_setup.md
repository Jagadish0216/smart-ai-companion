# First-boot Wi-Fi setup

Smart AI Companion uses one NetworkManager-managed Wi-Fi adapter and switches it between client and access-point modes. It never tries to operate an AP and client concurrently on `wlan0`.

## State machine

The persisted states are `UNPROVISIONED`, `SETUP_AP`, `CONNECTING`, `FAILED`, and `NORMAL_MODE`. `CONNECTED` is an observed transition: after a client connection is verified, it is immediately persisted as `NORMAL_MODE` rather than stored redundantly.

- A boot with an active non-setup Wi-Fi profile becomes `NORMAL_MODE`, even when NetworkManager reports no Internet access.
- An unprovisioned boot tries up to `SETUP_SAVED_PROFILE_ATTEMPTS` saved client profiles. If none works, it starts the setup AP.
- A persisted `SETUP_AP`, `FAILED`, or interrupted `CONNECTING` state restores the setup AP on boot.
- Setup submission moves `SETUP_AP → CONNECTING → NORMAL_MODE` after the selected SSID is observed as the active client connection.
- A failed or unverifiable connection moves through `FAILED` and restores `SETUP_AP`. If AP restoration itself fails, `FAILED` remains persisted for the next boot.

The setup AP profile is named `SmartCompanion Setup`. Its SSID is `SmartCompanion-XXXX`, where the suffix is a stable SHA-256-derived fragment of the OS machine ID. No MAC address is published. The profile uses NetworkManager shared IPv4 and WPA2/RSN PSK, has autoconnect disabled, and is never operated concurrently with client mode. Existing Wi-Fi profiles are neither changed nor deleted.

## Required deployment configuration

Set a unique random passphrase in the production `.env`; do not commit or log it:

```env
ALLOWED_HOSTS=smart-ai-companion.local,localhost,127.0.0.1
SETUP_AP_PASSWORD=<unique-random-16-or-more-character-passphrase>
SETUP_SAVED_PROFILE_ATTEMPTS=5
SETUP_CONNECT_COOLDOWN_SECONDS=5
```

On the deployed Pi, edit the existing file interactively so the secret is not placed in shell history, then restrict it to the service account:

```bash
cd /home/jagadish/apps/smart-ai-companion
nano .env
chmod 600 .env
```

`SETUP_AP_PASSWORD` must be 8–63 UTF-8 bytes. A unique random 16+ character value is recommended. Missing values, control characters, and common placeholder/default passwords fail closed. There is deliberately no default password. The application passes it only as an argument to NetworkManager; it is not returned by APIs or saved in the provisioning table. Replace the example placeholder; never install it literally and never document the real device password.

NetworkManager must manage `wlan0`. Both the Django service and boot command run as the non-root `jagadish` account. Django never invokes `sudo`; the versioned Polkit rule grants that account only the four required NetworkManager actions.

## NetworkManager Polkit policy

Install the repository rule before testing AP mode:

```bash
cd /home/jagadish/apps/smart-ai-companion
sudo install -o root -g root -m 0644 \
  deployment/polkit/49-smart-ai-companion-network.rules \
  /etc/polkit-1/rules.d/49-smart-ai-companion-network.rules
```

Polkit monitors `rules.d` and normally reloads JavaScript rules automatically, so Debian 13 does not normally require a service restart. Verify first. Only if the new permission is not visible after a short wait, reload Polkit with `sudo systemctl restart polkit.service`, then verify again.

```bash
nmcli general permissions
```

The following entries must report `yes` for user `jagadish`:

```text
org.freedesktop.NetworkManager.network-control            yes
org.freedesktop.NetworkManager.settings.modify.system     yes
org.freedesktop.NetworkManager.wifi.scan                  yes
org.freedesktop.NetworkManager.wifi.share.protected       yes
```

The rule does not grant open hotspot sharing, Wi-Fi/network enable-disable, hostname mutation, wildcards, or permissions to any other user.

## Raspberry Pi boot integration

Install NetworkManager and Avahi using the Raspberry Pi OS package manager, set the OS hostname once to `smart-ai-companion`, and enable both services at boot. The application does not change the hostname and does not implement mDNS itself.

The current Pi deployment is `/home/jagadish/apps/smart-ai-companion`, runs as `jagadish:jagadish`, loads `/home/jagadish/apps/smart-ai-companion/.env`, and uses `smart-ai-companion.service` for Django/Gunicorn.

First update the deployed code and `.env`, install the Polkit rule above, and prepare Django:

```bash
cd /home/jagadish/apps/smart-ai-companion
/home/jagadish/apps/smart-ai-companion/venv/bin/python manage.py migrate
/home/jagadish/apps/smart-ai-companion/venv/bin/python manage.py collectstatic --noinput
/home/jagadish/apps/smart-ai-companion/venv/bin/python manage.py check
```

Before enabling boot automation, verify the safe client path while `wlan0` is connected normally:

```bash
cd /home/jagadish/apps/smart-ai-companion
/home/jagadish/apps/smart-ai-companion/venv/bin/python manage.py ensure_network_mode --prefer-client
```

The expected result is `NORMAL_MODE: An active local Wi-Fi connection is available.` Do not run `--force-setup` over the only SSH/Wi-Fi path; it intentionally disconnects client Wi-Fi. Arrange local console or Ethernet recovery before the first AP test.

Install and enable the provisioning unit:

```bash
cd /home/jagadish/apps/smart-ai-companion
sudo install -o root -g root -m 0644 \
  deployment/systemd/smart-ai-companion-network-mode.service \
  /etc/systemd/system/smart-ai-companion-network-mode.service
sudo systemctl daemon-reload
sudo systemctl enable smart-ai-companion-network-mode.service
sudo systemctl enable NetworkManager.service avahi-daemon.service
sudo systemctl start smart-ai-companion-network-mode.service
sudo systemctl status --no-pager smart-ai-companion-network-mode.service
```

The unit is a bounded 90-second oneshot running as `jagadish:jagadish`, with no restart loop, shell, root execution, or embedded credential. It weakly wants both NetworkManager and Avahi and starts after them. An Avahi failure does not make this unit fail, so basic NetworkManager provisioning remains available. `Before=smart-ai-companion.service` ensures the enabled provisioning job completes before the enabled Django/Gunicorn service during boot. `RemainAfterExit=yes` records that the boot decision ran; use `systemctl restart smart-ai-companion-network-mode.service` only when an intentional re-evaluation is required.

The canonical address in both normal and setup modes is:

`http://smart-ai-companion.local:8000/`

The setup page is `/setup/`. NetworkManager may internally allocate an address such as `10.42.0.1`, but DHCP addresses are not product identity and are not generated into dashboard links. Avahi/mDNS must advertise the stable hostname. A temporary `.local` lookup failure is not considered a Wi-Fi failure.

## Recovery and development controls

From an authorized local service session, explicitly enter recovery mode with:

```bash
cd /home/jagadish/apps/smart-ai-companion
/home/jagadish/apps/smart-ai-companion/venv/bin/python manage.py ensure_network_mode --force-setup
```

This intentionally drops the current Wi-Fi client connection. The setup-only APIs are available only while the persisted state is provisioning/recovery; the normal staff-only Wi-Fi endpoint remains unchanged.

To return a development device to a known saved network without deleting any profile, use local console/Ethernet access:

```bash
nmcli connection down "SmartCompanion Setup"
nmcli connection up "<known-profile-name>" ifname wlan0
/home/jagadish/apps/smart-ai-companion/venv/bin/python manage.py ensure_network_mode --prefer-client
```

`--prefer-client` bypasses a persisted recovery request once, verifies an active client profile, and otherwise restores the setup AP. On Windows, do not invoke this command to test real networking; use the mocked test suite. Setup mode remains dormant in the initial `UNPROVISIONED` database state until the boot command is run.

## Setup security boundary

The setup page has no dashboard navigation, logs, device controls, or arbitrary command surface. While setup mode is active, request isolation redirects ordinary pages to `/setup/` and returns 404 for every non-setup API; only setup routes and local static assets remain reachable. Its three APIs expose status, scanning, and one validated Wi-Fi connection operation. They return 404 outside provisioning mode. The mutation endpoint uses Django CSRF protection and a global configurable cooldown. Passwords are cleared from the browser field before the radio switch and are never echoed in responses. Normal `/api/system/network/wifi/connect/` authorization remains staff-only in normal mode.
