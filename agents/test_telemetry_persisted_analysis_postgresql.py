import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import skipUnless

from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from .models import (AgentMachine, EndpointPerformanceSample, PersistedTelemetryAnalysis,
                     PersistedTelemetryAnalysisSchedule, TelemetryAnalysisIncorporation)
from .telemetry_persisted_analysis import analyze_endpoint, scheduled_analysis_round


@skipUnless(connection.vendor == 'postgresql', 'requires PostgreSQL row locks')
class PersistedTelemetryAnalysisPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def test_two_workers_racing_same_window_incorporate_exactly_once(self):
        now = timezone.now().replace(second=0, microsecond=0)
        machine, _ = AgentMachine.create_with_token(
            hostname='SYNTHETIC-ANALYSIS-PG', machine_id=str(uuid.uuid4()), agent_version='test-v2')
        machine.last_seen_at = now
        machine.save(update_fields=['last_seen_at'])
        start = now - timedelta(minutes=5)
        sample_id = uuid.uuid4()
        metric = {'count': 6, 'sum': 120, 'min': 20, 'max': 20, 'weight': 60,
                  'weighted_sum': 1200, 'distribution': {'20': 60}}
        payload = {
            'window_id': str(sample_id), 'start_at': start.isoformat(),
            'end_at': (start + timedelta(minutes=1)).isoformat(),
            'resolution_seconds': 60, 'expected_samples': 6, 'received_samples': 6,
            'metrics': {'cpu.usage_percent': metric, 'memory.used_percent': metric},
            'quality': {'cpu': 'available', 'memory': 'available'},
        }
        EndpointPerformanceSample.objects.create(
            endpoint=machine, sample_id=sample_id, schema_version=2, resolution_seconds=60,
            collected_at=start, window_end_at=start + timedelta(minutes=1),
            metric_summaries=payload, cpu_percent=20, memory_used_percent=20,
            collection_duration_ms=0)

        barrier = threading.Barrier(2)

        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return analyze_endpoint(machine.pk, apply=True, now=now + timedelta(minutes=2))['state']
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            outcomes = sorted(future.result(timeout=20) for future in futures)

        self.assertEqual(outcomes, ['already_incorporated', 'succeeded'])
        state = PersistedTelemetryAnalysis.objects.get(endpoint=machine)
        self.assertEqual(state.analysis_count, 1)
        self.assertEqual(TelemetryAnalysisIncorporation.objects.filter(endpoint=machine).count(), 1)

    @override_settings(
        NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_ENABLED=True,
        NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_DRY_RUN=False,
        NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_FIRST_CAPTURE_POLICY='immediate',
        NIGHTOWL_TELEMETRY_RETENTION_DAYS={60: 7, 300: 30, 3600: 90},
    )
    def test_two_scheduler_workers_claim_due_endpoint_once(self):
        now = timezone.now().replace(second=0, microsecond=0)
        machine, _ = AgentMachine.create_with_token(
            hostname='SYNTHETIC-ANALYSIS-SCHED-PG', machine_id=str(uuid.uuid4()), agent_version='test-v2')
        machine.last_seen_at = now
        machine.save(update_fields=['last_seen_at'])
        start = now - timedelta(minutes=5)
        sample_id = uuid.uuid4()
        metric = {'count': 6, 'sum': 120, 'min': 20, 'max': 20, 'weight': 60,
                  'weighted_sum': 1200, 'distribution': {'20': 60}}
        payload = {
            'window_id': str(sample_id), 'start_at': start.isoformat(),
            'end_at': (start + timedelta(minutes=1)).isoformat(),
            'resolution_seconds': 60, 'expected_samples': 6, 'received_samples': 6,
            'metrics': {'cpu.usage_percent': metric, 'memory.used_percent': metric},
            'quality': {'cpu': 'available', 'memory': 'available'},
        }
        EndpointPerformanceSample.objects.create(
            endpoint=machine, sample_id=sample_id, schema_version=2, resolution_seconds=60,
            collected_at=start, window_end_at=start + timedelta(minutes=1),
            metric_summaries=payload, cpu_percent=20, memory_used_percent=20,
            collection_duration_ms=0)
        PersistedTelemetryAnalysisSchedule.objects.create(
            endpoint=machine, machine_id_snapshot=machine.machine_id,
            first_scheduled_at=now, scheduled_for=now, next_run_at=now)
        barrier = threading.Barrier(2)

        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return scheduled_analysis_round(now=now, limit=5, dry_run=False)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            outcomes = [future.result(timeout=30) for future in futures]

        self.assertEqual(sum(item['processed'] for item in outcomes), 1)
        self.assertEqual(PersistedTelemetryAnalysis.objects.filter(endpoint=machine).get().analysis_count, 1)
        self.assertEqual(TelemetryAnalysisIncorporation.objects.filter(endpoint=machine).count(), 1)
        schedule = PersistedTelemetryAnalysisSchedule.objects.get(endpoint=machine)
        self.assertEqual(schedule.next_run_at, now + timedelta(days=15))
        self.assertEqual(len(schedule.attempt_history), 1)
