"""Dedicated protected demo AP using the existing bounded NetworkManager adapter."""
import ipaddress
import shutil
from django.conf import settings
from . import network

PROFILE = 'SmartCompanion Demo'
SSID = 'SmartAI-Companion'
GATEWAY = '10.77.0.1'


def _command(*args):
    executable = shutil.which('nmcli')
    if not executable:
        raise RuntimeError('NetworkManager is unavailable.')
    result = network._execute([executable, '--wait', '12', *args], 15)
    if result.returncode != 0:
        # Never include command, stderr or a credential in an error or log.
        raise RuntimeError('Demo NetworkManager operation failed; use local console to inspect its profile.')
    return result.stdout.strip()


def configure(password):
    """Create/update only our inactive profile. Do not switch the radio here."""
    if network._setup_password_error(password):
        raise ValueError('Use a non-placeholder WPA2 password of 8-63 bytes (16+ recommended).')
    executable = shutil.which('nmcli')
    if not executable:
        raise RuntimeError('NetworkManager is unavailable.')
    profiles, state = network._connection_profiles(executable)
    if state != 'OK':
        raise RuntimeError('NetworkManager profiles could not be read.')
    if not any(p['name'] == PROFILE for p in profiles):
        _command('connection', 'add', 'type', 'wifi', 'ifname', 'wlan0', 'con-name', PROFILE,
                 'autoconnect', 'no', 'ssid', SSID)
    _command('connection', 'modify', 'id', PROFILE,
             'connection.autoconnect', 'no', '802-11-wireless.mode', 'ap',
             '802-11-wireless.ssid', SSID, 'ipv4.method', 'shared',
             'ipv4.addresses', GATEWAY + '/24', 'ipv6.method', 'disabled',
             '802-11-wireless-security.key-mgmt', 'wpa-psk',
             '802-11-wireless-security.proto', 'rsn',
             '802-11-wireless-security.psk-flags', '0',
             '802-11-wireless-security.psk', password)
    return configured_gateway()


def configured_gateway():
    address = _command('-g', 'ipv4.addresses', 'connection', 'show', 'id', PROFILE).splitlines()[0]
    actual = str(ipaddress.ip_interface(address).ip)
    if actual != GATEWAY:
        raise RuntimeError('Demo profile gateway does not match the approved Django host configuration.')
    return actual


def activate():
    """Bounded boot operation; missing/insecure configuration fails closed."""
    for field, expected in [('802-11-wireless.mode', 'ap'), ('ipv4.method', 'shared'),
                            ('802-11-wireless.ssid', SSID),
                            ('802-11-wireless-security.key-mgmt', 'wpa-psk'),
                            ('802-11-wireless-security.proto', 'rsn')]:
        if _command('-g', field, 'connection', 'show', 'id', PROFILE) != expected:
            raise RuntimeError('Demo AP is not configured as a protected shared network.')
    # Confirm NM has a stored secret without publishing it. No default/open AP.
    password = _command('--show-secrets', '-g', '802-11-wireless-security.psk', 'connection', 'show', 'id', PROFILE)
    if network._setup_password_error(password):
        raise RuntimeError('Demo AP has no valid stored password; configure it interactively.')
    del password
    configured_gateway()
    _command('connection', 'up', 'id', PROFILE, 'ifname', 'wlan0')
    active = _command('-g', 'GENERAL.CONNECTION', 'device', 'show', 'wlan0')
    if active != PROFILE:
        raise RuntimeError('Demo profile activation could not be verified.')
    address = _command('-g', 'IP4.ADDRESS', 'device', 'show', 'wlan0').splitlines()[0]
    if str(ipaddress.ip_interface(address).ip) != GATEWAY:
        raise RuntimeError('Active hotspot address does not match the configured gateway.')
    return {'success': True, 'state': 'DEMO_AP', 'message': 'Protected offline demo network is active.',
            'gateway': GATEWAY, 'ssid': SSID}
