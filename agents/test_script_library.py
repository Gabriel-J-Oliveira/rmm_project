import copy
import json
import os
import subprocess
import uuid
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, LiveServerTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AgentMachine, AgentJob, AgentJobResultReceipt, AuditEvent, InventorySnapshot
from .operational_actions import create_action_job, validate_payload
from .script_library import DEMO, capability, resolve


def payload():
    return {'action_id': 'run_approved_script', 'action_version': 1, 'parameters': {
        'script_id': DEMO.script_id, 'script_version': DEMO.version, 'sha256': DEMO.sha256, 'parameters': {}}}


def heartbeat(machine):
    return InventorySnapshot.objects.create(machine=machine, hostname=machine.hostname, collected_at=timezone.now(),
        raw_payload={'snapshot_source': 'heartbeat', 'machine_id': machine.machine_id, 'agent_version': machine.agent_version,
            'agent': {'capabilities': ['jobs_protocol_v2', 'operational_action:run_approved_script:1', capability(DEMO)]}})


@override_settings(NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED=True, NIGHTOWL_APPROVED_SCRIPTS_ENABLED=True)
class ScriptLibraryTests(TestCase):
    def setUp(self):
        self.machine, token = AgentMachine.create_with_token(hostname='SYNTHETIC-SCRIPT', machine_id=str(uuid.uuid4()))
        self.agent = Client(HTTP_AUTHORIZATION='Bearer ' + token)
        self.operator = get_user_model().objects.create_superuser('synthetic-scripts', password='synthetic-only')
        self.client.force_login(self.operator)
        self.url = reverse('operational-action-create', args=[self.machine.pk])
        self.snapshot = heartbeat(self.machine)

    def create(self, value=None):
        return self.client.post(self.url, value or payload(), content_type='application/json')

    def pull(self, v2=True):
        return self.agent.get(reverse('agent-jobs-pull'), **({'HTTP_X_NIGHTOWL_JOB_PROTOCOL': '2'} if v2 else {}))

    def test_pinned_registry_real_bytes_and_immutability(self):
        self.assertEqual(resolve(DEMO.script_id, 1), DEMO)
        with self.assertRaises(FrozenInstanceError):
            DEMO.version = 2
        from .script_library import CATALOG
        with self.assertRaises(TypeError):
            CATALOG[(DEMO.script_id, 2)] = DEMO
        with patch('agents.script_library.Path.read_bytes', return_value=b'modified'):
            with self.assertRaisesMessage(ValueError, 'script_integrity_mismatch'):
                validate_payload(payload())
        with patch('agents.script_library.Path.read_bytes', side_effect=OSError):
            self.assertEqual(self.create().status_code, 400)
        with patch.dict('agents.script_library.__dict__', CATALOG={(DEMO.script_id, 1): replace(DEMO, approved=False)}):
            self.assertEqual(self.create().status_code, 400)
        self.assertFalse(AgentJob.objects.exists())

    def test_reject_unknown_version_hash_parameters_content_and_runtime(self):
        invalid = [('script_id', 'unknown'), ('script_version', 2), ('script_version', True),
            ('script_version', '1'), ('sha256', '0' * 64), ('parameters', {'command': 'whoami'}),
            ('content', 'whoami'), ('runtime', 'cmd.exe'), ('path', 'C:\\anything.ps1')]
        for field, value in invalid:
            body = payload()
            body['parameters'][field] = value
            self.assertEqual(self.create(body).status_code, 400, field)
        self.assertFalse(AgentJob.objects.exists())

    def test_permissions_csrf_catalog_and_default_disabled(self):
        self.client.logout()
        self.assertEqual(self.create().status_code, 403)
        self.assertEqual(self.agent.get(reverse('approved-script-catalog')).status_code, 403)
        user = get_user_model().objects.create_user('script-reader', is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.create().status_code, 403)
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='agents',
            codename__in=['add_agentjob', 'view_agentmachine']))
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(user)
        self.assertEqual(csrf.post(self.url, payload(), content_type='application/json').status_code, 403)
        catalog = self.client.get(reverse('approved-script-catalog'))
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(catalog.json()['scripts'][0]['sha256'], DEMO.sha256)
        self.assertFalse(catalog.json()['scripts'][0]['parameter_schema']['additionalProperties'])
        with override_settings(NIGHTOWL_APPROVED_SCRIPTS_ENABLED=False):
            self.assertEqual(self.create().status_code, 400)
        self.assertEqual(self.create().status_code, 201)

    def test_revocation_and_permission_change_at_delivery_and_start(self):
        job = create_action_job(self.operator, self.machine.pk, payload())
        with override_settings(NIGHTOWL_REVOKED_SCRIPT_VERSIONS=['environment_diagnostic:1']):
            self.assertEqual(self.pull().json()['jobs'], [])
        job.refresh_from_db()
        self.assertEqual(job.status, 'cancelled')
        job = create_action_job(self.operator, self.machine.pk, payload())
        self.pull()
        for enabled, revoked in [(False, []), (True, ['environment_diagnostic:1'])]:
            with override_settings(NIGHTOWL_APPROVED_SCRIPTS_ENABLED=enabled, NIGHTOWL_REVOKED_SCRIPT_VERSIONS=revoked):
                response = self.agent.post(reverse('agent-jobs-started', args=[job.pk]),
                    {'machine_id': self.machine.machine_id}, content_type='application/json')
                self.assertEqual(response.status_code, 409)
        self.operator.is_active = False
        self.operator.save()
        self.assertEqual(self.agent.post(reverse('agent-jobs-started', args=[job.pk]),
            {'machine_id': self.machine.machine_id}, content_type='application/json').status_code, 409)
        job.refresh_from_db()
        self.assertIsNone(job.started_at)

    def test_quarantine_duplicate_identity_capability_legacy_and_conflicts(self):
        for hostname in ('CS-CVEL-0029', 'CS-CVEL-0059', 'CS-CVEL-0133', 'CS-CVEL-0262'):
            self.machine.hostname = hostname
            self.machine.save()
            self.assertEqual(self.create().status_code, 400)
        self.machine.hostname = 'SYNTHETIC-SCRIPT'
        self.machine.save()
        other, _ = AgentMachine.create_with_token(hostname='SYNTHETIC-DUPLICATE', machine_id=self.machine.machine_id)
        self.assertEqual(self.create().status_code, 400)
        other.delete()
        raw = copy.deepcopy(self.snapshot.raw_payload)
        raw['agent']['capabilities'] = ['operational_action:run_approved_script:1', 'unknown_capability']
        self.snapshot.raw_payload = raw
        self.snapshot.save()
        self.assertEqual(self.create().status_code, 400)
        self.snapshot.delete()
        heartbeat(self.machine)
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.create().status_code, 400)
        self.assertEqual(self.pull(v2=False).json()['jobs'], [])
        self.assertEqual(AgentJob.objects.get().status, 'cancelled')
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(len(self.pull().json()['jobs']), 1)

    def test_result_persistence_receipt_replay_and_revocation_after_execution(self):
        job = create_action_job(self.operator, self.machine.pk, payload())
        self.pull()
        self.assertEqual(self.agent.post(reverse('agent-jobs-started', args=[job.pk]),
            {'machine_id': self.machine.machine_id}, content_type='application/json').status_code, 200)
        output = {'schema_version': 1, 'action_id': 'run_approved_script', 'action_version': 1, 'success': True,
            'script_id': DEMO.script_id, 'script_version': 1, 'sha256': DEMO.sha256, 'runtime': DEMO.runtime,
            'diagnostic': {'utc': '2026-10-30T14:00:00.0000000Z', 'powershell_version': '5.1.19041.1'}}
        body = {'job_id': str(job.pk), 'machine_id': self.machine.machine_id, 'status': 'completed',
            'exit_code': 0, 'stdout': json.dumps(output['diagnostic']), 'stderr': '', 'duration_seconds': 1,
            'result': {'machine_id': self.machine.machine_id, 'output': output}}
        for key, value in [('sha256', '0' * 64), ('script_version', True), ('script_id', 'unknown'), ('runtime', 'cmd.exe')]:
            bad = copy.deepcopy(body)
            bad['result']['output'][key] = value
            self.assertEqual(self.agent.post(reverse('agent-jobs-result'), bad, content_type='application/json').status_code, 400)
        job.payload['action_version'] = True
        job.save()
        self.assertEqual(self.agent.post(reverse('agent-jobs-result'), body, content_type='application/json').status_code, 400)
        job.payload['action_version'] = 1
        job.save()
        with override_settings(NIGHTOWL_REVOKED_SCRIPT_VERSIONS=['environment_diagnostic:1']):
            for _ in range(2):
                response = self.agent.post(reverse('agent-jobs-result'), body, content_type='application/json',
                    HTTP_IDEMPOTENCY_KEY='synthetic-script-receipt')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['result_id'], 'synthetic-script-receipt')
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
        self.assertEqual(job.result['output']['sha256'], DEMO.sha256)
        self.assertEqual(AgentJobResultReceipt.objects.filter(job=job).count(), 1)
        audit = AuditEvent.objects.get(event_type='job.created')
        self.assertEqual(audit.metadata['parameters']['sha256'], DEMO.sha256)


@override_settings(NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED=True, NIGHTOWL_APPROVED_SCRIPTS_ENABLED=True)
class ScriptLibraryE2ETests(LiveServerTestCase):
    def test_actual_powershell_diagnostic_v2_persisted_with_exact_ack(self):
        dll = Path(settings.BASE_DIR) / 'NightOwl.Agent.Windows.Tests/bin/Release/net8.0-windows/NightOwl.Agent.Windows.Tests.dll'
        self.assertTrue(dll.exists(), 'Build affected .NET projects first.')
        machine, token = AgentMachine.create_with_token(hostname='SYNTHETIC-SCRIPT-E2E', machine_id=str(uuid.uuid4()))
        operator = get_user_model().objects.create_superuser('synthetic-script-e2e', password='synthetic-only')
        heartbeat(machine)
        job = create_action_job(operator, machine.pk, payload())
        result = subprocess.run(['dotnet', str(dll), '--approved-scripts-e2e'], capture_output=True, text=True, timeout=100,
            env={**os.environ, 'NIGHTOWL_TEST_SERVER': self.live_server_url, 'NIGHTOWL_TEST_TOKEN': token,
                'NIGHTOWL_TEST_MACHINE': machine.machine_id, 'NIGHTOWL_TEST_JOB_COUNT': '1'})
        self.assertEqual(result.returncode, 0, 'Local script E2E failed; raw output withheld.')
        evidence = json.loads(next(line.split('=', 1)[1] for line in result.stdout.splitlines() if line.startswith('JOB_PROTOCOL_TIMINGS=')))
        self.assertIn('result_ack', evidence['jobs'][str(job.pk)])
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
        self.assertEqual(job.result['output']['sha256'], DEMO.sha256)
        self.assertTrue(job.result['output']['diagnostic']['powershell_version'].startswith('5.1'))
        self.assertEqual(AgentJobResultReceipt.objects.filter(job=job).count(), 1)
