import math
from datetime import datetime

from django.utils import timezone

from .models import AgentMachine, EndpointPerformanceSample


TELEMETRY_SAMPLE_INTERVAL_SECONDS = 300
TELEMETRY_GAP_TOLERANCE_SECONDS = 120
TELEMETRY_GAP_THRESHOLD_SECONDS = (
    TELEMETRY_SAMPLE_INTERVAL_SECONDS + TELEMETRY_GAP_TOLERANCE_SECONDS
)


def _percentile(sorted_values, fraction):
    position = (len(sorted_values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def _statistics(values):
    ordered = sorted(values)
    if not ordered:
        return {'valid_samples': 0, 'min': None, 'max': None, 'avg': None,
                'p50': None, 'p95': None, 'p99': None}
    return {
        'valid_samples': len(ordered),
        'min': ordered[0],
        'max': ordered[-1],
        'avg': math.fsum(ordered) / len(ordered),
        'p50': _percentile(ordered, 0.50),
        'p95': _percentile(ordered, 0.95),
        'p99': _percentile(ordered, 0.99),
    }


def build_endpoint_telemetry_summary(endpoint, start, end):
    if not isinstance(endpoint, AgentMachine) or endpoint._state.adding:
        raise ValueError('endpoint must be a persisted AgentMachine')
    if not isinstance(start, datetime) or not timezone.is_aware(start):
        raise ValueError('start must be timezone-aware')
    if not isinstance(end, datetime) or not timezone.is_aware(end):
        raise ValueError('end must be timezone-aware')
    if end <= start:
        raise ValueError('end must be after start')

    effective_end = min(end, timezone.now())
    expected = (math.ceil((effective_end - start).total_seconds() / TELEMETRY_SAMPLE_INTERVAL_SECONDS)
                if effective_end > start else 0)
    fields = (
        'cpu_percent', 'memory_used_percent', 'memory_committed_percent',
        'network_received_bytes', 'network_sent_bytes',
        'collection_duration_ms', 'agent_working_set_bytes', 'telemetry_errors_count',
    )
    series = {name: [] for name in fields}
    received = 0
    first_sample_at = None
    last_sample_at = None
    largest_gap = None
    gaps_over_threshold = 0
    lags = []
    negative_lags = 0
    samples = (EndpointPerformanceSample.objects
               .filter(endpoint=endpoint, collected_at__gte=start, collected_at__lt=end)
               .order_by('collected_at', 'id')
               .values_list('collected_at', 'created_at', *fields))
    for row in samples:
        collected_at, created_at, *values = row
        received += 1
        if first_sample_at is None:
            first_sample_at = collected_at
        if last_sample_at is not None:
            gap = (collected_at - last_sample_at).total_seconds()
            largest_gap = max(largest_gap or 0, gap)
            gaps_over_threshold += gap > TELEMETRY_GAP_THRESHOLD_SECONDS
        last_sample_at = collected_at
        lag = (created_at - collected_at).total_seconds()
        lags.append(lag)
        negative_lags += lag < 0
        for name, value in zip(fields, values):
            if value is not None:
                series[name].append(value)

    def observed_bytes(name):
        values = series[name]
        return {'valid_samples': len(values),
                'observed_total_bytes': sum(values) if values else None}

    errors = series['telemetry_errors_count']
    return {
        'schema_version': 1,
        'endpoint_id': str(endpoint.pk),
        'window': {
            'start': start.isoformat(),
            'end': end.isoformat(),
            'duration_seconds': (end - start).total_seconds(),
        },
        'samples': {'received': received},
        'cpu': {'usage_percent': _statistics(series['cpu_percent'])},
        'memory': {
            'used_percent': _statistics(series['memory_used_percent']),
            'committed_percent': _statistics(series['memory_committed_percent']),
        },
        'network': {
            'received_bytes': observed_bytes('network_received_bytes'),
            'sent_bytes': observed_bytes('network_sent_bytes'),
        },
        'agent': {
            'collection_duration_ms': _statistics(series['collection_duration_ms']),
            'working_set_bytes': _statistics(series['agent_working_set_bytes']),
            'telemetry_errors': {
                'samples_with_errors': sum(value > 0 for value in errors),
                'total_errors': sum(errors),
            },
        },
        'quality': {
            'expected_samples': expected,
            'received_samples': received,
            'coverage_percent': received / expected * 100 if expected else None,
            'over_expected_samples': max(received - expected, 0),
            'first_sample_at': first_sample_at.isoformat() if first_sample_at else None,
            'last_sample_at': last_sample_at.isoformat() if last_sample_at else None,
            'largest_internal_gap_seconds': largest_gap,
            'gap_threshold_seconds': TELEMETRY_GAP_THRESHOLD_SECONDS,
            'gaps_over_threshold_count': gaps_over_threshold,
            'metric_validity': {
                name: {'valid_samples': len(series[name])} for name in fields
            },
            'ingestion_lag_seconds': _statistics(lags),
            'negative_lag_samples': negative_lags,
        },
    }
