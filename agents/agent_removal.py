"""Durable, server-side record of an authenticated agent-removal attempt."""

from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from .asset_registry import link_matches_endpoint
from .identity import lock_identity_registry, machine_id_matches, normalized_machine_id
from .internal_jobs import internal_capability
from .models import AgentJob, AgentMachine, AgentRemovalAudit, AgentUninstallRequest, AssetAgentLink, AutomationOccurrence, InventorySnapshot
from .rollout_models import AgentRolloutTarget


REMOVAL_CAPABILITY = 'verified_agent_removal:1'


def removal_block_reason(endpoint, *, fresh=False):
    if not endpoint.is_active or endpoint.has_terminal_lifecycle:
        return 'inactive_endpoint'
    try:
        machine_id = normalized_machine_id(endpoint.machine_id)
    except ValueError:
        machine_id = ''
    if not machine_id or machine_id_matches(machine_id).exclude(pk=endpoint.pk).exists():
        return 'endpoint_identity_conflict'
    heartbeat = InventorySnapshot.objects.filter(machine=endpoint, raw_payload__snapshot_source='heartbeat')
    raw = heartbeat.order_by('-received_at').values_list('raw_payload', flat=True).first() or {}
    agent = raw.get('agent')
    advertised = (raw.get('machine_id') == endpoint.machine_id and raw.get('agent_version') == endpoint.agent_version
                  and isinstance(agent, dict) and isinstance(agent.get('capabilities'), list)
                  and REMOVAL_CAPABILITY in agent['capabilities'])
    if not advertised or (fresh and not internal_capability(endpoint, REMOVAL_CAPABILITY)):
        return 'verified_removal_unsupported'
    if live_rollout_targets(endpoint).exists():
        return 'endpoint_in_live_rollout'
    return ''


def live_rollout_targets(endpoint):
    return AgentRolloutTarget.objects.filter(endpoint=endpoint).exclude(campaign__state__in=('completed', 'aborted'))


def removal_receipt_token(audit):
    return salted_hmac('nightowl.removal.receipt.v1', f'{audit.job_id}:{audit.request_id}:{audit.endpoint_id}',
                       algorithm='sha256').hexdigest()


def _auth_identity_stamp(endpoint):
    return salted_hmac('nightowl.removal.endpoint.v1', f'{endpoint.pk}:{endpoint.agent_token_hash}',
                       algorithm='sha256').hexdigest()


def removal_identity_matches(audit, endpoint):
    return (audit.endpoint_id == endpoint.pk and audit.machine_id == endpoint.machine_id
            and constant_time_compare(audit.auth_identity_stamp, _auth_identity_stamp(endpoint)))


def receive_removal_receipt(job_id, result_id, payload, digest, token):
    if not isinstance(job_id, str) or len(token) != 64:
        return {'error': 'removal_receipt_invalid'}, 403
    try:
        key = UUID(job_id)
    except ValueError:
        return {'error': 'removal_receipt_invalid'}, 403
    audit = AgentRemovalAudit.objects.filter(job_id=key, requested_at__gte=timezone.now() - timedelta(days=7)).first()
    if (not audit or result_id != f'uninstall-{audit.job_id}'
            or not constant_time_compare(removal_receipt_token(audit), token)):
        return {'error': 'removal_receipt_invalid'}, 403
    lock_identity_registry()
    audit = AgentRemovalAudit.objects.select_for_update().get(pk=audit.pk)
    if audit.outcome == 'removed':
        if not constant_time_compare(digest, audit.receipt_digest):
            return {'error': 'removal_receipt_conflict'}, 409
        return {'status': 'ok', 'removed': True, 'duplicate': True, 'job_id': job_id}, 200
    job = AgentJob.objects.select_for_update().filter(pk=key, endpoint_id=audit.endpoint_id).first()
    endpoint = AgentMachine.objects.select_for_update().filter(pk=audit.endpoint_id).first()
    if (not job or not endpoint or not removal_identity_matches(audit, endpoint) or not job.dispatched_at
            or job.job_type != AgentJob.TYPE_UNINSTALL_AGENT or job.payload.get('verified_removal') is not True
            or job.status not in ('sent', 'running', 'timed_out', 'failed')
            or AgentRemovalAudit.objects.filter(endpoint_id=endpoint.pk, requested_at__gt=audit.requested_at).exists()):
        return {'error': 'removal_identity_changed'}, 409
    if payload.get('status') == 'failed':
        now = timezone.now()
        AgentRemovalAudit.objects.filter(pk=audit.pk).update(
            outcome='cleanup_unverified', result_id=result_id, agent_error_code='REMOVAL_CLEANUP_NOT_CONFIRMED',
            finished_at=now,
        )
        AgentJob.objects.filter(pk=job.pk).update(status='failed', error_code='REMOVAL_CLEANUP_NOT_CONFIRMED',
                                                  finished_at=now, result_received_at=now)
        AgentUninstallRequest.objects.filter(pk=audit.pk).update(
            status=AgentUninstallRequest.STATUS_FAILED, completed_at=now, error_code='REMOVAL_CLEANUP_NOT_CONFIRMED',
        )
        return {'status': 'ok', 'removed': False, 'retryable': True}, 200
    if not cleanup_is_verified(job, payload, result_id):
        return {'error': 'cleanup_unverified'}, 409
    if not finalize_verified_removal(endpoint, job, result_id, digest):
        return {'error': 'removal_dependencies_changed'}, 409
    return {'status': 'ok', 'removed': True, 'job_id': job_id}, 200


def cleanup_is_verified(job, payload, result_id):
    proof = payload.get('result')
    return (payload.get('status') == 'completed' and bool(job.dispatched_at)
            and job.status in ('sent', 'running', 'timed_out', 'failed')
            and result_id == f'uninstall-{job.pk}' and type(payload.get('exit_code')) is int
            and payload['exit_code'] == 0 and isinstance(proof, dict)
            and type(proof.get('cleanup_version')) is int and proof['cleanup_version'] == 1
            and proof.get('mode') == 'purge' and proof.get('machine_id') == job.endpoint.machine_id
            and proof.get('cleanup_verified') is True and proof.get('binary_removed') is True
            and proof.get('persistent_data_preserved') is False)


@transaction.atomic
def finalize_verified_removal(endpoint, job, result_id, digest):
    audit = AgentRemovalAudit.objects.select_for_update().get(job_id=job.pk, endpoint_id=endpoint.pk)
    if (audit.machine_id != endpoint.machine_id or live_rollout_targets(endpoint).exists()
            or endpoint.jobs.exclude(pk=job.pk).filter(status__in=('sent', 'running')).exists()):
        return False
    # PROTECT edges are scoped to this installation. Never remove another
    # endpoint's row even if an inconsistent FK points to this endpoint's job.
    jobs = AgentJob.objects.filter(endpoint=endpoint)
    if (AutomationOccurrence.objects.filter(job__in=jobs).exclude(endpoint=endpoint).exists()
            or AgentRolloutTarget.objects.filter(agent_job__in=jobs).exclude(endpoint=endpoint).exists()):
        return False
    now = timezone.now()
    AssetAgentLink.objects.filter(endpoint=endpoint, ended_at__isnull=True).update(ended_at=now)
    audit.outcome = 'removed'
    audit.finished_at = now
    audit.result_id = result_id
    audit.binary_removed = True
    audit.persistent_data_preserved = False
    audit.receipt_digest = digest
    audit.save(update_fields=['outcome', 'finished_at', 'result_id', 'binary_removed', 'persistent_data_preserved',
                              'receipt_digest'])
    AutomationOccurrence.objects.filter(endpoint=endpoint).delete()
    AgentRolloutTarget.objects.filter(endpoint=endpoint).delete()
    endpoint.delete()
    return True


def record_purge_request(endpoint, uninstall_request, job):
    link = AssetAgentLink.objects.filter(endpoint=endpoint, ended_at__isnull=True).first()
    user_observation = InventorySnapshot.objects.filter(machine=endpoint).exclude(
        logged_user='',
    ).order_by('-received_at', '-pk').values('logged_user', 'collected_at').first()
    observed_user = (user_observation or {}).get('logged_user') or endpoint.last_logged_user or None
    observed_at = (user_observation or {}).get('collected_at')
    return AgentRemovalAudit.objects.create(
        request_id=uninstall_request.pk, job_id=job.pk, correlation_id=job.correlation_id,
        endpoint_id=endpoint.pk, machine_id=endpoint.machine_id, hostname=endpoint.hostname,
        asset_id=link.asset_id if link and link_matches_endpoint(link, endpoint) else None,
        requested_by=uninstall_request.requested_by,
        authorized_by=uninstall_request.authorized_by, requested_at=uninstall_request.requested_at,
        auth_identity_stamp=_auth_identity_stamp(endpoint) if job.payload.get('verified_removal') is True else '',
        observed_user=observed_user, observed_user_at=observed_at,
        observed_user_source=('inventory_snapshot' if user_observation else 'agent_machine' if observed_user else ''),
    )


def record_purge_result(job, result_id):
    evidence = job.result if isinstance(job.result, dict) else {}
    # The legacy receipt does not attest to every required resource. Preserve
    # what it reported without treating it as authorization for endpoint deletion.
    AgentRemovalAudit.objects.filter(job_id=job.pk, finished_at__isnull=True).update(
        outcome='cleanup_unverified' if job.status == 'completed' else job.status,
        finished_at=timezone.now(), result_id=result_id or '',
        agent_error_code=str(job.error_code or '')[:80],
        binary_removed=evidence.get('binary_removed') if isinstance(evidence.get('binary_removed'), bool) else None,
        persistent_data_preserved=(evidence.get('persistent_data_preserved')
                                   if isinstance(evidence.get('persistent_data_preserved'), bool) else None),
    )
