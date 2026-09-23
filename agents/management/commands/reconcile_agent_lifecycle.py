import json
import uuid

from django.core.management.base import BaseCommand, CommandError

from agents.lifecycle_reconciliation import (
    apply_legacy_lifecycle_reconciliation,
    evaluate_legacy_lifecycle_reconciliation,
)
from agents.models import AgentMachine


class Command(BaseCommand):
    help = 'Evaluate or explicitly reconcile one legacy agent lifecycle; dry-run by default.'

    def add_arguments(self, parser):
        parser.add_argument('--endpoint', required=True)
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--reason')

    def handle(self, *args, **options):
        try:
            endpoint_id = uuid.UUID(options['endpoint'])
        except (ValueError, TypeError):
            raise CommandError('invalid_endpoint_id') from None
        if options['apply'] and not (options['reason'] or '').strip():
            raise CommandError('reason_required')
        try:
            if options['apply']:
                result = apply_legacy_lifecycle_reconciliation(endpoint_id, reason=options['reason'])
            else:
                result = evaluate_legacy_lifecycle_reconciliation(AgentMachine.objects.get(pk=endpoint_id))
        except AgentMachine.DoesNotExist:
            raise CommandError('endpoint_not_found') from None
        self.stdout.write(json.dumps({'mode': 'apply' if options['apply'] else 'dry_run', **result}, sort_keys=True))
