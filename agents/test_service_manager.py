import json
import os
import subprocess
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, LiveServerTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .internal_jobs import create_internal_job, validate_internal_completion, validate_internal_payload
from .models import AgentJob, AgentMachine, AuditEvent, InventorySnapshot


def service_page(*, names=('EventLog', 'Spooler'), total=2, has_more=False, snapshot='0123456789abcdef0123456789abcdef', collected_at=None):
    collected_at = collected_at or timezone.now().isoformat()
    rows = [{'name': name, 'display_name': name + ' Display', 'status': 'Running', 'startup_type': 'Automatic'}
            for name in names]
    return {'schema_version': 2, 'read_only': True, 'snapshot_id': snapshot, 'collected_at': collected_at,
            'total_services': total, 'services': rows,
            'next_after_name': rows[-1]['name'] if has_more and rows else None, 'has_more': has_more}


@override_settings(NIGHTOWL_SERVICE_COLLECTION_ENABLED=True)
class ServiceManagerApiTests(TestCase):
    def setUp(self):
        self.machine, self.token = AgentMachine.create_with_token(hostname='SYNTHETIC-SERVICES', machine_id=str(uuid.uuid4()),
            agent_version='synthetic-v2', last_seen_at=timezone.now(), status=AgentMachine.STATUS_ONLINE)
        self.operator = get_user_model().objects.create_superuser('synthetic-service-operator', password='synthetic-only')
        self.client.force_login(self.operator)
        self.capability = 'collect_services_paged:2'
        self.heartbeat()
        self.create_url = reverse('service-manager-create', args=[self.machine.pk])
        self.availability_url = reverse('service-manager-availability', args=[self.machine.pk])

    def heartbeat(self, capabilities=None):
        return InventorySnapshot.objects.create(machine=self.machine, hostname=self.machine.hostname,
            collected_at=timezone.now(), raw_payload={'snapshot_source': 'heartbeat', 'machine_id': self.machine.machine_id,
                'agent_version': self.machine.agent_version,
                'agent': {'capabilities': capabilities or [self.capability]}})

    def begin(self, request_id=None, cursor=None):
        return self.client.post(self.create_url, json.dumps({'request_id': request_id or str(uuid.uuid4()), 'cursor': cursor}),
                                content_type='application/json')

    def test_only_authorized_recent_capable_online_unique_endpoint_is_available(self):
        self.assertTrue(self.client.get(self.availability_url).json()['available'])
        self.machine.is_active = False
        self.machine.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(self.availability_url).json()['reason_code'], 'endpoint_inactive')
        self.machine.is_active = True
        self.machine.save(update_fields=['is_active'])
        InventorySnapshot.objects.filter(machine=self.machine).update(received_at=timezone.now() - timedelta(minutes=16))
        self.assertFalse(self.client.get(self.availability_url).json()['available'])
        InventorySnapshot.objects.filter(machine=self.machine).delete()
        self.heartbeat(['collect_services'])
        self.assertEqual(self.client.get(self.availability_url).json()['reason_code'], 'capability_unconfirmed')
        self.heartbeat()
        self.machine.status = AgentMachine.STATUS_OFFLINE
        self.machine.save(update_fields=['status'])
        self.assertEqual(self.begin().status_code, 409)

    def test_disabled_flag_old_agent_identity_ambiguity_and_common_user_are_blocked(self):
        with override_settings(NIGHTOWL_SERVICE_COLLECTION_ENABLED=False):
            self.assertEqual(self.client.get(self.availability_url).json()['reason_code'], 'collection_disabled')
            self.assertEqual(self.begin().status_code, 409)
        self.heartbeat(['collect_services'])
        self.assertEqual(self.client.get(self.availability_url).json()['available'], False)
        self.heartbeat()
        duplicate, _ = AgentMachine.create_with_token(hostname='SYNTHETIC-DUPLICATE', machine_id=self.machine.machine_id,
            agent_version='synthetic-v2', last_seen_at=timezone.now())
        self.assertEqual(self.client.get(self.availability_url).json()['reason_code'], 'identity_unavailable')
        duplicate.delete()
        common = get_user_model().objects.create_user('ordinary-user', password='synthetic-only')
        common.user_permissions.add(*Permission.objects.filter(content_type__app_label='agents',
            codename__in=['add_agentjob', 'view_agentmachine', 'view_agentjob']))
        self.client.force_login(common)
        self.assertEqual(self.client.get(self.availability_url).status_code, 403)
        self.assertEqual(self.begin().status_code, 403)
        self.assertFalse(AgentJob.objects.exists())

    def test_versioned_payload_is_bounded_and_rejects_arbitrary_fields(self):
        payload = {'schema_version': 2, 'page_size': 20, 'after_name': '', 'expected_snapshot_id': '',
            'page_index': 1, 'rows_collected': 0, 'expected_total_services': 0,
            'expected_collected_at': '', 'endpoint_machine_id': self.machine.machine_id}
        self.assertEqual(validate_internal_payload('collect_services', payload), payload)
        invalid = [
            {**payload, 'page_size': 100}, {**payload, 'command': 'whoami'},
            {**payload, 'endpoint_machine_id': 'other-machine'}, {**payload, 'rows_collected': 20},
            {**payload, 'after_name': 'EventLog'}, {**payload, 'schema_version': True},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_internal_payload('collect_services', value)
        last_page = {**payload, 'after_name': 'Service779', 'expected_snapshot_id': '0123456789abcdef0123456789abcdef',
            'page_index': 40, 'rows_collected': 780, 'expected_total_services': 800,
            'expected_collected_at': timezone.now().isoformat()}
        self.assertEqual(validate_internal_payload('collect_services', last_page), last_page)
        with self.assertRaises(ValueError):
            validate_internal_payload('collect_services', {**last_page, 'page_index': 41, 'rows_collected': 800})

    def test_read_only_paged_job_auditing_completion_and_signed_cursor(self):
        first = self.begin()
        self.assertEqual(first.status_code, 202)
        job = AgentJob.objects.get(pk=first.json()['job_id'])
        self.assertEqual(job.job_type, 'collect_services')
        self.assertEqual(job.payload['schema_version'], 2)
        self.assertEqual(job.payload['page_size'], 20)
        self.assertEqual(job.payload['endpoint_machine_id'], self.machine.machine_id)
        self.assertEqual(AuditEvent.objects.filter(event_type='job.created', endpoint=self.machine).count(), 1)
        self.assertFalse(AgentJob.objects.filter(job_type='operational_action').exists())

        collected_at = timezone.now().isoformat()
        first_names = tuple(f'Service{i:02}' for i in range(20))
        output = service_page(names=first_names, total=21, has_more=True, collected_at=collected_at)
        result = {'machine_id': self.machine.machine_id, 'output': output}
        validate_internal_completion(job, result)
        job.status = AgentJob.STATUS_COMPLETED
        job.result = result
        job.save(update_fields=['status', 'result'])
        status_url = reverse('service-manager-job', args=[self.machine.pk, job.pk])
        status = self.client.get(status_url)
        self.assertEqual(status.status_code, 200)
        cursor = status.json()['page']['cursor']
        self.assertTrue(cursor)

        request_id = str(uuid.uuid4())
        second = self.begin(request_id=request_id, cursor=cursor)
        self.assertEqual(second.status_code, 202)
        next_job = AgentJob.objects.get(pk=second.json()['job_id'])
        self.assertEqual(next_job.payload['page_index'], 2)
        self.assertEqual(next_job.payload['rows_collected'], 20)
        self.assertEqual(next_job.payload['expected_total_services'], 21)
        replay = self.begin(request_id=request_id, cursor=cursor)
        self.assertEqual(replay.status_code, 202)
        self.assertEqual(replay.json()['job_id'], second.json()['job_id'])
        self.assertEqual(AgentJob.objects.filter(job_type='collect_services').count(), 2)
        tampered = cursor[:-1] + ('A' if cursor[-1] != 'A' else 'B')
        self.assertEqual(self.begin(cursor=tampered).status_code, 400)

    def test_invalid_truncated_or_sensitive_service_results_are_rejected(self):
        job = create_internal_job(self.operator, self.machine.pk, 'collect_services', {
            'schema_version': 2, 'page_size': 20, 'after_name': '', 'expected_snapshot_id': '',
            'page_index': 1, 'rows_collected': 0, 'expected_total_services': 0,
            'expected_collected_at': '', 'endpoint_machine_id': self.machine.machine_id,
        })
        valid = service_page(names=('EventLog',), total=1)
        validate_internal_completion(job, {'machine_id': self.machine.machine_id, 'output': valid})
        for bad in (
            {**valid, 'services': valid['services'] + valid['services'] * 41},
            {**valid, 'services': [{**valid['services'][0], 'account': 'secret'}]},
            {**valid, 'output_truncated': True},
            {**valid, 'total_services': 2},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_internal_completion(job, {'machine_id': self.machine.machine_id, 'output': bad})
        with self.assertRaises(ValueError):
            validate_internal_completion(job, {'machine_id': self.machine.machine_id, 'output': valid, 'output_truncated': True})
        with self.assertRaises(ValueError):
            validate_internal_completion(job, {'machine_id': 'different-machine', 'output': valid})

    def test_missing_service_configuration_is_unknown_not_absent(self):
        job = create_internal_job(self.operator, self.machine.pk, 'collect_services', {
            'schema_version': 2, 'page_size': 20, 'after_name': '', 'expected_snapshot_id': '',
            'page_index': 1, 'rows_collected': 0, 'expected_total_services': 0,
            'expected_collected_at': '', 'endpoint_machine_id': self.machine.machine_id,
        })
        output = service_page(names=('UnknownService',), total=1)
        output['services'][0]['status'] = 'unknown'
        output['services'][0]['startup_type'] = 'unknown'
        validate_internal_completion(job, {'machine_id': self.machine.machine_id, 'output': output})
        output['snapshot_id'] = 'not-a-hex-snapshot-id-00000000000'
        with self.assertRaises(ValueError):
            validate_internal_completion(job, {'machine_id': self.machine.machine_id, 'output': output})

    def test_csrf_is_required_and_no_write_operation_is_exposed(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.operator)
        response = csrf.post(self.create_url, json.dumps({'request_id': str(uuid.uuid4()), 'cursor': None}),
                             content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AgentJob.objects.exists())
        self.assertNotIn('start_service', response.content.decode())
        self.assertNotIn('stop_service', response.content.decode())


@override_settings(NIGHTOWL_SERVICE_COLLECTION_ENABLED=True, NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED=True)
class ServiceManagerBrowserTests(LiveServerTestCase):
    @skipUnless(os.environ.get('NIGHTOWL_TEST_BROWSER_E2E') == '1', 'Explicit isolated browser gate required')
    def test_real_endpoint_service_manager_ui(self):
        endpoint, _ = AgentMachine.create_with_token(hostname='SYNTHETIC-SERVICES-E2E',
            machine_id=str(uuid.uuid4()), agent_version='synthetic-v2', last_seen_at=timezone.now(),
            status=AgentMachine.STATUS_ONLINE)
        InventorySnapshot.objects.create(machine=endpoint, hostname=endpoint.hostname, collected_at=timezone.now(),
            raw_payload={'snapshot_source': 'heartbeat', 'machine_id': endpoint.machine_id,
                'agent_version': endpoint.agent_version,
                'agent': {'capabilities': ['operational_action:query_service:1', 'collect_services_paged:2']}})
        user = get_user_model().objects.create_superuser('synthetic-service-ui', password='synthetic-only')
        self.client.force_login(user)
        result = subprocess.run(['node', str(Path(settings.BASE_DIR) / 'tests/frontend/test_endpoint_service_manager_e2e.cjs')],
            env={**os.environ, 'NIGHTOWL_E2E_SERVER': self.live_server_url,
                 'NIGHTOWL_E2E_SESSION': self.client.cookies['sessionid'].value,
                 'NIGHTOWL_E2E_ENDPOINT': str(endpoint.pk),
                 'NIGHTOWL_E2E_ENDPOINT_NAME': endpoint.hostname}, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(AgentJob.objects.exists(), 'Browser UI fixture must not create remote jobs.')
        print(result.stdout.strip())
