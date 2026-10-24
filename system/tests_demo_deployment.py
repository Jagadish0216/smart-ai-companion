from pathlib import Path
from django.test import SimpleTestCase

ROOT=Path(__file__).resolve().parents[1]


class DemoDeploymentTests(SimpleTestCase):
    def test_voice_and_stt_units_are_bounded_nonroot_and_have_single_input_mode(self):
        voice=(ROOT/'deployment/systemd/smart-ai-companion-voice.service').read_text()
        stt=(ROOT/'deployment/systemd/smart-ai-companion-stt.service').read_text()
        for unit in (voice,stt):
            self.assertIn('User=jagadish',unit)
            self.assertIn('StartLimitBurst=2',unit)
            self.assertIn('NoNewPrivileges=true',unit)
            self.assertIn('StandardOutput=null',unit)
        self.assertIn('--push-to-talk',voice)
        self.assertNotIn('--hands-free',voice)
        self.assertNotIn('--voice-barge-in',voice)
        self.assertIn('Requires=smart-ai-companion-stt.service',voice)
        self.assertIn('KillSignal=SIGINT',voice)

    def test_prepare_and_disruptive_activation_are_separate_and_no_models_or_profiles_deleted(self):
        script=(ROOT/'scripts/final_review_demo.sh').read_text()
        preparation=script.split('prepare)')[1].split('activate)')[0]
        self.assertNotIn('connection up',preparation)
        self.assertNotIn('restart smart-ai-companion-network-mode',preparation)
        self.assertIn('Type DEMO-AP',script)
        self.assertIn('git diff --quiet',script)
        for forbidden in ('git reset','git push','ollama pull','connection delete','rm -rf'):
            self.assertNotIn(forbidden,script)
        self.assertIn('source.backup(destination)',script)

    def test_demo_hosts_and_model_configuration_have_no_wildcards_or_model_changes(self):
        config=(ROOT/'deployment/demo.env.example').read_text()
        self.assertIn('DEBUG=False',config)
        self.assertIn('ONLINE_RETRIEVAL_ENABLED=false',config)
        self.assertIn('STT_ENGINE=whisper_server',config)
        self.assertNotIn('*',config)
        self.assertNotIn('PASSWORD=',config)
        self.assertNotIn('OLLAMA_MODEL=',config)
