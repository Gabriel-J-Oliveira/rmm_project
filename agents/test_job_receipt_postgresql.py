"""PostgreSQL-only receipt replay test using independent transactions and row locks."""

import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.db import connection, connections, transaction
from django.test import Client, TransactionTestCase
from django.utils import timezone

from .models import AgentJob, AgentJobResultReceipt, AgentJobResultReceiptProgression, AgentMachine, AuditEvent


@unittest.skipUnless(connection.vendor == 'postgresql', 'Requires a PostgreSQL test database')
class PostgreSQLReceiptProgressionTests(TransactionTestCase):
    def test_two_late_rollback_replays_append_once(self):
        machine = AgentMachine(machine_id='receipt-concurrency-test', hostname='RECEIPT-TEST')
        machine.set_agent_token('synthetic-receipt-concurrency-token')
        machine.save()
        auth = 'Bearer synthetic-receipt-concurrency-token'
        job = AgentJob.objects.create(
            endpoint=machine, job_type=AgentJob.TYPE_UPDATE_AGENT,
            status=AgentJob.STATUS_RUNNING, payload={'target_version': '0.1.1.0-rc42'},
        )
        update_id = str(uuid.uuid4())
        result_id = str(uuid.uuid4())
        completed_at = timezone.now() - timedelta(minutes=2)
        completed = {
            'job_id': str(job.id), 'status': 'completed', 'finished_at': completed_at.isoformat(),
            'result': {'update_id': update_id, 'target_version': '0.1.1.0-rc42',
                       'previous_version': '0.1.1.0-rc41', 'installed_version': '0.1.1.0-rc42',
                       'health_check_confirmed': True},
        }
        response = Client(HTTP_AUTHORIZATION=auth).post(
            '/api/agent/jobs/result/', data=completed, content_type='application/json',
            HTTP_IDEMPOTENCY_KEY=result_id,
        )
        self.assertEqual(response.status_code, 200)
        root_hash = AgentJobResultReceipt.objects.get(result_id=result_id).payload_sha256
        rollback = {
            'job_id': str(job.id), 'status': 'rolled_back',
            'finished_at': timezone.now().isoformat(),
            'result': {'update_id': update_id, 'target_version': '0.1.1.0-rc42',
                       'previous_version': '0.1.1.0-rc41', 'installed_version': '0.1.1.0-rc41',
                       'rollback_performed': True, 'rollback_confirmed': True},
        }
        holder_finished = threading.Event()
        release_holder = threading.Event()
        waiter_ready = threading.Event()
        pids = {}

        def submit(name):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pids[name] = cursor.fetchone()[0]
                if name == 'holder':
                    with transaction.atomic():
                        result = Client(HTTP_AUTHORIZATION=auth).post(
                            '/api/agent/jobs/result/', data=rollback, content_type='application/json',
                            HTTP_IDEMPOTENCY_KEY=result_id,
                        )
                        holder_finished.set()
                        self.assertTrue(release_holder.wait(15))
                    return result
                self.assertTrue(holder_finished.wait(15))
                waiter_ready.set()
                return Client(HTTP_AUTHORIZATION=auth).post(
                    '/api/agent/jobs/result/', data=rollback, content_type='application/json',
                    HTTP_IDEMPOTENCY_KEY=result_id,
                )
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(submit, 'holder')
            second = pool.submit(submit, 'waiter')
            try:
                self.assertTrue(holder_finished.wait(15))
                self.assertTrue(waiter_ready.wait(15))
                self.assertNotEqual(pids['holder'], pids['waiter'])
                deadline = time.monotonic() + 10
                blocked = False
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_blocking_pids(%s)', [pids['waiter']])
                        blocked = pids['holder'] in cursor.fetchone()[0]
                    if blocked:
                        break
                    if second.done():
                        break
                    threading.Event().wait(0.02)
                self.assertTrue(blocked, 'Second receipt did not wait for the job row lock')
            finally:
                release_holder.set()
            self.assertEqual(first.result(timeout=15).status_code, 200)
            replay = second.result(timeout=15)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()['duplicate'])
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_ROLLED_BACK)
        self.assertEqual(AgentJobResultReceipt.objects.get(result_id=result_id).payload_sha256, root_hash)
        self.assertEqual(AgentJobResultReceiptProgression.objects.filter(receipt__result_id=result_id).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(endpoint=machine,
            event_type='agent.update.late_rollback_reconciled').count(), 1)
