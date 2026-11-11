"""Controlled persistence of one eligible continuous-v2 window per endpoint.

This module never collects telemetry. The scheduled/manual input is an already
persisted, authenticated EndpointPerformanceSample row.
"""
import hashlib
import json
import uuid
from datetime import timedelta, timezone as datetime_timezone

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import (AgentMachine, EndpointPerformanceSample,
                     PersistedTelemetryAnalysis, TelemetryAnalysisIncorporation)
from .telemetry_continuous import _metric_statistics, project_continuous_metrics, validate_metric

SOURCE_NAME = 'continuous_v2'
ANALYSIS_SCHEMA_VERSION = 1
QUALITY_POLICY_VERSION = 'rc47-03a-v1'
MIN_RECEIVED_SAMPLES = 5
ONLINE_FRESHNESS = timedelta(minutes=15)
MAX_CANDIDATE_SCAN = 100


def _utc(value):
    return value.astimezone(datetime_timezone.utc) if timezone.is_aware(value) else None


def _window_payload(row):
    payload = row.metric_summaries
    if not isinstance(payload, dict):
        raise ValueError('window_payload_invalid')
    try:
        sample_id = uuid.UUID(str(payload.get('window_id')))
        start = parse_datetime(payload.get('start_at'))
        end = parse_datetime(payload.get('end_at'))
    except (TypeError, ValueError, AttributeError):
        raise ValueError('window_identity_invalid') from None
    if (sample_id != row.sample_id or start is None or end is None or
            not timezone.is_aware(start) or not timezone.is_aware(end) or
            row.endpoint_id is None or row.schema_version != 2 or
            row.resolution_seconds != 60 or row.window_end_at is None or
            _utc(start) != _utc(row.collected_at) or _utc(end) != _utc(row.window_end_at) or
            (end - start).total_seconds() != 60):
        raise ValueError('window_identity_invalid')
    if (payload.get('resolution_seconds') != 60 or payload.get('expected_samples') != 6 or
            type(payload.get('received_samples')) is not int or
            not 0 <= payload['received_samples'] <= payload['expected_samples']):
        raise ValueError('window_coverage_invalid')
    metrics = payload.get('metrics')
    quality = payload.get('quality')
    if not isinstance(metrics, dict) or len(metrics) > 256 or not isinstance(quality, dict):
        raise ValueError('window_quality_invalid')
    for name, value in metrics.items():
        if not isinstance(name, str) or len(name) > 220 or not name.startswith(
                ('cpu.', 'memory.', 'disk.', 'network.', 'agent.')):
            raise ValueError('window_metric_invalid')
        try:
            validate_metric(value)
        except (TypeError, ValueError, KeyError):
            raise ValueError('window_metric_invalid') from None
    try:
        if len(json.dumps(quality, allow_nan=False, separators=(',', ':'))) > 32768:
            raise ValueError('window_quality_invalid')
    except (TypeError, ValueError):
        raise ValueError('window_quality_invalid') from None
    status_counts = quality.get('group_status_counts')
    if status_counts is not None:
        if not isinstance(status_counts, dict) or len(status_counts) > 8:
            raise ValueError('window_quality_invalid')
        for group, values in status_counts.items():
            if not isinstance(group, str) or len(group) > 64 or not isinstance(values, dict) or len(values) > 32:
                raise ValueError('window_quality_invalid')
            if any(not isinstance(status, str) or len(status) > 64 or type(count) is not int or
                   count < 0 or count > payload['expected_samples'] for status, count in values.items()):
                raise ValueError('window_quality_invalid')
            if sum(values.values()) > payload['expected_samples']:
                raise ValueError('window_quality_invalid')
    received = payload['received_samples']
    coverage = received / payload['expected_samples']
    if received < MIN_RECEIVED_SAMPLES:
        raise ValueError('coverage_below_policy')
    if not any(name in metrics for name in ('cpu.usage_percent', 'memory.used_percent')):
        raise ValueError('primary_metrics_unavailable')
    return payload, sample_id, start, end, coverage


def _idempotency_key(endpoint, row, payload):
    material = '|'.join((str(endpoint.pk), endpoint.machine_id, str(row.sample_id),
                         str(row.schema_version), str(row.resolution_seconds),
                         QUALITY_POLICY_VERSION))
    return hashlib.sha256(material.encode('utf-8')).hexdigest()


def _analysis_id(endpoint, key):
    return uuid.uuid5(endpoint.pk, key)


def _quality_state(payload):
    quality = payload['quality']
    if (payload['received_samples'] < payload['expected_samples'] or
            not {'cpu.usage_percent', 'memory.used_percent'}.issubset(payload['metrics'])):
        return 'partial'
    group_counts = quality.get('group_status_counts')
    unresolved = False
    for group in ('cpu', 'memory'):
        counts = group_counts.get(group) if isinstance(group_counts, dict) else None
        if isinstance(counts, dict) and counts:
            if any(status != 'available' and type(count) is int and count > 0
                   for status, count in counts.items()):
                return 'partial'
            if sum(count for count in counts.values() if type(count) is int and count > 0) < payload['expected_samples']:
                unresolved = True
            continue
        status = quality.get(group)
        if status == 'available':
            continue
        if status in {'partial', 'error', 'unavailable', 'counter_unavailable', 'failed'}:
            return 'partial'
        unresolved = True
    return 'unknown' if unresolved else 'complete'


def _detail(endpoint, row, payload, sample_id, start, end, coverage, key, analysis_id, selection):
    metrics = payload['metrics']
    projected = project_continuous_metrics(metrics, [payload['quality']])
    error_metric = metrics.get('agent.telemetry_errors_count')
    error_samples = (error_metric['count'] if error_metric and error_metric['min'] > 0
                     else 0 if error_metric and error_metric['max'] == 0 else None)
    expected = payload['expected_samples']
    received = payload['received_samples']
    start_utc = _utc(start)
    end_utc = _utc(end)
    # The minute aggregator stores counts, not individual ten-second timestamps.
    # Report the missing count and explicitly avoid fabricating gap positions.
    quality = {
        'state': _quality_state(payload),
        'expected_samples': expected,
        'received_samples': received,
        'coverage_percent': coverage * 100,
        'missing_sample_count': expected - received,
        'gap_intervals': None,
        'gap_positions_available': False,
        'metric_status': projected['quality'],
        'metric_validity': projected['metric_validity'],
        'metric_presence': {
            name: 'reported' if name in payload['metrics'] else 'unknown'
            for name in ('cpu.usage_percent', 'memory.used_percent',
                         'memory.committed_percent', 'agent.telemetry_errors_count')
        },
        'reported_metric_keys': sorted(payload['metrics']),
        'source_quality': {key: value for key, value in payload['quality'].items()
                           if key in {'cpu', 'memory', 'commit', 'disk', 'network',
                                      'group_status_counts', 'disk_instances', 'interfaces',
                                      'aggregation_basis', 'missing_samples'}},
        'policy_version': QUALITY_POLICY_VERSION,
    }
    return {
        'analysis_id': str(analysis_id),
        'idempotency_key': key,
        'endpoint_id': str(endpoint.pk),
        'identity': {'machine_id': endpoint.machine_id},
        'source': SOURCE_NAME,
        'schema_version': row.schema_version,
        'analysis_schema_version': ANALYSIS_SCHEMA_VERSION,
        'resolution_seconds': row.resolution_seconds,
        'sample_id': str(sample_id),
        # EndpointPerformanceSample has no trustworthy agent-version snapshot.
        # The current endpoint version may be newer than this historical window.
        'agent_version': None,
        'candidate_selection': selection,
        'window': {'start': start_utc.isoformat(), 'end': end_utc.isoformat(), 'duration_seconds': 60},
        'cpu': projected['cpu'],
        'memory': projected['memory'],
        'disk': projected['disk'],
        'network': projected['network'],
        'agent': {
            'collection_duration_ms': _metric_statistics(metrics.get('agent.collection_duration_ms')),
            'working_set_bytes': _metric_statistics(metrics.get('agent.working_set_bytes')),
            'telemetry_errors': {
                'total_errors': round(error_metric['sum']) if error_metric else None,
                'samples_with_errors': error_samples,
                'samples_with_errors_known': error_samples is not None,
            },
        },
        'quality': quality,
    }


def _merge_history(previous, payload, start, end, coverage):
    history = json.loads(json.dumps(previous or {}))
    old_count = history.get('analysis_count', 0)
    if type(old_count) is not int or old_count < 0:
        raise ValueError('stored_history_invalid')
    metrics = history.setdefault('metrics', {})
    if not isinstance(metrics, dict):
        raise ValueError('stored_history_invalid')
    incoming = payload['metrics']
    analysis_state = _quality_state(payload)
    new_metric_count = len(set(incoming) - set(metrics))
    if len(metrics) + new_metric_count > 512:
        raise ValueError('metric_cardinality_limit')
    for aggregate in metrics.values():
        if isinstance(aggregate, dict):
            aggregate['missing_analysis_count'] = aggregate.get('missing_analysis_count', 0) + 1
    timestamp_start, timestamp_end = _utc(start).isoformat(), _utc(end).isoformat()
    for name, source in incoming.items():
        aggregate = metrics.get(name)
        if aggregate is None:
            aggregate = {
                'valid_analysis_count': 0,
                'missing_analysis_count': old_count,
                'sample_count': 0,
                'sum': 0,
                'weight': 0,
                'weighted_sum': 0,
                'min': None,
                'max': None,
                'first_seen_at': timestamp_start,
                'last_seen_at': timestamp_end,
                'distribution': {},
                'distribution_complete': True,
                'by_quality': {},
            }
            metrics[name] = aggregate
        aggregate['valid_analysis_count'] += 1
        aggregate['missing_analysis_count'] = max(0, aggregate['missing_analysis_count'] - 1)
        aggregate['sample_count'] += source['count']
        aggregate['sum'] += source['sum']
        aggregate['weight'] += source['weight']
        aggregate['weighted_sum'] += source['weighted_sum']
        aggregate['min'] = source['min'] if aggregate['min'] is None else min(aggregate['min'], source['min'])
        aggregate['max'] = source['max'] if aggregate['max'] is None else max(aggregate['max'], source['max'])
        aggregate['first_seen_at'] = min(aggregate['first_seen_at'], timestamp_start)
        aggregate['last_seen_at'] = max(aggregate['last_seen_at'], timestamp_end)
        distribution = source.get('distribution') or {}
        for bucket, weight in distribution.items():
            aggregate['distribution'][bucket] = aggregate['distribution'].get(bucket, 0) + weight
        if name.endswith('_percent'):
            aggregate['distribution_complete'] = bool(aggregate['distribution_complete'] and distribution)
        else:
            aggregate['distribution_complete'] = False
        strata = aggregate.setdefault('by_quality', {})
        stratum = strata.setdefault(analysis_state, {
            'analysis_count': 0, 'sample_count': 0, 'sum': 0, 'weight': 0,
            'weighted_sum': 0, 'min': None, 'max': None, 'distribution': {},
            'first_seen_at': timestamp_start, 'last_seen_at': timestamp_end,
            'distribution_complete': bool(source.get('distribution')) and name.endswith('_percent'),
        })
        stratum['analysis_count'] += 1
        stratum['sample_count'] += source['count']
        stratum['sum'] += source['sum']
        stratum['weight'] += source['weight']
        stratum['weighted_sum'] += source['weighted_sum']
        stratum['min'] = source['min'] if stratum['min'] is None else min(stratum['min'], source['min'])
        stratum['max'] = source['max'] if stratum['max'] is None else max(stratum['max'], source['max'])
        stratum['first_seen_at'] = min(stratum['first_seen_at'], timestamp_start)
        stratum['last_seen_at'] = max(stratum['last_seen_at'], timestamp_end)
        for bucket, weight in distribution.items():
            stratum['distribution'][bucket] = stratum['distribution'].get(bucket, 0) + weight
        if name.endswith('_percent'):
            stratum['distribution_complete'] = bool(stratum['distribution_complete'] and distribution)
        else:
            stratum['distribution_complete'] = False

    quality = history.setdefault('quality', {})
    status_counts = quality.setdefault('analysis_state_counts', {})
    status_counts[analysis_state] = status_counts.get(analysis_state, 0) + 1
    coverage_stats = quality.setdefault('coverage', {'count': 0, 'sum_percent': 0,
                                                      'min_percent': None, 'max_percent': None,
                                                      'received_samples': 0, 'expected_samples': 0,
                                                      'missing_samples': 0})
    coverage_stats['count'] += 1
    coverage_percent = coverage * 100
    coverage_stats['sum_percent'] += coverage_percent
    coverage_stats['min_percent'] = coverage_percent if coverage_stats['min_percent'] is None else min(coverage_stats['min_percent'], coverage_percent)
    coverage_stats['max_percent'] = coverage_percent if coverage_stats['max_percent'] is None else max(coverage_stats['max_percent'], coverage_percent)
    coverage_stats['received_samples'] += payload['received_samples']
    coverage_stats['expected_samples'] += payload['expected_samples']
    coverage_stats['missing_samples'] += payload['expected_samples'] - payload['received_samples']
    source_statuses = payload['quality'].get('group_status_counts', {})
    if isinstance(source_statuses, dict):
        group_counts = quality.setdefault('group_status_counts', {})
        for group, statuses in source_statuses.items():
            if not isinstance(group, str) or not isinstance(statuses, dict):
                continue
            target = group_counts.setdefault(group, {})
            for status, count in statuses.items():
                if isinstance(status, str) and type(count) is int and count >= 0:
                    target[status] = target.get(status, 0) + count

    history.update({
        'source': SOURCE_NAME,
        'schema_version': 2,
        'analysis_schema_version': ANALYSIS_SCHEMA_VERSION,
        'analysis_count': old_count + 1,
        'first_analysis_at': min(history.get('first_analysis_at') or timestamp_start, timestamp_start),
        'last_analysis_at': max(history.get('last_analysis_at') or timestamp_end, timestamp_end),
        'quality': quality,
        'metrics': metrics,
    })
    return history


def _attempt(state, now, status, reason):
    state.last_attempt_at = now
    state.last_attempt_state = status
    state.last_attempt_reason = reason
    state.save(update_fields=['last_attempt_at', 'last_attempt_state', 'last_attempt_reason', 'updated_at'])


def _candidate(endpoint, now, state, *, lock=False):
    retention = getattr(settings, 'NIGHTOWL_TELEMETRY_RETENTION_DAYS', {}).get(60)
    if type(retention) is not int or retention <= 0:
        raise ValueError('invalid_detail_retention')
    rows = EndpointPerformanceSample.objects.filter(
        endpoint_id=endpoint.pk, schema_version=2, resolution_seconds=60,
        collected_at__gte=now - timedelta(days=retention), window_end_at__lte=now,
    )
    incorporated_sample_ids = TelemetryAnalysisIncorporation.objects.filter(
        endpoint_id=endpoint.pk).values('sample_id')
    rows = rows.exclude(sample_id__in=incorporated_sample_ids)
    if state is not None and state.window_end_at:
        rows = rows.filter(window_end_at__gt=state.window_end_at)
    rows = rows.order_by('-window_end_at', '-collected_at', '-pk')
    if lock:
        rows = rows.select_for_update()

    # Probe one row beyond the bound so the result can distinguish an exhausted
    # search from an intentionally truncated search without an unbounded COUNT.
    batch = list(rows[:MAX_CANDIDATE_SCAN + 1])
    scan = {
        'records_examined': 0,
        'rejected_candidates': 0,
        'rejection_reasons': {},
        'limit': MAX_CANDIDATE_SCAN,
        'limit_reached': len(batch) > MAX_CANDIDATE_SCAN,
    }
    for row in batch[:MAX_CANDIDATE_SCAN]:
        scan['records_examined'] += 1
        try:
            _window_payload(row)
        except ValueError as error:
            reason = str(error)
            scan['rejected_candidates'] += 1
            scan['rejection_reasons'][reason] = scan['rejection_reasons'].get(reason, 0) + 1
            continue
        return row, scan
    return None, scan


def analyze_endpoint(endpoint_id, *, apply=False, now=None):
    """Preview by default; with apply=True, atomically persist a valid latest analysis."""
    now = now or timezone.now()
    if not timezone.is_aware(now):
        raise ValueError('now_must_be_aware')
    if not apply:
        endpoint = AgentMachine.objects.filter(pk=endpoint_id).first()
        if endpoint is None:
            return {'endpoint_id': str(endpoint_id), 'state': 'endpoint_not_found', 'reason': 'endpoint_not_found'}
        state = PersistedTelemetryAnalysis.objects.filter(endpoint=endpoint).first()
        return _preview(endpoint, state, now)

    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute("SET LOCAL statement_timeout = '15s'")
        endpoint = AgentMachine.objects.select_for_update().filter(pk=endpoint_id).first()
        if endpoint is None:
            return {'endpoint_id': str(endpoint_id), 'state': 'endpoint_not_found', 'reason': 'endpoint_not_found'}
        state, _ = PersistedTelemetryAnalysis.objects.get_or_create(
            endpoint=endpoint, defaults={'machine_id_snapshot': endpoint.machine_id})
        if not endpoint.machine_id:
            _attempt(state, now, PersistedTelemetryAnalysis.STATUS_REJECTED, 'identity_missing')
            return _result(endpoint, state, 'rejected', 'identity_missing')
        if state.machine_id_snapshot and state.machine_id_snapshot != endpoint.machine_id:
            _attempt(state, now, PersistedTelemetryAnalysis.STATUS_REJECTED, 'machine_identity_changed')
            return _result(endpoint, state, 'rejected', 'machine_identity_changed')
        if not state.machine_id_snapshot:
            state.machine_id_snapshot = endpoint.machine_id
            state.save(update_fields=['machine_id_snapshot', 'updated_at'])
        if not endpoint.is_active or endpoint.has_terminal_lifecycle:
            _attempt(state, now, PersistedTelemetryAnalysis.STATUS_REJECTED, 'endpoint_inactive')
            return _result(endpoint, state, 'rejected', 'endpoint_inactive')
        if not endpoint.last_seen_at or endpoint.last_seen_at < now - ONLINE_FRESHNESS:
            _attempt(state, now, PersistedTelemetryAnalysis.STATUS_ENDPOINT_OFFLINE, 'endpoint_offline')
            return _result(endpoint, state, 'endpoint_offline', 'endpoint_offline')
        row, selection = _candidate(endpoint, now, state, lock=True)
        if row is None:
            if selection['records_examined']:
                reason = ('candidate_scan_limit_reached' if selection['limit_reached']
                          else next(iter(selection['rejection_reasons']), 'no_valid_candidate'))
                _attempt(state, now, PersistedTelemetryAnalysis.STATUS_REJECTED, reason)
                result = _result(endpoint, state, 'rejected', reason)
                result['selection'] = selection
                return result
            if state.latest_analysis_id:
                _attempt(state, now, PersistedTelemetryAnalysis.STATUS_ALREADY_INCORPORATED,
                         'no_new_window_since_latest_success')
                return _result(endpoint, state, 'already_incorporated', 'no_new_window_since_latest_success')
            _attempt(state, now, PersistedTelemetryAnalysis.STATUS_NO_ELIGIBLE_DATA, 'no_new_recent_v2_window')
            return _result(endpoint, state, 'no_eligible_data', 'no_new_recent_v2_window')
        try:
            payload, sample_id, start, end, coverage = _window_payload(row)
        except ValueError as error:
            reason = str(error)
            _attempt(state, now, PersistedTelemetryAnalysis.STATUS_REJECTED, reason)
            return _result(endpoint, state, 'rejected', reason, row=row)
        key = _idempotency_key(endpoint, row, payload)
        receipt = TelemetryAnalysisIncorporation.objects.filter(endpoint=endpoint, idempotency_key=key).first()
        if receipt:
            _attempt(state, now, PersistedTelemetryAnalysis.STATUS_ALREADY_INCORPORATED,
                     'analysis_already_incorporated')
            return _result(endpoint, state, 'already_incorporated', 'analysis_already_incorporated', row=row)
        if state.window_end_at and end <= state.window_end_at:
            _attempt(state, now, PersistedTelemetryAnalysis.STATUS_STALE_WINDOW, 'window_not_newer_than_latest')
            return _result(endpoint, state, 'stale_window', 'window_not_newer_than_latest', row=row)

        analysis_id = _analysis_id(endpoint, key)
        detail = _detail(endpoint, row, payload, sample_id, start, end, coverage,
                         key, analysis_id, selection)
        try:
            history = _merge_history(state.historical_statistics, payload, start, end, coverage)
        except ValueError as error:
            reason = str(error)
            _attempt(state, now, PersistedTelemetryAnalysis.STATUS_REJECTED, reason)
            return _result(endpoint, state, 'rejected', reason, row=row)
        # The receipt, latest analysis, and aggregate are one commit. A replay
        # cannot increment history twice, even when workers race on PostgreSQL.
        TelemetryAnalysisIncorporation.objects.create(
            endpoint=endpoint, analysis_id=analysis_id, idempotency_key=key,
            sample_id=sample_id, window_start_at=start, window_end_at=end,
            incorporated_at=now)
        state.machine_id_snapshot = endpoint.machine_id
        state.last_attempt_at = now
        state.last_attempt_state = PersistedTelemetryAnalysis.STATUS_SUCCEEDED
        state.last_attempt_reason = ''
        state.last_success_at = now
        state.latest_analysis_id = analysis_id
        state.latest_idempotency_key = key
        state.source = SOURCE_NAME
        state.schema_version = 2
        state.resolution_seconds = 60
        # The sampled window does not carry a verifiable agent-version snapshot.
        state.agent_version = ''
        state.window_start_at = start
        state.window_end_at = end
        state.latest_analysis = detail
        state.historical_statistics = history
        state.analysis_count = history['analysis_count']
        state.first_analysis_at = start if state.first_analysis_at is None else min(state.first_analysis_at, start)
        state.last_analysis_at = end if state.last_analysis_at is None else max(state.last_analysis_at, end)
        state.save()
        result = _result(endpoint, state, 'succeeded', '', row=row)
        result['selection'] = selection
        return result


def _preview(endpoint, state, now):
    if not endpoint.machine_id:
        return {'endpoint_id': str(endpoint.pk), 'state': 'rejected', 'reason': 'identity_missing'}
    if state and state.machine_id_snapshot and state.machine_id_snapshot != endpoint.machine_id:
        return {'endpoint_id': str(endpoint.pk), 'state': 'rejected', 'reason': 'machine_identity_changed'}
    if not endpoint.is_active or endpoint.has_terminal_lifecycle:
        return {'endpoint_id': str(endpoint.pk), 'state': 'rejected', 'reason': 'endpoint_inactive'}
    if not endpoint.last_seen_at or endpoint.last_seen_at < now - ONLINE_FRESHNESS:
        return {'endpoint_id': str(endpoint.pk), 'state': 'endpoint_offline', 'reason': 'endpoint_offline'}
    row, selection = _candidate(endpoint, now, state)
    if row is None:
        if selection['records_examined']:
            reason = ('candidate_scan_limit_reached' if selection['limit_reached']
                      else next(iter(selection['rejection_reasons']), 'no_valid_candidate'))
            return {'endpoint_id': str(endpoint.pk), 'state': 'rejected', 'reason': reason,
                    'selection': selection}
        if state and state.latest_analysis_id:
            return {'endpoint_id': str(endpoint.pk), 'state': 'already_incorporated',
                    'reason': 'no_new_window_since_latest_success',
                    'analysis_id': str(state.latest_analysis_id)}
        return {'endpoint_id': str(endpoint.pk), 'state': 'no_eligible_data', 'reason': 'no_new_recent_v2_window'}
    try:
        payload, sample_id, start, end, coverage = _window_payload(row)
    except ValueError as error:
        return {'endpoint_id': str(endpoint.pk), 'state': 'rejected', 'reason': str(error),
                'sample_id': str(row.sample_id)}
    key = _idempotency_key(endpoint, row, payload)
    if TelemetryAnalysisIncorporation.objects.filter(endpoint=endpoint, idempotency_key=key).exists():
        return {'endpoint_id': str(endpoint.pk), 'state': 'already_incorporated', 'reason': 'analysis_already_incorporated',
                'sample_id': str(sample_id)}
    return {'endpoint_id': str(endpoint.pk), 'state': 'eligible', 'reason': '',
            'sample_id': str(sample_id), 'window_start_at': start.isoformat(), 'window_end_at': end.isoformat(),
            'coverage_percent': coverage * 100, 'source': SOURCE_NAME, 'resolution_seconds': 60,
            'selection': selection}


def _result(endpoint, state, status, reason, row=None):
    result = {'endpoint_id': str(endpoint.pk), 'state': status, 'reason': reason,
              'last_success_at': state.last_success_at.isoformat() if state.last_success_at else None,
              'analysis_id': str(state.latest_analysis_id) if status == 'succeeded' and state.latest_analysis_id else None}
    if row is not None:
        result['sample_id'] = str(row.sample_id)
    return result
