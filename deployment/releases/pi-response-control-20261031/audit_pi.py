"""Read-only live acceptance information. Never opens UART or changes services."""
import json
from pathlib import Path
import shutil
import subprocess

UNITS = ['smart-ai-companion', 'smart-ai-companion-voice',
         'smart-ai-companion-network-mode', 'smart-ai-companion-model-warm',
         'smart-ai-companion-stt', 'ollama', 'mosquitto']


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else '<unavailable>'
    except (OSError, subprocess.TimeoutExpired):
        return '<unavailable>'


def main():
    if not shutil.which('systemctl'):
        raise SystemExit('Run this read-only audit on the Raspberry Pi.')
    services = {}
    for unit in UNITS:
        properties = command(['systemctl', 'show', unit + '.service',
            '-p', 'LoadState', '-p', 'ActiveState', '-p', 'SubState', '-p', 'Result',
            '-p', 'UnitFileState', '-p', 'After', '-p', 'Before', '-p', 'Requires',
            '-p', 'Wants', '-p', 'MainPID', '-p', 'EnvironmentFiles', '-p', 'FragmentPath',
            '-p', 'DropInPaths'])
        services[unit] = dict(line.split('=', 1) for line in properties.splitlines() if '=' in line)
    print(json.dumps({'services': services}, indent=2))
    # Inspect only allowlisted values from the existing service process, not its
    # complete environment or environment-file contents. Values are never written.
    allowed = {'AI_ENGINE', 'OLLAMA_MODEL', 'DEMO_AP_ENABLED', 'ONLINE_PROVIDER',
               'ONLINE_RETRIEVAL_ENABLED', 'STT_ENGINE', 'TTS_ENGINE',
               'COMPANION_DISPLAY_ENABLED', 'COMPANION_DISPLAY_PORT'}
    for unit in ('smart-ai-companion', 'smart-ai-companion-voice'):
        pid = services.get(unit, {}).get('MainPID', '0')
        if not pid.isdigit() or pid == '0':
            continue
        try:
            entries = Path('/proc', pid, 'environ').read_bytes().split(b'\0')
            values = {}
            for entry in entries:
                key, separator, value = entry.partition(b'=')
                name = key.decode('ascii', errors='replace')
                if separator and name in allowed:
                    values[name] = value.decode('utf-8', errors='replace')
            print(json.dumps({'unit': unit, 'startup_environment': values}, indent=2))
            if unit.endswith('-voice') and values.get('COMPANION_DISPLAY_PORT'):
                port = values['COMPANION_DISPLAY_PORT']
                print('Existing UART owner PIDs:', command(['fuser', '--', port]))
        except OSError:
            print(unit + ': startup environment not readable as this user.')
    print('Dashboard listeners:', command(['ss', '-H', '-ltn', 'sport = :8000']))
    print('Active wlan0 profile:', command(['nmcli', '-g', 'GENERAL.CONNECTION', 'device', 'show', 'wlan0']))
    print('Saved demo gateway:', command(['nmcli', '-g', 'ipv4.addresses', 'connection', 'show', 'id', 'SmartCompanion Demo']))
    print('Wi-Fi capabilities (read-only; capability advertising does not prove coexistence):')
    print(command(['iw', 'list']))
    print('No services, network profiles, environment files or UART state were changed.')


if __name__ == '__main__':
    main()
