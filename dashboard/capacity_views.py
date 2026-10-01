"""Read-only presentation data for the OBZ capacity dashboard."""

from collections import defaultdict
from datetime import timedelta
import math
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.db import connection
from django.db.models import Count, OuterRef, Q, Subquery
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


def _row(endpoint, snapshot, primary, context, alerts, processes):
    primary_evidence = build_telemetry_evidence(primary)
    primary_diagnostics = build_resource_diagnostics(primary)
    context_diagnostics = build_resource_diagnostics(context)
    capacity = build_capacity_assessment(primary_diagnostics, context_diagnostics, _hardware(snapshot))
    critical = alerts.get('critical', 0)
    hardware = capacity['hardware_context']
    os_name = (snapshot.os_name if snapshot else None) or endpoint.os_name or None
    os_version = (snapshot.os_version if snapshot else None) or endpoint.os_version or None
    return {
        'id': str(endpoint.pk), 'hostname': endpoint.hostname, 'status': endpoint.status,
        'last_seen': endpoint.last_seen_at.isoformat() if endpoint.last_seen_at else None,
        'agent_version': endpoint.agent_version or None, 'os_name': os_name,
        'os_version': os_version, 'os_build': (snapshot.windows_build if snapshot else None) or endpoint.windows_build or None,
        'cpu_name': hardware['cpu']['name'], 'cpu_cores': hardware['cpu']['physical_cores'],
        'memory_total_bytes': hardware['memory']['total_bytes'],
        'cpu_inventory_status': hardware['cpu']['status'],
        'memory_inventory_status': hardware['memory']['status'],
        'cpu_p95': primary['cpu']['usage_percent']['p95'],
        'memory_p95': primary['memory']['used_percent']['p95'],
        'coverage': primary['quality']['coverage_percent'],
        'evidence': primary_evidence['evidence']['overall']['status'],
        'cpu_capacity': capacity['capacity']['cpu']['status'],
        'memory_capacity': capacity['capacity']['memory']['status'],
        'alerts_total': alerts.get('total', 0), 'alerts_critical': critical,
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
            'status': status, 'last_seen': None, 'agent_version': 'demo',
            'os_name': 'Windows Server' if index < 5 else None, 'os_version': None,
            'os_build': None, 'cpu_name': 'CPU demonstrativa' if index < 6 else None,
            'cpu_cores': 8 if index < 6 else None, 'memory_total_bytes': 17179869184 if index < 6 else None,
            'cpu_inventory_status': 'PARTIAL' if index == 6 else 'AVAILABLE',
            'memory_inventory_status': 'PARTIAL' if index == 6 else 'AVAILABLE',
            'cpu_p95': None if insufficient else [91, 22, 78, 24][(index - 1) % 4],
            'memory_p95': None if insufficient else [42, 83, 94, 36][(index - 1) % 4],
            'coverage': 0 if insufficient else 98, 'evidence': 'INSUFFICIENT' if insufficient else 'SUFFICIENT',
            'cpu_capacity': cpu, 'memory_capacity': memory,
            'alerts_total': critical, 'alerts_critical': critical,
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
               SUM(telemetry_errors_count) AS total_errors
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
        return {'avg': data.get(f'{prefix}_avg'), 'p95': data.get(f'{prefix}_p95'),
                'p99': data.get(f'{prefix}_p99')}
    return {
        'schema_version': 1, 'endpoint_id': str(endpoint.pk),
        'window': {'start': start.isoformat(), 'end': end.isoformat(),
                   'duration_seconds': (end - start).total_seconds()},
        'quality': {
            'expected_samples': expected, 'received_samples': received,
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
        'os_name', 'os_version', 'windows_build',
    ).annotate(obz_last_processes=Subquery(latest_processes_query)).order_by('hostname', 'pk'))
    if not endpoints:
        return []
    latest_pk = (InventorySnapshot.objects.filter(machine_id=OuterRef('machine_id'))
                 .order_by('-received_at', '-pk').values('pk')[:1])
    snapshots = {item.machine_id: item for item in
                 InventorySnapshot.objects.only(
                     'id', 'machine_id', 'os_name', 'os_version', 'windows_build',
                     'cpu', 'memory_total_bytes', 'raw_payload',
                 ).filter(pk=Subquery(latest_pk), machine_id__in=[e.pk for e in endpoints])}
    alert_map = {item['endpoint_id']: item for item in
                 EndpointAlert.objects.filter(endpoint_id__in=[e.pk for e in endpoints], status__in=ACTIVE_ALERT_STATUSES)
                 .values('endpoint_id').annotate(total=Count('pk'), critical=Count('pk', filter=Q(severity=EndpointAlert.SEVERITY_CRITICAL)))}
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
                           alert_map.get(endpoint.pk, {}), _latest_processes(endpoint.obz_last_processes)))
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
                             'alerts': [], 'applications': [], 'processes': [], 'series': []})
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
    alerts = list(EndpointAlert.objects.filter(endpoint=endpoint, status__in=ACTIVE_ALERT_STATUSES)
                  .order_by('-last_seen_at').values('title', 'severity', 'status', 'last_seen_at')[:20])
    samples = list(EndpointPerformanceSample.objects.filter(endpoint=endpoint, collected_at__gte=now - timedelta(hours=hours))
                   .order_by('-collected_at').values('collected_at', 'cpu_percent', 'memory_used_percent',
                                                    'process_consumers', 'uptime_seconds')[:288])
    series = [{'at': item['collected_at'].isoformat(), 'cpu': item['cpu_percent'],
               'memory': item['memory_used_percent']} for item in reversed(samples)]
    processes = _latest_processes(samples[0]['process_consumers']) if samples else []
    raw = snapshot.raw_payload if snapshot and isinstance(snapshot.raw_payload, dict) else {}
    collections = raw.get('collections') if isinstance(raw.get('collections'), dict) else {}
    apps = snapshot.installed_software if snapshot and isinstance(snapshot.installed_software, list) else []
    applications = []
    for app in apps[:40]:
        if isinstance(app, dict):
            name = app.get('name') or app.get('display_name')
            if isinstance(name, str) and name:
                applications.append({'name': name[:160], 'version': str(app.get('version') or '')[:60]})
    disk = collections.get('disk') if isinstance(collections.get('disk'), dict) else {}
    disks = disk.get('disks') if isinstance(disk.get('disks'), list) else snapshot.disks if snapshot and isinstance(snapshot.disks, list) else []
    return JsonResponse({
        'demo': False,
        'endpoint': {
            'id': str(endpoint.pk), 'hostname': endpoint.hostname, 'status': endpoint.status,
            'agent_version': endpoint.agent_version, 'last_seen': endpoint.last_seen_at.isoformat() if endpoint.last_seen_at else None,
            'os_name': (snapshot.os_name if snapshot else None) or endpoint.os_name,
            'os_version': (snapshot.os_version if snapshot else None) or endpoint.os_version,
            'os_build': (snapshot.windows_build if snapshot else None) or endpoint.windows_build,
            'uptime_seconds': samples[0]['uptime_seconds'] if samples else (snapshot.uptime_seconds if snapshot else None),
            'endpoint_url': reverse('endpoint-detail', args=[endpoint.pk]),
            'alerts_url': reverse('alerts-list') + '?' + urlencode({'q': endpoint.hostname}),
        },
        'primary': {'quality': primary['quality'], 'stats': _stats(primary), 'diagnostics': primary_diagnostics},
        'context': {'quality': context['quality'], 'stats': _stats(context), 'diagnostics': context_diagnostics},
        'capacity': capacity, 'hardware': capacity['hardware_context'],
        'alerts': alerts, 'applications': applications, 'processes': processes,
        'disks': [{'model': str(item.get('model') or '')[:120], 'size_bytes': item.get('size_bytes')}
                  for item in disks[:8] if isinstance(item, dict)],
        'series': series,
    })
