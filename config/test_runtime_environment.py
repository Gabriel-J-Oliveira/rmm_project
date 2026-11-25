import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import environ
from django.core.exceptions import ImproperlyConfigured

from .runtime_environment import load_runtime_environment


class RuntimeEnvironmentTests(TestCase):
    def test_upgrade_downgrade_reinstall_and_restart_share_one_policy(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / 'approved.env'
            policy.write_text('NIGHTOWL_TERMINAL_ENABLED=True\nNIGHTOWL_TERMINAL_ADMIN_AUTH_ENABLED=False\n'
                              'NIGHTOWL_SERVICE_COLLECTION_ALLOWED_ENDPOINT_IDS=pilot\n', encoding='utf-8')
            for release in ('old', 'upgrade', 'old', 'reinstall', 'reinstall'):
                checkout = root / release
                checkout.mkdir(exist_ok=True)
                (checkout / '.env').write_text('NIGHTOWL_TERMINAL_ADMIN_AUTH_ENABLED=True\n', encoding='utf-8')
                # Each load starts with a clean process environment.
                with self.subTest(release=release), patch.dict(os.environ, {'NIGHTOWL_RUNTIME_ENV_FILE': str(policy)}, clear=True):
                    load_runtime_environment(checkout, environ.Env.read_env)
                    env = environ.Env()
                    self.assertTrue(env.bool('NIGHTOWL_TERMINAL_ENABLED'))
                    self.assertFalse(env.bool('NIGHTOWL_TERMINAL_ADMIN_AUTH_ENABLED'))
                    self.assertEqual(env.list('NIGHTOWL_SERVICE_COLLECTION_ALLOWED_ENDPOINT_IDS'), ['pilot'])

    def test_missing_external_file_never_falls_back_or_overrides_environment(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / '.env'
            path.write_text('NIGHTOWL_TERMINAL_ENABLED=True\n', encoding='utf-8')
            with patch.dict(os.environ, {'NIGHTOWL_RUNTIME_ENV_FILE': str(root / 'missing')}, clear=True):
                with self.assertRaises(ImproperlyConfigured):
                    load_runtime_environment(root, environ.Env.read_env)
            with patch.dict(os.environ, {'NIGHTOWL_RUNTIME_ENV_FILE': str(path), 'NIGHTOWL_TERMINAL_ENABLED': 'False'}, clear=True):
                load_runtime_environment(root, environ.Env.read_env)
                self.assertFalse(environ.Env().bool('NIGHTOWL_TERMINAL_ENABLED'))
