"""Read-only, bounded measurements for RC48 telemetry-retention decisions."""

import json
import math
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.db.models import Count, F, Func, IntegerField, Max, Min, ExpressionWrapper
from django.utils import timezone

from .models import EndpointPerformanceSample


RESOLUTIONS = (60, 300, 3600)
METRIC_GROUPS = ('cpu', 'memory', 'disk', 'network')


class PostgreSqlColumnSize(Func):
    function = 'pg_column_size'
    output_field = IntegerField()


def _metric_groups_present(metric_summaries):
    metrics = metric_summaries.get('metrics') if isinstance(metric_summaries, dict) else None
    if not isinstance(metrics, dict):
        return set()
    return {group for group in METRIC_GROUPS
            if any(isinstance(name, str) and name.startswith(group + '.') for name in metrics)}


def _quality_status(metric_summaries):
    if not isinstance(metric_summaries, dict):
        return 'unknown', None, None
    expected = metric_summaries.get('expected_samples')
    received = metric_summaries.get('received_samples')
    if (type(expected) is not int or expected <= 0 or type(received) is not int
            or received < 0 or received > expected):
        return 'unknown', None, None
    return ('complete' if received == expected else 'partial'), received, expected


def _metric_family(name):
    if not isinstance(name, str):
        return None
    for group in ('disk', 'network'):
        prefix = group + '.'
        if name.startswith(prefix):
            _, separator, metric_name = name[len(prefix):].partition('.')
            return prefix + metric_name if separator and metric_name else None
    if name.startswith(('cpu.', 'memory.', 'agent.')):
        return name
    return None


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _postgres_table_size_bytes():
    if connection.vendor != 'postgresql':
        return None
    table_name = EndpointPerformanceSample._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_total_relation_size(%s::regclass)', [table_name])
        value = cursor.fetchone()[0]
    return int(value) if value is not None else None


def build_telemetry_storage_report(*, all_endpoints=False, endpoint_id=None,
                                   window_days=90, sample_limit=200, now=None):
    """Report exact row counts and bounded quality/size samples; never writes rows."""
    if bool(all_endpoints) == bool(endpoint_id):
        raise ValueError('select_all_endpoints_or_one_endpoint')
    if type(window_days) is not int or not 1 <= window_days <= 365:
        raise ValueError('invalid_window_days')
    if type(sample_limit) is not int or not 1 <= sample_limit <= 1000:
        raise ValueError('invalid_sample_limit')

    retention = getattr(settings, 'NIGHTOWL_TELEMETRY_RETENTION_DAYS', {})
    if not all(type(retention.get(resolution)) is int and retention[resolution] > 0
               for resolution in RESOLUTIONS):
        raise ValueError('invalid_retention_settings')

    now = now or timezone.now()
    window_start = now - timedelta(days=window_days)
    queryset = EndpointPerformanceSample.objects.filter(
        collected_at__gte=window_start,
        collected_at__lt=now,
    )
    if endpoint_id is not None:
        queryset = queryset.filter(endpoint_id=endpoint_id)

    grouped = list(queryset.values('schema_version', 'resolution_seconds').annotate(
        row_count=Count('id'),
        endpoint_count=Count('endpoint_id', distinct=True),
        first_collected_at=Min('collected_at'),
        last_collected_at=Max('collected_at'),
    ).order_by('schema_version', 'resolution_seconds'))

    groups = []
    for aggregate in grouped:
        schema_version = aggregate['schema_version']
        resolution = aggregate['resolution_seconds']
        group_rows = queryset.filter(schema_version=schema_version,
                                     resolution_seconds=resolution)
        retention_days = retention.get(resolution) if schema_version == 2 else None
        expired_count = None
        if retention_days is not None:
            expired_count = group_rows.filter(
                collected_at__lt=now - timedelta(days=retention_days)
            ).count()

        sampled = list(group_rows.order_by('-collected_at', 'id')[:sample_limit].values(
            'metric_summaries', 'process_consumers',
        ))
        quality_counts = {'complete': 0, 'partial': 0, 'unknown': 0}
        received_values, expected_values = [], []
        metric_group_counts = {group: 0 for group in METRIC_GROUPS}
        source_group_status_counts = {group: {} for group in METRIC_GROUPS}
        sampled_metrics = {}
        logical_payload_sizes, postgres_payload_sizes = [], []

        if connection.vendor == 'postgresql' and sampled:
            storage_expression = ExpressionWrapper(
                PostgreSqlColumnSize(F('metric_summaries'))
                + PostgreSqlColumnSize(F('process_consumers')),
                output_field=IntegerField(),
            )
            storage_rows = group_rows.order_by('-collected_at', 'id').annotate(
                sampled_storage_bytes=storage_expression,
            ).values_list('sampled_storage_bytes',)[:sample_limit]
            postgres_payload_sizes = [int(value) for (value,) in storage_rows if value is not None]

        for row in sampled:
            summaries = row['metric_summaries']
            consumers = row['process_consumers']
            quality, received, expected = _quality_status(summaries)
            quality_counts[quality] += 1
            if received is not None:
                received_values.append(received)
                expected_values.append(expected)
            for group in _metric_groups_present(summaries):
                metric_group_counts[group] += 1
            quality_details = summaries.get('quality') if isinstance(summaries, dict) else None
            status_counts = quality_details.get('group_status_counts') if isinstance(quality_details, dict) else None
            if isinstance(status_counts, dict):
                for group in METRIC_GROUPS:
                    values = status_counts.get(group)
                    if isinstance(values, dict):
                        for status, count in values.items():
                            if isinstance(status, str) and type(count) is int and count > 0:
                                totals = source_group_status_counts[group]
                                totals[status] = totals.get(status, 0) + count
            elif isinstance(quality_details, dict):
                for group in METRIC_GROUPS:
                    status = quality_details.get(group)
                    if isinstance(status, str):
                        source_group_status_counts[group][status] = (
                            source_group_status_counts[group].get(status, 0) + 1)
            metrics = summaries.get('metrics') if isinstance(summaries, dict) else None
            if isinstance(metrics, dict):
                for name, metric in metrics.items():
                    family = _metric_family(name)
                    if not family or not isinstance(metric, dict):
                        continue
                    count, minimum, maximum = metric.get('count'), metric.get('min'), metric.get('max')
                    if (type(count) is not int or count < 0
                            or not isinstance(minimum, (int, float)) or isinstance(minimum, bool)
                            or not isinstance(maximum, (int, float)) or isinstance(maximum, bool)
                            or not math.isfinite(minimum) or not math.isfinite(maximum)):
                        continue
                    metric_aggregate = sampled_metrics.setdefault(family, {
                        'sampled_rows_with_values': 0, 'valid_native_reads': 0,
                        'min': None, 'max': None,
                    })
                    metric_aggregate['sampled_rows_with_values'] += 1
                    metric_aggregate['valid_native_reads'] += count
                    metric_aggregate['min'] = (minimum if metric_aggregate['min'] is None
                                               else min(metric_aggregate['min'], minimum))
                    metric_aggregate['max'] = (maximum if metric_aggregate['max'] is None
                                               else max(metric_aggregate['max'], maximum))
            payload = (json.dumps(summaries, ensure_ascii=False, sort_keys=True,
                                  separators=(',', ':'))
                       + json.dumps(consumers, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')))
            logical_payload_sizes.append(len(payload.encode('utf-8')))

        groups.append({
            'schema_version': schema_version,
            'resolution_seconds': resolution,
            'row_count': aggregate['row_count'],
            'endpoint_count': aggregate['endpoint_count'],
            'first_collected_at': aggregate['first_collected_at'].isoformat(),
            'last_collected_at': aggregate['last_collected_at'].isoformat(),
            'source_retention_days': retention_days,
            'rows_beyond_source_retention': expired_count,
            'sample_limit': sample_limit,
            'sampled_rows': len(sampled),
            'sample_order': 'most_recent_first',
            'sample_quality': {
                'complete_windows': quality_counts['complete'],
                'partial_windows': quality_counts['partial'],
                'unknown_windows': quality_counts['unknown'],
                'received_samples_min': min(received_values) if received_values else None,
                'received_samples_max': max(received_values) if received_values else None,
                'expected_samples_min': min(expected_values) if expected_values else None,
                'expected_samples_max': max(expected_values) if expected_values else None,
                'rows_with_metric_group': metric_group_counts,
                'source_group_status_counts': source_group_status_counts,
            },
            'sampled_metric_ranges': sampled_metrics,
            'sampled_payload_bytes': {
                'logical_json_min': min(logical_payload_sizes) if logical_payload_sizes else None,
                'logical_json_max': max(logical_payload_sizes) if logical_payload_sizes else None,
                'logical_json_mean': (sum(logical_payload_sizes) / len(logical_payload_sizes)
                                      if logical_payload_sizes else None),
                'logical_json_p95': _percentile(logical_payload_sizes, .95),
                'postgres_json_storage_bytes': (sum(postgres_payload_sizes)
                                                if postgres_payload_sizes else None),
            },
        })

    return {
        'report_version': 1,
        'read_only': True,
        'generated_at': now.isoformat(),
        'window_days': window_days,
        'scope': 'all_endpoints' if all_endpoints else 'single_endpoint',
        'database_vendor': connection.vendor,
        'postgres_table_total_bytes': _postgres_table_size_bytes(),
        'postgres_table_size_scope': 'global table including indexes and TOAST; not attributable by endpoint or resolution',
        'retention_days': {str(key): retention[key] for key in RESOLUTIONS},
        'sampling_note': 'quality and payload statistics use the most recent bounded sample; row counts and date bounds are exact within the requested time window',
        'groups': groups,
    }
