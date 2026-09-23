"""Administrative timeout of sent jobs with no result evidence."""

from django.db import transaction
from django.utils import timezone

from .job_progress import job_stale_info, sanitize_job_value
from .models import AgentJob, AuditEvent


ALLOWED_STALE_REASONS = {'timeout_exceeded', 'dispatched_too_long'}


def evaluate_stale_sent_job_timeout(job, *, now=None):
    now = now or timezone.now()
    stale = job_stale_info(job, now=now)
    result_exists = bool(job.result or job.result_id or job.result_received_at)
    receipt_count = job.result_receipts.count()
    blockers = []
    if job.status != AgentJob.STATUS_SENT:
        blockers.append('job_not_sent')
    if not stale['is_stale'] or stale['stale_reason'] not in ALLOWED_STALE_REASONS:
        blockers.append('job_not_stale')
    if result_exists:
        blockers.append('result_exists')
    if receipt_count:
        blockers.append('receipt_exists')
    return {
        'job_id': str(job.pk), 'endpoint_id': str(job.endpoint_id),
        'status': job.status, 'job_type': job.job_type,
        'stale': stale['is_stale'], 'stale_reason': stale['stale_reason'],
        'stale_since': stale['stale_since'].isoformat() if stale['stale_since'] else None,
        'expected_timeout_at': stale['expected_timeout_at'].isoformat() if stale['expected_timeout_at'] else None,
        'result_exists': result_exists, 'receipt_count': receipt_count,
        'eligible_for_timeout': not blockers,
        'proposed_status': AgentJob.STATUS_TIMED_OUT if not blockers else None,
        'blockers': blockers,
    }


@transaction.atomic
def apply_stale_sent_job_timeout(job_id, *, reason, now=None):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('reason_required')
    now = now or timezone.now()
    job = AgentJob.objects.select_for_update().get(pk=job_id)
    decision = evaluate_stale_sent_job_timeout(job, now=now)
    if not decision['eligible_for_timeout']:
        return decision
    job.status = AgentJob.STATUS_TIMED_OUT
    job.finished_at = now
    job.error_code = 'JOB_TIMEOUT'
    job.error_message = 'Timeout administrativo: nenhum resultado recebido apos o prazo do job.'
    job.save(update_fields=['status', 'finished_at', 'error_code', 'error_message', 'updated_at'])
    AuditEvent.objects.create(
        event_type='job.admin_timeout', title='Sent job timed out administratively',
        severity=AuditEvent.SEVERITY_WARNING,
        actor_type=AuditEvent.ACTOR_SYSTEM, actor_name='reconcile_stale_agent_job',
        endpoint_id=job.endpoint_id,
        description=sanitize_job_value(reason.strip(), max_string=1000),
        metadata={'job_id': str(job.pk), 'job_type': job.job_type,
                  'previous_status': AgentJob.STATUS_SENT, 'status': job.status,
                  'stale_reason': decision['stale_reason'], 'stale_since': decision['stale_since']},
    )
    return {**decision, 'status': job.status, 'eligible_for_timeout': False,
            'proposed_status': None, 'blockers': ['job_not_sent'], 'applied': True}
