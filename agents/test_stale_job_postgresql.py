"""Real PostgreSQL row-lock races; no production data or mocked locks."""

import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

from django.db import connection, connections, transaction
from django.test import Client, TransactionTestCase

from .models import AgentJob, AgentJobResultReceipt, AuditEvent
from .stale_job_reconciliation import apply_stale_sent_job_timeout
from . import test_stale_job_reconciliation as fixtures


@unittest.skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks')
class PostgreSQLStaleRunningConcurrencyTests(TransactionTestCase):
    setUp = fixtures.StaleSentJobReconciliationTests.setUp
    job = fixtures.StaleSentJobReconciliationTests.job

    def complete(self, job_id, result_id):
        response = Client(**self.client.defaults).post(
            '/api/agent/jobs/result/',
            data={'job_id': str(job_id), 'status': 'completed', 'exit_code': 0,
                  'result': {'installed_version': '0.1.1.0-rc39',
                             'health_check': {'confirmed': True}}},
            content_type='application/json', HTTP_IDEMPOTENCY_KEY=result_id,
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def run_race(self, holder, waiter):
        locked = threading.Event()
        release = threading.Event()
        waiter_ready = threading.Event()
        pids = {}
        lock_queries = set()

        def operation_with_sql_evidence(name, operation):
            def record(execute, sql, params, many, context):
                if 'FOR UPDATE' in sql.upper():
                    lock_queries.add(name)
                return execute(sql, params, many, context)
            with connection.execute_wrapper(record):
                return operation()

        def run(name, operation):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid(), current_database()')
                    pid, database = cursor.fetchone()
                self.assertEqual(database, connection.settings_dict['NAME'])
                pids[name] = pid
                if name == 'holder':
                    with transaction.atomic():
                        result = operation_with_sql_evidence(name, operation)
                        locked.set()
                        self.assertTrue(release.wait(15), 'Coordinator did not release row lock')
                    return result
                self.assertTrue(locked.wait(15), 'Holder did not obtain row lock')
                waiter_ready.set()
                return operation_with_sql_evidence(name, operation)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run, 'holder', holder)
            second = pool.submit(run, 'waiter', waiter)
            try:
                self.assertTrue(locked.wait(15))
                self.assertTrue(waiter_ready.wait(15))
                self.assertNotEqual(pids['holder'], pids['waiter'])
                deadline = time.monotonic() + 10
                observed_lock = False
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            'SELECT wait_event_type, pg_blocking_pids(pid), query '
                            'FROM pg_stat_activity WHERE pid = %s',
                            [pids['waiter']],
                        )
                        row = cursor.fetchone()
                    if row and row[0] == 'Lock':
                        self.assertIn(pids['holder'], row[1])
                        self.assertIn('AGENTS_AGENTJOB', row[2].upper())
                        observed_lock = True
                        break
                    if second.done():
                        second.result()
                        break
                    threading.Event().wait(0.02)
                self.assertTrue(observed_lock, 'Waiter never blocked on a real PostgreSQL lock')
                self.assertFalse(second.done())
            finally:
                release.set()
            results = first.result(timeout=15), second.result(timeout=15)
            self.assertEqual(lock_queries, {'holder', 'waiter'})
            return results

    def timeout(self, job_id):
        return apply_stale_sent_job_timeout(job_id, reason='Synthetic PostgreSQL race', now=self.now)

    def test_completion_wins_and_timeout_rechecks_after_lock(self):
        job = self.job(status=AgentJob.STATUS_RUNNING)
        self.run_race(lambda: self.complete(job.pk, str(uuid.uuid4())), lambda: self.timeout(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_COMPLETED)
        self.assertEqual(AuditEvent.objects.filter(event_type='job.admin_timeout').count(), 0)
        self.assertEqual(AgentJobResultReceipt.objects.filter(job=job).count(), 1)

    def test_timeout_wins_and_late_http_result_cannot_reopen(self):
        job = self.job(status=AgentJob.STATUS_RUNNING)
        result_id = str(uuid.uuid4())
        applied, response = self.run_race(
            lambda: self.timeout(job.pk), lambda: self.complete(job.pk, result_id),
        )
        self.assertTrue(applied['applied'])
        self.assertEqual(response['reason'], 'job_already_final')
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_TIMED_OUT)
        self.assertEqual(job.error_code, 'JOB_RUNNING_TIMEOUT')
        self.assertEqual(AuditEvent.objects.filter(event_type='job.admin_timeout').count(), 1)
        self.assertEqual(AgentJobResultReceipt.objects.filter(job=job, result_id=result_id).count(), 1)

    def test_two_timeout_reconcilers_apply_and_audit_once(self):
        job = self.job(status=AgentJob.STATUS_RUNNING)
        results = self.run_race(lambda: self.timeout(job.pk), lambda: self.timeout(job.pk))
        self.assertCountEqual([result.get('applied', False) for result in results], [True, False])
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_TIMED_OUT)
        self.assertEqual(AuditEvent.objects.filter(event_type='job.admin_timeout').count(), 1)
