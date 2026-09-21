import json
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from agents.models import AgentRolloutCampaign
from agents.rollout_planning import build_rollout_dispatch_plan
from agents.rollout_dispatch import dispatch_rollout_campaign
from agents.fleet_policy import PolicyContractError
from agents.rollout_runner import run_rollout_round


class Command(BaseCommand):
    help = 'Read-only planning or explicit, feature-gated campaign dispatch.'

    def add_arguments(self, parser):
        parser.add_argument('--plan-only', action='store_true')
        parser.add_argument('--campaign')
        parser.add_argument('--execute', action='store_true')
        parser.add_argument('--run-once', action='store_true')

    def handle(self, *args, **options):
        modes = sum(bool(options[key]) for key in ('plan_only', 'execute', 'run_once'))
        if modes > 1:
            raise CommandError('ROLLOUT_MODE_CONFLICT')
        if not modes:
            raise CommandError('ROLLOUT_DISPATCH_NOT_IMPLEMENTED')
        if options['execute'] and not options['campaign']:
            raise CommandError('EXPLICIT_CAMPAIGN_REQUIRED')
        if options['run_once']:
            if options['campaign']:
                raise CommandError('RUN_ONCE_CAMPAIGN_NOT_ALLOWED')
            try:
                self.stdout.write(json.dumps(run_rollout_round(), sort_keys=True))
            except PolicyContractError as exc:
                raise CommandError(str(exc)) from None
            return
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
            try:
                result = dispatch_rollout_campaign(campaign, now=now) if options['execute'] else build_rollout_dispatch_plan(campaign, now=now)
            except PolicyContractError as exc:
                raise CommandError(str(exc)) from None
            self.stdout.write(json.dumps(result, sort_keys=True))
