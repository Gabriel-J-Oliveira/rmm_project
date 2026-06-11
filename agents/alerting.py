from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone
from django.conf import settings

from agents.audit import create_audit_event
from agents.software_catalog import (
    ADMIN_NETWORK_SOFTWARE,
    REMOTE_ACCESS_SOFTWARE,
    normalize_key,
)
from agents.versioning import compare_versions

from .models import AgentMachine, AlertEvent, EndpointAlert


ANTIVIRUS_SOFTWARE = [
    'bitdefender',
    'kaspersky',
    'eset',
    'sophos',
    'trend micro',
    'crowdstrike',
    'sentinelone',
    'malwarebytes',
    'avast',
    'avg',
    'mcafee',
    'symantec',
    'webroot',
]


@dataclass
class AlertEvaluationResult:
    endpoints_evaluated: int = 0
    created: int = 0
    updated: int = 0
    resolved: int = 0
    dry_run_actions: list[str] = field(default_factory=list)


def software_text(software):
    return ' '.join([
        str((software or {}).get('name') or ''),
        str((software or {}).get('publisher') or ''),
    ]).lower()


def detect_software(installed_software, terms):
    matches = []
    for software in installed_software or []:
        text = software_text(software)
        if any(term in text for term in terms):
            matches.append(software)
    return matches


def gb(value):
    try:
        return round(int(value) / (1024 ** 3), 1)
    except (TypeError, ValueError):
        return None


def desired_alert(alert_type, dedupe_key, severity, title, description, metadata):
    payload = dict(metadata or {})
    payload['dedupe_key'] = dedupe_key
    return {
        'alert_type': alert_type,
        'dedupe_key': dedupe_key,
        'severity': severity,
        'title': title,
        'description': description,
        'metadata': payload,
    }


def create_event(alert, event_type, message, metadata=None):
    AlertEvent.objects.create(
        alert=alert,
        event_type=event_type,
        message=message,
        metadata=metadata or {},
    )


def audit_alert(alert, event_type, title, description, severity=None, actor_type='system', metadata=None):
    create_audit_event(
        event_type=event_type,
        severity=severity or alert.severity,
        actor_type=actor_type,
        actor_name='Night Owl',
        endpoint=alert.endpoint,
        alert=alert,
        title=title,
        description=description,
        metadata={
            'alert_type': alert.alert_type,
            'alert_id': str(alert.id),
            'severity': alert.severity,
            'status': alert.status,
            **(metadata or {}),
        },
    )


def evaluate_endpoint(endpoint, options):
    now = timezone.now()
    snapshot = endpoint.inventory_snapshots.order_by('-received_at').first()
    alerts = []

    if endpoint.status == AgentMachine.STATUS_OFFLINE and endpoint.last_seen_at:
        offline_for = now - endpoint.last_seen_at
        offline_seconds = int(offline_for.total_seconds())
        warning = timedelta(hours=options['offline_warning_hours'])
        critical = timedelta(hours=options['offline_critical_hours'])
        if offline_for >= critical:
            severity = EndpointAlert.SEVERITY_CRITICAL
            threshold = 'critical'
        elif offline_for >= warning:
            severity = EndpointAlert.SEVERITY_WARNING
            threshold = 'warning'
        else:
            severity = None
        if severity:
            hours = max(round(offline_seconds / 3600, 1), 0)
            alerts.append(desired_alert(
                'endpoint_offline',
                'endpoint_offline',
                severity,
                'Endpoint offline',
                f'Endpoint sem comunicação há {hours} horas.',
                {
                    'last_seen_at': endpoint.last_seen_at.isoformat(),
                    'offline_for_seconds': offline_seconds,
                    'threshold': threshold,
                },
            ))

    recommended_agent_version = getattr(settings, 'NIGHTOWL_RECOMMENDED_AGENT_VERSION', '')
    if endpoint.agent_version and recommended_agent_version:
        comparison = compare_versions(endpoint.agent_version, recommended_agent_version)
        if comparison is None or comparison < 0:
            alerts.append(desired_alert(
                'agent_outdated',
                'agent_outdated',
                EndpointAlert.SEVERITY_WARNING,
                'Agente desatualizado',
                f'Este endpoint usa a versao {endpoint.agent_version} do agente. A versao recomendada e {recommended_agent_version}.',
                {
                    'current_version': endpoint.agent_version,
                    'recommended_version': recommended_agent_version,
                },
            ))

    if not snapshot:
        alerts.append(desired_alert(
            'stale_inventory',
            'stale_inventory',
            EndpointAlert.SEVERITY_WARNING,
            'Inventário desatualizado',
            'Endpoint ainda não possui snapshot de inventário.',
            {'last_snapshot_at': None, 'age_seconds': None},
        ))
        return alerts

    for disk in snapshot.disks or []:
        size = int(disk.get('size_bytes') or 0)
        free = int(disk.get('free_bytes') or 0)
        if size <= 0:
            continue
        free_percent = round((free / size) * 100, 1)
        used_percent = round(100 - free_percent, 1)
        name = disk.get('name') or 'Disco'
        if free_percent <= options['disk_critical_free_percent']:
            severity = EndpointAlert.SEVERITY_CRITICAL
            title = f'Disco {name} crítico'
        elif free_percent <= options['disk_warning_free_percent']:
            severity = EndpointAlert.SEVERITY_WARNING
            title = f'Disco {name} com pouco espaço'
        else:
            continue
        alerts.append(desired_alert(
            'disk_low',
            f'disk_low:{name}',
            severity,
            title,
            f'Disco {name} possui apenas {free_percent}% livre. Livre: {gb(free)} GB de {gb(size)} GB.',
            {
                'disk_name': name,
                'free_bytes': free,
                'size_bytes': size,
                'used_percent': used_percent,
                'free_percent': free_percent,
            },
        ))

    defender = snapshot.defender_status or {}
    installed = snapshot.installed_software or []
    defender_ok = defender.get('enabled') is True and defender.get('real_time_protection_enabled') is True
    detected_av = detect_software(installed, ANTIVIRUS_SOFTWARE)
    if not defender_ok:
        if detected_av:
            av_name = detected_av[0].get('name') or 'antivírus alternativo'
            alerts.append(desired_alert(
                'security_alternative_av',
                'security_alternative_av',
                EndpointAlert.SEVERITY_INFO,
                'Defender ausente, antivírus alternativo detectado',
                f'Defender não identificado, mas {av_name} foi detectado nos softwares instalados.',
                {'defender_status': defender, 'detected_av': av_name, 'security_state': 'alternative_av'},
            ))
        elif defender:
            alerts.append(desired_alert(
                'security_antivirus',
                'security_antivirus',
                EndpointAlert.SEVERITY_WARNING,
                'Proteção de endpoint parcial',
                'Defender ausente, desativado ou com proteção em tempo real parcial.',
                {'defender_status': defender, 'detected_av': None, 'security_state': 'partial'},
            ))
        else:
            alerts.append(desired_alert(
                'security_antivirus',
                'security_antivirus',
                EndpointAlert.SEVERITY_CRITICAL,
                'Nenhuma proteção antivírus identificada',
                'Nenhum antivírus ou proteção de endpoint foi identificado.',
                {'defender_status': defender, 'detected_av': None, 'security_state': 'missing'},
            ))

    uptime = snapshot.uptime_seconds
    if uptime is not None:
        days = round(int(uptime) / 86400, 1)
        if days >= options['uptime_critical_days']:
            severity = EndpointAlert.SEVERITY_CRITICAL
            title = 'Uptime elevado'
        elif days >= options['uptime_warning_days']:
            severity = EndpointAlert.SEVERITY_WARNING
            title = 'Reinicialização recomendada'
        else:
            severity = None
        if severity:
            alerts.append(desired_alert(
                'high_uptime',
                'high_uptime',
                severity,
                title,
                f'Endpoint ligado há {days} dias. Reinicialização recomendada.',
                {'uptime_seconds': int(uptime), 'uptime_days': days},
            ))

    for software in detect_software(installed, REMOTE_ACCESS_SOFTWARE):
        name = software.get('name') or 'software remoto'
        alerts.append(desired_alert(
            'remote_access_software',
            f'remote_access_software:{normalize_key(name)}',
            EndpointAlert.SEVERITY_SECURITY,
            'Software de acesso remoto detectado',
            f'Ferramenta de acesso remoto detectada: {name}.',
            {'software_name': name, 'version': software.get('version'), 'publisher': software.get('publisher'), 'category': 'remote_access'},
        ))

    for software in detect_software(installed, ADMIN_NETWORK_SOFTWARE):
        name = software.get('name') or 'ferramenta administrativa'
        alerts.append(desired_alert(
            'admin_network_tool',
            f'admin_network_tool:{normalize_key(name)}',
            EndpointAlert.SEVERITY_SECURITY,
            'Ferramenta administrativa detectada',
            f'Ferramenta administrativa/rede detectada: {name}.',
            {'software_name': name, 'version': software.get('version'), 'publisher': software.get('publisher'), 'category': 'admin_network'},
        ))

    age = now - snapshot.received_at
    if age >= timedelta(hours=options['stale_inventory_hours']):
        alerts.append(desired_alert(
            'stale_inventory',
            'stale_inventory',
            EndpointAlert.SEVERITY_WARNING,
            'Inventário desatualizado',
            'Último inventário recebido há mais de 24 horas.',
            {'last_snapshot_at': snapshot.received_at.isoformat(), 'age_seconds': int(age.total_seconds())},
        ))

    memory = snapshot.memory_total_bytes
    if memory and int(memory) < 8 * 1024 ** 3:
        memory_gb = gb(memory)
        alerts.append(desired_alert(
            'low_memory',
            'low_memory',
            EndpointAlert.SEVERITY_WARNING,
            'Memória baixa',
            f'Endpoint possui apenas {memory_gb} GB de RAM.',
            {'memory_total_bytes': int(memory), 'memory_total_gb': memory_gb},
        ))

    return alerts


def apply_alerts_for_endpoint(endpoint, desired_alerts, dry_run=False):
    result = AlertEvaluationResult()
    now = timezone.now()
    desired_keys = {item['dedupe_key'] for item in desired_alerts}

    active_existing = EndpointAlert.objects.filter(
        endpoint=endpoint,
        status__in=[EndpointAlert.STATUS_OPEN, EndpointAlert.STATUS_ACKNOWLEDGED],
        is_temporary=False,
    )

    existing_by_key = {
        alert.metadata.get('dedupe_key'): alert
        for alert in active_existing
        if alert.metadata.get('dedupe_key')
    }
    resolved_by_key = {}
    for alert in EndpointAlert.objects.filter(endpoint=endpoint, status=EndpointAlert.STATUS_RESOLVED).order_by('-resolved_at', '-updated_at'):
        dedupe_key = alert.metadata.get('dedupe_key')
        if dedupe_key and dedupe_key not in resolved_by_key:
            resolved_by_key[dedupe_key] = alert

    for item in desired_alerts:
        alert = existing_by_key.get(item['dedupe_key'])
        if alert:
            result.updated += 1
            if dry_run:
                result.dry_run_actions.append(f'update {endpoint.hostname}: {item["dedupe_key"]}')
            else:
                changed = (
                    alert.alert_type != item['alert_type']
                    or alert.severity != item['severity']
                    or alert.title != item['title']
                    or alert.description != item['description']
                    or alert.metadata != item['metadata']
                )
                alert.alert_type = item['alert_type']
                alert.severity = item['severity']
                alert.title = item['title']
                alert.description = item['description']
                alert.last_seen_at = now
                alert.metadata = item['metadata']
                alert.save(update_fields=['alert_type', 'severity', 'title', 'description', 'last_seen_at', 'metadata', 'updated_at'])
                if changed:
                    create_event(alert, AlertEvent.TYPE_UPDATED, 'Alerta atualizado pela avaliacao automatica.', {'dedupe_key': item['dedupe_key']})
                    audit_alert(
                        alert,
                        'alert.updated',
                        'Alerta atualizado',
                        f'{alert.title}: {alert.description}',
                        metadata={'dedupe_key': item['dedupe_key']},
                    )
        elif item['dedupe_key'] in resolved_by_key:
            alert = resolved_by_key[item['dedupe_key']]
            result.updated += 1
            if dry_run:
                result.dry_run_actions.append(f'reopen {endpoint.hostname}: {item["dedupe_key"]}')
            else:
                alert.alert_type = item['alert_type']
                alert.severity = item['severity']
                alert.title = item['title']
                alert.description = item['description']
                alert.status = EndpointAlert.STATUS_OPEN
                alert.last_seen_at = now
                alert.resolved_at = None
                alert.resolution_type = ''
                alert.metadata = item['metadata']
                alert.save(update_fields=['alert_type', 'severity', 'title', 'description', 'status', 'last_seen_at', 'resolved_at', 'resolution_type', 'metadata', 'updated_at'])
                existing_by_key[item['dedupe_key']] = alert
                create_event(alert, AlertEvent.TYPE_REOPENED, 'Alerta reaberto porque a condicao voltou a existir.', {'dedupe_key': item['dedupe_key']})
                audit_alert(
                    alert,
                    'alert.reopened',
                    'Alerta reaberto',
                    'A condicao do alerta voltou a ser detectada.',
                    metadata={'dedupe_key': item['dedupe_key']},
                )
        else:
            result.created += 1
            if dry_run:
                result.dry_run_actions.append(f'create {endpoint.hostname}: {item["dedupe_key"]}')
            else:
                alert = EndpointAlert.objects.create(
                    endpoint=endpoint,
                    alert_type=item['alert_type'],
                    severity=item['severity'],
                    title=item['title'],
                    description=item['description'],
                    status=EndpointAlert.STATUS_OPEN,
                    first_seen_at=now,
                    last_seen_at=now,
                    metadata=item['metadata'],
                )
                create_event(alert, AlertEvent.TYPE_CREATED, 'Alerta criado pela avaliacao automatica.', {'dedupe_key': item['dedupe_key']})
                audit_alert(
                    alert,
                    'alert.created',
                    'Alerta criado',
                    f'{alert.title}: {alert.description}',
                    metadata={'dedupe_key': item['dedupe_key']},
                )

    for alert in active_existing:
        dedupe_key = alert.metadata.get('dedupe_key')
        if dedupe_key and dedupe_key not in desired_keys:
            result.resolved += 1
            if dry_run:
                result.dry_run_actions.append(f'resolve {endpoint.hostname}: {dedupe_key}')
            else:
                alert.status = EndpointAlert.STATUS_RESOLVED
                alert.resolved_at = now
                alert.resolution_type = EndpointAlert.RESOLUTION_AUTOMATIC
                alert.save(update_fields=['status', 'resolved_at', 'resolution_type', 'updated_at'])
                create_event(alert, AlertEvent.TYPE_RESOLVED_AUTOMATIC, 'Alerta resolvido automaticamente porque a condicao deixou de existir.', {'dedupe_key': dedupe_key})
                audit_alert(
                    alert,
                    'alert.resolved_auto',
                    'Alerta resolvido automaticamente',
                    'A condicao do alerta nao foi mais detectada.',
                    severity='success',
                    metadata={'dedupe_key': dedupe_key},
                )

    return result


def evaluate_all_alerts(options, dry_run=False):
    result = AlertEvaluationResult()
    for endpoint in AgentMachine.objects.filter(is_active=True):
        result.endpoints_evaluated += 1
        desired = evaluate_endpoint(endpoint, options)
        endpoint_result = apply_alerts_for_endpoint(endpoint, desired, dry_run=dry_run)
        result.created += endpoint_result.created
        result.updated += endpoint_result.updated
        result.resolved += endpoint_result.resolved
        result.dry_run_actions.extend(endpoint_result.dry_run_actions)
    return result
