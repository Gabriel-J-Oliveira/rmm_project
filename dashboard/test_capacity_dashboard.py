import uuid
from datetime import timedelta
from decimal import Decimal
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
from .capacity_views import CAPACITY_PERMISSIONS, _compact_summary, _overview, _disk_usage, _inventory_map


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
        self.series_url = reverse('api-capacity-series', args=[self.endpoint.pk])

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
        self.assertEqual(self.client.get(self.series_url).status_code, 302)
        self.login()
        self.user.user_permissions.clear()
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.overview_url).status_code, 403)
        self.assertEqual(self.client.get(reverse('api-capacity-detail', args=[uuid.uuid4()])).status_code, 403)
        self.assertEqual(self.client.get(reverse('api-capacity-series', args=[uuid.uuid4()])).status_code, 403)

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
        self.assertEqual(row['alert_types'][0]['alert_type'], 'synthetic')
        self.assertEqual(row['alerts_warning'], 0)
        self.assertFalse(row['inventory_stale'])
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

    def test_overview_identity_inventory_and_last_telemetry(self):
        now = timezone.now()
        self.endpoint.first_seen_at = now - timedelta(hours=2)
        self.endpoint.last_ip = '192.0.2.12'
        self.endpoint.last_logged_user = 'synthetic-user'
        self.endpoint.fqdn = 'lab.example.test'
        self.endpoint.save()
        snapshot = self.snapshot()
        snapshot.manufacturer = 'Synthetic maker'
        snapshot.model = 'Synthetic model'
        snapshot.disks = [{'name': 'C:\\', 'size_bytes': 1000, 'free_bytes': 200},
                          {'name': 'D:', 'size_bytes': 2000, 'free_bytes': 100}]
        snapshot.save()
        # Old samples remain visible even outside the selected/context windows.
        sample = self.sample(collected_at=now - timedelta(days=9))
        row = _overview(now, 24)[0]
        self.assertEqual(row['first_seen'], self.endpoint.first_seen_at.isoformat())
        self.assertEqual(row['last_ip'], '192.0.2.12')
        self.assertEqual(row['last_logged_user'], 'synthetic-user')
        self.assertEqual(row['fqdn'], 'lab.example.test')
        self.assertEqual(row['manufacturer'], 'Synthetic maker')
        self.assertEqual(row['model'], 'Synthetic model')
        self.assertEqual(row['inventory_at'], snapshot.collected_at.isoformat())
        self.assertEqual(row['telemetry_last_at'], sample.collected_at.isoformat())
        self.assertEqual(row['received_samples'], 0)
        self.assertEqual(row['system_disk_used_percent'], 80)
        self.assertEqual(row['max_disk_used_percent'], 95)
        self.assertEqual(row['min_disk_free_bytes'], 100)
        self.assertEqual(row['disk_count'], 2)

    def test_invalid_disks_never_fabricate_zero(self):
        snapshot = self.snapshot()
        for disks in (None, [], {}, [{'name': 'C:', 'size_bytes': 0, 'free_bytes': 0}],
                      [{'name': 'C:', 'size_bytes': 100, 'free_bytes': 101}],
                      [{'name': 'C:', 'size_bytes': 100, 'free_bytes': -1}],
                      [{'name': 'C:', 'size_bytes': True, 'free_bytes': 0}],
                      [{'name': 'C:', 'size_bytes': '100', 'free_bytes': 0}],
                      [{'name': 'C:', 'size_bytes': float('nan'), 'free_bytes': 0}]):
            with self.subTest(disks=disks):
                snapshot.disks = disks
                self.assertTrue(all(v is None for v in _disk_usage(snapshot).values()))
        snapshot.disks = [{'name': 'Recovery', 'size_bytes': 100, 'free_bytes': 100}]
        facts = _disk_usage(snapshot)
        self.assertIsNone(facts['system_disk_used_percent'])
        self.assertEqual(facts['max_disk_used_percent'], 0)

    def test_overview_queries_do_not_grow_with_fleet(self):
        now = timezone.now()
        self.snapshot()
        self.sample()
        with CaptureQueriesContext(connection) as small:
            _overview(now, 24)
        for i in range(40):
            self.make_endpoint(f'SYNTHETIC-{i}')
        with CaptureQueriesContext(connection) as fleet:
            rows = _overview(now, 24)
        self.assertEqual(len(rows), 41)
        self.assertEqual(len(small), len(fleet))
        self.assertLessEqual(len(fleet), 5)

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
            'first_sample_at': sample.collected_at, 'last_sample_at': sample.collected_at,
        }
        projected = _compact_summary(self.endpoint, start, end, aggregate)
        self.assertEqual(projected['quality']['first_sample_at'], expected['quality']['first_sample_at'])
        self.assertEqual(projected['quality']['last_sample_at'], expected['quality']['last_sample_at'])
        self.assertEqual(build_resource_diagnostics(projected), build_resource_diagnostics(expected))

    def test_compact_projection_without_samples_is_not_evaluated(self):
        end = timezone.now()
        projected = _compact_summary(self.endpoint, end - timedelta(hours=24), end, None)
        diagnostics = build_resource_diagnostics(projected)
        self.assertEqual(diagnostics['diagnostics']['cpu']['status'], 'NOT_EVALUATED')
        self.assertEqual(diagnostics['diagnostics']['memory']['status'], 'NOT_EVALUATED')

    def test_postgresql_decimal_statistics_are_numeric_for_frontend(self):
        end = timezone.now()
        projected = _compact_summary(self.endpoint, end - timedelta(hours=24), end, {
            'received': 1, 'cpu_avg': Decimal('12.5'), 'cpu_p95': Decimal('18.2'),
            'cpu_p99': Decimal('19.3'),
        })
        self.assertIsInstance(projected['cpu']['usage_percent']['avg'], float)
        self.assertEqual(projected['cpu']['usage_percent']['p95'], 18.2)

    def test_detail_is_lazy_and_exposes_only_selected_endpoint(self):
        self.login()
        self.snapshot()
        self.sample()
        self.sample(collected_at=timezone.now() - timedelta(minutes=10))
        other = self.make_endpoint('LAB-OTHER')
        self.sample(endpoint=other, cpu_percent=97)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['endpoint']['id'], str(self.endpoint.pk))
        self.assertNotIn('series', data)
        self.assertTrue(data['series_ranges']['24h'])
        series = self.client.get(self.series_url).json()['series']
        self.assertEqual(len(series), 2)
        self.assertEqual(series[0]['cpu'], 25)
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
        self.assertNotIn('series', detail)
        self.assertFalse(detail['series_ranges']['7d'])
        self.assertEqual(self.client.get(self.series_url).json()['series'], [])

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

    def test_alert_categories_count_endpoints_and_no_alerts_remain_zero(self):
        self.login()
        EndpointAlert.objects.create(endpoint=self.endpoint, alert_type='agent.update.failed',
                                     severity=EndpointAlert.SEVERITY_CRITICAL,
                                     title='Synthetic update', description='Synthetic only')
        EndpointAlert.objects.create(endpoint=self.endpoint, alert_type='agent.update.failed',
                                     severity=EndpointAlert.SEVERITY_CRITICAL,
                                     title='Synthetic second update', description='Synthetic only')
        EndpointAlert.objects.create(endpoint=self.endpoint, alert_type='antivirus_disabled',
                                     severity=EndpointAlert.SEVERITY_SECURITY,
                                     title='Synthetic security', description='Synthetic only')
        second = self.make_endpoint('LAB-NO-ALERTS')
        rows = {item['id']: item for item in self.client.get(self.overview_url).json()['endpoints']}
        self.assertEqual(rows[str(self.endpoint.pk)]['alerts_critical'], 2)
        self.assertTrue(rows[str(self.endpoint.pk)]['security_alert'])
        self.assertTrue(rows[str(self.endpoint.pk)]['agent_update_alert'])
        self.assertEqual(rows[str(second.pk)]['alerts_total'], 0)

    def test_alert_summary_counts_all_records_when_timeline_is_limited(self):
        self.login()
        for index in range(31):
            EndpointAlert.objects.create(
                endpoint=self.endpoint, alert_type='synthetic',
                severity=EndpointAlert.SEVERITY_CRITICAL,
                title=f'Synthetic alert {index}', description='Synthetic only',
            )
        detail = self.client.get(self.detail_url).json()
        self.assertEqual(detail['alert_counts']['critical'], 31)
        self.assertEqual(len(detail['alerts']), 30)

    def test_disk_association_requires_confidence(self):
        self.login()
        self.snapshot(hardware={
            'cpu': {'name': 'Synthetic CPU', 'physical_cores': 4, 'logical_processors': 8},
            'memory_total_bytes': 17179869184,
            'memory': {'total_bytes': 17179869184},
            'physical_disks': [
                {'model': 'Synthetic SSD', 'size_bytes': 1000, 'drive_letters': ['C:'],
                 'association_confidence': 'none', 'health_status': None},
                {'model': 'Synthetic HDD', 'size_bytes': 2000, 'drive_letters': ['D:'],
                 'association_confidence': 'high', 'is_system_disk': False},
            ],
        })
        disks = self.client.get(self.detail_url).json()['disks']
        self.assertEqual(disks[0]['drive_letters'], [])
        self.assertIsNone(disks[0]['is_system_disk'])
        self.assertEqual(disks[1]['drive_letters'], ['D:'])

    def test_series_is_lazy_bounded_and_read_only(self):
        self.login()
        self.sample()
        with CaptureQueriesContext(connection) as detail_queries:
            detail = self.client.get(self.detail_url)
        self.assertNotIn('series', detail.json())
        self.assertFalse(any('process_consumers' in q['sql'].lower() and 'cpu_percent' in q['sql'].lower()
                             for q in detail_queries))
        with CaptureQueriesContext(connection) as series_queries:
            response = self.client.get(self.series_url, {'period': '6h'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['series']), 1)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE '))
                             for q in series_queries))
        self.assertEqual(self.client.get(self.series_url, {'period': 'all'}).status_code, 400)

    def test_detailed_inventory_survives_heartbeat_and_preserves_collection_date(self):
        from agents.services import record_collection, record_heartbeat
        self.login()
        collected = timezone.now() - timedelta(hours=2)
        payload = {'hostname': self.endpoint.hostname, 'collected_at': collected.isoformat(),
            'hardware': {'cpu': {'name': 'Inventory CPU', 'physical_cores': 6, 'logical_processors': 12},
                         'memory_total_bytes': 17179869184, 'manufacturer': 'Inventory Maker',
                         'memory': {'total_bytes': 17179869184, 'slots_total': 2, 'slots_used': 1, 'slots_free': 1, 'modules': [{}]}},
            'disk': [{'name': 'C:', 'size_bytes': 1000, 'free_bytes': 200}],
            'system': {'os': {'name': 'Inventory OS'}}}
        snapshot = record_collection(self.endpoint, 'full_inventory', payload)
        # Exercise legacy snapshots too: inherited heartbeat keys are not a source marker.
        raw = snapshot.raw_payload; raw.pop('snapshot_source'); raw['heartbeat_at'] = (collected - timedelta(minutes=1)).isoformat()
        snapshot.raw_payload = raw; snapshot.save(update_fields=['raw_payload'])
        heartbeat = {'hostname': self.endpoint.hostname, 'heartbeat_at': timezone.now().isoformat(),
                     'hardware': {'cpu': 'Heartbeat CPU'}, 'disks': []}
        record_heartbeat(self.endpoint, heartbeat, heartbeat)
        rows = self.client.get(self.overview_url).json()['endpoints']
        row = next(item for item in rows if item['id'] == str(self.endpoint.pk))
        self.assertEqual(row['cpu_name'], 'Inventory CPU')
        self.assertEqual(row['cpu_cores'], 6)
        self.assertEqual(row['disk_count'], 1)
        self.assertEqual(row['system_disk_used_percent'], 80)
        self.assertEqual(row['inventory_at'], collected.isoformat())
        detail = self.client.get(self.detail_url).json()
        self.assertEqual(detail['hardware']['memory']['slots_total'], 2)
        self.assertEqual(detail['inventory_provenance']['snapshot_id'], str(snapshot.pk))
        self.assertEqual(detail['inventory_provenance']['collected_at'], collected.isoformat())
        self.assertEqual(InventorySnapshot.objects.filter(machine=self.endpoint).count(), 2)

    def test_heartbeat_only_is_not_full_inventory(self):
        from agents.services import record_heartbeat
        self.login()
        payload = {'hostname': self.endpoint.hostname, 'hardware': {'cpu': 'Heartbeat CPU'}}
        record_heartbeat(self.endpoint, payload, payload)
        row = self.client.get(self.overview_url).json()['endpoints'][0]
        self.assertIsNone(row['inventory_at'])
        self.assertIsNone(row['disk_count'])
        self.assertIsNone(row['memory_total_bytes'])

    def test_new_endpoint_without_lifecycle_or_telemetry_is_in_overview_api(self):
        self.login()
        self.snapshot()
        self.endpoint.agent_lifecycle_status = ''
        self.endpoint.save(update_fields=['agent_lifecycle_status'])
        row = next(r for r in self.client.get(self.overview_url).json()['endpoints']
                   if r['id'] == str(self.endpoint.pk))
        self.assertEqual(row['received_samples'], 0)
        self.assertIsNone(row['cpu_p95'])
        self.assertIsNone(row['memory_p95'])
        self.assertEqual(row['memory_total_bytes'], 17179869184)
        self.assertIsNotNone(row['inventory_at'])

    def test_full_inventory_reference_survives_partial_collect_and_legacy_heartbeat(self):
        from agents.services import record_collection, record_heartbeat
        collected = timezone.now() - timedelta(hours=2)
        timestamp = collected.isoformat().replace('+00:00', '7+00:00')
        snapshot = record_collection(self.endpoint, 'full_inventory', {
            'hostname': self.endpoint.hostname, 'collected_at': timestamp,
            'hardware': {'cpu': {'name': 'Detailed CPU'}},
            'disk': [{'name': 'C:', 'size_bytes': 1000, 'free_bytes': 100}],
        })
        record_collection(self.endpoint, 'patches', {
            'hostname': self.endpoint.hostname, 'collected_at': timezone.now().isoformat(),
            'pending_updates_count': 0,
        })
        payload = {'hostname': self.endpoint.hostname, 'heartbeat_at': timezone.now().isoformat()}
        heartbeat = record_heartbeat(self.endpoint, payload, payload)
        heartbeat.raw_payload.pop('snapshot_source')
        heartbeat.save(update_fields=['raw_payload'])
        with CaptureQueriesContext(connection) as queries:
            inventory = _inventory_map([self.endpoint.pk])
        self.assertEqual(inventory[self.endpoint.pk].pk, snapshot.pk)
        self.assertEqual(inventory[self.endpoint.pk].collected_at, collected)
        self.assertEqual(len(inventory[self.endpoint.pk].disks), 1)
        self.assertEqual(len(queries), 2)
        self.assertNotIn('raw_payload', queries[-1]['sql'].split('WHERE')[-1])

    def test_invalid_full_reference_does_not_scan_or_promote_heartbeat_history(self):
        from agents.services import record_heartbeat
        for timestamp in ('invalid', '2026-99-99T00:00:00Z', None, '2026-01-01T00:00:00'):
            with self.subTest(timestamp=timestamp):
                payload = {'hostname': self.endpoint.hostname,
                           'collections': {'full_inventory': {'collected_at': timestamp}},
                           'latest_collection_type': 'full_inventory'}
                record_heartbeat(self.endpoint, payload, payload)
                with CaptureQueriesContext(connection) as queries:
                    self.assertEqual(_inventory_map([self.endpoint.pk]), {})
                self.assertEqual(len(queries), 1)
