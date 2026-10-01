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


DIAGNOSTIC_NOT_EVALUATED = 'NOT_EVALUATED'
DIAGNOSTIC_NO_PRESSURE = 'NO_PRESSURE_OBSERVED'
DIAGNOSTIC_SPIKY = 'SPIKY'
DIAGNOSTIC_ELEVATED = 'ELEVATED'
DIAGNOSTIC_PRESSURE = 'PRESSURE'

CPU_AVG_ELEVATED_PERCENT = 50
CPU_AVG_PRESSURE_PERCENT = 70
CPU_P95_ELEVATED_PERCENT = 70
CPU_P95_PRESSURE_PERCENT = 85
CPU_P99_SPIKE_PERCENT = 85

MEMORY_P95_ELEVATED_PERCENT = 80
MEMORY_P95_PRESSURE_PERCENT = 90
MEMORY_P99_SPIKE_PERCENT = 90


def _resource_stats(summary, section, metric):
    group = summary.get(section)
    stats = group.get(metric) if isinstance(group, dict) else None
    if not isinstance(stats, dict):
        raise ValueError(f'{section}.{metric} statistics are required')
    values = {}
    for name in ('avg', 'p95', 'p99'):
        value = _finite_number(stats.get(name), f'{section}.{metric}.{name}')
        if not 0 <= value <= 100:
            raise ValueError(f'{section}.{metric}.{name} must be a percentage')
        values[name] = value
    if values['p95'] > values['p99']:
        raise ValueError(f'{section}.{metric}.p95 cannot exceed p99')
    return values


def _cpu_diagnostic(summary, evidence_status):
    if evidence_status != STATUS_SUFFICIENT:
        return {
            'status': DIAGNOSTIC_NOT_EVALUATED,
            'evidence_status': evidence_status,
            'facts': {},
            'reasons': ['evidence_not_sufficient'],
        }
    stats = _resource_stats(summary, 'cpu', 'usage_percent')
    avg, p95, p99 = stats['avg'], stats['p95'], stats['p99']
    if avg >= CPU_AVG_PRESSURE_PERCENT or p95 >= CPU_P95_PRESSURE_PERCENT:
        status = DIAGNOSTIC_PRESSURE
        reasons = []
        if avg >= CPU_AVG_PRESSURE_PERCENT:
            reasons.append('cpu_average_pressure')
        if p95 >= CPU_P95_PRESSURE_PERCENT:
            reasons.append('cpu_p95_pressure')
    elif avg >= CPU_AVG_ELEVATED_PERCENT or p95 >= CPU_P95_ELEVATED_PERCENT:
        status = DIAGNOSTIC_ELEVATED
        reasons = []
        if avg >= CPU_AVG_ELEVATED_PERCENT:
            reasons.append('cpu_average_elevated')
        if p95 >= CPU_P95_ELEVATED_PERCENT:
            reasons.append('cpu_p95_elevated')
    elif p99 >= CPU_P99_SPIKE_PERCENT:
        status = DIAGNOSTIC_SPIKY
        reasons = ['cpu_p99_spike']
    else:
        status = DIAGNOSTIC_NO_PRESSURE
        reasons = ['cpu_below_elevated_thresholds']
    return {
        'status': status,
        'evidence_status': evidence_status,
        'facts': {
            'avg_percent': avg,
            'p95_percent': p95,
            'p99_percent': p99,
        },
        'reasons': reasons,
    }


def _memory_diagnostic(summary, used_evidence, committed_evidence):
    evidence_status = {
        'used_percent': used_evidence,
        'committed_percent': committed_evidence,
    }
    if used_evidence != STATUS_SUFFICIENT or committed_evidence != STATUS_SUFFICIENT:
        return {
            'status': DIAGNOSTIC_NOT_EVALUATED,
            'evidence_status': evidence_status,
            'facts': {},
            'reasons': ['evidence_not_sufficient'],
        }
    used = _resource_stats(summary, 'memory', 'used_percent')
    committed = _resource_stats(summary, 'memory', 'committed_percent')
    if used['p95'] >= MEMORY_P95_PRESSURE_PERCENT or committed['p95'] >= MEMORY_P95_PRESSURE_PERCENT:
        status = DIAGNOSTIC_PRESSURE
        reasons = []
        if used['p95'] >= MEMORY_P95_PRESSURE_PERCENT:
            reasons.append('memory_used_p95_pressure')
        if committed['p95'] >= MEMORY_P95_PRESSURE_PERCENT:
            reasons.append('memory_committed_p95_pressure')
    elif used['p95'] >= MEMORY_P95_ELEVATED_PERCENT or committed['p95'] >= MEMORY_P95_ELEVATED_PERCENT:
        status = DIAGNOSTIC_ELEVATED
        reasons = []
        if used['p95'] >= MEMORY_P95_ELEVATED_PERCENT:
            reasons.append('memory_used_p95_elevated')
        if committed['p95'] >= MEMORY_P95_ELEVATED_PERCENT:
            reasons.append('memory_committed_p95_elevated')
    elif used['p99'] >= MEMORY_P99_SPIKE_PERCENT or committed['p99'] >= MEMORY_P99_SPIKE_PERCENT:
        status = DIAGNOSTIC_SPIKY
        reasons = []
        if used['p99'] >= MEMORY_P99_SPIKE_PERCENT:
            reasons.append('memory_used_p99_spike')
        if committed['p99'] >= MEMORY_P99_SPIKE_PERCENT:
            reasons.append('memory_committed_p99_spike')
    else:
        status = DIAGNOSTIC_NO_PRESSURE
        reasons = ['memory_below_elevated_thresholds']
    return {
        'status': status,
        'evidence_status': evidence_status,
        'facts': {
            'used_avg_percent': used['avg'],
            'used_p95_percent': used['p95'],
            'used_p99_percent': used['p99'],
            'committed_avg_percent': committed['avg'],
            'committed_p95_percent': committed['p95'],
            'committed_p99_percent': committed['p99'],
        },
        'reasons': reasons,
    }


def build_resource_diagnostics(summary):
    """Describe CPU and memory utilization patterns for one M4 summary window."""
    result = build_telemetry_evidence(summary)
    metrics = result['evidence']['metrics']
    return {
        'schema_version': 1,
        'window': result['window'],
        'evidence': result['evidence'],
        'diagnostics': {
            'cpu': _cpu_diagnostic(summary, metrics['cpu_percent']['status']),
            'memory': _memory_diagnostic(
                summary,
                metrics['memory_used_percent']['status'],
                metrics['memory_committed_percent']['status'],
            ),
        },
    }


CAPACITY_NOT_EVALUATED = 'NOT_EVALUATED'
CAPACITY_NO_PRESSURE = 'NO_PRESSURE_OBSERVED'
CAPACITY_OBSERVE = 'OBSERVE'
CAPACITY_SUSTAINED_PRESSURE = 'SUSTAINED_PRESSURE'

INVENTORY_AVAILABLE = 'AVAILABLE'
INVENTORY_PARTIAL = 'PARTIAL'
INVENTORY_MISSING = 'MISSING'

_RESOURCE_DIAGNOSTIC_STATUSES = {
    DIAGNOSTIC_NOT_EVALUATED,
    DIAGNOSTIC_NO_PRESSURE,
    DIAGNOSTIC_SPIKY,
    DIAGNOSTIC_ELEVATED,
    DIAGNOSTIC_PRESSURE,
}


def _capacity_input(value, name):
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValueError(f'{name} must be a version 1 resource diagnostic')
    window = value.get('window')
    diagnostics = value.get('diagnostics')
    if not isinstance(window, dict) or not isinstance(diagnostics, dict):
        raise ValueError(f'{name} window and diagnostics are required')
    if not isinstance(window.get('start'), str) or not isinstance(window.get('end'), str):
        raise ValueError(f'{name} window timestamps must be strings')
    duration = _finite_number(window.get('duration_seconds'), f'{name}.window.duration_seconds')
    if duration <= 0:
        raise ValueError(f'{name} window duration must be positive')
    statuses = {}
    for resource in ('cpu', 'memory'):
        diagnostic = diagnostics.get(resource)
        status = diagnostic.get('status') if isinstance(diagnostic, dict) else None
        if not isinstance(status, str) or status not in _RESOURCE_DIAGNOSTIC_STATUSES:
            raise ValueError(f'{name}.diagnostics.{resource}.status is invalid')
        statuses[resource] = status
    return {
        'window': {
            'start': window['start'],
            'end': window['end'],
            'duration_seconds': duration,
        },
        'statuses': statuses,
    }


def _capacity_resource(primary, context):
    reasons = []
    if primary == DIAGNOSTIC_NOT_EVALUATED:
        status = CAPACITY_NOT_EVALUATED
        reasons.append('primary_not_evaluated')
    elif primary == DIAGNOSTIC_NO_PRESSURE:
        if context in (DIAGNOSTIC_ELEVATED, DIAGNOSTIC_PRESSURE):
            status = CAPACITY_OBSERVE
            reasons.append('historical_signal_present')
        else:
            status = CAPACITY_NO_PRESSURE
            reasons.append('primary_no_pressure_observed')
    elif primary == DIAGNOSTIC_SPIKY:
        status = CAPACITY_OBSERVE
        reasons.append('primary_spiky')
        if context in (DIAGNOSTIC_ELEVATED, DIAGNOSTIC_PRESSURE):
            reasons.append('context_pressure_signal')
    elif primary == DIAGNOSTIC_ELEVATED:
        status = CAPACITY_OBSERVE
        reasons.append('primary_elevated')
        if context in (DIAGNOSTIC_ELEVATED, DIAGNOSTIC_PRESSURE):
            reasons.append('context_elevated_or_pressure')
    else:
        reasons.append('primary_pressure')
        if context in (DIAGNOSTIC_ELEVATED, DIAGNOSTIC_PRESSURE):
            status = CAPACITY_SUSTAINED_PRESSURE
            reasons.append('context_corroborates_pressure')
        else:
            status = CAPACITY_OBSERVE
            if context in (DIAGNOSTIC_NO_PRESSURE, DIAGNOSTIC_SPIKY):
                reasons.append('context_not_corroborated')
    if primary != DIAGNOSTIC_NOT_EVALUATED:
        if context is None and primary != DIAGNOSTIC_NO_PRESSURE:
            reasons.append('context_window_unavailable')
        elif context == DIAGNOSTIC_NOT_EVALUATED:
            reasons.append('context_not_evaluated')
    return {
        'status': status,
        'primary_diagnostic': primary,
        'context_diagnostic': context,
        'reasons': reasons,
    }


def _optional_nonnegative_int(value, name):
    return None if value is None else _nonnegative_int(value, name)


def _hardware_context(hardware):
    if hardware is None:
        hardware = {}
    if not isinstance(hardware, dict):
        raise ValueError('hardware must be a dict or None')

    cpu_input = hardware.get('cpu')
    if cpu_input is None:
        cpu_input = {}
    if not isinstance(cpu_input, dict):
        raise ValueError('hardware.cpu must be a dict')
    name = cpu_input.get('name')
    if name is not None and not isinstance(name, str):
        raise ValueError('hardware.cpu.name must be a string')
    if name == '':
        name = None
    physical = _optional_nonnegative_int(cpu_input.get('physical_cores'), 'hardware.cpu.physical_cores')
    logical = _optional_nonnegative_int(cpu_input.get('logical_processors'), 'hardware.cpu.logical_processors')
    clock = cpu_input.get('max_clock_mhz')
    if clock is not None:
        _finite_number(clock, 'hardware.cpu.max_clock_mhz')
        if clock < 0:
            raise ValueError('hardware.cpu.max_clock_mhz must be nonnegative')
    sockets = _optional_nonnegative_int(cpu_input.get('socket_count'), 'hardware.cpu.socket_count')
    cpu_status = (INVENTORY_AVAILABLE if all(value is not None for value in (name, physical, logical))
                  else INVENTORY_PARTIAL if any(value is not None for value in (name, physical, logical, clock, sockets))
                  else INVENTORY_MISSING)

    memory_input = hardware.get('memory')
    if memory_input is None:
        memory_input = {}
    if not isinstance(memory_input, dict):
        raise ValueError('hardware.memory must be a dict')
    nested_total = _optional_nonnegative_int(memory_input.get('total_bytes'), 'hardware.memory.total_bytes')
    top_total = _optional_nonnegative_int(hardware.get('memory_total_bytes'), 'hardware.memory_total_bytes')
    if nested_total is not None and top_total is not None and nested_total != top_total:
        raise ValueError('hardware memory totals disagree')
    total = nested_total if nested_total is not None else top_total
    slots_total = _optional_nonnegative_int(memory_input.get('slots_total'), 'hardware.memory.slots_total')
    slots_used = _optional_nonnegative_int(memory_input.get('slots_used'), 'hardware.memory.slots_used')
    slots_free = _optional_nonnegative_int(memory_input.get('slots_free'), 'hardware.memory.slots_free')
    if all(value is not None for value in (slots_total, slots_used, slots_free)):
        if slots_used + slots_free != slots_total:
            raise ValueError('hardware memory slots disagree')
    modules = memory_input.get('modules')
    if modules is not None and not isinstance(modules, list):
        raise ValueError('hardware.memory.modules must be a list')
    module_count = len(modules) if modules is not None else None
    memory_status = (INVENTORY_AVAILABLE if all(value is not None for value in
                     (total, slots_total, slots_used, slots_free))
                     else INVENTORY_PARTIAL if any(value is not None for value in
                     (total, slots_total, slots_used, slots_free, module_count))
                     else INVENTORY_MISSING)
    return {
        'cpu': {
            'status': cpu_status,
            'name': name,
            'physical_cores': physical,
            'logical_processors': logical,
            'max_clock_mhz': clock,
            'socket_count': sockets,
        },
        'memory': {
            'status': memory_status,
            'total_bytes': total,
            'slots_total': slots_total,
            'slots_used': slots_used,
            'slots_free': slots_free,
            'module_count': module_count,
        },
    }


def build_capacity_assessment(primary_diagnostics, context_diagnostics=None, hardware=None):
    """Corroborate independent CPU and memory patterns without sizing hardware."""
    primary = _capacity_input(primary_diagnostics, 'primary')
    context = _capacity_input(context_diagnostics, 'context') if context_diagnostics is not None else None
    if context and context['window']['duration_seconds'] < primary['window']['duration_seconds']:
        raise ValueError('context window must be at least as long as primary')
    return {
        'schema_version': 1,
        'windows': {
            'primary': primary['window'],
            'context': context['window'] if context else None,
        },
        'capacity': {
            resource: _capacity_resource(
                primary['statuses'][resource], context['statuses'][resource] if context else None
            ) for resource in ('cpu', 'memory')
        },
        'hardware_context': _hardware_context(hardware),
    }
