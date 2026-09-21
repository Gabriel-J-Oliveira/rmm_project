import json
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from agents.models import AgentRolloutCampaign
from agents.rollout_reconcile import reconcile_rollout_campaign


class Command(BaseCommand):
    help = 'Explicitly reconcile persisted rollout job evidence; never dispatches jobs.'

    def add_arguments(self, parser):
        parser.add_argument('--run-once', action='store_true')
        parser.add_argument('--campaign')

    def handle(self, *args, **options):
        if bool(options['run_once']) == bool(options['campaign']):
            raise CommandError('RECONCILE_MODE_REQUIRED')
        campaigns = AgentRolloutCampaign.objects.filter(targets__agent_job__isnull=False).filter(
            Q(state__in=['running', 'paused', 'aborted']) | Q(targets__state__in=['queued', 'running'])
        ).distinct().order_by('pk')
        if options['campaign']:
            try:
                campaign_id = uuid.UUID(options['campaign'])
            except ValueError:
                raise CommandError('INVALID_CAMPAIGN_ID') from None
            campaigns = AgentRolloutCampaign.objects.filter(pk=campaign_id)
            if not campaigns.exists():
                raise CommandError('CAMPAIGN_NOT_FOUND')
        results = [reconcile_rollout_campaign(campaign) for campaign in campaigns]
        self.stdout.write(json.dumps({'campaigns': results}, sort_keys=True, default=str))
