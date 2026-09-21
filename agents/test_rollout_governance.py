import io
import json
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from . import test_rollout_control as control_fixtures
from .fleet_policy import PolicyContractError
from .models import (
    AgentJob,
    AgentJobResultReceipt,
    AgentMachine,
    AgentOperationalStatus,
    AgentRelease,
    AgentReleaseGroup,
    AgentRolloutCampaign,
    AuditEvent,
)
from .rollout_campaigns import create_agent_rollout_campaign_from_preview
from .rollout_control import control_rollout_campaign
from .rollout_dispatch import dispatch_rollout_campaign
from .rollout_governance import (
    apply_rollout_governance,
    build_rollout_advance_preview,
    evaluate_rollout_governance,
    governance_runner_lock,
    observation_elapsed_seconds,
    run_governance_round,
    validate_auto_pause_policy,
)
from .rollout_reconcile import reconcile_rollout_campaign
from .rollout_models import default_auto_pause_policy
from .services import build_agent_rollout_preview


@override_settings(NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=True,
                   NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=True,
                   NIGHTOWL_ROLLOUT_GOVERNANCE_ENABLED=True)
class RolloutGovernanceTests(TestCase):
    setUp = control_fixtures.RolloutControlTests.setUp
    create = control_fixtures.RolloutControlTests.create
    act = control_fixtures.RolloutControlTests.act
    running = control_fixtures.RolloutControlTests.running
    add_endpoints = control_fixtures.RolloutControlTests.add_endpoints

    def add_synthetic_endpoints(self, count):
        AgentMachine.objects.bulk_create([
            AgentMachine(
                hostname=f'GOVERNANCE-{index}', machine_id=str(uuid.uuid4()),
                agent_token_hash=f'synthetic-governance-{index}',
                agent_version=self.machine.agent_version,
                updater_version=self.machine.updater_version,
                update_channel=self.machine.update_channel,
                update_policy=self.machine.update_policy,
                auto_update_enabled=True,
                agent_lifecycle_status='installed', status='online', last_seen_at=self.now,
            )
            for index in range(count)
        ])

    def dispatch(self, campaign):
        return dispatch_rollout_campaign(campaign, now=self.now)

    def complete_targets(self, campaign, *, conflicts=0):
        for target in campaign.targets.filter(agent_job__isnull=False).select_related('agent_job', 'endpoint'):
            result_id = f'synthetic-{uuid.uuid4()}'
            AgentJob.objects.filter(pk=target.agent_job_id).update(
                status='completed', exit_code=0, result_id=result_id, result_received_at=self.now,
                finished_at=self.now, result={'installed_version': campaign.release.version,
                    'target_version': campaign.release.version, 'health_check': {'confirmed': True}, 'stage': 'completed'})
            AgentMachine.objects.filter(pk=target.endpoint_id).update(
                agent_version=campaign.release.version, status='online', is_active=True,
                agent_lifecycle_status='installed', last_seen_at=self.now)
            AgentJobResultReceipt.objects.create(result_id=result_id, job_id=target.agent_job_id,
                endpoint_id=target.endpoint_id, payload_sha256='0' * 64, first_payload={}, conflict_count=conflicts)
        reconcile_rollout_campaign(campaign, now=self.now)

    def test_policy_default_custom_and_strict_validation(self):
        campaign = self.create()
        self.assertEqual(campaign.auto_pause_policy['schema'], 1)
        self.assertTrue(campaign.auto_pause_policy['enabled'])
        custom = dict(campaign.auto_pause_policy, enabled=False, failed_count=3)
        self.assertEqual(validate_auto_pause_policy(custom), custom)
        invalid = [dict(custom, schema=2), dict(custom, extra=1), dict(custom, failed_count=0),
                   dict(custom, failed_count=-1), dict(custom, failed_count=True), {'schema': 1}]
        for value in invalid:
            with self.assertRaisesMessage(PolicyContractError, 'invalid_auto_pause_policy'):
                validate_auto_pause_policy(value)

    def test_policy_is_editable_in_draft_and_database_immutable_after_ready(self):
        campaign = self.create(ready=False)
        policy = dict(campaign.auto_pause_policy, failed_count=4)
        AgentRolloutCampaign.objects.filter(pk=campaign.pk).update(auto_pause_policy=policy)
        self.act(campaign, 'approve')
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentRolloutCampaign.objects.filter(pk=campaign.pk).update(
                auto_pause_policy=dict(policy, failed_count=5))

    def _negative_campaign(self, status, *, policy=None):
        campaign = self.create(**({'auto_pause_policy': policy} if policy else {}))
        self.act(campaign, 'start'); wave = campaign.waves.get(); self.act(campaign, 'prepare', wave); self.act(campaign, 'start', wave)
        self.dispatch(campaign)
        target = campaign.targets.get(agent_job__isnull=False)
        AgentJob.objects.filter(pk=target.agent_job_id).update(status=status)
        reconcile_rollout_campaign(campaign, now=self.now)
        return campaign, wave

    def test_normal_auto_pause_matrix_and_exact_threshold(self):
        mappings = [('failed', 'auto_pause_failed'), ('rolled_back', 'auto_pause_rolled_back'),
                    ('cancelled', 'auto_pause_cancelled')]
        for status, reason in mappings:
            campaign, _ = self._negative_campaign(status)
            decision = evaluate_rollout_governance(campaign, now=self.now)
            self.assertEqual((decision['decision'], decision['reason_code']), ('auto_pause', reason))
            apply_rollout_governance(campaign, now=self.now)
            campaign.refresh_from_db()
            self.assertEqual((campaign.state, campaign.current_wave.state), ('paused', 'paused'))
            self.assertEqual(AuditEvent.objects.filter(event_type='rollout.auto_paused').count(), 1)
            break

    def test_rolled_back_auto_pause(self):
        campaign, _ = self._negative_campaign('rolled_back')
        self.assertEqual(evaluate_rollout_governance(campaign, now=self.now)['reason_code'], 'auto_pause_rolled_back')

    def test_cancelled_auto_pause(self):
        campaign, _ = self._negative_campaign('cancelled')
        self.assertEqual(evaluate_rollout_governance(campaign, now=self.now)['reason_code'], 'auto_pause_cancelled')

    def test_threshold_below_then_exact_and_policy_disabled(self):
        self.add_synthetic_endpoints(2)
        policy = dict(default_auto_pause_policy(), failed_count=2)
        campaign = self.create(auto_pause_policy=policy, concurrency_limit=3)
        self.act(campaign, 'start'); wave = campaign.waves.get(); self.act(campaign, 'prepare', wave); self.act(campaign, 'start', wave)
        self.dispatch(campaign)
        jobs = list(AgentJob.objects.filter(
            pk__in=campaign.targets.values('agent_job_id')).order_by('pk'))
        for index, expected in ((1, False), (2, True), (3, True)):
            AgentJob.objects.filter(pk=jobs[index - 1].pk).update(status='failed')
            reconcile_rollout_campaign(campaign, now=self.now)
            decision = evaluate_rollout_governance(campaign, now=self.now)
            self.assertEqual(decision['auto_pause_required'], expected)
            if expected:
                self.assertEqual(decision['evidence']['observed_count'], index)
                self.assertEqual(decision['evidence']['threshold'], 2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentRolloutCampaign.objects.filter(pk=campaign.pk).update(auto_pause_policy=dict(policy, enabled=False))

        self.act(campaign, 'abort')
        disabled = self.create(auto_pause_policy=dict(default_auto_pause_policy(), enabled=False), concurrency_limit=3)
        self.act(disabled, 'start'); disabled_wave = disabled.waves.get()
        self.act(disabled, 'prepare', disabled_wave); self.act(disabled, 'start', disabled_wave)
        self.dispatch(disabled)
        AgentJob.objects.filter(pk__in=disabled.targets.values('agent_job_id')).update(status='failed')
        reconcile_rollout_campaign(disabled, now=self.now)
        self.assertFalse(evaluate_rollout_governance(disabled, now=self.now)['auto_pause_required'])

    def test_governance_runtime_off_is_preview_only(self):
        campaign, _ = self._negative_campaign('failed')
        before = list(campaign.waves.values()), campaign.state, AuditEvent.objects.count()
        with override_settings(NIGHTOWL_ROLLOUT_GOVERNANCE_ENABLED=False):
            self.assertTrue(evaluate_rollout_governance(campaign, now=self.now)['auto_pause_required'])
            with self.assertRaisesMessage(PolicyContractError, 'rollout_governance_disabled'):
                apply_rollout_governance(campaign, now=self.now)
        self.assertEqual(before, (list(campaign.waves.values()), AgentRolloutCampaign.objects.get(pk=campaign.pk).state, AuditEvent.objects.count()))

    def test_hard_stop_pauses_even_when_policy_disabled(self):
        policy = dict(default_auto_pause_policy(), enabled=False)
        campaign = self.create(auto_pause_policy=policy)
        self.act(campaign, 'start'); wave = campaign.waves.get(); self.act(campaign, 'prepare', wave); self.act(campaign, 'start', wave)
        self.dispatch(campaign)
        target = campaign.targets.get()
        payload = dict(target.agent_job.payload)
        payload['rollout_metadata'] = dict(payload['rollout_metadata'], cohort_hash='invalid')
        AgentJob.objects.filter(pk=target.agent_job_id).update(payload=payload)
        decision = evaluate_rollout_governance(campaign, now=self.now)
        self.assertEqual(decision['reason_code'], 'auto_pause_binding_invalid')
        apply_rollout_governance(campaign, now=self.now)
        self.assertEqual(AgentRolloutCampaign.objects.get(pk=campaign.pk).state, 'paused')

    def test_receipt_conflict_and_rollback_failed_are_hard_stops(self):
        policy = dict(default_auto_pause_policy(), enabled=False)
        campaign = self.create(auto_pause_policy=policy)
        self.act(campaign, 'start'); wave = campaign.waves.get()
        self.act(campaign, 'prepare', wave); self.act(campaign, 'start', wave)
        self.dispatch(campaign); self.complete_targets(campaign, conflicts=1)
        decision = evaluate_rollout_governance(campaign, now=self.now)
        self.assertEqual(decision['reason_code'], 'auto_pause_receipt_conflict')
        self.act(campaign, 'abort')
        AgentMachine.objects.filter(pk=self.machine.pk).update(agent_version='0.1.1.0-rc38')

        campaign = self.create(auto_pause_policy=policy)
        self.act(campaign, 'start'); wave = campaign.waves.get()
        self.act(campaign, 'prepare', wave); self.act(campaign, 'start', wave)
        self.dispatch(campaign)
        AgentJob.objects.filter(pk__in=campaign.targets.values('agent_job_id')).update(status='rollback_failed')
        reconcile_rollout_campaign(campaign, now=self.now)
        decision = evaluate_rollout_governance(campaign, now=self.now)
        self.assertEqual(decision['reason_code'], 'auto_pause_rollback_failed')

    def test_stalled_and_offline_are_policy_triggers_but_waiting_is_not(self):
        campaign, _ = self.running()
        self.dispatch(campaign)
        target = campaign.targets.get()
        AgentJob.objects.filter(pk=target.agent_job_id).update(status='sent', dispatched_at=self.now)
        self.assertFalse(evaluate_rollout_governance(campaign, now=self.now)['auto_pause_required'])
        self.assertEqual(evaluate_rollout_governance(campaign, now=self.now + timedelta(minutes=6))['reason_code'], 'auto_pause_stalled')
        AgentJob.objects.filter(pk=target.agent_job_id).update(status='running', result={'update_status': 'waiting_health_check'}, result_received_at=self.now)
        self.assertFalse(evaluate_rollout_governance(campaign, now=self.now)['auto_pause_required'])
        AgentMachine.objects.filter(pk=target.endpoint_id).update(status='offline')
        self.assertEqual(evaluate_rollout_governance(campaign, now=self.now)['reason_code'], 'auto_pause_offline')

    def test_all_succeeded_enters_observation_and_completion_is_idempotent(self):
        campaign, wave = self.running()
        self.dispatch(campaign)
        self.complete_targets(campaign)
        decision = apply_rollout_governance(campaign, now=self.now)
        wave.refresh_from_db()
        self.assertEqual(wave.state, 'observing')
        self.assertEqual(wave.observation_resumed_at, self.now)
        self.assertEqual(AuditEvent.objects.filter(event_type='rollout.wave_observation_started').count(), 1)
        decision = apply_rollout_governance(campaign, now=self.now + timedelta(seconds=1))
        campaign.refresh_from_db(); wave.refresh_from_db()
        self.assertEqual((wave.state, campaign.state), ('completed', 'completed'))
        self.assertEqual(AuditEvent.objects.filter(event_type='rollout.wave_completed').count(), 1)
        self.assertEqual(AuditEvent.objects.filter(event_type='rollout.campaign_completed').count(), 1)
        self.assertEqual(apply_rollout_governance(campaign, now=self.now + timedelta(seconds=2))['decision'], 'terminal')

    def test_queued_running_and_waiting_evidence_do_not_enter_observation(self):
        campaign, _ = self.running()
        self.dispatch(campaign)
        job = campaign.targets.get().agent_job
        self.assertEqual(evaluate_rollout_governance(campaign, now=self.now)['reason_code'], 'wave_in_progress')
        AgentJob.objects.filter(pk=job.pk).update(status='running')
        reconcile_rollout_campaign(campaign, now=self.now)
        self.assertEqual(evaluate_rollout_governance(campaign, now=self.now)['reason_code'], 'wave_in_progress')
        AgentJob.objects.filter(pk=job.pk).update(
            status='completed', exit_code=0, result_id='synthetic-waiting',
            result_received_at=self.now, result={'installed_version': campaign.release.version})
        reconcile_rollout_campaign(campaign, now=self.now)
        decision = evaluate_rollout_governance(campaign, now=self.now)
        self.assertEqual(decision['reason_code'], 'wave_in_progress')
        self.assertFalse(decision['can_enter_observation'])

    def test_observation_duration_pause_resume_excludes_paused_time(self):
        campaign = self.create(wave_plan=[{'remaining': True, 'observation_seconds': 100}])
        self.act(campaign, 'start')
        wave = campaign.waves.get()
        self.act(campaign, 'prepare', wave)
        self.act(campaign, 'start', wave)
        self.dispatch(campaign); self.complete_targets(campaign)
        apply_rollout_governance(campaign, now=self.now)
        wave.refresh_from_db()
        pause_at = self.now + timedelta(seconds=30)
        campaign.refresh_from_db(); wave.refresh_from_db()
        control_rollout_campaign(campaign, {'action': 'pause', 'reason': 'Synthetic pause',
            'expected_state': campaign.state, 'expected_wave_state': wave.state}, self.actor, wave_id=wave.pk, now=pause_at)
        campaign.refresh_from_db()
        control_rollout_campaign(campaign, {'action': 'pause', 'reason': 'Synthetic campaign pause',
            'expected_state': campaign.state}, self.actor, now=pause_at)
        wave.refresh_from_db()
        self.assertEqual(observation_elapsed_seconds(wave, now=self.now + timedelta(hours=1)), 30)
        campaign.refresh_from_db()
        control_rollout_campaign(campaign, {'action': 'resume', 'reason': 'Synthetic resume',
            'expected_state': campaign.state}, self.actor, now=self.now + timedelta(hours=1))
        campaign.refresh_from_db(); wave.refresh_from_db()
        control_rollout_campaign(campaign, {'action': 'resume', 'reason': 'Synthetic wave resume',
            'expected_state': campaign.state, 'expected_wave_state': wave.state}, self.actor,
            wave_id=wave.pk, now=self.now + timedelta(hours=1))
        wave.refresh_from_db()
        AgentMachine.objects.filter(pk=campaign.targets.get().endpoint_id).update(
            last_seen_at=self.now + timedelta(hours=1))
        self.assertEqual(wave.observation_resumed_at, self.now + timedelta(hours=1))
        decision = evaluate_rollout_governance(campaign, now=self.now + timedelta(hours=1, seconds=69))
        self.assertEqual(decision['reason_code'], 'minimum_observation_pending')
        decision = evaluate_rollout_governance(campaign, now=self.now + timedelta(hours=1, seconds=70))
        self.assertTrue(decision['can_complete_wave'])

    def test_observation_health_risk_matrix(self):
        campaign, wave = self.running()
        self.dispatch(campaign); self.complete_targets(campaign); apply_rollout_governance(campaign, now=self.now)
        target = campaign.targets.get()
        changes = [
            ({'status': 'offline'}, 'observation_endpoint_offline'),
            ({'last_seen_at': self.now - timedelta(hours=1)}, 'observation_endpoint_stale'),
            ({'agent_version': '0.0.1'}, 'observation_version_drift'),
            ({'machine_id': str(uuid.uuid4())}, 'observation_identity_changed'),
            ({'is_active': False}, 'observation_endpoint_inactive'),
            ({'agent_lifecycle_status': 'uninstalled'}, 'observation_lifecycle_changed'),
        ]
        original = AgentMachine.objects.get(pk=target.endpoint_id)
        for update, reason in changes:
            AgentMachine.objects.filter(pk=target.endpoint_id).update(**update)
            self.assertEqual(evaluate_rollout_governance(campaign, now=self.now)['reason_code'], reason)
            AgentMachine.objects.filter(pk=target.endpoint_id).update(
                status=original.status, last_seen_at=original.last_seen_at, agent_version=original.agent_version,
                machine_id=original.machine_id, is_active=original.is_active,
                agent_lifecycle_status=original.agent_lifecycle_status)
        AgentOperationalStatus.objects.create(endpoint_id=target.endpoint_id, health_indicator='critical')
        self.assertEqual(evaluate_rollout_governance(campaign, now=self.now)['reason_code'], 'observation_health_critical')

    def test_observation_operational_status_contradictions(self):
        campaign, _ = self.running()
        self.dispatch(campaign); self.complete_targets(campaign); apply_rollout_governance(campaign, now=self.now)
        target = campaign.targets.get()
        status = AgentOperationalStatus.objects.create(
            endpoint_id=target.endpoint_id, health_indicator='healthy',
            installed_version=campaign.release.version, update_job_id='different-job')
        self.assertEqual(
            evaluate_rollout_governance(campaign, now=self.now)['reason_code'],
            'observation_operational_job_mismatch',
        )
        status.update_job_id = str(target.agent_job_id)
        status.installed_version = '0.0.1'
        status.save(update_fields=['update_job_id', 'installed_version'])
        self.assertEqual(
            evaluate_rollout_governance(campaign, now=self.now)['reason_code'],
            'observation_version_drift',
        )

    def _two_wave_campaign(self):
        self.add_endpoints()
        campaign = self.create(wave_plan=[{'count': 1}, {'remaining': True}], concurrency_limit=1)
        self.act(campaign, 'start')
        first = campaign.waves.get(sequence=1)
        self.act(campaign, 'prepare', first); self.act(campaign, 'start', first)
        self.dispatch(campaign); self.complete_targets(campaign)
        apply_rollout_governance(campaign, now=self.now)
        apply_rollout_governance(campaign, now=self.now + timedelta(seconds=1))
        campaign.refresh_from_db(); first.refresh_from_db()
        return campaign, first

    def test_advance_preview_hash_optimistic_prepare_and_no_substitution(self):
        campaign, first = self._two_wave_campaign()
        before_ids = list(campaign.targets.order_by('pk').values_list('pk', flat=True))
        first_preview = build_rollout_advance_preview(campaign, now=self.now)
        second_preview = build_rollout_advance_preview(campaign, now=self.now + timedelta(seconds=1))
        self.assertEqual(first_preview['advance_hash'], second_preview['advance_hash'])
        self.assertTrue(first_preview['advance_ready'])
        AgentMachine.objects.filter(pk=campaign.waves.get(sequence=2).targets.first().endpoint_id).update(update_paused=True)
        changed = build_rollout_advance_preview(campaign, now=self.now)
        self.assertFalse(changed['advance_ready'])
        with self.assertRaisesMessage(PolicyContractError, 'advance_preview_changed'):
            self.act(campaign, 'prepare_next_wave', first, expected_advance_schema=1,
                     expected_advance_hash=first_preview['advance_hash'])
        AgentMachine.objects.filter(pk=campaign.waves.get(sequence=2).targets.first().endpoint_id).update(update_paused=False)
        current = build_rollout_advance_preview(campaign, now=self.now)
        self.act(campaign, 'prepare_next_wave', first, expected_advance_schema=1,
                 expected_advance_hash=current['advance_hash'])
        self.assertEqual(campaign.waves.get(sequence=2).state, 'ready')
        self.assertEqual(before_ids, list(campaign.targets.order_by('pk').values_list('pk', flat=True)))
        self.assertEqual(AgentJob.objects.count(), 1)

    def test_advance_preview_revalidates_release_endpoint_group_updater_and_jobs(self):
        campaign, _ = self._two_wave_campaign()
        endpoint = campaign.waves.get(sequence=2).targets.first().endpoint
        baseline = build_rollout_advance_preview(campaign, now=self.now)
        self.assertTrue(baseline['advance_ready'])
        cases = [
            ('release_revoked', lambda: AgentRelease.objects.filter(pk=campaign.release_id).update(revoked=True),
             lambda: AgentRelease.objects.filter(pk=campaign.release_id).update(revoked=False)),
            ('endpoint_paused', lambda: AgentMachine.objects.filter(pk=endpoint.pk).update(update_paused=True),
             lambda: AgentMachine.objects.filter(pk=endpoint.pk).update(update_paused=False)),
            ('updater_bootstrap_required', lambda: AgentMachine.objects.filter(pk=endpoint.pk).update(updater_version='0.1.0.7'),
             lambda: AgentMachine.objects.filter(pk=endpoint.pk).update(updater_version=self.machine.updater_version)),
        ]
        for reason, change, restore in cases:
            with self.subTest(reason=reason):
                change()
                preview = build_rollout_advance_preview(campaign, now=self.now)
                self.assertFalse(preview['advance_ready'])
                self.assertIn(reason, preview['reason_counts'])
                self.assertNotEqual(preview['advance_hash'], baseline['advance_hash'])
                restore()

        remote = AgentReleaseGroup.objects.get(slug='remote')
        endpoint.rollout_groups.add(remote)
        preview = build_rollout_advance_preview(campaign, now=self.now)
        self.assertIn('endpoint_groups_changed', preview['reason_counts'])
        endpoint.rollout_groups.clear()

        job = AgentJob.objects.create(endpoint=endpoint, job_type='repair_agent', status='queued', payload={})
        preview = build_rollout_advance_preview(campaign, now=self.now)
        self.assertIn('update_job_active', preview['reason_counts'])
        job.delete()
        self.assertEqual(build_rollout_advance_preview(campaign, now=self.now)['advance_hash'], baseline['advance_hash'])

    def test_advance_requires_completed_predecessor_and_existing_next_wave(self):
        self.add_synthetic_endpoints(1)
        campaign = self.create(wave_plan=[{'count': 1}, {'remaining': True}])
        preview = build_rollout_advance_preview(campaign, now=self.now)
        self.assertFalse(preview['advance_ready'])
        self.assertIn('previous_wave_not_completed', preview['reason_counts'])
        self.act(campaign, 'abort')

        campaign, wave = self.running()
        self.dispatch(campaign); self.complete_targets(campaign)
        apply_rollout_governance(campaign, now=self.now)
        apply_rollout_governance(campaign, now=self.now + timedelta(seconds=1))
        preview = build_rollout_advance_preview(campaign, now=self.now)
        self.assertFalse(preview['advance_ready'])
        self.assertEqual(preview['reason_counts'], {'next_wave_unavailable': 1})

    def test_negative_campaign_never_auto_completes(self):
        campaign, wave = self._negative_campaign('failed')
        decision = evaluate_rollout_governance(campaign, now=self.now)
        self.assertTrue(decision['auto_pause_required'])
        self.assertFalse(decision['can_complete_wave'])
        self.assertFalse(decision['can_complete_campaign'])
        apply_rollout_governance(campaign, now=self.now)
        campaign.refresh_from_db(); wave.refresh_from_db()
        self.assertEqual((campaign.state, wave.state), ('paused', 'paused'))
        self.assertFalse(AuditEvent.objects.filter(event_type='rollout.campaign_completed').exists())

    def test_250_target_governance_queries_are_bounded(self):
        self.add_endpoints()
        campaign = self.create(concurrency_limit=250)
        self.act(campaign, 'start'); wave = campaign.waves.get(); self.act(campaign, 'prepare', wave); self.act(campaign, 'start', wave)
        with CaptureQueriesContext(connection) as queries:
            decision = evaluate_rollout_governance(campaign, now=self.now)
        self.assertEqual(decision['reason_code'], 'wave_in_progress')
        self.assertLess(len(queries), 20)
        print(f'Governance summary: 250 targets, queries={len(queries)}')

    def test_command_plan_works_off_and_run_requires_flag(self):
        campaign, _ = self.running()
        output = io.StringIO()
        with override_settings(NIGHTOWL_ROLLOUT_GOVERNANCE_ENABLED=False):
            call_command('govern_agent_rollouts', plan_only=True, campaign=str(campaign.pk), stdout=output)
            self.assertEqual(json.loads(output.getvalue())['campaign_id'], str(campaign.pk))
            with self.assertRaisesMessage(CommandError, 'rollout_governance_disabled'):
                call_command('govern_agent_rollouts', run_once=True)

    def test_audit_failure_rolls_back_automatic_transition(self):
        campaign, _ = self._negative_campaign('failed')
        with patch.object(AuditEvent.objects, 'create', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                apply_rollout_governance(campaign, now=self.now)
        campaign.refresh_from_db()
        self.assertEqual(campaign.state, 'running')


@unittest.skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL advisory/row locks')
@override_settings(NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=True,
                   NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=True,
                   NIGHTOWL_ROLLOUT_GOVERNANCE_ENABLED=True)
class PostgreSQLRolloutGovernanceTests(TransactionTestCase):
    setUp = RolloutGovernanceTests.setUp
    create = RolloutGovernanceTests.create
    act = RolloutGovernanceTests.act
    running = RolloutGovernanceTests.running
    dispatch = RolloutGovernanceTests.dispatch
    complete_targets = RolloutGovernanceTests.complete_targets
    _negative_campaign = RolloutGovernanceTests._negative_campaign
    add_synthetic_endpoints = RolloutGovernanceTests.add_synthetic_endpoints

    def two_target_campaign_with_failure(self):
        self.add_synthetic_endpoints(1)
        campaign = self.create(concurrency_limit=1)
        self.act(campaign, 'start')
        wave = campaign.waves.get()
        self.act(campaign, 'prepare', wave)
        self.act(campaign, 'start', wave)
        self.dispatch(campaign)
        first = campaign.targets.get(agent_job__isnull=False)
        AgentJob.objects.filter(pk=first.agent_job_id).update(status='failed')
        reconcile_rollout_campaign(campaign, now=self.now)
        return campaign, wave

    def test_two_governance_runners_and_restart_safety(self):
        campaign, wave = self.running(); self.dispatch(campaign); self.complete_targets(campaign)
        acquired, release = threading.Event(), threading.Event()

        def hold():
            connections.close_all()
            try:
                with governance_runner_lock() as locked:
                    self.assertTrue(locked); acquired.set(); release.wait(timeout=10)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold); self.assertTrue(acquired.wait(timeout=10))
            self.assertEqual(run_governance_round(now=self.now)['status'], 'already_running')
            release.set(); future.result(timeout=15)
        connections.close_all()
        run_governance_round(now=self.now)
        wave.refresh_from_db()
        self.assertEqual(wave.state, 'observing')

    def test_two_auto_pause_calls_are_idempotent(self):
        campaign, _ = self._negative_campaign('failed')
        barrier = threading.Barrier(2)

        def run():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                return apply_rollout_governance(campaign.pk, now=self.now)['decision']
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertIn('already_paused', results)
        self.assertEqual(AuditEvent.objects.filter(event_type='rollout.auto_paused').count(), 1)

    def test_dispatch_commit_before_governance_can_create_only_pre_pause_job(self):
        campaign, _ = self.two_target_campaign_with_failure()
        acquired, release = threading.Event(), threading.Event()
        original = AuditEvent.objects.create

        def hold_dispatch(**kwargs):
            result = original(**kwargs)
            if kwargs.get('event_type') == 'rollout.dispatch_created':
                acquired.set()
                release.wait(timeout=10)
            return result

        def dispatch():
            connections.close_all()
            try:
                return dispatch_rollout_campaign(campaign.pk, now=self.now)['created_count']
            finally:
                connections.close_all()

        def govern():
            connections.close_all()
            try:
                return apply_rollout_governance(campaign.pk, now=self.now)['decision']
            finally:
                connections.close_all()

        with patch.object(AuditEvent.objects, 'create', side_effect=hold_dispatch), ThreadPoolExecutor(max_workers=2) as pool:
            dispatch_future = pool.submit(dispatch)
            self.assertTrue(acquired.wait(timeout=10))
            govern_future = pool.submit(govern)
            time.sleep(0.1)
            self.assertFalse(govern_future.done())
            release.set()
            self.assertEqual(dispatch_future.result(timeout=20), 1)
            govern_future.result(timeout=20)
        campaign.refresh_from_db()
        self.assertEqual(campaign.state, 'paused')
        self.assertEqual(AgentJob.objects.count(), 2)

    def test_governance_commit_before_dispatch_creates_zero_post_pause_jobs(self):
        campaign, _ = self.two_target_campaign_with_failure()
        acquired, release = threading.Event(), threading.Event()
        original = AuditEvent.objects.create

        def hold_governance(**kwargs):
            result = original(**kwargs)
            if kwargs.get('event_type') == 'rollout.auto_paused':
                acquired.set()
                release.wait(timeout=10)
            return result

        def govern():
            connections.close_all()
            try:
                return apply_rollout_governance(campaign.pk, now=self.now)['decision']
            finally:
                connections.close_all()

        def dispatch():
            connections.close_all()
            try:
                try:
                    return dispatch_rollout_campaign(campaign.pk, now=self.now)['created_count']
                except PolicyContractError:
                    return 0
            finally:
                connections.close_all()

        with patch.object(AuditEvent.objects, 'create', side_effect=hold_governance), ThreadPoolExecutor(max_workers=2) as pool:
            govern_future = pool.submit(govern)
            self.assertTrue(acquired.wait(timeout=10))
            dispatch_future = pool.submit(dispatch)
            time.sleep(0.1)
            self.assertFalse(dispatch_future.done())
            release.set()
            govern_future.result(timeout=20)
            self.assertEqual(dispatch_future.result(timeout=20), 0)
        campaign.refresh_from_db()
        self.assertEqual(campaign.state, 'paused')
        self.assertEqual(AgentJob.objects.count(), 1)

    def test_governance_serializes_with_human_abort(self):
        campaign, _ = self._negative_campaign('failed')
        barrier = threading.Barrier(2)

        def govern():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                return apply_rollout_governance(campaign.pk, now=self.now)['decision']
            except PolicyContractError:
                return 'conflict'
            finally:
                connections.close_all()

        def abort():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                control_rollout_campaign(campaign.pk, {
                    'action': 'abort', 'reason': 'Synthetic concurrent abort',
                    'expected_state': 'running',
                }, self.actor, now=self.now)
                return 'changed'
            except PolicyContractError:
                return 'conflict'
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(govern), pool.submit(abort)]
            [future.result(timeout=20) for future in results]
        campaign.refresh_from_db()
        self.assertIn(campaign.state, {'paused', 'aborted'})
        self.assertLessEqual(AuditEvent.objects.filter(event_type='rollout.auto_paused').count(), 1)

    def test_auto_pause_serializes_with_human_pause(self):
        campaign, wave = self._negative_campaign('failed')
        barrier = threading.Barrier(2)

        def govern():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                return apply_rollout_governance(campaign.pk, now=self.now)['decision']
            except PolicyContractError:
                return 'conflict'
            finally:
                connections.close_all()

        def pause():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                control_rollout_campaign(campaign.pk, {
                    'action': 'pause', 'reason': 'Synthetic concurrent pause',
                    'expected_state': 'running',
                }, self.actor, now=self.now)
                return 'changed'
            except PolicyContractError:
                return 'conflict'
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(govern), pool.submit(pause)]
            [future.result(timeout=20) for future in results]
        campaign.refresh_from_db(); wave.refresh_from_db()
        self.assertEqual(campaign.state, 'paused')
        self.assertIn(wave.state, {'running', 'paused'})
        self.assertLessEqual(AuditEvent.objects.filter(event_type='rollout.auto_paused').count(), 1)

    def _observing_campaign(self):
        campaign, wave = self.running()
        self.dispatch(campaign); self.complete_targets(campaign)
        apply_rollout_governance(campaign, now=self.now)
        wave.refresh_from_db()
        return campaign, wave

    def test_wave_completion_serializes_with_human_wave_pause(self):
        campaign, wave = self._observing_campaign()
        barrier = threading.Barrier(2)

        def complete():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                return apply_rollout_governance(campaign.pk, now=self.now + timedelta(seconds=1))['decision']
            except PolicyContractError:
                return 'conflict'
            finally:
                connections.close_all()

        def pause_wave():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                control_rollout_campaign(campaign.pk, {
                    'action': 'pause', 'reason': 'Synthetic concurrent wave pause',
                    'expected_state': 'running', 'expected_wave_state': 'observing',
                }, self.actor, wave_id=wave.pk, now=self.now + timedelta(seconds=1))
                return 'changed'
            except PolicyContractError:
                return 'conflict'
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(complete), pool.submit(pause_wave)]
            [future.result(timeout=20) for future in futures]
        campaign.refresh_from_db(); wave.refresh_from_db()
        self.assertIn(wave.state, {'completed', 'paused'})
        self.assertIn(campaign.state, {'running', 'completed'})

    def test_observation_completion_serializes_with_abort(self):
        campaign, wave = self._observing_campaign()
        barrier = threading.Barrier(2)

        def complete():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                return apply_rollout_governance(campaign.pk, now=self.now + timedelta(seconds=1))['decision']
            except PolicyContractError:
                return 'conflict'
            finally:
                connections.close_all()

        def abort():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                control_rollout_campaign(campaign.pk, {
                    'action': 'abort', 'reason': 'Synthetic concurrent observation abort',
                    'expected_state': 'running',
                }, self.actor, now=self.now + timedelta(seconds=1))
                return 'changed'
            except PolicyContractError:
                return 'conflict'
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(complete), pool.submit(abort)]
            [future.result(timeout=20) for future in futures]
        campaign.refresh_from_db(); wave.refresh_from_db()
        self.assertIn(campaign.state, {'completed', 'aborted'})
        self.assertIn(wave.state, {'completed', 'cancelled'})

    def test_concurrent_wave_and_campaign_completion_are_idempotent(self):
        campaign, wave = self.running()
        self.dispatch(campaign); self.complete_targets(campaign)
        apply_rollout_governance(campaign, now=self.now)
        barrier = threading.Barrier(2)

        def complete():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                return apply_rollout_governance(campaign.pk, now=self.now + timedelta(seconds=1))['decision']
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: complete(), range(2)))
        campaign.refresh_from_db(); wave.refresh_from_db()
        self.assertEqual((campaign.state, wave.state), ('completed', 'completed'))
        self.assertIn('terminal', results)
        self.assertEqual(AuditEvent.objects.filter(event_type='rollout.wave_completed').count(), 1)
        self.assertEqual(AuditEvent.objects.filter(event_type='rollout.campaign_completed').count(), 1)

    def test_migration_0033_backfill_guards_reverse_and_reapply(self):
        campaign = self.create(ready=False)
        campaign_id = campaign.pk

        MigrationExecutor(connection).migrate([('agents', '0032_rollout_target_execution')])
        MigrationExecutor(connection).migrate([('agents', '0033_agentrolloutcampaign_auto_pause_policy_and_more')])
        campaign = AgentRolloutCampaign.objects.get(pk=campaign_id)
        self.assertEqual(campaign.auto_pause_policy, default_auto_pause_policy())
        wave = campaign.waves.get()
        self.assertEqual((wave.observation_accumulated_seconds, wave.observation_resumed_at), (0, None))

        self.act(campaign, 'approve')
        self.act(campaign, 'start')
        MigrationExecutor(connection).migrate([('agents', '0032_rollout_target_execution')])
        MigrationExecutor(connection).migrate([('agents', '0033_agentrolloutcampaign_auto_pause_policy_and_more')])
        campaign = AgentRolloutCampaign.objects.get(pk=campaign_id)
        self.assertEqual((campaign.state, campaign.auto_pause_policy), ('running', default_auto_pause_policy()))
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentRolloutCampaign.objects.filter(pk=campaign_id).update(
                auto_pause_policy=dict(default_auto_pause_policy(), failed_count=2))
        campaign.waves.update(observation_accumulated_seconds=10, observation_resumed_at=self.now)
        wave = campaign.waves.get()
        self.assertEqual((wave.observation_accumulated_seconds, wave.observation_resumed_at), (10, self.now))
