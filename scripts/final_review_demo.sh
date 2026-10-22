#!/bin/bash
# Explicit operator-run deployment only. No Wi-Fi switch in 'prepare'.
set -euo pipefail
cd /home/jagadish/apps/smart-ai-companion
mode=${1:-}
expected=${2:-}
test "$(id -un)" = jagadish || { echo 'Run as jagadish, using sudo only where requested.'; exit 1; }
test "$(git branch --show-current)" = codex/offline-model-selection
git diff --quiet && git diff --cached --quiet
test -n "$expected" && test "$(git rev-parse HEAD)" = "$expected"
case "$mode" in
prepare)
    command -v nmcli >/dev/null
    command -v pinctrl >/dev/null
    command -v ffmpeg >/dev/null
    if pgrep -f '[p]ython.*scripts/voice_loop.py' >/dev/null; then
        echo 'Stop the existing voice loop/voice service before preparing the demo.'; exit 1
    fi
    test -f .env
    # Preserve production records/settings before any migration or service change.
    backup="$HOME/.cache/smart-ai-companion/demo-backup-$expected"
    install -d -m 700 "$backup"
    if ! test -f "$backup/complete"; then
    cp -p .env "$backup/production.env"
    ./venv/bin/python - "$backup/database.sqlite3" <<'PY'
import sqlite3, sys
with sqlite3.connect('db.sqlite3') as source, sqlite3.connect(sys.argv[1]) as destination:
    source.backup(destination)
PY
    if test -f /etc/smart-ai-companion/demo.env; then sudo cp -p /etc/smart-ai-companion/demo.env "$backup/demo.env"; fi
    for unit in smart-ai-companion smart-ai-companion-network-mode smart-ai-companion-model-warm; do
        if test -f "/etc/systemd/system/$unit.service"; then
            sudo cp -p "/etc/systemd/system/$unit.service" "$backup/$unit.service"
        fi
        if test -f "/etc/systemd/system/$unit.service.d/demo.conf"; then
            sudo cp -p "/etc/systemd/system/$unit.service.d/demo.conf" "$backup/$unit.demo.conf"
        fi
    done
    touch "$backup/complete"
    fi
    printf '%s\n' "$backup" > "$HOME/.cache/smart-ai-companion/last-demo-backup"
    chmod 600 "$HOME/.cache/smart-ai-companion/last-demo-backup" "$backup/production.env" "$backup/database.sqlite3"
    if ! ./venv/bin/python -c 'import lgpio' 2>/dev/null; then
        ./venv/bin/python -m pip install -r requirements-pi-demo.txt
    fi
    # GPIO preflight claims only the chosen input, then releases it. No audio capture.
    pinctrl get 17
    ./venv/bin/python -c 'from assistant.voice.push_to_talk import TalkButton; b=TalkButton(); b.close(); print("RP1 BCM17 input claim/release passed")'
    ./venv/bin/python - <<'PY'
import os
import subprocess
from pathlib import Path
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from django.conf import settings
from assistant.voice.whisper_server import prepare_server_binary
assert settings.SECRET_KEY != 'django-insecure-default-key-for-dev', 'Configure a private Django SECRET_KEY first.'
assert settings.OLLAMA_MODEL == 'qwen3.5:4b-q4_K_M' and settings.OLLAMA_NUM_CTX == 4096, 'Preserve approved model/context.'
assert settings.TTS_ENGINE == 'piper', 'Existing Piper configuration required.'
for asset in (settings.STT_WHISPER_MODEL, settings.TTS_PIPER_BIN, settings.TTS_PIPER_VOICE):
    assert Path(asset).is_file(), 'A required existing local speech asset is missing.'
assert Path(settings.STT_WHISPER_MODEL).name == 'ggml-base.en.bin', 'Keep the approved existing base.en model.'
binary = prepare_server_binary(settings.STT_WHISPER_BIN)
help_result = subprocess.run([str(binary), '--help'], capture_output=True, timeout=5)
help_text = help_result.stdout + help_result.stderr
assert all(flag in help_text for flag in (b'--host', b'--port', b'--model', b'--no-context')), 'Installed whisper-server has incompatible flags.'
PY
    sudo install -d -o root -g jagadish -m 0750 /etc/smart-ai-companion
    sudo install -o root -g jagadish -m 0640 deployment/demo.env.example /etc/smart-ai-companion/demo.env
    set -a; . /etc/smart-ai-companion/demo.env; set +a
    ./venv/bin/python manage.py migrate --noinput
    ./venv/bin/python manage.py collectstatic --noinput
    ./venv/bin/python manage.py check
    if ! ./venv/bin/python manage.py shell -c 'from django.contrib.auth import get_user_model; assert get_user_model().objects.filter(is_active=True,is_staff=True).exists()' >/dev/null 2>&1; then
        ./venv/bin/python manage.py createsuperuser
    fi
    sudo install -o root -g root -m 0644 deployment/polkit/49-smart-ai-companion-network.rules /etc/polkit-1/rules.d/49-smart-ai-companion-network.rules
    ./venv/bin/python manage.py configure_demo_ap
    # Modify no saved client profile, activate no radio connection here.
    for unit in smart-ai-companion smart-ai-companion-network-mode smart-ai-companion-model-warm; do
        sudo install -o root -g root -m 0644 "deployment/systemd/$unit.service" "/etc/systemd/system/$unit.service"
        sudo install -d -m 0755 "/etc/systemd/system/$unit.service.d"
        sudo install -o root -g root -m 0644 deployment/systemd/demo.conf "/etc/systemd/system/$unit.service.d/demo.conf"
        if test "$unit" = smart-ai-companion-model-warm; then
            printf '\nRemainAfterExit=yes\n' | sudo tee -a "/etc/systemd/system/$unit.service.d/demo.conf" >/dev/null
        fi
    done
    for unit in smart-ai-companion-voice smart-ai-companion-stt; do
        sudo install -o root -g root -m 0644 "deployment/systemd/$unit.service" "/etc/systemd/system/$unit.service"
    done
    sudo systemctl daemon-reload
    sudo systemctl enable NetworkManager.service avahi-daemon.service ollama.service smart-ai-companion-network-mode.service smart-ai-companion-model-warm.service smart-ai-companion.service smart-ai-companion-stt.service smart-ai-companion-voice.service
    systemd-analyze verify /etc/systemd/system/smart-ai-companion{,-network-mode,-model-warm,-stt,-voice}.service
    echo 'Prepared; Wi-Fi has NOT been switched. Continue activate ONLY on local console or wired Ethernet.'
    ;;
activate)
    # This intentional boundary can disconnect Wi-Fi SSH. An explicit TTY acknowledgement is required.
    test -t 0 || { echo 'Activation requires an interactive console.'; exit 1; }
    read -r -p 'Using LOCAL CONSOLE or WIRED ETHERNET recovery? Type DEMO-AP: ' answer
    test "$answer" = DEMO-AP || exit 1
    # Do not expose a previously loaded, pre-demo middleware process on the new AP.
    sudo systemctl stop smart-ai-companion.service
    sudo systemctl restart smart-ai-companion-network-mode.service
    sudo systemctl start avahi-daemon.service ollama.service
    sudo systemctl restart smart-ai-companion-stt.service smart-ai-companion-model-warm.service smart-ai-companion.service smart-ai-companion-voice.service
    gateway=$(nmcli -g ipv4.addresses connection show 'SmartCompanion Demo')
    active=$(nmcli -g GENERAL.CONNECTION device show wlan0)
    test "$active" = 'SmartCompanion Demo' && test "$gateway" = '10.77.0.1/24'
    nmcli -g IP4.ADDRESS device show wlan0
    curl --fail --max-time 5 -s -o /dev/null http://127.0.0.1:8000/login/
    sudo systemctl --no-pager --full status smart-ai-companion{,-network-mode,-stt,-voice}.service
    echo 'Connect to SmartAI-Companion; sign in at http://10.77.0.1:8000/ or http://smart-ai-companion.local:8000/'
    echo 'After the supervised checks, reboot with: sudo systemctl reboot'
    ;;
rollback)
    test -t 0 || exit 1
    read -r -p 'Using LOCAL CONSOLE or WIRED ETHERNET? Type ROLLBACK: ' answer
    test "$answer" = ROLLBACK || exit 1
    sudo systemctl disable --now smart-ai-companion-voice.service smart-ai-companion-stt.service
    backup=$(cat "$HOME/.cache/smart-ai-companion/last-demo-backup")
    case "$backup" in "$HOME/.cache/smart-ai-companion/demo-backup-"*) ;; *) echo 'Invalid backup location'; exit 1;; esac
    for unit in smart-ai-companion smart-ai-companion-network-mode smart-ai-companion-model-warm; do
        if test -f "$backup/$unit.demo.conf"; then
            sudo install -o root -g root -m 0644 "$backup/$unit.demo.conf" "/etc/systemd/system/$unit.service.d/demo.conf"
        else
            sudo rm -f "/etc/systemd/system/$unit.service.d/demo.conf"
        fi
        if test -f "$backup/$unit.service"; then
            sudo install -o root -g root -m 0644 "$backup/$unit.service" "/etc/systemd/system/$unit.service"
        fi
    done
    sudo systemctl daemon-reload
    nmcli connection down 'SmartCompanion Demo' || true
    sudo systemctl stop smart-ai-companion-network-mode.service
    ./venv/bin/python manage.py ensure_network_mode --prefer-client
    sudo systemctl restart smart-ai-companion.service
    echo 'Demo profile retained but inactive; original client/setup and CLI speech configuration restored.'
    echo 'Database was NOT reverted or deleted. Saved Wi-Fi profiles and production .env were preserved.'
    ;;
*) echo 'Usage: bash scripts/final_review_demo.sh prepare|activate|rollback EXACT_COMMIT_SHA'; exit 2 ;;
esac
