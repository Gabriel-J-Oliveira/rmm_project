from datetime import timedelta
from time import perf_counter

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from agents.models import AgentMachine, EndpointAlert, InventorySnapshot


class DashboardInitialLoadPerformanceTests(TestCase):
    def setUp(self):
        self.operator = get_user_model().objects.create_superuser(
            'dashboard-performance', password='synthetic-only',
        )
        self.client.force_login(self.operator)
        self.created = 0

    def add_endpoints(self, count):
        now = timezone.now()
        for _ in range(count):
            hostname = f'DASH-{self.created:03}'
            endpoint, _ = AgentMachine.create_with_token(
                hostname=hostname,
                machine_id=f'dashboard-performance-{self.created}',
                status=AgentMachine.STATUS_ONLINE,
                os_name='Windows 11 Pro',
                last_logged_user='CONTROL\\synthetic',
                last_seen_at=now,
            )
            InventorySnapshot.objects.create(
                machine=endpoint,
                hostname=hostname,
                collected_at=now - timedelta(minutes=1),
                received_at=now - timedelta(minutes=1),
                os_name='Windows 11 Pro',
                disks=[{'name': 'C:', 'size_bytes': 100, 'free_bytes': 50}],
                raw_payload={'collections': {'system': {'product_type': 1}}},
            )
            self.created += 1

    def count_dashboard_queries(self):
        started = perf_counter()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('dashboard:index'))
        elapsed_ms = (perf_counter() - started) * 1000
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['pulse_health']['online'], self.created)
        self.assertEqual(len(response.context['pulse_endpoint_rows']), self.created)
        return len(queries), elapsed_ms

    def test_query_count_stays_bounded_as_fleet_grows(self):
        self.add_endpoints(5)
        small_fleet_queries, small_fleet_ms = self.count_dashboard_queries()

        self.add_endpoints(25)
        large_fleet_queries, large_fleet_ms = self.count_dashboard_queries()

        self.assertEqual(
            large_fleet_queries,
            small_fleet_queries,
            f'queries grew with fleet size: {small_fleet_queries} -> {large_fleet_queries}',
        )
        print(
            'dashboard_synthetic_performance='
            f'{{"hosts_small":5,"queries_small":{small_fleet_queries},"ms_small":{small_fleet_ms:.1f},'
            f'"hosts_large":30,"queries_large":{large_fleet_queries},"ms_large":{large_fleet_ms:.1f}}}'
        )

    def test_dashboard_uses_one_release_lookup_and_preserves_alert_counts(self):
        self.add_endpoints(2)
        first, second = AgentMachine.objects.order_by('hostname')
        now = timezone.now()
        EndpointAlert.objects.create(
            endpoint=first, alert_type='synthetic-critical', severity=EndpointAlert.SEVERITY_CRITICAL,
            title='Open critical', description='Synthetic', status=EndpointAlert.STATUS_OPEN,
            first_seen_at=now - timedelta(minutes=3), last_seen_at=now - timedelta(minutes=1),
        )
        EndpointAlert.objects.create(
            endpoint=second, alert_type='synthetic-warning', severity=EndpointAlert.SEVERITY_WARNING,
            title='Open warning', description='Synthetic', status=EndpointAlert.STATUS_OPEN,
            first_seen_at=now - timedelta(minutes=4), last_seen_at=now - timedelta(minutes=2),
        )
        EndpointAlert.objects.create(
            endpoint=first, alert_type='synthetic-muted', severity=EndpointAlert.SEVERITY_CRITICAL,
            title='Muted critical', description='Synthetic', status=EndpointAlert.STATUS_OPEN,
            muted_until=now + timedelta(hours=1),
        )
        EndpointAlert.objects.create(
            endpoint=second, alert_type='synthetic-resolved', severity=EndpointAlert.SEVERITY_CRITICAL,
            title='Resolved critical', description='Synthetic', status=EndpointAlert.STATUS_RESOLVED,
        )

        from unittest.mock import patch

        with patch('dashboard.views.latest_agent_version', return_value='0.1.1.0-rc46') as version_lookup:
            response = self.client.get(reverse('dashboard:index'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['open_alerts_count'], 2)
        self.assertEqual(response.context['critical_alerts_count'], 1)
        self.assertEqual(
            [alert.title for alert in response.context['recent_alerts']],
            ['Open critical', 'Open warning'],
        )
        version_lookup.assert_called_once_with()
