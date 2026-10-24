"""Third-model plumbing and fair pairing; all sizes, outputs and digests are mock data."""
from unittest.mock import patch
from django.test import SimpleTestCase
from scripts import compare_offline_models as models
from scripts import evaluate_offline_quality as quality
from scripts import report_offline_quality as reports
from assistant.tests_quality_evaluation import trial, point, completed_review

THREE_B="llama3.2:3b"
DIGEST="a80c4f17acd5"+"0"*52
RESIDENT={"name":THREE_B,"digest":DIGEST,"context_length":4096,"size":3*1024**3,"size_vram":0}


class LlamaThreeBTests(SimpleTestCase):
    def test_allowlists_preserve_existing_positions_and_add_exact_tag(self):
        self.assertEqual(models.MODELS[:4],("qwen3.5:4b-q4_K_M","llama3.2:1b","qwen3.5:2b-q4_K_M","qwen3.5:0.8b-q8_0"))
        self.assertEqual(quality.MODELS[:2],("llama3.2:1b","qwen3.5:4b-q4_K_M"))
        self.assertIn(THREE_B,quality.MODELS)
        self.assertEqual(models.CONFIRMED_PREFIXES[THREE_B],"a80c4f17acd5")

    def test_exact_inventory_retains_actual_quantization_and_artifact_size(self):
        item={"name":THREE_B,"digest":DIGEST,"size":2*1024**3,
              "details":{"format":"gguf","quantization_level":"SYNTHETIC_QUANT","parameter_size":"3B"}}
        with patch.object(models,"metadata",return_value={"models":[item]}):
            installed=models.inventory("http://127.0.0.1:11434")[THREE_B]
        self.assertTrue(installed["usable"])
        self.assertEqual(installed["artifact_bytes"],item["size"])
        self.assertEqual(installed["details"]["quantization_level"],"SYNTHETIC_QUANT")
        for change in ({"name":"llama3.2:3b-latest"},{"digest":"a80c4f17acd5"},{"remote_host":"remote"}):
            with patch.object(models,"metadata",return_value={"models":[dict(item,**change)]}):
                self.assertFalse(models.inventory("http://127.0.0.1:11434")[THREE_B]["usable"])

    def test_full_digest_and_confirmed_prefix_both_enforced(self):
        installed={"usable":True,"digest":DIGEST}
        self.assertEqual(models.verify_identity(THREE_B,installed,DIGEST),DIGEST)
        for item,expected in ((dict(installed,digest="d8b0f5e9760c"+"0"*52),None),
                              (installed,"a80c4f17acd5"+"1"*52)):
            with self.assertRaises(models.TrialRefused):models.verify_identity(THREE_B,item,expected)

    def test_resident_identity_context_memory_allocation_and_conflicts(self):
        with patch.object(models,"metadata",return_value={"models":[RESIDENT]}):
            actual=quality.resident("http://127.0.0.1:11434",THREE_B,DIGEST,"warm")
            self.assertEqual(actual[0]["size"],3*1024**3)
        invalid=([dict(RESIDENT,name="llama3.2:1b")],[dict(RESIDENT,name="qwen3.5:4b-q4_K_M")],
                 [RESIDENT,RESIDENT],[dict(RESIDENT,digest="a"*64)],
                 [dict(RESIDENT,context_length=2048)],[])
        for resident in invalid:
            with patch.object(models,"metadata",return_value={"models":resident}):
                with self.assertRaises(models.TrialRefused):quality.resident("http://127.0.0.1:11434",THREE_B,DIGEST,"warm")

    def test_cold_three_b_memory_margin_unchanged(self):
        installed={"artifact_bytes":2*1024**3}
        with self.assertRaises(models.TrialRefused):quality.memory_floor(point(),installed,False)
        quality.memory_floor(point(ram_available_bytes=4*1024**3),installed,False)
        quality.memory_floor(point(),installed,True)
        with self.assertRaises(models.TrialRefused):quality.memory_floor(point(ram_available_bytes=511*1024**2),installed,True)

    def test_thermal_guards_remain_model_independent(self):
        with self.assertRaises(models.TrialRefused):quality.memory_floor(point(temperature_c=55),{},True)
        self.assertEqual(models.pressure_reason(point(temperature_c=70)),"temperature_safety_margin")
        pressured=point();pressured["throttled"]["under_voltage"]=True
        self.assertEqual(models.pressure_reason(pressured),"current_power_or_throttle_pressure")

    def test_old_two_model_report_does_not_require_third_trial(self):
        result=reports.summarize([trial(),trial(quality.REFERENCE_MODEL)],[])
        self.assertEqual(len(result["paired_latency"]),1)
        self.assertTrue(result["paired_latency"][0]["eligible"])
        self.assertEqual(result["models"][THREE_B]["tested_questions"],0)

    def test_three_b_reference_pair_without_one_b_is_independent(self):
        result=reports.summarize([trial(THREE_B),trial(quality.REFERENCE_MODEL)],[])
        pair=result["paired_latency"][0]
        self.assertEqual((pair["candidate_model"],pair["reference_model"]),(THREE_B,quality.REFERENCE_MODEL))
        self.assertTrue(pair["eligible"])

    def test_three_models_have_three_explicit_unambiguous_pairs(self):
        inputs=[trial(model) for model in quality.MODELS]
        result=reports.summarize(inputs,[completed_review(r) for r in inputs])
        pairs=result["paired_latency"]
        self.assertEqual({(p["candidate_model"],p["reference_model"]) for p in pairs},
            {("llama3.2:1b",quality.REFERENCE_MODEL),(THREE_B,quality.REFERENCE_MODEL),(THREE_B,"llama3.2:1b")})
        self.assertTrue(all(p["eligible"] for p in pairs))
        self.assertEqual(result["models"][THREE_B]["digest"],DIGEST)
        self.assertIn("pending",result["replacement_recommendation"])

    def test_three_b_unequal_cache_controls_and_truncation_get_no_ratio(self):
        for change in (lambda s:s.update(truncated=True),lambda s:s["load"].update(label="cold"),
                       lambda s:s["prompt_cache"].update(cached_tokens_reported=90),
                       lambda s:s["controls"]["options"].update(num_predict=192)):
            candidate=trial(THREE_B);change(candidate["evaluation"]["samples"][0])
            result=reports.summarize([candidate,trial(quality.REFERENCE_MODEL)],[])
            self.assertFalse(result["paired_latency"][0]["eligible"])
            self.assertIsNone(result["paired_latency"][0]["candidate_over_reference_complete"])
