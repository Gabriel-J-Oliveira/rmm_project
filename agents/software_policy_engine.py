from dataclasses import dataclass, field
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from agents.audit import create_audit_event
from agents.models import (
    AgentMachine,
    AlertEvent,
    AuditEvent,
    EndpointAlert,
    SoftwarePolicy,
    SoftwarePolicyException,
    SoftwarePolicyViolation,
)
from agents.software_catalog import normalize_key


SOFTWARE_POLICY_ALERT_TYPE = 'software_policy_violation'


@dataclass
class SoftwarePolicyEvaluationResult:
    policies_evaluated: int = 0
    endpoints_evaluated: int = 0
    violations_created: int = 0
    violations_updated: int = 0
    violations_resolved: int = 0
    alerts_created: int = 0
    alerts_resolved: int = 0
    exceptions_applied: int = 0
    errors: int = 0
    dry_run_actions: list[str] = field(default_factory=list)


def text(value):
    return str(value or '').strip()


def normalize_text(value):
    return text(value).casefold()


def software_name(software):
    return text((software or {}).get('name')) or 'Software'


def software_version(software):
    return text((software or {}).get('version'))


def software_publisher(software):
    return text((software or {}).get('publisher'))


def software_matches_policy(policy, software):
    name = normalize_text(software_name(software))
    target = normalize_text(policy.software_name)
    if not target:
        return False

    if policy.match_type == SoftwarePolicy.MATCH_EQUALS:
        matched = name == target
    elif policy.match_type == SoftwarePolicy.MATCH_STARTS_WITH:
        matched = name.startswith(target)
    else:
        matched = target in name

    if not matched:
        return False

    if policy.publisher:
        publisher = normalize_text(software_publisher(software))
        if normalize_text(policy.publisher) not in publisher:
            return False

    if policy.version_rule:
        version = normalize_text(software_version(software))
        if normalize_text(policy.version_rule) not in version:
            return False

    return True


def policy_applies_to_endpoint(policy, endpoint):
    if policy.scope_type == SoftwarePolicy.SCOPE_ALL:
        return True
    if policy.scope_type == SoftwarePolicy.SCOPE_HOSTNAME_PREFIX:
        return normalize_text(endpoint.hostname).startswith(normalize_text(policy.scope_value))
    if policy.scope_type == SoftwarePolicy.SCOPE_HOSTNAME_CONTAINS:
        return normalize_text(policy.scope_value) in normalize_text(endpoint.hostname)
    if policy.scope_type == SoftwarePolicy.SCOPE_DOMAIN:
        return normalize_text(endpoint.domain) == normalize_text(policy.scope_value)
    if policy.scope_type == SoftwarePolicy.SCOPE_SPECIFIC_ENDPOINTS:
        return any(item.endpoint_id == endpoint.id for item in policy.target_endpoints.all())
    return False


def policy_has_specific_targets(policy):
    if policy.scope_type != SoftwarePolicy.SCOPE_SPECIFIC_ENDPOINTS:
        return True
    return bool(list(policy.target_endpoints.all()))


def active_exception_exists(policy, endpoint, now=None):
    now = now or timezone.now()
    return SoftwarePolicyException.objects.filter(
        policy=policy,
        endpoint=endpoint,
        is_active=True,
    ).filter(
        Q(exception_type=SoftwarePolicyException.TYPE_PERMANENT)
        | Q(exception_type=SoftwarePolicyException.TYPE_TEMPORARY, expires_at__gte=now)
    ).exists()


def latest_snapshot(endpoint):
    return endpoint.inventory_snapshots.order_by('-received_at').first()


def violation_dedupe_key(policy, endpoint, software_name_value):
    return f'{policy.id}:{endpoint.id}:{normalize_key(software_name_value)}'


def alert_dedupe_key(violation):
    return f'{SOFTWARE_POLICY_ALERT_TYPE}:{violation.id}'


def build_violation(policy, endpoint, snapshot, software=None, required_missing=False):
    if required_missing:
        name = policy.software_name
        version = ''
        publisher = policy.publisher
        reason = 'required_missing'
    else:
        name = software_name(software)
        version = software_version(software)
        publisher = software_publisher(software)
        reason = policy.policy_type

    return {
        'policy': policy,
        'endpoint': endpoint,
        'snapshot': snapshot,
        'software_name': name,
        'software_version': version,
        'publisher': publisher,
        'severity': policy.severity,
        'dedupe_key': violation_dedupe_key(policy, endpoint, name),
        'reason': reason,
        'metadata': {
            'policy_type': policy.policy_type,
            'policy_id': str(policy.id),
            'policy_name': policy.name,
            'endpoint_id': str(endpoint.id),
            'snapshot_id': str(snapshot.id) if snapshot else None,
            'dedupe_key': violation_dedupe_key(policy, endpoint, name),
            'match_type': policy.match_type,
            'scope_type': policy.scope_type,
            'scope_value': policy.scope_value,
            'required_missing': required_missing,
        },
    }


def desired_violations_for_policy_endpoint(policy, endpoint, snapshot):
    if policy.policy_type == SoftwarePolicy.TYPE_PERMITTED:
        return []

    installed = snapshot.installed_software or []
    matches = [software for software in installed if software_matches_policy(policy, software)]

    if policy.policy_type == SoftwarePolicy.TYPE_REQUIRED:
        if not matches:
            return [build_violation(policy, endpoint, snapshot, required_missing=True)]
        return []

    if policy.policy_type == SoftwarePolicy.TYPE_RESTRICTED:
        if policy_applies_to_endpoint(policy, endpoint):
            return []
        return [build_violation(policy, endpoint, snapshot, software=software) for software in matches]

    if policy.policy_type in {SoftwarePolicy.TYPE_PROHIBITED, SoftwarePolicy.TYPE_OBSERVED}:
        if not policy_applies_to_endpoint(policy, endpoint):
            return []
        return [build_violation(policy, endpoint, snapshot, software=software) for software in matches]

    return []


def create_violation_audit(violation, event_type, title, description, severity=None, extra_metadata=None, dry_run=False):
    if dry_run or not violation.policy.create_audit_event:
        return
    create_audit_event(
        event_type=event_type,
        title=title,
        description=description,
        severity=severity or violation.severity,
        actor_type=AuditEvent.ACTOR_SYSTEM,
        actor_name='evaluate_software_policies',
        endpoint=violation.endpoint,
        alert=violation.alert,
        metadata={
            'policy_id': str(violation.policy_id),
            'policy_name': violation.policy.name,
            'violation_id': str(violation.id),
            'software_name': violation.software_name,
            'status': violation.status,
            **(extra_metadata or {}),
        },
    )


def find_open_violation(payload):
    return SoftwarePolicyViolation.objects.filter(
        policy=payload['policy'],
        endpoint=payload['endpoint'],
        status=SoftwarePolicyViolation.STATUS_OPEN,
        metadata__dedupe_key=payload['dedupe_key'],
    ).first()


def create_or_update_alert_for_violation(violation, now, dry_run=False):
    policy = violation.policy
    if not policy.create_alert or policy.monitor_only:
        return None, False

    if dry_run:
        return violation.alert, violation.alert is None

    existing = violation.alert
    if existing and existing.status in {EndpointAlert.STATUS_OPEN, EndpointAlert.STATUS_ACKNOWLEDGED}:
        existing.severity = violation.severity
        existing.title = 'Violacao de politica de software'
        existing.description = alert_description(violation)
        existing.last_seen_at = now
        existing.metadata = alert_metadata(violation)
        existing.save(update_fields=['severity', 'title', 'description', 'last_seen_at', 'metadata', 'updated_at'])
        return existing, False

    existing = EndpointAlert.objects.filter(
        endpoint=violation.endpoint,
        alert_type=SOFTWARE_POLICY_ALERT_TYPE,
        status__in=[EndpointAlert.STATUS_OPEN, EndpointAlert.STATUS_ACKNOWLEDGED],
        metadata__violation_id=str(violation.id),
    ).first()
    if existing:
        violation.alert = existing
        violation.save(update_fields=['alert', 'updated_at'])
        return existing, False

    alert = EndpointAlert.objects.create(
        endpoint=violation.endpoint,
        alert_type=SOFTWARE_POLICY_ALERT_TYPE,
        severity=violation.severity,
        title='Violacao de politica de software',
        description=alert_description(violation),
        status=EndpointAlert.STATUS_OPEN,
        first_seen_at=now,
        last_seen_at=now,
        source='software_policy',
        metadata=alert_metadata(violation),
    )
    AlertEvent.objects.create(
        alert=alert,
        event_type=AlertEvent.TYPE_CREATED,
        message='Alerta criado por violacao de politica de software.',
        metadata={'violation_id': str(violation.id), 'policy_id': str(policy.id)},
    )
    violation.alert = alert
    violation.save(update_fields=['alert', 'updated_at'])
    create_audit_event(
        event_type='alert.created',
        severity=alert.severity,
        actor_type=AuditEvent.ACTOR_SYSTEM,
        actor_name='evaluate_software_policies',
        endpoint=violation.endpoint,
        alert=alert,
        title='Alerta criado',
        description=alert.description,
        metadata=alert.metadata,
    )
    return alert, True


def alert_description(violation):
    return (
        f'Politica: {violation.policy.name}. '
        f'Software detectado: {violation.software_name or violation.policy.software_name} '
        f'em {violation.endpoint.hostname}.'
    )


def alert_metadata(violation):
    return {
        'dedupe_key': alert_dedupe_key(violation),
        'policy_id': str(violation.policy_id),
        'policy_name': violation.policy.name,
        'violation_id': str(violation.id),
        'software_name': violation.software_name,
        'software_version': violation.software_version,
        'publisher': violation.publisher,
    }


def resolve_alert_for_violation(violation, now, dry_run=False):
    alert = violation.alert
    if not alert or alert.status == EndpointAlert.STATUS_RESOLVED:
        return False
    if dry_run:
        return True
    alert.status = EndpointAlert.STATUS_RESOLVED
    alert.resolved_at = now
    alert.resolution_type = EndpointAlert.RESOLUTION_AUTOMATIC
    alert.save(update_fields=['status', 'resolved_at', 'resolution_type', 'updated_at'])
    AlertEvent.objects.create(
        alert=alert,
        event_type=AlertEvent.TYPE_RESOLVED_AUTOMATIC,
        message='Alerta resolvido automaticamente pela avaliacao de politicas de software.',
        metadata={'violation_id': str(violation.id), 'policy_id': str(violation.policy_id)},
    )
    create_audit_event(
        event_type='alert.resolved_auto',
        severity=AuditEvent.SEVERITY_SUCCESS,
        actor_type=AuditEvent.ACTOR_SYSTEM,
        actor_name='evaluate_software_policies',
        endpoint=violation.endpoint,
        alert=alert,
        title='Alerta resolvido automaticamente',
        description='A violacao de politica de software nao foi mais detectada.',
        metadata=alert.metadata,
    )
    return True


def apply_violation_payload(payload, now, dry_run=False):
    violation = find_open_violation(payload)
    created = False
    if violation:
        if dry_run:
            return violation, False, False
        changed = (
            violation.snapshot_id != payload['snapshot'].id
            or violation.software_version != payload['software_version']
            or violation.publisher != payload['publisher']
            or violation.severity != payload['severity']
        )
        violation.snapshot = payload['snapshot']
        violation.software_version = payload['software_version']
        violation.publisher = payload['publisher']
        violation.severity = payload['severity']
        violation.last_seen_at = now
        violation.metadata = payload['metadata']
        violation.save(update_fields=[
            'snapshot',
            'software_version',
            'publisher',
            'severity',
            'last_seen_at',
            'metadata',
            'updated_at',
        ])
        return violation, False, changed

    if dry_run:
        return None, True, False

    violation = SoftwarePolicyViolation.objects.create(
        policy=payload['policy'],
        endpoint=payload['endpoint'],
        snapshot=payload['snapshot'],
        software_name=payload['software_name'],
        software_version=payload['software_version'],
        publisher=payload['publisher'],
        status=SoftwarePolicyViolation.STATUS_OPEN,
        severity=payload['severity'],
        first_seen_at=now,
        last_seen_at=now,
        metadata=payload['metadata'],
    )
    create_violation_audit(
        violation,
        'software_policy.violation_detected',
        'Violacao de politica de software detectada',
        alert_description(violation),
        severity=violation.severity,
    )
    created = True
    return violation, created, False


def resolve_violation(violation, reason, now, dry_run=False):
    if dry_run:
        return True
    violation.status = (
        SoftwarePolicyViolation.STATUS_EXCEPTION_APPLIED
        if reason == 'exception_applied'
        else SoftwarePolicyViolation.STATUS_RESOLVED
    )
    violation.resolved_at = now
    violation.resolution_reason = reason
    violation.save(update_fields=['status', 'resolved_at', 'resolution_reason', 'updated_at'])
    create_violation_audit(
        violation,
        'software_policy.violation_resolved',
        'Violacao de politica de software resolvida',
        f'Violacao resolvida: {reason}.',
        severity=AuditEvent.SEVERITY_SUCCESS,
        extra_metadata={'resolution_reason': reason},
    )
    return True


def resolve_missing_violations(policy, endpoint, desired_keys, reason, now, result, dry_run=False):
    open_violations = SoftwarePolicyViolation.objects.filter(
        policy=policy,
        endpoint=endpoint,
        status=SoftwarePolicyViolation.STATUS_OPEN,
    )
    for violation in open_violations:
        key = violation.metadata.get('dedupe_key') or violation_dedupe_key(policy, endpoint, violation.software_name)
        if key in desired_keys:
            continue
        result.violations_resolved += 1
        result.dry_run_actions.append(f'resolve violation {endpoint.hostname}: {policy.name} ({reason})')
        if resolve_alert_for_violation(violation, now, dry_run=dry_run):
            result.alerts_resolved += 1
        resolve_violation(violation, reason, now, dry_run=dry_run)


def evaluate_policy_endpoint(policy, endpoint, snapshot, result, options, now, dry_run=False):
    if active_exception_exists(policy, endpoint, now):
        result.exceptions_applied += 1
        open_violations = SoftwarePolicyViolation.objects.filter(
            policy=policy,
            endpoint=endpoint,
            status=SoftwarePolicyViolation.STATUS_OPEN,
        )
        had_open_violations = open_violations.exists()
        for violation in open_violations:
            result.violations_resolved += 1
            result.dry_run_actions.append(f'exception applied {endpoint.hostname}: {policy.name}')
            if resolve_alert_for_violation(violation, now, dry_run=dry_run):
                result.alerts_resolved += 1
            resolve_violation(violation, 'exception_applied', now, dry_run=dry_run)
        if policy.create_audit_event and not dry_run and had_open_violations:
            create_audit_event(
                event_type='software_policy.exception_applied',
                title='Excecao de politica aplicada',
                description=f'Excecao ativa aplicada para {endpoint.hostname} na politica {policy.name}.',
                severity=AuditEvent.SEVERITY_INFO,
                actor_type=AuditEvent.ACTOR_SYSTEM,
                actor_name='evaluate_software_policies',
                endpoint=endpoint,
                metadata={'policy_id': str(policy.id), 'policy_name': policy.name},
            )
        return

    if not policy.is_active:
        if options['resolve_missing']:
            resolve_missing_violations(policy, endpoint, set(), 'policy_inactive', now, result, dry_run=dry_run)
        return

    if policy.scope_type == SoftwarePolicy.SCOPE_SPECIFIC_ENDPOINTS and not policy_has_specific_targets(policy):
        if options['verbose']:
            result.dry_run_actions.append(f'skip {endpoint.hostname}: {policy.name} has no target endpoints')
        if options['resolve_missing']:
            resolve_missing_violations(policy, endpoint, set(), 'endpoint_out_of_scope', now, result, dry_run=dry_run)
        return

    if not snapshot:
        if options['verbose']:
            result.dry_run_actions.append(f'skip {endpoint.hostname}: no inventory snapshot')
        return

    if policy.policy_type != SoftwarePolicy.TYPE_RESTRICTED and not policy_applies_to_endpoint(policy, endpoint):
        if options['resolve_missing']:
            resolve_missing_violations(policy, endpoint, set(), 'endpoint_out_of_scope', now, result, dry_run=dry_run)
        return

    desired = desired_violations_for_policy_endpoint(policy, endpoint, snapshot)
    desired_keys = {item['dedupe_key'] for item in desired}

    for payload in desired:
        violation, created, changed = apply_violation_payload(payload, now, dry_run=dry_run)
        if created:
            result.violations_created += 1
            result.dry_run_actions.append(f'create violation {endpoint.hostname}: {policy.name} / {payload["software_name"]}')
            if dry_run and policy.create_alert and not policy.monitor_only:
                result.alerts_created += 1
        elif changed:
            result.violations_updated += 1
            result.dry_run_actions.append(f'update violation {endpoint.hostname}: {policy.name} / {payload["software_name"]}')
        else:
            result.violations_updated += 1

        if violation:
            alert, alert_created = create_or_update_alert_for_violation(violation, now, dry_run=dry_run)
            if alert_created:
                result.alerts_created += 1

    if options['resolve_missing']:
        reason = 'condition_cleared'
        if policy.policy_type != SoftwarePolicy.TYPE_RESTRICTED and not policy_applies_to_endpoint(policy, endpoint):
            reason = 'endpoint_out_of_scope'
        resolve_missing_violations(policy, endpoint, desired_keys, reason, now, result, dry_run=dry_run)


def filter_policies(policy_filter):
    queryset = SoftwarePolicy.objects.prefetch_related('target_endpoints').all().order_by('name')
    if not policy_filter:
        return queryset
    policy_filter = text(policy_filter)
    try:
        policy_id = UUID(policy_filter)
    except ValueError:
        return queryset.filter(name__icontains=policy_filter)
    return queryset.filter(Q(id=policy_id) | Q(name__icontains=policy_filter))


def filter_endpoints(endpoint_filter):
    queryset = AgentMachine.objects.filter(is_active=True).order_by('hostname', 'domain')
    if not endpoint_filter:
        return queryset
    endpoint_filter = text(endpoint_filter)
    try:
        endpoint_id = UUID(endpoint_filter)
    except ValueError:
        return queryset.filter(hostname__icontains=endpoint_filter)
    return queryset.filter(Q(id=endpoint_id) | Q(hostname__icontains=endpoint_filter))


def evaluate_software_policies(options=None, dry_run=False):
    options = {
        'policy': None,
        'endpoint': None,
        'verbose': False,
        'resolve_missing': True,
        **(options or {}),
    }
    result = SoftwarePolicyEvaluationResult()
    now = timezone.now()
    policies = list(filter_policies(options['policy']).prefetch_related('exceptions'))
    endpoints = list(filter_endpoints(options['endpoint']))

    result.policies_evaluated = len(policies)
    result.endpoints_evaluated = len(endpoints)

    snapshots = {endpoint.id: latest_snapshot(endpoint) for endpoint in endpoints}

    context_manager = transaction.atomic()
    if dry_run:
        context_manager = transaction.atomic()

    with context_manager:
        for policy in policies:
            for endpoint in endpoints:
                try:
                    evaluate_policy_endpoint(
                        policy,
                        endpoint,
                        snapshots.get(endpoint.id),
                        result,
                        options,
                        now,
                        dry_run=dry_run,
                    )
                except Exception as exc:
                    result.errors += 1
                    result.dry_run_actions.append(f'error {endpoint.hostname}: {policy.name} - {exc}')
        if dry_run:
            transaction.set_rollback(True)

    return result
