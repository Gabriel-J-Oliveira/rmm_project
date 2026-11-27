"""Periodic Stable discovery. Execution stays in the ordinary campaign engine."""
from collections import Counter
import hashlib
import json
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Max

from .fleet_policy import PolicyContractError
from .models import (
    AgentMachine, AgentRelease, AgentReleaseSigningKey, AgentRolloutCampaign,
    AgentRolloutCoverageRound, AgentRolloutTarget, AuditEvent,
)
from .rollout_campaigns import authorize, create_agent_rollout_campaign_from_preview, transition_wave
from .rollout_control import CHANGE_PERMISSION, control_rollout_campaign
from .rollout_planning import release_safety, rollout_orchestration_enabled
from .services import build_agent_rollout_preview
from .stable_rollout import DESKTOP_GROUP, enabled
from .versioning import sort_releases_by_version


def latest_stable_release_id():
    # A newer paused release must not cause fallback to an older release.
    releases = AgentRelease.objects.filter(
        channel=AgentRelease.CHANNEL_STABLE,
        status__in=[AgentRelease.STATUS_PUBLISHED, AgentRelease.STATUS_PAUSED],
        revoked=False,
    ).order_by('-released_at', '-created_at', 'pk')
    ordered = sort_releases_by_version(releases)
    return ordered[0].pk if ordered else None


@transaction.atomic
def discover_stable_coverage(*, now):
    if not enabled(AgentRelease.CHANNEL_STABLE) or not rollout_orchestration_enabled():
        return None
    release_id = latest_stable_release_id()
    if release_id is None:
        return None
    # The release row also serializes existing campaign creation. No existing
    # campaign or endpoint rows are locked here: dispatch owns those locks.
    release = AgentRelease.objects.select_for_update().get(pk=release_id)
    if release.pk != latest_stable_release_id() or release.rollout_percentage != 100:
        return None
    key = AgentReleaseSigningKey.objects.select_for_update().filter(key_id=release.signature_key_id).first()
    if release_safety(release, key, now):
        return None
    campaigns = AgentRolloutCampaign.objects.filter(release=release)
    if campaigns.exclude(state='completed').exists():
        return None  # An abort is a veto for future automatic rounds on this release.
    original = campaigns.filter(
        release_snapshot__stable_desktop_profile=True, source_coverage_round__isnull=True,
    ).select_related('approved_by').order_by('created_at', 'pk').first()
    if original is None or original.approved_by is None:
        return None

    covered = AgentRolloutTarget.objects.filter(
        campaign__release=release, eligibility_at_selection=True,
    ).values('endpoint_id')
    candidates = AgentMachine.objects.filter(
        update_channel=AgentRelease.CHANNEL_STABLE, rollout_groups__slug=DESKTOP_GROUP,
    ).exclude(pk__in=covered)
    groups = original.target_group_ids_snapshot
    if groups:
        candidates = candidates.filter(rollout_groups__id__in=groups).distinct()
    preview = build_agent_rollout_preview(release, endpoints=candidates, now=now,
                                          freshness_seconds=original.freshness_seconds,
                                          target_group_ids=groups)
    last_seen = dict(candidates.values_list('pk', 'last_seen_at'))
    online = set(candidates.filter(status=AgentMachine.STATUS_ONLINE).values_list('pk', flat=True))
    allowed = {str(pk) for pk in release.allowed_groups.values_list('pk', flat=True)}
    previous = {}
    for snapshot in original.coverage_rounds.order_by('sequence').values_list('target_snapshot', flat=True):
        previous.update((entry['endpoint_id'], entry) for entry in snapshot)

    observations, selected = [], []
    for entry in preview['targets']:
        pk = UUID(entry['endpoint_id'])
        reason = entry['reason_code']
        if reason == 'already_current' and allowed and not allowed.intersection(entry['groups']):
            reason = 'group_not_allowed'
        if entry['eligible'] or reason == 'already_current':
            heartbeat = last_seen.get(pk)
            if pk not in online:
                reason = 'endpoint_offline'
            elif not heartbeat or not 0 <= (now - heartbeat).total_seconds() <= original.freshness_seconds:
                reason = 'endpoint_stale'
        outcome = ('already_current' if reason == 'already_current' else 'included'
                   if entry['eligible'] and reason == entry['reason_code'] else 'excluded')
        if outcome == 'included':
            selected.append(pk)
            reason = 'stable_catch_up_eligible'
        observation = {'endpoint_id': entry['endpoint_id'], 'version': entry['current_version'],
                       'outcome': outcome, 'reason': reason}
        if outcome == 'included' or previous.get(entry['endpoint_id']) != observation:
            observations.append(observation)
    if not observations:
        return None

    actor = original.approved_by
    authorize(actor)
    authorize(actor, CHANGE_PERMISSION)
    reason = 'Periodic Stable coverage under the original campaign approval'
    executor = None
    if selected:
        endpoints = AgentMachine.objects.filter(
            pk__in=selected, status=AgentMachine.STATUS_ONLINE, last_seen_at__lte=now,
            last_seen_at__gte=now - timedelta(seconds=original.freshness_seconds),
        )
        execution_preview = build_agent_rollout_preview(
            release, endpoints=endpoints, now=now, freshness_seconds=original.freshness_seconds,
            target_group_ids=groups)
        if execution_preview['eligible_count'] != len(selected) or latest_stable_release_id() != release.pk:
            raise PolicyContractError('stable_catch_up_selection_changed', status=409)
        executor = create_agent_rollout_campaign_from_preview(release, {
            'reason': reason, 'expected_cohort_hash': execution_preview['cohort_hash'],
            'cohort_schema': execution_preview['cohort_schema'],
            'wave_plan': [{'remaining': True}],
            'freshness_seconds': original.freshness_seconds,
            'target_group_ids': groups,
            'concurrency_limit': settings.NIGHTOWL_STABLE_DESKTOP_ROLLOUT_BATCH_SIZE,
            'minimum_observation_seconds': original.minimum_observation_seconds,
            'auto_pause_policy': original.auto_pause_policy,
        }, actor, now=now, endpoints=endpoints)
        control_rollout_campaign(executor, {'action': 'start', 'reason': reason, 'expected_state': 'ready'},
                                 actor, now=now)
        wave = transition_wave(executor.waves.get(), 'ready', actor, reason, now=now)
        transition_wave(wave, 'running', actor, reason, now=now)

    counts = Counter(entry['outcome'] for entry in observations)
    reasons = Counter(entry['reason'] for entry in observations)
    digest = hashlib.sha256(json.dumps(observations, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    coverage = AgentRolloutCoverageRound(
        campaign=original, execution_campaign=executor,
        sequence=(original.coverage_rounds.aggregate(last=Max('sequence'))['last'] or 0) + 1,
        cohort_schema=1, cohort_hash=digest, evaluated_at=now, target_snapshot=observations,
        reason=reason, total_candidates=len(observations), eligible_count=counts['included'],
        excluded_count=counts['excluded'], already_current_count=counts['already_current'],
        reason_summary=dict(reasons), created_by=actor,
    )
    coverage.clean()
    coverage.save(force_insert=True)
    AuditEvent.objects.create(
        event_type='rollout.stable_coverage_recorded', title='Stable coverage recorded',
        actor_type=AuditEvent.ACTOR_SCHEDULER, actor_name='RolloutOrchestrator',
        metadata={'release_id': str(release.pk), 'campaign_id': str(original.pk),
                  'coverage_round_id': str(coverage.pk), 'cohort_hash': digest,
                  'execution_campaign_id': str(executor.pk) if executor else None,
                  'counts': dict(counts), 'reasons': dict(reasons)},
    )
    return str(coverage.pk)
