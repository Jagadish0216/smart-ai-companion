from copy import deepcopy
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from system.control_plane import resource_manager
from system.models import NetworkProvisioningState


GIB = 1024 ** 3


def healthy_snapshot() -> dict:
    no_throttling = {
        "raw": "0x0",
        "under_voltage": False,
        "frequency_capped": False,
        "currently_throttled": False,
        "soft_temperature_limit": False,
        "under_voltage_occurred": False,
        "frequency_cap_occurred": False,
        "throttling_occurred": False,
        "soft_temperature_limit_occurred": False,
    }
    return {
        "snapshot_generated_at": "2026-10-03T12:00:00+05:30",
        "host": {
            "device_name": "Raspberry Pi 5",
            "hostname": "smart-ai-companion",
            "platform": "Linux",
            "architecture": "aarch64",
            "uptime_seconds": 1000.0,
        },
        "cpu": {
            "utilization_percent": 50.0,
            "logical_count": 4,
            "load_average": {
                "one_minute": 1.0,
                "five_minutes": 1.0,
                "fifteen_minutes": 0.8,
            },
        },
        "memory": {
            "total_bytes": 8 * GIB,
            "used_bytes": 4 * GIB,
            "available_bytes": 4 * GIB,
            "used_percent": 50.0,
        },
        "storage": {
            "total_bytes": 64 * GIB,
            "used_bytes": 32 * GIB,
            "free_bytes": 32 * GIB,
            "used_percent": 50.0,
        },
        "thermal": {
            "cpu_temperature_c": 55.0,
            "throttling_supported": True,
            "throttling": no_throttling,
        },
        "network": {
            "lan_connected": True,
            "wifi_connected": True,
            "ethernet_connected": False,
            "internet_state": "FULL",
            "internet_available": True,
        },
        "services": {
            name: {"status": "READY", "latency_ms": 1, "detail": "ready"}
            for name in ("django", "ollama", "searxng", "mosquitto")
        },
        "ai": {
            "configured_engine": "local",
            "configured_model": "llama3.2:3b",
            "online_retrieval_enabled": True,
        },
        "provisioning": {"state": "NORMAL_MODE"},
    }


class ResourcePolicyTests(SimpleTestCase):
    def test_healthy_snapshot_recommends_balanced_profile(self):
        result = resource_manager.evaluate_resource_policy(healthy_snapshot())

        self.assertEqual(result["overall_health"], "HEALTHY")
        self.assertEqual(result["recommended_profile"], "BALANCED")
        self.assertTrue(result["ai_recommendation"]["local_ai_allowed"])
        self.assertEqual(result["reasons"], [])

    def test_ample_measured_headroom_recommends_performance(self):
        snapshot = healthy_snapshot()
        snapshot["cpu"]["utilization_percent"] = 25.0
        snapshot["memory"]["used_percent"] = 40.0
        snapshot["storage"]["used_percent"] = 50.0
        snapshot["thermal"]["cpu_temperature_c"] = 50.0

        result = resource_manager.evaluate_resource_policy(snapshot)

        self.assertEqual(result["overall_health"], "HEALTHY")
        self.assertEqual(result["recommended_profile"], "PERFORMANCE")
        self.assertEqual(
            result["ai_recommendation"]["preferred_model_class"],
            "STANDARD",
        )

    def test_high_cpu_alone_is_caution_not_critical(self):
        snapshot = healthy_snapshot()
        snapshot["cpu"]["utilization_percent"] = 80.0

        result = resource_manager.evaluate_resource_policy(snapshot)

        self.assertEqual(result["subsystem_health"]["cpu"]["level"], "CAUTION")
        self.assertEqual(result["overall_health"], "CAUTION")
        self.assertEqual(result["recommended_profile"], "ECO")

    def test_memory_pressure_recommends_eco_and_lightweight(self):
        snapshot = healthy_snapshot()
        snapshot["memory"]["used_percent"] = 80.0

        result = resource_manager.evaluate_resource_policy(snapshot)

        self.assertEqual(result["recommended_profile"], "ECO")
        self.assertEqual(
            result["ai_recommendation"]["preferred_model_class"],
            "LIGHTWEIGHT",
        )
        self.assertEqual(result["reasons"][0]["code"], "MEMORY_PRESSURE")

    def test_critical_memory_pressure_is_protective(self):
        snapshot = healthy_snapshot()
        snapshot["memory"]["used_percent"] = 95.0

        result = resource_manager.evaluate_resource_policy(snapshot)

        self.assertEqual(result["subsystem_health"]["memory"]["level"], "CRITICAL")
        self.assertEqual(result["recommended_profile"], "PROTECTIVE")
        self.assertFalse(result["ai_recommendation"]["local_ai_allowed"])

    def test_high_temperature_constrains_policy(self):
        snapshot = healthy_snapshot()
        snapshot["thermal"]["cpu_temperature_c"] = 75.0

        result = resource_manager.evaluate_resource_policy(snapshot)

        self.assertEqual(
            result["subsystem_health"]["temperature"]["level"],
            "CONSTRAINED",
        )
        self.assertEqual(result["recommended_profile"], "ECO")

    def test_current_throttling_is_protective(self):
        snapshot = healthy_snapshot()
        snapshot["thermal"]["throttling"]["currently_throttled"] = True

        result = resource_manager.evaluate_resource_policy(snapshot)

        self.assertEqual(result["subsystem_health"]["throttling"]["level"], "CRITICAL")
        self.assertEqual(result["recommended_profile"], "PROTECTIVE")
        self.assertEqual(result["reasons"][0]["code"], "THROTTLING_ACTIVE")

    def test_historical_throttling_is_represented_separately(self):
        snapshot = healthy_snapshot()
        snapshot["thermal"]["throttling"]["throttling_occurred"] = True

        result = resource_manager.evaluate_resource_policy(snapshot)

        self.assertEqual(result["subsystem_health"]["throttling"]["level"], "CAUTION")
        self.assertEqual(result["reasons"][0]["code"], "THROTTLING_HISTORY")

    def test_low_storage_recommends_cleanup(self):
        snapshot = healthy_snapshot()
        snapshot["storage"].update(
            {"used_percent": 85.0, "free_bytes": 8 * GIB}
        )

        result = resource_manager.evaluate_resource_policy(snapshot)

        self.assertEqual(result["subsystem_health"]["storage"]["level"], "CAUTION")
        self.assertEqual(
            result["storage_recommendation"]["actions"],
            ["CLEANUP_RECOMMENDED"],
        )

    def test_critical_storage_blocks_model_downloads(self):
        snapshot = healthy_snapshot()
        snapshot["storage"].update(
            {"used_percent": 95.0, "free_bytes": GIB // 2}
        )

        result = resource_manager.evaluate_resource_policy(snapshot)

        self.assertEqual(result["recommended_profile"], "PROTECTIVE")
        self.assertEqual(
            result["storage_recommendation"]["actions"],
            ["CLEANUP_RECOMMENDED", "MODEL_DOWNLOADS_BLOCKED"],
        )

    def test_offline_internet_does_not_disable_local_ai(self):
        snapshot = healthy_snapshot()
        snapshot["network"].update(
            {"internet_state": "NONE", "internet_available": False}
        )

        result = resource_manager.evaluate_resource_policy(snapshot)

        self.assertTrue(result["ai_recommendation"]["local_ai_allowed"])
        self.assertFalse(result["ai_recommendation"]["online_allowed"])
        self.assertEqual(result["subsystem_health"]["network"]["level"], "CAUTION")

    def test_ollama_unavailable_is_separate_from_internet(self):
        snapshot = healthy_snapshot()
        snapshot["services"]["ollama"]["status"] = "UNAVAILABLE"

        result = resource_manager.evaluate_resource_policy(snapshot)

        self.assertEqual(result["subsystem_health"]["network"]["level"], "HEALTHY")
        self.assertEqual(result["subsystem_health"]["ai"]["level"], "CONSTRAINED")
        self.assertFalse(result["ai_recommendation"]["local_ai_allowed"])

    def test_setup_mode_is_represented_without_forcing_resource_pressure(self):
        snapshot = healthy_snapshot()
        snapshot["provisioning"]["state"] = "SETUP_AP"
        snapshot["network"]["lan_connected"] = False

        result = resource_manager.evaluate_resource_policy(snapshot)

        self.assertEqual(result["subsystem_health"]["provisioning"]["level"], "CAUTION")
        self.assertIn(
            "PROVISIONING_INCOMPLETE",
            [reason["code"] for reason in result["reasons"]],
        )
        self.assertEqual(result["recommended_profile"], "BALANCED")

    def test_missing_temperature_is_unknown_and_conservative(self):
        snapshot = healthy_snapshot()
        snapshot["thermal"]["cpu_temperature_c"] = None

        result = resource_manager.evaluate_resource_policy(snapshot)

        self.assertEqual(result["subsystem_health"]["temperature"]["level"], "UNKNOWN")
        self.assertEqual(result["overall_health"], "UNKNOWN")
        self.assertEqual(
            result["ai_recommendation"]["preferred_model_class"],
            "LIGHTWEIGHT",
        )

    def test_multiple_warnings_have_stable_priority_and_codes(self):
        snapshot = healthy_snapshot()
        snapshot["thermal"]["cpu_temperature_c"] = 80.0
        snapshot["memory"]["used_percent"] = 90.0
        snapshot["storage"].update({"used_percent": 85.0, "free_bytes": 8 * GIB})

        first = resource_manager.evaluate_resource_policy(snapshot)
        second = resource_manager.evaluate_resource_policy(deepcopy(snapshot))

        codes = [reason["code"] for reason in first["reasons"]]
        self.assertEqual(first["recommended_profile"], "PROTECTIVE")
        self.assertEqual(codes[:3], ["TEMPERATURE_PRESSURE", "MEMORY_PRESSURE", "STORAGE_LOW"])
        self.assertEqual(first["reasons"], second["reasons"])

    @override_settings(
        RESOURCE_MEMORY_CAUTION_PERCENT=70,
        RESOURCE_MEMORY_CONSTRAINED_PERCENT=80,
        RESOURCE_MEMORY_CRITICAL_PERCENT=90,
    )
    def test_explicit_threshold_boundaries_come_from_settings(self):
        snapshot = healthy_snapshot()
        snapshot["memory"]["used_percent"] = 90.0

        result = resource_manager.evaluate_resource_policy(snapshot)

        self.assertEqual(result["subsystem_health"]["memory"]["level"], "CRITICAL")
        self.assertEqual(result["thresholds"]["memory_critical_percent"], 90.0)


class ResourceSnapshotTests(SimpleTestCase):
    @patch.object(resource_manager.NetworkProvisioningState, "get_current")
    @patch.object(resource_manager, "get_service_health")
    @patch.object(resource_manager, "get_network_status")
    @patch.object(resource_manager, "get_resource_metrics")
    def test_missing_host_telemetry_degrades_without_fabrication(
        self, metrics, network, services, provisioning
    ):
        metrics.return_value = {
            "device_name": "Windows host",
            "cpu_percent": None,
            "cpu_count": 8,
            "ram_percent": None,
            "storage_percent": None,
            "temperature_c": None,
            "throttled": None,
        }
        network.side_effect = OSError("network unavailable")
        services.side_effect = OSError("services unavailable")
        provisioning.side_effect = OSError("database unavailable")

        snapshot = resource_manager.collect_resource_snapshot()
        result = resource_manager.evaluate_resource_policy(snapshot)

        self.assertIsNone(snapshot["thermal"]["cpu_temperature_c"])
        self.assertFalse(snapshot["thermal"]["throttling_supported"])
        self.assertEqual(result["subsystem_health"]["temperature"]["level"], "UNKNOWN")
        self.assertEqual(result["subsystem_health"]["throttling"]["level"], "UNKNOWN")
        self.assertIsNone(snapshot["network"]["lan_connected"])

    @patch.object(resource_manager, "collect_resource_snapshot")
    def test_report_exposes_collection_and_policy_timing(self, collect):
        collect.return_value = healthy_snapshot()

        report = resource_manager.get_resource_manager_report(use_cache=False)

        self.assertIn("collection_ms", report["timing"])
        self.assertIn("policy_ms", report["timing"])
        self.assertIn("total_ms", report["timing"])
        self.assertGreaterEqual(report["timing"]["collection_ms"], 0)


class ResourceManagerAPITests(TestCase):
    def setUp(self):
        cache.delete(resource_manager.RESOURCE_MANAGER_CACHE_KEY)

        state = NetworkProvisioningState.get_current()
        state.state = NetworkProvisioningState.State.NORMAL_MODE
        state.save(update_fields=["state", "updated_at"])

    @patch("system.views.get_resource_manager_report")
    def test_api_is_read_only_and_returns_expected_schema(self, report):
        policy = resource_manager.evaluate_resource_policy(healthy_snapshot())
        report.return_value = {
            "snapshot": healthy_snapshot(),
            **policy,
            "timing": {"collection_ms": 1.0, "policy_ms": 0.1, "total_ms": 1.1},
        }

        response = self.client.get("/api/system/resource-manager/")
        mutation = self.client.post("/api/system/resource-manager/", {})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(mutation.status_code, 405)
        for key in (
            "snapshot",
            "subsystem_health",
            "overall_health",
            "recommended_profile",
            "reasons",
            "ai_recommendation",
            "storage_recommendation",
            "timing",
            "thresholds",
        ):
            self.assertIn(key, response.json())

    @patch("system.views.get_resource_manager_report")
    def test_api_is_blocked_in_setup_and_connecting_modes(self, report):
        state = NetworkProvisioningState.get_current()
        for provisioning_state in (
            NetworkProvisioningState.State.SETUP_AP,
            NetworkProvisioningState.State.CONNECTING,
        ):
            with self.subTest(provisioning_state=provisioning_state):
                state.state = provisioning_state
                state.save(update_fields=["state", "updated_at"])

                response = self.client.get("/api/system/resource-manager/")

                self.assertEqual(response.status_code, 404)
                self.assertIn("Only Wi-Fi setup", response.json()["detail"])

        report.assert_not_called()

    @patch("system.control_plane.network.deactivate_setup_access_point")
    @patch("system.control_plane.network.connect_wifi")
    @patch.object(resource_manager, "collect_resource_snapshot", return_value=healthy_snapshot())
    def test_report_evaluation_executes_no_mutation_actions(
        self, _collect, connect_wifi, deactivate_ap
    ):
        resource_manager.get_resource_manager_report(use_cache=False)

        connect_wifi.assert_not_called()
        deactivate_ap.assert_not_called()
