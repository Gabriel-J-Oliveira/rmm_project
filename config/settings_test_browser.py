"""Isolated file SQLite: live HTTP threads must not share one in-memory connection."""
import os
import tempfile
from pathlib import Path
from .settings_test import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE':'django.db.backends.sqlite3',
        'NAME':':memory:',
        'TEST':{'NAME':str(Path(tempfile.gettempdir()) / f'nightowl-browser-isolated-{os.getpid()}.sqlite3')},
        'OPTIONS':{'timeout':20},
    },
}
