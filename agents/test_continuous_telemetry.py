import copy
import json
import tempfile
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache.backends.filebased import FileBasedCache
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from .models import AgentMachine, EndpointPerformanceSample, EndpointAlert, TelemetryLiveSession
from .telemetry_continuous import validate_windows, persist_windows, workload, compact_history, merge_metrics


def metric(value, *, count=6, weight=60, percent=True):
    return {'count': count, 'sum': value * count, 'min': value, 'max': value, 'weight': weight,
            'weighted_sum': value * weight, 'distribution': {str(int(value)): weight} if percent else {}}


def window(at, cpu=95, **changes):
    value = {'window_id': str(uuid.uuid4()), 'start_at': at.isoformat(), 'end_at': (at + timedelta(minutes=1)).isoformat(),
             'resolution_seconds': 60, 'expected_samples': 6, 'received_samples': 6,
             'metrics': {'cpu.usage_percent': metric(cpu), 'memory.used_percent': metric(60), 'memory.committed_percent': metric(40)}, 'quality': {}}
    value.update(changes)
    return value


class ContinuousTelemetryTests(TestCase):
    def setUp(self):
        self.machine, self.token = AgentMachine.create_with_token(hostname='SYNTHETIC-V2', machine_id=str(uuid.uuid4()))
        self.at = timezone.now().replace(second=0, microsecond=0) - timedelta(minutes=5)
        self.agent = Client(HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.temporary = tempfile.TemporaryDirectory(prefix='nightowl-live-test-')
        self.addCleanup(self.temporary.cleanup)
        self.enterContext(self.settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
            'telemetry_live': {'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache', 'LOCATION': self.temporary.name}}))
        self.operator = get_user_model().objects.create_superuser('synthetic-operator', password='synthetic-only')
        self.client.force_login(self.operator)
        self.live_url = reverse('endpoint-telemetry-live', args=[self.machine.pk])

    def send(self, windows):
        return self.agent.post(reverse('agent-telemetry'), {'schema_version': 2, 'machine_id': self.machine.machine_id, 'windows': windows}, content_type='application/json')

    def test_minute_ingestion_and_ack_loss_are_idempotent(self):
        data = window(self.at)
        self.assertEqual(self.send([data]).status_code, 200)
        self.assertEqual(self.send([data]).json()['accepted_window_ids'], [data['window_id']])
        self.assertEqual(EndpointPerformanceSample.objects.count(), 1)
        conflicting = copy.deepcopy(data)
        conflicting['metrics']['cpu.usage_percent'] = metric(10)
        self.assertEqual(self.send([conflicting]).status_code, 400)
        self.assertEqual(EndpointPerformanceSample.objects.get().cpu_percent, 95)

    def test_bad_identity_cadence_nonfinite_and_atomic_batch_rejected(self):
        payload = {'schema_version': 2, 'machine_id': str(uuid.uuid4()), 'windows': [window(self.at)]}
        self.assertEqual(self.agent.post(reverse('agent-telemetry'), payload, content_type='application/json').status_code, 403)
        bad = window(self.at + timedelta(minutes=1))
        bad['metrics']['cpu.usage_percent']['count'] = 7
        self.assertEqual(self.send([window(self.at), bad]).status_code, 400)
        self.assertEqual(EndpointPerformanceSample.objects.count(), 0)
        bad = window(self.at, resolution_seconds=300)
        self.assertEqual(self.send([bad]).status_code, 400)
        bad = window(self.at)
        bad['metrics']['cpu.usage_percent']['sum'] = float('inf')
        with self.assertRaises(ValueError):
            validate_windows({'schema_version': 2, 'machine_id': self.machine.machine_id, 'windows': [bad]})

    def test_two_minute_peak_retained_and_alerted_immediately(self):
        data = [window(self.at), window(self.at + timedelta(minutes=1))]
        self.assertEqual(self.send(data).status_code, 200)
        rows = list(EndpointPerformanceSample.objects.order_by('collected_at'))
        self.assertEqual(workload(rows)['cpu']['status'], 'SUSTAINED_PRESSURE')
        self.assertEqual(EndpointAlert.objects.filter(alert_type='workload_cpu').count(), 1)
        rows[1].collected_at += timedelta(minutes=1)
        self.assertNotEqual(workload(rows)['cpu']['status'], 'SUSTAINED_PRESSURE')

    def test_isolated_peak_and_missing_memory_do_not_recommend_upgrade(self):
        data = window(self.at)
        data['metrics']['cpu.usage_percent'] = {'count':6,'sum':150,'min':10,'max':100,'weight':60,'weighted_sum':1500,'distribution':{'10':50,'100':10}}
        persist_windows(self.machine, [data])
        result = workload(list(EndpointPerformanceSample.objects.all()))
        self.assertNotEqual(result['cpu']['status'], 'SUSTAINED_PRESSURE')
        self.assertEqual(result['memory']['status'], 'INSUFFICIENT_EVIDENCE')

    def test_weighted_latency_and_distribution_merge(self):
        low = {'disk.a.read_latency_ms': metric(10, weight=100, percent=False)}
        high = {'disk.a.read_latency_ms': metric(100, weight=1, percent=False)}
        value = merge_metrics([low, high])['disk.a.read_latency_ms']
        self.assertAlmostEqual(value['weighted_sum']/value['weight'], 1100/101)
        self.assertEqual(value['max'], 100)

    def test_summary_uses_minute_coverage_and_preserves_maximum(self):
        self.send([window(self.at), window(self.at + timedelta(minutes=1))])
        from .telemetry_analytics import build_endpoint_telemetry_summary
        data = build_endpoint_telemetry_summary(self.machine, self.at, self.at + timedelta(minutes=2))
        self.assertEqual(data['quality']['expected_samples'], 2)
        self.assertEqual(data['quality']['received_samples'], 2)
        self.assertEqual(data['cpu']['usage_percent']['max'], 95)
        self.assertEqual(data['percentile_method'], 'duration_histogram_1pct')

    def test_retention_rollup_ack_replay_and_no_legacy_deletion(self):
        data = [window(self.at + timedelta(minutes=index)) for index in range(3)]
        persist_windows(self.machine, data)
        legacy = EndpointPerformanceSample.objects.create(endpoint=self.machine, sample_id=uuid.uuid4(), collected_at=self.at, uptime_seconds=1, collection_duration_ms=1)
        future = self.at + timedelta(days=8)
        before = EndpointPerformanceSample.objects.count()
        compact_history(now=future)
        self.assertEqual(EndpointPerformanceSample.objects.count(), before)
        compact_history(apply=True, now=future)
        self.assertTrue(EndpointPerformanceSample.objects.filter(pk=legacy.pk).exists())
        self.assertFalse(EndpointPerformanceSample.objects.filter(schema_version=2, resolution_seconds=60).exists())
        compact_history(apply=True, now=future)
        self.assertEqual(persist_windows(self.machine, [data[0]]), [data[0]['window_id']])
        self.assertFalse(EndpointPerformanceSample.objects.filter(schema_version=2, resolution_seconds=60).exists())
        self.assertEqual(EndpointPerformanceSample.objects.filter(schema_version=2).first().metric_summaries['metrics']['cpu.usage_percent']['max'], 95)

    def test_live_authorization_points_cross_worker_and_no_raw_database_rows(self):
        self.assertEqual(Client().post(self.live_url, {}, content_type='application/json').status_code, 403)
        ordinary = get_user_model().objects.create_user('synthetic-viewer', password='synthetic-only')
        other = Client()
        other.force_login(ordinary)
        self.assertEqual(other.post(self.live_url, {}, content_type='application/json').status_code, 403)
        started = self.client.post(self.live_url, {}, content_type='application/json')
        self.assertEqual(started.status_code, 200, started.content)
        sid = started.json()['session_id']
        self.assertEqual(self.agent.get(reverse('agent-telemetry-control')).json()['session_id'], sid)
        point = {'sample_id':str(uuid.uuid4()), 'collected_at':timezone.now().isoformat(), 'metrics':{'cpu.usage_percent':95}, 'quality':{}}
        response = self.agent.post(reverse('agent-telemetry-live'), {'session_id':sid, 'machine_id':self.machine.machine_id, 'points':[point]}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        independent_worker = FileBasedCache(self.temporary.name, {})
        self.assertEqual(len(independent_worker.get('live:'+sid)), 1)
        import subprocess, sys, os
        process = subprocess.run([sys.executable, '-c',
            "import os; from django.core.cache.backends.filebased import FileBasedCache; c=FileBasedCache(os.environ['NIGHTOWL_TEST_CACHE'],{}); print(len(c.get('live:'+os.environ['NIGHTOWL_TEST_SESSION'],[])))"],
            env={**os.environ,'NIGHTOWL_TEST_CACHE':self.temporary.name,'NIGHTOWL_TEST_SESSION':sid},
            capture_output=True,text=True,timeout=10)
        self.assertEqual(process.returncode,0,'Isolated cache worker failed (output withheld).')
        self.assertEqual(process.stdout.strip(),'1')
        self.assertEqual(self.client.get(self.live_url).json()['points'][0]['metrics']['cpu.usage_percent'], 95)
        self.assertEqual(EndpointPerformanceSample.objects.count(), 0)

    def test_live_expiration_renewal_wrong_identity_and_locmem_block(self):
        sid = self.client.post(self.live_url, {}, content_type='application/json').json()['session_id']
        self.assertEqual(self.client.post(self.live_url, {'action':'renew','session_id':sid}, content_type='application/json').status_code, 200)
        self.assertEqual(self.agent.post(reverse('agent-telemetry-live'), {'session_id':sid, 'machine_id':str(uuid.uuid4()), 'points':[]}, content_type='application/json').status_code, 403)
        TelemetryLiveSession.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertIsNone(self.agent.get(reverse('agent-telemetry-control')).json()['session_id'])
        self.assertEqual(self.client.get(self.live_url).json()['state'], 'expired')
        with self.settings(CACHES={'telemetry_live': {'BACKEND':'django.core.cache.backends.locmem.LocMemCache'}}):
            self.assertEqual(self.client.post(self.live_url, {}, content_type='application/json').status_code, 503)

    def test_simulated_100_endpoint_ingestion(self):
        # Synthetic load measures this isolated database, never a production capacity claim.
        import time
        start = time.perf_counter()
        for index in range(100):
            endpoint, _ = AgentMachine.create_with_token(hostname='SYNTHETIC-' + str(index), machine_id=str(uuid.uuid4()))
            persist_windows(endpoint, [window(self.at, cpu=20)])
        self.assertEqual(EndpointPerformanceSample.objects.count(), 100)
        print(f'SYNTHETIC_SQLITE_100_ENDPOINTS_SECONDS={time.perf_counter()-start:.3f}')

    def test_live_owner_csrf_hard_deadline_and_malformed_session(self):
        sid = self.client.post(self.live_url, {}, content_type='application/json').json()['session_id']
        other_operator = get_user_model().objects.create_superuser('synthetic-other', password='synthetic-only')
        second = Client()
        second.force_login(other_operator)
        self.assertEqual(second.post(self.live_url, {'action':'renew','session_id':sid}, content_type='application/json').status_code, 409)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.operator)
        self.assertEqual(csrf_client.post(self.live_url, {}, content_type='application/json').status_code, 403)
        deadline = timezone.now()+timedelta(seconds=5)
        TelemetryLiveSession.objects.update(hard_expires_at=deadline)
        renewed = self.client.post(self.live_url, {'action':'renew','session_id':sid}, content_type='application/json')
        from django.utils.dateparse import parse_datetime
        self.assertEqual(parse_datetime(renewed.json()['expires_at']), deadline)
        self.assertEqual(self.agent.post(reverse('agent-telemetry-live'), {'session_id':'malformed'}, content_type='application/json').status_code, 400)

    def test_different_disks_or_interfaces_are_not_continuous_pressure(self):
        rows = []
        for index in range(2):
            item = window(self.at + timedelta(minutes=index), cpu=10)
            prefix = f'disk.device{index}.'
            item['metrics'].update({prefix+'write_latency_ms':metric(50, percent=False),
                prefix+'write_ops_per_sec':metric(10, percent=False), prefix+'queue_length':metric(3, percent=False),
                f'network.nic{index}.rx_utilization_percent':metric(95)})
            persist_windows(self.machine,[item])
        result = workload(list(EndpointPerformanceSample.objects.order_by('collected_at')))
        self.assertNotEqual(result['disk']['status'], 'SUSTAINED_PRESSURE')
        self.assertNotEqual(result['network']['status'], 'SUSTAINED_PRESSURE')

    def test_rollup_to_hour_and_expiry_preserve_weighted_evidence(self):
        persist_windows(self.machine,[window(self.at),window(self.at+timedelta(minutes=1))])
        compact_history(apply=True, now=self.at+timedelta(days=31))
        row = EndpointPerformanceSample.objects.get(schema_version=2)
        self.assertEqual(row.resolution_seconds,3600)
        self.assertEqual(row.metric_summaries['received_samples'],12)
        self.assertEqual(row.metric_summaries['metrics']['cpu.usage_percent']['max'],95)
        compact_history(apply=True, now=self.at+timedelta(days=92))
        self.assertFalse(EndpointPerformanceSample.objects.filter(schema_version=2).exists())

    def test_low_available_memory_with_paging_not_necessarily_high_commit(self):
        for index in range(2):
            item = window(self.at+timedelta(minutes=index), cpu=20)
            item['metrics'].update({'memory.available_percent':metric(5), 'memory.page_reads_per_sec':metric(20,percent=False)})
            persist_windows(self.machine,[item])
        diagnostic = workload(list(EndpointPerformanceSample.objects.order_by('collected_at')))['memory']
        self.assertEqual(diagnostic['status'],'SUSTAINED_PRESSURE')

    def test_historical_series_selects_resolution_without_averaging_p95(self):
        persist_windows(self.machine,[window(self.at),window(self.at+timedelta(minutes=1),cpu=20)])
        from .telemetry_history import historical_series
        monthly = historical_series(self.machine.pk,720)
        hourly = historical_series(self.machine.pk,2160)
        self.assertTrue(all(item['resolution_seconds'] == 300 for item in monthly))
        self.assertTrue(all(item['resolution_seconds'] == 3600 for item in hourly))
        self.assertEqual(max(item['cpu_max'] for item in monthly),95)

    def test_pre_migration_backend_insert_uses_database_defaults(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        old_apps = MigrationExecutor(connection).loader.project_state([('agents','0036_configure_telemetry_job')]).apps
        old_sample = old_apps.get_model('agents','EndpointPerformanceSample')
        item = old_sample.objects.create(endpoint_id=self.machine.pk, sample_id=uuid.uuid4(),
            collected_at=self.at, uptime_seconds=1, collection_duration_ms=1)
        stored = EndpointPerformanceSample.objects.get(pk=item.pk)
        self.assertEqual(stored.schema_version,1)
        self.assertEqual(stored.resolution_seconds,300)
        self.assertEqual(stored.metric_summaries,{})
