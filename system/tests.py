import inspect
import subprocess
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status

from .control_plane import health, network, resources
from .models import CompanionState

class SystemAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.control_url = '/api/system/companion/control/'
        self.state_url = '/api/system/companion/state/'
        self.metrics_url = '/api/system/metrics/'
        self.network_url = '/api/system/network/'
        self.wifi_scan_url = '/api/system/network/wifi/scan/'
        self.wifi_connect_url = '/api/system/network/wifi/connect/'
        self.services_url = '/api/system/services/'

    def test_valid_companion_command(self):
        response = self.client.post(self.control_url, {'action': 'set_state', 'value': 'THINKING'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['state'], 'THINKING') # verify returning full state
        
        # Verify state changed in DB
        state_response = self.client.get(self.state_url)
        self.assertEqual(state_response.data['state'], 'THINKING')

    def test_other_companion_commands_persist(self):
        self.client.post(self.control_url, {'action': 'set_expression', 'value': 'HAPPY'}, format='json')
        self.client.post(self.control_url, {'action': 'set_eye_style', 'value': 'SLIT'}, format='json')
        self.client.post(self.control_url, {'action': 'set_animation', 'value': 'SCAN'}, format='json')
        self.client.post(self.control_url, {'action': 'set_brightness', 'value': 45}, format='json')
        self.client.post(self.control_url, {'action': 'set_display_text', 'value': 'Test Text'}, format='json')

        state_response = self.client.get(self.state_url)
        data = state_response.data
        self.assertEqual(data['expression'], 'HAPPY')
        self.assertEqual(data['eye_style'], 'SLIT')
        self.assertEqual(data['animation'], 'SCAN')
        self.assertEqual(data['brightness'], 45)
        self.assertEqual(data['display_text'], 'Test Text')

    def test_invalid_companion_command(self):
        response = self.client.post(self.control_url, {'action': 'invalid_action', 'value': 'blah'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_device_metrics_format(self):
        response = self.client.get(self.metrics_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_keys = [
            'device_name', 'status', 'hostname', 'platform', 'architecture',
            'cpu_count', 'cpu_percent', 'uptime_seconds', 'ram_percent',
            'ram_used_gb', 'ram_total_gb', 'ram_available_gb',
            'storage_percent', 'storage_total_gb', 'storage_used_gb',
            'storage_free_gb', 'temperature_c', 'load_1m', 'load_5m',
            'load_15m', 'throttled', 'network', 'audio_status',
            'display_status'
        ]
        for key in expected_keys:
            self.assertIn(key, response.data)

        for key in ('cpu_percent', 'ram_percent', 'storage_percent'):
            value = response.data[key]
            self.assertTrue(value is None or 0 <= value <= 100)

        self.assertNotEqual(response.data['network'], 'CONNECTED')
        self.assertNotEqual(response.data['audio_status'], 'READY')
        self.assertNotEqual(response.data['display_status'], 'READY')

    @patch('system.views.get_network_status')
    def test_network_endpoint_degrades_without_nmcli(self, get_network_status):
        get_network_status.return_value = {
            'hostname': 'dev-host',
            'wifi': {
                'supported': False, 'connected': False, 'interface': None,
                'ssid': None, 'signal_percent': None, 'ip_address': None,
            },
            'ethernet': {
                'supported': False, 'connected': False, 'interface': None,
                'ip_address': None,
            },
            'interfaces': [],
            'default_route': None,
            'internet': {'state': 'UNKNOWN', 'available': None},
        }
        response = self.client.get(self.network_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('password', str(response.data).lower())

    @patch('system.views.get_service_health')
    def test_services_endpoint_survives_unavailable_services(self, get_service_health):
        get_service_health.return_value = {
            name: {
                'status': 'READY' if name == 'django' else 'UNAVAILABLE',
                'latency_ms': 1,
                'detail': 'bounded check result',
            }
            for name in ('django', 'ollama', 'searxng', 'mosquitto')
        }
        response = self.client.get(self.services_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['django']['status'], 'READY')
        self.assertEqual(response.data['ollama']['status'], 'UNAVAILABLE')

    @patch('system.views.scan_wifi_networks')
    def test_wifi_scan_endpoint_returns_normalized_response(self, scan):
        scan.return_value = {
            'supported': True,
            'interface': 'wlan0',
            'networks': [{
                'ssid': 'Home WiFi',
                'signal_percent': 82,
                'security': 'WPA2',
                'connected': False,
            }],
        }

        response = self.client.get(self.wifi_scan_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['interface'], 'wlan0')
        self.assertEqual(response.data['networks'][0]['ssid'], 'Home WiFi')

    @patch('system.views.connect_wifi')
    @patch('system.views.scan_wifi_networks')
    def test_unicode_ssid_round_trips_from_scan_response_to_connect_request(
        self, scan, connect
    ):
        ssid = 'Sravani\u2019s iPhone'
        scan.return_value = {
            'supported': True,
            'interface': 'wlan0',
            'networks': [{
                'ssid': ssid,
                'signal_percent': 72,
                'security': 'WPA2',
                'connected': False,
            }],
        }
        connect.return_value = {
            'success': True,
            'ssid': ssid,
            'state': 'CONNECTED',
            'message': 'Connected successfully.',
        }
        user = get_user_model().objects.create_user(
            username='unicode-network-admin',
            password='login-password',
            is_staff=True,
        )
        self.client.force_authenticate(user=user)

        scan_response = self.client.get(self.wifi_scan_url)
        selected_ssid = scan_response.data['networks'][0]['ssid']
        connect_response = self.client.post(
            self.wifi_connect_url,
            {'ssid': selected_ssid, 'password': 'private-password'},
            format='json',
        )

        self.assertEqual(selected_ssid, ssid)
        self.assertEqual(connect_response.status_code, status.HTTP_200_OK)
        connect.assert_called_once_with(ssid, 'private-password')
        self.assertNotIn('private-password', str(connect_response.data))

    @patch('system.views.connect_wifi')
    def test_anonymous_user_cannot_connect_wifi(self, connect):
        response = self.client.post(
            self.wifi_connect_url,
            {'ssid': 'Home WiFi', 'password': 'private-password'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        connect.assert_not_called()

    @patch('system.views.connect_wifi')
    def test_staff_user_reaches_wifi_service_without_password_response(self, connect):
        user = get_user_model().objects.create_user(
            username='network-admin',
            password='login-password',
            is_staff=True,
        )
        self.client.force_authenticate(user=user)
        connect.return_value = {
            'success': True,
            'ssid': 'Home WiFi',
            'state': 'CONNECTED',
            'message': 'Connected successfully.',
        }

        response = self.client.post(
            self.wifi_connect_url,
            {'ssid': 'Home WiFi', 'password': 'private-password'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        connect.assert_called_once_with('Home WiFi', 'private-password')
        self.assertNotIn('private-password', str(response.data))

    def test_connect_request_validation_rejects_malformed_input(self):
        user = get_user_model().objects.create_user(
            username='validation-admin',
            password='login-password',
            is_staff=True,
        )
        self.client.force_authenticate(user=user)

        response = self.client.post(
            self.wifi_connect_url,
            {'ssid': '', 'password': {'not': 'text'}},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['state'], 'INVALID')

    @patch('system.views.connect_wifi')
    def test_connection_exception_never_leaks_password_or_raw_error(self, connect):
        user = get_user_model().objects.create_user(
            username='error-admin',
            password='login-password',
            is_staff=True,
        )
        self.client.force_authenticate(user=user)
        connect.side_effect = RuntimeError(
            'nmcli failed with private-password and return code 10'
        )

        response = self.client.post(
            self.wifi_connect_url,
            {'ssid': 'Home WiFi', 'password': 'private-password'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        payload = str(response.data).lower()
        self.assertNotIn('private-password', payload)
        self.assertNotIn('return code', payload)


class ResourceTelemetryTests(SimpleTestCase):
    def test_no_random_telemetry_implementation_remains(self):
        self.assertNotIn('random', inspect.getsource(resources))

    @patch('system.control_plane.resources.shutil.which', return_value=None)
    @patch('system.control_plane.resources._read_text', return_value=None)
    def test_linux_specific_files_and_commands_degrade_safely(self, _read_text, _which):
        self.assertIsNone(resources._temperature_c())
        self.assertIsNone(resources._throttling())
        self.assertIsNone(resources._linux_cpu_times())


class NetworkTelemetryTests(SimpleTestCase):
    @patch('system.control_plane.network._fallback_interfaces', return_value=[])
    @patch('system.control_plane.network.shutil.which', return_value=None)
    def test_nmcli_unavailable_returns_honest_response(self, _which, _interfaces):
        result = network.get_network_status()
        self.assertFalse(result['wifi']['supported'])
        self.assertFalse(result['wifi']['connected'])
        self.assertEqual(result['internet']['state'], 'UNKNOWN')
        self.assertIsNone(result['internet']['available'])
        self.assertNotIn('password', str(result).lower())

    def test_terse_output_handles_escaped_ssid_separator(self):
        self.assertEqual(
            network._split_terse(r'*:Office\:Lab:78'),
            ['*', 'Office:Lab', '78'],
        )

    @patch('system.control_plane.network._run')
    def test_primary_ethernet_prefers_physical_interface(self, run):
        def command_result(command):
            fields = command[command.index('-f') + 1]
            if fields == 'DEVICE,TYPE,STATE,CONNECTION':
                return (
                    'veth3474c7f:ethernet:connected:container\n'
                    'docker0:ethernet:connected:docker0\n'
                    'eth0:ethernet:disconnected:--'
                )
            if fields == 'IP4.ADDRESS':
                return 'IP4.ADDRESS[1]:172.17.0.1/16'
            return None

        run.side_effect = command_result

        result = network._from_nmcli('/usr/bin/nmcli')

        self.assertTrue(result['ethernet']['supported'])
        self.assertFalse(result['ethernet']['connected'])
        self.assertEqual(result['ethernet']['interface'], 'eth0')
        self.assertIsNone(result['ethernet']['ip_address'])
        self.assertEqual(
            [item['interface'] for item in result['interfaces']],
            ['veth3474c7f', 'docker0', 'eth0'],
        )

    @patch('system.control_plane.network._run')
    def test_virtual_ethernet_remains_detailed_but_not_primary(self, run):
        def command_result(command):
            fields = command[command.index('-f') + 1]
            if fields == 'DEVICE,TYPE,STATE,CONNECTION':
                return 'veth3474c7f:ethernet:connected:container'
            if fields == 'IP4.ADDRESS':
                return 'IP4.ADDRESS[1]:172.18.0.2/16'
            return None

        run.side_effect = command_result

        result = network._from_nmcli('/usr/bin/nmcli')

        self.assertFalse(result['ethernet']['supported'])
        self.assertFalse(result['ethernet']['connected'])
        self.assertIsNone(result['ethernet']['interface'])
        self.assertEqual(result['interfaces'][0]['interface'], 'veth3474c7f')


class WifiNetworkControlTests(SimpleTestCase):
    @patch('system.control_plane.network.shutil.which', return_value=None)
    def test_scan_without_nmcli_is_unsupported(self, _which):
        result = network.scan_wifi_networks()

        self.assertFalse(result['supported'])
        self.assertIsNone(result['interface'])
        self.assertEqual(result['networks'], [])

    def test_scan_deduplicates_by_strongest_signal_and_preserves_connected(self):
        output = (
            '*:Home WiFi:42:WPA2\n'
            ':Home WiFi:88:WPA3\n'
            ':Sravani\u2019s iPhone:72:WPA2\n'
            ":O'Brien WiFi:64:WPA2\n"
            ':Cafe\\:Main:67:WPA1 WPA2\n'
            r':Lab\\Net:54:--' '\n'
            ':Caf\u00e9 \u6771\u4eac:51:WPA3\n'
            ':   :90:WPA2'
        )

        result = network._parse_wifi_networks(output)

        by_ssid = {item['ssid']: item for item in result}
        self.assertEqual(len(result), 6)
        self.assertEqual(by_ssid['Home WiFi']['signal_percent'], 88)
        self.assertEqual(by_ssid['Home WiFi']['security'], 'WPA3')
        self.assertTrue(by_ssid['Home WiFi']['connected'])
        self.assertEqual(by_ssid['Sravani\u2019s iPhone']['signal_percent'], 72)
        self.assertIn("O'Brien WiFi", by_ssid)
        self.assertEqual(by_ssid['Cafe:Main']['security'], 'WPA1 WPA2')
        self.assertEqual(by_ssid[r'Lab\Net']['security'], 'OPEN')
        self.assertIn('Caf\u00e9 \u6771\u4eac', by_ssid)

    @patch('system.control_plane.network._wifi_interface', return_value=('wlan0', 'OK'))
    @patch('system.control_plane.network.shutil.which', return_value='/usr/bin/nmcli')
    @patch('system.control_plane.network.subprocess.run')
    def test_scan_uses_bounded_argument_array(self, run, _which, _interface):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='*:Home WiFi:75:WPA2\n',
            stderr='',
        )

        result = network.scan_wifi_networks()

        self.assertTrue(result['supported'])
        self.assertEqual(result['networks'][0]['ssid'], 'Home WiFi')
        command = run.call_args.args[0]
        self.assertIsInstance(command, list)
        self.assertEqual(command[0], '/usr/bin/nmcli')
        self.assertIn('--rescan', command)
        self.assertFalse(run.call_args.kwargs['shell'])
        self.assertEqual(
            run.call_args.kwargs['timeout'],
            network.WIFI_SCAN_TIMEOUT_SECONDS,
        )

    def test_connection_validation_rejects_unsafe_values(self):
        cases = (
            (None, None),
            ('', None),
            ('-' + 'option', None),
            ('x' * 33, None),
            ('Home WiFi', {'not': 'text'}),
            ('Home WiFi', 'x' * 65),
            ('Home\nWiFi', 'valid-password'),
        )
        for ssid, password in cases:
            with self.subTest(ssid=ssid, password_type=type(password).__name__):
                result = network.connect_wifi(ssid, password)
                self.assertEqual(result['state'], 'INVALID')
                self.assertFalse(result['success'])

    @patch('system.control_plane.network._wifi_interface', return_value=('wlan0', 'OK'))
    @patch('system.control_plane.network.shutil.which', return_value='/usr/bin/nmcli')
    @patch('system.control_plane.network.subprocess.run')
    def test_connect_uses_bounded_argument_array(self, run, _which, _interface):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='success', stderr=''
        )

        result = network.connect_wifi('Home WiFi', 'private-password')

        self.assertTrue(result['success'])
        self.assertEqual(result['state'], 'CONNECTED')
        self.assertNotIn('private-password', str(result))
        command = run.call_args.args[0]
        self.assertIsInstance(command, list)
        self.assertEqual(command[-2:], ['password', 'private-password'])
        self.assertFalse(run.call_args.kwargs['shell'])
        self.assertEqual(
            run.call_args.kwargs['timeout'],
            network.WIFI_CONNECT_TIMEOUT_SECONDS,
        )

    @patch('system.control_plane.network._wifi_interface', return_value=('wlan0', 'OK'))
    @patch('system.control_plane.network.shutil.which', return_value='/usr/bin/nmcli')
    @patch('system.control_plane.network.subprocess.run')
    def test_connect_preserves_ssid_characters_exactly(self, run, _which, _interface):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='success', stderr=''
        )
        ssids = (
            'Sravani\u2019s iPhone',
            "Sravani's iPhone",
            'Home WiFi',
            r'Cafe:Main\Lab',
            'Caf\u00e9 \u6771\u4eac',
        )

        for ssid in ssids:
            with self.subTest(ssid=ssid):
                result = network.connect_wifi(ssid, 'private-password')
                command = run.call_args.args[0]

                self.assertTrue(result['success'])
                self.assertEqual(result['ssid'], ssid)
                self.assertIn(ssid, command)
                self.assertEqual(command[command.index('connect') + 1], ssid)
                self.assertNotIn('private-password', str(result))

    @patch('system.control_plane.network._wifi_interface', return_value=('wlan0', 'OK'))
    @patch('system.control_plane.network.shutil.which', return_value='/usr/bin/nmcli')
    @patch('system.control_plane.network.subprocess.run')
    def test_missing_ap_cache_is_refreshed_once_without_scan_result_gate(
        self, run, _which, _interface
    ):
        ssid = 'Sravani\u2019s iPhone'
        run.side_effect = (
            subprocess.CompletedProcess(
                args=[],
                returncode=10,
                stdout='',
                stderr=f"Error: No network with SSID '{ssid}' found.",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=':Unrelated Network:45:WPA2\n',
                stderr='',
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout='success', stderr=''
            ),
        )

        result = network.connect_wifi(ssid, 'private-password')

        self.assertTrue(result['success'])
        self.assertEqual(result['ssid'], ssid)
        self.assertEqual(run.call_count, 3)
        first_command = run.call_args_list[0].args[0]
        refresh_command = run.call_args_list[1].args[0]
        retry_command = run.call_args_list[2].args[0]
        self.assertEqual(first_command, retry_command)
        self.assertEqual(first_command[first_command.index('connect') + 1], ssid)
        self.assertIn('--rescan', refresh_command)
        self.assertIn('yes', refresh_command)
        self.assertNotIn('private-password', refresh_command)
        self.assertNotIn('private-password', str(result))
        self.assertEqual(
            run.call_args_list[1].kwargs['timeout'],
            network.WIFI_RETRY_SCAN_TIMEOUT_SECONDS,
        )

    @patch('system.control_plane.network._wifi_interface', return_value=('wlan0', 'OK'))
    @patch('system.control_plane.network.shutil.which', return_value='/usr/bin/nmcli')
    @patch('system.control_plane.network.subprocess.run')
    def test_non_network_not_found_error_is_not_misreported_or_retried(
        self, run, _which, _interface
    ):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=10,
            stdout='',
            stderr="Error: Device 'wlan0' not found.",
        )

        result = network.connect_wifi('Home WiFi', 'private-password')

        self.assertFalse(result['success'])
        self.assertEqual(result['state'], 'FAILED')
        self.assertEqual(result['message'], 'Could not connect to the selected network.')
        self.assertEqual(run.call_count, 1)

    def test_network_not_found_detection_covers_nmcli_failure_variants(self):
        self.assertTrue(
            network._network_not_found(
                "Error: No network with SSID 'Home WiFi' found."
            )
        )
        self.assertTrue(
            network._network_not_found(
                'Connection activation failed: (53) '
                'The Wi-Fi network could not be found.'
            )
        )
        self.assertFalse(network._network_not_found("Device 'wlan0' not found."))

    @patch('system.control_plane.network._wifi_interface', return_value=('wlan0', 'OK'))
    @patch('system.control_plane.network.shutil.which', return_value='/usr/bin/nmcli')
    @patch('system.control_plane.network.subprocess.run')
    def test_subprocess_timeout_returns_safe_unknown_state(
        self, run, _which, _interface
    ):
        run.side_effect = subprocess.TimeoutExpired('nmcli', 10)

        result = network.connect_wifi('Home WiFi', 'private-password')

        self.assertFalse(result['success'])
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertNotIn('private-password', str(result))

    @patch('system.control_plane.network._wifi_interface', return_value=('wlan0', 'OK'))
    @patch('system.control_plane.network.shutil.which', return_value='/usr/bin/nmcli')
    @patch('system.control_plane.network.subprocess.run')
    def test_nmcli_wait_timeout_returns_connecting_state(
        self, run, _which, _interface
    ):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=3, stdout='', stderr='operation timed out'
        )

        result = network.connect_wifi('Home WiFi', 'private-password')

        self.assertFalse(result['success'])
        self.assertEqual(result['state'], 'CONNECTING')
        self.assertNotIn('private-password', str(result))

    @patch('system.control_plane.network._wifi_interface', return_value=('wlan0', 'OK'))
    @patch('system.control_plane.network.shutil.which', return_value='/usr/bin/nmcli')
    @patch('system.control_plane.network.subprocess.run')
    def test_connect_failure_maps_raw_error_to_safe_message(
        self, run, _which, _interface
    ):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=10,
            stdout='',
            stderr='Error: private-password was rejected; secrets were required.',
        )

        result = network.connect_wifi('Home WiFi', 'private-password')

        self.assertFalse(result['success'])
        self.assertEqual(result['state'], 'FAILED')
        self.assertEqual(
            result['message'],
            'Could not authenticate with the selected network.',
        )
        self.assertNotIn('private-password', str(result))
        self.assertNotIn('secrets were required', str(result))


class ServiceHealthTests(SimpleTestCase):
    @override_settings(CONTROL_PLANE_HEALTH_TIMEOUT_SECONDS=99)
    @patch('system.control_plane.health.socket.create_connection')
    @patch('system.control_plane.health.request.urlopen')
    def test_failures_are_isolated_and_timeouts_are_bounded(
        self, urlopen, create_connection
    ):
        urlopen.side_effect = OSError('service unavailable')
        create_connection.side_effect = OSError('broker unavailable')

        result = health.get_service_health()

        self.assertEqual(result['django']['status'], 'READY')
        self.assertEqual(result['ollama']['status'], 'UNAVAILABLE')
        self.assertEqual(result['searxng']['status'], 'UNAVAILABLE')
        self.assertEqual(result['mosquitto']['status'], 'UNAVAILABLE')
        for call in urlopen.call_args_list:
            self.assertLessEqual(call.kwargs['timeout'], health.MAX_TIMEOUT_SECONDS)
        self.assertLessEqual(
            create_connection.call_args.kwargs['timeout'],
            health.MAX_TIMEOUT_SECONDS,
        )

    @patch('system.control_plane.health.socket.create_connection')
    @patch('system.control_plane.health.request.urlopen')
    def test_ready_checks_return_expected_shape(self, urlopen, create_connection):
        response = MagicMock()
        response.getcode.return_value = 200
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        urlopen.return_value = response
        create_connection.return_value = MagicMock(
            __enter__=MagicMock(), __exit__=MagicMock(return_value=False)
        )

        result = health.get_service_health()

        for name in ('django', 'ollama', 'searxng', 'mosquitto'):
            self.assertEqual(result[name]['status'], 'READY')
            self.assertIn('latency_ms', result[name])
            self.assertIn('detail', result[name])
