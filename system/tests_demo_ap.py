from unittest.mock import MagicMock, patch
from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, override_settings
from system.models import NetworkProvisioningState
from system.control_plane import demo_ap, network, provisioning


@override_settings(DEMO_AP_ENABLED=True)
class DemoAccessTests(TestCase):
    def test_anonymous_dashboard_requires_login_and_api_requires_session(self):
        self.assertEqual(self.client.get('/').status_code,302)
        self.assertEqual(self.client.get('/api/system/setup/status/').status_code,401)
        response=self.client.get('/login/')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'csrfmiddlewaretoken')

    def test_authenticated_routes_keep_staff_permissions_and_radio_is_console_only(self):
        user=get_user_model().objects.create_user('reviewer',password='test-only-user-password')
        self.client.force_login(user)
        state=NetworkProvisioningState.get_current();state.state='DEMO_AP';state.save()
        self.assertEqual(self.client.get('/api/system/setup/status/').status_code,404)
        self.assertEqual(self.client.post('/api/system/network/wifi/connect/',{}).status_code,403)
        self.assertEqual(self.client.get('/admin/').status_code,302) # still requires staff

    @patch('system.control_plane.demo_ap.activate')
    @patch('system.control_plane.network.get_active_wifi_connection')
    def test_demo_ap_wins_on_every_boot_without_trying_saved_client_profiles(self,client,activate):
        activate.return_value={'success':True,'state':'DEMO_AP','message':'Protected offline demo network is active.'}
        for i in range(2):self.assertTrue(provisioning.ensure_network_mode()['success'])
        client.assert_not_called()
        self.assertEqual(activate.call_count,2)
        self.assertEqual(NetworkProvisioningState.get_current().state,'DEMO_AP')
        self.assertFalse(provisioning.setup_mode_available())

    @patch('system.control_plane.demo_ap.activate',side_effect=RuntimeError('unavailable'))
    def test_missing_credentials_do_not_expose_setup_routes_or_crash_web(self,_activate):
        self.assertFalse(provisioning.ensure_network_mode()['success'])
        self.assertEqual(NetworkProvisioningState.get_current().state,'DEMO_AP')
        self.assertEqual(self.client.get('/').status_code,302)

    def test_csrf_not_disabled_on_login(self):
        from django.test import Client
        self.assertEqual(Client(enforce_csrf_checks=True).post('/login/',{'username':'x','password':'x'}).status_code,403)


class DemoProfileTests(SimpleTestCase):
    @patch('system.control_plane.demo_ap._command')
    def test_boot_verifies_protected_profile_and_actual_gateway(self,command):
        command.side_effect=['ap','shared',demo_ap.SSID,'wpa-psk','rsn','synthetic-private-test-value',
                             '10.77.0.1/24','',demo_ap.PROFILE,'10.77.0.1/24']
        result=demo_ap.activate()
        self.assertEqual(result['gateway'],'10.77.0.1')
        self.assertEqual(result['state'],'DEMO_AP')
        self.assertNotIn('synthetic-private',str(result))

    @patch('system.control_plane.demo_ap.shutil.which',return_value='nmcli')
    @patch('system.control_plane.network._connection_profiles',return_value=([],'OK'))
    @patch('system.control_plane.network._execute')
    def test_profile_is_protected_not_activated_and_password_never_in_return(self,execute,_profiles,_which):
        secret='synthetic-test-only-secret'
        execute.side_effect=[network._CommandOutcome(0),network._CommandOutcome(0),network._CommandOutcome(0,'10.77.0.1/24')]
        result=demo_ap.configure(secret)
        self.assertEqual(result,'10.77.0.1')
        commands=[call.args[0] for call in execute.call_args_list]
        self.assertFalse(any('up' in c or 'delete' in c for c in commands))
        self.assertIn('wpa-psk',commands[1]);self.assertIn('rsn',commands[1]);self.assertIn('shared',commands[1])
        self.assertTrue(all(c.kwargs=={} or 'shell' not in c.kwargs for c in execute.call_args_list))
        self.assertTrue(all(c.args[1]<=15 for c in execute.call_args_list))

    @patch('system.control_plane.demo_ap._command')
    def test_open_profile_is_refused_without_activation(self,command):
        command.side_effect=['ap','shared','none']
        with self.assertRaisesRegex(RuntimeError,'protected'):demo_ap.activate()
        self.assertFalse(any('up' in c.args for c in command.call_args_list))

    @patch('system.control_plane.demo_ap._command',return_value='10.42.0.1/24')
    def test_mismatched_gateway_is_refused(self,_command):
        with self.assertRaisesRegex(RuntimeError,'gateway'):demo_ap.configured_gateway()

    @patch('system.control_plane.demo_ap.shutil.which',return_value='nmcli')
    @patch('system.control_plane.network._execute',return_value=network._CommandOutcome(4,stderr='SENSITIVE PASSWORD'))
    def test_nm_error_never_returns_command_or_secret(self,_execute,_which):
        with self.assertRaises(RuntimeError) as raised:demo_ap._command('connection','show','x')
        self.assertNotIn('SENSITIVE',str(raised.exception))
