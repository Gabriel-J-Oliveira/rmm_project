"""Explicit recovery for a dead batch process; never resumes with old credentials."""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from dashboard.models import RemoteInstallBatch
from dashboard.remote_install_batch import AUTO_STALL_SECONDS, interrupt_batch, stale_seconds


class Command(BaseCommand):
    help = 'Report dead batch runners; --apply interrupts safely without installing anything.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        for pk in RemoteInstallBatch.objects.filter(active_slot='global').values_list('pk', flat=True):
            with transaction.atomic():
                batch = RemoteInstallBatch.objects.select_for_update().get(pk=pk)
                age = (timezone.now() - (batch.runner_heartbeat_at or batch.created_at)).total_seconds()
                if batch.active_slot != 'global' or age < AUTO_STALL_SECONDS:
                    continue
                # Never overlap a healthy child just because its parent died.
                jobs = [item.remote_install_job for item in batch.items.select_related('remote_install_job')
                        if item.remote_install_job_id and item.remote_install_job.status in ('QUEUED', 'RUNNING')]
                if any(stale_seconds(job) < AUTO_STALL_SECONDS for job in jobs):
                    self.stdout.write(f'{pk}: CHILD_STILL_ALIVE')
                    continue
                self.stdout.write(f'{pk}: INTERRUPT' if options['apply'] else f'{pk}: WOULD_INTERRUPT')
                if options['apply']:
                    interrupt_batch(pk)
