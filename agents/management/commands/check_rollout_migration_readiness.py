from django.core.management.base import BaseCommand, CommandError
from agents.models import AgentRolloutTarget


class Command(BaseCommand):
    help = 'Read-only preflight for migration 0032; compatible with schema 0031.'

    def handle(self, *args, **options):
        count = AgentRolloutTarget.objects.filter(state='eligible', wave__isnull=True).count()
        self.stdout.write(f'eligible_without_wave={count}')
        if count:
            raise CommandError('ROLLOUT_WAVE_ASSIGNMENT_REQUIRED')
