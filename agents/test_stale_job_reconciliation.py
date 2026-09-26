import json
import uuid
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase
from django.utils import timezone

from .models import AgentJob, AgentJobResultReceipt, AgentMachine, AuditEvent
from .stale_job_reconciliation import apply_stale_sent_job_timeout, evaluate_stale_sent_job_timeout


class StaleSentJobReconciliationTests(TestCase):
    def setUp(self):
        self.machine, token = AgentMachine.create_with_token(
            hostname='TEST-STALE-001', machine_id=str(uuid.uuid4()), agent_version='0.1.1.0-rc17',
        )
        self.client = Client(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.now = timezone.now()

    def job(self, *, status=AgentJob.STATUS_SENT, age=timedelta(hours=1), timeout_seconds=900):
        return AgentJob.objects.create(
            endpoint=self.machine, job_type=AgentJob.TYPE_UPDATE_AGENT, status=status,
            dispatched_at=self.now - age, timeout_seconds=timeout_seconds,
            payload={'target_version': '0.1.1.0-rc39'},
        )

    def test_sent_timeout_exceeded_is_eligible(self):
        decision = evaluate_stale_sent_job_timeout(self.job(), now=self.now)
        self.assertTrue(decision['eligible_for_timeout'])
        self.assertEqual(decision['stale_reason'], 'timeout_exceeded')
        self.assertEqual(decision['proposed_status'], AgentJob.STATUS_TIMED_OUT)

    def test_sent_dispatched_too_long_is_eligible(self):
        decision = evaluate_stale_sent_job_timeout(self.job(timeout_seconds=None), now=self.now)
        self.assertTrue(decision['eligible_for_timeout'])
        self.assertEqual(decision['stale_reason'], 'dispatched_too_long')

    def test_non_active_or_fresh_sent_jobs_are_blocked(self):
        for status in (
            AgentJob.STATUS_QUEUED, AgentJob.STATUS_COMPLETED,
            AgentJob.STATUS_FAILED, AgentJob.STATUS_TIMED_OUT,
        ):
            with self.subTest(status=status):
                decision = evaluate_stale_sent_job_timeout(self.job(status=status), now=self.now)
                self.assertFalse(decision['eligible_for_timeout'])
        fresh = evaluate_stale_sent_job_timeout(self.job(age=timedelta(minutes=1)), now=self.now)
        self.assertFalse(fresh['eligible_for_timeout'])

    def test_sent_evidence_still_blocks(self):
        for change in ('result', 'result_id', 'result_received_at', 'receipt'):
            with self.subTest(change=change):
                job = self.job()
                if change == 'receipt':
                    AgentJobResultReceipt.objects.create(
                        result_id=str(uuid.uuid4()), job=job, endpoint=self.machine,
                        payload_sha256='0' * 64,
                        first_payload={'status': 'running', 'result': {'update_status': 'runner_started'}},
                    )
                else:
                    setattr(job, change, {'update_status': 'runner_started'} if change == 'result' else
                            self.now if change == 'result_received_at' else str(uuid.uuid4()))
                    job.save(update_fields=[change])
                decision = evaluate_stale_sent_job_timeout(job, now=self.now)
                self.assertFalse(decision['eligible_for_timeout'])
                self.assertIn('receipt_exists' if change == 'receipt' else 'result_exists', decision['blockers'])

    def test_running_recent_is_blocked(self):
        decision = evaluate_stale_sent_job_timeout(
            self.job(status=AgentJob.STATUS_RUNNING, age=timedelta(minutes=1)), now=self.now,
        )
        self.assertFalse(decision['eligible_for_timeout'])
        self.assertIn('job_not_stale', decision['blockers'])

    def test_running_without_update_and_timeout_exceeded_are_eligible(self):
        no_timeout = self.job(status=AgentJob.STATUS_RUNNING, timeout_seconds=None)
        AgentJob.objects.filter(pk=no_timeout.pk).update(
            result_received_at=self.now - timedelta(minutes=20),
            result={'update_status': 'runner_started'},
        )
        no_timeout.refresh_from_db()
        decision = evaluate_stale_sent_job_timeout(no_timeout, now=self.now)
        self.assertTrue(decision['eligible_for_timeout'])
        self.assertEqual(decision['stale_reason'], 'running_without_update')

        timed_out = self.job(status=AgentJob.STATUS_RUNNING)
        decision = evaluate_stale_sent_job_timeout(timed_out, now=self.now)
        self.assertTrue(decision['eligible_for_timeout'])
        self.assertEqual(decision['stale_reason'], 'timeout_exceeded')

    def test_running_intermediate_result_and_receipt_are_eligible(self):
        job = self.job(status=AgentJob.STATUS_RUNNING)
        result_id = str(uuid.uuid4())
        job.result = {'update_status': 'runner_started', 'target_version': '0.1.1.0-rc39'}
        job.result_id = result_id
        job.result_received_at = self.now - timedelta(minutes=20)
        job.exit_code = 0  # Progress receipts may carry an exit code before completion.
        job.save(update_fields=['result', 'result_id', 'result_received_at', 'exit_code'])
        AgentJobResultReceipt.objects.create(
            result_id=result_id, job=job, endpoint=self.machine, payload_sha256='0' * 64,
            first_payload={'job_id': str(job.pk), 'status': 'running',
                           'result': {'update_status': 'runner_started', 'target_version': '0.1.1.0-rc39'}},
        )
        decision = evaluate_stale_sent_job_timeout(job, now=self.now)
        self.assertTrue(decision['eligible_for_timeout'])
        self.assertEqual(decision['intermediate_receipt_count'], 1)
        self.assertTrue(decision['intermediate_result_present'])
        self.assertFalse(decision['terminal_evidence_present'])

    def test_running_terminal_or_ambiguous_evidence_is_blocked(self):
        for result, receipt_payload in (
            ({'status': 'completed'}, None),
            ({'update_status': 'runner_started'}, {'status': 'completed'}),
            ({'message': 'unknown progress'}, None),
            ({'update_status': 'runner_started'}, {}),
        ):
            with self.subTest(result=result, receipt_payload=receipt_payload):
                job = self.job(status=AgentJob.STATUS_RUNNING)
                job.result = result
                job.save(update_fields=['result'])
                if receipt_payload is not None:
                    AgentJobResultReceipt.objects.create(
                        result_id=str(uuid.uuid4()), job=job, endpoint=self.machine,
                        payload_sha256='0' * 64, first_payload=receipt_payload,
                    )
                decision = evaluate_stale_sent_job_timeout(job, now=self.now)
                self.assertFalse(decision['eligible_for_timeout'])
                self.assertTrue(any(blocker in decision['blockers'] for blocker in
                                    ('result_not_intermediate', 'receipt_not_intermediate')))

    def test_running_apply_times_out_with_audit_and_is_idempotent(self):
        job = self.job(status=AgentJob.STATUS_RUNNING)
        result_id = str(uuid.uuid4())
        job.result = {'update_status': 'runner_started'}
        job.result_id = result_id
        job.result_received_at = self.now - timedelta(minutes=20)
        job.save(update_fields=['result', 'result_id', 'result_received_at'])
        AgentJobResultReceipt.objects.create(
            result_id=result_id, job=job, endpoint=self.machine, payload_sha256='0' * 64,
            first_payload={'job_id': str(job.pk), 'status': 'running',
                           'result': {'update_status': 'runner_started'}},
        )
        out = StringIO()
        call_command('reconcile_stale_agent_job', '--job', str(job.pk), stdout=out)
        dry_run = json.loads(out.getvalue())
        self.assertEqual((dry_run['status'], dry_run['stale_reason'], dry_run['proposed_status']),
                         ('running', 'timeout_exceeded', 'timed_out'))
        self.assertTrue(dry_run['eligible_for_timeout'])
        self.assertEqual(AgentJob.objects.get(pk=job.pk).status, AgentJob.STATUS_RUNNING)

        result = apply_stale_sent_job_timeout(job.pk, reason='Synthetic running timeout', now=self.now)
        self.assertTrue(result['applied'])
        job.refresh_from_db()
        self.assertEqual((job.status, job.finished_at, job.error_code),
                         (AgentJob.STATUS_TIMED_OUT, self.now, 'JOB_RUNNING_TIMEOUT'))
        self.assertEqual(job.result, {'update_status': 'runner_started'})
        audit = AuditEvent.objects.get(event_type='job.admin_timeout')
        self.assertEqual(audit.metadata['previous_status'], 'running')
        self.assertEqual(audit.metadata['new_status'], 'timed_out')
        self.assertEqual(audit.metadata['intermediate_receipt_count'], 1)
        self.assertEqual(audit.metadata['expected_timeout_at'], result['expected_timeout_at'])
        self.assertFalse(apply_stale_sent_job_timeout(job.pk, reason='Synthetic replay', now=self.now).get('applied', False))
        self.assertEqual(AuditEvent.objects.filter(event_type='job.admin_timeout').count(), 1)

    def test_running_terminal_before_apply_is_not_overwritten(self):
        job = self.job(status=AgentJob.STATUS_RUNNING)
        self.assertTrue(evaluate_stale_sent_job_timeout(job, now=self.now)['eligible_for_timeout'])
        AgentJob.objects.filter(pk=job.pk).update(status=AgentJob.STATUS_COMPLETED, finished_at=self.now)
        decision = apply_stale_sent_job_timeout(job.pk, reason='Synthetic concurrent completion', now=self.now)
        self.assertFalse(decision.get('applied', False))
        self.assertEqual(AgentJob.objects.get(pk=job.pk).status, AgentJob.STATUS_COMPLETED)
        self.assertFalse(AuditEvent.objects.filter(event_type='job.admin_timeout').exists())

    def test_running_audit_reason_is_redacted(self):
        job = self.job(status=AgentJob.STATUS_RUNNING)
        apply_stale_sent_job_timeout(job.pk, reason='Bearer synthetic-test-secret', now=self.now)
        audit = AuditEvent.objects.get(event_type='job.admin_timeout')
        self.assertNotIn('synthetic-test-secret', audit.description)
        self.assertNotIn('synthetic-test-secret', json.dumps(audit.metadata))

    def test_command_apply_running_requires_reason(self):
        job = self.job(status=AgentJob.STATUS_RUNNING)
        with self.assertRaisesMessage(CommandError, 'reason_required'):
            call_command('reconcile_stale_agent_job', '--job', str(job.pk), '--apply')
        self.assertEqual(AgentJob.objects.get(pk=job.pk).status, AgentJob.STATUS_RUNNING)
        out = StringIO()
        call_command('reconcile_stale_agent_job', '--job', str(job.pk), '--apply',
                     '--reason', 'Synthetic running reconciliation', stdout=out)
        body = json.loads(out.getvalue())
        self.assertTrue(body['applied'])
        self.assertEqual(body['status'], AgentJob.STATUS_TIMED_OUT)
        self.assertEqual(AgentJob.objects.get(pk=job.pk).error_code, 'JOB_RUNNING_TIMEOUT')

    def test_dry_run_does_not_mutate_and_apply_is_idempotent(self):
        job = self.job()
        out = StringIO()
        call_command('reconcile_stale_agent_job', '--job', str(job.pk), stdout=out)
        self.assertTrue(json.loads(out.getvalue())['eligible_for_timeout'])
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_SENT)
        self.assertEqual(AuditEvent.objects.filter(event_type='job.admin_timeout').count(), 0)

        result = apply_stale_sent_job_timeout(job.pk, reason='Phase 6 synthetic test', now=self.now)
        self.assertTrue(result['applied'])
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_TIMED_OUT)
        self.assertEqual(job.finished_at, self.now)
        self.assertEqual(job.error_code, 'JOB_TIMEOUT')
        self.assertEqual(job.dispatched_at, self.now - timedelta(hours=1))
        self.assertEqual(job.result, {})
        self.assertEqual(job.result_receipts.count(), 0)
        self.assertEqual(AuditEvent.objects.filter(event_type='job.admin_timeout').count(), 1)
        second = apply_stale_sent_job_timeout(job.pk, reason='Phase 6 synthetic replay', now=self.now)
        self.assertFalse(second.get('applied', False))
        self.assertEqual(AuditEvent.objects.filter(event_type='job.admin_timeout').count(), 1)

    def test_new_result_or_status_between_dry_run_and_apply_blocks(self):
        for change in ('result', 'status', 'receipt'):
            with self.subTest(change=change):
                job = self.job()
                self.assertTrue(evaluate_stale_sent_job_timeout(job, now=self.now)['eligible_for_timeout'])
                if change == 'result':
                    job.result = {'status': 'completed'}
                    job.save(update_fields=['result'])
                elif change == 'status':
                    job.status = AgentJob.STATUS_COMPLETED
                    job.save(update_fields=['status'])
                else:
                    AgentJobResultReceipt.objects.create(
                        result_id=str(uuid.uuid4()), job=job, endpoint=self.machine,
                        payload_sha256='0' * 64,
                    )
                result = apply_stale_sent_job_timeout(job.pk, reason='Phase 6 synthetic test', now=self.now)
                self.assertFalse(result.get('applied', False))
                job.refresh_from_db()
                self.assertNotEqual(job.status, AgentJob.STATUS_TIMED_OUT)
        self.assertEqual(AuditEvent.objects.filter(event_type='job.admin_timeout').count(), 0)

    def test_late_completed_result_does_not_reopen_timed_out_job(self):
        job = self.job()
        apply_stale_sent_job_timeout(job.pk, reason='Phase 6 synthetic test', now=self.now)
        result_id = str(uuid.uuid4())
        response = self.client.post(
            '/api/agent/jobs/result/',
            data={
                'job_id': str(job.pk), 'status': 'completed',
                'result': {'installed_version': '0.1.1.0-rc39', 'health_check': {'confirmed': True}},
            },
            content_type='application/json', HTTP_IDEMPOTENCY_KEY=result_id,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['reason'], 'job_already_final')
        job.refresh_from_db()
        self.machine.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_TIMED_OUT)
        self.assertEqual(job.result, {})
        self.assertEqual(self.machine.agent_version, '0.1.1.0-rc17')
        self.assertEqual(AgentJobResultReceipt.objects.filter(job=job, result_id=result_id).count(), 1)

    def test_late_completion_reusing_progress_result_id_cannot_reopen(self):
        job = self.job(status=AgentJob.STATUS_RUNNING)
        result_id = str(uuid.uuid4())
        job.result_id = result_id
        job.result = {'update_status': 'runner_started'}
        job.save(update_fields=['result_id', 'result'])
        receipt = AgentJobResultReceipt.objects.create(
            result_id=result_id, job=job, endpoint=self.machine, payload_sha256='0' * 64,
            first_payload={'job_id': str(job.pk), 'status': 'running',
                           'result': {'update_status': 'runner_started'}},
        )
        apply_stale_sent_job_timeout(job.pk, reason='Synthetic stale runner', now=self.now)
        response = self.client.post(
            '/api/agent/jobs/result/',
            data={'job_id': str(job.pk), 'status': 'completed',
                  'result': {'installed_version': '0.1.1.0-rc39', 'health_check': {'confirmed': True}}},
            content_type='application/json', HTTP_IDEMPOTENCY_KEY=result_id,
        )
        self.assertEqual(response.status_code, 409)
        receipt.refresh_from_db()
        job.refresh_from_db()
        self.machine.refresh_from_db()
        self.assertEqual(receipt.conflict_count, 1)
        self.assertEqual(job.status, AgentJob.STATUS_TIMED_OUT)
        self.assertEqual(self.machine.agent_version, '0.1.1.0-rc17')
        self.assertEqual(AuditEvent.objects.filter(event_type='job.result_conflict').count(), 1)
