"""Re-arm due, explicitly authorized Stable retries within the same campaign."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import AgentJob, AgentRolloutAttempt, AgentRolloutCampaign, AgentRolloutTarget, AuditEvent


@transaction.atomic
def prepare_due_stable_retries(campaign_id, *, now=None):
    now = now or timezone.now()
    campaign = AgentRolloutCampaign.objects.select_for_update().get(pk=campaign_id)
    if campaign.state != 'running' or not campaign.release_snapshot.get('stable_desktop_profile'):
        return 0
    candidate_attempts = AgentRolloutAttempt.objects.filter(
        target__campaign=campaign, outcome__in=['retry_scheduled', 'awaiting_condition'],
        ordinal__lt=4, job__status=AgentJob.STATUS_FAILED,
    ).select_related('target').order_by('target__endpoint_id', 'ordinal')
    target_ids = list(candidate_attempts.values_list('target_id', flat=True).distinct())
    # Keep the same Campaign -> Target -> Attempt lock order as reconciliation
    # so a retry scheduler cannot deadlock a concurrent result reconciliation.
    locked_targets = {
        target.pk: target for target in AgentRolloutTarget.objects.select_for_update(of=('self',))
        .filter(pk__in=target_ids).order_by('endpoint_id', 'pk')
    }
    attempts = list(AgentRolloutAttempt.objects.select_for_update().filter(
        pk__in=candidate_attempts.values('pk'), target_id__in=locked_targets,
    ).select_related('job').order_by('target__endpoint_id', 'ordinal'))
    for attempt in attempts:
        target = locked_targets[attempt.target_id]
        if target.state != 'running':
            continue
        if attempt.outcome == 'retry_scheduled':
            if not attempt.retry_after or attempt.retry_after > now:
                continue
        else:
            from .models import InventorySnapshot
            snapshot = InventorySnapshot.objects.filter(machine_id=target.endpoint_id,
                received_at__gte=now - timedelta(hours=24)).order_by('-received_at').first()
            required = campaign.release.size * 2
            if not snapshot or not required or not any(
                type(row.get('free_bytes')) is int and row['free_bytes'] >= required
                for row in (snapshot.disks or []) if isinstance(row, dict)
            ):
                continue
        if AgentJob.objects.filter(endpoint_id=target.endpoint_id, job_type__in=('update_agent', 'repair_agent', 'uninstall_agent'),
                                   status__in=('queued', 'sent', 'running')).exclude(pk=target.agent_job_id).exists():
            continue
        attempt.outcome = 'retry_ready'
        attempt.reason_code = 'backoff_elapsed_ready_for_dispatch'
        attempt.save(update_fields=['outcome', 'reason_code'])
        AuditEvent.objects.create(event_type='rollout.retry_ready', title='Stable retry ready for dispatch',
            actor_type=AuditEvent.ACTOR_SCHEDULER, actor_name='RolloutRetryScheduler',
            metadata={'campaign_id': str(campaign.pk), 'target_id': str(target.pk),
                      'attempt_id': str(attempt.pk), 'ordinal': attempt.ordinal + 1,
                      'previous_job_id': str(attempt.job_id), 'release_id': str(campaign.release_id)})
    return sum(1 for attempt in attempts if attempt.outcome == 'retry_ready')
