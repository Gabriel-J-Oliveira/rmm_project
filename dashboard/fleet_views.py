"""Administrative read-only previews and explicit policy edits. No job dispatch."""
import json
import re
import uuid
from collections import Counter

from django.conf import settings
from django.http import Http404
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.http import require_GET

from config.authz import is_nightowl_technical_user
from agents.fleet_policy import PolicyContractError, bulk_policy_operation
from agents.models import AgentRelease, AgentReleaseGroup, AgentRolloutCampaign, AuditEvent
from agents.services import build_agent_rollout_preview
from agents.rollout_campaigns import create_agent_rollout_campaign_from_preview
from agents.rollout_control import control_rollout_campaign
from agents.rollout_planning import build_rollout_dispatch_plan
from agents.rollout_reconcile import summarize_rollout_campaign
from agents.rollout_governance import build_rollout_advance_preview, evaluate_rollout_governance, governance_enabled


def authorized(request, permission):
    user = request.user
    return user.is_authenticated and user.is_active and is_nightowl_technical_user(user) and user.has_perm(permission)


def read_json(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise PolicyContractError('invalid_json') from None
    if not isinstance(data, dict):
        raise PolicyContractError('invalid_contract')
    return data


@require_POST
def rollout_preview(request, pk, validate=False):
    if not authorized(request, 'agents.view_agent_release_rollout'):
        return JsonResponse({'error': 'forbidden'}, status=403)
    try:
        data = read_json(request)
        allowed = {'target_group_ids', 'freshness_seconds'} | ({'expected_cohort_hash', 'cohort_schema'} if validate else set())
        if set(data) - allowed:
            raise PolicyContractError('invalid_contract')
        freshness = data.get('freshness_seconds', 900)
        if type(freshness) is not int or not 60 <= freshness <= 3600:
            raise PolicyContractError('invalid_freshness')
        groups = data.get('target_group_ids', [])
        if not isinstance(groups, list):
            raise PolicyContractError('invalid_groups')
        try:
            groups = sorted({str(uuid.UUID(str(value))) for value in groups})
        except (ValueError, TypeError, AttributeError):
            raise PolicyContractError('invalid_groups') from None
        if AgentReleaseGroup.objects.filter(pk__in=groups).count() != len(groups):
            raise PolicyContractError('unknown_group')
        release = AgentRelease.objects.filter(pk=pk).first()
        if release is None:
            raise PolicyContractError('release_not_found', status=404)
        if validate and (type(data.get('cohort_schema')) is not int or data['cohort_schema'] != 1 or not re.fullmatch(r'[a-f0-9]{64}', str(data.get('expected_cohort_hash', '')))):
            raise PolicyContractError('invalid_hash_contract')
        preview = build_agent_rollout_preview(release, now=timezone.now(), target_group_ids=groups, freshness_seconds=freshness)
        preview['group_options'] = [{'id': str(group.pk), 'name': group.name} for group in AgentReleaseGroup.objects.order_by('slug')]
        preview['release'] = {'id': str(release.pk), 'version': release.version, 'channel': release.channel, 'status': release.status,
                              'signature_valid': release.signature_valid, 'rollout_percentage': release.rollout_percentage,
                              'rollout_paused': release.rollout_paused, 'allowed_groups': sorted(str(value) for value in release.allowed_groups.values_list('pk', flat=True))}
        if validate:
            matches = data['expected_cohort_hash'] == preview['cohort_hash']
            return JsonResponse({'matches': matches, 'error': '' if matches else 'preview_changed',
                                 'expected_cohort_hash': data['expected_cohort_hash'], 'current_cohort_hash': preview['cohort_hash'],
                                 'preview': preview}, status=200 if matches else 409)
        return JsonResponse(preview)
    except PolicyContractError as exc:
        return JsonResponse({'error': str(exc)}, status=exc.status)


@require_POST
def bulk_policy(request):
    if not authorized(request, 'agents.change_agentmachine'):
        return JsonResponse({'error': 'forbidden'}, status=403)
    try:
        return JsonResponse(bulk_policy_operation(read_json(request), request.user))
    except PolicyContractError as exc:
        return JsonResponse({'error': str(exc), 'preview': exc.plan}, status=exc.status)


@require_POST
def rollout_campaign_create(request, pk):
    if not authorized(request, 'agents.add_agentrolloutcampaign'):
        return JsonResponse({'error': 'forbidden'}, status=403)
    release = AgentRelease.objects.filter(pk=pk).first()
    if release is None:
        return JsonResponse({'error': 'release_not_found'}, status=404)
    try:
        campaign = create_agent_rollout_campaign_from_preview(release, read_json(request), request.user)
        return JsonResponse({'campaign_id': str(campaign.pk), 'state': campaign.state,
                             'cohort_schema': campaign.cohort_schema, 'cohort_hash': campaign.cohort_hash,
                             'total_candidates': campaign.total_candidates, 'eligible_count': campaign.eligible_count,
                             'excluded_count': campaign.excluded_count,
                             'auto_pause_policy': campaign.auto_pause_policy,
                             'waves': [{'id': str(w.pk), 'sequence': w.sequence, 'state': w.state,
                                        'count': w.target_count, 'observation_seconds': w.minimum_observation_seconds} for w in campaign.waves.all()]}, status=201)
    except PolicyContractError as exc:
        return JsonResponse({'error': str(exc), 'preview': exc.plan}, status=exc.status)


@require_POST
def rollout_control(request, pk, wave_id=None):
    if not authorized(request, 'agents.change_agentrolloutcampaign'):
        return JsonResponse({'error': 'forbidden'}, status=403)
    campaign = AgentRolloutCampaign.objects.filter(pk=pk).first()
    if campaign is None:
        return JsonResponse({'error': 'campaign_not_found'}, status=404)
    try:
        return JsonResponse(control_rollout_campaign(campaign, read_json(request), request.user, wave_id=wave_id))
    except PolicyContractError as exc:
        return JsonResponse({'error': str(exc)}, status=exc.status)


@require_GET
def rollout_detail(request, pk):
    if not authorized(request, 'agents.view_agentrolloutcampaign'):
        return JsonResponse({'error': 'forbidden'}, status=403)
    campaign = AgentRolloutCampaign.objects.select_related('release').filter(pk=pk).first()
    if campaign is None:
        raise Http404
    targets = list(campaign.targets.select_related('endpoint').defer('endpoint__agent_token_hash').order_by('rollout_bucket', 'endpoint_id'))
    counts = Counter(t.state for t in targets)
    wave_counts = {}
    for target in targets:
        wave_counts.setdefault(target.wave_id, Counter())[target.state] += 1
    plan = build_rollout_dispatch_plan(campaign)
    reconciliation = summarize_rollout_campaign(campaign)
    governance = evaluate_rollout_governance(campaign, summary=reconciliation)
    advance = build_rollout_advance_preview(campaign)
    reconciliation_targets = {item['target_id']: item for item in reconciliation['targets']}
    blockers = {t['target_id']: t['reason_code'] for t in plan['targets']}
    approved_at = AuditEvent.objects.filter(event_type='campaign.ready', metadata__campaign_id=str(pk)).order_by('created_at').values_list('created_at', flat=True).first()
    return JsonResponse({'id': str(campaign.pk), 'release_version': campaign.release.version,
        'state': campaign.state, 'cohort_hash': campaign.cohort_hash, 'reason': campaign.administrative_reason,
        'created_at': campaign.created_at.isoformat(), 'approved': bool(campaign.approved_by_id),
        'approved_at': approved_at.isoformat() if approved_at else None,
        'started_at': campaign.started_at.isoformat() if campaign.started_at else None,
        'updated_at': campaign.updated_at.isoformat(), 'concurrency_limit': campaign.concurrency_limit,
        'total': campaign.total_candidates, 'eligible': campaign.eligible_count, 'excluded': campaign.excluded_count,
        'target_counts': dict(counts), 'current_wave': str(campaign.current_wave_id) if campaign.current_wave_id else None,
        'reconciliation_metrics': reconciliation['metrics'],
        'governance_enabled': governance_enabled(), 'auto_pause_policy': campaign.auto_pause_policy,
        'governance': governance, 'advance_preview': advance,
        'orchestrator_enabled': settings.NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED,
        'automatic_enabled': settings.NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED,
        'waves': [{'id': str(w.pk), 'sequence': w.sequence, 'state': w.state, 'resume_state': w.resume_state,
            'target_count': w.target_count, 'counts': dict(wave_counts.get(w.pk, {})),
            'reconciliation_metrics': reconciliation['waves'].get(str(w.pk), {}),
            'observation_seconds': w.minimum_observation_seconds,
            'started_at': w.started_at.isoformat() if w.started_at else None,
            'completed_at': w.completed_at.isoformat() if w.completed_at else None} for w in campaign.waves.all()],
        'targets': [{'id': str(t.pk), 'hostname': t.endpoint_hostname_snapshot,
            'current_version': t.endpoint.agent_version, 'snapshot_version': t.current_version_snapshot,
            'state': t.state, 'initial_reason': t.reason_code, 'blocker': blockers.get(str(t.pk), 'wave_not_dispatchable'),
            'job_id': str(t.agent_job_id) if t.agent_job_id else None, 'bucket': t.rollout_bucket,
            'reconciliation': reconciliation_targets.get(str(t.pk), {})} for t in targets]})


@require_GET
def rollout_operations(request):
    if not authorized(request, 'agents.view_agentrolloutcampaign'):
        return JsonResponse({'error': 'forbidden'}, status=403)
    return render(request, 'dashboard/rollout_operations.html', {
        'active_nav': 'agent_releases', 'campaigns': AgentRolloutCampaign.objects.select_related('release').order_by('-created_at'),
        'can_change': authorized(request, 'agents.change_agentrolloutcampaign')})
