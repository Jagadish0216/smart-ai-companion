import json
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from system.models import NetworkProvisioningState

from .context_processors import HEADER_INTERNET_CACHE_KEY


class AssistantChatTimeoutTests(TestCase):
    @override_settings(CHAT_REQUEST_TIMEOUT_SECONDS=45)
    def test_chat_post_timeout_is_configurable(self):
        response = self.client.get('/assistant/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'const CHAT_REQUEST_TIMEOUT_MS = 45000;')
        self.assertContains(response, 'new AbortController()')
        self.assertContains(response, 'signal: controller.signal')
        self.assertContains(response, 'AI request timed out. Please try again.')


class GlobalHeaderStateTests(TestCase):
    def setUp(self):
        cache.delete(HEADER_INTERNET_CACHE_KEY)

    def tearDown(self):
        cache.delete(HEADER_INTERNET_CACHE_KEY)

    @override_settings(AI_ENGINE='local', OLLAMA_MODEL='llama3.2:3b-test')
    @patch('dashboard.context_processors.get_internet_status')
    def test_header_uses_configured_engine_and_model_on_all_headers(
        self, internet_status
    ):
        internet_status.return_value = {'state': 'FULL', 'available': True}

        for url in ('/', '/assistant/', '/network/'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    'id="global-ai-engine">LOCAL AI</span>',
                )
                self.assertContains(
                    response,
                    'id="global-ai-model">llama3.2:3b-test</span>',
                )
                self.assertNotContains(response, 'llama3.2:1b')

        self.assertEqual(internet_status.call_count, 1)

    @override_settings(AI_ENGINE='local', OLLAMA_MODEL='configured-model')
    @patch('dashboard.context_processors.get_internet_status')
    def test_header_maps_internet_state_without_conflating_local_ai(
        self, internet_status
    ):
        cases = (
            ('FULL', 'ONLINE', 'online'),
            ('NONE', 'OFFLINE', 'offline'),
            ('UNKNOWN', 'UNKNOWN', 'unknown'),
        )

        for state, label, css_class in cases:
            with self.subTest(state=state):
                cache.delete(HEADER_INTERNET_CACHE_KEY)
                internet_status.return_value = {'state': state, 'available': None}

                response = self.client.get('/network/')

                self.assertContains(
                    response,
                    f'class="status-pill {css_class}" id="global-internet-status"',
                )
                self.assertContains(
                    response,
                    f'data-network-state="{state}"',
                )
                self.assertContains(
                    response,
                    f'id="global-internet-label">{label}</span>',
                )
                self.assertContains(
                    response,
                    'id="global-ai-engine">LOCAL AI</span>',
                )

        self.assertEqual(internet_status.call_count, len(cases))


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


class ResourceManagerDashboardTests(TestCase):
    def setUp(self):
        self.provisioning_state = NetworkProvisioningState.get_current()
        self.provisioning_state.state = NetworkProvisioningState.State.NORMAL_MODE
        self.provisioning_state.save(update_fields=['state', 'updated_at'])

    def test_resource_manager_page_exposes_product_facing_health_sections(self):
        response = self.client.get('/resource-manager/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'System Health &amp; Resource Manager')
        self.assertContains(response, 'id="resource-overall-health"')
        self.assertContains(response, 'id="resource-profile"')
        self.assertContains(response, 'id="resource-cpu-value"')
        self.assertContains(response, 'id="resource-memory-value"')
        self.assertContains(response, 'id="resource-storage-value"')
        self.assertContains(response, 'id="resource-temperature-value"')
        self.assertContains(response, 'id="resource-network-value"')
        self.assertContains(response, 'id="resource-ai-value"')
        self.assertContains(response, '/static/js/resource_manager.js')

    def test_resource_manager_script_uses_live_api_and_honest_unknown_values(self):
        script = (
            Path(settings.BASE_DIR) / 'static' / 'js' / 'resource_manager.js'
        ).read_text(encoding='utf-8')

        self.assertIn("const endpoint = '/api/system/resource-manager/';", script)
        self.assertIn("cache: 'no-store'", script)
        self.assertIn("return 'Unavailable';", script)
        self.assertIn('reason.message', script)
        self.assertNotIn('Math.random', script)

    def test_resource_manager_page_redirects_during_setup_and_connecting_modes(self):
        for provisioning_state in (
            NetworkProvisioningState.State.SETUP_AP,
            NetworkProvisioningState.State.CONNECTING,
        ):
            with self.subTest(provisioning_state=provisioning_state):
                self.provisioning_state.state = provisioning_state
                self.provisioning_state.save(update_fields=['state', 'updated_at'])

                response = self.client.get('/resource-manager/')

                self.assertRedirects(
                    response,
                    '/setup/',
                    fetch_redirect_response=False,
                )
