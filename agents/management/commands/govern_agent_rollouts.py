import json
import uuid

from django.core.management.base import BaseCommand, CommandError

from agents.fleet_policy import PolicyContractError
from agents.models import AgentRolloutCampaign
from agents.rollout_governance import evaluate_rollout_governance, run_governance_round


class Command(BaseCommand):
    help = 'Read-only governance planning or one explicit feature-gated governance round.'

    def add_arguments(self, parser):
        parser.add_argument('--plan-only', action='store_true')
        parser.add_argument('--campaign')
        parser.add_argument('--run-once', action='store_true')

    def handle(self, *args, **options):
        if options['plan_only']:
            if options['run_once'] or not options['campaign']:
                raise CommandError('GOVERNANCE_PLAN_REQUIRES_CAMPAIGN')
            try:
                campaign_id = uuid.UUID(options['campaign'])
            except ValueError:
                raise CommandError('INVALID_CAMPAIGN_ID') from None
            campaign = AgentRolloutCampaign.objects.filter(pk=campaign_id).first()
            if campaign is None:
                raise CommandError('CAMPAIGN_NOT_FOUND')
            self.stdout.write(json.dumps(evaluate_rollout_governance(campaign), sort_keys=True, default=str))
            return
        if not options['run_once'] or options['campaign']:
            raise CommandError('GOVERNANCE_MODE_REQUIRED')
        try:
            self.stdout.write(json.dumps(run_governance_round(), sort_keys=True, default=str))
        except PolicyContractError as exc:
            raise CommandError(str(exc)) from None
