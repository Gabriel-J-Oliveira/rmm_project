from django.core.management.base import BaseCommand
from django.db.models import Count

from agents.models import SoftwarePolicyViolation
from agents.software_policy_engine import evaluate_software_policies


class Command(BaseCommand):
    help = 'Evaluate SoftwarePolicy rules against the latest endpoint inventory snapshots.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--policy', help='Policy UUID or partial policy name.')
        parser.add_argument('--endpoint', help='Endpoint UUID or partial hostname.')
        parser.add_argument('--verbose', action='store_true')
        parser.add_argument('--resolve-missing', dest='resolve_missing', action='store_true', default=True)
        parser.add_argument('--no-resolve-missing', dest='resolve_missing', action='store_false')

    def handle(self, *args, **options):
        dry_run = options.pop('dry_run')
        result = evaluate_software_policies(options, dry_run=dry_run)

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run: no changes saved.'))
            for action in result.dry_run_actions:
                self.stdout.write(action)

        self.stdout.write(self.style.SUCCESS('Software policy evaluation complete.'))
        self.stdout.write(f'policies evaluated: {result.policies_evaluated}')
        self.stdout.write(f'endpoints evaluated: {result.endpoints_evaluated}')
        self.stdout.write(f'violations created: {result.violations_created}')
        self.stdout.write(f'violations updated: {result.violations_updated}')
        self.stdout.write(f'violations resolved: {result.violations_resolved}')
        self.stdout.write(f'alerts created: {result.alerts_created}')
        self.stdout.write(f'alerts resolved: {result.alerts_resolved}')
        self.stdout.write(f'exceptions applied: {result.exceptions_applied}')
        self.stdout.write(f'errors: {result.errors}')

        open_counts = {
            item['severity']: item['count']
            for item in SoftwarePolicyViolation.objects.filter(status=SoftwarePolicyViolation.STATUS_OPEN)
            .values('severity')
            .order_by()
            .annotate(count=Count('id'))
        }
        self.stdout.write('open software policy violations by severity:')
        for severity, _label in SoftwarePolicyViolation._meta.get_field('severity').choices:
            self.stdout.write(f'  {severity}: {open_counts.get(severity, 0)}')
