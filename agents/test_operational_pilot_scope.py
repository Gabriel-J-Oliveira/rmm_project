import uuid
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from agents.test_operational_actions import payload, heartbeat
from agents.models import AgentJob, AgentMachine, AgentJobResultReceipt, AuditEvent


@override_settings(NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED=True, NIGHTOWL_OPERATIONAL_ACTION_SCOPED_ONLY=True)
class OperationalPilotScopeTests(TestCase):
    def setUp(self):
        super().setUp()
        self.machine, token = AgentMachine.create_with_token(hostname='SYNTHETIC-PILOT', machine_id=str(uuid.uuid4()))
        self.agent = Client(HTTP_AUTHORIZATION='Bearer ' + token)
        self.operator = get_user_model().objects.create_superuser('synthetic-pilot', password='synthetic')
        self.client.force_login(self.operator)
        self.url = reverse('operational-action-create', args=[self.machine.pk])
        heartbeat(self.machine)
        self.machine.status = 'online'
        self.machine.save(update_fields=['status'])
        self.scopes = {str(self.machine.pk): {'machine_id': self.machine.machine_id,
                       'operator_ids': [str(self.operator.pk)], 'actions': ['query_service']}}
        self.scope_settings = override_settings(NIGHTOWL_OPERATIONAL_ACTION_SCOPES=self.scopes)
        self.scope_settings.enable()
        self.addCleanup(self.scope_settings.disable)

    def create(self, value=None):
        return self.client.post(self.url, payload() if value is None else value, content_type='application/json')

    def pull(self):
        return self.agent.get(reverse('agent-jobs-pull'), HTTP_X_NIGHTOWL_JOB_PROTOCOL='2')

    def test_explicit_scope_allows_only_selected_action_and_endpoint(self):
        self.assertEqual(self.create(payload('flush_dns')).status_code, 403)
        other, _ = AgentMachine.create_with_token(hostname='SYNTHETIC-OTHER', machine_id='other-identity')
        heartbeat(other)
        self.assertEqual(self.client.post(reverse('operational-action-create', args=[other.pk]),
            payload(), content_type='application/json').status_code, 403)
        self.assertEqual(self.create().status_code, 201)

    def test_scoped_action_reaches_start_result_and_idempotent_ack(self):
        self.assertEqual(self.create().status_code, 201)
        job = AgentJob.objects.get()
        self.assertEqual(len(self.pull().json()['jobs']), 1)
        self.assertEqual(self.agent.post(reverse('agent-jobs-started', args=[job.pk]),
            {'machine_id': self.machine.machine_id}, content_type='application/json').status_code, 200)
        body = {'job_id': str(job.pk), 'machine_id': self.machine.machine_id, 'status': 'completed',
                'result': {'machine_id': self.machine.machine_id, 'output': {
                    'schema_version': 1, 'action_id': 'query_service', 'action_version': 1,
                    'success': True, 'service_name': 'EventLog', 'state': 'Running'}}}
        for _ in range(2):
            response = self.agent.post(reverse('agent-jobs-result'), body, content_type='application/json',
                HTTP_IDEMPOTENCY_KEY='scoped-pilot-result')
            self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
        self.assertEqual(AgentJobResultReceipt.objects.filter(job=job).count(), 1)
        self.assertTrue(AuditEvent.objects.filter(endpoint=self.machine, event_type='job.created').exists())

    def test_wrong_identity_operator_and_malformed_scope_fail_closed(self):
        from copy import deepcopy
        for mutation in ({'machine_id': 'wrong'}, {'operator_ids': ['*']}, {'operator_ids': [0]},
                         {'actions': ['*']}, {'actions': []}, {'extra': True}):
            scope = deepcopy(self.scopes)
            scope[str(self.machine.pk)].update(mutation)
            with self.subTest(mutation=mutation), override_settings(NIGHTOWL_OPERATIONAL_ACTION_SCOPES=scope):
                self.assertEqual(self.create().status_code, 403)
        self.assertFalse(AgentJob.objects.exists())

    @override_settings(NIGHTOWL_OPERATIONAL_ACTION_SCOPES=[])
    def test_non_mapping_scope_fails_closed(self):
        self.assertEqual(self.create().status_code, 403)
        self.assertFalse(AgentJob.objects.exists())

    def test_scope_revocation_is_rechecked_before_delivery(self):
        self.assertEqual(self.create().status_code, 201)
        with override_settings(NIGHTOWL_OPERATIONAL_ACTION_SCOPES={}):
            jobs = self.pull().json().get('jobs', [])
        self.assertEqual(jobs, [])
        self.assertEqual(AgentJob.objects.get().status, 'cancelled')

    def test_scope_revocation_after_delivery_prevents_start(self):
        self.assertEqual(self.create().status_code, 201)
        job = AgentJob.objects.get()
        self.assertEqual(len(self.pull().json()['jobs']), 1)
        with override_settings(NIGHTOWL_OPERATIONAL_ACTION_SCOPES={}):
            response = self.agent.post(reverse('agent-jobs-started', args=[job.pk]),
                {'machine_id': self.machine.machine_id}, content_type='application/json')
        self.assertEqual(response.status_code, 409)
        job.refresh_from_db()
        self.assertIsNone(job.started_at)

    def test_other_privileged_operator_is_rejected(self):
        other = get_user_model().objects.create_superuser('other-pilot-operator', password='synthetic')
        self.client.force_login(other)
        self.assertEqual(self.create().status_code, 403)
        self.assertFalse(AgentJob.objects.exists())

    @override_settings(NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED=False)
    def test_scope_never_overrides_disabled_global_gate(self):
        self.assertEqual(self.create().status_code, 400)
        self.assertFalse(AgentJob.objects.exists())

    def test_ui_availability_respects_same_scope(self):
        from dashboard.views import operational_action_availability
        available = operational_action_availability(self.machine, self.operator)
        self.assertTrue(available['query_service']['enabled'])
        self.assertFalse(available['flush_dns']['enabled'])
        self.assertIn('não autorizada', available['flush_dns']['reason'])

    @override_settings(NIGHTOWL_APPROVED_SCRIPTS_ENABLED=True)
    def test_approved_script_requires_pinned_reference_in_scope(self):
        from agents.test_script_library import payload as script_payload, heartbeat as script_heartbeat
        from agents.script_library import DEMO
        self.machine.inventory_snapshots.all().delete()
        script_heartbeat(self.machine)
        self.scopes[str(self.machine.pk)]['actions'] = ['run_approved_script']
        self.assertEqual(self.create(script_payload()).status_code, 403)
        self.scopes[str(self.machine.pk)]['scripts'] = [f'{DEMO.script_id}:{DEMO.version}:{DEMO.sha256}']
        self.assertEqual(self.create(script_payload()).status_code, 201)
