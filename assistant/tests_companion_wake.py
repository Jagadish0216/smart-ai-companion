"""Integration fixtures only: no trained or acoustically validated model here."""
from contextlib import ExitStack
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import wave
from django.core.management import call_command
from django.test import SimpleTestCase
from assistant.voice.wakeword import companion
from assistant.voice.wakeword.base import WakeWordConfig, WakeWordError, WakeWordCleanupError, WakeWordDetection
from assistant.voice.wakeword.openwakeword_provider import OpenWakeWordProvider
from assistant.tests_wakeword import native_pcm
from scripts import voice_loop


class CompanionValidationTests(SimpleTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'hey_companion.onnx'
        self.path.write_bytes(b'NOT A TRAINED MODEL - TEST FIXTURE')
        self.config = companion.candidate_config(self.path)
        self.report_path = self.root / 'report.json'
        self.metrics = dict(positives=40, recall=.95, negative_hours=5,
                            false_activations_per_hour=0, p95_frame_seconds=.01)
        self.report = dict(profile=companion.PROFILE, phrase=companion.PHRASE, runtime={'fixture': True},
                           threshold=.6, metrics=self.metrics, accepted_on_pi=True)

    def approve(self):
        self.report_path.write_text(json.dumps(self.report))
        return replace(self.config, validation_report=str(self.report_path))

    def test_missing_model_or_report_never_falls_back_to_jarvis(self):
        with self.assertRaises(WakeWordError): companion.candidate_config(self.root / 'absent.onnx')
        with self.assertRaises(WakeWordError): companion.approved_config(self.path, self.report_path)
        with self.assertRaises(WakeWordError): companion.candidate_config(None)

    @patch.object(companion, 'load_engine')
    @patch.object(companion, 'fingerprint', return_value={'fixture': True})
    def test_approval_matches_model_assets_runtime_threshold_and_phrase(self, fingerprint, engine):
        config = self.approve()
        companion.verify_approval(config)
        self.assertEqual(companion.approved_config(self.path, self.report_path), config)
        for key, value in [('phrase', 'Hey Jarvis'), ('accepted_on_pi', False), ('profile', 'old'),
                           ('threshold', .7), ('runtime', {'changed': True})]:
            with self.subTest(key=key):
                original = self.report[key]
                self.report[key] = value
                with self.assertRaises(WakeWordError): companion.verify_approval(self.approve())
                self.report[key] = original

    def test_nonfinite_incomplete_and_failing_metrics_cannot_enable(self):
        for key, value in [('positives', 39), ('recall', .89), ('negative_hours', 4.9),
                           ('false_activations_per_hour', .21), ('p95_frame_seconds', .081),
                           ('recall', float('nan')), ('recall', True)]:
            with self.subTest(key=key, value=value):
                self.assertFalse(companion.passing_metrics({**self.metrics, key: value}))
        self.assertFalse(companion.passing_metrics(None))
        self.assertFalse(companion.passing_metrics({}))

    def test_renamed_installed_builtin_is_rejected_by_hash(self):
        jarvis = self.root / 'hey_jarvis.onnx'; jarvis.write_bytes(self.path.read_bytes())
        engine = SimpleNamespace(FEATURE_MODELS={}, VAD_MODELS={}, MODELS={'hey_jarvis': {'model_path': str(jarvis)}})
        with self.assertRaisesRegex(WakeWordError, 'substituted'):
            companion.fingerprint(self.config, engine)

    def test_changed_model_invalidates_fingerprint(self):
        engine = SimpleNamespace(FEATURE_MODELS={}, VAD_MODELS={}, MODELS={})
        with patch.object(companion, 'version', return_value='fixture'):
            first = companion.fingerprint(self.config, engine)
            self.path.write_bytes(b'CHANGED TEST FIXTURE')
            self.assertNotEqual(first, companion.fingerprint(self.config, engine))

    def test_incompatible_output_or_nan_fails_actual_prediction_check(self):
        model = MagicMock()
        for scores in ({'hey_jarvis': .9}, {'hey_companion': float('nan')}, {'hey_companion': 2}):
            model.predict.return_value = scores
            with self.assertRaises(WakeWordError): companion.score(model, bytes(2560))

    def test_gate_fails_before_engine_or_microphone_warmup(self):
        provider = OpenWakeWordProvider(self.config)
        with patch('assistant.voice.wakeword.openwakeword_provider.load_engine') as engine, \
                patch('assistant.voice.wakeword.openwakeword_provider.AlsaWakeCapture') as capture:
            with self.assertRaises(WakeWordError): provider.wait_for_wake_word('mic')
        engine.assert_not_called(); capture.assert_not_called()

    def test_evaluation_runs_real_frame_contract_and_never_approves_small_fixture_corpus(self):
        training = self.root / 'training.json'
        training.write_text(json.dumps(dict(phrase=companion.PHRASE, model_sha256='fixture',
                                           training_source='UNIT TEST ONLY', held_out_real_recordings=True)))
        for category, pcm in [('positive', b'\x01\0'), ('negative', b'\x02\0')]:
            folder = self.root / category; folder.mkdir()
            with wave.open(str(folder / 'fixture.wav'), 'wb') as audio:
                audio.setnchannels(1); audio.setframerate(16000); audio.setsampwidth(2)
                audio.writeframes(pcm * 3840)
        engine = MagicMock()
        engine.Model.return_value.predict.side_effect = [{'hey_companion': 0}] + [{'hey_companion': .9}]*2 + [{'hey_companion': 0}]*3
        with patch.object(companion, 'fingerprint', return_value={'model_sha256': 'fixture'}), \
                patch.object(companion, 'load_engine', return_value=engine):
            report = companion.evaluate(self.config, self.root, training, accept_pi=True)
        self.assertEqual(report['metrics']['recall'], 1)
        self.assertEqual(report['metrics']['false_activations_per_hour'], 0)
        self.assertFalse(report['accepted_on_pi'])
        self.assertNotIn(str(self.root), json.dumps(report))

    def test_command_is_offline_and_default_report_does_not_attest_acceptance(self):
        with patch('assistant.management.commands.validate_companion_wake.evaluate', return_value={
                **self.report, 'accepted_on_pi': False}) as evaluate, \
                patch('assistant.voice.wakeword.models.urllib.request.urlopen') as network:
            call_command('validate_companion_wake', model=self.path, corpus=self.root,
                         training_record=self.root/'training.json', report=self.report_path, stdout=io.StringIO())
        self.assertFalse(evaluate.call_args.kwargs['accept_pi']); network.assert_not_called()
        self.assertFalse(json.loads(self.report_path.read_text())['accepted_on_pi'])


class CompanionDetectionLifecycleTests(SimpleTestCase):
    def provider(self):
        provider = OpenWakeWordProvider(WakeWordConfig(enabled=True, model='hey_companion', model_path='hey_companion.onnx'))
        provider.model = MagicMock() # Explicit fixture bypass; never a real model acceptance.
        return provider

    def test_requires_two_consecutive_non_noise_scores_and_releases_before_request(self):
        provider = self.provider()
        provider.model.predict.side_effect = [{'hey_companion': s} for s in (.9, .1, float('nan'), .9, .9)]
        with patch('assistant.voice.wakeword.openwakeword_provider.AlsaWakeCapture') as factory:
            factory.return_value.chunks.return_value = iter([native_pcm()] * 5)
            self.assertTrue(provider.wait_for_wake_word('mic').detected)
            self.assertEqual(provider.model.predict.call_count, 5)
            factory.return_value.close.assert_called_once()
        self.assertIsNone(provider.capture)

    def test_cancellation_and_repeated_restart_close_previous_capture(self):
        provider = self.provider(); cancel = Event(); cancel.set()
        with patch('assistant.voice.wakeword.openwakeword_provider.AlsaWakeCapture') as factory:
            factory.return_value.chunks.return_value = iter([])
            self.assertFalse(provider.wait_for_wake_word('mic', cancel_event=cancel).detected)
            provider.reset()
            factory.return_value.chunks.return_value = iter([])
            self.assertFalse(provider.wait_for_wake_word('mic', cancel_event=cancel).detected)
            self.assertEqual(factory.return_value.close.call_count, 2)
        provider.close(); provider.close()

    def test_playback_is_serial_then_cooldown_before_detector_reset_and_rearm(self):
        provider = MagicMock(); events = []
        provider.wait_for_wake_word.side_effect = [WakeWordDetection(True), KeyboardInterrupt()]
        config = voice_loop.VoiceLoopConfig('source', audio_backend='alsa')
        def cycle(*a, **k):
            events.append('playback-drained')
            self.assertEqual(provider.wait_for_wake_word.call_count, 1)
            return 7
        provider.reset.side_effect = lambda: events.append('reset')
        with patch('scripts.voice_loop.run_voice_cycle', side_effect=cycle), \
                patch('scripts.voice_loop.time.sleep', side_effect=lambda seconds: events.append(seconds)), \
                patch('builtins.print'):
            with self.assertRaises(KeyboardInterrupt):
                voice_loop.run_hands_free(config, WakeWordConfig(cooldown_seconds=1.5), provider, None)
        self.assertEqual(events, ['playback-drained', 1.5, 'reset'])

    def test_uncertain_capture_cleanup_never_reopens_or_attempts_fallback(self):
        provider = MagicMock(); provider.wait_for_wake_word.side_effect = WakeWordCleanupError('unreaped')
        with patch('builtins.print'):
            with self.assertRaises(WakeWordCleanupError):
                voice_loop.run_hands_free(voice_loop.VoiceLoopConfig('mic', audio_backend='alsa'), WakeWordConfig(), provider, None)
        provider.wait_for_wake_word.assert_called_once()

    def test_cli_missing_approval_fails_closed_and_default_ptt_does_not_load_wake(self):
        config = voice_loop.VoiceLoopConfig('source', audio_backend='alsa')
        with ExitStack() as stack:
            stack.enter_context(patch('scripts.voice_loop.sys.platform', 'win32'))
            stack.enter_context(patch('scripts.voice_loop.VoiceLoopConfig.from_settings', return_value=config))
            stack.enter_context(patch('builtins.print'))
            wake = stack.enter_context(patch('scripts.voice_loop.get_wake_word_provider'))
            self.assertEqual(voice_loop.main(['--hey-companion']), 1)
            wake.assert_not_called()
            stack.enter_context(patch('scripts.voice_loop.get_companion_display'))
            stack.enter_context(patch('scripts.voice_loop.get_tts_provider'))
            stack.enter_context(patch('scripts.voice_loop.get_stt_provider'))
            stack.enter_context(patch('assistant.voice.push_to_talk.TalkButton'))
            stack.enter_context(patch('scripts.voice_loop.run_push_to_talk', side_effect=KeyboardInterrupt))
            self.assertEqual(voice_loop.main(['--push-to-talk']), 130)
            wake.assert_not_called()

    def test_service_style_restart_revalidates_and_releases_mic_and_providers(self):
        config = voice_loop.VoiceLoopConfig('source', audio_backend='alsa')
        approved = WakeWordConfig(enabled=True, model='hey_companion', model_path='hey_companion.onnx')
        with ExitStack() as stack:
            stack.enter_context(patch('scripts.voice_loop.sys.platform', 'linux'))
            stack.enter_context(patch('signal.signal'))
            stack.enter_context(patch('scripts.voice_loop.VoiceLoopConfig.from_settings', return_value=config))
            stack.enter_context(patch('scripts.voice_loop.get_companion_display'))
            stack.enter_context(patch('scripts.voice_loop.close_tts_provider'))
            stack.enter_context(patch('builtins.print'))
            validate = stack.enter_context(patch.object(companion, 'approved_config', return_value=approved))
            ownership = stack.enter_context(patch('assistant.voice.push_to_talk.microphone_ownership'))
            wake = stack.enter_context(patch('scripts.voice_loop.get_wake_word_provider')).return_value
            tts = stack.enter_context(patch('scripts.voice_loop.get_tts_provider')).return_value
            run = stack.enter_context(patch('scripts.voice_loop.run_hands_free', side_effect=KeyboardInterrupt))
            for _ in range(2):
                self.assertEqual(voice_loop.main(['--hey-companion', '--companion-model', 'hey_companion.onnx',
                                                  '--companion-validation', 'report.json']), 130)
        self.assertEqual(validate.call_count, 2)
        self.assertEqual(ownership.return_value.__enter__.call_count, 2)
        self.assertEqual(ownership.return_value.__exit__.call_count, 2)
        self.assertEqual(wake.close.call_count, 2)
        self.assertEqual(tts.close.call_count, 2)
        self.assertEqual(run.call_count, 2)

    def test_cli_cannot_enable_concurrent_speaker_monitor_or_ptt(self):
        for options in (['--hey-companion', '--voice-barge-in'], ['--hey-companion', '--push-to-talk']):
            with self.subTest(options=options), patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
                voice_loop.main(options)

    def test_mocked_linux_lock_prevents_second_owner_and_releases_on_cancel(self):
        from assistant.voice.push_to_talk import microphone_ownership
        held = []
        def flock(stream, mode):
            if mode == 8: held.clear()
            elif held: raise BlockingIOError()
            else: held.append(stream)
        fcntl = SimpleNamespace(flock=flock, LOCK_EX=2, LOCK_NB=4, LOCK_UN=8)
        with tempfile.TemporaryDirectory() as directory, patch('pathlib.Path.home', return_value=Path(directory)), \
                patch.dict('sys.modules', {'fcntl': fcntl}):
            with self.assertRaises(KeyboardInterrupt):
                with microphone_ownership():
                    with self.assertRaisesRegex(RuntimeError, 'owns the microphone'):
                        with microphone_ownership(): self.fail('Second owner admitted')
                    raise KeyboardInterrupt()
            with microphone_ownership(): pass # A subsequent service process can acquire it.
        self.assertEqual(held, [])
