import io
import json
import subprocess
from pathlib import Path
from unittest.mock import call, patch

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, SimpleTestCase, TestCase, override_settings

from .control_plane import network, provisioning
from .models import NetworkProvisioningState


ACTIVE_CLIENT = {
    "supported": True,
    "connected": True,
    "interface": "wlan0",
    "profile": "Home WiFi",
}
NO_CLIENT = {
    "supported": True,
    "connected": False,
    "interface": "wlan0",
    "profile": None,
}
AP_STARTED = {
    "success": True,
    "state": "SETUP_AP",
    "message": "The setup network is active.",
    "already_active": False,
}


@override_settings(SETUP_AP_PASSWORD="unique-setup-secret")
class ProvisioningDecisionTests(TestCase):
    @patch.object(network, "activate_setup_access_point", return_value=AP_STARTED)
    @patch.object(network, "list_saved_wifi_profiles", return_value={"supported": True, "profiles": []})
    @patch.object(network, "get_active_wifi_connection", return_value=NO_CLIENT)
    def test_unprovisioned_device_chooses_setup_mode(self, _active, _saved, start_ap):
        result = provisioning.ensure_network_mode()

        self.assertTrue(result["success"])
        self.assertEqual(result["state"], NetworkProvisioningState.State.SETUP_AP)
        self.assertEqual(
            NetworkProvisioningState.get_current().state,
            NetworkProvisioningState.State.SETUP_AP,
        )
        start_ap.assert_called_once()

    @patch.object(network, "activate_setup_access_point")
    @patch.object(network, "get_active_wifi_connection", return_value=ACTIVE_CLIENT)
    def test_connected_client_wifi_chooses_normal_mode(self, _active, start_ap):
        result = provisioning.ensure_network_mode()

        self.assertEqual(result["state"], NetworkProvisioningState.State.NORMAL_MODE)
        start_ap.assert_not_called()

    @patch.object(network, "get_internet_status", return_value={"state": "NONE", "available": False})
    @patch.object(network, "get_active_wifi_connection", return_value=ACTIVE_CLIENT)
    def test_lan_without_internet_remains_normal_mode(self, _active, internet):
        result = provisioning.ensure_network_mode()

        self.assertEqual(result["state"], NetworkProvisioningState.State.NORMAL_MODE)
        internet.assert_not_called()

    @patch.object(network, "activate_setup_access_point", return_value=AP_STARTED)
    @patch.object(network, "activate_saved_wifi_profile", return_value={"success": False})
    @patch.object(
        network,
        "list_saved_wifi_profiles",
        return_value={"supported": True, "profiles": ["one", "two", "three"]},
    )
    @patch.object(network, "get_active_wifi_connection", return_value=NO_CLIENT)
    @override_settings(SETUP_SAVED_PROFILE_ATTEMPTS=2)
    def test_saved_profile_attempts_are_bounded(
        self, _active, _saved, activate_saved, _start_ap
    ):
        provisioning.ensure_network_mode()

        self.assertEqual(
            activate_saved.call_args_list,
            [call("one"), call("two")],
        )

    @patch.object(network, "activate_setup_access_point", return_value={**AP_STARTED, "already_active": True})
    @patch.object(network, "list_saved_wifi_profiles")
    def test_repeated_setup_mode_invocation_is_idempotent(self, saved, start_ap):
        state = NetworkProvisioningState.get_current()
        state.state = NetworkProvisioningState.State.SETUP_AP
        state.save()

        first = provisioning.ensure_network_mode()
        second = provisioning.ensure_network_mode()

        self.assertTrue(first["already_active"])
        self.assertTrue(second["already_active"])
        self.assertEqual(start_ap.call_count, 2)
        saved.assert_not_called()

    @patch.object(network, "activate_setup_access_point", return_value=AP_STARTED)
    def test_interrupted_connecting_state_recovers_setup_ap(self, _start_ap):
        state = NetworkProvisioningState.get_current()
        state.state = NetworkProvisioningState.State.CONNECTING
        state.save()

        result = provisioning.ensure_network_mode()

        self.assertEqual(result["state"], NetworkProvisioningState.State.SETUP_AP)

    @patch.object(network, "activate_setup_access_point", return_value=AP_STARTED)
    def test_force_setup_enters_recovery_mode(self, _start_ap):
        state = NetworkProvisioningState.get_current()
        state.state = NetworkProvisioningState.State.NORMAL_MODE
        state.save()

        result = provisioning.ensure_network_mode(force_setup=True)

        self.assertEqual(result["state"], NetworkProvisioningState.State.SETUP_AP)


class SetupAccessPointNetworkTests(SimpleTestCase):
    def test_setup_ssid_is_deterministic_and_short(self):
        first = provisioning.setup_ssid("stable-device-id")
        second = provisioning.setup_ssid("stable-device-id")

        self.assertEqual(first, second)
        self.assertRegex(first, r"^SmartCompanion-[0-9A-F]{4}$")
        self.assertNotIn("stable-device-id", first)

    @patch.object(network, "shutil")
    def test_missing_setup_password_fails_before_nmcli(self, shutil_module):
        result = network.activate_setup_access_point("SmartCompanion-A3F2", "")

        self.assertFalse(result["success"])
        self.assertNotIn("password", str(result).lower().replace("setup access-point password", ""))
        shutil_module.which.assert_not_called()

    @patch.object(network, "_connection_profiles", return_value=([], "OK"))
    @patch.object(network, "setup_access_point_is_active", return_value=False)
    @patch.object(network, "_wifi_interface", return_value=("wlan0", "OK"))
    @patch.object(network.shutil, "which", return_value="/usr/bin/nmcli")
    @patch.object(network.subprocess, "run")
    def test_setup_ap_uses_bounded_argument_arrays_without_shell(
        self, run, _which, _interface, _active, _profiles
    ):
        secret = "unique-setup-secret"
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        )

        result = network.activate_setup_access_point("SmartCompanion-A3F2", secret)

        self.assertTrue(result["success"])
        self.assertNotIn(secret, str(result))
        self.assertEqual(run.call_count, 3)
        for invocation in run.call_args_list:
            self.assertIsInstance(invocation.args[0], list)
            self.assertFalse(invocation.kwargs["shell"])
            self.assertLessEqual(invocation.kwargs["timeout"], network.SETUP_AP_TIMEOUT_SECONDS)
        modify_command = run.call_args_list[1].args[0]
        self.assertIn("wpa-psk", modify_command)
        self.assertIn("rsn", modify_command)
        self.assertIn("shared", modify_command)
        self.assertIn(secret, modify_command)

    @patch.object(network, "_connection_profiles")
    @patch.object(network, "setup_access_point_is_active", return_value=True)
    @patch.object(network, "_wifi_interface", return_value=("wlan0", "OK"))
    @patch.object(network.shutil, "which", return_value="/usr/bin/nmcli")
    @patch.object(network.subprocess, "run")
    def test_already_active_setup_ap_runs_no_mutating_command(
        self, run, _which, _interface, _active, profiles
    ):
        result = network.activate_setup_access_point(
            "SmartCompanion-A3F2", "unique-setup-secret"
        )

        self.assertTrue(result["already_active"])
        run.assert_not_called()
        profiles.assert_not_called()

    @patch.object(
        network,
        "_connection_profiles",
        return_value=(
            [
                {"name": network.SETUP_AP_PROFILE_NAME, "type": "802-11-wireless", "device": ""},
                {"name": "Home WiFi", "type": "802-11-wireless", "device": ""},
                {"name": "Wired", "type": "802-3-ethernet", "device": ""},
            ],
            "OK",
        ),
    )
    @patch.object(network, "_wifi_profile_mode", return_value="infrastructure")
    @patch.object(network.shutil, "which", return_value="/usr/bin/nmcli")
    def test_saved_profiles_exclude_setup_ap(self, _which, _mode, _profiles):
        result = network.list_saved_wifi_profiles()

        self.assertEqual(result["profiles"], ["Home WiFi"])

    @patch.object(network.shutil, "which", return_value=None)
    def test_windows_without_nmcli_degrades_safely(self, _which):
        self.assertFalse(network.get_active_wifi_connection()["connected"])
        self.assertFalse(network.list_saved_wifi_profiles()["supported"])
        result = network.activate_setup_access_point(
            "SmartCompanion-A3F2", "unique-setup-secret"
        )
        self.assertEqual(result["state"], "NOT_AVAILABLE")

    @patch.object(network, "_wifi_interface", return_value=("wlan0", "OK"))
    @patch.object(
        network,
        "_execute",
        return_value=network._CommandOutcome(returncode=0, stdout="ap\n"),
    )
    @patch.object(
        network,
        "_connection_profiles",
        return_value=(
            [{"name": "Other Hotspot", "type": "802-11-wireless", "device": "wlan0"}],
            "OK",
        ),
    )
    @patch.object(network.shutil, "which", return_value="/usr/bin/nmcli")
    def test_non_setup_hotspot_is_not_mistaken_for_client_wifi(
        self, _which, _profiles, _execute, _interface
    ):
        result = network.get_active_wifi_connection()

        self.assertFalse(result["connected"])


@override_settings(SETUP_AP_PASSWORD="deployment-setup-secret")
class SetupTransitionTests(TestCase):
    def setUp(self):
        state = NetworkProvisioningState.get_current()
        state.state = NetworkProvisioningState.State.SETUP_AP
        state.save()

    @patch.object(network, "get_network_status")
    @patch.object(network, "connect_wifi")
    @patch.object(network, "deactivate_setup_access_point")
    def test_successful_unicode_wifi_connection_reaches_normal_mode(
        self, deactivate, connect, status
    ):
        ssid = "Sravani’s iPhone"
        deactivate.return_value = {"success": True, "state": "INACTIVE", "message": "stopped"}
        connect.return_value = {
            "success": True,
            "state": "CONNECTED",
            "ssid": ssid,
            "message": "Connected successfully.",
        }
        status.return_value = {
            "wifi": {"connected": True, "ssid": ssid},
            "internet": {"state": "NONE", "available": False},
        }

        result = provisioning.provision_wifi(ssid, "private-password")

        self.assertTrue(result["success"])
        self.assertEqual(result["ssid"], ssid)
        self.assertEqual(result["state"], NetworkProvisioningState.State.NORMAL_MODE)
        self.assertNotIn("private-password", str(result))
        connect.assert_called_once_with(ssid, "private-password")
        self.assertEqual(
            NetworkProvisioningState.get_current().state,
            NetworkProvisioningState.State.NORMAL_MODE,
        )

    @patch.object(network, "activate_setup_access_point", return_value=AP_STARTED)
    @patch.object(network, "get_network_status", return_value={"wifi": {"connected": False, "ssid": None}})
    @patch.object(network, "connect_wifi")
    @patch.object(network, "deactivate_setup_access_point")
    def test_wrong_password_restores_ap_without_raw_error(
        self, deactivate, connect, restore, _activate
    ):
        deactivate.return_value = {"success": True, "state": "INACTIVE", "message": "stopped"}
        connect.return_value = {
            "success": False,
            "state": "FAILED",
            "message": "Could not authenticate with the selected network.",
        }

        result = provisioning.provision_wifi("Home WiFi", "wrong-secret")

        self.assertFalse(result["success"])
        self.assertEqual(result["state"], NetworkProvisioningState.State.SETUP_AP)
        self.assertNotIn("wrong-secret", str(result))
        self.assertNotIn("nmcli", str(result).lower())
        self.assertEqual(
            NetworkProvisioningState.get_current().state,
            NetworkProvisioningState.State.SETUP_AP,
        )

    @patch.object(
        network,
        "activate_setup_access_point",
        return_value={"success": False, "state": "FAILED", "message": "The setup network could not be started."},
    )
    @patch.object(network, "get_network_status", return_value={"wifi": {"connected": False, "ssid": None}})
    @patch.object(network, "connect_wifi", return_value={"success": False, "state": "FAILED", "message": "Could not connect."})
    @patch.object(network, "deactivate_setup_access_point", return_value={"success": True, "state": "INACTIVE", "message": "stopped"})
    def test_failed_ap_restore_persists_failed_state(self, _down, _connect, _status, _restore):
        result = provisioning.provision_wifi("Home WiFi", "wrong-secret")

        self.assertEqual(result["state"], NetworkProvisioningState.State.FAILED)
        self.assertEqual(
            NetworkProvisioningState.get_current().state,
            NetworkProvisioningState.State.FAILED,
        )

    @patch.object(network, "deactivate_setup_access_point")
    def test_invalid_credentials_do_not_deactivate_ap(self, deactivate):
        result = provisioning.provision_wifi("", "secret")

        self.assertEqual(result["state"], "INVALID")
        deactivate.assert_not_called()


@override_settings(
    SETUP_AP_PASSWORD="deployment-setup-secret",
    SETUP_CONNECT_COOLDOWN_SECONDS=5,
)
class SetupPortalTests(TestCase):
    def setUp(self):
        cache.clear()
        self.state = NetworkProvisioningState.get_current()

    def tearDown(self):
        cache.clear()

    def activate_setup(self):
        self.state.state = NetworkProvisioningState.State.SETUP_AP
        self.state.save()

    def test_setup_page_and_apis_are_unavailable_in_normal_mode(self):
        self.state.state = NetworkProvisioningState.State.NORMAL_MODE
        self.state.save()

        for url in (
            "/setup/",
            "/api/system/setup/status/",
            "/api/system/setup/wifi/scan/",
            "/api/system/setup/wifi/connect/",
        ):
            with self.subTest(url=url):
                response = self.client.post(url) if url.endswith("connect/") else self.client.get(url)
                self.assertEqual(response.status_code, 404)

    def test_setup_page_is_local_minimal_and_uses_canonical_hostname(self):
        self.activate_setup()

        response = self.client.get("/setup/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Connect Companion to Wi-Fi")
        self.assertContains(response, "smart-ai-companion.local:8000")
        self.assertContains(response, "/static/css/setup.css")
        self.assertContains(response, "/static/js/setup.js")
        self.assertNotContains(response, "unpkg.com")
        self.assertNotContains(response, "Control Center")
        self.assertNotContains(response, "deployment-setup-secret")

    def test_setup_mode_redirects_dashboard_and_blocks_general_apis(self):
        self.activate_setup()

        dashboard = self.client.get("/")
        general_api = self.client.get("/api/system/network/")

        self.assertRedirects(dashboard, "/setup/", fetch_redirect_response=False)
        self.assertEqual(general_api.status_code, 404)
        self.assertIn("Only Wi-Fi setup", general_api.json()["detail"])

    @patch("system.views.scan_wifi_networks")
    def test_anonymous_setup_scan_only_works_during_setup(self, scan):
        self.activate_setup()
        scan.return_value = {
            "supported": True,
            "interface": "wlan0",
            "networks": [{"ssid": "Home", "security": "WPA2", "signal_percent": 80}],
        }

        response = self.client.get("/api/system/setup/wifi/scan/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["networks"][0]["ssid"], "Home")

    @patch("system.views.provision_wifi")
    def test_setup_connect_requires_csrf_and_preserves_unicode_ssid(self, provision):
        self.activate_setup()
        client = Client(enforce_csrf_checks=True)
        token = client.get("/setup/").cookies["csrftoken"].value
        ssid = "Café 東京"
        payload = json.dumps({"ssid": ssid, "password": "private-password"})

        rejected = client.post(
            "/api/system/setup/wifi/connect/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 403)
        provision.assert_not_called()

        provision.return_value = {
            "success": True,
            "state": "NORMAL_MODE",
            "ssid": ssid,
            "message": "Connected to Wi-Fi.",
        }
        accepted = client.post(
            "/api/system/setup/wifi/connect/",
            data=payload,
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(accepted.status_code, 200)
        provision.assert_called_once_with(ssid, "private-password")
        self.assertNotIn("private-password", accepted.content.decode())

    @patch("system.views.provision_wifi")
    def test_setup_connect_attempts_are_rate_limited(self, provision):
        self.activate_setup()
        client = Client(enforce_csrf_checks=True)
        token = client.get("/setup/").cookies["csrftoken"].value
        provision.return_value = {
            "success": False,
            "state": "SETUP_AP",
            "message": "Try again.",
        }
        kwargs = {
            "data": json.dumps({"ssid": "Home", "password": "secret-123"}),
            "content_type": "application/json",
            "HTTP_X_CSRFTOKEN": token,
        }

        first = client.post("/api/system/setup/wifi/connect/", **kwargs)
        second = client.post("/api/system/setup/wifi/connect/", **kwargs)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(provision.call_count, 1)

    @patch("system.views.connect_wifi")
    def test_normal_anonymous_wifi_endpoint_remains_protected(self, connect):
        self.state.state = NetworkProvisioningState.State.NORMAL_MODE
        self.state.save()

        response = self.client.post(
            "/api/system/network/wifi/connect/",
            {"ssid": "Home", "password": "private-password"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        connect.assert_not_called()


class ProvisioningCommandAndDeploymentTests(SimpleTestCase):
    @patch(
        "system.management.commands.ensure_network_mode.ensure_network_mode",
        return_value={"success": True, "state": "SETUP_AP", "message": "The setup network is active."},
    )
    def test_management_command_supports_force_setup(self, ensure):
        output = io.StringIO()

        call_command("ensure_network_mode", "--force-setup", stdout=output)

        ensure.assert_called_once_with(force_setup=True, prefer_client=False)
        self.assertIn("SETUP_AP", output.getvalue())

    def test_management_command_rejects_conflicting_modes(self):
        with self.assertRaises(CommandError):
            call_command("ensure_network_mode", "--force-setup", "--prefer-client")

    def test_systemd_template_is_bounded_and_contains_no_secret_or_sudo(self):
        unit = (
            Path(settings.BASE_DIR)
            / "deployment"
            / "systemd"
            / "smart-ai-companion-network-mode.service"
        ).read_text(encoding="utf-8")

        self.assertIn("After=NetworkManager.service", unit)
        self.assertIn("TimeoutStartSec=90", unit)
        self.assertIn("/opt/smart-ai-companion/venv/bin/python", unit)
        self.assertNotIn("SETUP_AP_PASSWORD", unit)
        self.assertNotIn("sudo", unit.lower())
        self.assertNotIn("Restart=always", unit)
