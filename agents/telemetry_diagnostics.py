"""Evidence quality for diagnostic rules, independent of resource diagnosis."""

import math


EVIDENCE_COVERAGE_SUFFICIENT_PERCENT = 90
EVIDENCE_COVERAGE_PARTIAL_PERCENT = 60
EVIDENCE_VALIDITY_SUFFICIENT_PERCENT = 90
EVIDENCE_VALIDITY_PARTIAL_PERCENT = 60

STATUS_SUFFICIENT = 'SUFFICIENT'
STATUS_PARTIAL = 'PARTIAL'
STATUS_INSUFFICIENT = 'INSUFFICIENT'

DIAGNOSTIC_METRICS = (
    'cpu_percent',
    'memory_used_percent',
    'memory_committed_percent',
)

_STATUS_RANK = {
    STATUS_SUFFICIENT: 0,
    STATUS_PARTIAL: 1,
    STATUS_INSUFFICIENT: 2,
}


def _nonnegative_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{name} must be a nonnegative integer')
    return value


def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    return value


def _status(percent, sufficient, partial):
    if percent >= sufficient:
        return STATUS_SUFFICIENT
    if percent >= partial:
        return STATUS_PARTIAL
    return STATUS_INSUFFICIENT


def build_telemetry_evidence(summary):
    """Classify whether an M4 summary can support later diagnostic rules."""
    if not isinstance(summary, dict) or summary.get('schema_version') != 1:
        raise ValueError('expected M4 summary schema version 1')
    window = summary.get('window')
    quality = summary.get('quality')
    agent = summary.get('agent')
    if not isinstance(window, dict) or not isinstance(quality, dict) or not isinstance(agent, dict):
        raise ValueError('summary window, quality and agent are required')
    if not isinstance(window.get('start'), str) or not isinstance(window.get('end'), str):
        raise ValueError('summary window timestamps must be strings')
    duration = _finite_number(window.get('duration_seconds'), 'window.duration_seconds')
    if duration <= 0:
        raise ValueError('window.duration_seconds must be positive')

    expected = _nonnegative_int(quality.get('expected_samples'), 'quality.expected_samples')
    received = _nonnegative_int(quality.get('received_samples'), 'quality.received_samples')
    coverage = quality.get('coverage_percent')
    if coverage is None:
        if expected != 0:
            raise ValueError('quality.coverage_percent is required when samples are expected')
    else:
        _finite_number(coverage, 'quality.coverage_percent')
        if coverage < 0:
            raise ValueError('quality.coverage_percent cannot be negative')
    validity = quality.get('metric_validity')
    if not isinstance(validity, dict):
        raise ValueError('quality.metric_validity is required')
    gaps = _nonnegative_int(quality.get('gaps_over_threshold_count'), 'quality.gaps_over_threshold_count')
    negative_lags = _nonnegative_int(quality.get('negative_lag_samples'), 'quality.negative_lag_samples')
    errors = agent.get('telemetry_errors')
    if not isinstance(errors, dict):
        raise ValueError('agent.telemetry_errors is required')
    error_samples = _nonnegative_int(errors.get('samples_with_errors'), 'agent.telemetry_errors.samples_with_errors')
    total_errors = _nonnegative_int(errors.get('total_errors'), 'agent.telemetry_errors.total_errors')

    shared_reasons = []
    if expected == 0:
        shared_reasons.append('no_expected_samples')
    if received == 0:
        shared_reasons.append('no_samples')
    if expected == 0 or received == 0:
        coverage_status = STATUS_INSUFFICIENT
    else:
        coverage_status = _status(
            coverage, EVIDENCE_COVERAGE_SUFFICIENT_PERCENT, EVIDENCE_COVERAGE_PARTIAL_PERCENT
        )
        if coverage_status == STATUS_PARTIAL:
            shared_reasons.append('coverage_partial')
        elif coverage_status == STATUS_INSUFFICIENT:
            shared_reasons.append('coverage_below_minimum')
    if gaps:
        shared_reasons.append('internal_gaps_present')
    if negative_lags:
        shared_reasons.append('negative_ingestion_lag_present')
    if error_samples or total_errors:
        shared_reasons.append('telemetry_errors_present')

    metrics = {}
    for name in DIAGNOSTIC_METRICS:
        metric = validity.get(name)
        if not isinstance(metric, dict):
            raise ValueError(f'quality.metric_validity.{name} is required')
        valid = _nonnegative_int(metric.get('valid_samples'), f'quality.metric_validity.{name}.valid_samples')
        if valid > received:
            raise ValueError(f'quality.metric_validity.{name} exceeds received samples')
        validity_percent = valid / received * 100 if received else None
        metric_reasons = list(shared_reasons)
        if valid == 0:
            validity_status = STATUS_INSUFFICIENT
            metric_reasons.append('metric_no_valid_samples')
        else:
            validity_status = _status(
                validity_percent, EVIDENCE_VALIDITY_SUFFICIENT_PERCENT, EVIDENCE_VALIDITY_PARTIAL_PERCENT
            )
            if validity_status == STATUS_PARTIAL:
                metric_reasons.append('metric_validity_partial')
            elif validity_status == STATUS_INSUFFICIENT:
                metric_reasons.append('metric_validity_below_minimum')
        metrics[name] = {
            'status': max((coverage_status, validity_status), key=_STATUS_RANK.get),
            'received_samples': received,
            'valid_samples': valid,
            'validity_percent': validity_percent,
            'reasons': metric_reasons,
        }

    overall_status = max((item['status'] for item in metrics.values()), key=_STATUS_RANK.get)
    overall_reasons = list(dict.fromkeys(
        reason for item in metrics.values() for reason in item['reasons']
    ))
    return {
        'schema_version': 1,
        'window': {
            'start': window['start'],
            'end': window['end'],
            'duration_seconds': duration,
        },
        'evidence': {
            'overall': {
                'status': overall_status,
                'expected_samples': expected,
                'received_samples': received,
                'coverage_percent': coverage,
                'reasons': overall_reasons,
            },
            'metrics': metrics,
            'facts': {
                'gaps_over_threshold_count': gaps,
                'negative_lag_samples': negative_lags,
                'telemetry_error_samples': error_samples,
                'telemetry_errors_total': total_errors,
            },
        },
    }
