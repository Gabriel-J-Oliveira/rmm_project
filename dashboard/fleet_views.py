"""Administrative read-only previews and explicit policy edits. No job dispatch."""
import json
import re
import uuid

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from config.authz import is_nightowl_technical_user
from agents.fleet_policy import PolicyContractError, bulk_policy_operation
from agents.models import AgentRelease, AgentReleaseGroup
from agents.services import build_agent_rollout_preview


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
