from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from agents.asset_hardware import TERMINAL_LIFECYCLES, reconcile_stored_observation
from agents.models import AgentMachine, AssetHardwareObservation


class Command(BaseCommand):
    help = 'Preview stored SMBIOS observations or explicitly reconcile one confirmed endpoint.'

    def add_arguments(self, parser):
        from uuid import UUID
        parser.add_argument('--apply-endpoint', type=UUID, help='Reconcile exactly this endpoint UUID.')

    def handle(self, *args, **options):
        if options['apply_endpoint']:
            if not settings.NIGHTOWL_AUTOMATIC_ASSETS_ENABLED:
                raise CommandError('NIGHTOWL_AUTOMATIC_ASSETS_ENABLED is required for apply.')
            self.stdout.write(reconcile_stored_observation(options['apply_endpoint']))
            return
        endpoints = AgentMachine.objects.filter(is_active=True).exclude(agent_lifecycle_status__in=TERMINAL_LIFECYCLES)
        observations = AssetHardwareObservation.objects.filter(endpoint__in=endpoints)
        states = Counter({row['state']: row['total'] for row in observations.values('state').annotate(total=Count('pk'))})
        states['without_observation'] = endpoints.count() - sum(states.values())
        states['duplicate_uuid_groups'] = observations.exclude(smbios_uuid=None).values('smbios_uuid').annotate(
            total=Count('pk'),
        ).filter(total__gt=1).count()
        for state, count in sorted(states.items()):
            self.stdout.write(f'{state}: {count}')
