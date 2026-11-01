"""Regression checks for source-export privacy and opt-in appliance settings."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

from scripts.export_pi_source import ExportError, export


class SourceExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'repo'
        self.root.mkdir()
        self.output = self.base / 'review.zip'
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.write('manage.py', 'print("baseline")\n')
        self.write('assistant/services.py', 'value = 1\n')
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Export test',
                        '-c', 'user.email=export-test@example.invalid', 'commit', '-qm', 'fixture'], check=True)

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding='utf-8')
        return path

    def test_exports_modified_and_untracked_source_without_private_data(self):
        self.write('assistant/services.py', 'value = 2\n')
        self.write('assistant/new_feature.py', 'enabled = True\n')
        self.write('.env', 'PRIVATE=do-not-export\n')
        self.write('firmware/secrets.h', '#define WIFI_PASSWORD "do-not-export"\n')
        self.write('media/personal.py', 'private conversation\n')
        self.write('assistant/debug.log', 'personal log\n')
        before = subprocess.check_output(['git', '-C', str(self.root), 'status', '--porcelain'])
        result = export(self.root, self.output)
        self.assertEqual(result['files'], 3)
        with ZipFile(self.output) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {'MANIFEST.json', 'source/manage.py',
                             'source/assistant/services.py', 'source/assistant/new_feature.py'})
            manifest = json.loads(archive.read('MANIFEST.json'))
            for name, record in manifest['files'].items():
                self.assertEqual(hashlib.sha256(archive.read('source/' + name)).hexdigest(), record['sha256'])
            self.assertNotIn('do-not-export', json.dumps(manifest))
            self.assertTrue(any(row['status'] == '??' for row in manifest['working_tree_status']))
        self.assertEqual(before, subprocess.check_output(['git', '-C', str(self.root), 'status', '--porcelain']))
        self.assertEqual((self.root / 'assistant/services.py').read_text(), 'value = 2\n')

    def test_refuses_overwrite_and_in_tree_output(self):
        self.output.write_bytes(b'preserve')
        with self.assertRaises(FileExistsError):
            export(self.root, self.output)
        self.assertEqual(self.output.read_bytes(), b'preserve')
        with self.assertRaises(ExportError):
            export(self.root, self.root / 'archive.zip')

    def test_refuses_literal_credentials_without_printing_value(self):
        value = 'not-a-real-test-credential'
        self.write('assistant/private_config.py', 'PASSWORD = "' + value + '"\n')
        # Private-named files are excluded; a production source literal is refused.
        self.write('assistant/provider.py', 'PASSWORD = "' + value + '"\n')
        with self.assertRaises(ExportError) as caught:
            export(self.root, self.output)
        self.assertNotIn(value, str(caught.exception))
        self.assertFalse(self.output.exists())

    def test_refuses_symlinks(self):
        target = self.base / 'outside.py'
        target.write_text('outside = True\n')
        try:
            (self.root / 'assistant/link.py').symlink_to(target)
        except OSError:
            self.skipTest('Creating symlinks requires unavailable OS permission')
        with self.assertRaises(ExportError):
            export(self.root, self.output)


class ApplianceProfileTests(unittest.TestCase):
    def setUp(self):
        self.secret = secrets.token_urlsafe(50)

    def settings(self, **extra):
        env = dict(os.environ)
        for name in ('SECRET_KEY', 'DEBUG', 'ALLOWED_HOSTS', 'COMPANION_HTTPS', 'COMPANION_HSTS_SECONDS'):
            env.pop(name, None)
        env['PYTHON_DOTENV_DISABLED'] = '1'
        env.update(extra)
        code = ('import json; import config.appliance_settings as s; '
                'print(json.dumps({k:getattr(s,k) for k in '
                '("DEBUG","SECURE_SSL_REDIRECT","SESSION_COOKIE_SECURE",'
                '"CSRF_COOKIE_SECURE","SECURE_HSTS_SECONDS")}))')
        return subprocess.run([sys.executable, '-c', code], env=env,
                              capture_output=True, text=True, timeout=30,
                              cwd=Path(__file__).resolve().parents[1])

    def test_refuses_missing_secret_and_wildcard_host(self):
        self.assertNotEqual(self.settings().returncode, 0)
        self.assertNotEqual(self.settings(SECRET_KEY=self.secret,
                                         ALLOWED_HOSTS='*').returncode, 0)

    def test_lan_keeps_http_and_disables_debug(self):
        result = self.settings(SECRET_KEY=self.secret, DEBUG='True')
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        self.assertEqual(values, {'DEBUG': False, 'SECURE_SSL_REDIRECT': False,
                                 'SESSION_COOKIE_SECURE': False, 'CSRF_COOKIE_SECURE': False,
                                 'SECURE_HSTS_SECONDS': 0})

    def test_https_requires_explicit_opt_in(self):
        result = self.settings(SECRET_KEY=self.secret,
                               COMPANION_HTTPS='true', COMPANION_HSTS_SECONDS='0')
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        self.assertTrue(values['SECURE_SSL_REDIRECT'])
        self.assertTrue(values['SESSION_COOKIE_SECURE'])
        self.assertTrue(values['CSRF_COOKIE_SECURE'])
        self.assertEqual(values['SECURE_HSTS_SECONDS'], 0)
