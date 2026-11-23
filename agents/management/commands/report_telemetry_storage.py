import json
import uuid

from django.core.management.base import BaseCommand, CommandError

from agents.telemetry_storage_report import build_telemetry_storage_report


class Command(BaseCommand):
    help = 'Read-only bounded telemetry storage and quality report for retention planning.'

    def add_arguments(self, parser):
        scope = parser.add_mutually_exclusive_group(required=True)
        scope.add_argument('--all-endpoints', action='store_true')
        scope.add_argument('--endpoint', type=uuid.UUID)
        parser.add_argument('--window-days', type=int, default=90)
        parser.add_argument('--sample-limit', type=int, default=200)

    def handle(self, *args, **options):
        try:
            report = build_telemetry_storage_report(
                all_endpoints=options['all_endpoints'],
                endpoint_id=options['endpoint'],
                window_days=options['window_days'],
                sample_limit=options['sample_limit'],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
