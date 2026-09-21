"""Administrative governance only. Never dispatch or interpret job results."""
from collections import Counter
import re

from django.db import transaction
from django.utils import timezone

from .fleet_policy import PolicyContractError
from .models import AgentRolloutCampaign, AuditEvent
from .rollout_campaigns import authorize, reason_text, transition_campaign, transition_wave
from .rollout_planning import MATERIAL_RELEASE_FIELDS, rollout_orchestration_enabled

CHANGE_PERMISSION = 'agents.change_agentrolloutcampaign'
CAMPAIGN_ACTIONS = {'approve': 'ready', 'start': 'running', 'pause': 'paused', 'resume': 'running', 'abort': 'aborted'}
WAVE_ACTIONS = {'prepare': 'ready', 'start': 'running', 'pause': 'paused', 'resume': None, 'prepare_next_wave': 'ready'}


def integrity(campaign):
    waves = list(campaign.waves.all())
    targets = list(campaign.targets.values('wave_id', 'eligibility_at_selection', 'exclusion_metadata'))
    eligible = [t for t in targets if t['eligibility_at_selection']]
    counts = Counter(t['wave_id'] for t in eligible)
    snapshot = campaign.release_snapshot
    complete = (len(targets) == campaign.total_candidates and len(eligible) == campaign.eligible_count
        and campaign.excluded_count == len(targets) - len(eligible) and campaign.eligible_count > 0
        and all(t['wave_id'] for t in eligible) and all(counts[w.pk] == w.target_count for w in waves)
        and sum(w.target_count for w in waves) == len(eligible)
        and campaign.wave_plan == [{'count': w.target_count, 'observation_seconds': w.minimum_observation_seconds} for w in waves]
        and isinstance(snapshot, dict) and set(MATERIAL_RELEASE_FIELDS) <= snapshot.keys()
        and campaign.cohort_schema == 1 and bool(re.fullmatch('[a-f0-9]{64}', campaign.cohort_hash))
        and snapshot['channel'] == campaign.channel_snapshot
        and len(snapshot.get('artifact_url_hashes', [])) == 4
        and all(isinstance(t['exclusion_metadata'], dict)
            and isinstance(t['exclusion_metadata'].get('dispatch_policy_snapshot'), dict)
            and {'pinned_agent_version', 'auto_update_enabled', 'maintenance_window_start', 'maintenance_window_end', 'maintenance_window_timezone'} <= t['exclusion_metadata']['dispatch_policy_snapshot'].keys()
            for t in eligible))
    if not complete:
        raise PolicyContractError('campaign_snapshot_incomplete', status=409)


@transaction.atomic
def control_rollout_campaign(campaign, data, actor, *, wave_id=None, now=None):
    authorize(actor, CHANGE_PERMISSION)
    allowed = {'action', 'reason', 'expected_state', 'expected_wave_state', 'expected_updated_at'}
    if not isinstance(data, dict) or set(data) - allowed:
        raise PolicyContractError('invalid_control_contract')
    reason = reason_text(data.get('reason'))
    action = data.get('action')
    actions = WAVE_ACTIONS if wave_id else CAMPAIGN_ACTIONS
    if not isinstance(action, str) or action not in actions:
        raise PolicyContractError('unsupported_control_action')
    now = now or timezone.now()
    campaign = AgentRolloutCampaign.objects.select_for_update().get(pk=getattr(campaign, 'pk', campaign))
    if data.get('expected_state') != campaign.state or (data.get('expected_updated_at') and data['expected_updated_at'] != campaign.updated_at.isoformat()):
        raise PolicyContractError('administrative_state_changed', status=409)
    wave = campaign.waves.select_for_update().filter(pk=wave_id).first() if wave_id else None
    if wave_id and (wave is None or data.get('expected_wave_state') != wave.state):
        raise PolicyContractError('administrative_wave_changed', status=409)
    before = wave.state if wave else campaign.state
    if wave:
        if action == 'prepare_next_wave':
            if wave.state != 'completed':
                raise PolicyContractError('previous_wave_not_completed', status=409)
            wave = campaign.waves.select_for_update().filter(sequence=wave.sequence + 1, state='pending').first()
            if wave is None:
                raise PolicyContractError('next_wave_unavailable', status=409)
            before = wave.state
        destination = wave.resume_state if action == 'resume' else actions[action]
        required_state = {'prepare': 'pending', 'start': 'ready', 'pause': 'running', 'resume': 'paused', 'prepare_next_wave': 'pending'}[action]
        if wave.state != required_state:
            raise PolicyContractError('invalid_control_transition', status=409)
        if destination == 'running' and not rollout_orchestration_enabled():
            raise PolicyContractError('rollout_orchestrator_disabled', status=409)
        if action in {'prepare', 'prepare_next_wave'}:
            if campaign.waves.filter(sequence__lt=wave.sequence).exclude(state='completed').exists():
                raise PolicyContractError('previous_wave_not_completed', status=409)
            if campaign.waves.exclude(pk=wave.pk).filter(state__in=['running', 'observing', 'paused']).exists():
                raise PolicyContractError('wave_active', status=409)
        wave = transition_wave(wave, destination, actor, reason, now=now, permission=CHANGE_PERMISSION)
    else:
        required_state = {'approve': 'draft', 'start': 'ready', 'pause': 'running', 'resume': 'paused'}
        if action != 'abort' and campaign.state != required_state[action]:
            raise PolicyContractError('invalid_control_transition', status=409)
        if action in {'start', 'resume'} and not rollout_orchestration_enabled():
            raise PolicyContractError('rollout_orchestrator_disabled', status=409)
        if action in {'approve', 'start', 'resume'}:
            integrity(campaign)
        if action in {'start', 'resume'}:
            endpoint_ids = campaign.targets.filter(eligibility_at_selection=True).values('endpoint_id')
            if AgentRolloutCampaign.objects.exclude(pk=campaign.pk).filter(
                    state__in=['running', 'paused'], targets__eligibility_at_selection=True,
                    targets__endpoint_id__in=endpoint_ids).exists():
                raise PolicyContractError('campaign_incompatible', status=409)
        campaign = transition_campaign(campaign, actions[action], actor, reason, now=now, permission=CHANGE_PERMISSION)
    counts = dict(Counter(campaign.targets.values_list('state', flat=True)))
    AuditEvent.objects.create(event_type='rollout.administrative_action', title='Rollout administrative action',
        actor_type='user', actor_name=actor.get_username(), description=reason,
        metadata={'action': action, 'campaign_id': str(campaign.pk), 'wave_id': str(wave.pk) if wave else None,
            'state_before': before, 'state_after': wave.state if wave else campaign.state,
            'job_count': campaign.targets.filter(agent_job__isnull=False).count(), 'target_state_counts': counts,
            'timestamp': now.isoformat()})
    return {'campaign_id': str(campaign.pk), 'state': campaign.state, 'wave_id': str(wave.pk) if wave else None,
            'wave_state': wave.state if wave else None}
