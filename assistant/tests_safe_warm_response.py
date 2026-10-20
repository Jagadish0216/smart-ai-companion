"""Windows mock-only safe pair coverage, including inherited streaming/monitor guards."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch
from assistant.tests_short_answer_thermal import ShortThermalTests, INSTALLED, DIGEST
from assistant.tests_quality_evaluation import point
from scripts import compare_safe_warm_response as pair

RESIDENT = [dict(name=pair.short.MODEL, digest=DIGEST, context_length=4096, size=123)]


class SafeWarmTests(ShortThermalTests):
    def trial(self, *, warm_temp=49, incomplete=False, abort=False, clock=None):
        seen = []
        process = Mock(returncode=0)
        def launch(command, **kwargs):
            phase = command[command.index('--phase') + 1]
            seen.append(phase)
            target = Path(command[command.index('--child-result') + 1])
            target.write_text(json.dumps(dict(answer='New Delhi' if phase == 'cold' else 'RAM is temporary working memory.',
                request=dict(messages=[dict(role='user', content=pair.QUESTIONS[phase == 'warm'])]),
                stream_done=not incomplete, first_meaningful_text_ms=100,
                total_response_seconds=1, metrics=dict(done_reason='stop', load_duration=1000,
                prompt_eval_duration=1000, eval_duration=1000000000, eval_count=8,
                prompt_eval_count=20, prompt_eval_cached_count=3))))
            return process
        def residents(host, model, digest, phase=None):
            return [] if not seen and phase == 'cold' else RESIDENT
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(output=str(Path(directory)/'report'), host='http://127.0.0.1:11434')
            with patch.object(pair.quality, 'require_pi'), patch.object(pair.models, 'acquire_trial_lock', return_value=Mock()), \
                 patch('scripts.validate_pi_performance.isolated_database', return_value=nullcontext()), \
                 patch('scripts.benchmark_inference_ab.telemetry', side_effect=lambda: point(temperature_c=warm_temp if seen else 49)), \
                 patch.object(pair.models, 'inventory', return_value={pair.short.MODEL:INSTALLED}), \
                 patch.object(pair.quality, 'resident', side_effect=residents), \
                 patch.object(pair.subprocess, 'Popen', side_effect=launch), \
                 patch.object(pair.short, 'monitor', side_effect=pair.models.TrialRefused('temperature_safety_margin') if abort else None), \
                 patch.object(pair.models, 'terminate'):
                pair.run(args, **({'clock': clock} if clock else {}))
            report = json.loads((Path(args.output)/'report.json').read_text())
        return seen, report

    def test_pair_sequence_and_accounting(self):
        seen, report = self.trial()
        self.assertEqual(seen, ['cold', 'warm'])
        self.assertEqual(report['status'], 'complete_manual_review_pending')
        samples = [e['sample'] for e in report['phases']]
        self.assertNotEqual(samples[0]['question_sha256'], samples[1]['question_sha256'])
        self.assertEqual(samples[1]['prompt_cache']['cached_tokens_reported'], 3)
        self.assertIsNone(samples[1]['correctness_review']['correct'])

    def test_pre_warm_refusal(self):
        seen, report = self.trial(warm_temp=55)
        self.assertEqual(seen, ['cold'])
        self.assertEqual(report['status'], 'warm_deferred_thermal')

    def test_incomplete_no_retry(self):
        seen, report = self.trial(incomplete=True)
        self.assertEqual(seen, ['cold'])
        self.assertIsNone(report['ratios'])
        self.assertEqual(report['phases'][0]['sample']['answer'], 'New Delhi')

    def test_abort_retains_partial(self):
        seen, report = self.trial(abort=True)
        self.assertEqual(seen, ['cold'])
        self.assertEqual(report['abort_reason'], 'temperature_safety_margin')
        self.assertEqual(report['phases'][0]['sample']['answer'], 'New Delhi')

    def test_session_deadline(self):
        seen, report = self.trial(clock=Mock(side_effect=[0, 60]))
        self.assertEqual(seen, [])
        self.assertEqual(report['abort_reason'], 'session_deadline')

    def test_missing_cache_is_unknown(self):
        sample = dict(metrics={'prompt_eval_count': 20})
        pair.summarize(sample, 'warm')
        self.assertFalse(sample['prompt_cache']['accounting_known'])
        self.assertIsNone(sample['prompt_cache']['cached_tokens_reported'])

    def test_warm_worker_does_not_require_fifty(self):
        pair.gate(point(temperature_c=54.9), INSTALLED, RESIDENT, 'warm')
        with self.assertRaises(pair.models.TrialRefused):
            pair.gate(point(temperature_c=50), INSTALLED, [], 'cold')

    def test_worker_fixed_distinct_payload_and_strict_start(self):
        args = SimpleNamespace(host='http://127.0.0.1:11434', digest=DIGEST,
                               phase='warm', child_result=Path('unused'))
        with patch.object(pair.quality, 'require_pi'), \
             patch.object(pair.models, 'inventory', return_value={pair.short.MODEL: INSTALLED}), \
             patch.object(pair.quality, 'resident', return_value=RESIDENT), \
             patch.object(pair.short, 'infer', return_value=0) as infer:
            self.assertEqual(pair.worker(args), 0)
            self.assertEqual(infer.call_args.kwargs['question'], pair.QUESTIONS[1])
            check = infer.call_args.kwargs['gate']
            check(point(temperature_c=49.9), INSTALLED, RESIDENT)
            with self.assertRaisesRegex(pair.models.TrialRefused, 'warm_deferred_thermal'):
                check(point(temperature_c=50), INSTALLED, RESIDENT)

    def test_warm_resident_identity_and_context(self):
        for resident in ([], [dict(RESIDENT[0], digest='b'*64)],
                         [dict(RESIDENT[0], context_length=2048)],
                         [dict(RESIDENT[0], name='llama3.2:1b')]):
            with patch.object(pair.models, 'metadata', return_value={'models':resident}):
                with self.assertRaises(pair.models.TrialRefused):
                    pair.quality.resident('host', pair.short.MODEL, DIGEST, 'warm')
