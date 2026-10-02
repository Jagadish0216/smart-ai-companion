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

`SETUP_AP_PASSWORD` must be 8–63 UTF-8 bytes. A unique 16+ character value is recommended. There is deliberately no default password. The application passes it only as an argument to NetworkManager; it is not returned by APIs or saved in the provisioning table.

NetworkManager must manage `wlan0`, and the Django/boot process must have permission to activate NetworkManager profiles. Django never invokes `sudo`. If the web service runs as an unprivileged account, grant only the required NetworkManager action through the deployment's polkit policy rather than making the web process generally privileged.

## Raspberry Pi boot integration

Install NetworkManager and Avahi using the Raspberry Pi OS package manager, set the OS hostname once to `smart-ai-companion`, and enable both services at boot. The application does not change the hostname and does not implement mDNS itself.

After deploying under `/opt/smart-ai-companion`:

```bash
cd /opt/smart-ai-companion
/opt/smart-ai-companion/venv/bin/python manage.py migrate
/opt/smart-ai-companion/venv/bin/python manage.py collectstatic --noinput
sudo install -m 0644 deployment/systemd/smart-ai-companion-network-mode.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable smart-ai-companion-network-mode.service
sudo systemctl enable NetworkManager.service avahi-daemon.service
sudo systemctl start smart-ai-companion-network-mode.service
```

The provided unit is a bounded 90-second oneshot with no restart loop and no embedded credential. Its executable paths assume `/opt/smart-ai-companion`; adapt the template before installation if the repository or virtual environment lives elsewhere. The web unit should be named `smart-ai-companion-web.service` or otherwise declare an equivalent ordering dependency so the network-mode command completes before Django starts.

The canonical address in both normal and setup modes is:

`http://smart-ai-companion.local:8000/`

The setup page is `/setup/`. NetworkManager may internally allocate an address such as `10.42.0.1`, but DHCP addresses are not product identity and are not generated into dashboard links. Avahi/mDNS must advertise the stable hostname. A temporary `.local` lookup failure is not considered a Wi-Fi failure.

## Recovery and development controls

From an authorized local service session, explicitly enter recovery mode with:

```bash
python manage.py ensure_network_mode --force-setup
```

This intentionally drops the current Wi-Fi client connection. The setup-only APIs are available only while the persisted state is provisioning/recovery; the normal staff-only Wi-Fi endpoint remains unchanged.

To return a development device to a known saved network without deleting any profile, use local console/Ethernet access:

```bash
nmcli connection down "SmartCompanion Setup"
nmcli connection up "<known-profile-name>" ifname wlan0
python manage.py ensure_network_mode --prefer-client
```

`--prefer-client` bypasses a persisted recovery request once, verifies an active client profile, and otherwise restores the setup AP. On Windows, do not invoke this command to test real networking; use the mocked test suite. Setup mode remains dormant in the initial `UNPROVISIONED` database state until the boot command is run.

## Setup security boundary

The setup page has no dashboard navigation, logs, device controls, or arbitrary command surface. While setup mode is active, request isolation redirects ordinary pages to `/setup/` and returns 404 for every non-setup API; only setup routes and local static assets remain reachable. Its three APIs expose status, scanning, and one validated Wi-Fi connection operation. They return 404 outside provisioning mode. The mutation endpoint uses Django CSRF protection and a global configurable cooldown. Passwords are cleared from the browser field before the radio switch and are never echoed in responses. Normal `/api/system/network/wifi/connect/` authorization remains staff-only in normal mode.
