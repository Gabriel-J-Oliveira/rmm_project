"""Creation and administrative state transitions, never job dispatch."""
import re
import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone

from config.authz import is_nightowl_technical_user
from .fleet_policy import PolicyContractError
from .job_progress import sanitize_job_value
from .models import AgentMachine, AgentRelease, AgentReleaseGroup, AgentRolloutCampaign, AgentRolloutTarget, AgentRolloutWave, AuditEvent
from .services import build_agent_rollout_preview
from .rollout_planning import campaign_release_snapshot
from .rollout_governance import validate_auto_pause_policy


def authorize(actor, permission='agents.add_agentrolloutcampaign'):
    if not actor or not actor.is_active or not is_nightowl_technical_user(actor) or not actor.has_perm(permission):
        raise PolicyContractError('forbidden', status=403)


def reason_text(reason):
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise PolicyContractError('administrative_reason_required')
    return sanitize_job_value(reason.strip(), max_string=1000)


def audit(event, campaign, actor, reason, **extra):
    AuditEvent.objects.create(event_type=event, actor_type='user', actor_name=actor.get_username(),
                             title=event, description=reason,
                             metadata={'campaign_id': str(campaign.pk), 'release_id': str(campaign.release_id),
                                       'cohort_hash': campaign.cohort_hash,
                                       'counts': {'total': campaign.total_candidates, 'eligible': campaign.eligible_count, 'excluded': campaign.excluded_count},
                                       'wave_plan': campaign.wave_plan, **extra})


def wave_contract(plan, eligible, minimum):
    if not isinstance(plan, list) or len(plan) > 250:
        raise PolicyContractError('invalid_wave_plan')
    result, used = [], 0
    for index, item in enumerate(plan):
        if not isinstance(item, dict) or set(item) - {'count', 'remaining', 'observation_seconds'}:
            raise PolicyContractError('invalid_wave_plan')
        observation = item.get('observation_seconds', minimum)
        if type(observation) is not int or observation < minimum:
            raise PolicyContractError('invalid_observation')
        if 'remaining' in item:
            if item['remaining'] is not True or 'count' in item or index != len(plan) - 1:
                raise PolicyContractError('invalid_remaining')
            count = eligible - used
        else:
            count = item.get('count')
        if type(count) is not int or count <= 0 or used + count > eligible:
            raise PolicyContractError('invalid_wave_count')
        result.append({'count': count, 'observation_seconds': observation})
        used += count
    return result, used


def create_agent_rollout_campaign_from_preview(release, data, actor, *, now=None):
    authorize(actor)
    if not isinstance(data, dict):
        raise PolicyContractError('invalid_contract')
    reason = reason_text(data.get('reason'))
    allowed = {'expected_cohort_hash', 'cohort_schema', 'target_group_ids', 'freshness_seconds',
               'wave_plan', 'concurrency_limit', 'minimum_observation_seconds', 'auto_pause_policy', 'reason', 'ready'}
    if set(data) - allowed or type(data.get('cohort_schema')) is not int or data['cohort_schema'] != 1 or not isinstance(data.get('expected_cohort_hash'), str) or not re.fullmatch('[a-f0-9]{64}', data['expected_cohort_hash']):
        raise PolicyContractError('invalid_hash_contract')
    freshness = data.get('freshness_seconds', 900)
    concurrency = data.get('concurrency_limit', 1)
    minimum = data.get('minimum_observation_seconds', 0)
    ready = data.get('ready', True)
    auto_pause_policy = validate_auto_pause_policy(data.get('auto_pause_policy'))
    if type(freshness) is not int or not 60 <= freshness <= 3600 or type(concurrency) is not int or not 1 <= concurrency <= 500 or type(minimum) is not int or minimum < 0 or type(ready) is not bool:
        raise PolicyContractError('invalid_limits')
    groups = data.get('target_group_ids', [])
    if not isinstance(groups, list):
        raise PolicyContractError('invalid_groups')
    try:
        groups = sorted({str(uuid.UUID(str(value))) for value in groups})
    except (ValueError, TypeError, AttributeError):
        raise PolicyContractError('invalid_groups') from None
    now = now or timezone.now()
    try:
        with transaction.atomic():
            release = AgentRelease.objects.select_for_update().get(pk=release.pk)
            if AgentReleaseGroup.objects.filter(pk__in=groups).count() != len(groups):
                raise PolicyContractError('unknown_group')
            preview = build_agent_rollout_preview(release, target_group_ids=groups, freshness_seconds=freshness, now=now)
            if preview['cohort_hash'] != data['expected_cohort_hash'] or preview['cohort_schema'] != data['cohort_schema']:
                raise PolicyContractError('preview_changed', status=409, plan=preview)
            if AgentRolloutCampaign.objects.filter(release=release).exclude(state__in=['completed', 'aborted']).exists():
                raise PolicyContractError('campaign_exists', status=409)
            plan, used = wave_contract(data.get('wave_plan', []), preview['eligible_count'], minimum)
            if ready and (not preview['eligible_count'] or used != preview['eligible_count']):
                raise PolicyContractError('ready_requires_complete_plan')
            if used != preview['eligible_count']:
                raise PolicyContractError('wave_assignment_required')
            policies = {}
            for row in AgentMachine.objects.filter(pk__in=[t['endpoint_id'] for t in preview['targets']]).values(
                    'id', 'pinned_agent_version', 'auto_update_enabled', 'maintenance_window_start',
                    'maintenance_window_end', 'maintenance_window_timezone'):
                pk = str(row.pop('id'))
                policies[pk] = {key: value.isoformat() if hasattr(value, 'isoformat') else value for key, value in row.items()}
            snapshot = campaign_release_snapshot(release)
            campaign = AgentRolloutCampaign.objects.create(release=release, release_snapshot=snapshot,
                channel_snapshot=release.channel, cohort_schema=preview['cohort_schema'], cohort_hash=preview['cohort_hash'],
                preview_generated_at=now, freshness_seconds=freshness, target_group_ids_snapshot=groups,
                wave_plan=plan, total_candidates=preview['total_candidates'], eligible_count=preview['eligible_count'],
                excluded_count=preview['excluded_count'], concurrency_limit=concurrency,
                minimum_observation_seconds=minimum, auto_pause_policy=auto_pause_policy,
                created_by=actor, administrative_reason=reason)
            audit('campaign.created', campaign, actor, reason)
            assignments, offset = {}, 0
            selected = sorted((target for target in preview['targets'] if target['eligible']), key=lambda t: (t['bucket'], t['endpoint_id']))
            for sequence, item in enumerate(plan, 1):
                wave = AgentRolloutWave.objects.create(campaign=campaign, sequence=sequence, target_count=item['count'],
                    eligible_target_count=item['count'], minimum_observation_seconds=item['observation_seconds'])
                for target in selected[offset:offset + item['count']]:
                    assignments[target['endpoint_id']] = wave
                offset += item['count']
                audit('wave.created', campaign, actor, reason, wave_id=str(wave.pk), sequence=sequence)
            AgentRolloutTarget.objects.bulk_create([AgentRolloutTarget(campaign=campaign,
                wave=assignments.get(t['endpoint_id']), endpoint_id=t['endpoint_id'],
                endpoint_machine_id_snapshot=t['machine_id'] or '', endpoint_hostname_snapshot=t['hostname'],
                current_version_snapshot=t['current_version'] or '', updater_version_snapshot=t['updater_version'] or '',
                update_channel_snapshot=t['channel'], update_policy_snapshot=t['policy'], group_ids_snapshot=t['groups'],
                rollout_bucket=t['bucket'], eligibility_at_selection=t['eligible'], reason_code=t['reason_code'],
                state='eligible' if t['eligible'] else 'excluded', selected_at=now,
                exclusion_metadata={'dispatch_policy_snapshot': policies[t['endpoint_id']],
                    **({'reason_code': t['reason_code']} if not t['eligible'] else {})}) for t in preview['targets']])
            if campaign.targets.count() != campaign.total_candidates or campaign.targets.filter(state='eligible').count() != campaign.eligible_count:
                raise PolicyContractError('snapshot_count_mismatch', status=409)
            if ready:
                campaign = transition_campaign(campaign, 'ready', actor, reason, now=now)
            return campaign
    except IntegrityError:
        raise PolicyContractError('campaign_conflict', status=409) from None


@transaction.atomic
def transition_campaign(campaign, state, actor, reason, *, now=None, permission='agents.add_agentrolloutcampaign'):
    authorize(actor, permission)
    reason = reason_text(reason)
    now = now or timezone.now()
    campaign = AgentRolloutCampaign.objects.select_for_update().get(pk=campaign.pk)
    transitions = {'draft': {'ready', 'aborted'}, 'ready': {'running', 'aborted'},
                   'running': {'paused', 'completed', 'aborted'}, 'paused': {'running', 'aborted'}}
    if state not in transitions.get(campaign.state, set()):
        raise PolicyContractError('invalid_campaign_transition', status=409)
    if state == 'ready' and (not campaign.eligible_count or campaign.targets.count() != campaign.total_candidates or campaign.targets.filter(state='eligible', wave__isnull=True).exists()):
        raise PolicyContractError('ready_requires_complete_plan', status=409)
    if state == 'ready':
        waves = list(campaign.waves.all())
        actual_plan = [{'count': w.target_count, 'observation_seconds': w.minimum_observation_seconds} for w in waves]
        if actual_plan != campaign.wave_plan or sum(w.target_count for w in waves) != campaign.eligible_count or any(w.targets.filter(state='eligible').count() != w.target_count for w in waves):
            raise PolicyContractError('snapshot_count_mismatch', status=409)
    if state == 'completed' and campaign.waves.exclude(state='completed').exists():
        raise PolicyContractError('waves_not_completed', status=409)
    if state == 'aborted':
        for wave in campaign.waves.exclude(state__in=['completed', 'cancelled']):
            wave.state = 'cancelled'
            wave.save(update_fields=['state', 'updated_at'])
            audit('wave.state_changed', campaign, actor, reason, wave_id=str(wave.pk), state='cancelled')
        campaign.current_wave = None
    campaign.state = state
    if state == 'ready':
        campaign.approved_by = actor
    if state == 'running' and campaign.started_at is None:
        campaign.started_at = now
    if state in {'paused', 'completed', 'aborted'}:
        setattr(campaign, state + '_at', now)
    campaign.save()
    audit('campaign.ready' if state == 'ready' else 'campaign.aborted' if state == 'aborted' else 'campaign.state_changed', campaign, actor, reason, state=state)
    return campaign


@transaction.atomic
def transition_wave(wave, state, actor, reason, *, now=None, permission='agents.add_agentrolloutcampaign'):
    authorize(actor, permission)
    reason = reason_text(reason)
    now = now or timezone.now()
    campaign = AgentRolloutCampaign.objects.select_for_update().get(pk=wave.campaign_id)
    wave = AgentRolloutWave.objects.select_for_update().get(pk=wave.pk)
    transitions = {'pending': {'ready', 'cancelled'}, 'ready': {'running', 'cancelled'},
                   'running': {'observing', 'paused', 'cancelled'}, 'observing': {'completed', 'paused', 'cancelled'},
                   'paused': {wave.resume_state, 'cancelled'}}
    if state not in transitions.get(wave.state, set()) or campaign.state != 'running':
        raise PolicyContractError('invalid_wave_transition', status=409)
    if state in {'running', 'observing', 'paused'} and campaign.waves.exclude(pk=wave.pk).filter(state__in=['running', 'observing', 'paused']).exists():
        raise PolicyContractError('wave_active', status=409)
    if state == 'running' and campaign.waves.filter(sequence__lt=wave.sequence).exclude(state='completed').exists():
        raise PolicyContractError('previous_wave_not_completed', status=409)
    if state == 'completed' and (not wave.started_at or not wave.observation_started_at or (now - wave.observation_started_at).total_seconds() < wave.minimum_observation_seconds):
        raise PolicyContractError('observation_incomplete', status=409)
    if state == 'paused':
        wave.resume_state = wave.state
        if wave.state == 'observing' and wave.observation_resumed_at:
            wave.observation_accumulated_seconds += max(0, int((now - wave.observation_resumed_at).total_seconds()))
            wave.observation_resumed_at = None
    if state == 'running' and wave.started_at is None:
        wave.started_at = now
    if state == 'observing' and wave.observation_started_at is None:
        wave.observation_started_at = now
    if state == 'observing' and wave.observation_resumed_at is None:
        wave.observation_resumed_at = now
    if state == 'completed':
        if wave.observation_resumed_at:
            wave.observation_accumulated_seconds += max(0, int((now - wave.observation_resumed_at).total_seconds()))
            wave.observation_resumed_at = None
        wave.completed_at = now
    wave.state = state
    wave.save()
    if state in {'running', 'observing', 'paused'}:
        campaign.current_wave = wave
        campaign.save(update_fields=['current_wave', 'updated_at'])
    elif state in {'completed', 'cancelled'} and campaign.current_wave_id == wave.pk:
        campaign.current_wave = None
        campaign.save(update_fields=['current_wave', 'updated_at'])
    audit('wave.state_changed', campaign, actor, reason, wave_id=str(wave.pk), state=state)
    return wave
