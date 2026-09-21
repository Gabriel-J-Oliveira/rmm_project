import io
import json
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from . import test_rollout_dispatch as fixtures
from .models import AgentJob, AgentJobResultReceipt, AgentRolloutTarget, AuditEvent
from .rollout_reconcile import (
    evaluate_rollout_target,
    reconcile_rollout_campaign,
    summarize_rollout_campaign,
)


@override_settings(NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=False, NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=False)
class RolloutReconcileTests(TestCase):
    setUp = fixtures.RolloutDispatchTests.setUp
    add_endpoints = fixtures.RolloutDispatchTests.add_endpoints

    def create_dispatched(self, **options):
        campaign = fixtures.RolloutDispatchTests.create(self, **options)
        with override_settings(NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=True, NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=True):
            fixtures.RolloutDispatchTests.dispatch(self, campaign)
        return campaign

    def target(self, campaign):
        return AgentRolloutTarget.objects.select_related('campaign__release', 'wave', 'endpoint', 'agent_job').get(campaign=campaign)

    def complete(self, campaign, *, installed=None, health=True, exit_code=0, receipt=True, conflicts=0):
        target = self.target(campaign)
        job = target.agent_job
        result_id = f'synthetic-result-{uuid.uuid4()}'
        installed = installed if installed is not None else campaign.release.version
        AgentJob.objects.filter(pk=job.pk).update(
            status='completed', exit_code=exit_code, result_id=result_id,
            result_received_at=self.now, finished_at=self.now,
            result={'installed_version': installed, 'target_version': campaign.release.version,
                    'health_check': {'confirmed': health}, 'stage': 'completed'},
        )
        target.endpoint.agent_version = installed
        target.endpoint.last_seen_at = self.now
        target.endpoint.save(update_fields=['agent_version', 'last_seen_at'])
        if receipt:
            AgentJobResultReceipt.objects.create(result_id=result_id, job_id=job.pk, endpoint=target.endpoint,
                payload_sha256='0' * 64, first_payload={}, conflict_count=conflicts)
        return job

    def evaluation(self, campaign):
        target = self.target(campaign)
        receipt = AgentJobResultReceipt.objects.filter(job=target.agent_job, result_id=target.agent_job.result_id).first()
        return evaluate_rollout_target(target, receipt=receipt, now=self.now)

    def test_complete_success_requires_every_evidence_and_is_idempotent(self):
        campaign = self.create_dispatched()
        self.complete(campaign)
        evaluation = self.evaluation(campaign)
        self.assertEqual((evaluation['classification'], evaluation['desired_state']), ('succeeded', 'succeeded'))
        first = reconcile_rollout_campaign(campaign, now=self.now)
        second = reconcile_rollout_campaign(campaign, now=self.now + timedelta(seconds=1))
        self.assertEqual(first['transition_count'], 1)
        self.assertEqual(second['transition_count'], 0)
        target = campaign.targets.get()
        self.assertEqual((target.state, target.completed_at), ('succeeded', self.now))
        self.assertEqual(AuditEvent.objects.filter(event_type='rollout.reconciled').count(), 1)

    def test_completed_incomplete_evidence_waits_without_false_success(self):
        campaign = self.create_dispatched()
        cases = [
            ({'receipt': False}, 'waiting_result_receipt'),
            ({'health': False}, 'waiting_health'),
            ({'installed': '0.1.1.0-rc38'}, 'waiting_version'),
            ({'conflicts': 1}, 'receipt_conflict'),
        ]
        for index, (options, classification) in enumerate(cases):
            AgentJobResultReceipt.objects.all().delete()
            self.complete(campaign, **options)
            evaluation = self.evaluation(campaign)
            self.assertEqual(evaluation['classification'], classification)
            reconcile_rollout_campaign(campaign, now=self.now + timedelta(seconds=index))
            self.assertEqual(campaign.targets.get().state, 'running')

    def test_nonzero_and_terminal_job_status_matrix(self):
        campaign = self.create_dispatched()
        self.complete(campaign, exit_code=10)
        self.assertEqual(self.evaluation(campaign)['desired_state'], 'failed')
        matrix = {
            'failed': 'failed', 'timed_out': 'failed', 'expired': 'failed', 'interrupted': 'failed',
            'cancelled': 'cancelled', 'rolled_back': 'rolled_back', 'rollback_failed': 'failed',
            'duplicate': 'failed',
        }
        for status, desired in matrix.items():
            AgentJob.objects.filter(pk=campaign.targets.get().agent_job_id).update(status=status)
            evaluation = self.evaluation(campaign)
            self.assertEqual(evaluation['desired_state'], desired)
            if status == 'rollback_failed':
                self.assertEqual(evaluation['classification'], 'rollback_failed')

    def test_queued_sent_running_stale_offline_and_waiting_health_metrics(self):
        campaign = self.create_dispatched()
        target = campaign.targets.get()
        target.endpoint.status = 'offline'
        target.endpoint.last_seen_at = self.now - timedelta(minutes=20)
        target.endpoint.save(update_fields=['status', 'last_seen_at'])
        job = target.agent_job
        AgentJob.objects.filter(pk=job.pk).update(status='sent', dispatched_at=self.now - timedelta(minutes=6))
        evaluation = self.evaluation(campaign)
        self.assertEqual((evaluation['desired_state'], evaluation['classification']), ('queued', 'dispatched'))
        self.assertTrue(evaluation['is_stale'])
        self.assertTrue(evaluation['offline_post_update'])
        AgentJob.objects.filter(pk=job.pk).update(status='running', result={'update_status': 'waiting_health_check'},
                                                   result_received_at=self.now - timedelta(minutes=6))
        evaluation = self.evaluation(campaign)
        self.assertEqual(evaluation['desired_state'], 'running')
        self.assertTrue(evaluation['is_stale'])

    def test_binding_invalid_fails_closed_and_never_repairs(self):
        campaign = self.create_dispatched()
        target = campaign.targets.get()
        payload = dict(target.agent_job.payload)
        payload['rollout_metadata'] = dict(payload['rollout_metadata'], cohort_hash='wrong')
        AgentJob.objects.filter(pk=target.agent_job_id).update(payload=payload)
        before = target.agent_job_id
        result = reconcile_rollout_campaign(campaign, now=self.now)
        target.refresh_from_db()
        self.assertEqual((target.state, target.agent_job_id), ('queued', before))
        self.assertEqual(result['metrics']['binding_invalid'], 1)
        self.assertEqual(AuditEvent.objects.filter(event_type='rollout.reconcile_integrity').count(), 1)
        reconcile_rollout_campaign(campaign, now=self.now)
        self.assertEqual(AuditEvent.objects.filter(event_type='rollout.reconcile_integrity').count(), 1)

    def test_terminal_target_never_reopens_on_late_conflict(self):
        campaign = self.create_dispatched()
        self.complete(campaign)
        reconcile_rollout_campaign(campaign, now=self.now)
        job = campaign.targets.get().agent_job
        AgentJob.objects.filter(pk=job.pk).update(status='failed', error_code='SYNTHETIC_LATE_FAILURE')
        result = reconcile_rollout_campaign(campaign, now=self.now + timedelta(seconds=1))
        self.assertEqual(campaign.targets.get().state, 'succeeded')
        self.assertEqual(result['transition_count'], 0)
        self.assertEqual(result['targets'][0]['classification'], 'terminal_evidence_conflict')

    def test_metrics_rates_and_wave_scope_are_read_only(self):
        campaign = self.create_dispatched()
        self.complete(campaign)
        before = list(campaign.targets.values())
        summary = summarize_rollout_campaign(campaign, now=self.now)
        self.assertEqual((summary['metrics']['total'], summary['metrics']['dispatched']), (1, 1))
        self.assertEqual(summary['metrics']['success_rate'], 100.0)
        self.assertEqual(next(iter(summary['waves'].values()))['total'], 1)
        self.assertEqual(before, list(campaign.targets.values()))
        self.assertFalse(AuditEvent.objects.filter(event_type='rollout.reconciled').exists())

    def test_command_modes_work_with_flags_off_and_paused_campaign(self):
        campaign = self.create_dispatched()
        self.complete(campaign)
        campaign.state = 'paused'
        campaign.save(update_fields=['state', 'updated_at'])
        with self.assertRaisesMessage(CommandError, 'RECONCILE_MODE_REQUIRED'):
            call_command('reconcile_agent_rollouts')
        output = io.StringIO()
        call_command('reconcile_agent_rollouts', campaign=str(campaign.pk), stdout=output)
        self.assertEqual(json.loads(output.getvalue())['campaigns'][0]['transition_count'], 1)
        output = io.StringIO()
        call_command('reconcile_agent_rollouts', run_once=True, stdout=output)
        self.assertEqual(json.loads(output.getvalue())['campaigns'][0]['transition_count'], 0)

    def test_250_targets_have_bounded_queries_and_ui_is_sanitized(self):
        self.add_endpoints()
        campaign = self.create_dispatched(concurrency_limit=250)
        with CaptureQueriesContext(connection) as queries:
            summary = summarize_rollout_campaign(campaign, now=self.now)
        self.assertEqual(summary['metrics']['total'], 250)
        self.assertLess(len(queries), 15)
        self.client.force_login(self.actor)
        response = self.client.get(f'/api/agent/rollout-campaigns/{campaign.pk}/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('reconciliation_metrics', body)
        for forbidden in ('package_url', 'stdout', 'stderr', 'agent_token'):
            self.assertNotIn(forbidden, body)


@unittest.skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks')
@override_settings(NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=False, NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=False)
class PostgreSQLRolloutReconcileTests(TransactionTestCase):
    setUp = RolloutReconcileTests.setUp
    create_dispatched = RolloutReconcileTests.create_dispatched
    target = RolloutReconcileTests.target
    complete = RolloutReconcileTests.complete

    def test_two_reconcilers_produce_one_transition_and_audit(self):
        campaign = self.create_dispatched()
        self.complete(campaign)
        barrier = threading.Barrier(2)

        def run():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                return reconcile_rollout_campaign(campaign.pk, now=self.now)['transition_count']
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertCountEqual(results, [1, 0])
        self.assertEqual(AgentRolloutTarget.objects.get(campaign=campaign).state, 'succeeded')
        self.assertEqual(AuditEvent.objects.filter(event_type='rollout.reconciled').count(), 1)

    def test_result_write_race_converges_on_next_round(self):
        campaign = self.create_dispatched()
        self.assertEqual(reconcile_rollout_campaign(campaign, now=self.now)['transition_count'], 0)
        self.complete(campaign)
        connections.close_all()
        self.assertEqual(reconcile_rollout_campaign(campaign, now=self.now)['transition_count'], 1)
        self.assertEqual(campaign.targets.get().state, 'succeeded')
