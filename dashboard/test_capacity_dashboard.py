import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from agents.models import AgentJob, AgentMachine, EndpointAlert, EndpointPerformanceSample, InventorySnapshot, hash_agent_token
from agents.telemetry_analytics import build_endpoint_telemetry_summary
from agents.telemetry_diagnostics import build_resource_diagnostics
from .capacity_views import CAPACITY_PERMISSIONS, _compact_summary, _overview


class CapacityDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='capacity-reviewer', password='synthetic-password', is_staff=True,
        )
        self.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='agents', codename__in=[name.split('.')[1] for name in CAPACITY_PERMISSIONS],
        ))
        self.endpoint = self.make_endpoint('LAB-CAPACITY')
        self.overview_url = reverse('api-capacity-overview')
        self.detail_url = reverse('api-capacity-detail', args=[self.endpoint.pk])

    def make_endpoint(self, hostname):
        return AgentMachine.objects.create(
            hostname=hostname, status=AgentMachine.STATUS_ONLINE, agent_version='0.1.1.0-rc44',
            machine_id=str(uuid.uuid4()), agent_token_hash=hash_agent_token(f'synthetic-{hostname}-credential'),
        )

    def sample(self, endpoint=None, **values):
        data = {
            'endpoint': endpoint or self.endpoint, 'sample_id': uuid.uuid4(),
            'collected_at': timezone.now() - timedelta(minutes=5),
            'cpu_percent': 25, 'memory_used_percent': 40, 'memory_committed_percent': 45,
            'uptime_seconds': 3600, 'collection_duration_ms': 20,
            'process_consumers': {'cpu': [{'process_name': 'synthetic-worker', 'cpu_percent': 12, 'working_set_bytes': 1024}], 'memory': []},
        }
        data.update(values)
        return EndpointPerformanceSample.objects.create(**data)

    def snapshot(self, hardware=None):
        hardware = hardware or {
            'cpu': {'name': 'Synthetic CPU', 'physical_cores': 4, 'logical_processors': 8},
            'memory_total_bytes': 17179869184,
            'memory': {'total_bytes': 17179869184, 'slots_total': 2, 'slots_used': 1,
                       'slots_free': 1, 'modules': [{}]},
        }
        return InventorySnapshot.objects.create(
            machine=self.endpoint, collected_at=timezone.now(), hostname=self.endpoint.hostname,
            os_name='Windows Server', cpu='Synthetic CPU', memory_total_bytes=17179869184,
            raw_payload={'collections': {'hardware': hardware}},
            installed_software=[{'name': 'Synthetic App', 'version': '1.0'}],
        )

    def login(self):
        self.client.force_login(self.user)

    def test_authentication_and_permissions_precede_endpoint_lookup(self):
        self.assertEqual(self.client.get(reverse('capacity-page')).status_code, 302)
        self.assertEqual(self.client.get(self.overview_url).status_code, 302)
        self.login()
        self.user.user_permissions.clear()
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.overview_url).status_code, 403)
        self.assertEqual(self.client.get(reverse('api-capacity-detail', args=[uuid.uuid4()])).status_code, 403)

    def test_page_uses_existing_navigation_and_endpoint_link(self):
        self.login()
        response = self.client.get(reverse('capacity-page'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Capacidade &amp; Planejamento')
        self.assertContains(response, reverse('alerts-list'))
        self.assertContains(response, 'data-detail-template')

    def test_real_overview_uses_m5_and_only_real_fields(self):
        self.login()
        self.snapshot()
        self.sample()
        EndpointAlert.objects.create(endpoint=self.endpoint, alert_type='synthetic',
                                     severity=EndpointAlert.SEVERITY_CRITICAL,
                                     title='Synthetic alert', description='Synthetic only')
        before = (AgentJob.objects.count(), EndpointPerformanceSample.objects.count(), InventorySnapshot.objects.count())
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.overview_url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['demo'])
        self.assertEqual(data['counts']['monitored'], 1)
        self.assertEqual(data['counts']['critical'], 1)
        row = data['endpoints'][0]
        self.assertEqual(row['id'], str(self.endpoint.pk))
        self.assertEqual(row['endpoint_url'], reverse('endpoint-detail', args=[self.endpoint.pk]))
        self.assertEqual(row['cpu_name'], 'Synthetic CPU')
        self.assertEqual(row['alerts_critical'], 1)
        self.assertEqual(row['cpu_capacity'], 'NOT_EVALUATED')
        self.assertNotIn('series', row)
        self.assertEqual(before, (AgentJob.objects.count(), EndpointPerformanceSample.objects.count(), InventorySnapshot.objects.count()))
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE '))
                             for query in queries))

    def test_batch_overview_has_fixed_query_count_and_matches_m4(self):
        self.sample()
        now = timezone.now()
        with CaptureQueriesContext(connection) as one:
            first = _overview(now, 24)
        self.make_endpoint('LAB-SECOND')
        with CaptureQueriesContext(connection) as two:
            second = _overview(now, 24)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 2)
        self.assertEqual(len(one), len(two))
        self.assertLessEqual(len(two), 5)
        rows = list(EndpointPerformanceSample.objects.filter(endpoint=self.endpoint)
                    .order_by('collected_at', 'id')
                    .values_list('collected_at', 'created_at', 'cpu_percent', 'memory_used_percent',
                                 'memory_committed_percent', 'network_received_bytes', 'network_sent_bytes',
                                 'collection_duration_ms', 'agent_working_set_bytes', 'telemetry_errors_count'))
        expected = build_endpoint_telemetry_summary(self.endpoint, now - timedelta(hours=24), now)
        batched = build_endpoint_telemetry_summary(
            self.endpoint, now - timedelta(hours=24), now, sample_rows=rows,
        )
        self.assertEqual(batched, expected)

    def test_compact_aggregate_projection_preserves_m5_facts(self):
        sample = self.sample(cpu_percent=72, memory_used_percent=82,
                             memory_committed_percent=84, telemetry_errors_count=1)
        start = sample.collected_at - timedelta(minutes=5)
        end = sample.collected_at + timedelta(microseconds=1)
        expected = build_endpoint_telemetry_summary(self.endpoint, start, end)
        aggregate = {
            'received': 1, 'cpu_valid': 1, 'cpu_avg': 72, 'cpu_p95': 72, 'cpu_p99': 72,
            'used_valid': 1, 'used_avg': 82, 'used_p95': 82, 'used_p99': 82,
            'committed_valid': 1, 'committed_avg': 84, 'committed_p95': 84, 'committed_p99': 84,
            'gaps': 0, 'negative_lags': expected['quality']['negative_lag_samples'],
            'error_samples': 1, 'total_errors': 1,
        }
        projected = _compact_summary(self.endpoint, start, end, aggregate)
        self.assertEqual(build_resource_diagnostics(projected), build_resource_diagnostics(expected))

    def test_compact_projection_without_samples_is_not_evaluated(self):
        end = timezone.now()
        projected = _compact_summary(self.endpoint, end - timedelta(hours=24), end, None)
        diagnostics = build_resource_diagnostics(projected)
        self.assertEqual(diagnostics['diagnostics']['cpu']['status'], 'NOT_EVALUATED')
        self.assertEqual(diagnostics['diagnostics']['memory']['status'], 'NOT_EVALUATED')

    def test_detail_is_lazy_and_exposes_only_selected_endpoint(self):
        self.login()
        self.snapshot()
        self.sample()
        other = self.make_endpoint('LAB-OTHER')
        self.sample(endpoint=other, cpu_percent=97)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['endpoint']['id'], str(self.endpoint.pk))
        self.assertEqual(len(data['series']), 1)
        self.assertEqual(data['series'][0]['cpu'], 25)
        self.assertEqual(data['applications'][0]['name'], 'Synthetic App')
        self.assertEqual(data['processes'][0]['name'], 'synthetic-worker')
        self.assertEqual(data['hardware']['memory']['slots_free'], 1)
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE '))
                             for query in queries))

    def test_missing_inventory_and_telemetry_degrade_gracefully(self):
        self.login()
        overview = self.client.get(self.overview_url).json()
        row = overview['endpoints'][0]
        self.assertEqual(row['cpu_capacity'], 'NOT_EVALUATED')
        self.assertIsNone(row['cpu_p95'])
        self.assertIsNone(row['cpu_name'])
        detail = self.client.get(self.detail_url).json()
        self.assertEqual(detail['capacity']['hardware_context']['cpu']['status'], 'MISSING')
        self.assertEqual(detail['series'], [])

    def test_demo_is_opt_in_and_cannot_activate_outside_debug(self):
        self.login()
        with override_settings(DEBUG=True):
            regular = self.client.get(self.overview_url).json()
            demo = self.client.get(self.overview_url, {'demo': '1'}).json()
            self.assertFalse(regular['demo'])
            self.assertTrue(demo['demo'])
            self.assertEqual(len(demo['endpoints']), 6)
            self.assertEqual({row['cpu_capacity'] for row in demo['endpoints']},
                             {'NO_PRESSURE_OBSERVED', 'OBSERVE', 'SUSTAINED_PRESSURE', 'NOT_EVALUATED'})
            self.assertContains(self.client.get(reverse('capacity-page') + '?demo=1'), 'Dados demonstrativos')
        with override_settings(DEBUG=False):
            response = self.client.get(self.overview_url, {'demo': '1'}).json()
            self.assertFalse(response['demo'])
            self.assertEqual(len(response['endpoints']), 1)
            self.assertNotContains(self.client.get(reverse('capacity-page') + '?demo=1'), 'Dados demonstrativos')

    def test_invalid_period_and_unknown_endpoint(self):
        self.login()
        self.assertEqual(self.client.get(self.overview_url, {'period': 'unbounded'}).status_code, 400)
        self.assertEqual(self.client.get(self.detail_url, {'period': 'unbounded'}).status_code, 400)
        self.assertEqual(self.client.get(reverse('api-capacity-detail', args=[uuid.uuid4()])).status_code, 404)

    def test_missing_optional_fields_do_not_create_fake_impact(self):
        self.login()
        self.sample(process_consumers={})
        row = self.client.get(self.overview_url).json()['endpoints'][0]
        self.assertEqual(row['processes'], [])
        self.assertNotIn('applications', row)
