import json
import uuid

from django.core.management.base import BaseCommand, CommandError

from agents.models import AgentJob
from agents.stale_job_reconciliation import (
    apply_stale_sent_job_timeout,
    evaluate_stale_sent_job_timeout,
)


class Command(BaseCommand):
    help = 'Dry-run or explicitly time out one sent job without result evidence.'

    def add_arguments(self, parser):
        parser.add_argument('--job', required=True)
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--reason')

    def handle(self, *args, **options):
        try:
            job_id = uuid.UUID(options['job'])
        except (ValueError, TypeError):
            raise CommandError('invalid_job_id') from None
        if options['apply'] and not (options['reason'] or '').strip():
            raise CommandError('reason_required')
        try:
            if options['apply']:
                result = apply_stale_sent_job_timeout(job_id, reason=options['reason'])
            else:
                result = evaluate_stale_sent_job_timeout(AgentJob.objects.get(pk=job_id))
        except AgentJob.DoesNotExist:
            raise CommandError('job_not_found') from None
        self.stdout.write(json.dumps({'mode': 'apply' if options['apply'] else 'dry_run', **result}, sort_keys=True))
