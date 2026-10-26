"""Serve synthetic acceptance fixtures; never opens the production database or knowledge."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.test_settings'
from django.conf import settings
with tempfile.TemporaryDirectory(prefix='companion-chat-preview-') as directory:
    settings.DATABASES['default']['NAME'] = str(Path(directory) / 'preview.sqlite3')
    settings.MEDIA_ROOT = str(Path(directory) / 'media')
    settings.RAG_ENABLED = True
    settings.DEBUG = True
    settings.ALLOWED_HOSTS = ['127.0.0.1', 'localhost']
    settings.DEMO_AP_ENABLED = False
    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    from assistant.ai_engine import AIEngineResult, AIEngineStreamEvent
    call_command('migrate', interactive=False, verbosity=0)
    get_user_model().objects.create_user('ui-acceptance', password='local-test-only', is_staff=True)
    from knowledge_base.tests_documents import pdf_bytes
    Path(directory, 'aurora.pdf').write_bytes(pdf_bytes('Aurora battery capacity is 42 Wh.'))
    class FixtureEngine:
        def generate(self, query, **kwargs):
            instruction = kwargs.get('system_instruction', '')
            text = 'The aurora battery capacity is 42 Wh.' if '42 Wh' in instruction else 'Hello! How can I help?'
            return AIEngineResult(text=text, engine='mock', model='mock', latency_ms=1)
        def generate_stream(self, query, **kwargs):
            result = self.generate(query, **kwargs)
            yield AIEngineStreamEvent(type='delta', text=result.text)
            yield AIEngineStreamEvent(type='done', result=result)
    print('FIXTURE_DIRECTORY=' + directory, flush=True)
    with patch('assistant.services.get_engine', return_value=FixtureEngine()):
        call_command('runserver', '127.0.0.1:8017', use_reloader=False)
