"""Socket error preservation tests; no real HTTP or Pi inference."""
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch
import urllib.error
from django.test import SimpleTestCase
from assistant.tests_short_answer_thermal import INSTALLED, DIGEST
from assistant.tests_quality_evaluation import point
from scripts import test_short_answer_thermal as short


class SocketFailureTests(SimpleTestCase):
    def invoke(self, error, *, partial=False, phase='cold'):
        opener = Mock()
        if partial:
            class Stream:
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def __iter__(self):
                    yield json.dumps({'message': {'content': 'New Delhi'}}).encode()
                    raise error
            opener.open.return_value = Stream()
        else:
            opener.open.side_effect = error
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'sample.json'
            with patch.object(short.quality, 'require_pi'), \
                 patch.object(short.models, 'inventory', return_value={short.MODEL:INSTALLED}), \
                 patch.object(short.quality, 'resident', return_value=[]), \
                 patch('scripts.benchmark_inference_ab.telemetry', side_effect=point):
                with self.assertRaises(type(error)):
                    short.infer('http://127.0.0.1:11434', DIGEST, target, opener=opener,
                                phase=phase, http_timeout=25 if phase == 'cold' else 20)
            sample = json.loads(target.read_text())
        opener.open.assert_called_once()
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 25 if phase == 'cold' else 20)
        return sample

    def test_direct_and_wrapped_socket_timeouts_preserved(self):
        for error in (TimeoutError('timed out'), urllib.error.URLError(TimeoutError('timed out'))):
            sample = self.invoke(error)
            self.assertEqual(sample['abort_reason'], 'cold_load_timeout')
            self.assertIn('timed out', sample['error_detail'])
            self.assertFalse(sample['complete'])
            self.assertEqual(sample['answer'], '')

    def test_partial_stream_timeout_keeps_answer_and_latency(self):
        sample = self.invoke(TimeoutError('timed out'), partial=True)
        self.assertEqual(sample['answer'], 'New Delhi')
        self.assertIsNotNone(sample['first_meaningful_text_ms'])
        self.assertEqual(sample['abort_reason'], 'cold_load_timeout')

    def test_warm_timeout_distinct_from_cold_loading(self):
        self.assertEqual(self.invoke(TimeoutError('timed out'), phase='warm')['abort_reason'], 'http_socket_timeout')

    def test_non_timeout_not_misclassified(self):
        self.assertEqual(self.invoke(urllib.error.URLError(ConnectionRefusedError('refused')))['abort_reason'], 'worker_failure')

    def test_outcome_precedence(self):
        self.assertEqual(short.request_failure({'abort_reason':'cold_load_timeout'}, 2), 'cold_load_timeout')
        self.assertEqual(short.request_failure({}, -9), 'worker_failure')
        self.assertEqual(short.request_failure({}, 2), 'incomplete_answer')
