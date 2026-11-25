import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from agents.alert_policy_engine import evaluate_policies
from agents.alerting import evaluate_all_alerts
from agents.models import AgentMachine, AlertPolicy, EndpointAlert, EndpointPerformanceSample, InventorySnapshot


class AlertPolicyEngineTests(TestCase):
    def setUp(self):
        self.endpoint = AgentMachine.objects.create(
            hostname='desktop', agent_token_hash='a' * 64, status=AgentMachine.STATUS_ONLINE,
        )
        self.now = timezone.now().replace(microsecond=0)
        self.rules = list(AlertPolicy.objects.filter(enabled=True).prefetch_related('endpoints'))

    def snapshot(self, *, age=0, free=0):
        return InventorySnapshot.objects.create(
            machine=self.endpoint, hostname=self.endpoint.hostname,
            collected_at=self.now - timedelta(minutes=age),
            disks=[{'name': 'C:', 'size_bytes': 100, 'free_bytes': free}],
        )

    def evaluate(self, **kwargs):
        with patch('agents.alert_policy_engine.timezone.now', return_value=self.now):
            return evaluate_policies(self.endpoint, self.rules, **kwargs)

    def test_preserves_legacy_ack_and_requires_fresh_recovery(self):
        old = self.endpoint.alerts.create(
            alert_type='disk_low', severity='warning', title='Disco', description='Alerta legado',
            status=EndpointAlert.STATUS_ACKNOWLEDGED, acknowledged_at=self.now,
            metadata={'dedupe_key': 'disk_low:C:'}, muted_until=self.now + timedelta(hours=1),
        )
        self.snapshot(free=None)
        self.evaluate()
        old.refresh_from_db()
        self.assertEqual(old.source, '')
        self.now += timedelta(minutes=1)
        self.snapshot()
        self.assertEqual(self.evaluate(dry_run=True)[1]['updated'], 1)
        old.refresh_from_db()
        self.assertEqual(old.source, '')
        self.evaluate()
        old.refresh_from_db()
        self.assertEqual(old.source, 'alert_policy')
        self.assertEqual(old.severity, 'critical')
        self.assertEqual(old.status, EndpointAlert.STATUS_ACKNOWLEDGED)
        self.assertIsNotNone(old.muted_until)
        self.assertEqual(self.endpoint.alerts.count(), 1)
        self.now += timedelta(days=2)
        self.snapshot(age=1441, free=30)
        self.evaluate()
        old.refresh_from_db()
        self.assertEqual(old.status, EndpointAlert.STATUS_ACKNOWLEDGED)
        self.snapshot(free=30)
        self.evaluate()
        old.refresh_from_db()
        self.assertEqual(old.status, EndpointAlert.STATUS_RESOLVED)

    def test_hysteresis_preserves_critical_until_lower_threshold(self):
        self.snapshot(free=2)
        self.evaluate()
        for free, severity in [(8, 'critical'), (10, 'warning'), (20, 'warning')]:
            self.now += timedelta(minutes=1)
            self.snapshot(free=free)
            self.evaluate()
            alert = self.endpoint.alerts.get()
            self.assertEqual(alert.severity, severity)
        self.assertEqual(alert.status, EndpointAlert.STATUS_RESOLVED)

    def test_sustained_signal_needs_consecutive_qualified_windows(self):
        for offset in range(16):
            end = self.now - timedelta(minutes=offset)
            EndpointPerformanceSample.objects.create(
                endpoint=self.endpoint, sample_id=uuid.uuid4(), schema_version=2, resolution_seconds=60,
                collected_at=end - timedelta(minutes=1), window_end_at=end, collection_duration_ms=60000,
                metric_summaries={'metrics': {'cpu.usage_percent': {'min': 96, 'max': 99, 'count': 4 if offset == 5 else 6}}},
            )
        self.evaluate()
        self.assertFalse(self.endpoint.alerts.exists())
        row = self.endpoint.performance_samples.get(window_end_at=self.now - timedelta(minutes=5))
        row.metric_summaries['metrics']['cpu.usage_percent']['count'] = 5
        row.save(update_fields=['metric_summaries'])
        self.evaluate()
        self.evaluate()
        self.assertEqual(self.endpoint.alerts.get().severity, 'warning')
        self.endpoint.performance_samples.filter(window_end_at=self.now).delete()
        self.now += timedelta(minutes=10)
        self.evaluate()
        self.assertEqual(self.endpoint.alerts.get().status, EndpointAlert.STATUS_OPEN)

    def test_heartbeat_with_inherited_disk_does_not_refresh_evidence(self):
        row = self.snapshot()
        row.raw_payload = {
            'snapshot_source': 'heartbeat',
            'heartbeat_inventory': {'inherited': {
                'disks': {'collected_at': (self.now - timedelta(days=2)).isoformat()},
            }},
        }
        row.save(update_fields=['raw_payload'])
        self.evaluate()
        self.assertFalse(self.endpoint.alerts.exists())

    def test_offline_recovery_requires_known_online_state(self):
        self.endpoint.last_seen_at = self.now - timedelta(hours=80)
        self.endpoint.status = AgentMachine.STATUS_OFFLINE
        self.endpoint.save(update_fields=['status', 'last_seen_at'])
        self.evaluate()
        alert = self.endpoint.alerts.get()
        self.assertEqual(alert.severity, 'critical')
        self.endpoint.status = AgentMachine.STATUS_UNKNOWN
        self.endpoint.save(update_fields=['status'])
        self.evaluate()
        alert.refresh_from_db()
        self.assertEqual(alert.status, EndpointAlert.STATUS_OPEN)
        self.endpoint.status = AgentMachine.STATUS_ONLINE
        self.endpoint.last_seen_at = self.now
        self.endpoint.save(update_fields=['status', 'last_seen_at'])
        self.evaluate()
        alert.refresh_from_db()
        self.assertEqual(alert.status, EndpointAlert.STATUS_RESOLVED)

    def test_scheduler_keeps_policy_and_legacy_from_creating_duplicate_disk_alerts(self):
        self.snapshot(free=1)
        options = {'offline_warning_hours': 1, 'offline_critical_hours': 24, 'disk_critical_free_percent': 10,
                   'disk_warning_free_percent': 15, 'uptime_warning_days': 15, 'uptime_critical_days': 30,
                   'stale_inventory_hours': 24}
        evaluate_all_alerts(options)
        evaluate_all_alerts(options)
        self.assertEqual(self.endpoint.alerts.filter(alert_type='disk_low').count(), 1)


class NocFragmentTests(TestCase):
    def test_fragment_shares_permissions_and_excludes_inactive_endpoints(self):
        user = get_user_model().objects.create_user('restricted', is_staff=True)
        self.client.force_login(user)
        url = reverse('noc')
        self.assertEqual(self.client.get(url, {'fragment': '1'}).status_code, 403)
        admin = get_user_model().objects.create_superuser('noc-admin')
        self.client.force_login(admin)
        self.assertContains(self.client.get(url), 'data-noc-content')
        archived = AgentMachine.objects.create(
            hostname='archived-only', is_active=False, agent_token_hash='b' * 64,
            status=AgentMachine.STATUS_OFFLINE,
        )
        archived.alerts.create(alert_type='disk_low', severity='critical', title='archived-only', description='old')
        active = AgentMachine.objects.create(hostname='active-real', agent_token_hash='c' * 64)
        alert = active.alerts.create(alert_type='disk_low', severity='critical', title='Real', description='real')
        result = self.client.get(url, {'fragment': '1', 'mock': '1'})
        self.assertEqual(result.status_code, 200)
        self.assertIn('active-real', result.json()['html'])
        self.assertIn('Espaço livre desconhecido', result.json()['html'])
        self.assertNotIn('archived-only', result.json()['html'])
        self.assertIn('no-store', result.headers['Cache-Control'])
        alert.source = 'alert_policy'
        alert.save(update_fields=['source'])
        self.client.force_login(user)
        self.assertEqual(self.client.post(reverse('alert-acknowledge', args=[alert.pk])).status_code, 403)
