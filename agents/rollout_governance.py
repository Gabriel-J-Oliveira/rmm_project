"""Rollout governance decisions and transitions; never dispatches AgentJobs."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from contextlib import contextmanager

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from .fleet_policy import PolicyContractError
from .models import AgentOperationalStatus, AgentRolloutCampaign, AgentRolloutTarget, AgentRolloutWave, AuditEvent
from .rollout_models import default_auto_pause_policy
from .rollout_planning import (
    build_rollout_planning_context,
    evaluate_rollout_target_dispatch_safety,
    release_safety,
    validate_campaign_release_contract,
)
from .rollout_reconcile import reconcile_rollout_campaign, summarize_rollout_campaign
from .versioning import compare_versions

POLICY_KEYS = set(default_auto_pause_policy())
THRESHOLD_KEYS = POLICY_KEYS - {'schema', 'enabled'}
HARD_STOPS = (
    ('binding_invalid', 'auto_pause_binding_invalid'),
    ('receipt_conflict', 'auto_pause_receipt_conflict'),
    ('rollback_failed', 'auto_pause_rollback_failed'),
)
NORMAL_STOPS = (
    ('failed', 'failed_count', 'auto_pause_failed'),
    ('rolled_back', 'rolled_back_count', 'auto_pause_rolled_back'),
    ('cancelled', 'cancelled_count', 'auto_pause_cancelled'),
    ('stalled', 'stalled_count', 'auto_pause_stalled'),
    ('offline_post_update', 'offline_post_update_count', 'auto_pause_offline'),
)
GOVERNANCE_LOCK_ID = 564987322


def governance_enabled():
    return bool(getattr(settings, 'NIGHTOWL_ROLLOUT_GOVERNANCE_ENABLED', False))


def validate_auto_pause_policy(value):
    policy = default_auto_pause_policy() if value is None else value
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        raise PolicyContractError('invalid_auto_pause_policy')
    if policy.get('schema') != 1 or type(policy.get('enabled')) is not bool:
        raise PolicyContractError('invalid_auto_pause_policy')
    if any(type(policy.get(key)) is not int or policy[key] < 1 for key in THRESHOLD_KEYS):
        raise PolicyContractError('invalid_auto_pause_policy')
    return dict(policy)


def observation_elapsed_seconds(wave, *, now):
    elapsed = int(wave.observation_accumulated_seconds or 0)
    if wave.state == 'observing' and wave.observation_resumed_at:
        elapsed += max(0, int((now - wave.observation_resumed_at).total_seconds()))
    return elapsed


def build_wave_observation_summary(campaign, wave, *, now):
    targets = list(AgentRolloutTarget.objects.filter(campaign=campaign, wave=wave, state='succeeded')
                   .select_related('endpoint', 'agent_job').order_by('endpoint_id', 'pk'))
    statuses = {item.endpoint_id: item for item in AgentOperationalStatus.objects.filter(
        endpoint_id__in=[target.endpoint_id for target in targets])}
    risks = []
    for target in targets:
        endpoint = target.endpoint
        reason = ''
        if not endpoint.is_active:
            reason = 'observation_endpoint_inactive'
        elif endpoint.agent_lifecycle_status != 'installed':
            reason = 'observation_lifecycle_changed'
        elif endpoint.machine_id != target.endpoint_machine_id_snapshot:
            reason = 'observation_identity_changed'
        elif endpoint.status != 'online':
            reason = 'observation_endpoint_offline'
        elif not endpoint.last_seen_at or endpoint.last_seen_at > now or (now - endpoint.last_seen_at).total_seconds() > campaign.freshness_seconds:
            reason = 'observation_endpoint_stale'
        elif compare_versions(endpoint.agent_version, campaign.release.version) != 0:
            reason = 'observation_version_drift'
        else:
            status = statuses.get(endpoint.pk)
            if status and status.health_indicator in {'critical', 'offline'}:
                reason = 'observation_health_critical'
            elif status and status.installed_version and compare_versions(status.installed_version, campaign.release.version) != 0:
                reason = 'observation_version_drift'
            elif status and status.update_job_id and str(status.update_job_id) != str(target.agent_job_id):
                reason = 'observation_operational_job_mismatch'
        if reason:
            risks.append({'target_id': str(target.pk), 'endpoint_id': str(endpoint.pk), 'reason_code': reason})
    elapsed = observation_elapsed_seconds(wave, now=now)
    required = wave.minimum_observation_seconds
    return {
        'healthy': not risks and len(targets) == wave.target_count,
        'checked_targets': len(targets), 'risk_count': len(risks),
        'reason_counts': dict(sorted(Counter(item['reason_code'] for item in risks).items())),
        'risks': risks, 'elapsed_seconds': elapsed, 'required_seconds': required,
        'remaining_seconds': max(0, required - elapsed),
    }


def evaluate_rollout_governance(campaign, *, now=None, summary=None):
    """Return a read-only governance decision from persisted reconciliation evidence."""
    now = now or timezone.now()
    if timezone.is_naive(now):
        raise ValueError('Governance requires an aware timestamp')
    campaign = AgentRolloutCampaign.objects.select_related('release', 'current_wave').get(pk=getattr(campaign, 'pk', campaign))
    policy = validate_auto_pause_policy(campaign.auto_pause_policy)
    summary = summary or summarize_rollout_campaign(campaign, now=now)
    wave = campaign.current_wave
    base = {
        'campaign_id': str(campaign.pk), 'campaign_state': campaign.state,
        'wave_id': str(wave.pk) if wave else None, 'wave_state': wave.state if wave else None,
        'decision': 'continue', 'reason_code': 'no_active_wave', 'auto_pause_required': False,
        'can_enter_observation': False, 'can_complete_wave': False, 'can_complete_campaign': False,
        'policy': policy, 'metrics': summary['metrics'], 'wave_metrics': {}, 'observation': {},
    }
    if campaign.state == 'paused':
        base.update(decision='already_paused', reason_code='campaign_paused')
        return base
    if campaign.state in {'completed', 'aborted'}:
        base.update(decision='terminal', reason_code=f'campaign_{campaign.state}')
        return base
    if campaign.state != 'running' or not wave:
        return base
    metrics = summary['waves'].get(str(wave.pk), {})
    base['wave_metrics'] = metrics
    for metric, reason in HARD_STOPS:
        if metrics.get(metric, 0) >= 1:
            base.update(decision='auto_pause', reason_code=reason, auto_pause_required=True,
                        evidence={'metric': metric, 'observed_count': metrics[metric], 'threshold': 1})
            return base
    if policy['enabled']:
        for metric, threshold_key, reason in NORMAL_STOPS:
            count, threshold = metrics.get(metric, 0), policy[threshold_key]
            if count >= threshold:
                base.update(decision='auto_pause', reason_code=reason, auto_pause_required=True,
                            evidence={'metric': metric, 'observed_count': count, 'threshold': threshold})
                return base
    if wave.state == 'running':
        waiting = any(item['wave_id'] == str(wave.pk) and item['classification'].startswith('waiting_') for item in summary['targets'])
        complete = (
            metrics.get('dispatched') == wave.target_count
            and metrics.get('terminal') == wave.target_count
            and metrics.get('succeeded') == wave.target_count
            and not waiting and not metrics.get('stalled')
        )
        if complete:
            base.update(decision='start_observation', reason_code='wave_success_ready_for_observation', can_enter_observation=True)
        else:
            base.update(reason_code='wave_in_progress')
        return base
    if wave.state == 'observing':
        observation = build_wave_observation_summary(campaign, wave, now=now)
        base['observation'] = observation
        if not observation['healthy']:
            reason = next(iter(observation['reason_counts']), 'observation_incomplete')
            base.update(decision='auto_pause' if policy['enabled'] else 'observation_blocked',
                        reason_code=reason, auto_pause_required=policy['enabled'],
                        evidence={'metric': 'observation_risk', 'observed_count': observation['risk_count'], 'threshold': 1})
        elif observation['remaining_seconds']:
            base.update(decision='observe', reason_code='minimum_observation_pending')
        else:
            later = campaign.waves.filter(sequence__gt=wave.sequence).exists()
            campaign_metrics = summary['metrics']
            campaign_clean = (
                campaign_metrics.get('eligible_initial') == campaign.eligible_count
                and campaign_metrics.get('terminal') == campaign.eligible_count
                and campaign_metrics.get('succeeded') == campaign.eligible_count
                and not any(campaign_metrics.get(key, 0) for key in (
                    'failed', 'rolled_back', 'cancelled', 'rollback_failed',
                    'receipt_conflict', 'binding_invalid', 'stalled',
                ))
            )
            base.update(decision='complete_wave', reason_code='observation_complete', can_complete_wave=True,
                        can_complete_campaign=campaign_clean and not later
                        and not campaign.waves.exclude(pk=wave.pk).exclude(state='completed').exists())
        return base
    base.update(reason_code=f'wave_{wave.state}')
    return base


def _system_pause(campaign, wave, decision, now):
    original = wave.state
    if original == 'observing' and wave.observation_resumed_at:
        wave.observation_accumulated_seconds += max(0, int((now - wave.observation_resumed_at).total_seconds()))
        wave.observation_resumed_at = None
    wave.resume_state = original
    wave.state = 'paused'
    wave.save(update_fields=['resume_state', 'state', 'observation_accumulated_seconds', 'observation_resumed_at', 'updated_at'])
    campaign.state = 'paused'
    campaign.paused_at = now
    campaign.save(update_fields=['state', 'paused_at', 'updated_at'])
    evidence = decision.get('evidence', {})
    AuditEvent.objects.create(event_type='rollout.auto_paused', title='Rollout automatically paused',
        actor_type=AuditEvent.ACTOR_SCHEDULER, actor_name='RolloutGovernance',
        metadata={'campaign_id': str(campaign.pk), 'wave_id': str(wave.pk), 'wave_sequence': wave.sequence,
                  'reason_code': decision['reason_code'], 'policy_schema': campaign.auto_pause_policy['schema'],
                  'threshold': evidence.get('threshold'), 'observed_count': evidence.get('observed_count'),
                  'wave_metrics': decision['wave_metrics'], 'timestamp': now.isoformat()})


def apply_rollout_governance(campaign, *, now=None):
    """Apply only governance transitions under Campaign -> Wave -> Target locks."""
    if not governance_enabled():
        raise PolicyContractError('rollout_governance_disabled', status=409)
    now = now or timezone.now()
    with transaction.atomic():
        campaign = AgentRolloutCampaign.objects.select_for_update().get(pk=getattr(campaign, 'pk', campaign))
        wave = campaign.waves.select_for_update().filter(pk=campaign.current_wave_id).first()
        list(AgentRolloutTarget.objects.select_for_update(of=('self',)).filter(campaign=campaign).order_by('endpoint_id', 'pk'))
        decision = evaluate_rollout_governance(campaign, now=now)
        if decision['decision'] in {'already_paused', 'terminal', 'continue', 'observe', 'observation_blocked'}:
            return decision
        if decision['decision'] == 'auto_pause':
            _system_pause(campaign, wave, decision, now)
        elif decision['decision'] == 'start_observation':
            wave.state = 'observing'
            wave.observation_started_at = wave.observation_started_at or now
            wave.observation_resumed_at = now
            wave.save(update_fields=['state', 'observation_started_at', 'observation_resumed_at', 'updated_at'])
            AuditEvent.objects.create(event_type='rollout.wave_observation_started', title='Rollout wave observation started',
                actor_type=AuditEvent.ACTOR_SCHEDULER, actor_name='RolloutGovernance',
                metadata={'campaign_id': str(campaign.pk), 'wave_id': str(wave.pk), 'wave_sequence': wave.sequence,
                          'target_count': wave.target_count, 'timestamp': now.isoformat()})
        elif decision['decision'] == 'complete_wave':
            elapsed = observation_elapsed_seconds(wave, now=now)
            if wave.observation_resumed_at:
                wave.observation_accumulated_seconds = elapsed
                wave.observation_resumed_at = None
            wave.state = 'completed'
            wave.completed_at = now
            wave.save(update_fields=['state', 'completed_at', 'observation_accumulated_seconds', 'observation_resumed_at', 'updated_at'])
            campaign.current_wave = None
            campaign.save(update_fields=['current_wave', 'updated_at'])
            AuditEvent.objects.create(event_type='rollout.wave_completed', title='Rollout wave completed',
                actor_type=AuditEvent.ACTOR_SCHEDULER, actor_name='RolloutGovernance',
                metadata={'campaign_id': str(campaign.pk), 'wave_id': str(wave.pk), 'wave_sequence': wave.sequence,
                          'observation_duration': elapsed, 'targets': wave.target_count,
                          'succeeded': decision['wave_metrics'].get('succeeded', 0), 'metrics': decision['wave_metrics']})
            if decision['can_complete_campaign']:
                campaign.state = 'completed'
                campaign.completed_at = now
                campaign.save(update_fields=['state', 'completed_at', 'updated_at'])
                AuditEvent.objects.create(event_type='rollout.campaign_completed', title='Rollout campaign completed',
                    actor_type=AuditEvent.ACTOR_SCHEDULER, actor_name='RolloutGovernance',
                    metadata={'campaign_id': str(campaign.pk), 'release_id': str(campaign.release_id),
                              'wave_count': campaign.waves.count(), 'timestamp': now.isoformat()})
        return evaluate_rollout_governance(campaign, now=now)


def build_rollout_advance_preview(campaign, *, now=None):
    now = now or timezone.now()
    campaign = AgentRolloutCampaign.objects.select_related('release').get(pk=getattr(campaign, 'pk', campaign))
    completed = campaign.waves.filter(state='completed').order_by('-sequence').first()
    next_wave = campaign.waves.filter(state='pending').order_by('sequence').first()
    result = {'campaign_id': str(campaign.pk), 'completed_wave_id': str(completed.pk) if completed else None,
              'next_wave_id': str(next_wave.pk) if next_wave else None,
              'next_wave_sequence': next_wave.sequence if next_wave else None, 'target_count': 0,
              'dispatchable_count': 0, 'blocked_count': 0, 'reason_counts': {}, 'targets': [],
              'generated_at': now.isoformat(), 'advance_schema': 1, 'advance_hash': '', 'advance_ready': False}
    evaluations = []
    if not next_wave:
        result['reason_counts'] = {'next_wave_unavailable': 1}
    elif (not completed or completed.sequence != next_wave.sequence - 1
          or campaign.current_wave_id is not None
          or campaign.waves.filter(sequence__lt=next_wave.sequence).exclude(state='completed').exists()):
        result.update(target_count=next_wave.target_count, blocked_count=next_wave.target_count,
                      reason_counts={'previous_wave_not_completed': next_wave.target_count})
    else:
        context = build_rollout_planning_context(campaign, wave_id=next_wave.pk)
        release_reason = release_safety(context['campaign'].release, context['key'], now)
        release_contract = validate_campaign_release_contract(
            context['campaign'], context['campaign'].release, allowed_groups=context['allowed_groups'])
        result['release_safety'] = {
            'reason_code': release_reason,
            'contract_valid': release_contract['valid'],
            'changed_fields': release_contract['changed_fields'],
            'status': context['campaign'].release.status,
            'revoked': context['campaign'].release.revoked,
            'rollout_paused': context['campaign'].release.rollout_paused,
            'signature_valid': context['campaign'].release.signature_valid,
            'signature_key_id': context['campaign'].release.signature_key_id,
            'signing_key_status': context['key'].status if context['key'] else 'missing',
        }
        context['wave_valid'] = bool(campaign.state == 'running' and campaign.current_wave_id is None
            and completed and completed.sequence == next_wave.sequence - 1
            and not campaign.waves.filter(sequence__lt=next_wave.sequence).exclude(state='completed').exists())
        evaluations = [evaluate_rollout_target_dispatch_safety(context['campaign'], target, now=now, context=context)
                       for target in sorted(context['targets'], key=lambda item: (item.rollout_bucket, str(item.endpoint_id)))]
        result.update(target_count=len(evaluations), dispatchable_count=sum(item['dispatchable'] for item in evaluations),
                      blocked_count=sum(not item['dispatchable'] for item in evaluations),
                      reason_counts=dict(sorted(Counter(item['reason_code'] for item in evaluations).items())),
                      targets=[{key: item[key] for key in ('target_id', 'endpoint_id', 'hostname', 'bucket', 'current_version',
                                                          'updater_version', 'dispatchable', 'reason_code')} for item in evaluations])
        result['advance_ready'] = bool(context['wave_valid'] and evaluations and all(item['dispatchable'] for item in evaluations))
    material = {key: result[key] for key in ('campaign_id', 'completed_wave_id', 'next_wave_id', 'next_wave_sequence',
                                              'target_count', 'dispatchable_count', 'blocked_count', 'reason_counts', 'targets')}
    material['cohort_hash'] = campaign.cohort_hash
    material['release_safety'] = result.get('release_safety', {'reason_code': 'next_wave_unavailable'})
    if evaluations:
        material['target_safety_state'] = [{key: item.get(key) for key in (
            'target_id', 'endpoint_id', 'dispatchable', 'reason_code', 'current_version', 'updater_version',
            'endpoint_status', 'endpoint_lifecycle', 'channel', 'policy', 'last_seen',
        )} for item in evaluations]
    result['advance_hash'] = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return result


@contextmanager
def governance_runner_lock():
    if connection.vendor != 'postgresql':
        raise PolicyContractError('rollout_governance_requires_postgresql', status=409)
    if connection.in_atomic_block:
        raise RuntimeError('Governance runner requires a dedicated autocommit connection')
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)', [GOVERNANCE_LOCK_ID])
        acquired = cursor.fetchone()[0]
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', [GOVERNANCE_LOCK_ID])


def run_governance_round(*, now=None):
    if not governance_enabled():
        raise PolicyContractError('rollout_governance_disabled', status=409)
    now = now or timezone.now()
    with governance_runner_lock() as acquired:
        if not acquired:
            return {'status': 'already_running', 'campaigns': []}
        ids = list(AgentRolloutCampaign.objects.filter(
            Q(state='running')
            | Q(state__in=['paused', 'aborted'], targets__agent_job__isnull=False)
        ).distinct().order_by('pk').values_list('pk', flat=True))
        results = []
        for campaign_id in ids:
            reconcile_rollout_campaign(campaign_id, now=now)
            campaign = AgentRolloutCampaign.objects.get(pk=campaign_id)
            decision = evaluate_rollout_governance(campaign, now=now)
            if campaign.state == 'running':
                decision = apply_rollout_governance(campaign, now=now)
            results.append(decision)
        return {'status': 'completed', 'campaigns': results}
