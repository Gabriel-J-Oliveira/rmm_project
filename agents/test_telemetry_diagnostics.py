import json

from django.test import TestCase

from .telemetry_diagnostics import (
    DIAGNOSTIC_METRICS,
    EVIDENCE_COVERAGE_PARTIAL_PERCENT,
    EVIDENCE_COVERAGE_SUFFICIENT_PERCENT,
    EVIDENCE_VALIDITY_PARTIAL_PERCENT,
    EVIDENCE_VALIDITY_SUFFICIENT_PERCENT,
    build_telemetry_evidence,
)


def summary(expected=288, received=288, valid=None, coverage=None, gaps=0, negative_lags=0, errors=0):
    if valid is None:
        valid = received
    if coverage is None and expected:
        coverage = received / expected * 100
    return {
        'schema_version': 1,
        'window': {
            'start': '2026-09-30T00:00:00+00:00',
            'end': '2026-10-01T00:00:00+00:00',
            'duration_seconds': 86400,
        },
        'quality': {
            'expected_samples': expected,
            'received_samples': received,
            'coverage_percent': coverage,
            'metric_validity': {name: {'valid_samples': valid} for name in DIAGNOSTIC_METRICS},
            'gaps_over_threshold_count': gaps,
            'negative_lag_samples': negative_lags,
        },
        'agent': {'telemetry_errors': {'samples_with_errors': int(errors > 0), 'total_errors': errors}},
    }


class TelemetryEvidenceTests(TestCase):
    def test_thresholds_are_explicit(self):
        self.assertEqual((EVIDENCE_COVERAGE_SUFFICIENT_PERCENT, EVIDENCE_COVERAGE_PARTIAL_PERCENT), (90, 60))
        self.assertEqual((EVIDENCE_VALIDITY_SUFFICIENT_PERCENT, EVIDENCE_VALIDITY_PARTIAL_PERCENT), (90, 60))

    def test_perfect_evidence_is_sufficient_and_json_safe(self):
        with self.assertNumQueries(0):
            result = build_telemetry_evidence(summary())
        self.assertEqual(result['evidence']['overall']['status'], 'SUFFICIENT')
        self.assertTrue(all(item['status'] == 'SUFFICIENT' for item in result['evidence']['metrics'].values()))
        json.dumps(result)

    def test_real_m4_24h_validity(self):
        data = summary()
        data['quality']['metric_validity']['cpu_percent']['valid_samples'] = 287
        result = build_telemetry_evidence(data)
        self.assertAlmostEqual(result['evidence']['metrics']['cpu_percent']['validity_percent'], 287 / 288 * 100)
        self.assertTrue(all(item['status'] == 'SUFFICIENT' for item in result['evidence']['metrics'].values()))

    def test_current_rolling_hour_is_insufficient_despite_full_validity(self):
        result = build_telemetry_evidence(summary(expected=12, received=4))
        self.assertEqual(result['evidence']['overall']['status'], 'INSUFFICIENT')
        self.assertEqual(result['evidence']['metrics']['cpu_percent']['validity_percent'], 100)
        self.assertIn('coverage_below_minimum', result['evidence']['metrics']['cpu_percent']['reasons'])

    def test_partial_7d_history_is_insufficient(self):
        result = build_telemetry_evidence(summary(expected=2016, received=746))
        self.assertEqual(result['evidence']['overall']['status'], 'INSUFFICIENT')
        self.assertIn('coverage_below_minimum', result['evidence']['overall']['reasons'])

    def test_partial_coverage_and_partial_validity(self):
        coverage = build_telemetry_evidence(summary(expected=100, received=75))
        validity = build_telemetry_evidence(summary(expected=100, received=100, valid=75))
        self.assertEqual(coverage['evidence']['metrics']['cpu_percent']['status'], 'PARTIAL')
        self.assertIn('coverage_partial', coverage['evidence']['overall']['reasons'])
        self.assertEqual(validity['evidence']['metrics']['cpu_percent']['status'], 'PARTIAL')
        self.assertIn('metric_validity_partial', validity['evidence']['overall']['reasons'])

    def test_insufficient_validity(self):
        result = build_telemetry_evidence(summary(expected=100, received=100, valid=40))
        self.assertEqual(result['evidence']['metrics']['cpu_percent']['status'], 'INSUFFICIENT')
        self.assertIn('metric_validity_below_minimum', result['evidence']['overall']['reasons'])

    def test_zero_expected_with_or_without_received_samples(self):
        for received in (0, 4):
            with self.subTest(received=received):
                result = build_telemetry_evidence(summary(expected=0, received=received))
                self.assertEqual(result['evidence']['overall']['status'], 'INSUFFICIENT')
                self.assertIn('no_expected_samples', result['evidence']['overall']['reasons'])
                self.assertEqual(result['evidence']['metrics']['cpu_percent']['validity_percent'],
                                 100 if received else None)

    def test_zero_received_and_zero_valid(self):
        empty = build_telemetry_evidence(summary(expected=12, received=0))
        self.assertEqual(empty['evidence']['metrics']['cpu_percent']['validity_percent'], None)
        self.assertIn('no_samples', empty['evidence']['overall']['reasons'])
        self.assertIn('metric_no_valid_samples', empty['evidence']['metrics']['cpu_percent']['reasons'])
        no_valid = build_telemetry_evidence(summary(expected=12, received=12, valid=0))
        self.assertEqual(no_valid['evidence']['metrics']['cpu_percent']['status'], 'INSUFFICIENT')

    def test_coverage_over_100_is_not_clamped(self):
        result = build_telemetry_evidence(summary(expected=12, received=13))
        self.assertEqual(result['evidence']['overall']['status'], 'SUFFICIENT')
        self.assertAlmostEqual(result['evidence']['overall']['coverage_percent'], 13 / 12 * 100)

    def test_gaps_are_informational(self):
        result = build_telemetry_evidence(summary(gaps=2))
        self.assertEqual(result['evidence']['overall']['status'], 'SUFFICIENT')
        self.assertIn('internal_gaps_present', result['evidence']['overall']['reasons'])
        self.assertEqual(result['evidence']['facts']['gaps_over_threshold_count'], 2)

    def test_negative_lag_and_errors_are_informational(self):
        result = build_telemetry_evidence(summary(negative_lags=2, errors=3))
        self.assertEqual(result['evidence']['overall']['status'], 'SUFFICIENT')
        self.assertIn('negative_ingestion_lag_present', result['evidence']['overall']['reasons'])
        self.assertIn('telemetry_errors_present', result['evidence']['overall']['reasons'])
        self.assertEqual(result['evidence']['facts']['telemetry_errors_total'], 3)

    def test_one_metric_can_lower_overall_without_lowering_other_metrics(self):
        data = summary()
        data['quality']['metric_validity']['cpu_percent']['valid_samples'] = 100
        result = build_telemetry_evidence(data)
        self.assertEqual(result['evidence']['overall']['status'], 'INSUFFICIENT')
        self.assertEqual(result['evidence']['metrics']['memory_used_percent']['status'], 'SUFFICIENT')

    def test_threshold_boundaries(self):
        for percent, status in ((59, 'INSUFFICIENT'), (60, 'PARTIAL'), (89, 'PARTIAL'), (90, 'SUFFICIENT')):
            with self.subTest(percent=percent):
                result = build_telemetry_evidence(summary(expected=100, received=percent))
                self.assertEqual(result['evidence']['overall']['status'], status)

    def test_validity_threshold_boundaries(self):
        for percent, status in ((59, 'INSUFFICIENT'), (60, 'PARTIAL'), (89, 'PARTIAL'), (90, 'SUFFICIENT')):
            with self.subTest(percent=percent):
                result = build_telemetry_evidence(summary(expected=100, received=100, valid=percent))
                self.assertEqual(result['evidence']['overall']['status'], status)

    def test_incompatible_contract_raises_value_error(self):
        for data in (None, {}, {'schema_version': 2}, {'schema_version': 1, 'quality': {}}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                build_telemetry_evidence(data)
        data = summary()
        del data['quality']['metric_validity']['cpu_percent']
        with self.assertRaises(ValueError):
            build_telemetry_evidence(data)
        data = summary()
        data['quality']['metric_validity']['cpu_percent']['valid_samples'] = 289
        with self.assertRaises(ValueError):
            build_telemetry_evidence(data)
