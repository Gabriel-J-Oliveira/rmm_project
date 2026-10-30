from django.core.management.base import BaseCommand
from agents.telemetry_continuous import compact_history


class Command(BaseCommand):
    help = 'Idempotent v2 retention/rollup. Read-only dry-run unless --apply is explicit.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--limit', type=int, default=500)

    def handle(self, *args, **options):
        limit = options['limit']
        if not 1 <= limit <= 5000:
            raise ValueError('limit must be between 1 and 5000')
        self.stdout.write(str({'apply': options['apply'], **compact_history(apply=options['apply'], limit=limit)}))
