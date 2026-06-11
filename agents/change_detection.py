import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from agents.audit import create_audit_event
from agents.models import AgentMachine, AuditEvent, EndpointAlert
from agents.software_catalog import classify_sensitive_software, normalize_key


CHANGE_SOURCE = 'change_detection'


@dataclass
class ChangeDetectionResult:
    endpoints_evaluated: int = 0
    insufficient_snapshots: int = 0
    events_created: int = 0
    events_deduped: int = 0
    temporary_alerts_created: int = 0
    errors: int = 0
    dry_run_actions: list[str] = field(default_factory=list)


def software_identity(software):
    return normalize_key((software or {}).get('name') or '')


def software_version(software):
    return str((software or {}).get('version') or '').strip()


def software_name(software):
    return str((software or {}).get('name') or 'Software').strip() or 'Software'


def software_map(snapshot):
    items = {}
    for software in snapshot.installed_software or []:
        key = software_identity(software)
        if key != 'unknown':
            items[key] = software
    return items


def primary_ip(snapshot):
    return (snapshot.ips or [None])[0]


def defender_state(snapshot):
    defender = snapshot.defender_status or {}
    enabled = defender.get('enabled') is True
    realtime = defender.get('real_time_protection_enabled') is True
    if enabled and realtime:
        return 'protected'
    if defender:
        return 'partial'
    return 'missing'


def disk_state(disk):
    size = int((disk or {}).get('size_bytes') or 0)
    free = int((disk or {}).get('free_bytes') or 0)
    if size <= 0:
        return 'unknown', 0, 0
    free_percent = round((free / size) * 100, 1)
    used_percent = round(100 - free_percent, 1)
    if used_percent >= 90:
        return 'critical', used_percent, free_percent
    if used_percent >= 80:
        return 'warning', used_percent, free_percent
    return 'normal', used_percent, free_percent


def disk_map(snapshot):
    items = {}
    for disk in snapshot.disks or []:
        name = str(disk.get('name') or 'Disco')
        items[name] = disk
    return items


def audit_exists(endpoint, event_type, from_snapshot_id, to_snapshot_id, dedupe_key):
    return AuditEvent.objects.filter(
        endpoint=endpoint,
        event_type=event_type,
        metadata__from_snapshot_id=str(from_snapshot_id),
        metadata__to_snapshot_id=str(to_snapshot_id),
        metadata__dedupe_key=dedupe_key,
    ).exists()


def build_change(event_type, severity, title, description, dedupe_key, old_value=None, new_value=None, alert=None):
    return {
        'event_type': event_type,
        'severity': severity,
        'title': title,
        'description': description,
        'dedupe_key': dedupe_key,
        'old_value': old_value,
        'new_value': new_value,
        'alert': alert,
    }


def temporary_alert_payload(alert_type, severity, title, description, dedupe_key):
    return {
        'alert_type': alert_type,
        'severity': severity,
        'title': title,
        'description': description,
        'dedupe_key': dedupe_key,
    }


def detect_snapshot_changes(previous, current):
    changes = []
    previous_software = software_map(previous)
    current_software = software_map(current)

    for key, software in current_software.items():
        if key not in previous_software:
            category = classify_sensitive_software(software)
            severity = AuditEvent.SEVERITY_SECURITY if category else AuditEvent.SEVERITY_INFO
            name = software_name(software)
            alert = None
            if category == 'remote_access':
                alert = temporary_alert_payload(
                    'change.remote_access_installed',
                    EndpointAlert.SEVERITY_SECURITY,
                    'Software de acesso remoto instalado',
                    f'Ferramenta de acesso remoto instalada: {name}.',
                    f'change.remote_access_installed:{key}',
                )
            elif category == 'admin_network':
                alert = temporary_alert_payload(
                    'change.admin_tool_installed',
                    EndpointAlert.SEVERITY_SECURITY,
                    'Ferramenta administrativa instalada',
                    f'Ferramenta administrativa/rede instalada: {name}.',
                    f'change.admin_tool_installed:{key}',
                )
            changes.append(build_change(
                'software.installed',
                severity,
                'Software instalado',
                f'{name} foi instalado neste endpoint.',
                f'software.installed:{key}',
                new_value={'name': name, 'version': software_version(software), 'category': category},
                alert=alert,
            ))

    for key, software in previous_software.items():
        if key not in current_software:
            changes.append(build_change(
                'software.removed',
                AuditEvent.SEVERITY_INFO,
                'Software removido',
                f'{software_name(software)} foi removido deste endpoint.',
                f'software.removed:{key}',
                old_value={'name': software_name(software), 'version': software_version(software)},
            ))

    for key, software in current_software.items():
        if key in previous_software and software_version(previous_software[key]) != software_version(software):
            changes.append(build_change(
                'software.updated',
                AuditEvent.SEVERITY_INFO,
                'Software atualizado',
                f'{software_name(software)} foi atualizado.',
                f'software.updated:{key}',
                old_value=software_version(previous_software[key]),
                new_value=software_version(software),
            ))

    old_ip = primary_ip(previous)
    new_ip = primary_ip(current)
    if old_ip != new_ip:
        changes.append(build_change(
            'network.ip_changed',
            AuditEvent.SEVERITY_INFO,
            'IP principal alterado',
            f'IP principal alterado de {old_ip or "-"} para {new_ip or "-"}.',
            'network.ip_changed',
            old_value=old_ip,
            new_value=new_ip,
        ))

    if previous.logged_user != current.logged_user:
        changes.append(build_change(
            'user.logged_user_changed',
            AuditEvent.SEVERITY_INFO,
            'Usuario logado alterado',
            f'Usuario logado alterado de {previous.logged_user or "-"} para {current.logged_user or "-"}.',
            'user.logged_user_changed',
            old_value=previous.logged_user,
            new_value=current.logged_user,
        ))

    old_defender = defender_state(previous)
    new_defender = defender_state(current)
    if old_defender != new_defender:
        if new_defender == 'missing':
            severity = AuditEvent.SEVERITY_CRITICAL
            alert = temporary_alert_payload(
                'change.security_protection_disabled',
                EndpointAlert.SEVERITY_CRITICAL,
                'Protecao de endpoint desativada',
                'Protecao de endpoint foi desativada ou ficou indisponivel.',
                'change.security_protection_disabled',
            )
        elif new_defender == 'protected':
            severity = AuditEvent.SEVERITY_SUCCESS
            alert = None
        else:
            severity = AuditEvent.SEVERITY_WARNING
            alert = None
        changes.append(build_change(
            'security.defender_changed',
            severity,
            'Seguranca alterada',
            f'Estado de protecao alterado de {old_defender} para {new_defender}.',
            'security.defender_changed',
            old_value=old_defender,
            new_value=new_defender,
            alert=alert,
        ))

    previous_disks = disk_map(previous)
    current_disks = disk_map(current)
    for name, disk in current_disks.items():
        if name not in previous_disks:
            continue
        old_state, old_used, old_free = disk_state(previous_disks[name])
        new_state, new_used, new_free = disk_state(disk)
        if old_state != new_state:
            if new_state == 'critical':
                severity = AuditEvent.SEVERITY_CRITICAL
                alert_type = 'change.disk_entered_critical'
                alert_severity = EndpointAlert.SEVERITY_CRITICAL
                alert_title = f'Disco {name} entrou em estado critico'
                alert_description = f'Disco {name} entrou em estado critico.'
            elif new_state == 'warning':
                severity = AuditEvent.SEVERITY_WARNING
                alert_type = 'change.disk_entered_warning'
                alert_severity = EndpointAlert.SEVERITY_WARNING
                alert_title = f'Disco {name} entrou em estado de atencao'
                alert_description = f'Disco {name} entrou em estado de atencao.'
            else:
                severity = AuditEvent.SEVERITY_SUCCESS
                alert_type = None
                alert_severity = None
                alert_title = ''
                alert_description = ''
            alert = None
            if alert_type:
                alert = temporary_alert_payload(
                    alert_type,
                    alert_severity,
                    alert_title,
                    alert_description,
                    f'{alert_type}:{normalize_key(name)}',
                )
            changes.append(build_change(
                'disk.state_changed',
                severity,
                'Estado de disco alterado',
                f'Disco {name} alterou de {old_state} para {new_state}.',
                f'disk.state_changed:{name}',
                old_value={'state': old_state, 'used_percent': old_used, 'free_percent': old_free},
                new_value={'state': new_state, 'used_percent': new_used, 'free_percent': new_free},
                alert=alert,
            ))

    if previous.windows_build != current.windows_build:
        changes.append(build_change(
            'os.build_changed',
            AuditEvent.SEVERITY_INFO,
            'Windows build alterada',
            f'Windows build alterada de {previous.windows_build or "-"} para {current.windows_build or "-"}.',
            'os.build_changed',
            old_value=previous.windows_build,
            new_value=current.windows_build,
        ))

    return changes


def create_temporary_alert(endpoint, change, audit_event, hours):
    payload = change['alert']
    if not payload:
        return None

    now = timezone.now()
    expires_at = now + timedelta(hours=hours)
    dedupe_key = payload['dedupe_key']
    existing = EndpointAlert.objects.filter(
        endpoint=endpoint,
        alert_type=payload['alert_type'],
        status__in=[EndpointAlert.STATUS_OPEN, EndpointAlert.STATUS_ACKNOWLEDGED],
        metadata__dedupe_key=dedupe_key,
    ).first()
    metadata = {
        'temporary': True,
        'expires_at': expires_at.isoformat(),
        'source': CHANGE_SOURCE,
        'change_event_id': str(audit_event.id) if audit_event else '',
        'dedupe_key': dedupe_key,
    }
    if existing:
        return None

    return EndpointAlert.objects.create(
        endpoint=endpoint,
        alert_type=payload['alert_type'],
        severity=payload['severity'],
        title=payload['title'],
        description=payload['description'],
        status=EndpointAlert.STATUS_OPEN,
        first_seen_at=now,
        last_seen_at=now,
        metadata=metadata,
        is_temporary=True,
        expires_at=expires_at,
        source=CHANGE_SOURCE,
    )


@transaction.atomic
def apply_changes_for_endpoint(endpoint, previous, current, hours, dry_run=False):
    result = ChangeDetectionResult(endpoints_evaluated=1)
    changes = detect_snapshot_changes(previous, current)
    for change in changes:
        metadata = {
            'from_snapshot_id': str(previous.id),
            'to_snapshot_id': str(current.id),
            'dedupe_key': change['dedupe_key'],
            'old_value': change['old_value'],
            'new_value': change['new_value'],
        }
        if audit_exists(endpoint, change['event_type'], previous.id, current.id, change['dedupe_key']):
            result.events_deduped += 1
            continue
        if dry_run:
            result.events_created += 1
            if change['alert']:
                result.temporary_alerts_created += 1
            result.dry_run_actions.append(f'{endpoint.hostname}: {change["event_type"]} {change["dedupe_key"]}')
            continue

        audit_event = create_audit_event(
            event_type=change['event_type'],
            title=change['title'],
            description=change['description'],
            severity=change['severity'],
            actor_type=AuditEvent.ACTOR_SYSTEM,
            actor_name='detect_changes',
            endpoint=endpoint,
            metadata=metadata,
        )
        result.events_created += 1
        alert = create_temporary_alert(endpoint, change, audit_event, hours)
        if alert:
            result.temporary_alerts_created += 1
            create_audit_event(
                event_type='alert.created',
                title='Alerta temporario de mudanca criado',
                description=alert.description,
                severity=alert.severity,
                actor_type=AuditEvent.ACTOR_SYSTEM,
                actor_name='detect_changes',
                endpoint=endpoint,
                alert=alert,
                metadata={
                    'alert_type': alert.alert_type,
                    'alert_id': str(alert.id),
                    'severity': alert.severity,
                    'status': alert.status,
                    'temporary': True,
                    'expires_at': alert.expires_at.isoformat() if alert.expires_at else None,
                    'change_event_id': str(audit_event.id) if audit_event else None,
                },
            )
    return result


def evaluate_endpoint_changes(endpoint, hours=72, dry_run=False):
    snapshots = list(endpoint.inventory_snapshots.order_by('-received_at')[:2])
    if len(snapshots) < 2:
        return ChangeDetectionResult(endpoints_evaluated=1, insufficient_snapshots=1)
    current, previous = snapshots[0], snapshots[1]
    return apply_changes_for_endpoint(endpoint, previous, current, hours, dry_run=dry_run)


def detect_all_changes(hours=72, endpoint_filter=None, dry_run=False):
    result = ChangeDetectionResult()
    queryset = AgentMachine.objects.filter(is_active=True)
    if endpoint_filter:
        queryset = queryset.filter(
            models_endpoint_filter(endpoint_filter)
        )
    for endpoint in queryset:
        try:
            endpoint_result = evaluate_endpoint_changes(endpoint, hours=hours, dry_run=dry_run)
            result.endpoints_evaluated += endpoint_result.endpoints_evaluated
            result.insufficient_snapshots += endpoint_result.insufficient_snapshots
            result.events_created += endpoint_result.events_created
            result.events_deduped += endpoint_result.events_deduped
            result.temporary_alerts_created += endpoint_result.temporary_alerts_created
            result.dry_run_actions.extend(endpoint_result.dry_run_actions)
        except Exception:
            result.errors += 1
    return result


def models_endpoint_filter(value):
    from django.db.models import Q

    try:
        endpoint_id = uuid.UUID(str(value))
    except (TypeError, ValueError):
        endpoint_id = None

    query = Q(hostname__iexact=value)
    if endpoint_id:
        query |= Q(id=endpoint_id)
    return query
