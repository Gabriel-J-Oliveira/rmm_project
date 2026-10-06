"""Serialize batch and legacy admission in the same database transaction."""

from contextlib import contextmanager
from threading import RLock

from django.db import connection, transaction

_SQLITE_LOCK = RLock()


@contextmanager
def install_admission():
    # PostgreSQL advisory lock also covers the empty-table admission race.
    # SQLite serializes competing writes; the local lock avoids thread contention.
    with _SQLITE_LOCK if connection.vendor == 'sqlite' else transaction.atomic():
        with transaction.atomic():
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_xact_lock(%s)', [71920431])
            elif connection.vendor != 'sqlite':
                raise RuntimeError('Unsupported install admission database')
            yield
