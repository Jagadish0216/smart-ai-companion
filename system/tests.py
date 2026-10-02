import inspect
from unittest.mock import MagicMock, patch

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
