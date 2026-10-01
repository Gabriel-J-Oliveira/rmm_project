import json

from django.test import TestCase

from .telemetry_diagnostics import (
    CPU_AVG_ELEVATED_PERCENT,
    CPU_AVG_PRESSURE_PERCENT,
    CPU_P95_ELEVATED_PERCENT,
    CPU_P95_PRESSURE_PERCENT,
    CPU_P99_SPIKE_PERCENT,
    MEMORY_P95_ELEVATED_PERCENT,
    MEMORY_P95_PRESSURE_PERCENT,
    MEMORY_P99_SPIKE_PERCENT,
    build_resource_diagnostics,
    build_telemetry_evidence,
)


def summary(expected=288, received=None, valid=None, cpu=(20, 40, 60),
            used=(20, 40, 60), committed=(20, 40, 60), duration=86400):
    if received is None:
        received = expected
    if valid is None:
        valid = {}

    def stats(values):
        return dict(zip(('avg', 'p95', 'p99'), values))

    return {
        'schema_version': 1,
        'window': {
            'start': '2026-09-30T00:00:00+00:00',
            'end': '2026-10-01T00:00:00+00:00',
            'duration_seconds': duration,
        },
        'quality': {
            'expected_samples': expected,
            'received_samples': received,
            'coverage_percent': received / expected * 100 if expected else None,
            'metric_validity': {
                name: {'valid_samples': valid.get(name, received)}
                for name in ('cpu_percent', 'memory_used_percent', 'memory_committed_percent')
            },
            'gaps_over_threshold_count': 0,
            'negative_lag_samples': 0,
        },
        'agent': {'telemetry_errors': {'samples_with_errors': 0, 'total_errors': 0}},
        'cpu': {'usage_percent': stats(cpu)},
        'memory': {'used_percent': stats(used), 'committed_percent': stats(committed)},
    }


class ResourceDiagnosticsTests(TestCase):
    def test_threshold_constants(self):
        self.assertEqual((CPU_AVG_ELEVATED_PERCENT, CPU_AVG_PRESSURE_PERCENT), (50, 70))
        self.assertEqual((CPU_P95_ELEVATED_PERCENT, CPU_P95_PRESSURE_PERCENT, CPU_P99_SPIKE_PERCENT),
                         (70, 85, 85))
        self.assertEqual((MEMORY_P95_ELEVATED_PERCENT, MEMORY_P95_PRESSURE_PERCENT,
                          MEMORY_P99_SPIKE_PERCENT), (80, 90, 90))

    def test_real_m4_24h_case_and_evidence_reuse(self):
        data = summary(cpu=(2.81, 4.55, 6.63), used=(20.83, 22.86, 28.89),
                       committed=(14.32, 14.62, 19.95), valid={'cpu_percent': 287})
        with self.assertNumQueries(0):
            result = build_resource_diagnostics(data)
        self.assertEqual(result['evidence'], build_telemetry_evidence(data)['evidence'])
        self.assertEqual(result['diagnostics']['cpu']['status'], 'NO_PRESSURE_OBSERVED')
        self.assertEqual(result['diagnostics']['memory']['status'], 'NO_PRESSURE_OBSERVED')
        self.assertAlmostEqual(result['evidence']['metrics']['cpu_percent']['validity_percent'], 287 / 288 * 100)
        self.assertEqual(result['window']['duration_seconds'], 86400)
        json.dumps(result)

    def test_partial_rolling_hour_skips_even_high_statistics(self):
        data = summary(expected=12, received=4, cpu=(95, 99, 100),
                       used=(90, 99, 100), committed=(90, 99, 100), duration=3600)
        result = build_resource_diagnostics(data)
        self.assertEqual(result['diagnostics']['cpu']['status'], 'NOT_EVALUATED')
        self.assertEqual(result['diagnostics']['memory']['status'], 'NOT_EVALUATED')
        self.assertEqual(result['diagnostics']['cpu']['evidence_status'], 'INSUFFICIENT')
        self.assertEqual(result['diagnostics']['cpu']['reasons'], ['evidence_not_sufficient'])
        self.assertEqual(result['diagnostics']['memory']['facts'], {})
        self.assertEqual(result['window']['duration_seconds'], 3600)

    def test_cpu_categories_and_reasons(self):
        cases = (
            ((70, 70, 75), 'PRESSURE', ['cpu_average_pressure']),
            ((20, 85, 90), 'PRESSURE', ['cpu_p95_pressure']),
            ((70, 85, 90), 'PRESSURE', ['cpu_average_pressure', 'cpu_p95_pressure']),
            ((50, 60, 70), 'ELEVATED', ['cpu_average_elevated']),
            ((20, 70, 75), 'ELEVATED', ['cpu_p95_elevated']),
            ((20, 60, 85), 'SPIKY', ['cpu_p99_spike']),
            ((20, 40, 60), 'NO_PRESSURE_OBSERVED', ['cpu_below_elevated_thresholds']),
        )
        for values, status, reasons in cases:
            with self.subTest(values=values):
                cpu = build_resource_diagnostics(summary(cpu=values))['diagnostics']['cpu']
                self.assertEqual((cpu['status'], cpu['reasons']), (status, reasons))
                self.assertEqual(cpu['facts'], dict(zip(('avg_percent', 'p95_percent', 'p99_percent'), values)))

    def test_cpu_boundaries(self):
        cases = (
            ((49.999, 60, 70), 'NO_PRESSURE_OBSERVED'),
            ((50, 60, 70), 'ELEVATED'),
            ((69.999, 70, 80), 'ELEVATED'),
            ((70, 70, 80), 'PRESSURE'),
            ((20, 69.999, 80), 'NO_PRESSURE_OBSERVED'),
            ((20, 70, 80), 'ELEVATED'),
            ((20, 84.999, 90), 'ELEVATED'),
            ((20, 85, 90), 'PRESSURE'),
            ((20, 60, 84.999), 'NO_PRESSURE_OBSERVED'),
            ((20, 60, 85), 'SPIKY'),
        )
        for values, status in cases:
            with self.subTest(values=values):
                self.assertEqual(build_resource_diagnostics(summary(cpu=values))['diagnostics']['cpu']['status'],
                                 status)

    def test_cpu_priority(self):
        for values, status in (((20, 85, 99), 'PRESSURE'), ((20, 70, 99), 'ELEVATED')):
            with self.subTest(values=values):
                self.assertEqual(build_resource_diagnostics(summary(cpu=values))['diagnostics']['cpu']['status'],
                                 status)

    def test_memory_categories_for_used_and_committed(self):
        cases = (
            ((20, 90, 95), (20, 40, 60), 'PRESSURE', ['memory_used_p95_pressure']),
            ((20, 40, 60), (20, 90, 95), 'PRESSURE', ['memory_committed_p95_pressure']),
            ((20, 90, 95), (20, 90, 95), 'PRESSURE',
             ['memory_used_p95_pressure', 'memory_committed_p95_pressure']),
            ((20, 80, 85), (20, 40, 60), 'ELEVATED', ['memory_used_p95_elevated']),
            ((20, 40, 60), (20, 80, 85), 'ELEVATED', ['memory_committed_p95_elevated']),
            ((20, 60, 90), (20, 60, 70), 'SPIKY', ['memory_used_p99_spike']),
            ((20, 60, 70), (20, 60, 90), 'SPIKY', ['memory_committed_p99_spike']),
            ((20, 60, 70), (20, 60, 70), 'NO_PRESSURE_OBSERVED',
             ['memory_below_elevated_thresholds']),
        )
        for used, committed, status, reasons in cases:
            with self.subTest(used=used, committed=committed):
                memory = build_resource_diagnostics(summary(used=used, committed=committed))['diagnostics']['memory']
                self.assertEqual((memory['status'], memory['reasons']), (status, reasons))
                self.assertEqual(memory['facts']['used_p95_percent'], used[1])
                self.assertEqual(memory['facts']['committed_p95_percent'], committed[1])

    def test_memory_boundaries_for_both_signals(self):
        baseline = (20, 40, 60)
        for signal in ('used', 'committed'):
            for p95, status in ((79.999, 'NO_PRESSURE_OBSERVED'), (80, 'ELEVATED'),
                                (89.999, 'ELEVATED'), (90, 'PRESSURE')):
                with self.subTest(signal=signal, p95=p95):
                    values = (20, p95, p95)
                    args = {signal: values, 'committed' if signal == 'used' else 'used': baseline}
                    self.assertEqual(build_resource_diagnostics(summary(**args))['diagnostics']['memory']['status'],
                                     status)
            for p99, status in ((89.999, 'NO_PRESSURE_OBSERVED'), (90, 'SPIKY')):
                with self.subTest(signal=signal, p99=p99):
                    args = {signal: (20, 60, p99), 'committed' if signal == 'used' else 'used': baseline}
                    self.assertEqual(build_resource_diagnostics(summary(**args))['diagnostics']['memory']['status'],
                                     status)

    def test_memory_priority(self):
        for p95, status in ((90, 'PRESSURE'), (80, 'ELEVATED')):
            with self.subTest(p95=p95):
                result = build_resource_diagnostics(summary(used=(20, p95, 100)))
                self.assertEqual(result['diagnostics']['memory']['status'], status)

    def test_partial_evidence_and_mixed_memory_evidence(self):
        high = {'cpu': (95, 99, 100), 'used': (95, 99, 100), 'committed': (95, 99, 100)}
        partial = build_resource_diagnostics(summary(expected=100, received=75, **high))
        self.assertEqual(partial['diagnostics']['cpu']['status'], 'NOT_EVALUATED')
        self.assertEqual(partial['diagnostics']['memory']['status'], 'NOT_EVALUATED')
        cpu_partial = build_resource_diagnostics(summary(expected=100, received=100,
                                                         valid={'cpu_percent': 75}, **high))
        self.assertEqual(cpu_partial['diagnostics']['cpu']['status'], 'NOT_EVALUATED')
        self.assertEqual(cpu_partial['diagnostics']['memory']['status'], 'PRESSURE')
        for metric in ('memory_used_percent', 'memory_committed_percent'):
            with self.subTest(metric=metric):
                mixed = build_resource_diagnostics(summary(expected=100, received=100,
                                                           valid={metric: 75}, **high))
                self.assertEqual(mixed['diagnostics']['cpu']['status'], 'PRESSURE')
                self.assertEqual(mixed['diagnostics']['memory']['status'], 'NOT_EVALUATED')
                self.assertEqual(mixed['diagnostics']['memory']['evidence_status'][
                    'used_percent' if metric == 'memory_used_percent' else 'committed_percent'], 'PARTIAL')

    def test_invalid_statistics_raise_only_when_evidence_is_sufficient(self):
        for section, metric in (('cpu', 'usage_percent'), ('memory', 'used_percent'),
                                ('memory', 'committed_percent')):
            for name, value in (('avg', None), ('p95', float('nan')), ('p99', float('inf')),
                                ('avg', -1), ('p95', 101), ('p99', True)):
                with self.subTest(section=section, metric=metric, name=name, value=value):
                    data = summary()
                    data[section][metric][name] = value
                    with self.assertRaises(ValueError):
                        build_resource_diagnostics(data)
            data = summary()
            data[section][metric]['p95'] = 80
            data[section][metric]['p99'] = 79
            with self.assertRaises(ValueError):
                build_resource_diagnostics(data)
        data = summary(expected=100, received=40)
        del data['cpu']
        del data['memory']
        result = build_resource_diagnostics(data)
        self.assertEqual(result['diagnostics']['cpu']['status'], 'NOT_EVALUATED')
        self.assertEqual(result['diagnostics']['memory']['status'], 'NOT_EVALUATED')

    def test_no_max_or_p50_gate_and_no_recommendation(self):
        data = summary()
        data['cpu']['usage_percent'].update({'min': 0, 'max': 100, 'p50': 2})
        data['memory']['used_percent'].update({'min': 0, 'max': 100, 'p50': 2})
        result = build_resource_diagnostics(data)
        self.assertEqual(result['diagnostics']['cpu']['status'], 'NO_PRESSURE_OBSERVED')
        self.assertEqual(result['diagnostics']['memory']['status'], 'NO_PRESSURE_OBSERVED')
        self.assertNotIn('recommendation', result)
