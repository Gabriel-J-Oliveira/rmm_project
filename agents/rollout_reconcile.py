"""Read persisted rollout evidence and reconcile targets without dispatching work."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .job_progress import job_installed_version, job_stage, job_stale_info, job_target_version
from .models import (
    AgentJob,
    AgentJobResultReceipt,
    AgentOperationalStatus,
    AgentRolloutCampaign,
    AgentRolloutTarget,
    AuditEvent,
)
from .versioning import compare_versions


TERMINAL_TARGET_STATES = {'succeeded', 'failed', 'rolled_back', 'cancelled'}
FAILED_JOB_STATUSES = {
    AgentJob.STATUS_FAILED,
    AgentJob.STATUS_UNSUPPORTED,
    AgentJob.STATUS_INVALID_PARAMETERS,
    AgentJob.STATUS_INTERRUPTED,
    AgentJob.STATUS_EXPIRED,
    AgentJob.STATUS_TIMED_OUT,
    AgentJob.STATUS_DUPLICATE,
    AgentJob.STATUS_ROLLBACK_FAILED,
}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _health_confirmed(job, operational_status):
    result = _dict(job.result)
    health = _dict(result.get('health_check'))
    primary = bool(result.get('health_check_confirmed') or health.get('confirmed') or health.get('success'))
    operational = bool(
        operational_status
        and operational_status.health_check_confirmed
        and str(operational_status.update_job_id or '') == str(job.pk)
    )
    return primary or operational, 'final_result' if primary else ('operational_status' if operational else '')


def _binding_valid(target, job):
    if not job:
        return False
    metadata = _dict(_dict(job.payload).get('rollout_metadata'))
    expected = {
        'campaign_id': str(target.campaign_id),
        'wave_id': str(target.wave_id),
        'target_id': str(target.pk),
        'cohort_hash': target.campaign.cohort_hash,
    }
    return (
        job.job_type == AgentJob.TYPE_UPDATE_AGENT
        and job.endpoint_id == target.endpoint_id
        and job.agent_release_id == target.campaign.release_id
        and job.correlation_id == str(target.pk)
        and all(str(metadata.get(key, '')) == value for key, value in expected.items())
        and metadata.get('cohort_schema') == target.campaign.cohort_schema
        and metadata.get('wave_sequence') == target.wave.sequence
    )


def evaluate_rollout_target(target, *, receipt=None, operational_status=None, now=None):
    """Purely classify already-loaded evidence for one frozen rollout target."""
    now = now or timezone.now()
    job = target.agent_job
    target_version = job_target_version(job) if job else target.campaign.release.version
    installed_version = job_installed_version(job) if job else ''
    health_confirmed, health_source = _health_confirmed(job, operational_status) if job else (False, '')
    stale = job_stale_info(job, now=now) if job else {
        'is_stale': False, 'stale_reason': '', 'stale_since': None, 'expected_timeout_at': None,
    }
    endpoint_online = target.endpoint.status == target.endpoint.STATUS_ONLINE
    if target.endpoint.last_seen_at and now - target.endpoint.last_seen_at > timedelta(minutes=5):
        endpoint_online = False
    receipt_present = bool(
        receipt and job and job.result_id and receipt.result_id == job.result_id
        and receipt.job_id == job.pk and receipt.endpoint_id == target.endpoint_id
    )
    receipt_conflict = bool(receipt_present and receipt.conflict_count)
    binding_valid = _binding_valid(target, job)
    terminal = False
    desired_state = target.state
    classification = 'not_dispatched'
    reason = 'target_has_no_job'

    if not binding_valid and target.agent_job_id:
        classification, reason = 'binding_invalid', 'rollout_job_binding_invalid'
    elif job:
        if job.status in {AgentJob.STATUS_QUEUED, AgentJob.STATUS_SENT}:
            desired_state = 'queued'
            classification = 'queued' if job.status == AgentJob.STATUS_QUEUED else 'dispatched'
            reason = f'job_{job.status}'
        elif job.status == AgentJob.STATUS_RUNNING:
            desired_state, classification, reason = 'running', 'running', 'job_running'
        elif job.status == AgentJob.STATUS_ROLLED_BACK:
            desired_state, classification, reason, terminal = 'rolled_back', 'rolled_back', 'job_rolled_back', True
        elif job.status == AgentJob.STATUS_CANCELLED:
            desired_state, classification, reason, terminal = 'cancelled', 'cancelled', 'job_cancelled', True
        elif job.status in FAILED_JOB_STATUSES:
            desired_state, terminal = 'failed', True
            classification = 'rollback_failed' if job.status == AgentJob.STATUS_ROLLBACK_FAILED else job.status
            reason = f'job_{job.status}'
        elif job.status == AgentJob.STATUS_COMPLETED:
            version_matches = bool(target_version and installed_version and compare_versions(installed_version, target_version) == 0)
            endpoint_version_matches = bool(target_version and target.endpoint.agent_version and compare_versions(target.endpoint.agent_version, target_version) == 0)
            if job.exit_code not in (None, 0):
                desired_state, classification, reason, terminal = 'failed', 'failed', 'completed_with_nonzero_exit', True
            elif receipt_conflict:
                desired_state, classification, reason = 'running', 'receipt_conflict', 'result_receipt_conflict'
            elif not job.result_received_at or not job.result_id or not receipt_present:
                desired_state, classification, reason = 'running', 'waiting_result_receipt', 'completed_without_final_receipt'
            elif job.exit_code is None:
                desired_state, classification, reason = 'running', 'waiting_result_evidence', 'completed_without_exit_code'
            elif not version_matches or not endpoint_version_matches:
                desired_state, classification, reason = 'running', 'waiting_version', 'target_version_not_confirmed'
            elif not health_confirmed:
                desired_state, classification, reason = 'running', 'waiting_health', 'health_not_confirmed'
            else:
                desired_state, classification, reason, terminal = 'succeeded', 'succeeded', 'complete_evidence_confirmed', True
        else:
            desired_state, classification, reason = 'running', 'running', f'job_{job.status}'

    evidence_conflict = target.state in TERMINAL_TARGET_STATES and desired_state != target.state
    if target.state in TERMINAL_TARGET_STATES:
        terminal = True
        if evidence_conflict:
            classification, reason = 'terminal_evidence_conflict', 'terminal_target_conflicts_with_current_evidence'
        desired_state = target.state
    offline_post_update = bool(job and target.state not in TERMINAL_TARGET_STATES and not endpoint_online)
    return {
        'campaign_id': str(target.campaign_id), 'wave_id': str(target.wave_id) if target.wave_id else None,
        'target_id': str(target.pk), 'endpoint_id': str(target.endpoint_id),
        'job_id': str(target.agent_job_id) if target.agent_job_id else None,
        'current_state': target.state, 'job_status': job.status if job else None,
        'job_stage': job_stage(job) if job else None, 'classification': classification,
        'terminal': terminal, 'desired_state': desired_state,
        'target_version': target_version, 'installed_version': installed_version,
        'receipt_present': receipt_present, 'receipt_conflict_count': receipt.conflict_count if receipt_present else 0,
        'health_confirmed': health_confirmed, 'health_source': health_source,
        'endpoint_online': endpoint_online, 'offline_post_update': offline_post_update,
        'is_stale': stale['is_stale'], 'stale_reason': stale['stale_reason'],
        'reason_code': reason, 'binding_valid': binding_valid, 'evidence_conflict': evidence_conflict,
    }


def _load_campaign_evaluations(campaign, *, now=None, lock=False):
    targets = campaign.targets.select_related('campaign__release', 'wave', 'endpoint', 'agent_job__agent_release')
    if lock:
        # PostgreSQL cannot lock nullable joined rows; Campaign is locked separately.
        targets = targets.select_for_update(of=('self',))
    targets = list(targets.order_by('endpoint_id', 'pk'))
    job_ids = [target.agent_job_id for target in targets if target.agent_job_id]
    endpoint_ids = [target.endpoint_id for target in targets]
    receipts = defaultdict(list)
    for receipt in AgentJobResultReceipt.objects.filter(job_id__in=job_ids).order_by('received_at'):
        receipts[receipt.job_id].append(receipt)
    statuses = {status.endpoint_id: status for status in AgentOperationalStatus.objects.filter(endpoint_id__in=endpoint_ids)}
    evaluations = []
    for target in targets:
        receipt = next((item for item in receipts.get(target.agent_job_id, ()) if item.result_id == target.agent_job.result_id), None) if target.agent_job_id else None
        evaluations.append((target, evaluate_rollout_target(target, receipt=receipt, operational_status=statuses.get(target.endpoint_id), now=now)))
    return evaluations


def _rate(numerator, denominator):
    return round((numerator / denominator) * 100, 2) if denominator else 0.0


def _metrics(items, *, excluded=0):
    counts = Counter(item['desired_state'] for item in items)
    classes = Counter(item['classification'] for item in items)
    total = len(items) + excluded
    eligible = len(items)
    dispatched = sum(bool(item['job_id']) for item in items)
    terminal = sum(item['desired_state'] in TERMINAL_TARGET_STATES for item in items)
    failed = counts['failed']
    result = {
        'total': total, 'excluded': excluded, 'eligible_initial': eligible, 'dispatched': dispatched,
        'queued': counts['queued'], 'running': counts['running'], 'succeeded': counts['succeeded'],
        'failed': failed, 'rolled_back': counts['rolled_back'], 'cancelled': counts['cancelled'],
        'terminal': terminal, 'in_flight': counts['queued'] + counts['running'],
        'waiting_health': classes['waiting_health'], 'stalled': sum(item['is_stale'] for item in items),
        'offline_post_update': sum(item['offline_post_update'] for item in items),
        'rollback_failed': classes['rollback_failed'], 'receipt_conflict': classes['receipt_conflict'],
        'binding_invalid': classes['binding_invalid'],
    }
    result.update({
        'success_rate': _rate(result['succeeded'], dispatched),
        # rollback_failed already contributes to the failed Target state.
        'failure_rate': _rate(failed + result['rolled_back'], dispatched),
        'completion_rate': _rate(terminal, dispatched),
    })
    return result


def summarize_rollout_campaign(campaign, *, now=None):
    """Read-only campaign/wave metrics with bounded query count."""
    campaign = AgentRolloutCampaign.objects.select_related('release').get(pk=getattr(campaign, 'pk', campaign))
    pairs = _load_campaign_evaluations(campaign, now=now)
    active = [evaluation for target, evaluation in pairs if target.eligibility_at_selection]
    excluded = sum(not target.eligibility_at_selection for target, _ in pairs)
    by_wave = defaultdict(list)
    for target, evaluation in pairs:
        if target.wave_id:
            by_wave[target.wave_id].append(evaluation)
    return {
        'campaign_id': str(campaign.pk), 'metrics': _metrics(active, excluded=excluded),
        'waves': {str(wave.pk): _metrics(by_wave.get(wave.pk, [])) for wave in campaign.waves.order_by('sequence')},
        'targets': [evaluation for _, evaluation in pairs],
    }


def reconcile_rollout_campaign(campaign, *, now=None):
    """Idempotently persist Target outcomes; never mutates jobs, endpoints, waves or campaigns."""
    now = now or timezone.now()
    with transaction.atomic():
        campaign = AgentRolloutCampaign.objects.select_for_update().select_related('release').get(pk=getattr(campaign, 'pk', campaign))
        pairs = _load_campaign_evaluations(campaign, now=now, lock=True)
        changed, transitions, issues = [], [], []
        for target, evaluation in pairs:
            if evaluation['evidence_conflict'] or evaluation['classification'] == 'binding_invalid':
                issues.append({'target_id': str(target.pk), 'classification': evaluation['classification'], 'job_id': evaluation['job_id']})
            if target.state in TERMINAL_TARGET_STATES or target.state == evaluation['desired_state']:
                continue
            target.state = evaluation['desired_state']
            target.completed_at = now if target.state in TERMINAL_TARGET_STATES else None
            target.updated_at = now
            changed.append(target)
            transitions.append({'target_id': str(target.pk), 'job_id': evaluation['job_id'],
                                'from': evaluation['current_state'], 'to': target.state,
                                'classification': evaluation['classification']})
        if changed:
            AgentRolloutTarget.objects.bulk_update(changed, ['state', 'completed_at', 'updated_at'])
            AuditEvent.objects.create(event_type='rollout.reconciled', title='Rollout targets reconciled',
                actor_type=AuditEvent.ACTOR_SCHEDULER, actor_name='RolloutReconciler',
                metadata={'campaign_id': str(campaign.pk), 'transition_count': len(transitions), 'transitions': transitions})
        if issues:
            fingerprint = hashlib.sha256(json.dumps(issues, sort_keys=True).encode()).hexdigest()
            known = {event.metadata.get('fingerprint') for event in AuditEvent.objects.filter(
                event_type='rollout.reconcile_integrity', metadata__campaign_id=str(campaign.pk))}
            if fingerprint not in known:
                AuditEvent.objects.create(event_type='rollout.reconcile_integrity', title='Rollout evidence integrity issue',
                    severity=AuditEvent.SEVERITY_WARNING, actor_type=AuditEvent.ACTOR_SCHEDULER,
                    actor_name='RolloutReconciler', metadata={'campaign_id': str(campaign.pk), 'fingerprint': fingerprint, 'issues': issues})
        summary = summarize_rollout_campaign(campaign, now=now)
        summary['transition_count'] = len(transitions)
        summary['transitions'] = transitions
        return summary
