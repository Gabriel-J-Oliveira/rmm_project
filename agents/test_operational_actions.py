import copy
import json
import os
import subprocess
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, LiveServerTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AgentMachine, AgentJob, AgentJobResultReceipt, AuditEvent, InventorySnapshot
from .operational_actions import create_action_job, validate_payload


def payload(action='query_service'):
    return {'action_id': action, 'action_version': 1,
            'parameters': {'service_name': 'EventLog'} if action == 'query_service' else {}}


def heartbeat(machine):
    return InventorySnapshot.objects.create(machine=machine, hostname=machine.hostname, collected_at=timezone.now(),
        raw_payload={'snapshot_source': 'heartbeat', 'machine_id': machine.machine_id, 'agent_version': machine.agent_version,
            'agent': {'capabilities': ['jobs_protocol_v2', 'operational_action:query_service:1', 'operational_action:flush_dns:1']}})


@override_settings(NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED=True)
class OperationalActionTests(TestCase):
    def setUp(self):
        self.machine, token = AgentMachine.create_with_token(hostname='SYNTHETIC-ACTIONS', machine_id=str(uuid.uuid4()))
        self.agent = Client(HTTP_AUTHORIZATION='Bearer ' + token)
        self.operator = get_user_model().objects.create_superuser('synthetic-actions', password='synthetic-only')
        self.client.force_login(self.operator)
        self.url = reverse('operational-action-create', args=[self.machine.pk])
        self.snapshot = heartbeat(self.machine)

    def create(self, value=None):
        return self.client.post(self.url, payload() if value is None else value, content_type='application/json')

    def pull(self, v2=True):
        return self.agent.get(reverse('agent-jobs-pull'), **({'HTTP_X_NIGHTOWL_JOB_PROTOCOL': '2'} if v2 else {}))

    def result(self, job, output=None):
        output = output or {'schema_version': 1, 'action_id': 'query_service', 'action_version': 1,
            'success': True, 'service_name': 'EventLog', 'state': 'Running'}
        return {'job_id': str(job.pk), 'machine_id': self.machine.machine_id, 'status': 'completed',
            'result': {'machine_id': self.machine.machine_id, 'output': output}}

    def test_strict_catalog_parameters_and_versions(self):
        invalid = [None, [], {}, {**payload(), 'command': 'whoami'}, payload('unknown'),
            {**payload(), 'action_version': 2}, {**payload(), 'action_version': True},
            {**payload(), 'action_version': '1'}, {**payload(), 'parameters': {}},
            {**payload('flush_dns'), 'parameters': {'command': 'whoami'}}]
        for name in ('', 'x' * 257, 'Spooler;whoami', '../secret', 'EventLog\n', 'a b', 'café'):
            invalid.append({**payload(), 'parameters': {'service_name': name}})
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_payload(value)
        self.assertEqual(validate_payload(payload()), payload())
        self.assertEqual(validate_payload(payload('flush_dns')), payload('flush_dns'))

    def test_creation_is_authenticated_and_csrf_protected(self):
        self.assertEqual(Client().post(self.url, payload(), content_type='application/json').status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.operator)
        self.assertEqual(csrf.post(self.url, payload(), content_type='application/json').status_code, 403)
        self.assertEqual(self.agent.post(self.url, payload(), content_type='application/json').status_code, 403)
        self.assertFalse(AgentJob.objects.exists())

    def test_staff_alone_and_read_permission_cannot_flush_dns(self):
        user = get_user_model().objects.create_user('reader', is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.create().status_code, 403)
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='agents', codename__in=['add_agentjob', 'view_agentmachine']))
        self.assertEqual(self.create(payload('flush_dns')).status_code, 403)
        self.assertEqual(self.create().status_code, 201)

    def test_explicit_mutation_permission_allows_flush(self):
        user = get_user_model().objects.create_user('administrator', is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='agents', codename__in=['add_agentjob', 'change_agentmachine']))
        self.client.force_login(user)
        self.assertEqual(self.create(payload('flush_dns')).status_code, 201)

    def test_default_disabled_missing_stale_or_wrong_version_capability(self):
        with override_settings(NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED=False):
            self.assertEqual(self.create().status_code, 400)
        InventorySnapshot.objects.filter(pk=self.snapshot.pk).update(received_at=timezone.now() - timedelta(minutes=16))
        self.assertEqual(self.create().status_code, 400)
        self.snapshot.delete()
        self.snapshot = heartbeat(self.machine)
        raw = self.snapshot.raw_payload
        raw['agent']['capabilities'] = ['operational_action:query_service:2']
        self.snapshot.raw_payload = raw
        self.snapshot.save()
        self.assertEqual(self.create().status_code, 400)

    def test_creation_delivery_start_result_ack_and_replay(self):
        self.assertEqual(self.create().status_code, 201)
        job = AgentJob.objects.get()
        self.assertEqual(job.timeout_seconds, 15)
        self.assertEqual(self.create().status_code, 400)
        delivered = self.pull().json()['jobs'][0]
        self.assertEqual(delivered['payload'], payload())
        self.assertEqual(delivered['type'], 'operational_action')
        start = reverse('agent-jobs-started', args=[job.pk])
        self.assertEqual(self.agent.post(start, {'machine_id': self.machine.machine_id}, content_type='application/json').status_code, 200)
        body = self.result(job)
        for _ in range(2):
            response = self.agent.post(reverse('agent-jobs-result'), body, content_type='application/json', HTTP_IDEMPOTENCY_KEY='synthetic-action-result')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['result_id'], 'synthetic-action-result')
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
        self.assertEqual(job.result['output']['state'], 'Running')
        self.assertEqual(AgentJobResultReceipt.objects.count(), 1)
        self.assertTrue(AuditEvent.objects.filter(event_type='job.created').exists())

    def test_revocation_checked_at_delivery_and_start(self):
        job = create_action_job(self.operator, self.machine.pk, payload())
        self.operator.is_active = False
        self.operator.save()
        self.assertEqual(self.pull().json()['jobs'], [])
        job.refresh_from_db()
        self.assertEqual(job.status, 'cancelled')
        self.operator.is_active = True
        self.operator.save()
        job = create_action_job(self.operator, self.machine.pk, payload())
        self.pull()
        with override_settings(NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED=False):
            response = self.agent.post(reverse('agent-jobs-started', args=[job.pk]), {'machine_id': self.machine.machine_id}, content_type='application/json')
        self.assertEqual(response.status_code, 409)

    def test_manual_unregistered_or_unbounded_jobs_never_delivered(self):
        for field, value in [('payload', payload('unknown')), ('timeout_seconds', 999), ('created_by', 'missing')]:
            job = AgentJob(endpoint=self.machine, job_type='operational_action', payload=payload(), created_by=self.operator.username, timeout_seconds=15)
            setattr(job, field, value)
            job.save()
            self.assertEqual(self.pull().json()['jobs'], [])
            job.refresh_from_db()
            self.assertEqual(job.status, 'cancelled')

    def test_legacy_delivery_and_frontend_bypass_rejected(self):
        create_action_job(self.operator, self.machine.pk, payload())
        self.assertEqual(self.pull(v2=False).json()['jobs'], [])
        url = reverse('api-endpoint-job-create', args=[self.machine.pk])
        self.assertEqual(self.client.post(url, {'job_type': 'operational_action'}).status_code, 400)

    def test_completed_result_must_match_registered_contract(self):
        job = create_action_job(self.operator, self.machine.pk, payload())
        self.pull()
        good = self.result(job)
        for field, value in [('service_name', 'other'), ('state', 'secret path'), ('action_version', True), ('success', False), ('command', 'whoami')]:
            body = copy.deepcopy(good)
            body['result']['output'][field] = value
            self.assertEqual(self.agent.post(reverse('agent-jobs-result'), body, content_type='application/json', HTTP_IDEMPOTENCY_KEY='invalid-action').status_code, 400)
        self.assertFalse(AgentJobResultReceipt.objects.exists())

    def test_timeout_uses_existing_result_receipt(self):
        job = create_action_job(self.operator, self.machine.pk, payload())
        self.pull()
        body = {'job_id': str(job.pk), 'machine_id': self.machine.machine_id, 'status': 'timed_out', 'error_code': 'JOB_TIMEOUT'}
        self.assertEqual(self.agent.post(reverse('agent-jobs-result'), body, content_type='application/json', HTTP_IDEMPOTENCY_KEY='timeout-action').status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, 'timed_out')

    def test_permission_revoked_after_delivery_cannot_start(self):
        user = get_user_model().objects.create_user('revoked-reader', is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='agents', codename__in=['add_agentjob', 'view_agentmachine']))
        job = create_action_job(user, self.machine.pk, payload())
        self.pull()
        user.user_permissions.clear()
        response = self.agent.post(reverse('agent-jobs-started', args=[job.pk]),
            {'machine_id': self.machine.machine_id}, content_type='application/json')
        self.assertEqual(response.status_code, 409)
        job.refresh_from_db()
        self.assertIsNone(job.started_at)

    def test_flush_completion_uses_same_result_ack_contract(self):
        job = create_action_job(self.operator, self.machine.pk, payload('flush_dns'))
        self.pull()
        body = self.result(job, {'schema_version': 1, 'action_id': 'flush_dns', 'action_version': 1, 'success': True})
        response = self.agent.post(reverse('agent-jobs-result'), body, content_type='application/json', HTTP_IDEMPOTENCY_KEY='synthetic-flush-result')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['result_id'], 'synthetic-flush-result')
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
        self.assertEqual(AgentJobResultReceipt.objects.filter(job=job).count(), 1)


@override_settings(NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED=True)
class OperationalActionE2ETests(LiveServerTestCase):
    def test_native_query_through_dotnet_runtime_result_and_ack(self):
        dll = Path(settings.BASE_DIR) / 'NightOwl.Agent.Windows.Tests/bin/Release/net8.0-windows/NightOwl.Agent.Windows.Tests.dll'
        self.assertTrue(dll.exists(), 'Build affected .NET projects in Release first.')
        machine, token = AgentMachine.create_with_token(hostname='SYNTHETIC-ACTIONS-E2E', machine_id=str(uuid.uuid4()))
        operator = get_user_model().objects.create_superuser('synthetic-action-e2e', password='synthetic-only')
        heartbeat(machine)
        job = create_action_job(operator, machine.pk, payload())
        result = subprocess.run(['dotnet', str(dll), '--operational-actions-e2e'], capture_output=True, text=True, timeout=100,
            env={**os.environ, 'NIGHTOWL_TEST_SERVER': self.live_server_url, 'NIGHTOWL_TEST_TOKEN': token,
                 'NIGHTOWL_TEST_MACHINE': machine.machine_id, 'NIGHTOWL_TEST_JOB_COUNT': '1'})
        self.assertEqual(result.returncode, 0, 'Local action E2E failed; process output withheld for credential safety.')
        evidence = json.loads(next(line.split('=', 1)[1] for line in result.stdout.splitlines() if line.startswith('JOB_PROTOCOL_TIMINGS=')))
        self.assertIn('result_ack', evidence['jobs'][str(job.pk)])
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
        self.assertEqual(job.result['output']['service_name'], 'EventLog')
        self.assertEqual(AgentJobResultReceipt.objects.filter(job=job).count(), 1)
