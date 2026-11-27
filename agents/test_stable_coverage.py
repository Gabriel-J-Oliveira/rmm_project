from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import skipUnless
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings

from . import test_stable_rollout as fixtures
from .fleet_policy import PolicyContractError
from .models import AgentJob, AgentRelease, AgentRolloutCampaign, AgentRolloutCoverageRound, AuditEvent
from .rollout_campaigns import transition_campaign, transition_wave
from .rollout_dispatch import dispatch_rollout_campaign
from .rollout_runner import run_rollout_round
from .services import publish_agent_release
from .stable_coverage import discover_stable_coverage
from .views import _update_agent_dispatch_block_reason


settings = override_settings(
    NIGHTOWL_STABLE_DESKTOP_ROLLOUT_ENABLED=True, NIGHTOWL_STABLE_DESKTOP_ROLLOUT_BATCH_SIZE=1,
    NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=True, NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=True,
)


class CoverageFixture:
    clone = fixtures.StableRolloutTests.clone

    def setUp(self):
        fixtures.StableRolloutTests.setUp(self)
        AgentRelease.objects.filter(pk=self.release.pk).update(status='draft')
        with patch('django.utils.timezone.now', return_value=self.now):
            self.release = publish_agent_release(self.release, self.actor, 'Synthetic Stable approval',
                                                 rollout_percentage=100, rollout_paused=False)
        self.original = AgentRolloutCampaign.objects.get(release=self.release)

    def complete(self, campaign):
        wave = transition_wave(campaign.waves.get(), 'observing', self.actor, 'Synthetic observation', now=self.now)
        transition_wave(wave, 'completed', self.actor, 'Synthetic completion', now=self.now)
        transition_campaign(campaign, 'completed', self.actor, 'Synthetic completion', now=self.now)

    def discover(self):
        return discover_stable_coverage(now=self.now)

    def newer(self, *, status='published'):
        fields = ('package_url', 'checksum_url', 'manifest_url', 'signature_url', 'sha256', 'size',
                  'manifest_sha256', 'signature_sha256', 'signature_key_id', 'signature_valid', 'legacy_unsigned')
        return AgentRelease.objects.create(
            version='0.1.2', channel='stable', source_channel='stable', status=status,
            rollout_percentage=100, released_at=self.now, **{field: getattr(self.release, field) for field in fields})


@settings
class StableCoverageTests(CoverageFixture, TestCase):
    def test_completed_original_new_endpoint_and_successive_rounds(self):
        self.complete(self.original)
        frozen = AgentRolloutCampaign.objects.values().get(pk=self.original.pk)
        first = self.clone()
        current = self.clone(agent_version=self.release.version)
        protected = self.clone(model='Virtual Machine')
        record = AgentRolloutCoverageRound.objects.get(pk=self.discover())
        self.assertEqual(record.campaign_id, self.original.pk)
        self.assertEqual(record.execution_campaign.release_id, self.release.pk)
        self.assertEqual(record.execution_campaign.state, 'running')
        self.assertEqual(list(record.execution_campaign.targets.values_list('endpoint_id', flat=True)), [first.pk])
        outcomes = {entry['endpoint_id']: entry['reason'] for entry in record.target_snapshot}
        self.assertEqual(outcomes[str(current.pk)], 'already_current')
        self.assertEqual(outcomes[str(protected.pk)], 'stable_protected_device')
        self.assertEqual(record.already_current_count, 1)
        self.assertEqual(dispatch_rollout_campaign(record.execution_campaign, now=self.now)['created_count'], 1)
        self.complete(record.execution_campaign)
        self.assertIsNone(self.discover())
        second = self.clone()
        next_record = AgentRolloutCoverageRound.objects.get(pk=self.discover())
        self.assertEqual(next_record.sequence, 2)
        self.assertEqual(list(next_record.execution_campaign.targets.values_list('endpoint_id', flat=True)), [second.pk])
        self.assertEqual(AgentRolloutCampaign.objects.values().get(pk=self.original.pk), frozen)
        self.assertEqual(AgentJob.objects.count(), 1)

    def test_offline_and_late_policy_are_reconsidered(self):
        self.complete(self.original)
        offline = self.clone(status='offline')
        late = self.clone(update_policy='manual')
        record = AgentRolloutCoverageRound.objects.get(pk=self.discover())
        self.assertIsNone(record.execution_campaign_id)
        self.assertEqual(record.excluded_count, 2)
        self.assertIn('endpoint_offline', record.reason_summary)
        self.assertIsNone(self.discover())
        type(offline).objects.filter(pk=offline.pk).update(status='online', last_seen_at=self.now)
        type(late).objects.filter(pk=late.pk).update(update_policy='automatic')
        record = AgentRolloutCoverageRound.objects.get(pk=self.discover())
        self.assertCountEqual(record.execution_campaign.targets.values_list('endpoint_id', flat=True), [offline.pk, late.pk])
        self.assertIsNone(self.discover())

    def test_paused_and_aborted_campaigns_block_catch_up(self):
        self.clone()
        transition_campaign(self.original, 'paused', self.actor, 'Synthetic pause', now=self.now)
        self.assertIsNone(self.discover())
        transition_campaign(self.original, 'aborted', self.actor, 'Synthetic abort', now=self.now)
        self.assertIsNone(self.discover())
        self.assertEqual(AgentRolloutCampaign.objects.count(), 1)

    def test_original_approver_must_still_be_authorized(self):
        self.complete(self.original)
        self.clone()
        type(self.actor).objects.filter(pk=self.actor.pk).update(is_active=False)
        with self.assertRaisesMessage(PolicyContractError, 'forbidden'):
            self.discover()
        self.assertFalse(self.original.coverage_rounds.exists())

    def test_newer_release_blocks_old_discovery_and_dispatch(self):
        self.complete(self.original)
        self.clone()
        record = AgentRolloutCoverageRound.objects.get(pk=self.discover())
        self.newer(status='paused')
        with self.assertRaisesMessage(PolicyContractError, 'stable_catch_up_release_no_longer_applicable'):
            dispatch_rollout_campaign(record.execution_campaign, now=self.now)
        self.complete(record.execution_campaign)
        self.clone()
        self.assertIsNone(self.discover())
        self.assertFalse(AgentJob.objects.exists())

    def test_explicit_supersession_blocks_new_rounds(self):
        self.complete(self.original)
        self.clone()
        replacement = self.newer()
        AgentRelease.objects.filter(pk=self.release.pk).update(
            status=AgentRelease.STATUS_SUPERSEDED, replacement_release=replacement,
        )
        self.assertIsNone(self.discover())
        self.assertFalse(self.original.coverage_rounds.exists())

    def test_newer_release_blocks_delivery_of_queued_complementary_job(self):
        self.complete(self.original)
        endpoint = self.clone()
        campaign = AgentRolloutCoverageRound.objects.get(pk=self.discover()).execution_campaign
        dispatch_rollout_campaign(campaign, now=self.now)
        target = campaign.targets.get()
        wave = campaign.waves.get()
        self.newer()
        self.assertEqual(_update_agent_dispatch_block_reason(
            target.agent_job, endpoint, self.release, {campaign.pk: campaign},
            {wave.pk: wave}, {target.pk: target}, self.now,
        ), 'stable_catch_up_release_no_longer_applicable')

    def test_active_job_defers_selection_and_abort_of_executor_stops_future_rounds(self):
        self.complete(self.original)
        endpoint = self.clone()
        job = AgentJob.objects.create(endpoint=endpoint, job_type='update_agent', status='queued')
        record = AgentRolloutCoverageRound.objects.get(pk=self.discover())
        self.assertEqual(record.reason_summary, {'update_job_active': 1})
        self.assertIsNone(record.execution_campaign_id)
        AgentJob.objects.filter(pk=job.pk).update(status='completed')
        record = AgentRolloutCoverageRound.objects.get(pk=self.discover())
        transition_campaign(record.execution_campaign, 'aborted', self.actor, 'Synthetic abort', now=self.now)
        self.clone()
        self.assertIsNone(self.discover())
        self.assertEqual(AgentRolloutCampaign.objects.count(), 2)

    def test_invalid_binding_rejected(self):
        self.complete(self.original)
        self.clone()
        record = AgentRolloutCoverageRound.objects.get(pk=self.discover())
        record.execution_campaign = self.original
        with self.assertRaises(ValidationError):
            record.clean()
        record.execution_campaign = AgentRolloutCampaign(release=self.newer())
        with self.assertRaises(ValidationError):
            record.clean()

    def test_failed_round_rolls_back_campaign_and_audit(self):
        self.complete(self.original)
        self.clone()
        before = AuditEvent.objects.count()
        with patch.object(AgentRolloutCoverageRound, 'save', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaisesMessage(RuntimeError, 'synthetic failure'):
                self.discover()
        self.assertFalse(self.original.coverage_rounds.exists())
        self.assertEqual(AgentRolloutCampaign.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.count(), before)
        self.assertTrue(self.discover())


@settings
@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL advisory and row locks')
class StableCoverageConcurrencyTests(CoverageFixture, TransactionTestCase):
    def test_discovery_transactions_serialize_even_without_runner_lock(self):
        self.complete(self.original)
        self.clone()
        entered, proceed, second_entered = Event(), Event(), Event()
        save = AgentRolloutCoverageRound.save

        def hold(instance, *args, **kwargs):
            entered.set()
            if not proceed.wait(15):
                raise RuntimeError('synthetic transaction timed out')
            return save(instance, *args, **kwargs)

        def discover(started=None):
            try:
                if started:
                    started.set()
                return self.discover()
            finally:
                connections.close_all()

        with patch.object(AgentRolloutCoverageRound, 'save', hold):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(discover)
                try:
                    self.assertTrue(entered.wait(15))
                    second = pool.submit(discover, second_entered)
                    self.assertTrue(second_entered.wait(15))
                finally:
                    proceed.set()
                self.assertIsNotNone(first.result(timeout=30))
                self.assertIsNone(second.result(timeout=30))
        self.assertEqual(self.original.coverage_rounds.count(), 1)
        self.assertEqual(AgentRolloutCampaign.objects.count(), 2)
        self.assertFalse(AgentJob.objects.exists())

    def test_two_runners_create_one_round_one_executor_one_job(self):
        self.complete(self.original)
        endpoint = self.clone()
        entered, proceed = Event(), Event()

        def discover(*, now):
            entered.set()
            if not proceed.wait(15):
                raise RuntimeError('synthetic runner timed out')
            return discover_stable_coverage(now=now)

        def run():
            try:
                return run_rollout_round(now=self.now)
            finally:
                connections.close_all()

        with patch('agents.rollout_runner.discover_stable_coverage', side_effect=discover):
            with ThreadPoolExecutor(max_workers=1) as pool:
                first = pool.submit(run)
                try:
                    self.assertTrue(entered.wait(15))
                    self.assertEqual(run_rollout_round(now=self.now)['status'], 'already_running')
                finally:
                    proceed.set()
                self.assertEqual(first.result(timeout=30)['campaigns'][0]['created_count'], 1)
        self.assertEqual(run_rollout_round(now=self.now)['campaigns'][0]['created_count'], 0)
        self.assertEqual(self.original.coverage_rounds.count(), 1)
        self.assertEqual(AgentRolloutCampaign.objects.count(), 2)
        self.assertEqual(AgentJob.objects.filter(endpoint=endpoint).count(), 1)
