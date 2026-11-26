import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def load_runtime_environment(base_dir, read_env):
    """Load protected configuration independently of the deployed checkout."""
    configured = os.environ.get('NIGHTOWL_RUNTIME_ENV_FILE')
    if configured is None:
        legacy = base_dir / '.env'
        if legacy.is_file():
            read_env(legacy)
        return
    path = Path(configured)
    if not path.is_absolute() or not path.is_file():
        raise ImproperlyConfigured('NIGHTOWL_RUNTIME_ENV_FILE must name a readable absolute file.')
    try:
        with path.open(encoding='utf-8') as stream:
            read_env(stream, overwrite=False)
    except (OSError, UnicodeError) as exc:
        raise ImproperlyConfigured('Cannot read NIGHTOWL_RUNTIME_ENV_FILE.') from exc
