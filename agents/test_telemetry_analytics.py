import json
import uuid
from datetime import datetime, timedelta, timezone as datetime_timezone

from django.test import TestCase

from .models import AgentMachine, EndpointPerformanceSample, hash_agent_token
from .telemetry_analytics import _statistics, build_endpoint_telemetry_summary


class EndpointTelemetryAnalyticsTests(TestCase):
    def setUp(self):
        self.endpoint = self.make_endpoint('ANALYTICS-A')
        self.other = self.make_endpoint('ANALYTICS-B')
        self.start = datetime(2026, 9, 1, tzinfo=datetime_timezone.utc)
        self.end = self.start + timedelta(hours=1)

    def make_endpoint(self, hostname):
        return AgentMachine.objects.create(
            hostname=hostname,
            machine_id=str(uuid.uuid4()),
            agent_token_hash=hash_agent_token(f'synthetic-{hostname}-credential'),
        )

    def sample(self, at, endpoint=None, **values):
        data = {
            'endpoint': endpoint or self.endpoint,
            'sample_id': uuid.uuid4(),
            'collected_at': at,
            'uptime_seconds': 1200,
            'collection_duration_ms': 25,
            'telemetry_errors_count': 0,
        }
        data.update(values)
        return EndpointPerformanceSample.objects.create(**data)

    def summary(self, endpoint=None, start=None, end=None):
        return build_endpoint_telemetry_summary(
            endpoint or self.endpoint, start or self.start, end or self.end,
        )

    def test_empty_window_is_serializable_and_has_no_observed_values(self):
        with self.assertNumQueries(1):
            result = self.summary()
        self.assertEqual(json.loads(json.dumps(result)), result)
        self.assertEqual(result['endpoint_id'], str(self.endpoint.pk))
        self.assertEqual(result['window'], {
            'start': self.start.isoformat(), 'end': self.end.isoformat(),
            'duration_seconds': 3600.0,
        })
        self.assertEqual(result['samples']['received'], 0)
        for stats in (result['cpu']['usage_percent'], *result['memory'].values(),
                      result['agent']['collection_duration_ms'], result['agent']['working_set_bytes']):
            self.assertEqual(stats, {'valid_samples': 0, 'min': None, 'max': None,
                                     'avg': None, 'p50': None, 'p95': None, 'p99': None})
        for network in result['network'].values():
            self.assertEqual(network, {'valid_samples': 0, 'observed_total_bytes': None})
        self.assertEqual(result['agent']['telemetry_errors'], {'samples_with_errors': 0, 'total_errors': 0})
        self.assertNotIn('disk', result)
        self.assertNotIn('processes', result)

    def test_window_includes_start_and_excludes_end(self):
        for at in (self.start - timedelta(seconds=1), self.start,
                   self.start + timedelta(minutes=30), self.end,
                   self.end + timedelta(seconds=1)):
            self.sample(at, cpu_percent=10)
        self.assertEqual(self.summary()['samples']['received'], 2)

    def test_endpoint_isolation(self):
        self.sample(self.start, cpu_percent=10)
        self.sample(self.start, endpoint=self.other, cpu_percent=90)
        self.assertEqual(self.summary()['cpu']['usage_percent']['avg'], 10)
        self.assertEqual(self.summary(endpoint=self.other)['cpu']['usage_percent']['avg'], 90)

    def test_nulls_are_metric_specific(self):
        for minute, cpu in enumerate((10, None, 30)):
            self.sample(self.start + timedelta(minutes=minute), cpu_percent=cpu,
                        memory_used_percent=None if minute == 0 else 40,
                        memory_committed_percent=None, agent_working_set_bytes=None)
        result = self.summary()
        self.assertEqual(result['samples']['received'], 3)
        cpu = result['cpu']['usage_percent']
        self.assertEqual(cpu['valid_samples'], 2)
        self.assertEqual((cpu['min'], cpu['max'], cpu['avg'], cpu['p50']), (10, 30, 20, 20))
        self.assertEqual((cpu['p95'], cpu['p99']), (29, 29.8))
        self.assertEqual(result['memory']['used_percent']['valid_samples'], 2)
        self.assertEqual(result['memory']['committed_percent']['valid_samples'], 0)
        self.assertEqual(result['agent']['working_set_bytes']['valid_samples'], 0)
        self.assertEqual(result['agent']['collection_duration_ms']['valid_samples'], 3)

    def test_percentiles_use_linear_interpolation(self):
        self.assertIsNone(_statistics([])['p95'])
        self.assertEqual(_statistics([7]), {
            'valid_samples': 1, 'min': 7, 'max': 7, 'avg': 7,
            'p50': 7, 'p95': 7, 'p99': 7,
        })
        two = _statistics([20, 10])
        self.assertEqual((two['p50'], two['p95'], two['p99']), (15, 19.5, 19.9))
        four = _statistics([4, 1, 3, 2])
        self.assertEqual(four['p50'], 2.5)
        self.assertAlmostEqual(four['p95'], 3.85)
        self.assertAlmostEqual(four['p99'], 3.97)

    def test_network_deltas_distinguish_zero_from_missing(self):
        cases = (
            ((None, 100, 200), 2, 300),
            ((0, 0), 2, 0),
            ((None, None), 0, None),
        )
        for offset, (values, valid, total) in enumerate(cases):
            start = self.start + timedelta(hours=offset)
            for minute, value in enumerate(values):
                self.sample(start + timedelta(minutes=minute),
                            network_received_bytes=value, network_sent_bytes=value)
            result = self.summary(start=start, end=start + timedelta(hours=1))
            for direction in result['network'].values():
                self.assertEqual(direction, {'valid_samples': valid, 'observed_total_bytes': total})
                self.assertNotIn('rate', direction)

    def test_error_summary_counts_samples_and_errors(self):
        for minute, errors in enumerate((0, 2, 0, 1)):
            self.sample(self.start + timedelta(minutes=minute), telemetry_errors_count=errors)
        self.assertEqual(self.summary()['agent']['telemetry_errors'],
                         {'samples_with_errors': 2, 'total_errors': 3})

    def test_invalid_time_window_is_rejected(self):
        naive = self.start.replace(tzinfo=None)
        for start, end in ((naive, self.end), (self.start, self.end.replace(tzinfo=None)),
                           (self.start, self.start), (self.end, self.start)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.summary(start=start, end=end)

    def test_endpoint_must_be_persisted_machine(self):
        for endpoint in (str(self.endpoint.pk), AgentMachine(hostname='UNSAVED')):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                build_endpoint_telemetry_summary(endpoint, self.start, self.end)
