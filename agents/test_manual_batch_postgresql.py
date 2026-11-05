"""Concurrency gate: requires a disposable PostgreSQL test database."""
import json
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import mock

from django.db import close_old_connections, connection, transaction
from django.test import Client, RequestFactory, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from agents.models import AgentDeploymentBatch, AgentDeploymentToken
from agents import tests as fixture_tests
from dashboard.views import deployment_batch_issue


@unittest.skipUnless(connection.vendor == 'postgresql', 'Requires isolated PostgreSQL')
@override_settings(NIGHTOWL_PUBLIC_URL='https://nightowl.test')
class ManualBatchPostgresTests(TransactionTestCase):
    def setUp(self):
        fixture_tests.AgentDeploymentTokenTests.setUp(self)
        self.batch = AgentDeploymentBatch.objects.create(name='Lab', release=self.release, created_by=self.user,
            allowed_domain='control.local', expires_at=timezone.now() + timedelta(minutes=10), max_uses=2)

    def issue(self, request_id, barrier=None):
        close_old_connections()
        try:
            request = RequestFactory().post('/', {'request_id': str(request_id)})
            request.user = self.user
            if barrier:
                barrier.wait(timeout=10)
            response = deployment_batch_issue(request, self.batch.pk)
            return response.status_code, json.loads(response.content)
        finally:
            close_old_connections()

    def test_two_simultaneous_emissions_and_quota(self):
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.issue(uuid.uuid4(), barrier), range(2)))
        self.assertEqual(sorted(x[0] for x in results), [201, 201])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.used_count, 2)
        self.assertEqual(self.issue(uuid.uuid4())[0], 409)
        self.assertEqual(AgentDeploymentToken.objects.filter(metadata__batch_id=str(self.batch.pk)).count(), 2)

    def test_same_request_and_last_slot_are_atomic(self):
        self.batch.max_uses = 1
        self.batch.save(update_fields=['max_uses'])
        barrier, key = threading.Barrier(2), uuid.uuid4()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.issue(key, barrier), range(2)))
        self.assertEqual(sorted(x[0] for x in results), [201, 409])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.used_count, 1)

    def test_different_requests_cannot_exceed_the_last_slot(self):
        self.batch.max_uses = 1
        self.batch.save(update_fields=['max_uses'])
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.issue(uuid.uuid4(), barrier), range(2)))
        self.assertEqual(sorted(x[0] for x in results), [201, 409])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.used_count, 1)
        self.assertEqual(AgentDeploymentToken.objects.filter(metadata__batch_id=str(self.batch.pk)).count(), 1)

    def test_same_individual_token_consumed_once(self):
        code, body = self.issue(uuid.uuid4())
        self.assertEqual(code, 201)
        token = body['deployment']['command'].split("$env:NIGHTOWL_DEPLOYMENT_TOKEN='", 1)[1].split("'", 1)[0]
        payload = {'enrollment_token': token, 'machine_id': 'concurrent-machine', 'hostname': 'TEST-PC',
                   'domain': 'control.local', 'agent_version': self.release.version}
        barrier = threading.Barrier(2)
        def enroll(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return Client().post(reverse('agent-enroll'), data=json.dumps(payload), content_type='application/json').status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(enroll, range(2)))
        self.assertEqual(statuses.count(200), 1)
        self.assertIn(next(value for value in statuses if value != 200), (403, 409))

    def test_expiry_and_revocation_while_waiting_for_lock(self):
        for field, value in [('is_active', False), ('expires_at', timezone.now() - timedelta(seconds=1))]:
            self.batch.is_active = True
            self.batch.expires_at = timezone.now() + timedelta(minutes=10)
            self.batch.save(update_fields=['is_active', 'expires_at'])
            started = threading.Event()
            def delayed():
                started.set()
                return self.issue(uuid.uuid4())
            with ThreadPoolExecutor(max_workers=1) as pool:
                with transaction.atomic():
                    locked = AgentDeploymentBatch.objects.select_for_update().get(pk=self.batch.pk)
                    future = pool.submit(delayed)
                    self.assertTrue(started.wait(5))
                    setattr(locked, field, value)
                    locked.save(update_fields=[field])
                self.assertEqual(future.result(timeout=10)[0], 409)
        self.assertEqual(AgentDeploymentToken.objects.filter(metadata__batch_id=str(self.batch.pk)).count(), 0)

    def test_intermediate_failure_rolls_back_quota_and_recovers(self):
        request = RequestFactory().post('/', {'request_id': str(uuid.uuid4())})
        request.user = self.user
        with mock.patch('dashboard.views.create_audit_event', side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):
                deployment_batch_issue(request, self.batch.pk)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.used_count, 0)
        self.assertEqual(self.issue(uuid.uuid4())[0], 201)
