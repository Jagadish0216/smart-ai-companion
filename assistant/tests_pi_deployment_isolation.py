"""Voice fixtures remain deterministic under Pi audio settings; no devices used."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from assistant.test_support import VOICE_LOOP_TEST_SETTINGS
from assistant import tests_voice_stream as voice_tests
from scripts.voice_loop import VoiceLoopConfig, VoiceLoopError


PRODUCTION_AUDIO_ENV = {
    'VOICE_AUDIO_BACKEND': 'alsa',
    'VOICE_INPUT_SOURCE': '',
    'VOICE_ALSA_CAPTURE_DEVICE': 'hw:CARD=sndrpigooglevoi,DEV=0',
    'VOICE_ALSA_PLAYBACK_DEVICE': 'hw:CARD=sndrpigooglevoi,DEV=0',
    'VOICE_PLAYBACK_PROFILE': 'approved_loudness',
    'VOICE_PLAYBACK_GAIN': '4.0',
    'VOICE_CAPTURE_VAD_ENABLED': 'false',
    'VOICE_CAPTURE_VAD_ADAPTIVE': 'true',
    'VOICE_CAPTURE_PREPARATION_POLICY': 'legacy',
    'VOICE_CAPTURE_READY_LIFECYCLE': 'false',
    'VOICE_STREAM_FIRST_SENTENCE_MIN_CHARS': '',
}


class VoiceFixtureIsolationTests(SimpleTestCase):
    def test_original_tests_pass_with_both_production_speaker_profiles_and_restore_settings(self):
        for profile in ('approved_loudness', 'bounded_gain'):
            # A valid, opt-in production readiness setup is an additional source
            # of Pulse incompatibility if fixtures override only the backend.
            with override_settings(**{**VOICE_LOOP_TEST_SETTINGS,
                    'VOICE_AUDIO_BACKEND':'alsa', 'VOICE_PLAYBACK_PROFILE':profile,
                    'VOICE_CAPTURE_PREPARATION_POLICY':'settled_fan',
                    'VOICE_CAPTURE_VAD_ENABLED':True, 'VOICE_CAPTURE_READY_LIFECYCLE':True}):
                for cls, name in ((voice_tests.FirstClauseBufferTests, 'test_first_chunk_settings_and_validation'),
                                  (voice_tests.StreamingBufferTests, 'test_target_setting_and_validation')):
                    result = unittest.TestResult()
                    cls(name).run(result)
                    self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
                    self.assertEqual(settings.VOICE_AUDIO_BACKEND, 'alsa')
                    self.assertEqual(settings.VOICE_PLAYBACK_PROFILE, profile)
                    self.assertTrue(settings.VOICE_CAPTURE_READY_LIFECYCLE)

    @override_settings(**VOICE_LOOP_TEST_SETTINGS)
    def test_legacy_pulse_baseline_is_valid_without_reading_appliance_settings(self):
        config = VoiceLoopConfig.from_settings()
        self.assertEqual(config.audio_backend, 'pulse')
        self.assertEqual(config.playback_profile, 'legacy')
        self.assertEqual(config.input_source, 'test-source')

    @override_settings(**VOICE_LOOP_TEST_SETTINGS)
    def test_configured_alsa_profiles_keep_devices_gain_and_capture_readiness(self):
        for profile in ('legacy', 'approved_loudness', 'bounded_gain'):
            with self.subTest(profile=profile), override_settings(
                    VOICE_AUDIO_BACKEND='alsa', VOICE_PLAYBACK_PROFILE=profile,
                    VOICE_ALSA_CAPTURE_DEVICE='hw:test-capture', VOICE_ALSA_PLAYBACK_DEVICE='hw:test-playback',
                    VOICE_PLAYBACK_GAIN='1.25', VOICE_CAPTURE_READY_LIFECYCLE=True,
                    VOICE_CAPTURE_VAD_ENABLED=True):
                config = VoiceLoopConfig.from_settings()
                self.assertEqual(config.playback_profile, profile)
                self.assertEqual(config.alsa_capture_device, 'hw:test-capture')
                self.assertEqual(config.alsa_playback_device, 'hw:test-playback')
                self.assertEqual(config.playback_gain, 1.25)
                self.assertTrue(config.capture_ready_lifecycle)

    @override_settings(**VOICE_LOOP_TEST_SETTINGS)
    def test_nonlegacy_profile_with_pulse_is_still_rejected(self):
        for profile in ('approved_loudness', 'bounded_gain'):
            with self.subTest(profile=profile), override_settings(VOICE_PLAYBACK_PROFILE=profile):
                with self.assertRaisesRegex(VoiceLoopError, 'existing ALSA path'):
                    VoiceLoopConfig.from_settings()

    def test_fresh_process_separates_regression_settings_from_runtime_environment(self):
        code = '''
import json,os,django
django.setup()
from django.conf import settings
from scripts.voice_loop import VoiceLoopConfig
c=VoiceLoopConfig.from_settings()
print(json.dumps({'backend':c.audio_backend,'profile':c.playback_profile,
                  'environment_backend':os.environ['VOICE_AUDIO_BACKEND'],
                  'database_memory':str(settings.DATABASES['default']['NAME'])==':memory:'}))
'''
        for module in ('config.test_settings', 'config.settings'):
            environment = {**os.environ, **PRODUCTION_AUDIO_ENV, 'DJANGO_SETTINGS_MODULE':module}
            result = subprocess.run([sys.executable, '-c', code], env=environment, text=True,
                capture_output=True, timeout=30, cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(result.returncode, 0, 'Fresh-process configuration regression failed')
            data = json.loads(result.stdout)
            self.assertEqual(data['environment_backend'], 'alsa')  # No environment mutation.
            if module == 'config.test_settings':
                self.assertEqual((data['backend'], data['profile']), ('pulse', 'legacy'))
                self.assertTrue(data['database_memory'])
            else:
                self.assertEqual((data['backend'], data['profile']), ('alsa', 'approved_loudness'))


class DeploymentIdempotencyTests(SimpleTestCase):
    def test_supervisor_detects_ancestor_and_equivalent_cherry_pick_without_services(self):
        root = Path(__file__).resolve().parents[1]
        script = (root/'scripts/deploy_benchmark_timeouts.sh').read_text()
        # Execute only the actual read-only detection function. Never execute
        # the supervisor, sudo, runuser or systemctl during this regression.
        function = re.search(r'^patch_already_applied\(\) \{\n.*?^\}', script, re.M|re.S)
        self.assertIsNotNone(function)
        bash = str(Path('C:/Program Files/Git/usr/bin/bash.exe')) if os.name == 'nt' else shutil.which('bash')
        self.assertTrue(bash, 'Bash is required to validate the deployment helper')
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                result = subprocess.run(['git','-c','user.name=Regression Test',
                    '-c','user.email=regression@example.invalid','-c','commit.gpgsign=false',
                    '-c',f'core.hooksPath={directory}/no-hooks',*args], cwd=directory,
                    text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, 'Temporary Git fixture command failed')
                return result.stdout.strip()
            git('init','-q','--object-format=sha1')
            (Path(directory)/'base.txt').write_text('base\n')
            git('add','base.txt'); git('commit','-qm','Base fixture')
            baseline = git('rev-parse','HEAD')
            original_branch = git('branch','--show-current')
            (Path(directory)/'fix.txt').write_text('isolated fix\n')
            git('add','fix.txt'); git('commit','-qm','Focused fixture fix')
            reviewed = git('rev-parse','HEAD')
            def probe():
                shell = 'as_user() { "$@"; }\n'+function[0]+'\nif patch_already_applied; then echo applied; else echo missing; fi\n'
                result = subprocess.run([bash,'--noprofile','--norc','-c',shell],cwd=directory,
                    env={**os.environ,'REVIEWED_COMMIT':reviewed},text=True,capture_output=True,timeout=30)
                self.assertEqual(result.returncode,0,'Read-only deployment detection failed')
                return result.stdout.strip()
            git('switch','-qc','deployed',baseline)
            self.assertEqual(probe(),'missing')
            (Path(directory)/'other.txt').write_text('unrelated deployment commit\n')
            git('add','other.txt'); git('commit','-qm','Other fixture work')
            git('cherry-pick',reviewed)
            self.assertNotEqual(git('rev-parse','HEAD'),reviewed)
            self.assertEqual(probe(),'applied')
            git('switch','-q',original_branch)
            self.assertEqual(probe(),'applied')
