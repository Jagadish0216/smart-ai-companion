"""Virtual-clock timeout, partial-report and cleanup regressions; no real Ollama."""
from contextlib import ExitStack, nullcontext
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from assistant import model_benchmark as benchmark
from assistant.ai_engine.base import AIEngineResult, AIEngineStreamEvent


class ManagementTimeoutTests(SimpleTestCase):
    @patch('assistant.model_benchmark.api')
    @patch('assistant.model_benchmark.residents')
    @patch('assistant.model_benchmark.time.monotonic')
    @patch('assistant.model_benchmark.threading.Event')
    def test_unload_over_ten_seconds_has_shared_finite_deadline(self, event, clock, residents, api):
        now=[0.0]
        clock.side_effect=lambda:now[0]
        def command(*args,**kwargs):
            now[0]+=12  # A valid slow CPU unload must no longer fail at 10s.
            return {}
        api.side_effect=command
        answers=iter([[benchmark.MODELS[0]], []])
        def residency(*args,**kwargs):
            now[0]+=11
            return next(answers)
        residents.side_effect=residency
        benchmark.unload(MagicMock(),benchmark.MODELS[0])
        self.assertEqual(api.call_args.kwargs['timeout'],60)
        self.assertEqual(residents.call_count,2)
        for call in residents.call_args_list:
            self.assertGreater(call.kwargs['timeout'],0)
            self.assertLessEqual(call.kwargs['timeout'],20)

    @patch('assistant.model_benchmark.api')
    @patch('assistant.model_benchmark.residents')
    @patch('assistant.model_benchmark.time.monotonic')
    @patch('assistant.model_benchmark.threading.Event')
    def test_unload_residency_never_restarts_its_ninety_second_budget(self,event,clock,residents,api):
        now=[0.0]; clock.side_effect=lambda:now[0]
        def residency(*args,**kwargs):
            now[0]+=31
            return [benchmark.MODELS[0]]
        residents.side_effect=residency
        with self.assertRaises(benchmark.BenchmarkFailure) as exc:
            benchmark.unload(MagicMock(),benchmark.MODELS[0])
        self.assertEqual(exc.exception.diagnostic['operation'],'unload_residency')
        self.assertEqual(exc.exception.diagnostic['category'],'timeout')
        self.assertEqual(residents.call_count,3)
        self.assertEqual(residents.call_args.kwargs['timeout'],20)

    @patch('assistant.model_benchmark.request.urlopen',side_effect=TimeoutError('secret endpoint'))
    def test_residency_timeout_exposes_operation_model_and_no_exception_body(self,http):
        engine=benchmark.BenchmarkEngine(host='http://localhost:11434')
        with self.assertRaises(benchmark.BenchmarkFailure) as exc:
            benchmark.residents(engine,model=benchmark.MODELS[1])
        data=exc.exception.diagnostic
        self.assertEqual(data['operation'],'residency_check')
        self.assertEqual(data['model'],benchmark.MODELS[1])
        self.assertEqual(data['category'],'timeout')
        self.assertGreaterEqual(data['elapsed_ms'],0)
        self.assertNotIn('secret',str(exc.exception))
        self.assertEqual(http.call_args.kwargs['timeout'],20)

    @patch('assistant.model_benchmark.request.urlopen',side_effect=TimeoutError())
    def test_generation_timeout_is_independent_and_labeled(self,http):
        engine=benchmark.BenchmarkEngine()
        with self.assertRaises(benchmark.BenchmarkFailure) as exc:
            list(engine.generate_stream('fixed',system_instruction='brief',num_predict=64,
                model=benchmark.MODELS[0],timeout_seconds=37))
        self.assertEqual(http.call_args.kwargs['timeout'],37)
        self.assertEqual(exc.exception.diagnostic['operation'],'generation')
        self.assertEqual(exc.exception.diagnostic['category'],'timeout')

    @patch('assistant.model_benchmark.request.urlopen')
    @patch('assistant.model_benchmark.threading.Timer')
    def test_watchdog_eof_is_timeout_not_invalid_response(self,timer,http):
        response=http.return_value.__enter__.return_value
        response.readline.side_effect=lambda *args:(timer.call_args.args[1](), b'')[1]
        engine=benchmark.BenchmarkEngine()
        with self.assertRaises(benchmark.BenchmarkFailure) as exc:
            list(engine.generate_stream('fixed',system_instruction='brief',num_predict=64,
                model=benchmark.MODELS[0],timeout_seconds=5))
        self.assertEqual(exc.exception.diagnostic['category'],'timeout')
        timer.return_value.cancel.assert_called_once()

    @patch('assistant.model_benchmark.threading.Event')
    @override_settings(AI_ENGINE='local')
    def test_resource_snapshot_wait_is_finite(self,event):
        event.return_value.wait.return_value=False
        with patch('assistant.model_benchmark.threading.Thread'):
            with self.assertRaises(benchmark.BenchmarkFailure) as exc:
                benchmark.check_resources()
        self.assertEqual(exc.exception.diagnostic['operation'],'resource_check')
        self.assertEqual(exc.exception.diagnostic['category'],'timeout')
        event.return_value.wait.assert_called_once_with(15)
        # Fake thread never runs its finally; release the test's acquired slot.
        benchmark._RESOURCE_SLOT.release()

    @patch('assistant.model_benchmark.os.name','posix')
    @patch('assistant.model_benchmark.subprocess.run')
    def test_failed_and_inactive_units_are_quiescent_but_missing_units_are_not(self,run):
        for state in ('inactive','failed'):
            run.return_value=SimpleNamespace(returncode=3,stdout=state)
            benchmark.maintenance_guard()
        for code,state in ((4,'inactive'),(0,'active'),(3,'deactivating')):
            run.return_value=SimpleNamespace(returncode=code,stdout=state)
            with self.assertRaises(benchmark.BenchmarkError):benchmark.maintenance_guard()


class WorkflowTests(SimpleTestCase):
    def harness(self, previous=()):
        stack=ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch('assistant.model_benchmark.maintenance_guard'))
        stack.enter_context(patch('assistant.voice.push_to_talk.microphone_ownership',return_value=nullcontext()))
        stack.enter_context(patch('assistant.model_benchmark.installed_models',return_value=set(benchmark.MODELS)))
        engine=stack.enter_context(patch('assistant.model_benchmark.BenchmarkEngine')).return_value
        engine._host='http://localhost:11434'
        first=[True]
        def residency(*args,**kwargs):
            value=list(previous) if first[0] else []
            first[0]=False
            return value
        stack.enter_context(patch('assistant.model_benchmark.residents',side_effect=residency))
        unload=stack.enter_context(patch('assistant.model_benchmark.unload'))
        api=stack.enter_context(patch('assistant.model_benchmark.api'))
        stack.enter_context(patch('assistant.model_benchmark.check_resources',return_value=SimpleNamespace(max_output_tokens=128,timeout_seconds=110)))
        sampler=stack.enter_context(patch('assistant.model_benchmark.Samples')).return_value
        sampler.unsafe=False; sampler.finish.return_value={'throttled_bits':0}
        def generate(*args,**kwargs):
            text='BCM2712 quad-core Cortex-A76 at 2.4 GHz. An IoT MQTT broker uses publish and subscribe. ESP32 is a microcontroller.'
            yield AIEngineStreamEvent(type='done',result=AIEngineResult(text=text,engine='local',ttft_ms=8,metrics={'tokens_per_sec':4}))
        engine.generate_stream.side_effect=generate
        return engine,unload,api,sampler

    def test_quick_has_eighteen_sequential_trials_and_summary(self):
        engine,unload,api,sampler=self.harness()
        rows=[];benchmark.run_benchmark(rows.append,quick=True)
        samples=[r for r in rows if r['type']=='sample']
        self.assertEqual(len(samples),18)
        self.assertEqual({r['case'] for r in samples},{'pi5_cpu','iot','esp32'})
        self.assertEqual([r['phase'] for r in samples],['cold','warm']*9)
        self.assertEqual([r['model'] for r in samples],[model for model in benchmark.MODELS for _ in range(6)])
        self.assertTrue(all(r['mode']=='quick' for r in samples))
        self.assertEqual(rows[-1]['mode'],'quick')
        self.assertTrue(rows[-1]['complete'])
        self.assertEqual(rows[-1]['candidate_for_manual_review'],benchmark.MODELS[0])
        for row in rows:
            if row['type']=='progress':self.assertTrue({'operation','model','case','phase'}<=row.keys())
        self.assertEqual(engine.generate_stream.call_count,18)

    def test_generation_failure_survives_both_sampler_and_unload_failures(self):
        engine,unload,api,sampler=self.harness()
        engine.generate_stream.side_effect=TimeoutError('private-primary')
        sampler.finish.side_effect=RuntimeError('private-sampler')
        unload.side_effect=[None,TimeoutError('private-cleanup')]
        rows=[]
        with self.assertRaises(benchmark.BenchmarkFailure) as exc:
            benchmark.run_benchmark(rows.append,quick=True)
        self.assertEqual(exc.exception.diagnostic['operation'],'generation')
        self.assertEqual(exc.exception.diagnostic['category'],'timeout')
        self.assertNotIn('private',json.dumps(rows))
        self.assertTrue(any(r['type']=='cleanup_error' and r['operation']=='sampler_cleanup' for r in rows))
        self.assertTrue(any(r['type']=='cleanup_error' and r['operation']=='cleanup_unload' for r in rows))
        self.assertEqual(engine.generate_stream.call_count,1)
        api.assert_not_called()

    def test_restore_timeout_is_labeled_and_preserves_all_completed_samples(self):
        engine,unload,api,sampler=self.harness(previous=[benchmark.MODELS[1]])
        api.side_effect=TimeoutError('private-restore')
        rows=[]
        with self.assertRaises(benchmark.BenchmarkFailure) as exc:
            benchmark.run_benchmark(rows.append,quick=True)
        self.assertEqual(exc.exception.diagnostic['operation'],'restore_residency')
        self.assertEqual(api.call_args.kwargs['timeout'],180)
        self.assertEqual(len([r for r in rows if r['type']=='sample']),18)
        self.assertFalse(rows[-1]['complete'])
        self.assertIsNone(rows[-1]['candidate_for_manual_review'])

    def test_interrupt_attempts_cleanup_and_never_replaces_interrupt(self):
        engine,unload,api,sampler=self.harness()
        engine.generate_stream.side_effect=KeyboardInterrupt()
        unload.side_effect=[None,TimeoutError()]
        with self.assertRaises(KeyboardInterrupt):benchmark.run_benchmark(lambda row:None,quick=True)
        self.assertEqual(unload.call_count,2)

    def test_unexpected_primary_exception_is_not_replaced_by_cleanup_timeout(self):
        engine,unload,api,sampler=self.harness()
        engine.generate_stream.side_effect=RuntimeError('private-primary')
        unload.side_effect=[None,TimeoutError('private-cleanup')]
        with self.assertRaises(benchmark.BenchmarkFailure) as exc:
            benchmark.run_benchmark(lambda row:None,quick=True)
        self.assertEqual(exc.exception.diagnostic['category'],'unexpected_error')
        self.assertEqual(exc.exception.diagnostic['operation'],'generation')
        self.assertNotIn('private',str(exc.exception))

    def test_residency_restore_timeout_cannot_replace_generation_failure(self):
        engine,unload,api,sampler=self.harness(previous=[benchmark.MODELS[1]])
        engine.generate_stream.side_effect=RuntimeError('private-generation')
        api.side_effect=TimeoutError('private-restore')
        rows=[]
        with self.assertRaises(benchmark.BenchmarkFailure) as exc:
            benchmark.run_benchmark(rows.append,quick=True)
        self.assertEqual(exc.exception.diagnostic['operation'],'generation')
        self.assertEqual(exc.exception.diagnostic['category'],'unexpected_error')
        self.assertTrue(any(row['type']=='cleanup_error' and row['operation']=='restore_residency' for row in rows))
        self.assertNotIn('private',json.dumps(rows))

    def test_completed_sample_precedes_later_failure_and_summary_has_no_winner(self):
        engine,unload,api,sampler=self.harness()
        good=engine.generate_stream.side_effect
        calls=[0]
        def generate(*args,**kwargs):
            calls[0]+=1
            if calls[0]>1:raise TimeoutError()
            yield from good(*args,**kwargs)
        engine.generate_stream.side_effect=generate
        rows=[]
        with self.assertRaises(benchmark.BenchmarkFailure):benchmark.run_benchmark(rows.append,quick=True)
        self.assertEqual(len([r for r in rows if r['type']=='sample']),1)
        self.assertTrue(any(r['type']=='summary' and not r['complete'] and r['candidate_for_manual_review'] is None for r in rows))


class ReportTests(SimpleTestCase):
    @patch('assistant.management.commands.benchmark_models.run_benchmark')
    def test_each_sample_is_on_disk_before_next_trial_and_retained_on_timeout(self,run):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'quick.jsonl'
            def benchmark_run(emit,*args,**kwargs):
                emit({'type':'sample','model':'fixed','wall_ms':12})
                self.assertEqual(json.loads(path.read_text())['wall_ms'],12)
                self.assertTrue(kwargs['quick'])
                raise TimeoutError('secret')
            run.side_effect=benchmark_run
            with self.assertRaises(CommandError) as exc:
                call_command('benchmark_models',quick=True,output=str(path),stdout=io.StringIO())
            self.assertNotIn('secret',str(exc.exception))
            self.assertEqual(len(path.read_text().splitlines()),1)

    def test_existing_report_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'existing.jsonl';path.write_text('keep')
            with self.assertRaises(CommandError):call_command('benchmark_models',output=str(path),stdout=io.StringIO())
            self.assertEqual(path.read_text(),'keep')
