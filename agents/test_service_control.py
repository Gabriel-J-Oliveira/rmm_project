import json
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AgentJob, AgentMachine, AuditEvent, InventorySnapshot


@override_settings(NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED=True, NIGHTOWL_ADMIN_ACTIONS_ENABLED=True,
    NIGHTOWL_SERVICE_CONTROL_ENABLED=True)
class ServiceControlApiTests(TestCase):
    def setUp(self):
        self.machine, self.token = AgentMachine.create_with_token(hostname='SYNTHETIC-SERVICE-CONTROL',
            machine_id=str(uuid.uuid4()), agent_version='synthetic-v2', last_seen_at=timezone.now(),
            status=AgentMachine.STATUS_ONLINE)
        self.operator = get_user_model().objects.create_superuser('synthetic-service-control', password='synthetic-only')
        self.client.force_login(self.operator)
        self.agent = Client(HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.request_id = str(uuid.uuid4())
        self.scope = {str(self.machine.pk): {
            'operator_ids': [str(self.operator.pk)],
            'start_service': ['AcmePilotService'], 'stop_service': ['AcmePilotService'],
            'restart_service': ['AcmePilotService'],
        }}
        self.scope_override = override_settings(NIGHTOWL_ADMIN_ACTION_SCOPES=self.scope)
        self.scope_override.enable()
        self.addCleanup(self.scope_override.disable)
        self.capabilities = ['jobs_protocol_v2', *[
            f'operational_action:{action}:1' for action in ('start_service', 'stop_service', 'restart_service')]]
        self.heartbeat(self.capabilities)
        self.create_url = reverse('service-manager-action-create', args=[self.machine.pk])

    def heartbeat(self, capabilities):
        return InventorySnapshot.objects.create(machine=self.machine, hostname=self.machine.hostname,
            collected_at=timezone.now(), raw_payload={'snapshot_source': 'heartbeat', 'machine_id': self.machine.machine_id,
                'agent_version': self.machine.agent_version, 'agent': {'capabilities': capabilities}})

    def body(self, *, action='start_service', service='AcmePilotService', request_id=None):
        return {'request_id': request_id or self.request_id, 'action_id': action, 'service_name': service}

    def post(self, **kwargs):
        return self.client.post(self.create_url, json.dumps(self.body(**kwargs)), content_type='application/json')

    def pull(self):
        return self.agent.get(reverse('agent-jobs-pull'), HTTP_X_NIGHTOWL_JOB_PROTOCOL='2')

    def test_separate_flag_endpoint_service_action_and_permission_are_required(self):
        with override_settings(NIGHTOWL_SERVICE_CONTROL_ENABLED=False):
            response = self.post()
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()['error'], 'service_control_disabled')
        with override_settings(NIGHTOWL_ADMIN_ACTION_SCOPES={}):
            self.assertEqual(self.post(request_id=str(uuid.uuid4())).status_code, 403)
        self.assertEqual(self.post(service='NotAuthorizedService', request_id=str(uuid.uuid4())).status_code, 409)

        user = get_user_model().objects.create_user('service-view-only', is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='agents',
            codename__in=['add_agentjob', 'view_agentmachine', 'view_agentjob']))
        self.client.force_login(user)
        response = self.post(request_id=str(uuid.uuid4()))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AgentJob.objects.exists())

    def test_service_control_scope_selects_operator_independently_of_optional_job_scope(self):
        allowed_operator = get_user_model().objects.create_user('synthetic-service-allowed', is_staff=True)
        peer = get_user_model().objects.create_user('synthetic-service-peer', is_staff=True)
        operator_permissions = Permission.objects.filter(content_type__app_label='agents',
            codename__in=['add_agentjob', 'view_agentmachine', 'view_agentjob', 'start_windows_service'])
        allowed_operator.user_permissions.add(*operator_permissions)
        peer.user_permissions.add(*operator_permissions)
        self.scope[str(self.machine.pk)]['operator_ids'] = [str(allowed_operator.pk)]
        for user in (allowed_operator, peer):
            self.assertTrue(user.has_perm('agents.add_agentjob'))
            self.assertTrue(user.has_perm('agents.start_windows_service'))

        self.client.force_login(peer)
        with override_settings(NIGHTOWL_OPERATIONAL_ACTION_SCOPED_ONLY=False):
            denied = self.post(request_id=str(uuid.uuid4()))
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()['error'], 'action_forbidden')
        self.assertFalse(AgentJob.objects.exists())

        self.client.force_login(allowed_operator)
        with override_settings(NIGHTOWL_OPERATIONAL_ACTION_SCOPED_ONLY=False):
            allowed = self.post(request_id=str(uuid.uuid4()))
        self.assertEqual(allowed.status_code, 202)
        self.assertEqual(AgentJob.objects.get().created_by, allowed_operator.get_username())

        AgentJob.objects.all().delete()
        for operator_ids in (None, [], ['*'], [allowed_operator.pk]):
            invalid_scope = {str(self.machine.pk): {**self.scope[str(self.machine.pk)],
                'operator_ids': operator_ids}}
            with self.subTest(operator_ids=operator_ids), override_settings(NIGHTOWL_ADMIN_ACTION_SCOPES=invalid_scope):
                denied = self.post(request_id=str(uuid.uuid4()))
                self.assertEqual(denied.status_code, 403)
                self.assertFalse(AgentJob.objects.exists())

    def test_protected_services_and_invalid_requests_are_rejected_without_jobs(self):
        for service in ('EventLog', 'WinRM', 'NightOwlAgentDotNet', '*', 'Spooler;whoami'):
            response = self.post(service=service, request_id=str(uuid.uuid4()))
            self.assertIn(response.status_code, (400, 409))
        for payload in ({}, {'request_id': str(uuid.uuid4()), 'action_id': 'gpupdate', 'service_name': 'AcmePilotService'},
                        {**self.body(), 'ignored': True}):
            response = self.client.post(self.create_url, json.dumps(payload), content_type='application/json')
            self.assertEqual(response.status_code, 400)
        self.assertFalse(AgentJob.objects.exists())

    def test_online_unique_identity_recent_capability_and_conflict_are_required(self):
        self.machine.status = AgentMachine.STATUS_OFFLINE
        self.machine.save(update_fields=['status'])
        self.assertEqual(self.post().json()['error'], 'service_endpoint_ineligible')
        self.machine.status = AgentMachine.STATUS_ONLINE
        self.machine.save(update_fields=['status'])
        InventorySnapshot.objects.filter(machine=self.machine).update(received_at=timezone.now() - timedelta(minutes=16))
        self.assertEqual(self.post().json()['error'], 'action_capability_unconfirmed')
        self.heartbeat(self.capabilities)
        duplicate, _ = AgentMachine.create_with_token(hostname='SYNTHETIC-DUPLICATE', machine_id=self.machine.machine_id)
        self.assertEqual(self.post().json()['error'], 'identity_unavailable')
        duplicate.delete()
        AgentJob.objects.create(endpoint=self.machine, job_type='ping', created_by='synthetic', status=AgentJob.STATUS_QUEUED)
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'job_conflict')

    def test_idempotent_request_does_not_create_or_dispatch_duplicate(self):
        first = self.post()
        second = self.post()
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()['job_id'], second.json()['job_id'])
        self.assertEqual(AgentJob.objects.filter(job_type='operational_action').count(), 1)
        self.assertEqual(AuditEvent.objects.filter(event_type='job.created', metadata__job_id=first.json()['job_id']).count(), 1)
        conflict = self.post(action='stop_service')
        self.assertEqual(conflict.status_code, 400)
        self.assertEqual(conflict.json()['error'], 'request_id_conflict')

    def test_revoked_scope_cancels_pending_job_and_is_rechecked_at_agent_start(self):
        pending = self.post().json()['job_id']
        revoked = {str(self.machine.pk): {**self.scope[str(self.machine.pk)], 'operator_ids': []}}
        with override_settings(NIGHTOWL_ADMIN_ACTION_SCOPES=revoked):
            self.assertEqual(self.pull().json()['jobs'], [])
        job = AgentJob.objects.get(pk=pending)
        self.assertEqual(job.status, AgentJob.STATUS_CANCELLED)
        self.assertEqual(job.error_code, 'ACTION_AUTHORIZATION_REVOKED')
        self.assertTrue(AuditEvent.objects.filter(event_type='job.cancelled', metadata__job_id=pending).exists())

        pending = self.post(request_id=str(uuid.uuid4())).json()['job_id']
        self.assertEqual(len(self.pull().json()['jobs']), 1)
        transferred = {str(self.machine.pk): {**self.scope[str(self.machine.pk)], 'operator_ids': ['999999']}}
        with override_settings(NIGHTOWL_ADMIN_ACTION_SCOPES=transferred):
            response = self.agent.post(reverse('agent-jobs-started', args=[pending]),
                {'machine_id': self.machine.machine_id}, content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(AgentJob.objects.get(pk=pending).status, AgentJob.STATUS_SENT)

    def test_status_only_reports_success_after_exact_agent_confirmation(self):
        job_id = self.post().json()['job_id']
        job = AgentJob.objects.get(pk=job_id)
        job.status = AgentJob.STATUS_COMPLETED
        job.finished_at = timezone.now()
        job.result = {'machine_id': self.machine.machine_id, 'output': {'schema_version': 1,
            'action_id': 'start_service', 'action_version': 1, 'success': True,
            'service_name': 'AcmePilotService', 'state': 'Running', 'changed': True}}
        job.save(update_fields=['status', 'finished_at', 'result'])
        status_url = reverse('service-manager-action-status', args=[self.machine.pk, job.pk])
        response = self.client.get(status_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'completed')
        self.assertTrue(response.json()['result_confirmed'])
        self.assertEqual(response.json()['result'], {'state': 'Running', 'changed': True})
        job.result['output']['state'] = 'Stopped'
        job.save(update_fields=['result'])
        response = self.client.get(status_url)
        self.assertEqual(response.json()['status'], 'failed')
        self.assertFalse(response.json()['result_confirmed'])
        self.assertEqual(response.json()['error_code'], 'SERVICE_RESULT_UNCONFIRMED')

    def test_agent_without_capability_cannot_create_job_and_query_feature_is_separate(self):
        self.heartbeat(['jobs_protocol_v2'])
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'action_capability_unconfirmed')
        self.assertFalse(AgentJob.objects.exists())
