"""Isolated configuration checks: never connect to the working database or SMTP."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import TestCase


class ProductionSettingsTests(TestCase):
    def load(self, **changes):
        with tempfile.TemporaryDirectory() as folder:
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(('DJANGO_', 'SALES_', 'POSTGRES_', 'EMAIL_', 'SERVERCORE_'))}
            env.update(DJANGO_SECRET_KEY='private-test-only-0123456789-abcdefghijklmnopqrstuvwxyz',
                       SALES_DATA_DIR=folder, SALES_ALLOWED_HOSTS='sales.overclockers.kz,127.0.0.1',
                       SALES_PUBLIC_BASE_URL='https://sales.overclockers.kz')
            env.update(changes)
            code = '''
import json
from mailer import production as p
from django.core.mail import get_connection
from django.conf import settings
settings.configure(EMAIL_BACKEND=p.EMAIL_BACKEND)
backend=get_connection()
print(json.dumps(dict(smtp=p.SALES_SMTP_ENABLED, backend=p.EMAIL_BACKEND,
    opened=backend.open(), data=str(p.DATA_DIR), db=str(p.DATABASES['default']['NAME']),
    secure=p.SESSION_COOKIE_SECURE and p.CSRF_COOKIE_SECURE,
    redirect=p.SECURE_SSL_REDIRECT, auth=p.SALES_AUTH_REQUIRED,
    csrf=p.CSRF_TRUSTED_ORIGINS, frame=p.X_FRAME_OPTIONS)))
'''
            return subprocess.run([sys.executable, '-c', code], env=env,
                                  capture_output=True, text=True, timeout=30)

    def test_no_send_and_durable_storage_even_with_smtp_environment(self):
        result = self.load(SALES_SMTP_ENABLED='1', SALES_MAIL_PROVIDER='servercore')
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertFalse(config['smtp'])
        self.assertEqual(config['backend'], 'django.core.mail.backends.dummy.EmailBackend')
        self.assertIsNone(config['opened'])
        self.assertEqual(Path(config['db']).parent, Path(config['data']))
        self.assertTrue(config['secure'] and config['redirect'] and config['auth'])
        self.assertEqual(config['csrf'], ['https://sales.overclockers.kz'])
        self.assertEqual(config['frame'], 'DENY')

    def test_refuses_missing_or_development_secret(self):
        for secret in ('', 'local-development-only-do-not-deploy', 'x' * 64):
            with self.subTest(secret_length=len(secret)):
                self.assertNotEqual(self.load(DJANGO_SECRET_KEY=secret).returncode, 0)

    def test_refuses_unsafe_hosts_and_origin(self):
        for values in ({'SALES_ALLOWED_HOSTS': '*'}, {'SALES_ALLOWED_HOSTS': '.overclockers.kz'},
                       {'SALES_PUBLIC_BASE_URL': 'http://sales.overclockers.kz'},
                       {'SALES_PUBLIC_BASE_URL': 'https://other.example'},
                       {'SALES_PUBLIC_BASE_URL': 'https://sales.overclockers.kz/path'},
                       {'SALES_PUBLIC_BASE_URL': 'https://user@sales.overclockers.kz'}):
            with self.subTest(values=values):
                self.assertNotEqual(self.load(**values).returncode, 0)

    def test_refuses_data_in_release_or_relative_path(self):
        root = Path(__file__).resolve().parent.parent
        for path in ('relative-data', str(root / 'data'), str(root.parent)):
            with self.subTest(path=path):
                self.assertNotEqual(self.load(SALES_DATA_DIR=path).returncode, 0)
