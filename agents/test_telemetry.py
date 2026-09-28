import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AgentMachine, EndpointPerformanceSample, hash_agent_token


class TelemetryApiTests(TestCase):
    def setUp(self):
        self.token = 'synthetic-test-agent-credential'
        self.machine = AgentMachine.objects.create(
            hostname='LAB-TELEMETRY',
            machine_id=str(uuid.uuid4()),
            agent_token_hash=hash_agent_token(self.token),
            agent_version='0.1.1.0-rc40',
        )
        self.other = AgentMachine.objects.create(
            hostname='OTHER-LAB',
            machine_id=str(uuid.uuid4()),
            agent_token_hash=hash_agent_token('synthetic-other-credential'),
            agent_version='0.1.1.0-rc39',
        )
        self.ingest_url = reverse('agent-telemetry')
        self.query_url = reverse('endpoint-telemetry', args=[self.machine.pk])

    def sample(self, collected_at=None):
        return {
            'sample_id': str(uuid.uuid4()),
            'collected_at': (collected_at or timezone.now()).isoformat(),
            'uptime_seconds': 1200,
            'cpu': {'usage_percent': 11.5},
            'memory': {
                'total_bytes': 1000, 'available_bytes': 500, 'used_percent': 50,
                'committed_bytes': None, 'commit_limit_bytes': None, 'committed_percent': None,
            },
            'disk': {
                'active_percent': None, 'queue_length': None,
                'read_bytes_per_sec': None, 'write_bytes_per_sec': None,
                'read_latency_ms': None, 'write_latency_ms': None,
            },
            'network': {'received_bytes': 20, 'sent_bytes': 10},
            'top_processes': {
                'cpu': [{'process_name': 'synthetic-worker', 'pid': 4, 'cpu_percent': 5, 'working_set_bytes': 200}],
                'memory': [],
            },
            'collection_duration_ms': 25,
            'agent_working_set_bytes': 300,
            'telemetry_errors_count': 0,
        }

    def batch(self, samples=None):
        return {'schema_version': 1, 'machine_id': self.machine.machine_id, 'samples': samples or [self.sample()]}

    def post(self, batch, token=None):
        return self.client.post(
            self.ingest_url, data=batch, content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {token or self.token}',
        )

    def test_agent_auth_and_endpoint_binding(self):
        payload = self.batch()
        self.assertIn(self.client.post(self.ingest_url, data=payload, content_type='application/json').status_code, (401, 403))
        payload['machine_id'] = self.other.machine_id
        self.assertEqual(self.post(payload).status_code, 403)
        self.assertEqual(EndpointPerformanceSample.objects.count(), 0)

    def test_batch_persists_and_retry_is_idempotent(self):
        payload = self.batch()
        self.assertEqual(self.post(payload).status_code, 200)
        self.assertEqual(self.post(payload).status_code, 200)
        self.assertEqual(EndpointPerformanceSample.objects.count(), 1)
        row = EndpointPerformanceSample.objects.get()
        self.assertEqual(row.endpoint, self.machine)
        self.assertEqual(str(row.sample_id), payload['samples'][0]['sample_id'])
        self.assertEqual(row.cpu_percent, 11.5)
        self.assertEqual(row.process_consumers['cpu'][0]['process_name'], 'synthetic-worker')

    def test_invalid_schema_batch_size_and_fields(self):
        payload = self.batch()
        payload['schema_version'] = 2
        self.assertEqual(self.post(payload).status_code, 400)
        payload = self.batch([self.sample() for _ in range(25)])
        self.assertEqual(self.post(payload).status_code, 400)
        payload = self.batch()
        payload['samples'][0]['top_processes']['cpu'] *= 6
        self.assertEqual(self.post(payload).status_code, 400)
        payload = self.batch()
        payload['samples'][0]['command_line'] = 'never-store-user-content'
        self.assertEqual(self.post(payload).status_code, 400)
        self.assertEqual(EndpointPerformanceSample.objects.count(), 0)

    def test_rejects_oversized_request(self):
        payload = self.batch()
        payload['padding'] = 'x' * (256 * 1024)
        self.assertEqual(self.post(payload).status_code, 413)

    def test_query_requires_login_permission_and_orders_by_time(self):
        newer = self.sample()
        older = self.sample(timezone.now() - timedelta(hours=1))
        self.assertEqual(self.post(self.batch([older, newer])).status_code, 200)
        self.assertIn(self.client.get(self.query_url).status_code, (401, 403))
        user = get_user_model().objects.create_user(username='telemetry-viewer', password='synthetic-password')
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.query_url).status_code, 403)
        user.is_superuser = True
        user.is_staff = True
        user.save(update_fields=['is_superuser', 'is_staff'])
        response = self.client.get(self.query_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['sample_id'] for item in response.json()['samples']], [newer['sample_id'], older['sample_id']])
        self.assertEqual(len(self.client.get(self.query_url, {'limit': 1}).json()['samples']), 1)
        self.assertEqual(self.client.get(reverse('endpoint-telemetry', args=[self.other.pk])).json()['samples'], [])
        self.assertEqual(self.client.get(self.query_url, {'from': timezone.now().isoformat()}).json()['samples'], [])
        self.assertEqual(self.client.get(self.query_url, {'limit': 501}).status_code, 400)

    def test_existing_agent_paths_and_versions_are_unchanged(self):
        self.assertEqual(self.machine.agent_version, '0.1.1.0-rc40')
        self.assertEqual(self.other.agent_version, '0.1.1.0-rc39')
        self.assertEqual(EndpointPerformanceSample.objects.count(), 0)
