import json
import uuid
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
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

    def test_non_sent_or_fresh_jobs_are_blocked(self):
        for status in (
            AgentJob.STATUS_QUEUED, AgentJob.STATUS_RUNNING, AgentJob.STATUS_COMPLETED,
            AgentJob.STATUS_FAILED, AgentJob.STATUS_TIMED_OUT,
        ):
            with self.subTest(status=status):
                decision = evaluate_stale_sent_job_timeout(self.job(status=status), now=self.now)
                self.assertFalse(decision['eligible_for_timeout'])
        fresh = evaluate_stale_sent_job_timeout(self.job(age=timedelta(minutes=1)), now=self.now)
        self.assertFalse(fresh['eligible_for_timeout'])

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
                    job.status = AgentJob.STATUS_RUNNING
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
