"""Child entrypoint: initialize Django before importing the install service."""

import json
import os
import sys


def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from dashboard.remote_install import run_remote_install

    if len(sys.argv) != 2:
        return 2
    try:
        secret = json.loads(sys.stdin.buffer.read(4096))
        run_remote_install(sys.argv[1], secret['username'], secret['password'])
    except Exception:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
