import json
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from agents.models import AgentRolloutCampaign
from agents.rollout_planning import build_rollout_dispatch_plan


class Command(BaseCommand):
    help = 'Read-only rollout planning. Dispatch is not implemented.'

    def add_arguments(self, parser):
        parser.add_argument('--plan-only', action='store_true')
        parser.add_argument('--campaign')

    def handle(self, *args, **options):
        if not options['plan_only']:
            raise CommandError('ROLLOUT_DISPATCH_NOT_IMPLEMENTED')
        campaigns = AgentRolloutCampaign.objects.order_by('id')
        if options['campaign']:
            try:
                pk = uuid.UUID(options['campaign'])
            except ValueError:
                raise CommandError('INVALID_CAMPAIGN_ID') from None
            campaigns = campaigns.filter(pk=pk)
            if not campaigns.exists():
                raise CommandError('CAMPAIGN_NOT_FOUND')
        now = timezone.now()
        for campaign in campaigns.iterator():
            self.stdout.write(json.dumps(build_rollout_dispatch_plan(campaign, now=now), sort_keys=True))
