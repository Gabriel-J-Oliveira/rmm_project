"""Administrative timeout of stale sent or running jobs."""

from django.db import transaction
from django.utils import timezone

from .job_progress import FINAL_MODEL_STATUSES, UPDATE_PROGRESS_BY_STAGE, job_stale_info, sanitize_job_value
from .models import AgentJob, AuditEvent


ALLOWED_STALE_REASONS = {
    AgentJob.STATUS_SENT: {'timeout_exceeded', 'dispatched_too_long'},
    AgentJob.STATUS_RUNNING: {'timeout_exceeded', 'running_without_update'},
}
TERMINAL_STAGES = FINAL_MODEL_STATUSES | {'success', 'already_current', 'no_update_available', 'target_not_installed'}
INTERMEDIATE_STAGES = set(UPDATE_PROGRESS_BY_STAGE) - TERMINAL_STAGES | {'running'}


def _result_evidence_kind(value):
    if not value:
        return 'none'
    if not isinstance(value, dict):
        return 'unknown'
    markers = [str(value[key]).strip().lower() for key in ('status', 'stage', 'current_stage', 'currentStage', 'update_status')
               if value.get(key) is not None]
    if any(marker in TERMINAL_STAGES for marker in markers):
        return 'terminal'
    if markers and all(marker in INTERMEDIATE_STAGES for marker in markers):
        return 'intermediate'
    return 'unknown'


def _receipt_evidence_kind(receipt, job):
    if receipt.conflict_count or receipt.endpoint_id != job.endpoint_id:
        return 'unknown'
    payload = receipt.first_payload
    if not isinstance(payload, dict) or not payload:
        return 'unknown'
    if payload.get('job_id') and str(payload['job_id']) != str(job.pk):
        return 'unknown'
    status = str(payload.get('status') or '').strip().lower()
    if status in TERMINAL_STAGES:
        return 'terminal'
    if status not in {'queued', 'pending', 'sent', 'dispatched', 'running'}:
        return 'unknown'
    result_kind = _result_evidence_kind(payload.get('result'))
    return 'intermediate' if result_kind in {'none', 'intermediate'} else result_kind


def evaluate_stale_sent_job_timeout(job, *, now=None):
    now = now or timezone.now()
    stale = job_stale_info(job, now=now)
    result_exists = bool(job.result or job.result_id or job.result_received_at)
    receipts = list(job.result_receipts.all()) if job.status == AgentJob.STATUS_RUNNING else []
    receipt_count = len(receipts) if job.status == AgentJob.STATUS_RUNNING else job.result_receipts.count()
    result_kind = _result_evidence_kind(job.result) if job.status == AgentJob.STATUS_RUNNING else 'not_evaluated'
    receipt_kinds = [_receipt_evidence_kind(receipt, job) for receipt in receipts]
    intermediate_receipt_count = receipt_kinds.count('intermediate')
    blockers = []
    if job.status not in ALLOWED_STALE_REASONS:
        blockers.append('job_not_sent')
    if not stale['is_stale'] or stale['stale_reason'] not in ALLOWED_STALE_REASONS.get(job.status, set()):
        blockers.append('job_not_stale')
    if job.status == AgentJob.STATUS_SENT:
        if result_exists:
            blockers.append('result_exists')
        if receipt_count:
            blockers.append('receipt_exists')
    elif job.status == AgentJob.STATUS_RUNNING:
        if job.finished_at:
            blockers.append('finished_at_exists')
        if result_kind in {'terminal', 'unknown'} or (job.result_received_at and not job.result and not receipts):
            blockers.append('result_not_intermediate')
        if any(kind != 'intermediate' for kind in receipt_kinds):
            blockers.append('receipt_not_intermediate')
        if job.result_id and not any(receipt.result_id == job.result_id for receipt in receipts):
            blockers.append('result_receipt_missing')
    return {
        'job_id': str(job.pk), 'endpoint_id': str(job.endpoint_id),
        'status': job.status, 'job_type': job.job_type,
        'stale': stale['is_stale'], 'stale_reason': stale['stale_reason'],
        'stale_since': stale['stale_since'].isoformat() if stale['stale_since'] else None,
        'expected_timeout_at': stale['expected_timeout_at'].isoformat() if stale['expected_timeout_at'] else None,
        'result_exists': result_exists, 'receipt_count': receipt_count,
        'result_evidence_kind': result_kind, 'intermediate_result_present': result_kind == 'intermediate',
        'intermediate_receipt_count': intermediate_receipt_count,
        'terminal_evidence_present': result_kind == 'terminal' or 'terminal' in receipt_kinds,
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
    previous_status = job.status
    job.status = AgentJob.STATUS_TIMED_OUT
    job.finished_at = now
    job.error_code = 'JOB_RUNNING_TIMEOUT' if previous_status == AgentJob.STATUS_RUNNING else 'JOB_TIMEOUT'
    job.error_message = (
        'Timeout administrativo: job running excedeu o limite ou ficou sem atualizacao; '
        'nenhum resultado terminal valido foi recebido.'
        if previous_status == AgentJob.STATUS_RUNNING else
        'Timeout administrativo: nenhum resultado recebido apos o prazo do job.'
    )
    job.save(update_fields=['status', 'finished_at', 'error_code', 'error_message', 'updated_at'])
    safe_reason = sanitize_job_value(reason.strip(), max_string=1000)
    AuditEvent.objects.create(
        event_type='job.admin_timeout', title=(
            'Sent job timed out administratively' if previous_status == AgentJob.STATUS_SENT
            else 'Running job timed out administratively'
        ),
        severity=AuditEvent.SEVERITY_WARNING,
        actor_type=AuditEvent.ACTOR_SYSTEM, actor_name='reconcile_stale_agent_job',
        endpoint_id=job.endpoint_id,
        description=safe_reason,
        metadata={'job_id': str(job.pk), 'endpoint_id': str(job.endpoint_id), 'job_type': job.job_type,
                  'previous_status': previous_status, 'status': job.status, 'new_status': job.status,
                  'stale_reason': decision['stale_reason'], 'stale_since': decision['stale_since'],
                  'expected_timeout_at': decision['expected_timeout_at'], 'administrative_reason': safe_reason,
                  'intermediate_result_present': decision['intermediate_result_present'],
                  'intermediate_receipt_count': decision['intermediate_receipt_count']},
    )
    return {**decision, 'status': job.status, 'eligible_for_timeout': False,
            'proposed_status': None, 'blockers': ['job_not_sent'], 'applied': True}
