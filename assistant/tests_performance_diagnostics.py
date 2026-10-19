"""Diagnostics must describe sizes/timings without carrying private text."""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from .ai_engine.local import LocalLLMEngine
from .services import _safe_ollama_metrics


class PromptDiagnosticsTests(SimpleTestCase):
    def test_service_diagnostics_reject_text_objects_and_nonfinite_values(self):
        metrics = {
            "input_chars": "PRIVATE_PROMPT", "system_chars": {"text": "PRIVATE_RULE"},
            "prompt_eval_count": ["PRIVATE_DOCUMENT"], "eval_count": True,
            "load_duration_ms": float("nan"), "tokens_per_sec": float("inf"),
            "done_reason": "PRIVATE_MEMORY", "credentials": "PRIVATE_CREDENTIAL",
            "input_messages": 4, "eval_duration_ms": 12.5,
        }
        self.assertEqual(_safe_ollama_metrics(metrics), {
            "input_messages": 4, "eval_duration_ms": 12.5,
        })
        self.assertEqual(_safe_ollama_metrics({"done_reason": "stop"}), {"done_reason": "stop"})

    @patch("assistant.ai_engine.local.urllib.request.urlopen")
    def test_batch_and_stream_count_only_actual_sent_messages(self, urlopen):
        payload = {"done": True, "done_reason": "length",
                   "message": {"content": "Answer."}, "eval_count": 8}
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(payload).encode()
        response.__iter__.side_effect = lambda: iter([json.dumps(payload).encode()])
        urlopen.return_value = response
        engine = LocalLLMEngine()
        history = [{"role": "USER", "content": "PRIVATE_HISTORY"}] * 12
        kwargs = dict(conversation_history=history, system_instruction="PRIVATE_RULE")
        results = [engine.generate("PRIVATE_QUERY", **kwargs),
                   list(engine.generate_stream("PRIVATE_QUERY", **kwargs))[-1].result]
        for call, result in zip(urlopen.call_args_list, results):
            messages = json.loads(call.args[0].data)["messages"]
            self.assertEqual(result.metrics["input_messages"], 12)
            self.assertEqual(result.metrics["input_chars"], sum(len(m["content"]) for m in messages))
            self.assertEqual(result.metrics["system_chars"], len(messages[0]["content"]))
            self.assertEqual(result.metrics["done_reason"], "length")
            self.assertNotIn("PRIVATE", str(result.metrics))

    def test_unknown_completion_reason_cannot_enter_diagnostics(self):
        engine = LocalLLMEngine()
        with self.assertLogs("assistant.ai_engine.local", level="INFO") as logs:
            metrics = engine._log_performance_metrics({
                "done_reason": "PRIVATE_BACKEND_TEXT", "eval_count": 1,
            })
        self.assertNotIn("done_reason", metrics)
        self.assertNotIn("PRIVATE_BACKEND_TEXT", str(logs.output))
