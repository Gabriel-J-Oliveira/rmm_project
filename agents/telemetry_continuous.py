"""Version 2 minute evidence. No rules execute on the agent."""
import hashlib
import json
import math
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import EndpointPerformanceSample, EndpointAlert

CORE_METRICS = {'cpu.usage_percent': 'cpu_percent', 'memory.used_percent': 'memory_used_percent',
                'memory.committed_percent': 'memory_committed_percent', 'agent.working_set_bytes': 'agent_working_set_bytes',
                'memory.total_bytes': 'memory_total_bytes', 'memory.available_bytes': 'memory_available_bytes',
                'memory.committed_bytes': 'memory_committed_bytes', 'memory.commit_limit_bytes': 'memory_commit_limit_bytes',
                'agent.collection_duration_ms': 'collection_duration_ms'}


def number(value, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < minimum:
        raise ValueError('invalid_number')
    return value


def validate_metric(value):
    if not isinstance(value, dict) or set(value) != {'count', 'sum', 'min', 'max', 'weight', 'weighted_sum', 'distribution'}:
        raise ValueError('invalid_metric')
    count = value['count']
    if type(count) is not int or not 1 <= count <= 1000000:
        raise ValueError('invalid_count')
    for name in ('sum', 'min', 'max', 'weight', 'weighted_sum'):
        number(value[name])
    if value['min'] > value['max'] or value['weight'] <= 0:
        raise ValueError('invalid_range')
    if not value['min'] * count - 0.001 <= value['sum'] <= value['max'] * count + 0.001:
        raise ValueError('invalid_sum')
    if not value['min'] * value['weight'] - .001 <= value['weighted_sum'] <= value['max'] * value['weight'] + .001:
        raise ValueError('invalid_weighted_sum')
    bins = value['distribution']
    if not isinstance(bins, dict) or len(bins) > 101:
        raise ValueError('invalid_distribution')
    for key, weight in bins.items():
        if not key.isdigit() or not 0 <= int(key) <= 100:
            raise ValueError('invalid_bin')
        number(weight)
        if weight <= 0 or not math.floor(value['min']) <= int(key) <= math.floor(value['max']):
            raise ValueError('invalid_distribution_bin')
    if bins and not math.isclose(sum(bins.values()), value['weight'], rel_tol=1e-8, abs_tol=1e-6):
        raise ValueError('invalid_distribution_weight')
    return value


def validate_windows(payload):
    if not isinstance(payload, dict) or set(payload) != {'schema_version', 'machine_id', 'windows'} or payload['schema_version'] != 2:
        raise ValueError('invalid_schema')
    windows = payload['windows']
    if not isinstance(windows, list) or not 1 <= len(windows) <= 24:
        raise ValueError('invalid_batch')
    seen = set()
    now = timezone.now()
    for window in windows:
        if not isinstance(window, dict) or set(window) != {'window_id', 'start_at', 'end_at', 'resolution_seconds', 'expected_samples', 'received_samples', 'metrics', 'quality'}:
            raise ValueError('invalid_window')
        identity = str(uuid.UUID(str(window['window_id'])))
        if identity in seen:
            raise ValueError('duplicate_window')
        seen.add(identity)
        start, end = parse_datetime(window['start_at']), parse_datetime(window['end_at'])
        if not start or not end or timezone.is_naive(start) or timezone.is_naive(end) or (end - start).total_seconds() != 60:
            raise ValueError('invalid_window_time')
        if start.second or start.microsecond or start < now - timedelta(days=9) or end > now + timedelta(minutes=5):
            raise ValueError('window_outside_range')
        if type(window['resolution_seconds']) is not int or type(window['expected_samples']) is not int or window['resolution_seconds'] != 60 or window['expected_samples'] != 6 or type(window['received_samples']) is not int or not 0 <= window['received_samples'] <= 6:
            raise ValueError('invalid_cadence')
        metrics = window['metrics']
        if not isinstance(metrics, dict) or len(metrics) > 256:
            raise ValueError('invalid_metrics')
        for name, value in metrics.items():
            if not isinstance(name, str) or len(name) > 220 or not name.startswith(('cpu.', 'memory.', 'disk.', 'network.', 'agent.')):
                raise ValueError('invalid_metric_name')
            validate_metric(value)
            if name in ('cpu.usage_percent', 'memory.used_percent', 'memory.available_percent', 'memory.committed_percent'):
                if value['max'] > 100 or not value['distribution']:
                    raise ValueError('invalid_percentage')
            if name.endswith('_bytes') and value['max'] > (1 << 63)-1:
                raise ValueError('invalid_byte_count')
            if value['count'] > window['received_samples']:
                raise ValueError('invalid_metric_count')
            if name.endswith('_percent') and value['distribution'] and value['max'] > 100:
                raise ValueError('invalid_percentage_distribution')
        if not isinstance(window['quality'], dict) or len(json.dumps(window['quality'])) > 32768:
            raise ValueError('invalid_quality')
    return windows


def merge_metrics(groups):
    result = {}
    for metrics in groups:
        for name, incoming in metrics.items():
            if name not in result:
                result[name] = {**incoming, 'distribution': dict(incoming['distribution'])}
                continue
            target = result[name]
            for key in ('count', 'sum', 'weight', 'weighted_sum'):
                target[key] += incoming[key]
            target['min'] = min(target['min'], incoming['min'])
            target['max'] = max(target['max'], incoming['max'])
            for bucket, weight in incoming['distribution'].items():
                target['distribution'][bucket] = target['distribution'].get(bucket, 0) + weight
    return result


def mean(metric):
    return metric['weighted_sum'] / metric['weight'] if metric and metric['weight'] else None


def policy():
    return {**{'cpu_percent': 85, 'memory_available_percent': 10, 'commit_percent': 90,
               'paging_per_sec': 5, 'disk_latency_ms': 25, 'disk_queue': 1,
               'network_percent': 85, 'consecutive_minutes': 2},
            **getattr(settings, 'NIGHTOWL_WORKLOAD_THRESHOLDS', {})}


def workload(rows):
    """Conservative consecutive complete windows, not p95s of overlapping periods."""
    rules = policy()
    result = {}
    for resource in ('cpu', 'memory', 'disk', 'network'):
        longest = current = 0
        runs = {}
        episodes = 0
        pressure_windows = 0
        spike_windows = 0
        last_end = None
        evidence = 0
        for row in sorted(rows, key=lambda item: item.collected_at):
            metrics = row.metric_summaries.get('metrics', {})
            good = row.metric_summaries.get('received_samples') == 6 and row.resolution_seconds == 60
            signal = False
            window_evidence = False
            candidates = set()
            if resource == 'cpu':
                value = metrics.get('cpu.usage_percent')
                good = good and bool(value and value['count'] == 6)
                signal = bool(value and value['min'] >= rules['cpu_percent'])
                spike_windows += bool(value and value['max'] >= rules['cpu_percent'] and value['min'] < rules['cpu_percent'])
            elif resource == 'memory':
                available, commit, paging = (metrics.get(key) for key in ('memory.available_percent', 'memory.committed_percent', 'memory.page_reads_per_sec'))
                commit_valid = bool(commit and commit['count'] == 6)
                paging_valid = bool(paging and paging['count'] == 6)
                good = good and bool(available and available['count'] == 6) and (commit_valid or paging_valid)
                signal = bool(good and available['max'] <= rules['memory_available_percent'] and
                              (commit_valid and commit['min'] >= rules['commit_percent'] or paging_valid and paging['min'] >= rules['paging_per_sec']))
            elif resource == 'disk':
                for key, value in metrics.items():
                    if key.startswith('disk.') and key.endswith(('.read_latency_ms', '.write_latency_ms')):
                        direction = 'read' if key.endswith('.read_latency_ms') else 'write'
                        prefix = key[:key.rfind('.')+1]
                        queue, operations = metrics.get(prefix + 'queue_length'), metrics.get(prefix + direction + '_ops_per_sec')
                        if value['count'] == 6 and queue and operations and queue['count'] == 6 and operations['count'] == 6:
                            window_evidence = True
                            if value['min'] >= rules['disk_latency_ms'] and queue['min'] >= rules['disk_queue'] and operations['min'] > 0:
                                candidates.add(prefix)
                good = good and window_evidence
            else:
                values = [value for key, value in metrics.items() if key.startswith('network.') and key.endswith(('.rx_utilization_percent', '.tx_utilization_percent'))]
                good = good and any(value['count'] == 6 for value in values)
                signal = any(value['count'] == 6 and value['min'] >= rules['network_percent'] for value in values)
                candidates = {key[:key.rfind('.')] for key, value in metrics.items() if key.startswith('network.') and key.endswith(('.rx_utilization_percent', '.tx_utilization_percent')) and value['count'] == 6 and value['min'] >= rules['network_percent']}
            evidence += bool(good)
            consecutive = last_end == row.collected_at
            if resource in ('cpu', 'memory') and good and signal:
                candidates.add(resource)
            if good and candidates:
                pressure_windows += 1
                if not consecutive or not candidates.intersection(runs):
                    episodes += 1
            runs = {key: (runs.get(key, 0) if consecutive else 0) + 1 for key in candidates} if good else {}
            longest = max(longest, max(runs.values(), default=0))
            last_end = row.window_end_at
        result[resource] = {'status': 'INSUFFICIENT_EVIDENCE' if not evidence else 'SUSTAINED_PRESSURE' if longest >= rules['consecutive_minutes'] else 'OBSERVE' if longest or spike_windows else 'NO_SUSTAINED_SIGNAL',
                            'longest_confirmed_minutes': longest, 'evidence_windows': evidence,
                            'episodes': episodes, 'pressure_windows': pressure_windows,
                            'spike_windows': spike_windows,
                            'interpretation': 'investigate_not_automatic_upgrade'}
    return result


@transaction.atomic
def persist_windows(endpoint, windows):
    # Same endpoint lock as identity checks: no conflicting duplicate ACKs.
    from .models import AgentMachine
    AgentMachine.objects.select_for_update().get(pk=endpoint.pk)
    accepted = []
    for window in windows:
        existing = EndpointPerformanceSample.objects.filter(endpoint=endpoint, sample_id=window['window_id']).first()
        if existing:
            if existing.metric_summaries != window:
                raise ValueError('window_identity_conflict')
        else:
            digest = hashlib.sha256(json.dumps(window, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            rolled = EndpointPerformanceSample.objects.filter(endpoint=endpoint, schema_version=2,
                resolution_seconds__gt=60, collected_at__lte=parse_datetime(window['start_at']),
                window_end_at__gt=parse_datetime(window['start_at']))
            matched = False
            for parent in rolled:
                old_digest = parent.metric_summaries.get('source_hashes', {}).get(str(window['window_id']))
                if old_digest:
                    if old_digest != digest:
                        raise ValueError('rolled_window_identity_conflict')
                    matched = True
                    break
            if matched:
                accepted.append(str(window['window_id']))
                continue
            fields = {column: mean(window['metrics'].get(name)) for name, column in CORE_METRICS.items()}
            fields['collection_duration_ms'] = round(fields['collection_duration_ms'] or 0)
            for name, value in fields.items():
                if name.endswith('_bytes') and value is not None: fields[name] = round(value)
            uptime = mean(window['metrics'].get('agent.uptime_seconds'))
            EndpointPerformanceSample.objects.create(endpoint=endpoint, sample_id=window['window_id'],
                schema_version=2, resolution_seconds=60, collected_at=parse_datetime(window['start_at']),
                window_end_at=parse_datetime(window['end_at']), metric_summaries=window,
                process_consumers=window['quality'].get('top_processes', {}),
                uptime_seconds=round(uptime) if uptime is not None else None, **fields)
        accepted.append(str(window['window_id']))
    recent = list(EndpointPerformanceSample.objects.filter(endpoint=endpoint, schema_version=2, resolution_seconds=60,
        collected_at__gte=timezone.now() - timedelta(minutes=15)).order_by('collected_at'))
    for resource, diagnostic in workload(recent).items():
        if diagnostic['status'] == 'SUSTAINED_PRESSURE':
            kind = 'workload_' + resource
            alert = EndpointAlert.objects.filter(endpoint=endpoint, alert_type=kind,
                status__in=[EndpointAlert.STATUS_OPEN, EndpointAlert.STATUS_ACKNOWLEDGED]).first()
            if alert:
                alert.last_seen_at = timezone.now()
                alert.metadata = {**diagnostic, 'dedupe_key': kind}
                alert.save(update_fields=['last_seen_at', 'metadata', 'updated_at'])
            else:
                from .alerting import create_event, audit_alert
                from .models import AlertEvent
                alert = EndpointAlert.objects.create(endpoint=endpoint, alert_type=kind, source='continuous_telemetry',
                    title='Investigar pressao sustentada: ' + resource, description='Sinal corroborado em janelas completas consecutivas. Nao e recomendacao automatica de upgrade.',
                    severity=EndpointAlert.SEVERITY_WARNING, metadata={**diagnostic, 'dedupe_key': kind})
                create_event(alert, AlertEvent.TYPE_CREATED, 'Evidencia de workload recebida.')
                audit_alert(alert, 'alert.created', alert.title, alert.description)
    return accepted


def rollup_id(endpoint_id, start, resolution):
    return uuid.UUID(hashlib.sha256(f'{endpoint_id}:{start.isoformat()}:{resolution}'.encode()).hexdigest()[:32])


def continuous_summary(endpoint, start, end, rows):
    rows = [row for row in rows if row.collected_at >= start and row.window_end_at <= end]
    merged = merge_metrics(row.metric_summaries.get('metrics', {}) for row in rows)
    expected = math.ceil((min(end, timezone.now()) - start).total_seconds() / 60)
    received = sum(row.resolution_seconds // 60 for row in rows)
    def stats(name):
        metric = merged.get(name)
        if not metric:
            return {'valid_samples': 0, 'min': None, 'max': None, 'avg': None, 'p50': None, 'p95': None, 'p99': None}
        def percentile(fraction):
            distribution = metric['distribution']
            if not distribution:
                return None
            accumulated = 0
            for bucket, weight in sorted(distribution.items(), key=lambda item: int(item[0])):
                accumulated += weight
                if accumulated >= metric['weight'] * fraction:
                    return max(metric['min'], min(metric['max'], int(bucket) + .5))
        return {'valid_samples': metric['count'], 'min': metric['min'], 'max': metric['max'], 'avg': mean(metric),
                'p50': percentile(.5), 'p95': percentile(.95), 'p99': percentile(.99)}
    validity = {}
    for metric, column in CORE_METRICS.items():
        # Existing diagnostics consume equivalent complete minute windows, not raw reads.
        validity[column] = {'valid_samples': min(received, merged.get(metric, {}).get('count', 0) // 6)}
    for column in ('network_received_bytes', 'network_sent_bytes', 'telemetry_errors_count'):
        validity[column] = {'valid_samples': 0}
    return {'schema_version': 1, 'data_kind': 'consolidated_windows', 'percentile_method': 'duration_histogram_1pct',
        'endpoint_id': str(endpoint.pk), 'window': {'start': start.isoformat(), 'end': end.isoformat(), 'duration_seconds': (end-start).total_seconds()},
        'samples': {'received': received}, 'cpu': {'usage_percent': stats('cpu.usage_percent')},
        'memory': {'used_percent': stats('memory.used_percent'), 'committed_percent': stats('memory.committed_percent')},
        'network': {'received_bytes': {'valid_samples': 0, 'observed_total_bytes': None}, 'sent_bytes': {'valid_samples': 0, 'observed_total_bytes': None}},
        'agent': {'collection_duration_ms': stats('agent.collection_duration_ms'), 'working_set_bytes': stats('agent.working_set_bytes'),
                  'telemetry_errors': {'samples_with_errors': 0, 'total_errors': 0}},
        'quality': {'expected_samples': expected, 'received_samples': received, 'coverage_percent': received/expected*100 if expected else None,
                    'over_expected_samples': max(0, received-expected), 'first_sample_at': rows[0].collected_at.isoformat() if rows else None,
                    'last_sample_at': rows[-1].window_end_at.isoformat() if rows else None, 'metric_validity': validity,
                    'gap_threshold_seconds': 70, 'largest_internal_gap_seconds': max(((b.collected_at-a.window_end_at).total_seconds() for a,b in zip(rows, rows[1:])), default=None),
                    'gaps_over_threshold_count': sum((b.collected_at-a.window_end_at).total_seconds() > 10 for a,b in zip(rows, rows[1:])),
                    'ingestion_lag_seconds': {'avg': sum((row.created_at-row.window_end_at).total_seconds() for row in rows)/len(rows) if rows else None},
                    'negative_lag_samples': sum(row.created_at < row.window_end_at for row in rows),
                    'valid_native_reads': sum(row.metric_summaries.get('received_samples', 0) for row in rows)},
        'workload': workload([row for row in rows if row.resolution_seconds == 60]),
        'metric_summaries': merged}


def compact_history(*, apply=False, now=None, limit=500):
    now = now or timezone.now()
    retention = getattr(settings, 'NIGHTOWL_TELEMETRY_RETENTION_DAYS', {60: 7, 300: 30, 3600: 90})
    if not all(type(retention.get(resolution)) is int and retention[resolution] > 0 for resolution in (60, 300, 3600)):
        raise ValueError('invalid_retention')
    counts = {'groups': 0, 'source_rows': 0, 'expired_hourly': 0}
    for source_resolution, target_resolution in ((60, 300), (300, 3600)):
        candidates = EndpointPerformanceSample.objects.filter(schema_version=2, resolution_seconds=source_resolution,
            collected_at__lt=now - timedelta(days=retention[source_resolution])).only('pk','endpoint_id','collected_at').order_by('endpoint_id', 'collected_at')[:limit * 60]
        groups = {}
        for row in candidates.iterator(chunk_size=500):
            bucket = row.collected_at.fromtimestamp(int(row.collected_at.timestamp()) // target_resolution * target_resolution, tz=row.collected_at.tzinfo)
            groups.setdefault((row.endpoint_id, bucket), []).append(row.pk)
            if len(groups) >= limit:
                break
        for (endpoint_id, bucket), ids in groups.items():
            counts['groups'] += 1
            counts['source_rows'] += len(ids)
            if not apply:
                continue
            with transaction.atomic():
                from .models import AgentMachine
                AgentMachine.objects.select_for_update().get(pk=endpoint_id)
                rows = list(EndpointPerformanceSample.objects.select_for_update().filter(pk__in=ids))
                target = EndpointPerformanceSample.objects.filter(endpoint_id=endpoint_id, schema_version=2,
                    resolution_seconds=target_resolution, collected_at=bucket).first()
                incoming = [item.metric_summaries for item in rows]
                if target:
                    incoming.insert(0, target.metric_summaries)
                hashes = {}
                for item in incoming:
                    hashes.update(item.get('source_hashes', {}))
                    if item['resolution_seconds'] == 60:
                        hashes[str(item['window_id'])] = hashlib.sha256(json.dumps(item, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                merged = {'window_id': str(rollup_id(endpoint_id, bucket, target_resolution)), 'start_at': bucket.isoformat(),
                    'end_at': (bucket + timedelta(seconds=target_resolution)).isoformat(), 'resolution_seconds': target_resolution,
                    'expected_samples': target_resolution // 10, 'received_samples': sum(item['received_samples'] for item in incoming),
                    'metrics': merge_metrics(item['metrics'] for item in incoming), 'quality': {'kind': 'historical_rollup'}, 'source_hashes': hashes}
                fields = {column: mean(merged['metrics'].get(name)) for name, column in CORE_METRICS.items()}
                fields['collection_duration_ms'] = round(fields['collection_duration_ms'] or 0)
                if fields['agent_working_set_bytes'] is not None:
                    fields['agent_working_set_bytes'] = round(fields['agent_working_set_bytes'])
                EndpointPerformanceSample.objects.update_or_create(endpoint_id=endpoint_id, sample_id=merged['window_id'], defaults={
                    'schema_version': 2, 'resolution_seconds': target_resolution, 'collected_at': bucket,
                    'window_end_at': bucket + timedelta(seconds=target_resolution), 'metric_summaries': merged, 'uptime_seconds': None, **fields})
                EndpointPerformanceSample.objects.filter(pk__in=[row.pk for row in rows]).delete()
    expired = EndpointPerformanceSample.objects.filter(schema_version=2, resolution_seconds=3600,
        window_end_at__lt=now - timedelta(days=retention[3600]))
    expired_ids = list(expired.values_list('pk', flat=True)[:limit])
    counts['expired_hourly'] = len(expired_ids)
    if apply:
        EndpointPerformanceSample.objects.filter(pk__in=expired_ids).delete()
    return counts
