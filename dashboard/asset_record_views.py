import json
import uuid
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods

from agents.asset_registry import (
    AssetRegistryError,
    add_asset_record,
    create_asset_for_endpoint,
    endpoint_asset_payload,
    link_asset_to_endpoint,
    link_endpoint_reference,
)
from agents.asset_hardware import live_claims
from agents.asset_registry import _lock_endpoint
from agents.identity import normalized_machine_id, machine_id_matches
from agents.models import AgentJob, AgentMachine, AssetAgentLink, AssetHardwareObservation, AuditEvent, PhysicalAsset


def _can_validate(user):
    return (_has_perm(user, 'agents.change_physicalasset') and _has_perm(user, 'agents.add_agentjob'))


def _validation_jobs(endpoint):
    return endpoint.jobs.filter(job_type=AgentJob.TYPE_FORCE_INVENTORY, payload__asset_validation=True)


@transaction.atomic
def _request_validation(endpoint, machine_id, user):
    endpoint = _lock_endpoint(endpoint.pk, machine_id)
    if endpoint.has_terminal_lifecycle:
        raise AssetRegistryError('endpoint_inactive')
    if endpoint.status != AgentMachine.STATUS_ONLINE:
        raise AssetRegistryError('asset_validation_offline')
    try:
        key = normalized_machine_id(endpoint.machine_id)
    except ValueError:
        key = ''
    if not key or machine_id_matches(key).exclude(pk=endpoint.pk).exists():
        raise AssetRegistryError('asset_validation_identity_conflict')
    pending = ('queued', 'sent', 'running')
    job = _validation_jobs(endpoint).filter(status__in=pending, expires_at__gt=timezone.now()).first()
    if job:
        return job
    if endpoint.jobs.filter(status__in=pending).count() >= 50:
        raise AssetRegistryError('endpoint_job_queue_full')
    job = AgentJob.objects.create(
        endpoint=endpoint, job_type=AgentJob.TYPE_FORCE_INVENTORY, created_by=user.get_username(),
        payload={'asset_validation': True}, correlation_id=str(uuid.uuid4()), timeout_seconds=300,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    AuditEvent.objects.create(event_type='asset.validation_requested', actor_type=AuditEvent.ACTOR_USER,
                              actor_name=user.get_username(), endpoint=endpoint,
                              title='Validação patrimonial solicitada', metadata={'job_id': str(job.pk)})
    return job


def _has_perm(user, permission):
    return bool(user.is_authenticated and user.is_active and user.has_perm(permission))


def _json_body(request):
    try:
        data = json.loads(request.body or b'{}')
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _permission_denied():
    return JsonResponse({'error': 'forbidden'}, status=403)


def _can_view_endpoint(user):
    return _has_perm(user, 'agents.view_agentmachine') and _has_perm(user, 'agents.view_physicalasset')


def _serialize_asset_search(asset, link=None):
    reference = link_endpoint_reference(link)
    return {
        'id': str(asset.pk), 'code': asset.asset_code, 'serial_number': asset.serial_number,
        'manufacturer': asset.manufacturer, 'model': asset.model,
        'identity_status': asset.identity_status,
        'active_endpoint': ({
            'id': reference['endpoint_id'], 'hostname': reference['hostname'], 'state': reference['state'],
        } if reference else None),
    }


@login_required
@require_http_methods(['GET', 'POST'])
def endpoint_asset_records(request, endpoint_id):
    """Read or mutate only registry records for the endpoint UUID in the URL."""
    if not _can_view_endpoint(request.user):
        return _permission_denied()
    endpoint = get_object_or_404(AgentMachine, pk=endpoint_id)

    if request.method == 'GET':
        asset_query = request.GET.get('asset_q', '').strip()[:100]
        payload = endpoint_asset_payload(endpoint)
        observation = AssetHardwareObservation.objects.filter(endpoint=endpoint).first()
        if observation:
            state = observation.state
            if observation.smbios_uuid and live_claims(observation.smbios_uuid, endpoint.pk).exists():
                state = 'uuid_duplicate'
            payload['hardware_identity'] = {'state': state, 'observed_at': observation.observed_at,
                                            'smbios_uuid': str(observation.smbios_uuid or '')}
        job = _validation_jobs(endpoint).order_by('-created_at').first()
        if job:
            job_state = job.status
            if job_state in ('queued', 'sent', 'running') and job.expires_at <= timezone.now():
                job_state = 'expired'
            payload['validation_job'] = {'id': str(job.pk), 'state': job_state,
                                         'result': (job.result or {}).get('asset_validation')}
        payload['permissions'] = {
            'validate_link': _can_validate(request.user),
            'create_asset': _has_perm(request.user, 'agents.add_physicalasset')
                            and _has_perm(request.user, 'agents.add_assetagentlink'),
            'link_asset': _has_perm(request.user, 'agents.change_physicalasset')
                          and _has_perm(request.user, 'agents.add_assetagentlink'),
            'add_record': _has_perm(request.user, 'agents.add_assetrecord'),
        }
        payload['asset_search_results'] = []
        if asset_query:
            assets = PhysicalAsset.objects.filter(status=PhysicalAsset.STATUS_ACTIVE).filter(
                models_q_asset_query(asset_query),
            ).order_by('asset_code')[:20]
            assets = list(assets)
            active_links = AssetAgentLink.objects.filter(
                asset_id__in=[asset.pk for asset in assets], ended_at__isnull=True,
            ).select_related('endpoint')
            links_by_asset = {link.asset_id: link for link in active_links}
            payload['asset_search_results'] = [
                _serialize_asset_search(asset, links_by_asset.get(asset.pk)) for asset in assets
            ]
        return JsonResponse(payload)

    data = _json_body(request)
    if data is None:
        return JsonResponse({'error': 'invalid_json'}, status=400)
    action = data.get('action')
    try:
        if action == 'validate_link':
            if not _can_validate(request.user):
                return _permission_denied()
            if not isinstance(data.get('machine_id'), str):
                return JsonResponse({'error': 'endpoint_identity_required'}, status=400)
            job = _request_validation(endpoint, data['machine_id'], request.user)
            return JsonResponse({'ok': True, 'job_id': str(job.pk), 'state': job.status}, status=202)
        if action == 'create_asset':
            if not (_can_view_endpoint(request.user) and _has_perm(request.user, 'agents.add_physicalasset')
                    and _has_perm(request.user, 'agents.add_assetagentlink')):
                return _permission_denied()
            if not isinstance(data.get('machine_id'), str):
                return JsonResponse({'error': 'endpoint_identity_required'}, status=400)
            asset = create_asset_for_endpoint(
                endpoint.pk, data.get('machine_id'), request.user,
                identity_confirmed=data.get('identity_confirmed') is True,
                reason=data.get('reason', ''),
            )
        elif action == 'link_asset':
            if not (_can_view_endpoint(request.user) and _has_perm(request.user, 'agents.change_physicalasset')
                    and _has_perm(request.user, 'agents.add_assetagentlink')):
                return _permission_denied()
            if not isinstance(data.get('machine_id'), str):
                return JsonResponse({'error': 'endpoint_identity_required'}, status=400)
            try:
                asset_id = uuid.UUID(str(data.get('asset_id') or ''))
            except (TypeError, ValueError, AttributeError):
                return JsonResponse({'error': 'asset_id_invalid'}, status=400)
            asset = link_asset_to_endpoint(
                endpoint.pk, data.get('machine_id'), asset_id, request.user,
                identity_confirmed=data.get('identity_confirmed') is True,
                transfer_confirmed=data.get('transfer_confirmed') is True,
                reason=data.get('reason', ''),
            )
        elif action == 'add_record':
            if not (_can_view_endpoint(request.user) and _has_perm(request.user, 'agents.add_assetrecord')):
                return _permission_denied()
            if not isinstance(data.get('machine_id'), str):
                return JsonResponse({'error': 'endpoint_identity_required'}, status=400)
            occurred_at = parse_datetime(str(data.get('occurred_at') or ''))
            if occurred_at is None:
                return JsonResponse({'error': 'record_date_invalid'}, status=400)
            if timezone.is_naive(occurred_at):
                occurred_at = timezone.make_aware(occurred_at, timezone.get_current_timezone())
            record = add_asset_record(
                endpoint.pk, data.get('machine_id'), request.user,
                record_type=data.get('record_type'), description=data.get('description'),
                occurred_at=occurred_at,
                previous_responsible=data.get('previous_responsible', ''),
                new_responsible=data.get('new_responsible', ''),
            )
            return JsonResponse({'ok': True, 'record_id': str(record.pk)}, status=201)
        else:
            return JsonResponse({'error': 'action_invalid'}, status=400)
    except AssetRegistryError as error:
        return JsonResponse({'error': error.code, **error.details}, status=error.status)
    except IntegrityError:
        return JsonResponse({'error': 'asset_identity_conflict'}, status=409)

    return JsonResponse({'ok': True, 'asset_id': str(asset.pk), 'asset_code': asset.asset_code}, status=201)


def models_q_asset_query(value):
    from agents.identity import meaningful_serial

    query = Q(asset_code__icontains=value)
    normalized = meaningful_serial(value)
    if normalized:
        query |= Q(serial_normalized__icontains=normalized)
    return query
