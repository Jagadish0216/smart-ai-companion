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
    def trial(self, *, warm_temp=49, incomplete=False, abort=False, clock=None, failure=None, returncode=0, missing=False, expired=False, monitor_reason=None):
        seen = []
        process = Mock(returncode=returncode)
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
            if failure:
                data = json.loads(target.read_text())
                data.update(answer="", stream_done=False, metrics={}, total_response_seconds=20.03,
                            abort_reason=failure, error_type="TimeoutError" if "timeout" in failure else "RuntimeError")
                data.pop("first_meaningful_text_ms", None)
                target.write_text(json.dumps(data))
            if missing:
                target.unlink()
            return process
        def residents(host, model, digest, phase=None):
            if seen and expired and phase == 'warm':
                raise pair.models.TrialRefused('premature_model_expiry')
            return [] if not seen and phase == 'cold' else RESIDENT
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(output=str(Path(directory)/'report'), host='http://127.0.0.1:11434')
            with patch.object(pair.quality, 'require_pi'), patch.object(pair.models, 'acquire_trial_lock', return_value=Mock()), \
                 patch('scripts.validate_pi_performance.isolated_database', return_value=nullcontext()), \
                 patch('scripts.benchmark_inference_ab.telemetry', side_effect=lambda: point(temperature_c=warm_temp if seen else 49)), \
                 patch.object(pair.models, 'inventory', return_value={pair.short.MODEL:INSTALLED}), \
                 patch.object(pair.quality, 'resident', side_effect=residents), \
                 patch.object(pair.subprocess, 'Popen', side_effect=launch), \
                 patch.object(pair.short, 'monitor', side_effect=pair.models.TrialRefused(monitor_reason or 'temperature_safety_margin') if abort or monitor_reason else None), \
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

    def test_supplied_cold_failure_not_masked_by_empty_residency(self):
        seen, report = self.trial(failure='cold_load_timeout', returncode=2, expired=True)
        self.assertEqual(seen, ['cold'])
        self.assertEqual(report['abort_reason'], 'cold_load_timeout')
        cold = report['phases'][0]
        self.assertEqual(cold['abort_reason'], 'cold_load_timeout')
        self.assertEqual(cold['sample']['total_response_seconds'], 20.03)
        self.assertEqual(cold['sample']['answer'], '')
        self.assertEqual(cold['sample']['error_type'], 'TimeoutError')
        self.assertNotIn('resident_after', cold)
        self.assertTrue(cold['telemetry'])

    def test_failure_categories_precede_post_completion_residency(self):
        for failure in ('worker_failure', 'http_socket_timeout', 'worker_cancelled'):
            seen, report = self.trial(failure=failure, returncode=2, expired=True)
            self.assertEqual(seen, ['cold'])
            self.assertEqual(report['abort_reason'], failure)
        seen, report = self.trial(incomplete=True, returncode=2, expired=True)
        self.assertEqual(seen, ['cold'])
        self.assertEqual(report['abort_reason'], 'incomplete_answer')
        self.assertEqual(report['phases'][0]['sample']['answer'], 'New Delhi')

    def test_missing_worker_checkpoint_is_worker_failure(self):
        seen, report = self.trial(missing=True, returncode=1, expired=True)
        self.assertEqual(seen, ['cold'])
        self.assertEqual(report['abort_reason'], 'worker_failure')

    def test_completed_cold_still_requires_residency(self):
        seen, report = self.trial(expired=True)
        self.assertEqual(seen, ['cold'])
        self.assertEqual(report['abort_reason'], 'premature_model_expiry')
        self.assertTrue(report['phases'][0]['sample']['complete'])

    def test_monitored_request_deadline_distinct_from_session_deadline(self):
        seen, report = self.trial(monitor_reason='bounded_cycle_timeout', clock=Mock(side_effect=[0, 0, 30]))
        self.assertEqual(seen, ['cold'])
        self.assertEqual(report['abort_reason'], 'request_deadline')
        self.assertEqual(report['phases'][0]['sample']['answer'], 'New Delhi')
        seen, report = self.trial(monitor_reason='bounded_cycle_timeout', clock=Mock(side_effect=[0, 40, 60]))
        self.assertEqual(seen, ['cold'])
        self.assertEqual(report['abort_reason'], 'session_deadline')

    def test_worker_http_timeouts_bounded_inside_unchanged_monitor(self):
        for phase in ('cold', 'warm'):
            args = SimpleNamespace(host='http://127.0.0.1:11434', digest=DIGEST,
                                   phase=phase, child_result=Path('unused'))
            with patch.object(pair.quality, 'require_pi'), \
                 patch.object(pair.models, 'inventory', return_value={pair.short.MODEL: INSTALLED}), \
                 patch.object(pair.short, 'infer', return_value=0) as infer:
                pair.worker(args)
            timeout = infer.call_args.kwargs['http_timeout']
            self.assertEqual(timeout, 25 if phase == 'cold' else 20)
            self.assertLess(timeout, pair.short.RUNTIME_SECONDS)
        self.assertEqual(pair.short.RUNTIME_SECONDS, 30)
        self.assertEqual(pair.SESSION_SECONDS, 60)
