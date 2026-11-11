import json
import uuid
from datetime import timedelta
from io import StringIO

from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (AgentMachine, EndpointPerformanceSample, PersistedTelemetryAnalysis,
                     TelemetryAnalysisIncorporation)
from .telemetry_continuous import compact_history, persist_windows
from .telemetry_persisted_analysis import MAX_CANDIDATE_SCAN, analyze_endpoint


def metric(value, *, count=6, weight=60, percent=True):
    return {'count': count, 'sum': value * count, 'min': value, 'max': value,
            'weight': weight, 'weighted_sum': value * weight,
            'distribution': {str(int(value)): weight} if percent else {}}


def window(start, *, cpu=20, memory=60, received=6, extra_metrics=None, quality=None):
    metrics = {}
    if cpu is not None:
        metrics['cpu.usage_percent'] = metric(cpu, count=received)
    if memory is not None:
        metrics['memory.used_percent'] = metric(memory, count=received)
    metrics.update(extra_metrics or {})
    return {
        'window_id': str(uuid.uuid4()),
        'start_at': start.isoformat(),
        'end_at': (start + timedelta(minutes=1)).isoformat(),
        'resolution_seconds': 60,
        'expected_samples': 6,
        'received_samples': received,
        'metrics': metrics,
        'quality': quality or {},
    }


class PersistedTelemetryAnalysisTests(TestCase):
    def setUp(self):
        now = timezone.now().replace(second=0, microsecond=0)
        self.machine, _ = AgentMachine.create_with_token(
            hostname='SYNTHETIC-ANALYSIS', machine_id=str(uuid.uuid4()), agent_version='test-v2')
        self.machine.last_seen_at = now
        self.machine.save(update_fields=['last_seen_at'])
        self.now = now

    def add_window(self, data):
        persist_windows(self.machine, [data])
        return EndpointPerformanceSample.objects.get(sample_id=data['window_id'])

    def test_dry_run_is_read_only_apply_persists_latest_and_replay_is_idempotent(self):
        data = window(self.now - timedelta(minutes=5), quality={
            'cpu': 'available', 'memory': 'available',
            'top_processes': {'must_not_be_copied': 'private-command-line'},
        })
        self.add_window(data)

        preview = analyze_endpoint(self.machine.pk, now=self.now + timedelta(minutes=2))
        self.assertEqual(preview['state'], 'eligible')
        self.assertFalse(PersistedTelemetryAnalysis.objects.exists())
        self.assertFalse(TelemetryAnalysisIncorporation.objects.exists())

        applied = analyze_endpoint(self.machine.pk, apply=True, now=self.now + timedelta(minutes=2))
        self.assertEqual(applied['state'], 'succeeded')
        state = PersistedTelemetryAnalysis.objects.get(endpoint=self.machine)
        self.assertEqual(state.analysis_count, 1)
        self.assertEqual(state.source, 'continuous_v2')
        self.assertEqual(state.schema_version, 2)
        self.assertEqual(state.resolution_seconds, 60)
        self.assertEqual(state.latest_analysis['identity']['machine_id'], self.machine.machine_id)
        self.assertEqual(state.latest_analysis['cpu']['usage_percent']['avg'], 20)
        self.assertEqual(state.latest_analysis['quality']['coverage_percent'], 100)
        self.assertEqual(state.latest_analysis['quality']['gap_positions_available'], False)
        self.assertNotIn('top_processes', state.latest_analysis['quality']['source_quality'])
        self.assertIsNone(state.latest_analysis['agent']['telemetry_errors']['total_errors'])
        self.assertEqual(TelemetryAnalysisIncorporation.objects.count(), 1)

        replay = analyze_endpoint(self.machine.pk, apply=True, now=self.now + timedelta(minutes=3))
        self.assertEqual(replay['state'], 'already_incorporated')
        self.assertEqual(PersistedTelemetryAnalysis.objects.get().analysis_count, 1)
        self.assertEqual(TelemetryAnalysisIncorporation.objects.count(), 1)

    def test_replacement_merges_weighted_statistics_histogram_and_quality_without_averaging_percentiles(self):
        first = window(self.now - timedelta(minutes=6), cpu=20, memory=50,
                       quality={'cpu': 'available', 'memory': 'available',
                                'group_status_counts': {'cpu': {'available': 6}}})
        self.add_window(first)
        analyze_endpoint(self.machine.pk, apply=True, now=self.now + timedelta(minutes=2))

        second = window(self.now - timedelta(minutes=5), cpu=80, memory=None,
                        quality={'cpu': 'partial', 'memory': 'unknown',
                                 'group_status_counts': {'cpu': {'available': 4, 'error': 2}}})
        self.add_window(second)
        analyze_endpoint(self.machine.pk, apply=True, now=self.now + timedelta(minutes=3))

        state = PersistedTelemetryAnalysis.objects.get(endpoint=self.machine)
        self.assertEqual(state.analysis_count, 2)
        self.assertEqual(state.historical_statistics['analysis_count'], 2)
        cpu = state.historical_statistics['metrics']['cpu.usage_percent']
        self.assertEqual(cpu['sample_count'], 12)
        self.assertEqual(cpu['sum'], 600)
        self.assertEqual(cpu['weight'], 120)
        self.assertEqual(cpu['weighted_sum'], 6000)
        self.assertEqual(cpu['min'], 20)
        self.assertEqual(cpu['max'], 80)
        self.assertEqual(cpu['distribution'], {'20': 60, '80': 60})
        self.assertNotIn('avg_percentile', cpu)
        self.assertEqual(cpu['by_quality']['complete']['analysis_count'], 1)
        self.assertEqual(cpu['by_quality']['partial']['analysis_count'], 1)
        memory = state.historical_statistics['metrics']['memory.used_percent']
        self.assertEqual(memory['valid_analysis_count'], 1)
        self.assertEqual(memory['missing_analysis_count'], 1)
        self.assertEqual(state.historical_statistics['quality']['group_status_counts']['cpu'],
                         {'available': 10, 'error': 2})
        self.assertEqual(state.historical_statistics['quality']['coverage']['count'], 2)
        self.assertEqual(state.last_success_at, self.now + timedelta(minutes=3))

    def test_unknown_quality_and_missing_metrics_are_preserved_without_zero_filling(self):
        data = window(self.now - timedelta(minutes=5), cpu=30, memory=None, received=5, quality={})
        self.add_window(data)
        result = analyze_endpoint(self.machine.pk, apply=True, now=self.now + timedelta(minutes=2))
        self.assertEqual(result['state'], 'succeeded')
        state = PersistedTelemetryAnalysis.objects.get(endpoint=self.machine)
        detail = state.latest_analysis
        self.assertEqual(detail['quality']['state'], 'partial')
        self.assertAlmostEqual(detail['quality']['coverage_percent'], 500 / 6)
        self.assertEqual(detail['quality']['metric_presence']['memory.used_percent'], 'unknown')
        self.assertNotIn('memory.used_percent', detail['memory'])
        self.assertEqual(detail['quality']['source_quality'], {})
        self.assertNotIn('memory.used_percent', state.historical_statistics['metrics'])

    def test_invalid_new_window_records_attempt_but_preserves_last_success_and_history(self):
        good = window(self.now - timedelta(minutes=6))
        self.add_window(good)
        analyze_endpoint(self.machine.pk, apply=True, now=self.now + timedelta(minutes=2))
        previous = PersistedTelemetryAnalysis.objects.get(endpoint=self.machine)
        old_analysis_id = previous.latest_analysis_id
        old_detail = json.loads(json.dumps(previous.latest_analysis))
        old_history = json.loads(json.dumps(previous.historical_statistics))

        incomplete = window(self.now - timedelta(minutes=5), cpu=40, received=4)
        self.add_window(incomplete)
        rejected = analyze_endpoint(self.machine.pk, apply=True, now=self.now + timedelta(minutes=3))

        self.assertEqual(rejected['state'], 'rejected')
        state = PersistedTelemetryAnalysis.objects.get(endpoint=self.machine)
        self.assertEqual(state.last_attempt_reason, 'coverage_below_policy')
        self.assertEqual(state.latest_analysis_id, old_analysis_id)
        self.assertEqual(state.latest_analysis, old_detail)
        self.assertEqual(state.historical_statistics, old_history)
        self.assertEqual(state.analysis_count, 1)

    def test_newest_invalid_window_is_skipped_for_older_valid_window(self):
        valid = window(self.now - timedelta(minutes=6), cpu=35)
        invalid = window(self.now - timedelta(minutes=5), cpu=90, received=4)
        self.add_window(valid)
        self.add_window(invalid)

        result = analyze_endpoint(self.machine.pk, apply=True, now=self.now + timedelta(minutes=3))

        self.assertEqual(result['state'], 'succeeded')
        self.assertEqual(result['selection']['rejection_reasons'], {'coverage_below_policy': 1})
        state = PersistedTelemetryAnalysis.objects.get(endpoint=self.machine)
        self.assertEqual(state.latest_analysis['sample_id'], valid['window_id'])
        self.assertEqual(state.latest_analysis['cpu']['usage_percent']['avg'], 35)
        self.assertEqual(state.latest_analysis['candidate_selection']['records_examined'], 2)
        self.assertEqual(state.latest_analysis['candidate_selection']['rejected_candidates'], 1)
        self.assertEqual(state.latest_analysis['candidate_selection']['rejection_reasons'],
                         {'coverage_below_policy': 1})
        self.assertEqual(TelemetryAnalysisIncorporation.objects.get().sample_id,
                         uuid.UUID(valid['window_id']))

    def test_candidate_selection_stops_at_configured_scan_limit(self):
        rows = []
        for index in range(MAX_CANDIDATE_SCAN + 1):
            start = self.now - timedelta(minutes=MAX_CANDIDATE_SCAN + 2 - index)
            data = window(start, cpu=25, received=4)
            rows.append(EndpointPerformanceSample(
                endpoint=self.machine, sample_id=uuid.UUID(data['window_id']),
                schema_version=2, resolution_seconds=60,
                collected_at=start, window_end_at=start + timedelta(minutes=1),
                metric_summaries=data, cpu_percent=25, memory_used_percent=60,
                collection_duration_ms=0))
        EndpointPerformanceSample.objects.bulk_create(rows)

        result = analyze_endpoint(self.machine.pk, apply=True, now=self.now + timedelta(minutes=2))

        self.assertEqual(result['state'], 'rejected')
        self.assertEqual(result['reason'], 'candidate_scan_limit_reached')
        self.assertEqual(result['selection']['records_examined'], MAX_CANDIDATE_SCAN)
        self.assertTrue(result['selection']['limit_reached'])
        state = PersistedTelemetryAnalysis.objects.get(endpoint=self.machine)
        self.assertEqual(state.last_attempt_reason, 'candidate_scan_limit_reached')
        self.assertIsNone(state.latest_analysis_id)
        self.assertEqual(state.analysis_count, 0)

    def test_historical_window_does_not_inherit_current_agent_version(self):
        data = window(self.now - timedelta(minutes=5))
        row = self.add_window(data)
        self.assertNotIn('agent_version', row.metric_summaries)
        self.machine.agent_version = '2.0.0-later'
        self.machine.save(update_fields=['agent_version'])

        result = analyze_endpoint(self.machine.pk, apply=True, now=self.now + timedelta(minutes=2))

        self.assertEqual(result['state'], 'succeeded')
        state = PersistedTelemetryAnalysis.objects.get(endpoint=self.machine)
        self.assertIsNone(state.latest_analysis['agent_version'])
        self.assertEqual(state.agent_version, '')

    def test_offline_endpoint_and_missing_identity_are_not_analyzed(self):
        row = window(self.now - timedelta(minutes=5))
        self.add_window(row)
        self.machine.last_seen_at = self.now - timedelta(minutes=16)
        self.machine.save(update_fields=['last_seen_at'])
        result = analyze_endpoint(self.machine.pk, apply=True, now=self.now)
        self.assertEqual(result['state'], 'endpoint_offline')
        state = PersistedTelemetryAnalysis.objects.get(endpoint=self.machine)
        self.assertEqual(state.last_attempt_reason, 'endpoint_offline')
        self.assertEqual(state.analysis_count, 0)

        self.machine.machine_id = ''
        self.machine.last_seen_at = self.now
        self.machine.save(update_fields=['machine_id', 'last_seen_at'])
        result = analyze_endpoint(self.machine.pk, apply=True, now=self.now)
        self.assertEqual(result['state'], 'rejected')
        self.assertEqual(PersistedTelemetryAnalysis.objects.get().last_attempt_reason, 'identity_missing')

    def test_machine_id_change_cannot_mix_history_for_the_same_endpoint_uuid(self):
        first = window(self.now - timedelta(minutes=6))
        self.add_window(first)
        analyze_endpoint(self.machine.pk, apply=True, now=self.now + timedelta(minutes=2))
        state = PersistedTelemetryAnalysis.objects.get(endpoint=self.machine)
        previous_analysis_id = state.latest_analysis_id
        previous_history = json.loads(json.dumps(state.historical_statistics))

        self.machine.machine_id = str(uuid.uuid4())
        self.machine.save(update_fields=['machine_id'])
        second = window(self.now - timedelta(minutes=5), cpu=80)
        self.add_window(second)
        result = analyze_endpoint(self.machine.pk, apply=True, now=self.now + timedelta(minutes=3))

        self.assertEqual(result['state'], 'rejected')
        self.assertEqual(result['reason'], 'machine_identity_changed')
        state.refresh_from_db()
        self.assertEqual(state.latest_analysis_id, previous_analysis_id)
        self.assertEqual(state.historical_statistics, previous_history)
        self.assertEqual(state.analysis_count, 1)

    def test_legacy_compacted_and_expired_windows_are_never_selected(self):
        at = self.now - timedelta(minutes=5)
        legacy = window(at)
        EndpointPerformanceSample.objects.create(
            endpoint=self.machine, sample_id=uuid.uuid4(), schema_version=1,
            resolution_seconds=300, collected_at=at, window_end_at=at + timedelta(minutes=1),
            metric_summaries=legacy, collection_duration_ms=0)
        compacted = window(at + timedelta(minutes=1))
        EndpointPerformanceSample.objects.create(
            endpoint=self.machine, sample_id=compacted['window_id'], schema_version=2,
            resolution_seconds=300, collected_at=at + timedelta(minutes=1),
            window_end_at=at + timedelta(minutes=2), metric_summaries=compacted,
            collection_duration_ms=0)
        expired = window(self.now - timedelta(days=8))
        EndpointPerformanceSample.objects.create(
            endpoint=self.machine, sample_id=expired['window_id'], schema_version=2,
            resolution_seconds=60, collected_at=self.now - timedelta(days=8),
            window_end_at=self.now - timedelta(days=8) + timedelta(minutes=1),
            metric_summaries=expired, collection_duration_ms=0)
        result = analyze_endpoint(self.machine.pk, apply=True, now=self.now)
        self.assertEqual(result['state'], 'no_eligible_data')
        self.assertFalse(TelemetryAnalysisIncorporation.objects.exists())

    def test_existing_telemetry_history_and_alerts_are_unchanged_by_analysis(self):
        data = window(self.now - timedelta(minutes=5), cpu=20)
        self.add_window(data)
        before = list(EndpointPerformanceSample.objects.values_list('pk', 'metric_summaries'))
        alert_count = self.machine.alerts.count()
        analyze_endpoint(self.machine.pk, apply=True, now=self.now + timedelta(minutes=2))
        self.assertEqual(list(EndpointPerformanceSample.objects.values_list('pk', 'metric_summaries')), before)
        self.assertEqual(self.machine.alerts.count(), alert_count)
        self.assertEqual(EndpointPerformanceSample.objects.count(), 1)

    @override_settings(NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_MANUAL_APPLY_ENABLED=False)
    def test_management_command_is_dry_run_by_default_and_apply_requires_explicit_setting(self):
        data = window(self.now - timedelta(minutes=5))
        self.add_window(data)
        output = StringIO()
        call_command('persist_latest_telemetry_analysis', endpoint=str(self.machine.pk), stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual(result['mode'], 'dry_run')
        self.assertEqual(result['results'][0]['state'], 'eligible')
        self.assertFalse(PersistedTelemetryAnalysis.objects.exists())
        with self.assertRaises(CommandError):
            call_command('persist_latest_telemetry_analysis', endpoint=str(self.machine.pk), apply=True)
        self.assertFalse(PersistedTelemetryAnalysis.objects.exists())

    def test_distribution_compaction_does_not_make_old_60_second_rows_eligible(self):
        data = window(self.now - timedelta(days=8))
        self.add_window(data)
        before = EndpointPerformanceSample.objects.count()
        # Compaction is invoked in dry-run only here; persisted analysis never
        # calls it or deletes/modifies source performance rows.
        preview = compact_history(apply=False, now=self.now)
        self.assertGreaterEqual(preview['groups'], 0)
        self.assertEqual(EndpointPerformanceSample.objects.count(), before)
        self.assertEqual(analyze_endpoint(self.machine.pk, now=self.now)['state'], 'no_eligible_data')
