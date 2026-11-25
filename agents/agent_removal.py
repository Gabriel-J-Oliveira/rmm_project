"""Durable, server-side record of an authenticated agent-removal attempt."""

from django.utils import timezone

from .models import AgentRemovalAudit, AssetAgentLink, InventorySnapshot


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
        asset_id=link.asset_id if link else None, requested_by=uninstall_request.requested_by,
        authorized_by=uninstall_request.authorized_by, requested_at=uninstall_request.requested_at,
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
