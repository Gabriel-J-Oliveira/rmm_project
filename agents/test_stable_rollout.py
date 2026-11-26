import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings

from .models import AgentJob, AgentMachine, AgentRelease, AgentReleaseGroup, AgentRolloutCampaign
from .rollout_control import control_rollout_campaign
from .rollout_dispatch import dispatch_rollout_campaign
from .services import build_agent_rollout_preview, evaluate_agent_update_eligibility, publish_agent_release
from . import test_rollout_planning as fixtures
from .views import _update_agent_dispatch_block_reason


@override_settings(NIGHTOWL_STABLE_DESKTOP_ROLLOUT_ENABLED=True, NIGHTOWL_STABLE_DESKTOP_ROLLOUT_BATCH_SIZE=1,
                   NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=True, NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=True)
class StableRolloutTests(TestCase):
    def setUp(self):
        fixtures.DispatchPlanningTests.setUp(self)
        self.group = AgentReleaseGroup.objects.create(name='Windows desktops', slug='stable-desktops')
        AgentRelease.objects.filter(pk=self.release.pk).update(channel='stable', source_channel='stable')
        self.release.refresh_from_db()
        self.machine.update_channel = 'stable'
        self.machine.os_name = 'Microsoft Windows 11 Pro'
        self.machine.manufacturer = 'Dell'
        self.machine.model = 'OptiPlex'
        self.machine.save()
        self.machine.rollout_groups.add(self.group)

    def clone(self, **overrides):
        data = {name: getattr(self.machine, name) for name in ('update_channel', 'os_name', 'manufacturer',
            'model', 'agent_version', 'updater_version', 'update_policy', 'auto_update_enabled',
            'agent_lifecycle_status', 'status', 'last_seen_at')}
        endpoint = AgentMachine.objects.create(hostname='SYNTHETIC', machine_id=str(uuid.uuid4()),
            agent_token_hash=uuid.uuid4().hex, **{**data, **overrides})
        endpoint.rollout_groups.add(self.group)
        return endpoint

    def test_publication_covers_offline_without_consuming_batch_and_respects_controls(self):
        offline = self.clone(status='offline')
        vm = self.clone(model='Virtual Machine')
        server = self.clone(os_name='Microsoft Windows Server 2025')
        AgentRelease.objects.filter(pk=self.release.pk).update(status='draft')
        with patch('django.utils.timezone.now', return_value=self.now):
            publish_agent_release(self.release, self.actor, 'Approved test Stable',
                                  rollout_percentage=100, rollout_paused=False)
        campaign = AgentRolloutCampaign.objects.get(release=self.release)
        self.assertEqual((campaign.state, campaign.eligible_count, campaign.concurrency_limit), ('running', 2, 1))
        self.assertEqual(campaign.targets.get(endpoint=vm).reason_code, 'stable_protected_device')
        self.assertEqual(campaign.targets.get(endpoint=server).reason_code, 'stable_not_windows_desktop')
        self.assertEqual(campaign.targets.get(endpoint=offline).state, 'eligible')
        self.assertEqual(dispatch_rollout_campaign(campaign, now=self.now)['created_count'], 1)
        self.assertEqual(AgentJob.objects.get().endpoint_id, self.machine.pk)
        job = AgentJob.objects.get()
        target = campaign.targets.get(endpoint=self.machine)
        wave = campaign.waves.get()
        self.release.refresh_from_db()

        def pull_gate():
            return _update_agent_dispatch_block_reason(job, self.machine, self.release,
                {campaign.pk: campaign}, {wave.pk: wave}, {target.pk: target}, self.now)

        self.assertEqual(pull_gate(), '')
        self.machine.model = 'Virtual Machine'
        self.assertEqual(pull_gate(), 'stable_protected_device')
        self.machine.model = 'OptiPlex'
        control_rollout_campaign(campaign, {'action': 'pause', 'reason': 'Test', 'expected_state': 'running'}, self.actor, now=self.now)
        campaign.refresh_from_db()
        self.assertEqual(pull_gate(), 'stable_campaign_waiting')
        control_rollout_campaign(campaign, {'action': 'resume', 'reason': 'Test', 'expected_state': 'paused'}, self.actor, now=self.now)
        AgentJob.objects.update(status='completed')
        AgentMachine.objects.filter(pk=offline.pk).update(status='online', last_seen_at=self.now)
        self.assertEqual(dispatch_rollout_campaign(campaign, now=self.now)['created_count'], 1)
        self.assertEqual(campaign.targets.get(endpoint=offline).state, 'queued')
        control_rollout_campaign(campaign, {'action': 'abort', 'reason': 'Test', 'expected_state': 'running'}, self.actor, now=self.now)
        campaign.refresh_from_db()
        self.assertEqual(campaign.state, 'aborted')

    def test_explicit_classification_and_campaign_only(self):
        self.machine.rollout_groups.clear()
        preview = build_agent_rollout_preview(self.release, now=self.now)
        self.assertEqual(preview['targets'][0]['reason_code'], 'stable_desktop_unconfirmed')
        decision = evaluate_agent_update_eligibility(self.machine, explicit_release=self.release, now=self.now, for_agent=True)
        self.assertEqual(decision.reason_code, 'stable_campaign_required')
