import copy
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .administrative_actions import PERMISSIONS, PROTECTED_SERVICES, validate_parameters
from .models import AgentMachine, AgentJob, AgentJobResultReceipt, AuditEvent, InventorySnapshot
from .operational_actions import create_action_job, validate_payload


def payload(action='start_service', params=None):
    return {'action_id': action, 'action_version': 1, 'parameters': params if params is not None else
        {'service_name': 'AcmePilotService'} if action.endswith('_service') else {}}


@override_settings(NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED=True, NIGHTOWL_ADMIN_ACTIONS_ENABLED=True)
class AdministrativeActionTests(TestCase):
    def setUp(self):
        self.machine, token = AgentMachine.create_with_token(hostname='SYNTHETIC-ADMIN', machine_id=str(uuid.uuid4()))
        self.operator = get_user_model().objects.create_superuser('synthetic-admin-actions', password='synthetic-only')
        self.client.force_login(self.operator)
        self.agent = Client(HTTP_AUTHORIZATION='Bearer ' + token)
        self.url = reverse('operational-action-create', args=[self.machine.pk])
        self.scope = {str(self.machine.pk): {**{action: ['AcmePilotService'] for action in
            ('start_service', 'stop_service', 'restart_service')}, 'gpupdate': True}}
        self.settings_override = override_settings(NIGHTOWL_ADMIN_ACTION_SCOPES=self.scope)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.heartbeat = InventorySnapshot.objects.create(machine=self.machine, hostname=self.machine.hostname, collected_at=timezone.now(),
            raw_payload={'snapshot_source': 'heartbeat', 'machine_id': self.machine.machine_id, 'agent_version': self.machine.agent_version,
                'agent': {'capabilities': ['jobs_protocol_v2'] + [f'operational_action:{action}:1' for action in PERMISSIONS]}})

    def create(self, body=None):
        return self.client.post(self.url, body or payload(), content_type='application/json')

    def pull(self):
        return self.agent.get(reverse('agent-jobs-pull'), HTTP_X_NIGHTOWL_JOB_PROTOCOL='2')

    def test_specific_permissions_not_staff_or_generic_machine_edit(self):
        user = get_user_model().objects.create_user('admin-reader', is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='agents',
            codename__in=['add_agentjob', 'change_agentmachine']))
        self.client.force_login(user)
        self.assertEqual(self.create().status_code, 403)
        user.user_permissions.add(Permission.objects.get(content_type__app_label='agents', codename='start_windows_service'))
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.create(payload('stop_service')).status_code, 403)
        self.assertEqual(self.create(payload('gpupdate')).status_code, 403)
        user.is_active = False
        user.save()
        self.assertEqual(self.create().status_code, 403)

    def test_authentication_csrf_default_flags_scope_and_capability(self):
        self.assertEqual(Client().post(self.url, payload(), content_type='application/json').status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.operator)
        self.assertEqual(csrf.post(self.url, payload(), content_type='application/json').status_code, 403)
        for overrides in ({'NIGHTOWL_ADMIN_ACTIONS_ENABLED': False}, {'NIGHTOWL_ADMIN_ACTION_SCOPES': {}},
                          {'NIGHTOWL_ADMIN_ACTION_SCOPES': []},
                          {'NIGHTOWL_ADMIN_ACTION_SCOPES': {str(self.machine.pk): {'start_service': ['*']}}}):
            with override_settings(**overrides):
                self.assertEqual(self.create().status_code, 400)
        self.heartbeat.raw_payload['agent']['capabilities'] = ['unknown_capability']
        self.heartbeat.save()
        self.assertEqual(self.create().status_code, 400)
        self.assertFalse(AgentJob.objects.exists())

    def test_protected_resources_parameters_versions_and_plaintext_rejected(self):
        for name in (*PROTECTED_SERVICES, 'NightOwlAgentDotNet', 'NightOwlAnything', '*', '../svc', 'svc;whoami'):
            self.assertEqual(self.create(payload(params={'service_name': name})).status_code, 400)
        for action, params in (
            ('gpupdate', {'command': 'whoami'}), ('gpupdate', {'target': 'user'}), ('gpupdate', {'reboot': True}),
            ('create_local_user', {'user_name': 'nw_help', 'password': 'synthetic-private-password'}),
            ('reset_local_password', {'user_name': 'nw_help', 'account_sid': 'S-1-5-21-1-2-3-1001', 'password': 'synthetic-private-password'}),
            ('set_local_user_enabled', {'user_name': 'nw_help', 'account_sid': 'S-1-5-21-1-2-3-1001', 'enabled': 1})):
            self.assertEqual(self.create(payload(action, params)).status_code, 400)
        for version in (True, '1', 2):
            value = payload()
            value['action_version'] = version
            self.assertEqual(self.create(value).status_code, 400)
        self.assertFalse(AgentJob.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(event_type='job.created').exists())

    def test_account_contracts_valid_but_execution_unconditionally_blocked(self):
        sid = 'S-1-5-21-1-2-3-1001'
        secret = str(uuid.uuid4())
        for action, params, code in (
            ('create_local_user', {'user_name': 'nw_help', 'secret_reference': secret}, 'protected_secret_delivery_unavailable'),
            ('reset_local_password', {'user_name': 'nw_help', 'account_sid': sid, 'secret_reference': secret}, 'protected_secret_delivery_unavailable'),
            ('set_local_user_enabled', {'user_name': 'nw_help', 'account_sid': sid, 'enabled': False}, 'local_account_isolation_unavailable')):
            value = payload(action, params)
            self.assertEqual(validate_payload(value), value)
            response = self.create(value)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['error'], code)
        for name in ('Administrator', 'Administrador', 'Guest', 'CONTROL\\someone', 'someone@control.local', 'system', 'krbtgt', 'nw_help$'):
            with self.assertRaises(ValueError):
                validate_parameters('create_local_user', {'user_name': name, 'secret_reference': secret})
        for protected in ('S-1-5-18', 'S-1-5-32-544', 'S-1-5-21-1-2-3-500', 'S-1-5-21-1-2-3-501'):
            with self.assertRaises(ValueError):
                validate_parameters('set_local_user_enabled', {'user_name': 'nw_help', 'account_sid': protected, 'enabled': True})
        self.assertFalse(AgentJob.objects.exists())

    def test_binding_pins_operator_endpoint_parameters_timeout_and_expiry(self):
        job = create_action_job(self.operator, self.machine.pk, payload())
        audit = AuditEvent.objects.get(event_type='job.created')
        self.assertEqual(audit.metadata['authorization']['operator_id'], str(self.operator.pk))
        self.assertEqual(audit.metadata['authorization']['target']['machine_id'], self.machine.machine_id)
        self.assertEqual(self.create().status_code, 400)
        original = copy.deepcopy(job.payload)
        for field, value in [('payload', payload('stop_service')), ('timeout_seconds', 5),
                             ('expires_at', job.expires_at + timedelta(minutes=1)), ('created_by', 'missing')]:
            previous = getattr(job, field)
            setattr(job, field, value)
            job.status = 'queued'
            job.save()
            self.assertEqual(self.pull().json()['jobs'], [])
            job.refresh_from_db()
            self.assertEqual(job.status, 'cancelled')
            setattr(job, field, previous)
            job.save()
        job.payload = original
        job.status = 'queued'
        job.save()
        self.assertEqual(len(self.pull().json()['jobs']), 1)
        self.machine.hostname = 'SYNTHETIC-RENAMED'
        self.machine.save()
        response = self.agent.post(reverse('agent-jobs-started', args=[job.pk]), {'machine_id': self.machine.machine_id}, content_type='application/json')
        self.assertEqual(response.status_code, 409)

    def test_revocation_at_delivery_start_and_result_ack(self):
        job = create_action_job(self.operator, self.machine.pk, payload('gpupdate'))
        with override_settings(NIGHTOWL_ADMIN_ACTION_SCOPES={}):
            self.assertEqual(self.pull().json()['jobs'], [])
        job.refresh_from_db()
        self.assertEqual(job.status, 'cancelled')
        job = create_action_job(self.operator, self.machine.pk, payload('gpupdate'))
        self.pull()
        with override_settings(NIGHTOWL_ADMIN_ACTIONS_ENABLED=False):
            self.assertEqual(self.agent.post(reverse('agent-jobs-started', args=[job.pk]),
                {'machine_id': self.machine.machine_id}, content_type='application/json').status_code, 409)
        self.assertEqual(self.agent.post(reverse('agent-jobs-started', args=[job.pk]),
            {'machine_id': self.machine.machine_id}, content_type='application/json').status_code, 200)
        body = {'job_id': str(job.pk), 'machine_id': self.machine.machine_id, 'status': 'completed',
            'result': {'machine_id': self.machine.machine_id, 'output': {'schema_version': 1, 'action_id': 'gpupdate',
                'action_version': 1, 'success': True, 'request_accepted': True, 'processing_confirmed': True}}}
        self.assertEqual(self.agent.post(reverse('agent-jobs-result'), body, content_type='application/json').status_code, 400)
        body['result']['output']['processing_confirmed'] = False
        for _ in range(2):
            response = self.agent.post(reverse('agent-jobs-result'), body, content_type='application/json', HTTP_IDEMPOTENCY_KEY='synthetic-admin-ack')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['result_id'], 'synthetic-admin-ack')
        self.assertEqual(AgentJobResultReceipt.objects.filter(job=job).count(), 1)

    def test_service_completion_expected_state_and_binding_required_for_manual_jobs(self):
        job = AgentJob.objects.create(endpoint=self.machine, created_by=self.operator.username,
            job_type='operational_action', payload=payload(), timeout_seconds=15)
        self.assertEqual(self.pull().json()['jobs'], [])
        job = create_action_job(self.operator, self.machine.pk, payload('stop_service'))
        self.pull()
        body = {'job_id': str(job.pk), 'machine_id': self.machine.machine_id, 'status': 'completed', 'result': {
            'machine_id': self.machine.machine_id, 'output': {'schema_version': 1, 'action_id': 'stop_service', 'action_version': 1,
                'success': True, 'service_name': 'AcmePilotService', 'changed': True, 'state': 'Running'}}}
        self.assertEqual(self.agent.post(reverse('agent-jobs-result'), body, content_type='application/json').status_code, 400)
        body['result']['output']['state'] = 'Stopped'
        self.assertEqual(self.agent.post(reverse('agent-jobs-result'), body, content_type='application/json').status_code, 200)

    def test_permissions_and_operator_identity_rechecked_after_delivery(self):
        user = get_user_model().objects.create_user('specific-operator', is_staff=True)
        permissions = Permission.objects.filter(content_type__app_label='agents', codename__in=['add_agentjob', 'start_windows_service'])
        user.user_permissions.add(*permissions)
        job = create_action_job(user, self.machine.pk, payload())
        self.pull()
        user.user_permissions.clear()
        start = reverse('agent-jobs-started', args=[job.pk])
        body = {'machine_id': self.machine.machine_id}
        self.assertEqual(self.agent.post(start, body, content_type='application/json').status_code, 409)
        user.delete()
        replacement = get_user_model().objects.create_user('specific-operator', is_staff=True)
        replacement.user_permissions.add(*permissions)
        self.assertEqual(self.agent.post(start, body, content_type='application/json').status_code, 409)
        job.refresh_from_db()
        self.assertIsNone(job.started_at)
