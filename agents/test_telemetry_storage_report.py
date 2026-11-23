import json
import uuid
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import AgentMachine, EndpointPerformanceSample
from .telemetry_storage_report import build_telemetry_storage_report


RETENTION = {'NIGHTOWL_TELEMETRY_RETENTION_DAYS': {60: 7, 300: 30, 3600: 90}}


@override_settings(**RETENTION)
class TelemetryStorageReportTests(TestCase):
    def setUp(self):
        self.endpoint, _ = AgentMachine.create_with_token(
            hostname='RC48-REPORT-SYNTHETIC', machine_id=str(uuid.uuid4()))
        self.now = timezone.now().replace(microsecond=0)

    def add_sample(self, *, minutes_ago, schema=2, resolution=60, received=6,
                   expected=6, metrics=None):
        at = self.now - timedelta(minutes=minutes_ago)
        EndpointPerformanceSample.objects.create(
            endpoint=self.endpoint,
            sample_id=uuid.uuid4(),
            schema_version=schema,
            resolution_seconds=resolution,
            collected_at=at,
            window_end_at=at + timedelta(seconds=resolution),
            metric_summaries={
                'expected_samples': expected,
                'received_samples': received,
                'metrics': metrics or {},
            } if schema == 2 else {},
            process_consumers={},
            collection_duration_ms=0,
        )

    def test_report_is_read_only_and_reports_exact_counts_sample_quality_and_bytes(self):
        self.add_sample(minutes_ago=5, metrics={
            'cpu.usage_percent': {'count': 6, 'min': 10, 'max': 80},
            'memory.used_percent': {'count': 6, 'min': 20, 'max': 40},
            'disk.instance.read_latency_ms': {'count': 6, 'min': 1, 'max': 12},
            'network.interface.received_bytes_per_sec': {'count': 6, 'min': 100, 'max': 900},
        })
        self.add_sample(minutes_ago=6, received=4, metrics={
            'cpu.usage_percent': {'count': 4, 'min': 5, 'max': 75},
        })
        self.add_sample(minutes_ago=8, schema=1, resolution=300)
        before = list(EndpointPerformanceSample.objects.values_list('pk', 'metric_summaries'))

        report = build_telemetry_storage_report(
            endpoint_id=self.endpoint.pk, window_days=90, sample_limit=10, now=self.now)

        self.assertTrue(report['read_only'])
        self.assertEqual(report['scope'], 'single_endpoint')
        one_minute = next(group for group in report['groups'] if group['resolution_seconds'] == 60)
        self.assertEqual(one_minute['row_count'], 2)
        self.assertEqual(one_minute['endpoint_count'], 1)
        self.assertEqual(one_minute['sample_quality']['complete_windows'], 1)
        self.assertEqual(one_minute['sample_quality']['partial_windows'], 1)
        self.assertEqual(one_minute['sample_quality']['rows_with_metric_group'], {
            'cpu': 2, 'memory': 1, 'disk': 1, 'network': 1,
        })
        self.assertEqual(one_minute['sampled_metric_ranges']['cpu.usage_percent'], {
            'sampled_rows_with_values': 2, 'valid_native_reads': 10, 'min': 5, 'max': 80,
        })
        self.assertEqual(
            one_minute['sampled_metric_ranges']['disk.read_latency_ms']['valid_native_reads'], 6)
        self.assertEqual(
            one_minute['sampled_metric_ranges']['network.received_bytes_per_sec']['min'], 100)
        self.assertGreater(one_minute['sampled_payload_bytes']['logical_json_max'], 0)
        self.assertEqual(list(EndpointPerformanceSample.objects.values_list('pk', 'metric_summaries')), before)

    def test_retention_cutoff_and_legacy_quality_are_reported_without_mixing(self):
        self.add_sample(minutes_ago=60 * 24 * 8)
        self.add_sample(minutes_ago=60 * 24 * 8, schema=1, resolution=300)
        report = build_telemetry_storage_report(
            endpoint_id=self.endpoint.pk, window_days=90, sample_limit=10, now=self.now)
        v2 = next(group for group in report['groups'] if group['schema_version'] == 2)
        legacy = next(group for group in report['groups'] if group['schema_version'] == 1)
        self.assertEqual(v2['rows_beyond_source_retention'], 1)
        self.assertEqual(legacy['source_retention_days'], None)
        self.assertIsNone(legacy['rows_beyond_source_retention'])
        self.assertEqual(legacy['sample_quality']['unknown_windows'], 1)

    def test_all_endpoint_scope_and_command_output_are_aggregated(self):
        self.add_sample(minutes_ago=1)
        report = build_telemetry_storage_report(
            all_endpoints=True, window_days=1, sample_limit=1, now=self.now)
        self.assertEqual(report['scope'], 'all_endpoints')

        from io import StringIO
        output = StringIO()
        call_command('report_telemetry_storage', all_endpoints=True,
                     window_days=1, sample_limit=1, stdout=output)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload['read_only'])
        self.assertNotIn(str(self.endpoint.pk), output.getvalue())

    def test_invalid_scope_window_or_sample_limit_fails_closed(self):
        for kwargs in (
            {},
            {'all_endpoints': True, 'endpoint_id': self.endpoint.pk},
            {'all_endpoints': True, 'window_days': 0},
            {'all_endpoints': True, 'sample_limit': 1001},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_telemetry_storage_report(**kwargs)
