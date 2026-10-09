import json
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone

from dashboard.views import endpoint_job_create
from .models import AgentJob, AgentJobResultReceipt, AgentMachine, InventorySnapshot
from .telemetry_configuration import monitoring_summary, validate_configuration


class TelemetryConfigurationTests(TestCase):
    def setUp(self):
        self.machine = AgentMachine(machine_id=str(uuid.uuid4()), hostname='TELEMETRY-LAB', agent_version='0.1.1.0-rc46')
        self.machine.set_agent_token('synthetic-token')
        self.machine.save()
        self.user = get_user_model().objects.create_user('telemetry-operator', is_staff=True)
        self.client = Client(HTTP_AUTHORIZATION='Bearer synthetic-token')
        self.config = {'telemetryEnabled': True, 'telemetrySampleSeconds': 300, 'telemetryFlushSeconds': 900}
        self.heartbeat()

    def heartbeat(self, config=None):
        return InventorySnapshot.objects.create(machine=self.machine, hostname=self.machine.hostname,
            collected_at=timezone.now(), raw_payload={
                'snapshot_source': 'heartbeat', 'machine_id': self.machine.machine_id,
                'agent_version': self.machine.agent_version,
                'agent': {'capabilities': ['configure_telemetry'], 'telemetry_configuration': config or self.config},
            })

    def create(self, config=None, user=None):
        request = RequestFactory().post('/jobs/', {'action': 'configure_telemetry',
            'configuration': json.dumps(self.config if config is None else config)})
        request.user = user or self.user
        return endpoint_job_create(request, self.machine.pk)

    def job(self):
        self.assertEqual(self.create().status_code, 201)
        return self.machine.jobs.get()

    def result(self, job, result=None, result_id=None):
        result = result or {'confirmed': True, 'configuration_id': str(job.pk), 'machine_id': self.machine.machine_id,
            'effective': self.config, 'applied_at': timezone.now().isoformat()}
        return self.client.post('/api/agent/jobs/result/', {
            'job_id': str(job.pk), 'status': 'completed', 'exit_code': 0, 'result': result,
        }, content_type='application/json', HTTP_IDEMPOTENCY_KEY=result_id or str(uuid.uuid4()))

    def test_strict_types_ranges_and_fields(self):
        for config in [dict(self.config, telemetryEnabled='true'), dict(self.config, telemetrySampleSeconds=True),
                       dict(self.config, telemetrySampleSeconds=59), dict(self.config, telemetryFlushSeconds=86401),
                       dict(self.config, agentToken='secret'), {}, dict(self.config, telemetryFlushSeconds=900.5)]:
            with self.subTest(config=config):
                self.assertEqual(self.create(config).status_code, 400)
        self.assertFalse(self.machine.jobs.exists())
        for sample in (60, 3600):
            for flush in (60, 86400):
                self.assertEqual(validate_configuration(dict(self.config, telemetrySampleSeconds=sample,
                    telemetryFlushSeconds=flush))['telemetrySampleSeconds'], sample)

    def test_technical_authorization(self):
        self.assertEqual(self.create(user=AnonymousUser()).status_code, 403)
        ordinary = get_user_model().objects.create_user('ordinary')
        self.assertEqual(self.create(user=ordinary).status_code, 403)
        self.assertFalse(self.machine.jobs.exists())

    def test_rc45_blocked_and_no_configuration_job(self):
        self.machine.agent_version = '0.1.1.0-rc45'
        self.machine.save()
        self.assertEqual(self.create().status_code, 409)
        self.assertFalse(self.machine.jobs.exists())

    def test_capability_must_be_reported(self):
        self.machine.inventory_snapshots.all().delete()
        self.assertEqual(self.create().status_code, 409)

    def test_compatible_job_is_bounded_and_audited(self):
        job = self.job()
        self.assertEqual(job.payload, self.config)
        self.assertTrue(self.machine.audit_events.filter(event_type='job.created', metadata__job_id=str(job.pk)).exists())
        self.assertEqual(self.create().status_code, 409)

    def test_lifecycle_conflict_blocks_configuration(self):
        AgentJob.objects.create(endpoint=self.machine, job_type='update_agent')
        self.assertEqual(self.create().status_code, 409)

    def test_pending_configuration_blocks_other_exclusive_job(self):
        self.job()
        request = RequestFactory().post('/jobs/', {'action': 'restart_agent'})
        request.user = self.user
        self.assertEqual(endpoint_job_create(request, self.machine.pk).status_code, 409)

    def test_pull_refuses_incompatible_or_malformed_direct_job(self):
        self.machine.agent_version = '0.1.1.0-rc45'
        self.machine.save()
        job = AgentJob.objects.create(endpoint=self.machine, job_type='configure_telemetry', payload=self.config)
        response = self.client.get('/api/agent/jobs/pull/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['jobs'], [])
        job.refresh_from_db()
        self.assertEqual(job.status, 'unsupported')

    def test_pull_preserves_exact_configuration_payload(self):
        job = self.job()
        response = self.client.get('/api/agent/jobs/pull/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['jobs'][0]['payload'], self.config)

    def test_completed_requires_effective_confirmation(self):
        job = self.job()
        for result in [{}, {'confirmed': False}, {'confirmed': True, 'configuration_id': str(job.pk),
            'machine_id': 'another-machine', 'effective': self.config, 'applied_at': timezone.now().isoformat()},
            {'confirmed': True, 'configuration_id': str(job.pk), 'machine_id': self.machine.machine_id,
             'effective': dict(self.config, telemetryEnabled=False), 'applied_at': timezone.now().isoformat()}]:
            with self.subTest(result=result):
                response = self.client.post('/api/agent/jobs/result/', {'job_id': str(job.pk), 'status': 'completed',
                    'result': result}, content_type='application/json')
                self.assertEqual(response.status_code, 400)
        job.refresh_from_db()
        self.assertEqual(job.status, 'queued')
        self.assertFalse(AgentJobResultReceipt.objects.filter(job=job).exists())

    def test_completion_idempotent_and_samples_not_inferred(self):
        job = self.job()
        result_id = str(uuid.uuid4())
        payload = {'confirmed': True, 'configuration_id': str(job.pk), 'machine_id': self.machine.machine_id,
            'effective': self.config, 'applied_at': timezone.now().isoformat()}
        self.assertEqual(self.result(job, payload, result_id).status_code, 200)
        self.assertEqual(self.result(job, payload, result_id).status_code, 200)
        self.assertEqual(AgentJobResultReceipt.objects.filter(job=job).count(), 1)
        summary = monitoring_summary(self.machine)
        self.assertEqual(summary['effective'], self.config)
        self.assertEqual(summary['job_status'], 'completed')
        self.assertIsNone(summary['last_received_at'])
        self.assertFalse(summary['samples_after_application'])

    def test_unknown_configuration_not_inferred_from_zero_samples(self):
        self.machine.agent_version = '0.1.1.0-rc45'
        self.machine.save()
        summary = monitoring_summary(self.machine)
        self.assertIsNone(summary['effective'])
        self.assertFalse(summary['compatible'])

    def test_new_heartbeat_overrides_older_effective_configuration(self):
        job = self.job()
        self.assertEqual(self.result(job).status_code, 200)
        disabled = dict(self.config, telemetryEnabled=False)
        self.heartbeat(disabled)
        self.assertEqual(monitoring_summary(self.machine)['effective'], disabled)
