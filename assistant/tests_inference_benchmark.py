"""A/B experiment bounds and safety gates, without real Ollama or audio."""

from types import SimpleNamespace
from django.test import SimpleTestCase
from scripts.benchmark_inference_ab import options_for, stop_for_pressure, FIXTURES, digest, validate_local_model, condition_differences


class InferenceComparisonTests(SimpleTestCase):
    def test_comparison_reports_unequal_controls_and_temperature(self):
        a = {"sink": "ALSA null"}
        sample = {"request": {"options": {"num_predict": 64}}, "telemetry": [{"temperature_c": 50}]}
        self.assertEqual(condition_differences(a, a, sample, sample), {})
        b = {"sink": "speaker"}
        candidate = {"request": {"options": {"num_predict": 192}}, "telemetry": [{"temperature_c": 60}]}
        differences = condition_differences(a, b, sample, candidate)
        self.assertEqual(set(differences), {"request", "sink", "starting_temperature_c"})
        self.assertEqual(differences["starting_temperature_c"], {"A": 50, "B": 60})

    def test_cloud_and_unconfirmed_formats_are_rejected_before_inference(self):
        validate_local_model({"details": {"format": "gguf"}})
        for metadata in ({}, {"details": {"format": "gguf"}, "remote_host": "cloud.example"},
                         {"details": {"format": "gguf"}, "remote_model": "remote"},
                         {"details": {"format": "safetensors"}}):
            with self.subTest(metadata=metadata), self.assertRaises(RuntimeError):
                validate_local_model(metadata)

    def args(self, **updates):
        values = dict(context=4096, threads=4, batch=128, profile="defaults",
                      candidate_threads=None, candidate_batch=None)
        values.update(updates)
        return SimpleNamespace(**values)

    def test_equal_defaults_and_single_cpu_variable(self):
        args = self.args()
        self.assertEqual(options_for(args, "A"), options_for(args, "B"))
        args = self.args(profile="cpu", candidate_threads=3)
        a, b = options_for(args, "A"), options_for(args, "B")
        self.assertEqual([key for key in a if a[key] != b[key]], ["num_thread"])
        self.assertEqual(a["seed"], 42)
        self.assertEqual(a["temperature"], 0)

    def test_history_only_does_not_stop_comparison_but_each_current_flag_does(self):
        historical = {key: True for key in ("under_voltage_occurred", "frequency_cap_occurred",
                                            "throttling_occurred", "soft_temperature_limit_occurred")}
        self.assertFalse(stop_for_pressure({"temperature_c": 65, "throttled": historical}))
        for key in ("under_voltage", "frequency_capped", "currently_throttled", "soft_temperature_limit"):
            self.assertTrue(stop_for_pressure({"temperature_c": 40, "throttled": {**historical, key: True}}))
        self.assertTrue(stop_for_pressure({"temperature_c": 75, "throttled": historical}))

    def test_fixed_fixtures_cover_grounding_and_have_stable_digest(self):
        self.assertEqual([item[0] for item in FIXTURES], ["brief", "normal", "normal", "detailed", "rag", "memory"])
        self.assertEqual(digest(FIXTURES), digest(tuple(FIXTURES)))
