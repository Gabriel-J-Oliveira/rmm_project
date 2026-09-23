"""Conservative reconciliation of legacy endpoints without lifecycle metadata."""

import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .job_progress import sanitize_job_value
from .models import AgentJob, AgentMachine, AuditEvent, InventorySnapshot
from .versioning import normalize_agent_version, parse_semver


ACTIVE_LIFECYCLE_JOBS = ('update_agent', 'repair_agent', 'uninstall_agent')
FRESHNESS_SECONDS = 900


def evaluate_legacy_lifecycle_reconciliation(endpoint, *, now=None):
    now = now or timezone.now()
    lifecycle = (endpoint.agent_lifecycle_status or '').strip().lower()
    evidence = {
        'active': endpoint.is_active,
        'online': endpoint.status == AgentMachine.STATUS_ONLINE,
        'last_seen_fresh': bool(
            endpoint.last_seen_at and timedelta(0) <= now - endpoint.last_seen_at <= timedelta(seconds=FRESHNESS_SECONDS)
        ),
        'identity_valid': False,
        'identity_unique': False,
        'agent_version_valid': bool(endpoint.agent_version and parse_semver(endpoint.agent_version)),
        'agent_install_reported': bool(endpoint.agent_install_path and endpoint.agent_mode),
        'authenticated_heartbeat_fresh': False,
        'heartbeat_identity_matches': False,
        'heartbeat_version_matches': False,
        'active_lifecycle_jobs': endpoint.jobs.filter(
            job_type__in=ACTIVE_LIFECYCLE_JOBS, status__in=['queued', 'sent', 'running']
        ).count(),
    }
    try:
        identity = uuid.UUID(endpoint.machine_id)
        evidence['identity_valid'] = identity.int != 0
    except (ValueError, TypeError, AttributeError):
        pass
    if evidence['identity_valid']:
        evidence['identity_unique'] = not AgentMachine.objects.filter(
            machine_id__iexact=endpoint.machine_id
        ).exclude(pk=endpoint.pk).exists()

    for snapshot in InventorySnapshot.objects.filter(machine=endpoint).order_by('-received_at')[:30]:
        raw = snapshot.raw_payload if isinstance(snapshot.raw_payload, dict) else {}
        if not raw.get('heartbeat_at'):
            continue
        try:
            heartbeat_at = parse_datetime(str(raw['heartbeat_at']))
        except ValueError:
            heartbeat_at = None
        evidence['authenticated_heartbeat_fresh'] = bool(
            heartbeat_at and timezone.is_aware(heartbeat_at)
            and timedelta(0) <= now - heartbeat_at <= timedelta(seconds=FRESHNESS_SECONDS)
            and timedelta(0) <= now - snapshot.received_at <= timedelta(seconds=FRESHNESS_SECONDS)
        )
        evidence['heartbeat_identity_matches'] = raw.get('machine_id') == endpoint.machine_id
        agent = raw.get('agent') if isinstance(raw.get('agent'), dict) else {}
        reported_version = raw.get('agent_version') or agent.get('version')
        evidence['heartbeat_version_matches'] = bool(
            reported_version and normalize_agent_version(reported_version) == endpoint.agent_version
        )
        break

    blockers = []
    if lifecycle in {'uninstalled', 'purged'}:
        blockers.append('terminal_lifecycle')
    elif lifecycle == 'installed':
        blockers.append('already_installed')
    elif lifecycle not in {'', 'unknown'}:
        blockers.append('unsupported_lifecycle')
    for name, value in evidence.items():
        if name == 'active_lifecycle_jobs':
            if value:
                blockers.append(name)
        elif not value:
            blockers.append(name)
    return {
        'endpoint_id': str(endpoint.pk),
        'current_lifecycle': endpoint.agent_lifecycle_status or '',
        'proposed_lifecycle': 'installed' if lifecycle in {'', 'unknown'} else endpoint.agent_lifecycle_status,
        'eligible_for_reconciliation': not blockers,
        'evidence': evidence,
        'blockers': blockers,
    }


@transaction.atomic
def apply_legacy_lifecycle_reconciliation(endpoint_id, *, reason, now=None):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('reason_required')
    now = now or timezone.now()
    endpoint = AgentMachine.objects.select_for_update().get(pk=endpoint_id)
    decision = evaluate_legacy_lifecycle_reconciliation(endpoint, now=now)
    if decision['current_lifecycle'] == 'installed':
        return decision
    if not decision['eligible_for_reconciliation']:
        return decision
    endpoint.agent_lifecycle_status = 'installed'
    endpoint.save(update_fields=['agent_lifecycle_status', 'updated_at'])
    AuditEvent.objects.create(
        event_type='agent.lifecycle_legacy_reconciled',
        title='Legacy agent lifecycle reconciled',
        actor_type=AuditEvent.ACTOR_SYSTEM,
        actor_name='reconcile_agent_lifecycle',
        endpoint=endpoint,
        description=sanitize_job_value(reason.strip(), max_string=1000),
        metadata={
            'endpoint_id': str(endpoint.pk),
            'previous_lifecycle': decision['current_lifecycle'],
            'new_lifecycle': 'installed',
            'evidence': decision['evidence'],
        },
    )
    return {**decision, 'current_lifecycle': 'installed', 'eligible_for_reconciliation': False,
            'blockers': ['already_installed'], 'applied': True}
