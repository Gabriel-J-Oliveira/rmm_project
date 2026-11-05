"""Explicit disposable loopback PostgreSQL gate; never inherit DATABASE_URL."""
import os
import environ
from urllib.parse import urlsplit
from django.core.exceptions import ImproperlyConfigured
from .settings_test import *  # noqa: F401,F403

raw=os.environ.get('NIGHTOWL_TEST_POSTGRES_URL','')
try:
    parsed=urlsplit(raw)
    permitted=(parsed.scheme in ('postgres','postgresql') and parsed.hostname in ('127.0.0.1','localhost')
               and parsed.path=='/nightowl_rc46_isolated' and not parsed.query and not parsed.fragment)
except ValueError:
    permitted=False
if not permitted:
    raise ImproperlyConfigured('Explicit disposable loopback PostgreSQL URL required; no production fallback.')
DATABASES={'default':environ.Env.db_url_config(raw)}
DATABASES['default']['TEST']={'NAME':f'test_nightowl_rc46_isolated_{os.getpid()}'}
