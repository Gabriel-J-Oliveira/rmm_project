from datetime import datetime, time, timedelta

import csv

from django.contrib import messages
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone
from django.views.decorators.http import require_POST

from agents.models import (
    AgentEnrollmentLog,
    AgentEnrollmentToken,
    AgentMachine,
    AgentManualValidationToken,
    AlertEvent,
    AuditEvent,
    EndpointAlert,
    MaintenanceRun,
    SoftwarePolicy,
    SoftwarePolicyException,
    SoftwarePolicyTargetEndpoint,
    SoftwarePolicyViolation,
)
from agents.audit import create_audit_event
from agents.software_catalog import (
    ADMIN_NETWORK_SOFTWARE,
    CATEGORY_LABELS,
    REMOTE_ACCESS_SOFTWARE,
    RISK_LABELS,
    SECURITY_SOFTWARE,
    classify_software as classify_software_catalog,
    normalize_key,
)
from agents.versioning import agent_version_state


REMOTE_ACCESS_TERMS = REMOTE_ACCESS_SOFTWARE
ADMIN_TOOL_TERMS = ADMIN_NETWORK_SOFTWARE
SECURITY_TERMS = SECURITY_SOFTWARE

ALERT_TYPE_OPTIONS = [
    ('disk_low', 'Disco baixo'),
    ('endpoint_offline', 'Endpoint offline'),
    ('security_antivirus', 'Antivirus'),
    ('security_alternative_av', 'AV alternativo'),
    ('high_uptime', 'Uptime alto'),
    ('remote_access_software', 'Acesso remoto'),
    ('admin_network_tool', 'Admin/Rede'),
    ('stale_inventory', 'Inventario desatualizado'),
    ('low_memory', 'Memoria baixa'),
    ('agent_outdated', 'Agente desatualizado'),
    ('software_policy_violation', 'Politica de Software'),
]

PERIOD_OPTIONS = [
    ('all', 'Todos'),
    ('today', 'Hoje'),
    ('24h', '24h'),
    ('7d', '7d'),
]

EVENT_PERIOD_OPTIONS = [
    ('24h', '24h'),
    ('7d', '7d'),
    ('30d', '30d'),
    ('all', 'Todos'),
]

SOFTWARE_CATEGORY_OPTIONS = [('all', 'Todas'), *CATEGORY_LABELS.items()]
SOFTWARE_RISK_OPTIONS = [('all', 'Todos'), *RISK_LABELS.items()]

MUTE_DURATIONS = {
    '1h': ('1 hora', timedelta(hours=1)),
    '4h': ('4 horas', timedelta(hours=4)),
    '24h': ('24 horas', timedelta(hours=24)),
    '7d': ('7 dias', timedelta(days=7)),
}


def redirect_back(request):
    return redirect(request.META.get('HTTP_REFERER') or 'alerts-list')


def actor_label(request):
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        return user.get_username()
    return ''


def create_alert_event(alert, event_type, message, request=None, metadata=None):
    AlertEvent.objects.create(
        alert=alert,
        event_type=event_type,
        message=message,
        metadata=metadata or {},
        actor=actor_label(request) if request else '',
    )


def audit_alert_action(request, alert, event_type, title, description='', severity=AuditEvent.SEVERITY_INFO, metadata=None):
    create_audit_event(
        event_type=event_type,
        title=title,
        description=description,
        severity=severity,
        endpoint=alert.endpoint,
        alert=alert,
        metadata={
            'alert_type': alert.alert_type,
            'alert_id': str(alert.id),
            'severity': alert.severity,
            'status': alert.status,
            **(metadata or {}),
        },
        request=request,
    )


def build_agent_install_command(enrollment_token):
    source_path = getattr(settings, 'NIGHTOWL_AGENT_SOURCE_PATH', r'\\192.168.104.120\controlsul\Comum\_Agents')
    heartbeat_url = getattr(settings, 'NIGHTOWL_AGENT_HEARTBEAT_URL', 'http://192.168.101.242:8000/api/agent/heartbeat/')
    installer_path = f'{source_path}\\Install-RmmAgent.ps1'
    return (
        'powershell.exe -ExecutionPolicy Bypass '
        f'-File "{installer_path}" '
        f'-ServerUrl "{heartbeat_url}" '
        f'-EnrollmentToken "{enrollment_token}" '
        '-RunOnce -RunCheck'
    )


def enrollment_token_state(token):
    if not token.is_active:
        return {'key': 'critical', 'label': 'Revogado'}
    if token.is_expired:
        return {'key': 'unknown', 'label': 'Expirado'}
    if token.usage_limit_reached:
        return {'key': 'warning', 'label': 'Esgotado'}
    return {'key': 'online', 'label': 'Ativo'}


def software_display_name(software):
    return str((software or {}).get('name') or '').strip()


def software_publisher(software):
    return str((software or {}).get('publisher') or '').strip()


def software_version_value(software):
    return str((software or {}).get('version') or '').strip()


def latest_snapshot_rows():
    rows = []
    endpoints = AgentMachine.objects.prefetch_related('inventory_snapshots').order_by('hostname', 'domain')
    for endpoint in endpoints:
        snapshot = endpoint.inventory_snapshots.order_by('-received_at').first()
        if snapshot:
            rows.append((endpoint, snapshot))
    return rows


def build_software_inventory():
    inventory = {}
    total_installs = 0
    endpoints_with_inventory = 0

    for endpoint, snapshot in latest_snapshot_rows():
        endpoints_with_inventory += 1
        for software in snapshot.installed_software or []:
            name = software_display_name(software)
            if not name:
                continue
            publisher = software_publisher(software)
            version = software_version_value(software)
            classification = classify_software_catalog(software)
            key = f'{normalize_key(name)}::{normalize_key(publisher)}'
            item = inventory.setdefault(key, {
                'key': key,
                'name': name,
                'publisher': publisher,
                'category': classification['category'],
                'category_label': classification['category_label'],
                'risk_level': classification['risk_level'],
                'risk_label': classification['risk_label'],
                'is_sensitive': classification['is_sensitive'],
                'install_count': 0,
                'endpoint_ids': set(),
                'versions': set(),
                'latest_seen_at': snapshot.received_at,
                'endpoints': [],
            })

            item['install_count'] += 1
            total_installs += 1
            item['endpoint_ids'].add(endpoint.id)
            if version:
                item['versions'].add(version)
            if snapshot.received_at > item['latest_seen_at']:
                item['latest_seen_at'] = snapshot.received_at
            item['endpoints'].append({
                'endpoint': endpoint,
                'version': version,
                'snapshot': snapshot,
            })

    rows = []
    for item in inventory.values():
        endpoint_count = len(item['endpoint_ids'])
        versions = sorted(item['versions'])
        rows.append({
            **item,
            'endpoint_count': endpoint_count,
            'versions': versions,
            'versions_display': ', '.join(versions[:4]) + (' +' + str(len(versions) - 4) if len(versions) > 4 else '') if versions else '—',
            'example_endpoints': sorted(item['endpoints'], key=lambda row: row['endpoint'].hostname)[:4],
        })

    risk_order = {'security': 0, 'warning': 1, 'info': 2, 'ok': 3, 'unknown': 4}
    category_order = {'remote_access': 0, 'admin_network': 1, 'security': 2, 'development': 3, 'browser': 4, 'office': 5, 'utility': 6, 'unknown': 7}
    rows.sort(key=lambda row: (
        risk_order.get(row['risk_level'], 9),
        category_order.get(row['category'], 9),
        -row['endpoint_count'],
        row['name'].lower(),
    ))
    return rows, {
        'unique_count': len(rows),
        'install_count': total_installs,
        'endpoints_with_inventory': endpoints_with_inventory,
        'remote_access_count': sum(1 for row in rows if row['category'] == 'remote_access'),
        'admin_network_count': sum(1 for row in rows if row['category'] == 'admin_network'),
        'security_count': sum(1 for row in rows if row['category'] == 'security'),
        'unknown_count': sum(1 for row in rows if row['category'] == 'unknown'),
    }


def software_row_matches(row, filters):
    q = filters['q'].lower()
    if q:
        haystack = ' '.join([
            row['name'],
            row['publisher'],
            ' '.join(row['versions']),
            ' '.join(item['endpoint'].hostname for item in row['endpoints']),
        ]).lower()
        if q not in haystack:
            return False
    if filters['category'] != 'all' and row['category'] != filters['category']:
        return False
    if filters['risk'] != 'all' and row['risk_level'] != filters['risk']:
        return False
    if filters['publisher'] and row['publisher'] != filters['publisher']:
        return False
    if filters['version'] and filters['version'] not in row['versions']:
        return False
    if filters['endpoint']:
        endpoint_filter = filters['endpoint'].lower()
        if not any(endpoint_filter in item['endpoint'].hostname.lower() for item in row['endpoints']):
            return False
    if filters['sensitive'] == 'yes' and not row['is_sensitive']:
        return False
    if filters['sensitive'] == 'no' and row['is_sensitive']:
        return False
    return True


def format_bytes_gb(value):
    if value in (None, ''):
        return None
    try:
        return round(int(value) / (1024 ** 3), 1)
    except (TypeError, ValueError):
        return None


def format_uptime(seconds):
    if seconds in (None, ''):
        return None
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None

    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    if days:
        return f'{days}d {hours}h'
    if hours:
        return f'{hours}h {minutes}min'
    return f'{minutes}min'


def build_disk_rows(disks):
    rows = []
    for disk in disks or []:
        size = disk.get('size_bytes') or 0
        free = disk.get('free_bytes') or 0
        try:
            size = int(size)
            free = int(free)
        except (TypeError, ValueError):
            size = 0
            free = 0

        used_percent = 0
        if size > 0:
            used_percent = round(((size - free) / size) * 100)

        if used_percent >= 90:
            level = 'critical'
        elif used_percent >= 80:
            level = 'warning'
        else:
            level = 'normal'

        rows.append({
            'name': disk.get('name') or '-',
            'size_gb': format_bytes_gb(size),
            'free_gb': format_bytes_gb(free),
            'used_percent': used_percent,
            'level': level,
        })
    return rows


def get_primary_disk(disks):
    rows = build_disk_rows(disks)
    if not rows:
        return {
            'name': '-',
            'summary': '-',
            'used_percent': None,
            'level': 'unknown',
            'has_data': False,
        }

    primary = next((disk for disk in rows if str(disk['name']).upper().startswith('C:')), rows[0])
    total = f"{primary['size_gb']} GB" if primary['size_gb'] is not None else '-'
    free = f"{primary['free_gb']} GB" if primary['free_gb'] is not None else '-'
    return {
        **primary,
        'summary': f"{primary['name']} · {primary['used_percent']}% usado · {free} livre de {total}",
        'has_data': True,
    }


def defender_state(defender_status):
    status = defender_status or {}
    enabled = status.get('enabled') is True
    realtime = status.get('real_time_protection_enabled') is True

    if enabled and realtime:
        return {
            'label': 'Protegido',
            'class': 'security-good',
            'detail': 'Defender ativo com prote&ccedil;&atilde;o em tempo real.',
        }
    if status:
        return {
            'label': 'Aten&ccedil;&atilde;o',
            'class': 'security-warning',
            'detail': 'Defender ausente ou prote&ccedil;&atilde;o parcial.',
        }
    return {
        'label': 'Indispon&iacute;vel',
        'class': 'security-critical',
        'detail': 'Status de seguran&ccedil;a n&atilde;o recebido.',
    }


def software_name(software):
    return str((software or {}).get('name') or '')


def software_text(software):
    item = software or {}
    return ' '.join([
        str(item.get('name') or ''),
        str(item.get('publisher') or ''),
    ]).lower()


def has_software_match(installed_software, terms):
    for software in installed_software or []:
        text = software_text(software)
        if any(term in text for term in terms):
            return True
    return False


def classify_software(software):
    text = software_text(software)
    if 'microsoft' in text:
        return 'microsoft'
    if any(term in text for term in REMOTE_ACCESS_TERMS):
        return 'remote'
    if any(term in text for term in SECURITY_TERMS):
        return 'security'
    if any(term in text for term in ADMIN_TOOL_TERMS):
        return 'admin'
    return 'other'


def build_software_rows(installed_software):
    rows = []
    for software in installed_software or []:
        classification = classify_software_catalog(software)
        chip_category = classification['category']
        if chip_category == 'remote_access':
            chip_category = 'remote'
        elif chip_category == 'admin_network':
            chip_category = 'admin'
        elif 'microsoft' in software_text(software):
            chip_category = 'microsoft'
        rows.append({
            'name': software.get('name') or '',
            'version': software.get('version') or '',
            'publisher': software.get('publisher') or '',
            'category': chip_category,
            'category_label': classification['category_label'],
            'risk_level': classification['risk_level'],
            'risk_label': classification['risk_label'],
        })
    return rows


def detail_defender_state(defender_status, installed_software):
    state = defender_state(defender_status)
    has_security = has_software_match(installed_software, SECURITY_TERMS)
    has_bitdefender = has_software_match(installed_software, ['bitdefender'])

    status = defender_status or {}
    defender_ok = status.get('enabled') is True and status.get('real_time_protection_enabled') is True

    if defender_ok:
        return {
            **state,
            'key': 'ok',
        }

    if has_bitdefender:
        return {
            'label': 'Protegido por terceiro',
            'class': 'security-good',
            'detail': 'Defender n&atilde;o identificado, mas Bitdefender foi detectado nos softwares instalados.',
            'key': 'ok',
        }

    if has_security:
        return {
            'label': 'Antiv&iacute;rus detectado',
            'class': 'security-warning',
            'detail': 'Defender n&atilde;o identificado, mas outro software de seguran&ccedil;a foi detectado.',
            'key': 'attention',
        }

    return {
        **state,
        'key': 'attention' if status else 'unknown',
    }


def calculate_health(endpoint, primary_disk, defender):
    if endpoint.last_seen_at is None and not primary_disk.get('has_data') and defender.get('key') == 'unknown':
        return {
            'score': None,
            'label': 'Dados insuficientes',
            'class': 'health-unknown',
        }

    score = 0
    if endpoint.status == AgentMachine.STATUS_ONLINE:
        score += 40
    elif endpoint.status == AgentMachine.STATUS_UNKNOWN:
        score += 20
    else:
        score += 10

    disk_level = primary_disk.get('level')
    if disk_level == 'normal':
        score += 30
    elif disk_level == 'warning':
        score += 15
    elif disk_level == 'critical':
        score += 0
    else:
        score += 12

    defender_key = defender.get('key')
    if defender_key == 'ok':
        score += 30
    elif defender_key == 'attention':
        score += 10
    else:
        score += 14

    if score >= 80:
        label = 'Saud&aacute;vel'
        css_class = 'health-good'
    elif score >= 55:
        label = 'Aten&ccedil;&atilde;o'
        css_class = 'health-warning'
    else:
        label = 'Cr&iacute;tico'
        css_class = 'health-critical'

    return {
        'score': score,
        'label': label,
        'class': css_class,
    }


def build_smart_badges(endpoint, primary_disk, defender, installed_software):
    badges = [
        {
            'label': endpoint.get_status_display() or 'Unknown',
            'class': f'status-{endpoint.status}',
        },
    ]

    if primary_disk.get('has_data'):
        disk_level = primary_disk.get('level')
        badges.append({
            'label': f"Disco {disk_level}",
            'class': f'disk-pill-{disk_level}',
        })

    defender_key = defender.get('key', 'unknown')
    defender_label = 'Defender OK' if defender_key == 'ok' else 'Defender Aten&ccedil;&atilde;o' if defender_key == 'attention' else 'Defender desconhecido'
    badges.append({
        'label': defender_label,
        'class': f'defender-{defender_key}',
    })

    if has_software_match(installed_software, REMOTE_ACCESS_TERMS):
        badges.append({
            'label': 'Acesso remoto detectado',
            'class': 'badge-security',
        })

    if has_software_match(installed_software, ADMIN_TOOL_TERMS):
        badges.append({
            'label': 'Admin/Rede detectado',
            'class': 'badge-admin',
        })

    return badges


def defender_filter_state(defender_status):
    status = defender_status or {}
    if not status:
        return 'unknown'
    if status.get('enabled') is True and status.get('real_time_protection_enabled') is True:
        return 'ok'
    return 'attention'


def build_endpoint_row(endpoint):
    snapshot = endpoint.inventory_snapshots.order_by('-received_at').first()
    primary_disk = get_primary_disk(snapshot.disks if snapshot else [])
    defender_key = defender_filter_state(snapshot.defender_status if snapshot else {})
    software_count = len(snapshot.installed_software or []) if snapshot else None
    os_name = endpoint.os_name or (snapshot.os_name if snapshot else '')
    domain = endpoint.domain or (snapshot.domain if snapshot else '')
    logged_user = endpoint.last_logged_user or (snapshot.logged_user if snapshot else '')
    primary_ip = endpoint.last_ip

    if not primary_ip and snapshot and snapshot.ips:
        primary_ip = snapshot.ips[0]

    return {
        'endpoint': endpoint,
        'snapshot': snapshot,
        'hostname': endpoint.hostname or '',
        'domain': domain or '',
        'logged_user': logged_user or '',
        'primary_ip': primary_ip or '',
        'os_name': os_name or '',
        'last_seen_at': endpoint.last_seen_at,
        'primary_disk': primary_disk,
        'defender_key': defender_key,
        'software_count': software_count,
        'has_attention': defender_key == 'attention' or primary_disk['level'] in ('warning', 'critical'),
        'agent_version': endpoint.agent_version,
        'agent_version_state': agent_version_state(
            endpoint.agent_version,
            getattr(settings, 'NIGHTOWL_RECOMMENDED_AGENT_VERSION', ''),
        ),
    }


def row_matches_query(row, query):
    if not query:
        return True

    haystack = ' '.join([
        row['hostname'],
        row['domain'],
        row['logged_user'],
        str(row['primary_ip']),
        row['os_name'],
    ]).lower()
    return query.lower() in haystack


def index(request):
    now = timezone.now()
    status_counts = {
        item['status']: item['count']
        for item in AgentMachine.objects.values('status').annotate(count=Count('id'))
    }
    endpoints = AgentMachine.objects.order_by('hostname', 'domain')
    open_alerts = EndpointAlert.objects.filter(status=EndpointAlert.STATUS_OPEN).filter(
        Q(muted_until__isnull=True) | Q(muted_until__lte=now),
    )
    recent_alerts = open_alerts.select_related('endpoint').order_by('-last_seen_at')[:8]

    context = {
        'total_endpoints': AgentMachine.objects.count(),
        'online_count': status_counts.get(AgentMachine.STATUS_ONLINE, 0),
        'offline_count': status_counts.get(AgentMachine.STATUS_OFFLINE, 0),
        'unknown_count': status_counts.get(AgentMachine.STATUS_UNKNOWN, 0),
        'endpoints': endpoints,
        'open_alerts_count': open_alerts.count(),
        'critical_alerts_count': open_alerts.filter(severity=EndpointAlert.SEVERITY_CRITICAL).count(),
        'recent_alerts': recent_alerts,
    }
    return render(request, 'dashboard/index.html', context)


def alerts_list(request):
    now = timezone.now()
    queryset = EndpointAlert.objects.select_related('endpoint').prefetch_related('events')

    status_filter = request.GET.get('status', EndpointAlert.STATUS_OPEN).strip() or EndpointAlert.STATUS_OPEN
    severity_filter = request.GET.get('severity', 'all').strip() or 'all'
    type_filter = request.GET.get('type', 'all').strip() or 'all'
    period_filter = request.GET.get('period', 'all').strip() or 'all'
    query = request.GET.get('q', '').strip()

    if status_filter != 'all':
        queryset = queryset.filter(status=status_filter)

    if severity_filter != 'all':
        queryset = queryset.filter(severity=severity_filter)

    if type_filter != 'all':
        queryset = queryset.filter(alert_type=type_filter)

    if period_filter == 'today':
        queryset = queryset.filter(last_seen_at__date=timezone.localdate())
    elif period_filter == '24h':
        queryset = queryset.filter(last_seen_at__gte=now - timedelta(hours=24))
    elif period_filter == '7d':
        queryset = queryset.filter(last_seen_at__gte=now - timedelta(days=7))

    if query:
        queryset = queryset.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(endpoint__hostname__icontains=query)
            | Q(endpoint__last_logged_user__icontains=query)
            | Q(endpoint__last_ip__icontains=query)
        )

    summary_queryset = queryset
    unmuted_open_filter = Q(status=EndpointAlert.STATUS_OPEN) & (Q(muted_until__isnull=True) | Q(muted_until__lte=now))
    priority_order = Case(
        When(muted_until__gt=now, then=Value(4)),
        When(status=EndpointAlert.STATUS_OPEN, severity=EndpointAlert.SEVERITY_CRITICAL, then=Value(0)),
        When(status=EndpointAlert.STATUS_OPEN, severity=EndpointAlert.SEVERITY_SECURITY, then=Value(1)),
        When(status=EndpointAlert.STATUS_OPEN, severity=EndpointAlert.SEVERITY_WARNING, then=Value(2)),
        When(status=EndpointAlert.STATUS_OPEN, severity=EndpointAlert.SEVERITY_INFO, then=Value(3)),
        When(status=EndpointAlert.STATUS_ACKNOWLEDGED, then=Value(5)),
        When(status=EndpointAlert.STATUS_RESOLVED, then=Value(6)),
        default=Value(7),
        output_field=IntegerField(),
    )
    queryset = queryset.alias(priority_rank=priority_order).order_by('priority_rank', '-last_seen_at')

    recent_activity = EndpointAlert.objects.select_related('endpoint').order_by('-updated_at')[:8]
    resolved_recent = EndpointAlert.objects.filter(
        status=EndpointAlert.STATUS_RESOLVED,
        resolved_at__isnull=False,
    ).select_related('endpoint').order_by('-resolved_at')[:6]
    toast_resolved_alerts = EndpointAlert.objects.filter(
        status=EndpointAlert.STATUS_RESOLVED,
        resolved_at__gte=now - timedelta(minutes=10),
    ).select_related('endpoint').order_by('-resolved_at')[:4]

    open_alerts_for_top = EndpointAlert.objects.filter(
        status=EndpointAlert.STATUS_OPEN,
    ).filter(
        Q(muted_until__isnull=True) | Q(muted_until__lte=now),
    ).select_related('endpoint').order_by('endpoint__hostname')
    severity_rank = {
        EndpointAlert.SEVERITY_CRITICAL: 0,
        EndpointAlert.SEVERITY_SECURITY: 1,
        EndpointAlert.SEVERITY_WARNING: 2,
        EndpointAlert.SEVERITY_INFO: 3,
    }
    top_endpoint_map = {}
    for alert in open_alerts_for_top:
        current = top_endpoint_map.setdefault(alert.endpoint_id, {
            'endpoint': alert.endpoint,
            'count': 0,
            'max_severity': alert.severity,
        })
        current['count'] += 1
        if severity_rank.get(alert.severity, 9) < severity_rank.get(current['max_severity'], 9):
            current['max_severity'] = alert.severity

    top_endpoints = sorted(
        top_endpoint_map.values(),
        key=lambda item: (-item['count'], severity_rank.get(item['max_severity'], 9), item['endpoint'].hostname or ''),
    )[:6]

    context = {
        'active_nav': 'alerts',
        'alerts': queryset,
        'filters': {
            'status': status_filter,
            'severity': severity_filter,
            'type': type_filter,
            'period': period_filter,
            'q': query,
        },
        'alert_type_options': ALERT_TYPE_OPTIONS,
        'period_options': PERIOD_OPTIONS,
        'mute_durations': [(key, label) for key, (label, _delta) in MUTE_DURATIONS.items()],
        'severity_options': EndpointAlert.SEVERITY_CHOICES,
        'status_options': EndpointAlert.STATUS_CHOICES,
        'open_count': summary_queryset.filter(unmuted_open_filter).count(),
        'critical_count': summary_queryset.filter(unmuted_open_filter, severity=EndpointAlert.SEVERITY_CRITICAL).count(),
        'warning_count': summary_queryset.filter(unmuted_open_filter, severity=EndpointAlert.SEVERITY_WARNING).count(),
        'security_count': summary_queryset.filter(unmuted_open_filter, severity=EndpointAlert.SEVERITY_SECURITY).count(),
        'affected_endpoint_count': summary_queryset.filter(unmuted_open_filter).values('endpoint_id').distinct().count(),
        'recent_activity': recent_activity,
        'resolved_recent': resolved_recent,
        'toast_resolved_alerts': toast_resolved_alerts,
        'top_endpoints': top_endpoints,
        'last_updated_at': now,
    }
    return render(request, 'dashboard/alerts.html', context)


def noc_view(request):
    now = timezone.now()
    status_counts = {
        item['status']: item['count']
        for item in AgentMachine.objects.values('status').annotate(count=Count('id'))
    }
    open_alerts = EndpointAlert.objects.filter(status=EndpointAlert.STATUS_OPEN).filter(
        Q(muted_until__isnull=True) | Q(muted_until__lte=now),
    )
    severity_order = Case(
        When(severity=EndpointAlert.SEVERITY_CRITICAL, then=Value(0)),
        When(severity=EndpointAlert.SEVERITY_WARNING, then=Value(1)),
        When(severity=EndpointAlert.SEVERITY_SECURITY, then=Value(2)),
        When(severity=EndpointAlert.SEVERITY_INFO, then=Value(3)),
        default=Value(4),
        output_field=IntegerField(),
    )

    disk_alerts = open_alerts.filter(alert_type='disk_low').select_related('endpoint').alias(
        severity_rank=severity_order,
    ).order_by('severity_rank', '-last_seen_at')[:8]
    offline_endpoints = AgentMachine.objects.filter(
        status=AgentMachine.STATUS_OFFLINE,
    ).order_by('last_seen_at')[:8]
    relevant_event_types = [
        'alert.created',
        'alert.expired',
        'alert.resolved_auto',
        'endpoint.status_changed',
        'alert.reopened',
        'alert.resolved_manual',
        'alert.acknowledged',
        'security.defender_changed',
        'disk.state_changed',
        'os.build_changed',
    ]
    recent_activity = AuditEvent.objects.filter(
        Q(event_type__in=relevant_event_types)
        | Q(event_type='software.installed', severity=AuditEvent.SEVERITY_SECURITY)
    ).select_related('endpoint', 'alert').order_by('-created_at')[:10]
    last_alert_evaluation = EndpointAlert.objects.order_by('-updated_at').values_list('updated_at', flat=True).first()

    context = {
        'active_nav': 'noc',
        'last_updated_at': now,
        'last_alert_evaluation': last_alert_evaluation,
        'total_endpoints': AgentMachine.objects.count(),
        'online_count': status_counts.get(AgentMachine.STATUS_ONLINE, 0),
        'offline_count': status_counts.get(AgentMachine.STATUS_OFFLINE, 0),
        'unknown_count': status_counts.get(AgentMachine.STATUS_UNKNOWN, 0),
        'open_critical_count': open_alerts.filter(severity=EndpointAlert.SEVERITY_CRITICAL).count(),
        'open_warning_count': open_alerts.filter(severity=EndpointAlert.SEVERITY_WARNING).count(),
        'open_security_count': open_alerts.filter(severity=EndpointAlert.SEVERITY_SECURITY).count(),
        'affected_endpoints_count': open_alerts.values('endpoint_id').distinct().count(),
        'critical_alerts': open_alerts.filter(
            severity=EndpointAlert.SEVERITY_CRITICAL,
        ).select_related('endpoint').order_by('-last_seen_at')[:8],
        'offline_endpoints': offline_endpoints,
        'security_alerts': open_alerts.filter(
            severity=EndpointAlert.SEVERITY_SECURITY,
        ).select_related('endpoint').order_by('-last_seen_at')[:8],
        'disk_alerts': disk_alerts,
        'recent_activity': recent_activity,
    }
    return render(request, 'dashboard/noc.html', context)


def events_list(request):
    now = timezone.now()
    queryset = AuditEvent.objects.select_related('endpoint', 'alert')

    query = request.GET.get('q', '').strip()
    severity_filter = request.GET.get('severity', 'all').strip() or 'all'
    event_type_filter = request.GET.get('event_type', 'all').strip() or 'all'
    actor_type_filter = request.GET.get('actor_type', 'all').strip() or 'all'
    endpoint_filter = request.GET.get('endpoint', '').strip()
    period_filter = request.GET.get('period', '7d').strip() or '7d'

    if period_filter == '24h':
        queryset = queryset.filter(created_at__gte=now - timedelta(hours=24))
    elif period_filter == '7d':
        queryset = queryset.filter(created_at__gte=now - timedelta(days=7))
    elif period_filter == '30d':
        queryset = queryset.filter(created_at__gte=now - timedelta(days=30))

    if severity_filter != 'all':
        queryset = queryset.filter(severity=severity_filter)
    if event_type_filter != 'all':
        queryset = queryset.filter(event_type=event_type_filter)
    if actor_type_filter != 'all':
        queryset = queryset.filter(actor_type=actor_type_filter)
    if endpoint_filter:
        queryset = queryset.filter(endpoint_id=endpoint_filter)
    if query:
        queryset = queryset.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(event_type__icontains=query)
            | Q(endpoint__hostname__icontains=query)
            | Q(actor_name__icontains=query)
        )

    filtered_queryset = queryset
    last_24h = now - timedelta(hours=24)
    context = {
        'active_nav': 'events',
        'events': queryset.order_by('-created_at')[:250],
        'filters': {
            'q': query,
            'severity': severity_filter,
            'event_type': event_type_filter,
            'actor_type': actor_type_filter,
            'endpoint': endpoint_filter,
            'period': period_filter,
        },
        'severity_options': AuditEvent.SEVERITY_CHOICES,
        'actor_type_options': AuditEvent.ACTOR_CHOICES,
        'event_type_options': AuditEvent.objects.values_list('event_type', flat=True).distinct().order_by('event_type'),
        'period_options': EVENT_PERIOD_OPTIONS,
        'endpoint_options': AgentMachine.objects.order_by('hostname').values('id', 'hostname')[:500],
        'events_24h_count': AuditEvent.objects.filter(created_at__gte=last_24h).count(),
        'critical_count': filtered_queryset.filter(severity=AuditEvent.SEVERITY_CRITICAL).count(),
        'security_count': filtered_queryset.filter(severity=AuditEvent.SEVERITY_SECURITY).count(),
        'system_count': filtered_queryset.filter(actor_type__in=[AuditEvent.ACTOR_SYSTEM, AuditEvent.ACTOR_SCHEDULER]).count(),
        'user_count': filtered_queryset.filter(actor_type=AuditEvent.ACTOR_USER).count(),
    }
    return render(request, 'dashboard/events.html', context)


def maintenance_list(request):
    runs = MaintenanceRun.objects.prefetch_related('task_results').order_by('-started_at')[:25]
    latest_run = runs[0] if runs else None
    recent_failures = MaintenanceRun.objects.filter(
        status__in=[MaintenanceRun.STATUS_PARTIAL, MaintenanceRun.STATUS_FAILED],
    ).order_by('-started_at')[:6]
    status_counts = {
        item['status']: item['count']
        for item in MaintenanceRun.objects.values('status').annotate(count=Count('id'))
    }

    context = {
        'active_nav': 'maintenance',
        'latest_run': latest_run,
        'runs': runs,
        'recent_failures': recent_failures,
        'success_count': status_counts.get(MaintenanceRun.STATUS_SUCCESS, 0),
        'partial_count': status_counts.get(MaintenanceRun.STATUS_PARTIAL, 0),
        'failed_count': status_counts.get(MaintenanceRun.STATUS_FAILED, 0),
        'running_count': status_counts.get(MaintenanceRun.STATUS_RUNNING, 0),
    }
    return render(request, 'dashboard/maintenance.html', context)


@require_POST
def alert_acknowledge(request, pk):
    alert = get_object_or_404(EndpointAlert, pk=pk)
    if alert.status in (EndpointAlert.STATUS_OPEN, EndpointAlert.STATUS_ACKNOWLEDGED):
        was_open = alert.status == EndpointAlert.STATUS_OPEN
        alert.status = EndpointAlert.STATUS_ACKNOWLEDGED
        if alert.acknowledged_at is None:
            alert.acknowledged_at = timezone.now()
        alert.save(update_fields=['status', 'acknowledged_at', 'updated_at'])
        if was_open:
            create_alert_event(alert, AlertEvent.TYPE_ACKNOWLEDGED, 'Alerta reconhecido.', request)
            audit_alert_action(request, alert, 'alert.acknowledged', 'Alerta reconhecido', alert.title)
        messages.info(request, 'Alerta reconhecido.')
    else:
        messages.warning(request, 'Alerta resolvido nao foi reaberto.')
    return redirect_back(request)


@require_POST
def alert_resolve(request, pk):
    alert = get_object_or_404(EndpointAlert, pk=pk)
    if alert.status != EndpointAlert.STATUS_RESOLVED:
        alert.status = EndpointAlert.STATUS_RESOLVED
        alert.resolved_at = timezone.now()
        alert.resolution_type = EndpointAlert.RESOLUTION_MANUAL
        alert.save(update_fields=['status', 'resolved_at', 'resolution_type', 'updated_at'])
        create_alert_event(
            alert,
            AlertEvent.TYPE_RESOLVED_MANUAL,
            'Alerta resolvido manualmente. Se a condicao ainda existir, ele podera voltar na proxima avaliacao.',
            request,
        )
        audit_alert_action(
            request,
            alert,
            'alert.resolved_manual',
            'Alerta resolvido manualmente',
            'Se a condicao ainda existir, o alerta podera voltar na proxima avaliacao.',
            severity=AuditEvent.SEVERITY_SUCCESS,
            metadata={'resolution_type': EndpointAlert.RESOLUTION_MANUAL},
        )
        messages.success(request, 'Alerta resolvido manualmente.')
    else:
        messages.info(request, 'Alerta ja estava resolvido.')
    return redirect_back(request)


@require_POST
def alert_mute(request, pk):
    alert = get_object_or_404(EndpointAlert, pk=pk)
    duration = request.POST.get('duration', '').strip()
    reason = request.POST.get('reason', '').strip()
    label, delta = MUTE_DURATIONS.get(duration, (None, None))
    if not delta:
        messages.error(request, 'Duracao de silenciamento invalida.')
        return redirect_back(request)

    now = timezone.now()
    alert.muted_at = now
    alert.muted_until = now + delta
    alert.muted_reason = reason
    alert.save(update_fields=['muted_at', 'muted_until', 'muted_reason', 'updated_at'])
    create_alert_event(
        alert,
        AlertEvent.TYPE_MUTED,
        f'Alerta silenciado por {label}.',
        request,
        {'duration': duration, 'muted_until': alert.muted_until.isoformat(), 'reason': reason},
    )
    audit_alert_action(
        request,
        alert,
        'alert.muted',
        'Alerta silenciado',
        f'Alerta silenciado por {label}.',
        severity=AuditEvent.SEVERITY_INFO,
        metadata={'duration': duration, 'muted_until': alert.muted_until.isoformat(), 'muted_reason': reason},
    )
    messages.info(request, f'Alerta silenciado por {label}.')
    return redirect_back(request)


@require_POST
def alert_comment(request, pk):
    alert = get_object_or_404(EndpointAlert, pk=pk)
    message = request.POST.get('message', '').strip()
    if not message:
        messages.warning(request, 'Informe uma observacao antes de salvar.')
        return redirect_back(request)

    create_alert_event(alert, AlertEvent.TYPE_COMMENT, message, request)
    audit_alert_action(
        request,
        alert,
        'alert.comment_added',
        'Observacao adicionada',
        message,
        severity=AuditEvent.SEVERITY_INFO,
    )
    messages.info(request, 'Observacao adicionada.')
    return redirect_back(request)


def endpoint_list(request):
    rows = [build_endpoint_row(endpoint) for endpoint in AgentMachine.objects.order_by('hostname', 'domain')]

    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    os_filter = request.GET.get('os', '').strip()
    domain_filter = request.GET.get('domain', '').strip()
    defender_filter = request.GET.get('defender', '').strip()
    disk_filter = request.GET.get('disk', '').strip()

    filtered_rows = []
    for row in rows:
        if not row_matches_query(row, q):
            continue
        if status and row['endpoint'].status != status:
            continue
        if os_filter and row['os_name'] != os_filter:
            continue
        if domain_filter and row['domain'] != domain_filter:
            continue
        if defender_filter and row['defender_key'] != defender_filter:
            continue
        if disk_filter and row['primary_disk']['level'] != disk_filter:
            continue
        filtered_rows.append(row)

    status_counts = {
        AgentMachine.STATUS_ONLINE: 0,
        AgentMachine.STATUS_OFFLINE: 0,
        AgentMachine.STATUS_UNKNOWN: 0,
    }
    for row in filtered_rows:
        status_counts[row['endpoint'].status] = status_counts.get(row['endpoint'].status, 0) + 1

    context = {
        'active_nav': 'endpoints',
        'rows': filtered_rows,
        'total_endpoints': len(filtered_rows),
        'online_count': status_counts.get(AgentMachine.STATUS_ONLINE, 0),
        'offline_count': status_counts.get(AgentMachine.STATUS_OFFLINE, 0),
        'unknown_count': status_counts.get(AgentMachine.STATUS_UNKNOWN, 0),
        'attention_count': sum(1 for row in filtered_rows if row['has_attention']),
        'filters': {
            'q': q,
            'status': status,
            'os': os_filter,
            'domain': domain_filter,
            'defender': defender_filter,
            'disk': disk_filter,
        },
        'os_options': sorted({row['os_name'] for row in rows if row['os_name']}),
        'domain_options': sorted({row['domain'] for row in rows if row['domain']}),
        'status_options': AgentMachine.STATUS_CHOICES,
    }
    return render(request, 'dashboard/endpoint_list.html', context)


def endpoint_detail(request, pk):
    endpoint = get_object_or_404(AgentMachine, pk=pk)
    snapshot = endpoint.inventory_snapshots.order_by('-received_at').first()

    disks = build_disk_rows(snapshot.disks if snapshot else [])
    installed_software = snapshot.installed_software if snapshot else []
    software_rows = build_software_rows(installed_software)
    defender = detail_defender_state(snapshot.defender_status if snapshot else {}, installed_software)
    primary_disk = get_primary_disk(snapshot.disks if snapshot else [])
    health = calculate_health(endpoint, primary_disk, defender)
    smart_badges = build_smart_badges(endpoint, primary_disk, defender, installed_software)
    primary_ip = endpoint.last_ip

    if not primary_ip and snapshot and snapshot.ips:
        primary_ip = snapshot.ips[0]

    context = {
        'active_nav': 'endpoints',
        'endpoint': endpoint,
        'snapshot': snapshot,
        'memory_total_gb': format_bytes_gb(snapshot.memory_total_bytes) if snapshot else None,
        'uptime_display': format_uptime(snapshot.uptime_seconds) if snapshot else None,
        'disks': disks,
        'installed_software': software_rows,
        'defender': defender,
        'primary_disk': primary_disk,
        'health': health,
        'smart_badges': smart_badges,
        'primary_ip': primary_ip,
        'recommended_agent_version': getattr(settings, 'NIGHTOWL_RECOMMENDED_AGENT_VERSION', ''),
        'agent_version_state': agent_version_state(
            endpoint.agent_version,
            getattr(settings, 'NIGHTOWL_RECOMMENDED_AGENT_VERSION', ''),
        ),
        'endpoint_alerts': endpoint.alerts.filter(
            status__in=[EndpointAlert.STATUS_OPEN, EndpointAlert.STATUS_ACKNOWLEDGED],
        ).order_by('-last_seen_at')[:8],
        'audit_events': endpoint.audit_events.select_related('alert').order_by('-created_at')[:8],
        'related_tickets': endpoint.tickets.exclude(
            status__in=['closed', 'canceled'],
        ).order_by('-updated_at')[:5],
    }
    return render(request, 'dashboard/endpoint_detail.html', context)


def agent_install(request):
    created_token = None
    created_command = ''
    now = timezone.now()

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        allowed_domain = request.POST.get('allowed_domain', '').strip().lower()
        notes = request.POST.get('notes', '').strip()
        try:
            expires_hours = int(request.POST.get('expires_hours') or 168)
        except ValueError:
            expires_hours = 168
        max_uses_raw = request.POST.get('max_uses', '').strip()
        max_uses = None
        if max_uses_raw:
            try:
                max_uses = int(max_uses_raw)
            except ValueError:
                max_uses = None

        if not name:
            messages.warning(request, 'Informe um nome para o token.')
        elif expires_hours <= 0:
            messages.warning(request, 'A validade deve ser maior que zero.')
        elif max_uses is not None and max_uses <= 0:
            messages.warning(request, 'O limite de usos deve ser maior que zero.')
        else:
            enrollment, created_token = AgentEnrollmentToken.create_with_token(
                name=name,
                expires_at=now + timedelta(hours=expires_hours),
                max_uses=max_uses,
                allowed_domain=allowed_domain,
                notes=notes,
            )
            created_command = build_agent_install_command(created_token)
            create_audit_event(
                event_type='agent.enrollment_token_created',
                title='Enrollment token criado',
                description=f'Enrollment token criado: {enrollment.name}.',
                severity=AuditEvent.SEVERITY_INFO,
                metadata={
                    'enrollment_token_id': str(enrollment.id),
                    'prefix': enrollment.prefix,
                    'allowed_domain': enrollment.allowed_domain,
                    'max_uses': enrollment.max_uses,
                },
                request=request,
            )
            messages.success(request, 'Enrollment token criado. Copie o token agora; ele nao sera exibido novamente.')

    tokens = AgentEnrollmentToken.objects.order_by('-created_at')
    token_rows = []
    for token in tokens:
        token_rows.append({
            'token': token,
            'state': enrollment_token_state(token),
            'logs': token.logs.select_related('endpoint').order_by('-created_at')[:5],
            'command_without_token': build_agent_install_command('TOKEN_AQUI'),
        })

    last_24h = now - timedelta(hours=24)
    context = {
        'active_nav': 'agents',
        'rows': token_rows,
        'created_token': created_token,
        'created_command': created_command,
        'heartbeat_url': getattr(settings, 'NIGHTOWL_AGENT_HEARTBEAT_URL', ''),
        'source_path': getattr(settings, 'NIGHTOWL_AGENT_SOURCE_PATH', ''),
        'active_count': sum(1 for row in token_rows if row['state']['key'] == 'online'),
        'expired_count': sum(1 for row in token_rows if row['state']['key'] == 'unknown'),
        'uses_24h_count': AgentEnrollmentLog.objects.filter(created_at__gte=last_24h).count(),
        'success_24h_count': AgentEnrollmentLog.objects.filter(
            created_at__gte=last_24h,
            status=AgentEnrollmentLog.STATUS_SUCCESS,
        ).count(),
        'default_allowed_domain': 'control.local',
    }
    return render(request, 'dashboard/agent_install.html', context)


@require_POST
def agent_enrollment_revoke(request, pk):
    enrollment = get_object_or_404(AgentEnrollmentToken, pk=pk)
    if enrollment.is_active:
        enrollment.is_active = False
        enrollment.save(update_fields=['is_active', 'updated_at'])
        create_audit_event(
            event_type='agent.enrollment_token_revoked',
            title='Enrollment token revogado',
            description=f'Enrollment token revogado: {enrollment.name}.',
            severity=AuditEvent.SEVERITY_WARNING,
            metadata={
                'enrollment_token_id': str(enrollment.id),
                'prefix': enrollment.prefix,
                'allowed_domain': enrollment.allowed_domain,
            },
            request=request,
        )
        messages.warning(request, 'Enrollment token revogado.')
    else:
        messages.info(request, 'Este enrollment token ja estava inativo.')
    return redirect('agent-install')


def software_inventory(request):
    rows, summary = build_software_inventory()
    filters = {
        'q': request.GET.get('q', '').strip(),
        'category': request.GET.get('category', 'all').strip() or 'all',
        'risk': request.GET.get('risk', 'all').strip() or 'all',
        'publisher': request.GET.get('publisher', '').strip(),
        'version': request.GET.get('version', '').strip(),
        'endpoint': request.GET.get('endpoint', '').strip(),
        'sensitive': request.GET.get('sensitive', 'all').strip() or 'all',
    }
    filtered_rows = [row for row in rows if software_row_matches(row, filters)]

    context = {
        'active_nav': 'software',
        'rows': filtered_rows,
        'summary': {
            **summary,
            'filtered_count': len(filtered_rows),
        },
        'filters': filters,
        'category_options': SOFTWARE_CATEGORY_OPTIONS,
        'risk_options': SOFTWARE_RISK_OPTIONS,
        'publisher_options': sorted({row['publisher'] for row in rows if row['publisher']}),
        'version_options': sorted({version for row in rows for version in row['versions']}),
        'updated_at': timezone.now(),
    }
    return render(request, 'dashboard/software_inventory.html', context)


def policy_status(policy):
    if not policy.is_active:
        return 'inactive', 'Inativa'
    if policy.monitor_only:
        return 'monitor_only', 'Monitoramento'
    return 'active', 'Ativa'


def policy_scope_label(policy):
    if policy.scope_type == SoftwarePolicy.SCOPE_ALL:
        return 'Todos os endpoints'
    if policy.scope_type == SoftwarePolicy.SCOPE_SPECIFIC_ENDPOINTS:
        count = policy.target_endpoints.count()
        return f'{count} endpoint{"s" if count != 1 else ""} especifico{"s" if count != 1 else ""}'
    return f'{policy.get_scope_type_display()}: {policy.scope_value}'


def policy_behavior(policy):
    behavior = []
    if policy.create_alert:
        behavior.append('Gerar alerta')
    if policy.show_in_noc:
        behavior.append('Mostrar no NOC')
    if policy.create_audit_event:
        behavior.append('Criar auditoria')
    if policy.monitor_only:
        behavior.append('Somente monitoramento')
    return behavior or ['Nenhuma acao operacional configurada']


def serialize_policy(policy):
    status_key, status_label = policy_status(policy)
    active_exceptions = [item for item in policy.exceptions.all() if item.is_active]
    open_violations = [item for item in policy.violations.all() if item.status == SoftwarePolicyViolation.STATUS_OPEN]
    target_endpoints = [
        {
            'id': str(item.endpoint_id),
            'hostname': item.endpoint.hostname,
            'domain': item.endpoint.domain or 'sem dominio',
            'status': item.endpoint.status,
            'url': f'/endpoints/{item.endpoint_id}/',
            'label': f'{item.endpoint.hostname} Â· {item.endpoint.domain or "sem dominio"} Â· {item.endpoint.status}',
        }
        for item in policy.target_endpoints.all()
    ]
    return {
        'id': str(policy.id),
        'name': policy.name,
        'description': policy.description,
        'type': policy.policy_type,
        'type_label': policy.get_policy_type_display(),
        'software': policy.software_name,
        'match_type': policy.get_match_type_display(),
        'match_type_value': policy.match_type,
        'publisher': policy.publisher,
        'version': policy.version_rule or 'Qualquer',
        'scope': policy_scope_label(policy),
        'scope_type': policy.scope_type,
        'scope_value': policy.scope_value,
        'target_endpoints': target_endpoints,
        'target_endpoint_ids': [item['id'] for item in target_endpoints],
        'severity': policy.severity,
        'severity_label': policy.get_severity_display(),
        'status': status_key,
        'status_label': status_label,
        'is_active': policy.is_active,
        'monitor_only': policy.monitor_only,
        'create_alert': policy.create_alert,
        'show_in_noc': policy.show_in_noc,
        'create_audit_event': policy.create_audit_event,
        'violations_open': len(open_violations),
        'exceptions_active': len(active_exceptions),
        'updated_at': policy.updated_at.strftime('%d/%m %H:%M'),
        'behavior': policy_behavior(policy),
    }


def serialize_policy_violation(violation):
    return {
        'id': str(violation.id),
        'policy_id': str(violation.policy_id),
        'endpoint_id': str(violation.endpoint_id),
        'endpoint': violation.endpoint.hostname,
        'endpoint_url': f'/endpoints/{violation.endpoint_id}/',
        'software_name': violation.software_name or violation.policy.software_name,
        'software_version': violation.software_version or '—',
        'publisher': violation.publisher or '—',
        'severity': violation.severity,
        'severity_label': violation.get_severity_display(),
        'status': violation.status,
        'status_label': violation.get_status_display(),
        'first_seen_at': violation.first_seen_at.strftime('%d/%m/%Y %H:%M') if violation.first_seen_at else '—',
        'last_seen_at': violation.last_seen_at.strftime('%d/%m/%Y %H:%M') if violation.last_seen_at else '—',
        'alert_id': str(violation.alert_id) if violation.alert_id else '',
        'alert_label': 'Alerta vinculado' if violation.alert_id else 'Sem alerta',
        'resolution_reason': violation.resolution_reason or '',
    }


def serialize_policy_exception(exception):
    return {
        'id': str(exception.id),
        'policy_id': str(exception.policy_id),
        'policy': exception.policy.name,
        'endpoint_id': str(exception.endpoint_id),
        'endpoint': exception.endpoint.hostname,
        'endpoint_label': f'{exception.endpoint.hostname} · {exception.endpoint.domain or "sem dominio"} · {exception.endpoint.status}',
        'reason': exception.reason,
        'exception_type': exception.exception_type,
        'exception_type_label': exception.get_exception_type_display(),
        'expires_at': exception.expires_at.strftime('%d/%m/%Y %H:%M') if exception.expires_at else 'Permanente',
        'expires_value': exception.expires_at.strftime('%Y-%m-%d') if exception.expires_at else '',
        'status': exception.status_key,
        'status_label': exception.status_label,
        'created_by': 'Night Owl',
        'created_at': exception.created_at.strftime('%d/%m %H:%M'),
    }


def policy_audit_logs(policy):
    events = AuditEvent.objects.filter(
        event_type__startswith='software_policy',
        metadata__policy_id=str(policy.id),
    ).order_by('-created_at')[:20]
    return [
        {
            'time': event.created_at.strftime('%d/%m %H:%M'),
            'title': event.title,
            'description': event.description,
            'severity': event.severity,
            'event_type': event.event_type,
        }
        for event in events
    ]


def software_policies(request):
    policy_queryset = SoftwarePolicy.objects.prefetch_related(
        'exceptions__endpoint',
        'violations__endpoint',
        'target_endpoints__endpoint',
    ).order_by('-updated_at')
    policies = [serialize_policy(policy) for policy in policy_queryset]
    violations_queryset = SoftwarePolicyViolation.objects.select_related('policy', 'endpoint', 'alert').order_by('-last_seen_at')
    violations = [serialize_policy_violation(violation) for violation in violations_queryset]
    exceptions_queryset = SoftwarePolicyException.objects.select_related('policy', 'endpoint').order_by('-created_at')
    exceptions = [serialize_policy_exception(exception) for exception in exceptions_queryset]
    logs_by_policy = {
        str(policy.id): policy_audit_logs(policy)
        for policy in policy_queryset
    }
    endpoint_options = AgentMachine.objects.order_by('hostname', 'domain')
    endpoint_options_data = [
        {
            'id': str(endpoint.id),
            'label': f'{endpoint.hostname} · {endpoint.domain or "sem dominio"} · {endpoint.status}',
        }
        for endpoint in endpoint_options
    ]
    summary = {
        'active': SoftwarePolicy.objects.filter(is_active=True).count(),
        'inactive': SoftwarePolicy.objects.filter(is_active=False).count(),
        'monitoring': SoftwarePolicy.objects.filter(monitor_only=True).count(),
        'exceptions': SoftwarePolicyException.objects.filter(is_active=True).count(),
        'expired_exceptions': sum(
            1 for exception in exceptions_queryset
            if exception.status_key == 'expired'
        ),
        'total': SoftwarePolicy.objects.count(),
    }
    context = {
        'active_nav': 'software_policies',
        'policies': policies,
        'violations': violations,
        'exceptions': exceptions,
        'logs_by_policy': logs_by_policy,
        'summary': summary,
        'endpoint_options': endpoint_options,
        'endpoint_options_data': endpoint_options_data,
        'policy_type_options': SoftwarePolicy.TYPE_CHOICES,
        'match_type_options': SoftwarePolicy.MATCH_CHOICES,
        'scope_type_options': SoftwarePolicy.SCOPE_CHOICES,
        'severity_options': SoftwarePolicy.SEVERITY_CHOICES,
    }
    return render(request, 'dashboard/software_policies.html', context)


def checked(request, name):
    return request.POST.get(name) in {'on', 'true', '1', 'yes'}


def parse_exception_expires(value):
    value = (value or '').strip()
    if not value:
        return None
    parsed_datetime = parse_datetime(value)
    if parsed_datetime:
        if timezone.is_naive(parsed_datetime):
            return timezone.make_aware(parsed_datetime)
        return parsed_datetime
    parsed_date = parse_date(value)
    if parsed_date:
        return timezone.make_aware(datetime.combine(parsed_date, time(hour=23, minute=59, second=59)))
    return None


def apply_policy_post(policy, request):
    policy.name = request.POST.get('name', '').strip()
    policy.description = request.POST.get('description', '').strip()
    policy.policy_type = request.POST.get('policy_type', '').strip()
    policy.software_name = request.POST.get('software_name', '').strip()
    policy.match_type = request.POST.get('match_type', '').strip()
    policy.publisher = request.POST.get('publisher', '').strip()
    policy.version_rule = request.POST.get('version_rule', '').strip()
    policy.scope_type = request.POST.get('scope_type', '').strip()
    policy.scope_value = request.POST.get('scope_value', '').strip()
    policy.severity = request.POST.get('severity', '').strip()
    policy.is_active = checked(request, 'is_active')
    policy.monitor_only = checked(request, 'monitor_only')
    policy.create_alert = checked(request, 'create_alert')
    policy.show_in_noc = checked(request, 'show_in_noc')
    policy.create_audit_event = checked(request, 'create_audit_event')
    if policy.policy_type == SoftwarePolicy.TYPE_OBSERVED:
        policy.monitor_only = True
    policy.full_clean()
    policy.save()
    return policy


def selected_target_endpoint_ids(request):
    endpoint_ids = []
    seen = set()
    for endpoint_id in request.POST.getlist('target_endpoint'):
        endpoint_id = (endpoint_id or '').strip()
        if endpoint_id and endpoint_id not in seen:
            endpoint_ids.append(endpoint_id)
            seen.add(endpoint_id)
    return endpoint_ids


def sync_policy_target_endpoints(request, policy):
    selected_ids = selected_target_endpoint_ids(request)
    if policy.scope_type != SoftwarePolicy.SCOPE_SPECIFIC_ENDPOINTS:
        selected_ids = []
    elif not selected_ids:
        raise ValidationError({'target_endpoints': 'Selecione ao menos um endpoint para o escopo especifico.'})

    endpoints = list(AgentMachine.objects.filter(id__in=selected_ids))
    found_ids = {str(endpoint.id) for endpoint in endpoints}
    missing_ids = [endpoint_id for endpoint_id in selected_ids if endpoint_id not in found_ids]
    if missing_ids:
        raise ValidationError({'target_endpoints': 'Um ou mais endpoints selecionados nao foram encontrados.'})

    previous_ids = set(policy.target_endpoints.values_list('endpoint_id', flat=True))
    desired_ids = {endpoint.id for endpoint in endpoints}
    removed_ids = previous_ids - desired_ids
    added_endpoints = [endpoint for endpoint in endpoints if endpoint.id not in previous_ids]

    if removed_ids:
        SoftwarePolicyTargetEndpoint.objects.filter(policy=policy, endpoint_id__in=removed_ids).delete()
    created = 0
    for endpoint in added_endpoints:
        SoftwarePolicyTargetEndpoint.objects.get_or_create(policy=policy, endpoint=endpoint)
        created += 1

    if added_endpoints or removed_ids:
        create_audit_event(
            event_type='software_policy.target_endpoints_updated',
            title='Endpoints-alvo atualizados',
            description=f'Endpoints-alvo atualizados na politica {policy.name}.',
            severity=AuditEvent.SEVERITY_INFO,
            metadata={
                'policy_id': str(policy.id),
                'policy_name': policy.name,
                'added_count': len(added_endpoints),
                'removed_count': len(removed_ids),
                'target_count': len(desired_ids),
                'added_endpoint_ids': [str(endpoint.id) for endpoint in added_endpoints],
                'removed_endpoint_ids': [str(endpoint_id) for endpoint_id in removed_ids],
            },
            request=request,
        )

    for endpoint in added_endpoints:
        create_audit_event(
            event_type='software_policy.target_endpoint_added',
            title='Endpoint-alvo adicionado',
            description=f'{endpoint.hostname} passou a fazer parte do escopo da politica {policy.name}.',
            severity=AuditEvent.SEVERITY_INFO,
            endpoint=endpoint,
            metadata={
                'policy_id': str(policy.id),
                'policy_name': policy.name,
                'endpoint_id': str(endpoint.id),
                'endpoint_hostname': endpoint.hostname,
            },
            request=request,
        )

    for endpoint_id in removed_ids:
        create_audit_event(
            event_type='software_policy.target_endpoint_removed',
            title='Endpoint-alvo removido',
            description=f'Endpoint removido do escopo da politica {policy.name}.',
            severity=AuditEvent.SEVERITY_INFO,
            metadata={
                'policy_id': str(policy.id),
                'policy_name': policy.name,
                'endpoint_id': str(endpoint_id),
            },
            request=request,
        )

    return {'added': created, 'removed': len(removed_ids), 'total': len(desired_ids)}


def audit_software_policy(request, event_type, title, policy, severity=AuditEvent.SEVERITY_INFO, description='', metadata=None):
    create_audit_event(
        event_type=event_type,
        title=title,
        description=description,
        severity=severity,
        metadata={
            'policy_id': str(policy.id),
            'policy_name': policy.name,
            **(metadata or {}),
        },
        request=request,
    )


def create_policy_exceptions_from_post(request, policy):
    created = 0
    endpoint_ids = request.POST.getlist('exception_endpoint')
    reasons = request.POST.getlist('exception_reason')
    types = request.POST.getlist('exception_type')
    expires_values = request.POST.getlist('exception_expires_at')
    for index, endpoint_id in enumerate(endpoint_ids):
        endpoint_id = (endpoint_id or '').strip()
        if not endpoint_id:
            continue
        endpoint = AgentMachine.objects.filter(id=endpoint_id).first()
        if endpoint is None:
            messages.warning(request, 'Endpoint de excecao nao encontrado; linha ignorada.')
            continue
        exception_type = (types[index] if index < len(types) else SoftwarePolicyException.TYPE_TEMPORARY).strip()
        reason = (reasons[index] if index < len(reasons) else '').strip()
        expires_at = parse_exception_expires(expires_values[index] if index < len(expires_values) else '')
        exception = SoftwarePolicyException(
            policy=policy,
            endpoint=endpoint,
            reason=reason,
            exception_type=exception_type,
            expires_at=expires_at,
        )
        try:
            exception.full_clean()
            exception.save()
        except ValidationError as error:
            messages.warning(request, f'Excecao para {endpoint.hostname} ignorada: {error.messages[0]}')
            continue
        except IntegrityError:
            messages.warning(request, f'Ja existe excecao ativa para {endpoint.hostname} nesta politica.')
            continue
        created += 1
        create_audit_event(
            event_type='software_policy_exception.created',
            title='Excecao de politica criada',
            description=f'Excecao criada para {endpoint.hostname} na politica {policy.name}.',
            severity=AuditEvent.SEVERITY_INFO,
            endpoint=endpoint,
            metadata={
                'policy_id': str(policy.id),
                'policy_name': policy.name,
                'exception_id': str(exception.id),
                'endpoint_id': str(endpoint.id),
            },
            request=request,
        )
    return created


@require_POST
def software_policy_create(request):
    try:
        with transaction.atomic():
            policy = apply_policy_post(SoftwarePolicy(), request)
            target_result = sync_policy_target_endpoints(request, policy)
            created_exceptions = create_policy_exceptions_from_post(request, policy)
            audit_software_policy(
                request,
                'software_policy.created',
                'Politica de software criada',
                policy,
                description=f'Politica criada: {policy.name}.',
                metadata={'exceptions_created': created_exceptions, 'target_endpoints': target_result},
            )
        messages.success(request, 'Politica criada com sucesso.')
    except ValidationError as error:
        messages.error(request, 'Erro ao criar politica: ' + '; '.join(error.messages))
    return redirect('software-policies')


@require_POST
def software_policy_update(request, pk):
    policy = get_object_or_404(SoftwarePolicy, pk=pk)
    try:
        with transaction.atomic():
            apply_policy_post(policy, request)
            target_result = sync_policy_target_endpoints(request, policy)
            audit_software_policy(
                request,
                'software_policy.updated',
                'Politica de software atualizada',
                policy,
                description=f'Politica atualizada: {policy.name}.',
                metadata={'target_endpoints': target_result},
            )
        messages.success(request, 'Politica atualizada com sucesso.')
    except ValidationError as error:
        messages.error(request, 'Erro ao atualizar politica: ' + '; '.join(error.messages))
    return redirect('software-policies')


@require_POST
def software_policy_toggle_active(request, pk):
    policy = get_object_or_404(SoftwarePolicy, pk=pk)
    policy.is_active = not policy.is_active
    policy.save(update_fields=['is_active', 'updated_at'])
    if policy.is_active:
        event_type = 'software_policy.activated'
        title = 'Politica de software reativada'
        severity = AuditEvent.SEVERITY_INFO
        message = 'Politica reativada.'
    else:
        event_type = 'software_policy.deactivated'
        title = 'Politica de software desativada'
        severity = AuditEvent.SEVERITY_WARNING
        message = 'Politica desativada.'
    audit_software_policy(request, event_type, title, policy, severity=severity, description=title)

    is_async = (
        request.headers.get('x-requested-with') == 'XMLHttpRequest'
        or 'application/json' in request.headers.get('Accept', '')
    )
    if is_async:
        status, status_label = policy_status(policy)
        return JsonResponse({
            'status': 'ok',
            'policy_id': str(policy.id),
            'is_active': policy.is_active,
            'policy_status': status,
            'policy_status_label': status_label,
            'button_label': 'Desativar' if policy.is_active else 'Reativar',
            'message': 'Política reativada' if policy.is_active else 'Política desativada',
        })

    if policy.is_active:
        messages.success(request, message)
    else:
        messages.warning(request, message)
    return redirect('software-policies')


@require_POST
def software_policy_delete(request, pk):
    policy = get_object_or_404(SoftwarePolicy, pk=pk)
    policy_name = policy.name
    policy_id = str(policy.id)
    policy.delete()
    create_audit_event(
        event_type='software_policy.deleted',
        title='Politica de software excluida',
        description=f'Politica excluida: {policy_name}.',
        severity=AuditEvent.SEVERITY_WARNING,
        metadata={'policy_id': policy_id, 'policy_name': policy_name},
        request=request,
    )
    messages.warning(request, 'Politica excluida.')
    return redirect('software-policies')


@require_POST
def software_policy_exception_add(request, pk):
    policy = get_object_or_404(SoftwarePolicy, pk=pk)
    created = create_policy_exceptions_from_post(request, policy)
    if created:
        messages.success(request, f'{created} excecao(oes) adicionada(s).')
    else:
        messages.warning(request, 'Nenhuma excecao foi adicionada.')
    return redirect('software-policies')


@require_POST
def software_policy_exception_remove(request, pk):
    exception = get_object_or_404(SoftwarePolicyException.objects.select_related('policy', 'endpoint'), pk=pk)
    if exception.is_active:
        exception.is_active = False
        exception.save(update_fields=['is_active', 'updated_at'])
        create_audit_event(
            event_type='software_policy_exception.removed',
            title='Excecao de politica removida',
            description=f'Excecao removida para {exception.endpoint.hostname} na politica {exception.policy.name}.',
            severity=AuditEvent.SEVERITY_WARNING,
            endpoint=exception.endpoint,
            metadata={
                'policy_id': str(exception.policy_id),
                'policy_name': exception.policy.name,
                'exception_id': str(exception.id),
                'endpoint_id': str(exception.endpoint_id),
            },
            request=request,
        )
        messages.warning(request, 'Excecao removida.')
    else:
        messages.info(request, 'Esta excecao ja estava inativa.')
    return redirect('software-policies')


def software_detail(request):
    name = request.GET.get('name', '').strip()
    publisher = request.GET.get('publisher', '').strip()
    rows, _summary = build_software_inventory()
    selected = None
    name_key = normalize_key(name)
    publisher_key = normalize_key(publisher)
    for row in rows:
        if normalize_key(row['name']) == name_key and (not publisher or normalize_key(row['publisher']) == publisher_key):
            selected = row
            break

    if selected is None:
        return render(request, 'dashboard/software_detail.html', {
            'active_nav': 'software',
            'software': None,
            'endpoint_rows': [],
            'filters': {'q': '', 'version': '', 'status': ''},
        })

    filters = {
        'q': request.GET.get('q', '').strip(),
        'version': request.GET.get('version', '').strip(),
        'status': request.GET.get('status', '').strip(),
    }
    endpoint_rows = sorted(selected['endpoints'], key=lambda item: item['endpoint'].hostname)
    if filters['q']:
        q = filters['q'].lower()
        endpoint_rows = [
            item for item in endpoint_rows
            if q in item['endpoint'].hostname.lower()
            or q in item['endpoint'].domain.lower()
            or q in (item['endpoint'].last_logged_user or '').lower()
        ]
    if filters['version']:
        endpoint_rows = [item for item in endpoint_rows if item['version'] == filters['version']]
    if filters['status']:
        endpoint_rows = [item for item in endpoint_rows if item['endpoint'].status == filters['status']]

    context = {
        'active_nav': 'software',
        'software': selected,
        'endpoint_rows': endpoint_rows,
        'filters': filters,
        'status_options': AgentMachine.STATUS_CHOICES,
    }
    return render(request, 'dashboard/software_detail.html', context)


def software_export(request):
    rows, _summary = build_software_inventory()
    filters = {
        'q': request.GET.get('q', '').strip(),
        'category': request.GET.get('category', 'all').strip() or 'all',
        'risk': request.GET.get('risk', 'all').strip() or 'all',
        'publisher': request.GET.get('publisher', '').strip(),
        'version': request.GET.get('version', '').strip(),
        'endpoint': request.GET.get('endpoint', '').strip(),
        'sensitive': request.GET.get('sensitive', 'all').strip() or 'all',
    }
    filtered_rows = [row for row in rows if software_row_matches(row, filters)]
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="nightowl-softwares.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(['name', 'publisher', 'category', 'risk_level', 'endpoint_count', 'versions', 'latest_seen_at'])
    for row in filtered_rows:
        writer.writerow([
            row['name'],
            row['publisher'],
            row['category'],
            row['risk_level'],
            row['endpoint_count'],
            '; '.join(row['versions']),
            row['latest_seen_at'].isoformat() if row['latest_seen_at'] else '',
        ])
    return response
