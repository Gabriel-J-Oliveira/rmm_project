"""Read-only presentation data for the OBZ capacity dashboard."""

from collections import defaultdict
from datetime import timedelta
import math
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.db import connection
from django.db.models import Count, Min, OuterRef, Q, Subquery
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from agents.models import AgentMachine, EndpointAlert, EndpointPerformanceSample, InventorySnapshot
from agents.telemetry_analytics import build_endpoint_telemetry_summary
from agents.telemetry_diagnostics import (
    build_capacity_assessment,
    build_resource_diagnostics,
    build_telemetry_evidence,
)


CAPACITY_PERMISSIONS = (
    'agents.view_agentmachine',
    'agents.view_endpointperformancesample',
    'agents.view_inventorysnapshot',
    'agents.view_endpointalert',
)
SAMPLE_FIELDS = (
    'cpu_percent', 'memory_used_percent', 'memory_committed_percent',
    'network_received_bytes', 'network_sent_bytes', 'collection_duration_ms',
    'agent_working_set_bytes', 'telemetry_errors_count',
)
ACTIVE_ALERT_STATUSES = (EndpointAlert.STATUS_OPEN, EndpointAlert.STATUS_ACKNOWLEDGED)


def _hardware(snapshot):
    if snapshot is None:
        return None
    raw = snapshot.raw_payload if isinstance(snapshot.raw_payload, dict) else {}
    collections = raw.get('collections') if isinstance(raw.get('collections'), dict) else {}
    source = collections.get('hardware') if isinstance(collections.get('hardware'), dict) else {}
    cpu = source.get('cpu') if isinstance(source.get('cpu'), dict) else {}
    memory = source.get('memory') if isinstance(source.get('memory'), dict) else {}
    return {
        'cpu': {
            'name': cpu.get('name') or snapshot.cpu or None,
            'physical_cores': cpu.get('physical_cores'),
            'logical_processors': cpu.get('logical_processors'),
            'max_clock_mhz': cpu.get('max_clock_mhz'),
        },
        'memory': {
            'total_bytes': memory.get('total_bytes'),
            'slots_total': memory.get('slots_total'),
            'slots_used': memory.get('slots_used'),
            'slots_free': memory.get('slots_free'),
            'modules': memory.get('modules'),
        },
        'memory_total_bytes': source.get('memory_total_bytes') or snapshot.memory_total_bytes,
    }


def _stats(summary):
    return {
        'cpu': summary['cpu']['usage_percent'],
        'memory_used': summary['memory']['used_percent'],
        'memory_committed': summary['memory']['committed_percent'],
    }


def _disk_usage(snapshot):
    result = dict(system_disk_used_percent=None, max_disk_used_percent=None,
                  min_disk_free_bytes=None, disk_count=None)
    volumes = snapshot.disks if snapshot and isinstance(snapshot.disks, list) else []
    valid = []
    for item in volumes:
        if not isinstance(item, dict):
            continue
        size, free = item.get('size_bytes'), item.get('free_bytes')
        if (isinstance(size, bool) or isinstance(free, bool) or
                not isinstance(size, (int, float)) or not isinstance(free, (int, float)) or
                not math.isfinite(size) or not math.isfinite(free) or
                size <= 0 or not 0 <= free <= size):
            continue
        used = (size - free) / size * 100
        valid.append((used, free))
        name = item.get('name')
        if isinstance(name, str) and name.strip().upper().rstrip('\\/') == 'C:':
            result['system_disk_used_percent'] = used
    if valid:
        result.update(max_disk_used_percent=max(x[0] for x in valid),
                      min_disk_free_bytes=min(x[1] for x in valid), disk_count=len(valid))
    return result


def _latest_processes(process_consumers):
    if not isinstance(process_consumers, dict):
        return []
    items = []
    for category in ('cpu', 'memory'):
        for process in process_consumers.get(category) or []:
            if not isinstance(process, dict):
                continue
            name = process.get('process_name')
            if not isinstance(name, str) or not name:
                continue
            items.append({
                'category': category, 'name': name[:120],
                'cpu_percent': process.get('cpu_percent'),
                'working_set_bytes': process.get('working_set_bytes'),
            })
    return items


def _classification(capacity, endpoint, critical_count):
    statuses = {item['status'] for item in capacity['capacity'].values()}
    insufficient = 'NOT_EVALUATED' in statuses
    sustained = 'SUSTAINED_PRESSURE' in statuses
    observe = 'OBSERVE' in statuses
    offline = endpoint.status != AgentMachine.STATUS_ONLINE
    attention = insufficient or sustained or observe or offline or critical_count > 0
    return {
        'insufficient': insufficient, 'sustained': sustained, 'observe': observe,
        'offline': offline, 'attention': attention,
    }


def _alert_categories(alerts):
    types = alerts.get('types') or []
    return {
        'security': any(item['severity'] == EndpointAlert.SEVERITY_SECURITY or
                        any(term in item['alert_type'].lower() for term in ('security', 'antivirus', 'defender'))
                        for item in types),
        'agent_update': any(any(term in item['alert_type'].lower() for term in ('agent', 'update')) for item in types),
    }


def _row(endpoint, snapshot, primary, context, alerts, processes, now):
    primary_evidence = build_telemetry_evidence(primary)
    primary_diagnostics = build_resource_diagnostics(primary)
    context_diagnostics = build_resource_diagnostics(context)
    capacity = build_capacity_assessment(primary_diagnostics, context_diagnostics, _hardware(snapshot))
    critical = alerts.get('critical', 0)
    hardware = capacity['hardware_context']
    os_name = (snapshot.os_name if snapshot else None) or endpoint.os_name or None
    os_version = (snapshot.os_version if snapshot else None) or endpoint.os_version or None
    categories = _alert_categories(alerts)
    snapshot_at = snapshot.received_at if snapshot else None
    return {
        'id': str(endpoint.pk), 'hostname': endpoint.hostname, 'status': endpoint.status,
        'fqdn': endpoint.fqdn or None,
        'first_seen': endpoint.first_seen_at.isoformat() if endpoint.first_seen_at else None,
        'last_ip': endpoint.last_ip, 'last_logged_user': endpoint.last_logged_user or None,
        'manufacturer': (snapshot.manufacturer if snapshot else None) or endpoint.manufacturer or None,
        'model': (snapshot.model if snapshot else None) or endpoint.model or None,
        'telemetry_last_at': (endpoint.obz_last_telemetry.isoformat() if getattr(endpoint, 'obz_last_telemetry', None)
                              else context['quality'].get('last_sample_at')),
        **_disk_usage(snapshot),
        'last_seen': endpoint.last_seen_at.isoformat() if endpoint.last_seen_at else None,
        'agent_version': endpoint.agent_version or None, 'os_name': os_name,
        'os_version': os_version, 'os_build': (snapshot.windows_build if snapshot else None) or endpoint.windows_build or None,
        'cpu_name': hardware['cpu']['name'], 'cpu_cores': hardware['cpu']['physical_cores'],
        'memory_total_bytes': hardware['memory']['total_bytes'],
        'cpu_inventory_status': hardware['cpu']['status'],
        'memory_inventory_status': hardware['memory']['status'],
        'cpu_p95': primary['cpu']['usage_percent']['p95'],
        'memory_p95': primary['memory']['used_percent']['p95'],
        'context_coverage': context['quality']['coverage_percent'],
        'coverage': primary['quality']['coverage_percent'],
        'received_samples': primary['quality']['received_samples'],
        'expected_samples': primary['quality']['expected_samples'],
        'evidence': primary_evidence['evidence']['overall']['status'],
        'cpu_capacity': capacity['capacity']['cpu']['status'],
        'memory_capacity': capacity['capacity']['memory']['status'],
        'alerts_total': alerts.get('total', 0), 'alerts_critical': critical,
        'alerts_warning': alerts.get('warning', 0),
        'security_alert': categories['security'], 'agent_update_alert': categories['agent_update'],
        'inventory_stale': snapshot_at is not None and snapshot_at < now - timedelta(days=7),
        'inventory_at': snapshot_at.isoformat() if snapshot_at else None,
        'alert_types': alerts.get('types') or [],
        'classifications': _classification(capacity, endpoint, critical),
        'processes': processes, 'endpoint_url': reverse('endpoint-detail', args=[endpoint.pk]),
        'alerts_url': reverse('alerts-list') + '?' + urlencode({'q': endpoint.hostname}),
    }


def _counts(rows):
    return {
        'monitored': len(rows),
        'attention': sum(row['classifications']['attention'] for row in rows),
        'sustained': sum(row['classifications']['sustained'] for row in rows),
        'observe': sum(row['classifications']['observe'] for row in rows),
        'insufficient': sum(row['classifications']['insufficient'] for row in rows),
        'offline': sum(row['classifications']['offline'] for row in rows),
        'critical': sum(row['alerts_critical'] > 0 for row in rows),
    }


def _demo_rows():
    states = (
        ('LAB-CPU', 'online', 'SUSTAINED_PRESSURE', 'NO_PRESSURE_OBSERVED', 1),
        ('LAB-RAM', 'online', 'NO_PRESSURE_OBSERVED', 'OBSERVE', 0),
        ('LAB-MISTO', 'online', 'OBSERVE', 'SUSTAINED_PRESSURE', 2),
        ('LAB-OK', 'online', 'NO_PRESSURE_OBSERVED', 'NO_PRESSURE_OBSERVED', 0),
        ('LAB-OFFLINE', 'offline', 'NOT_EVALUATED', 'NOT_EVALUATED', 0),
        ('LAB-PARCIAL', 'online', 'NOT_EVALUATED', 'NO_PRESSURE_OBSERVED', 0),
    )
    rows = []
    for index, (hostname, status, cpu, memory, critical) in enumerate(states, 1):
        insufficient = 'NOT_EVALUATED' in (cpu, memory)
        sustained = 'SUSTAINED_PRESSURE' in (cpu, memory)
        observe = 'OBSERVE' in (cpu, memory)
        rows.append({
            'id': f'00000000-0000-4000-8000-{index:012d}', 'hostname': hostname,
            'fqdn': hostname.lower() + '.example.test', 'first_seen': (timezone.now() - timedelta(hours=index * 10)).isoformat(),
            'last_ip': f'192.0.2.{index}', 'last_logged_user': 'demo',
            'manufacturer': 'Demo', 'model': 'Demo',
            'telemetry_last_at': None if insufficient else timezone.now().isoformat(),
            'system_disk_used_percent': 40, 'max_disk_used_percent': 40,
            'min_disk_free_bytes': 60000000000, 'disk_count': 1,
            'status': status, 'last_seen': None, 'agent_version': 'demo',
            'os_name': 'Windows Server' if index < 5 else None, 'os_version': None,
            'os_build': None, 'cpu_name': 'CPU demonstrativa' if index < 6 else None,
            'cpu_cores': 8 if index < 6 else None, 'memory_total_bytes': 17179869184 if index < 6 else None,
            'cpu_inventory_status': 'PARTIAL' if index == 6 else 'AVAILABLE',
            'memory_inventory_status': 'PARTIAL' if index == 6 else 'AVAILABLE',
            'cpu_p95': None if insufficient else [91, 22, 78, 24][(index - 1) % 4],
            'memory_p95': None if insufficient else [42, 83, 94, 36][(index - 1) % 4],
            'coverage': 0 if insufficient else 98, 'context_coverage': 0 if insufficient else 40,
            'received_samples': 0 if insufficient else 283, 'expected_samples': 288,
            'evidence': 'INSUFFICIENT' if insufficient else 'SUFFICIENT',
            'cpu_capacity': cpu, 'memory_capacity': memory,
            'alerts_total': critical, 'alerts_critical': critical, 'alerts_warning': 0,
            'security_alert': False, 'agent_update_alert': False,
            'inventory_stale': False, 'inventory_at': None,
            'alert_types': [{'alert_type': 'demo', 'severity': 'critical', 'count': critical}] if critical else [],
            'classifications': {'insufficient': insufficient, 'sustained': sustained,
                                'observe': observe, 'offline': status != 'online',
                                'attention': insufficient or sustained or observe or status != 'online' or critical > 0},
            'processes': [], 'endpoint_url': None, 'alerts_url': reverse('alerts-list'),
        })
    return rows


def _period_hours(request):
    value = request.GET.get('period', '24h')
    if value not in ('12h', '24h', '48h'):
        raise ValueError('invalid period')
    return int(value[:-1])


def _aggregate_window(start, end):
    """Compute the M4 fields consumed by M5 without loading fleet-wide raw rows."""
    query = """
        WITH ordered AS (
            SELECT endpoint_id, collected_at, created_at, cpu_percent,
                   memory_used_percent, memory_committed_percent, telemetry_errors_count,
                   LAG(collected_at) OVER (
                       PARTITION BY endpoint_id ORDER BY collected_at, id
                   ) AS previous_at
            FROM agents_endpointperformancesample
            WHERE collected_at >= %s AND collected_at < %s
        )
        SELECT endpoint_id, COUNT(*) AS received,
               COUNT(cpu_percent) AS cpu_valid,
               AVG(cpu_percent) AS cpu_avg,
               PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY cpu_percent) AS cpu_p95,
               PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY cpu_percent) AS cpu_p99,
               COUNT(memory_used_percent) AS used_valid,
               AVG(memory_used_percent) AS used_avg,
               PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY memory_used_percent) AS used_p95,
               PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY memory_used_percent) AS used_p99,
               COUNT(memory_committed_percent) AS committed_valid,
               AVG(memory_committed_percent) AS committed_avg,
               PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY memory_committed_percent) AS committed_p95,
               PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY memory_committed_percent) AS committed_p99,
               COUNT(*) FILTER (WHERE previous_at IS NOT NULL AND
                   EXTRACT(EPOCH FROM collected_at - previous_at) > 420) AS gaps,
               COUNT(*) FILTER (WHERE created_at < collected_at) AS negative_lags,
               COUNT(*) FILTER (WHERE telemetry_errors_count > 0) AS error_samples,
               SUM(telemetry_errors_count) AS total_errors,
               MIN(collected_at) AS first_sample_at, MAX(collected_at) AS last_sample_at
        FROM ordered GROUP BY endpoint_id
    """
    with connection.cursor() as cursor:
        cursor.execute(query, [start, end])
        names = [item[0] for item in cursor.description]
        return {row[0]: dict(zip(names, row)) for row in cursor.fetchall()}


def _compact_summary(endpoint, start, end, aggregate):
    """M4-compatible projection for evidence, diagnostics and capacity only."""
    data = aggregate or {}
    expected = math.ceil((end - start).total_seconds() / 300)
    received = data.get('received') or 0
    def stats(prefix):
        return {
            key: float(data[f'{prefix}_{key}']) if data.get(f'{prefix}_{key}') is not None else None
            for key in ('avg', 'p95', 'p99')
        }
    return {
        'schema_version': 1, 'endpoint_id': str(endpoint.pk),
        'window': {'start': start.isoformat(), 'end': end.isoformat(),
                   'duration_seconds': (end - start).total_seconds()},
        'quality': {
            'expected_samples': expected, 'received_samples': received,
            'first_sample_at': data['first_sample_at'].isoformat() if data.get('first_sample_at') else None,
            'last_sample_at': data['last_sample_at'].isoformat() if data.get('last_sample_at') else None,
            'coverage_percent': received / expected * 100 if expected else None,
            'gaps_over_threshold_count': data.get('gaps') or 0,
            'negative_lag_samples': data.get('negative_lags') or 0,
            'metric_validity': {
                'cpu_percent': {'valid_samples': data.get('cpu_valid') or 0},
                'memory_used_percent': {'valid_samples': data.get('used_valid') or 0},
                'memory_committed_percent': {'valid_samples': data.get('committed_valid') or 0},
            },
        },
        'agent': {'telemetry_errors': {'samples_with_errors': data.get('error_samples') or 0,
                                      'total_errors': data.get('total_errors') or 0}},
        'cpu': {'usage_percent': stats('cpu')},
        'memory': {'used_percent': stats('used'), 'committed_percent': stats('committed')},
    }


def _overview(now, hours):
    latest_processes_query = (EndpointPerformanceSample.objects
                              .filter(endpoint_id=OuterRef('pk'), collected_at__gte=now - timedelta(days=7), collected_at__lt=now)
                              .order_by('-collected_at', '-id').values('process_consumers')[:1])
    endpoints = list(AgentMachine.objects.only(
        'id', 'hostname', 'status', 'last_seen_at', 'agent_version',
        'os_name', 'os_version', 'windows_build', 'first_seen_at', 'fqdn',
        'last_ip', 'last_logged_user', 'manufacturer', 'model',
    ).annotate(obz_last_processes=Subquery(latest_processes_query),
               obz_last_telemetry=Subquery(EndpointPerformanceSample.objects.filter(
                   endpoint_id=OuterRef('pk'), collected_at__lt=now).order_by('-collected_at', '-id')
                   .values('collected_at')[:1])).order_by('hostname', 'pk'))
    if not endpoints:
        return []
    latest_pk = (InventorySnapshot.objects.filter(machine_id=OuterRef('machine_id'))
                 .order_by('-received_at', '-pk').values('pk')[:1])
    snapshots = {item.machine_id: item for item in
                 InventorySnapshot.objects.only(
                     'id', 'machine_id', 'os_name', 'os_version', 'windows_build',
                     'cpu', 'memory_total_bytes', 'raw_payload', 'received_at', 'disks', 'manufacturer', 'model',
                 ).filter(pk=Subquery(latest_pk), machine_id__in=[e.pk for e in endpoints])}
    alert_map = {}
    alert_types = EndpointAlert.objects.filter(
        endpoint_id__in=[e.pk for e in endpoints], status__in=ACTIVE_ALERT_STATUSES,
    ).values('endpoint_id', 'alert_type', 'severity').annotate(count=Count('pk'))
    for item in alert_types:
        aggregate = alert_map.setdefault(item['endpoint_id'], {'total': 0, 'critical': 0, 'warning': 0, 'types': []})
        aggregate['total'] += item['count']
        if item['severity'] == EndpointAlert.SEVERITY_CRITICAL:
            aggregate['critical'] += item['count']
        if item['severity'] == EndpointAlert.SEVERITY_WARNING:
            aggregate['warning'] += item['count']
        aggregate['types'].append({
            'alert_type': item['alert_type'], 'severity': item['severity'], 'count': item['count'],
        })
    primary_start = now - timedelta(hours=hours)
    context_start = now - timedelta(days=7)
    if connection.vendor == 'postgresql':
        primary_aggregates = _aggregate_window(primary_start, now)
        context_aggregates = _aggregate_window(context_start, now)
    else:
        # Test databases without ordered-set percentiles still use one bounded query.
        grouped = defaultdict(list)
        rows = (EndpointPerformanceSample.objects
                .filter(endpoint_id__in=[e.pk for e in endpoints], collected_at__gte=context_start, collected_at__lt=now)
                .order_by('endpoint_id', 'collected_at', 'id')
                .values_list('endpoint_id', 'collected_at', 'created_at', *SAMPLE_FIELDS))
        for endpoint_id, *sample in rows.iterator(chunk_size=2000):
            grouped[endpoint_id].append(tuple(sample))
    result = []
    for endpoint in endpoints:
        if connection.vendor == 'postgresql':
            primary = _compact_summary(endpoint, primary_start, now, primary_aggregates.get(endpoint.pk))
            context = _compact_summary(endpoint, context_start, now, context_aggregates.get(endpoint.pk))
        else:
            samples = grouped.get(endpoint.pk, [])
            primary_rows = [sample for sample in samples if sample[0] >= primary_start]
            primary = build_endpoint_telemetry_summary(endpoint, primary_start, now, sample_rows=primary_rows)
            context = build_endpoint_telemetry_summary(endpoint, context_start, now, sample_rows=samples)
        result.append(_row(endpoint, snapshots.get(endpoint.pk), primary, context,
                           alert_map.get(endpoint.pk, {}), _latest_processes(endpoint.obz_last_processes), now))
    return result


@login_required
@permission_required(CAPACITY_PERMISSIONS, raise_exception=True)
@require_GET
def capacity_page(request):
    return render(request, 'dashboard/capacity.html', {
        'active_nav': 'capacity', 'demo': settings.DEBUG and request.GET.get('demo') == '1',
    })


@login_required
@permission_required(CAPACITY_PERMISSIONS, raise_exception=True)
@require_GET
def capacity_overview(request):
    try:
        hours = _period_hours(request)
    except ValueError:
        return JsonResponse({'error': 'invalid_period'}, status=400)
    demo = settings.DEBUG and request.GET.get('demo') == '1'
    rows = _demo_rows() if demo else _overview(timezone.now(), hours)
    return JsonResponse({'demo': demo, 'generated_at': timezone.now().isoformat(),
                         'counts': _counts(rows), 'endpoints': rows})


@login_required
@permission_required(CAPACITY_PERMISSIONS, raise_exception=True)
@require_GET
def capacity_detail(request, pk):
    try:
        hours = _period_hours(request)
    except ValueError:
        return JsonResponse({'error': 'invalid_period'}, status=400)
    if settings.DEBUG and request.GET.get('demo') == '1':
        row = next((item for item in _demo_rows() if item['id'] == str(pk)), None)
        if row is None:
            raise Http404
        return JsonResponse({'demo': True, 'endpoint': row, 'primary': None,
                             'context': None, 'capacity': None, 'hardware': None,
                             'alerts': [], 'applications': [], 'processes': [], 'series_ranges': {}})
    endpoint = AgentMachine.objects.only(
        'id', 'hostname', 'status', 'last_seen_at', 'agent_version',
        'os_name', 'os_version', 'windows_build',
    ).filter(pk=pk).first()
    if endpoint is None:
        raise Http404
    now = timezone.now()
    primary = build_endpoint_telemetry_summary(endpoint, now - timedelta(hours=hours), now)
    context = build_endpoint_telemetry_summary(endpoint, now - timedelta(days=7), now)
    primary_diagnostics = build_resource_diagnostics(primary)
    context_diagnostics = build_resource_diagnostics(context)
    snapshot = InventorySnapshot.objects.only(
        'id', 'machine_id', 'os_name', 'os_version', 'windows_build',
        'cpu', 'memory_total_bytes', 'raw_payload', 'installed_software',
        'disks', 'uptime_seconds',
    ).filter(machine=endpoint).order_by('-received_at', '-pk').first()
    hardware = _hardware(snapshot)
    capacity = build_capacity_assessment(primary_diagnostics, context_diagnostics, hardware)
    alert_queryset = EndpointAlert.objects.filter(endpoint=endpoint).filter(
        Q(status__in=ACTIVE_ALERT_STATUSES) |
        Q(status=EndpointAlert.STATUS_RESOLVED, resolved_at__gte=now - timedelta(days=7)),
    )
    alert_counts = alert_queryset.aggregate(
        critical=Count('pk', filter=Q(status__in=ACTIVE_ALERT_STATUSES, severity=EndpointAlert.SEVERITY_CRITICAL)),
        warning=Count('pk', filter=Q(status__in=ACTIVE_ALERT_STATUSES, severity=EndpointAlert.SEVERITY_WARNING)),
        open=Count('pk', filter=Q(status=EndpointAlert.STATUS_OPEN)),
        resolved=Count('pk', filter=Q(status=EndpointAlert.STATUS_RESOLVED)),
        other=Count('pk', filter=Q(status__in=ACTIVE_ALERT_STATUSES) &
                    ~Q(severity__in=(EndpointAlert.SEVERITY_CRITICAL, EndpointAlert.SEVERITY_WARNING))),
    )
    alerts = list(alert_queryset.order_by('-last_seen_at').values(
        'alert_type', 'title', 'severity', 'status', 'last_seen_at',
    )[:30])
    last_sample = (EndpointPerformanceSample.objects.filter(
        endpoint=endpoint, collected_at__gte=now - timedelta(days=7), collected_at__lt=now,
    )
                   .order_by('-collected_at', '-pk').values('process_consumers', 'uptime_seconds').first())
    processes = _latest_processes(last_sample['process_consumers']) if last_sample else []
    sample_ranges = EndpointPerformanceSample.objects.filter(
        endpoint=endpoint, collected_at__gte=now - timedelta(days=7), collected_at__lt=now,
    ).aggregate(**{
        f'{field}_{key}': aggregate('collected_at' if field == 'first' else 'pk', filter=Q(
            collected_at__gte=now - timedelta(hours=duration),
        ))
        for key, duration in (('6h', 6), ('24h', 24), ('3d', 72), ('7d', 168))
        for field, aggregate in (('first', Min), ('count', Count))
    })
    series_ranges = {
        key: bool(sample_ranges[f'count_{key}'] >= 2 and (duration <= 24 or (
            sample_ranges[f'first_{key}'] <= now - timedelta(hours=duration * 0.75)
            and sample_ranges[f'count_{key}'] >= duration * 12 * 0.5)))
        for key, duration in (('6h', 6), ('24h', 24), ('3d', 72), ('7d', 168))
    }
    raw = snapshot.raw_payload if snapshot and isinstance(snapshot.raw_payload, dict) else {}
    collections = raw.get('collections') if isinstance(raw.get('collections'), dict) else {}
    apps = snapshot.installed_software if snapshot and isinstance(snapshot.installed_software, list) else []
    applications = []
    for app in apps[:40]:
        if isinstance(app, dict):
            name = app.get('name') or app.get('display_name')
            if isinstance(name, str) and name:
                applications.append({'name': name[:160], 'version': str(app.get('version') or '')[:60]})
    hardware_raw = collections.get('hardware') if isinstance(collections.get('hardware'), dict) else {}
    disks = hardware_raw.get('physical_disks') if isinstance(hardware_raw.get('physical_disks'), list) else []
    disk_rows = []
    for item in disks[:8]:
        if not isinstance(item, dict):
            continue
        confident = item.get('association_confidence') in ('high', 'confirmed')
        disk_rows.append({
            'model': str(item.get('model') or '')[:120], 'size_bytes': item.get('size_bytes'),
            'media_type': item.get('media_type'), 'bus_type': item.get('bus_type'),
            'health_status': item.get('health_status'), 'operational_status': item.get('operational_status'),
            'is_system_disk': item.get('is_system_disk') if confident else None,
            'drive_letters': item.get('drive_letters') if confident and isinstance(item.get('drive_letters'), list) else [],
            'association_confidence': item.get('association_confidence'),
        })
    return JsonResponse({
        'demo': False,
        'endpoint': {
            'id': str(endpoint.pk), 'hostname': endpoint.hostname, 'status': endpoint.status,
            'agent_version': endpoint.agent_version, 'last_seen': endpoint.last_seen_at.isoformat() if endpoint.last_seen_at else None,
            'os_name': (snapshot.os_name if snapshot else None) or endpoint.os_name,
            'os_version': (snapshot.os_version if snapshot else None) or endpoint.os_version,
            'os_build': (snapshot.windows_build if snapshot else None) or endpoint.windows_build,
            'uptime_seconds': last_sample['uptime_seconds'] if last_sample else (snapshot.uptime_seconds if snapshot else None),
            'endpoint_url': reverse('endpoint-detail', args=[endpoint.pk]),
            'alerts_url': reverse('alerts-list') + '?' + urlencode({'q': endpoint.hostname}),
        },
        'primary': {'quality': primary['quality'], 'stats': _stats(primary), 'diagnostics': primary_diagnostics},
        'context': {'quality': context['quality'], 'stats': _stats(context), 'diagnostics': context_diagnostics},
        'capacity': capacity, 'hardware': capacity['hardware_context'],
        'alerts': alerts, 'alert_counts': alert_counts,
        'applications': applications, 'processes': processes,
        'disks': disk_rows, 'series_ranges': series_ranges,
    })


@login_required
@permission_required(CAPACITY_PERMISSIONS, raise_exception=True)
@require_GET
def capacity_series(request, pk):
    period = request.GET.get('period', '24h')
    hours = {'6h': 6, '24h': 24, '3d': 72, '7d': 168}.get(period)
    if hours is None:
        return JsonResponse({'error': 'invalid_period'}, status=400)
    if settings.DEBUG and request.GET.get('demo') == '1':
        if not any(row['id'] == str(pk) for row in _demo_rows()):
            raise Http404
        return JsonResponse({'demo': True, 'series': []})
    if not AgentMachine.objects.filter(pk=pk).exists():
        raise Http404
    end = timezone.now()
    samples = (EndpointPerformanceSample.objects.filter(
        endpoint_id=pk, collected_at__gte=end - timedelta(hours=hours), collected_at__lt=end,
    ).order_by('-collected_at').values('collected_at', 'cpu_percent', 'memory_used_percent')[:2100])
    return JsonResponse({'demo': False, 'series': [
        {'at': item['collected_at'].isoformat(), 'cpu': item['cpu_percent'], 'memory': item['memory_used_percent']}
        for item in reversed(list(samples))
    ]})
