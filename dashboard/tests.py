import json
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings


class AssistantChatTimeoutTests(TestCase):
    @override_settings(CHAT_REQUEST_TIMEOUT_SECONDS=45)
    def test_chat_post_timeout_is_configurable(self):
        response = self.client.get('/assistant/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'const CHAT_REQUEST_TIMEOUT_MS = 45000;')
        self.assertContains(response, 'new AbortController()')
        self.assertContains(response, 'signal: controller.signal')
        self.assertContains(response, 'AI request timed out. Please try again.')


class NetworkDashboardTests(TestCase):
    def test_network_script_submits_selected_ssid_value_without_reencoding(self):
        script = (
            Path(settings.BASE_DIR) / 'static' / 'js' / 'network.js'
        ).read_text(encoding='utf-8')

        self.assertIn('name.textContent = network.ssid;', script)
        self.assertIn('selectedSsid = network.ssid;', script)
        self.assertIn('selectedSsidLabel.textContent = network.ssid;', script)
        self.assertIn(
            'JSON.stringify({ssid: selectedSsid, password: password})',
            script,
        )

    def test_network_page_contains_status_scan_and_connection_controls(self):
        user = get_user_model().objects.create_user(
            username='dashboard-admin',
            password='login-password',
            is_staff=True,
        )
        self.client.force_login(user)

        response = self.client.get('/network/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="network-wifi-state"')
        self.assertContains(response, 'id="network-ethernet-state"')
        self.assertContains(response, 'id="network-internet-state"')
        self.assertContains(response, 'Refresh Networks')
        self.assertContains(response, 'id="network-connect-form"')
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assertContains(response, 'temporarily disconnect this dashboard')
        self.assertContains(response, '/static/js/network.js')

    def test_anonymous_network_page_explains_admin_requirement(self):
        response = self.client.get('/network/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Administrator access required')
        self.assertNotContains(response, 'id="network-connect-form"')

    @patch('system.views.connect_wifi')
    def test_session_connection_flow_requires_csrf(self, connect):
        user = get_user_model().objects.create_user(
            username='csrf-admin',
            password='login-password',
            is_staff=True,
        )
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        page_response = client.get('/network/')
        csrf_token = page_response.cookies['csrftoken'].value
        ssid = 'Sravani\u2019s iPhone'
        payload = json.dumps({
            'ssid': ssid,
            'password': 'private-password',
        })

        rejected = client.post(
            '/api/system/network/wifi/connect/',
            data=payload,
            content_type='application/json',
        )
        self.assertEqual(rejected.status_code, 403)
        connect.assert_not_called()

        connect.return_value = {
            'success': True,
            'ssid': ssid,
            'state': 'CONNECTED',
            'message': 'Connected successfully.',
        }
        accepted = client.post(
            '/api/system/network/wifi/connect/',
            data=payload,
            content_type='application/json',
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(accepted.status_code, 200)
        connect.assert_called_once_with(ssid, 'private-password')
        self.assertEqual(accepted.json()['ssid'], ssid)
        self.assertNotIn('private-password', accepted.content.decode())
