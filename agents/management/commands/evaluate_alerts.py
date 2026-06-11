from django.core.management.base import BaseCommand
from django.db.models import Count

from agents.alerting import evaluate_all_alerts
from agents.models import EndpointAlert


class Command(BaseCommand):
    help = 'Evaluate operational alerts for active endpoints.'

    def add_arguments(self, parser):
        parser.add_argument('--offline-warning-hours', type=int, default=1)
        parser.add_argument('--offline-critical-hours', type=int, default=24)
        parser.add_argument('--disk-warning-free-percent', type=int, default=15)
        parser.add_argument('--disk-critical-free-percent', type=int, default=10)
        parser.add_argument('--uptime-warning-days', type=int, default=15)
        parser.add_argument('--uptime-critical-days', type=int, default=30)
        parser.add_argument('--stale-inventory-hours', type=int, default=24)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options.pop('dry_run')
        result = evaluate_all_alerts(options, dry_run=dry_run)

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run: no changes saved.'))
            for action in result.dry_run_actions:
                self.stdout.write(action)

        self.stdout.write(self.style.SUCCESS('Alert evaluation complete.'))
        self.stdout.write(f'endpoints evaluated: {result.endpoints_evaluated}')
        self.stdout.write(f'alerts created: {result.created}')
        self.stdout.write(f'alerts updated: {result.updated}')
        self.stdout.write(f'alerts resolved: {result.resolved}')

        open_counts = {
            item['severity']: item['count']
            for item in EndpointAlert.objects.filter(status=EndpointAlert.STATUS_OPEN)
            .values('severity')
            .order_by()
            .annotate(count=Count('id'))
        }
        self.stdout.write('open alerts by severity:')
        for severity, _label in EndpointAlert.SEVERITY_CHOICES:
            self.stdout.write(f'  {severity}: {open_counts.get(severity, 0)}')
