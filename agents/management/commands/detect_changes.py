from django.core.management.base import BaseCommand

from agents.change_detection import detect_all_changes


class Command(BaseCommand):
    help = 'Detect changes between the last two inventory snapshots per endpoint.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--endpoint', help='Endpoint hostname or UUID.', default='')
        parser.add_argument('--temporary-alert-hours', type=int, default=72)

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        hours = options['temporary_alert_hours']
        endpoint = options['endpoint'] or None
        result = detect_all_changes(hours=hours, endpoint_filter=endpoint, dry_run=dry_run)

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run: no changes saved.'))
            for action in result.dry_run_actions:
                self.stdout.write(action)

        self.stdout.write(self.style.SUCCESS('Change detection complete.'))
        self.stdout.write(f'endpoints evaluated: {result.endpoints_evaluated}')
        self.stdout.write(f'endpoints without enough snapshots: {result.insufficient_snapshots}')
        self.stdout.write(f'events created: {result.events_created}')
        self.stdout.write(f'events ignored by dedupe: {result.events_deduped}')
        self.stdout.write(f'temporary alerts created: {result.temporary_alerts_created}')
        self.stdout.write(f'errors: {result.errors}')
