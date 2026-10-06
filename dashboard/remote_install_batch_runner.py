"""Batch child entrypoint: UUID argv, credentials only through stdin."""
import json
import os
import sys


def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from dashboard.remote_install_batch import run_batch
    if len(sys.argv) != 2:
        return 2
    try:
        secret = json.loads(sys.stdin.buffer.read(4096))
        run_batch(sys.argv[1], secret['username'], secret['password'])
    except Exception:
        return 1
    finally:
        secret = None
    return 0


if __name__ == '__main__':
    sys.exit(main())
