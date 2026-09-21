"""One periodic round; PostgreSQL session advisory lock and per-campaign transactions."""
from contextlib import contextmanager

from django.db import connection
from django.utils import timezone

from .fleet_policy import PolicyContractError
from .models import AgentRolloutCampaign
from .rollout_dispatch import dispatch_rollout_campaign
from .rollout_planning import rollout_orchestration_enabled

RUNNER_LOCK_ID = 564987321


@contextmanager
def rollout_runner_lock():
    if connection.vendor != 'postgresql':
        raise PolicyContractError('rollout_runner_requires_postgresql', status=409)
    if connection.in_atomic_block:
        raise RuntimeError('Periodic runner requires a dedicated autocommit connection')
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)', [RUNNER_LOCK_ID])
        acquired = cursor.fetchone()[0]
    try:
        yield acquired
    finally:
        if acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_unlock(%s)', [RUNNER_LOCK_ID])
            except Exception:
                connection.close()  # Closing the owning session releases the lock.
                raise


def run_rollout_round(*, now=None):
    if not rollout_orchestration_enabled():
        raise PolicyContractError('rollout_orchestrator_disabled', status=409)
    now = now or timezone.now()
    if timezone.is_naive(now):
        raise PolicyContractError('aware_timestamp_required')
    with rollout_runner_lock() as acquired:
        if not acquired:
            return {'status': 'already_running', 'campaigns': []}
        ids = list(AgentRolloutCampaign.objects.filter(state='running', current_wave__state='running').order_by('pk').values_list('pk', flat=True))
        summaries = []
        for pk in ids:
            try:
                result = dispatch_rollout_campaign(pk, now=now)
                summaries.append({key: result[key] for key in ('campaign_id', 'wave_id', 'created_count', 'job_ids')})
            except PolicyContractError as exc:
                summaries.append({'campaign_id': str(pk), 'status': 'blocked', 'reason_code': str(exc)})
        return {'status': 'completed', 'campaigns': summaries}
