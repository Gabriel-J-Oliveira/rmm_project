"""Conservative retry classification for Stable rollout attempts."""
from datetime import timedelta

from django.conf import settings


RETRYABLE_PRE_EXECUTION_CODES = {
    # UPDATE_DOWNLOAD_FAILED is emitted by the Windows updater before the
    # install/staging phase. Generic transport errors are intentionally not
    # enough to prove that the updater did not begin execution.
    'UPDATE_DOWNLOAD_FAILED',
}


def retry_delay(ordinal):
    """Delay before retry ordinal 2, 3 or 4; ordinal 1 is the initial attempt."""
    delays = list(settings.NIGHTOWL_STABLE_ROLLOUT_RETRY_DELAYS_SECONDS)
    if len(delays) != 3 or any(type(value) is not int or value < 1 for value in delays):
        raise ValueError('Stable retry delays must contain three positive integer seconds.')
    return timedelta(seconds=delays[ordinal - 2]) if 2 <= ordinal <= 4 else None


def classify_retry_evidence(job):
    """Only explicit, terminal pre-execution errors permit automatic retry."""
    code = (job.error_code or '').strip().upper()
    if job.status == 'rolled_back':
        return 'rolled_back', 'operator_intervention_required'
    if code in {'INSUFFICIENT_DISK_SPACE', 'DISK_SPACE_INSUFFICIENT'}:
        return 'awaiting_condition', 'space_insufficient'
    if code in RETRYABLE_PRE_EXECUTION_CODES and job.status == 'failed' and job.result_received_at:
        return 'retryable', code.lower()
    if job.status in {'timed_out', 'expired', 'interrupted'} or not job.result_received_at:
        return 'inconclusive', 'execution_state_unknown'
    if code in {'SIGNATURE_INVALID', 'IDENTITY_MISMATCH', 'ENDPOINT_IDENTITY_CHANGED'}:
        return 'permanent_failure', code.lower()
    if job.status in {'failed', 'unsupported', 'invalid_parameters', 'rollback_failed'}:
        return 'permanent_failure', code.lower() or f'job_{job.status}'
    return 'inconclusive', 'insufficient_retry_evidence'
