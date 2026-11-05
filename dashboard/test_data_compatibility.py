from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from agents.models import AgentMachine, AgentOperationalStatus, AgentJob
from agents.services import _normalize_disk_rows, record_collection, record_heartbeat
from agents.telemetry_continuous import continuous_summary
from dashboard.views import build_disk_rows, build_endpoint_detail_payload, _serialize_agent_diagnostic


class DataCompatibilityTests(TestCase):
    def setUp(self):
        self.machine, _ = AgentMachine.create_with_token(hostname='SYNTH-COMPAT', machine_id='synthetic-compat')

    def test_missing_free_and_actual_zero_remain_distinct(self):
        one = 1024 ** 3
        normalized = _normalize_disk_rows([{'name': 'C:', 'size_bytes': one}, {'name': 'D:', 'size_bytes': one, 'free_bytes': 0}])
        self.assertIsNone(normalized[0]['free_bytes'])
        self.assertIsNone(normalized[0]['used_bytes'])
        self.assertEqual(normalized[1]['free_bytes'], 0)
        rows = build_disk_rows(normalized)
        self.assertIsNone(rows[0]['used_percent'])
        self.assertEqual(rows[0]['level'], 'unknown')
        self.assertEqual(rows[1]['used_percent'], 100)
        self.assertEqual(rows[1]['free_gb'], 0)
        self.assertEqual(rows[1]['size_gb'], 1)
        self.assertEqual(rows[1]['level'], 'critical')

    def test_absent_diagnostic_is_not_zero_and_real_zero_is_preserved(self):
        empty = _serialize_agent_diagnostic(self.machine, None, [], '', can_view_technical=True)
        self.assertIsNone(empty['queue']['pending_count'])
        self.assertIsNone(empty['queue']['queue_full'])
        self.assertEqual(empty['summary']['service_status'], '')
        actual = AgentOperationalStatus.objects.create(endpoint=self.machine, result_pending_count=0, result_queue_full=False,
            raw_payload={'result_queue': {'pending_count': 0, 'queue_full': False}})
        result = _serialize_agent_diagnostic(self.machine, actual, [], '', can_view_technical=True)
        self.assertEqual(result['queue']['pending_count'], 0)
        self.assertIs(result['queue']['queue_full'], False)
        self.assertIsNotNone(result['summary']['diagnostic_reported_at'])

    def test_heartbeat_distinct_from_communication_and_status_unchanged(self):
        self.machine.status = AgentMachine.STATUS_OFFLINE
        self.machine.save(update_fields=['status'])
        heartbeat = record_heartbeat(self.machine, {'machine_id': self.machine.machine_id, 'hostname': self.machine.hostname, 'uptime_seconds': 123}, {})
        old = timezone.now() - timedelta(hours=1)
        type(heartbeat).objects.filter(pk=heartbeat.pk).update(received_at=old)
        snapshot = record_collection(self.machine, 'system', {'machine_id': self.machine.machine_id, 'collected_at': timezone.now().isoformat(), 'os': {'name':'Windows'}})
        self.machine.refresh_from_db()
        status_before = self.machine.status
        with patch('dashboard.views.evaluate_agent_update_policy') as policy:
            policy.return_value.target_version = ''
            data = build_endpoint_detail_payload(self.machine, snapshot, {'score': 100}, [], '', 'workstation', [], [], [])
        self.assertEqual(data['agent_health']['last_heartbeat_at'], old.isoformat())
        self.assertGreater(self.machine.last_seen_at, old)
        self.assertEqual(data['agent_health']['last_communication_at'], self.machine.last_seen_at.isoformat())
        self.assertEqual(status_before, AgentMachine.STATUS_OFFLINE)
        self.assertEqual(self.machine.status, status_before)
        self.assertIn('last_heartbeat_at', data['agent_health'])
        self.assertIn('lastFullInventory', data['inventory'])
        user = get_user_model().objects.create_superuser('compat-operator', password='synthetic')
        self.client.force_login(user)
        AgentJob.objects.create(endpoint=self.machine, job_type='ping')
        jobs = self.client.get(reverse('jobs-data')).json()['jobs']
        self.assertEqual(jobs[0]['lastHeartbeatAt'], old.isoformat())

    def test_gap_metadata_matches_counted_interval_between_windows(self):
        now = timezone.now(); start = now - timedelta(minutes=4)
        def row(offset):
            at = start + timedelta(seconds=offset)
            return SimpleNamespace(collected_at=at, window_end_at=at+timedelta(seconds=60), created_at=at+timedelta(seconds=60), resolution_seconds=60, metric_summaries={'metrics': {}})
        summary = continuous_summary(self.machine, start, now, [row(0), row(80)])
        self.assertEqual(summary['quality']['gap_threshold_seconds'], 10)
        self.assertEqual(summary['quality']['largest_internal_gap_seconds'], 20)
        self.assertEqual(summary['quality']['gaps_over_threshold_count'], 1)
