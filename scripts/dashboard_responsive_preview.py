"""Local-only responsive fixtures. Never migrate or seed the normal database."""
import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8004)
    parser.add_argument('--setup', action='store_true', help='Use an isolated setup-mode database.')
    args = parser.parse_args()
    from django.conf import settings
    original_database = Path(settings.DATABASES['default']['NAME']).resolve()
    with tempfile.TemporaryDirectory(prefix='companion-responsive-') as directory:
        database = Path(directory) / 'preview.sqlite3'
        if original_database.exists():
            source = sqlite3.connect(original_database.as_uri() + '?mode=ro', uri=True)
            target = sqlite3.connect(database)
            try:
                source.backup(target)
            finally:
                source.close()
                target.close()
        settings.DATABASES['default']['NAME'] = database
        settings.DEBUG = True
        settings.ALLOWED_HOSTS = ['127.0.0.1', 'localhost']
        settings.TEMPLATES[0]['APP_DIRS'] = False
        settings.TEMPLATES[0]['OPTIONS']['loaders'] = [
            'django.template.loaders.filesystem.Loader',
            'django.template.loaders.app_directories.Loader',
        ]
        import django
        django.setup()
        from django.contrib.auth import get_user_model
        from django.core.management import call_command
        from django.test import Client
        from system.models import NetworkProvisioningState, SystemLog, SystemSetting
        call_command('migrate', interactive=False, verbosity=0)
        NetworkProvisioningState.objects.update_or_create(
            id=1, defaults={'state': 'SETUP_AP' if args.setup else 'NORMAL_MODE'},
        )
        SystemSetting.objects.get_or_create(
            key='responsive_validation_long_configuration_key',
            defaults={'value': 'local-model-' + 'long-value-' * 12,
                      'description': 'Responsive acceptance fixture: ' + 'configuration description ' * 12},
        )
        SystemLog.objects.create(
            level='WARN', component='responsive_acceptance_component',
            message='Responsive acceptance fixture: ' + 'long diagnostic message ' * 30,
        )
        user, _ = get_user_model().objects.get_or_create(
            username='responsive_local_fixture', defaults={'is_staff': True},
        )
        client = Client()
        client.force_login(user)
        cookie_file = Path(directory) / 'staff-cookie.json'
        cookie_file.write_text(json.dumps({
            'name': settings.SESSION_COOKIE_NAME,
            'value': client.cookies[settings.SESSION_COOKIE_NAME].value,
        }), encoding='utf-8')
        print('Isolated database:', database, flush=True)
        print('Local staff cookie file:', cookie_file, flush=True)
        try:
            call_command('runserver', f'127.0.0.1:{args.port}', use_reloader=False)
        finally:
            from django.db import connections
            connections.close_all()


if __name__ == '__main__':
    main()
