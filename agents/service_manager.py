"""Read-only, bounded Windows service listing over the existing internal-job protocol."""
import re
import uuid
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.authz import is_nightowl_technical_user
from .internal_jobs import create_internal_job, internal_capability, validate_internal_completion
from .models import AgentJob, AgentMachine
from .service_collection_policy import service_collection_endpoint_allowed
from . import administrative_actions as admin
from .operational_actions import create_action_job, validate_payload as validate_action_payload

PAGE_SIZE = 20
MAX_PAGES = 40
MAX_SERVICES = PAGE_SIZE * MAX_PAGES
PAGE_SIZE_V3 = 100
MAX_PAGES_V3 = 800
PAGE_RESULT_BYTES_V3 = 48 * 1024
CURSOR_MAX_AGE_SECONDS = 600
PAGED_SERVICE_CAPABILITY = 'collect_services_paged:2'
PAGED_SERVICE_CAPABILITY_V3 = 'collect_services_paged:3'
CURSOR_SALT = 'agents.windows-service-manager.cursor.v2'
FINAL_STATUSES = {
    AgentJob.STATUS_COMPLETED, AgentJob.STATUS_FAILED, AgentJob.STATUS_EXPIRED,
    AgentJob.STATUS_CANCELLED, AgentJob.STATUS_TIMED_OUT, AgentJob.STATUS_UNSUPPORTED,
    AgentJob.STATUS_INVALID_PARAMETERS, AgentJob.STATUS_INTERRUPTED,
}


def _operator_allowed(user):
    return bool(user and user.is_active and is_nightowl_technical_user(user)
                and user.has_perm('agents.view_agentmachine')
                and user.has_perm('agents.view_agentjob')
                and user.has_perm('agents.add_agentjob'))


def _availability(endpoint):
    if not getattr(settings, 'NIGHTOWL_SERVICE_COLLECTION_ENABLED', False):
        return False, 'collection_disabled', 'A consulta de serviços está desativada no backend.'
    allowed, reason_code = service_collection_endpoint_allowed(
        endpoint.pk, getattr(settings, 'NIGHTOWL_SERVICE_COLLECTION_ALLOWED_ENDPOINT_IDS', ()))
    if not allowed:
        if reason_code == 'allowlist_invalid':
            return False, reason_code, 'A autorização para consulta de serviços está inválida.'
        return False, reason_code, 'Este endpoint não está autorizado para consulta de serviços.'
    if not endpoint.is_active:
        return False, 'endpoint_inactive', 'O endpoint não está ativo para novas solicitações.'
    if endpoint.status != AgentMachine.STATUS_ONLINE:
        return False, 'endpoint_offline', 'O agente precisa estar online para consultar os serviços.'
    if (endpoint.has_terminal_lifecycle or not endpoint.machine_id
            or AgentMachine.objects.filter(machine_id__iexact=endpoint.machine_id).count() != 1):
        return False, 'identity_unavailable', 'A identidade do endpoint não está confirmada como única.'
    if not _service_page_version(endpoint):
        return False, 'capability_unconfirmed', 'O agente não anunciou a capacidade de consulta paginada. Pode ser necessária uma versão Windows mais recente.'
    return True, '', ''


def _service_page_version(endpoint):
    if internal_capability(endpoint, PAGED_SERVICE_CAPABILITY_V3):
        return 3
    if internal_capability(endpoint, PAGED_SERVICE_CAPABILITY):
        return 2
    return 0


def _service_action_reason(code):
    return {
        'service_control_disabled': 'O controle de serviços está desativado no backend.',
        'administrative_actions_disabled': 'Ações administrativas estão desativadas no backend.',
        'operational_actions_disabled': 'Ações operacionais estão desativadas no backend.',
        'admin_resource_not_authorized': 'Este serviço ou ação não está autorizado para o endpoint.',
        'service_operator_not_authorized': 'Seu usuário não está incluído no escopo deste endpoint.',
        'action_forbidden': 'Seu usuário não tem permissão para esta ação.',
        'action_scope_forbidden': 'A ação não está autorizada para este operador e endpoint.',
        'action_capability_unconfirmed': 'A capacidade necessária do agente não foi confirmada em heartbeat recente.',
        'service_endpoint_ineligible': 'O endpoint precisa estar ativo, online e com identidade única confirmada.',
        'endpoint_inactive': 'O endpoint está inativo ou em ciclo de vida terminal.',
        'identity_unavailable': 'A identidade do endpoint não está confirmada como única.',
        'job_conflict': 'Há outro job em andamento neste endpoint.',
        'protected_service': 'Este serviço é protegido e não pode ser controlado pelo NightOwl.',
    }.get(code, 'Ação indisponível por política ou requisito do agente.')


def _service_control_availability(endpoint, user):
    """Evaluate each allowlisted operation through the same dispatcher validator."""
    actions = {}
    scopes = getattr(settings, 'NIGHTOWL_ADMIN_ACTION_SCOPES', {})
    endpoint_scope = scopes.get(str(endpoint.pk), {}) if isinstance(scopes, dict) else {}
    endpoint_scope = endpoint_scope if isinstance(endpoint_scope, dict) else {}
    for action in sorted(admin.SERVICES):
        names = endpoint_scope.get(action)
        entries = {'enabled': False, 'services': [], 'reason': ''}
        if not isinstance(names, list):
            entries['reason'] = _service_action_reason('admin_resource_not_authorized')
            actions[action] = entries
            continue
        failures = []
        for name in names:
            try:
                payload = validate_action_payload({'action_id': action, 'action_version': 1,
                    'parameters': {'service_name': name}})
                candidate = AgentJob(endpoint=endpoint, job_type='operational_action', payload=payload,
                    created_by=user.get_username(), timeout_seconds=15,
                    expires_at=timezone.now() + timedelta(minutes=15))
                from .operational_actions import validate_dispatch
                validate_dispatch(candidate, creating=True)
                entries['services'].append(name)
            except (PermissionError, ValueError) as exc:
                failures.append(str(exc))
        entries['enabled'] = bool(entries['services'])
        if not entries['enabled']:
            code = failures[0] if failures else 'admin_resource_not_authorized'
            entries['reason'] = _service_action_reason(code)
        actions[action] = entries
    return actions


def _no_store(response):
    response['Cache-Control'] = 'no-store, private'
    return response


def _cursor_decode(value, endpoint, user):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError('invalid_cursor')
    try:
        data = signing.loads(value, salt=CURSOR_SALT, max_age=CURSOR_MAX_AGE_SECONDS)
    except signing.SignatureExpired:
        raise ValueError('cursor_expired') from None
    except signing.BadSignature:
        raise ValueError('invalid_cursor') from None
    version = data.get('schema_version', 2) if isinstance(data, dict) else None
    v2_fields = {'endpoint_id', 'machine_id', 'operator_id', 'snapshot_id', 'next_after_name',
                 'page_index', 'total_services', 'rows_collected', 'collected_at'}
    v3_fields = v2_fields | {'schema_version', 'page_size', 'max_page_bytes'}
    expected = v3_fields if version == 3 else v2_fields
    page_index_max = MAX_PAGES_V3 if version == 3 else MAX_PAGES
    rows_valid = False
    if (isinstance(data, dict) and type(data.get('page_index')) is int
            and type(data.get('rows_collected')) is int and 1 <= data['rows_collected'] < MAX_SERVICES):
        rows_valid = (data['page_index'] <= data['rows_collected'] <= data['page_index'] * PAGE_SIZE_V3
                      if version == 3 else data['rows_collected'] == data['page_index'] * PAGE_SIZE)
    if (version not in {2, 3} or not isinstance(data, dict) or set(data) != expected
            or data.get('endpoint_id') != str(endpoint.pk)
            or data.get('machine_id') != endpoint.machine_id
            or data.get('operator_id') != str(user.pk)
            or not isinstance(data.get('snapshot_id'), str) or len(data['snapshot_id']) != 32
            or not isinstance(data.get('next_after_name'), str) or not data['next_after_name']
            or type(data.get('page_index')) is not int or not 1 <= data['page_index'] < page_index_max
            or type(data.get('total_services')) is not int or not 0 <= data['total_services'] <= MAX_SERVICES
            or not isinstance(data.get('collected_at'), str)
            or not rows_valid
            or data.get('rows_collected') >= data.get('total_services')
            or (version == 3 and (type(data.get('schema_version')) is not int
                                  or type(data.get('page_size')) is not int or data['page_size'] != PAGE_SIZE_V3
                                  or type(data.get('max_page_bytes')) is not int
                                  or data['max_page_bytes'] != PAGE_RESULT_BYTES_V3))):
        raise ValueError('invalid_cursor')
    if version == 3 and data['rows_collected'] > data['page_index'] * PAGE_SIZE_V3:
        raise ValueError('invalid_cursor')
    return data


def _cursor_encode(endpoint, user, output, job):
    rows_collected = job.payload['rows_collected'] + len(output['services'])
    page_index = job.payload['page_index']
    expected_total = job.payload['expected_total_services']
    expected_at = job.payload['expected_collected_at']
    if (rows_collected > output['total_services']
            or (expected_total and expected_total != output['total_services'])
            or (expected_at and expected_at != output['collected_at'])
            or (output['has_more'] and rows_collected >= output['total_services'])
            or (not output['has_more'] and rows_collected != output['total_services'])):
        raise ValueError('service_snapshot_changed')
    if not output['has_more']:
        return None
    if rows_collected >= MAX_SERVICES:
        raise ValueError('service_limit_exceeded')
    max_pages = MAX_PAGES_V3 if job.payload.get('schema_version') == 3 else MAX_PAGES
    if page_index >= max_pages:
        raise ValueError('service_limit_exceeded')
    data = {
        'endpoint_id': str(endpoint.pk), 'machine_id': endpoint.machine_id,
        'operator_id': str(user.pk), 'snapshot_id': output['snapshot_id'],
        'next_after_name': output['next_after_name'], 'page_index': page_index,
        'total_services': output['total_services'], 'rows_collected': rows_collected,
        'collected_at': output['collected_at'],
    }
    if job.payload.get('schema_version') == 3:
        if page_index >= MAX_PAGES_V3 or rows_collected >= MAX_SERVICES:
            raise ValueError('service_limit_exceeded')
        data.update({'schema_version': 3, 'page_size': PAGE_SIZE_V3, 'max_page_bytes': PAGE_RESULT_BYTES_V3})
    return signing.dumps(data, salt=CURSOR_SALT, compress=True)


class ServiceManagerAvailabilityView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, endpoint_id):
        if not _operator_allowed(request.user):
            return _no_store(Response({'error': 'service_manager_forbidden'}, status=403))
        endpoint = AgentMachine.objects.filter(pk=endpoint_id).first()
        if endpoint is None:
            return _no_store(Response({'error': 'endpoint_not_found'}, status=404))
        available, code, reason = _availability(endpoint)
        version = _service_page_version(endpoint) if available else 0
        return _no_store(Response({'available': available, 'reason_code': code, 'reason': reason,
                                   'schema_version': version or None,
                                   'page_size': (PAGE_SIZE_V3 if version == 3 else PAGE_SIZE) if available else None,
                                   'max_page_bytes': PAGE_RESULT_BYTES_V3 if version == 3 else None,
                                   'max_services': MAX_SERVICES if available else None,
                                   'service_actions': _service_control_availability(endpoint, request.user)}))


class ServiceManagerJobView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def _endpoint(self, endpoint_id, request, *, require_available=True):
        if not _operator_allowed(request.user):
            return None, _no_store(Response({'error': 'service_manager_forbidden'}, status=403))
        endpoint = AgentMachine.objects.filter(pk=endpoint_id).first()
        if endpoint is None:
            return None, _no_store(Response({'error': 'endpoint_not_found'}, status=404))
        if require_available:
            available, code, reason = _availability(endpoint)
            if not available:
                return None, _no_store(Response({'error': code, 'detail': reason}, status=409))
        return endpoint, None

    def post(self, request, endpoint_id):
        endpoint, denied = self._endpoint(endpoint_id, request, require_available=False)
        if denied:
            return denied
        body = request.data
        if not isinstance(body, dict) or set(body) != {'request_id', 'cursor'}:
            return _no_store(Response({'error': 'invalid_request'}, status=400))
        try:
            request_id = str(uuid.UUID(body['request_id']))
            cursor = _cursor_decode(body['cursor'], endpoint, request.user) if body['cursor'] is not None else None
        except (ValueError, TypeError, AttributeError) as exc:
            return _no_store(Response({'error': str(exc) if str(exc) in {'cursor_expired', 'invalid_cursor'} else 'invalid_request'}, status=400))

        page_version = cursor.get('schema_version', 2) if cursor else _service_page_version(endpoint)
        if page_version not in {2, 3}:
            return _no_store(Response({'error': 'capability_unconfirmed'}, status=409))
        page_size = PAGE_SIZE_V3 if page_version == 3 else PAGE_SIZE
        page_index = cursor['page_index'] + 1 if cursor else 1
        payload = {'schema_version': page_version, 'page_size': page_size,
            'after_name': cursor['next_after_name'] if cursor else '',
            'expected_snapshot_id': cursor['snapshot_id'] if cursor else '',
            'page_index': page_index, 'rows_collected': cursor['rows_collected'] if cursor else 0,
            'expected_total_services': cursor['total_services'] if cursor else 0,
            'expected_collected_at': cursor['collected_at'] if cursor else '',
            'endpoint_machine_id': endpoint.machine_id}
        if page_version == 3:
            payload['max_page_bytes'] = PAGE_RESULT_BYTES_V3
        try:
            with transaction.atomic():
                locked_endpoint = AgentMachine.objects.select_for_update().get(pk=endpoint.pk)
                existing_request = AgentJob.objects.filter(endpoint=locked_endpoint, job_type='collect_services',
                    correlation_id=request_id, created_by=request.user.get_username()).first()
                if existing_request:
                    if existing_request.payload != payload:
                        return _no_store(Response({'error': 'request_id_conflict'}, status=409))
                    job = existing_request
                else:
                    available, reason_code, _ = _availability(locked_endpoint)
                    if not available:
                        return _no_store(Response({'error': reason_code}, status=409))
                    # A repeated cursor cannot create parallel/replayed page jobs.
                    if cursor and AgentJob.objects.filter(endpoint=locked_endpoint, job_type='collect_services',
                            payload=payload).exists():
                        return _no_store(Response({'error': 'cursor_already_used'}, status=409))
                    job = create_internal_job(request.user, locked_endpoint.pk, 'collect_services', payload,
                                              request_id=request_id)
        except PermissionError:
            return _no_store(Response({'error': 'service_manager_forbidden'}, status=403))
        except AgentMachine.DoesNotExist:
            return _no_store(Response({'error': 'endpoint_not_found'}, status=404))
        except ValueError as exc:
            code = str(exc)
            if code in {'service_collection_endpoint_not_allowed', 'service_collection_allowlist_invalid'}:
                return _no_store(Response({'error': code}, status=409))
            status_code = 409 if code in {'identity_unavailable', 'capability_unconfirmed', 'job_conflict', 'internal_job_disabled'} else 400
            return _no_store(Response({'error': code}, status=status_code))
        return _no_store(Response({'job_id': str(job.pk), 'status': job.status,
                                   'request_id': request_id, 'schema_version': page_version,
                                   'page_size': page_size}, status=202))

    def get(self, request, endpoint_id, job_id):
        endpoint, denied = self._endpoint(endpoint_id, request, require_available=False)
        if denied:
            return denied
        job = AgentJob.objects.filter(pk=job_id, endpoint=endpoint, job_type='collect_services',
            created_by=request.user.get_username()).first()
        if job is None:
            return _no_store(Response({'error': 'service_job_not_found'}, status=404))
        if not isinstance(job.payload, dict) or job.payload.get('schema_version') not in {2, 3}:
            return _no_store(Response({'error': 'service_job_not_found'}, status=404))
        if (job.payload.get('endpoint_machine_id') != endpoint.machine_id or not endpoint.machine_id
                or AgentMachine.objects.filter(machine_id__iexact=endpoint.machine_id).count() != 1):
            return _no_store(Response({'error': 'identity_unavailable'}, status=409))
        if job.status not in FINAL_STATUSES:
            return _no_store(Response({'status': job.status, 'job_id': str(job.pk),
                                       'created_at': job.created_at.isoformat(),
                                       'dispatched_at': job.dispatched_at.isoformat() if job.dispatched_at else None,
                                       'started_at': job.started_at.isoformat() if job.started_at else None}))
        if job.status != AgentJob.STATUS_COMPLETED or job.output_truncated:
            allowed_errors = {'SERVICE_SNAPSHOT_EXPIRED': 'A cópia temporária do agente expirou. Inicie uma nova coleta.',
                              'SERVICE_LIMIT_EXCEEDED': 'A lista excede o limite seguro de 800 serviços; nenhuma lista parcial foi apresentada.',
                              'JOB_TIMEOUT': 'A consulta excedeu o tempo limite. Inicie uma nova coleta.'}
            code = job.error_code if job.error_code in allowed_errors else job.status
            return _no_store(Response({'status': job.status, 'job_id': str(job.pk), 'error_code': code,
                                       'detail': allowed_errors.get(code, 'A consulta não foi concluída.')}))
        try:
            result = job.result if isinstance(job.result, dict) else {}
            output = result.get('output')
            validate_internal_completion(job, result)
            if cursor := _cursor_encode(endpoint, request.user, output, job):
                next_cursor = cursor
            else:
                next_cursor = None
        except (ValueError, TypeError, KeyError):
            return _no_store(Response({'error': 'service_result_invalid'}, status=502))
        return _no_store(Response({'status': job.status, 'job_id': str(job.pk),
            'page': {'services': output['services'], 'collected_at': output['collected_at'],
                     'snapshot_id': output['snapshot_id'], 'total_services': output['total_services'],
                     'has_more': output['has_more'], 'cursor': next_cursor,
                     'schema_version': job.payload['schema_version'],
                     'page_size': job.payload['page_size'],
                     'rows_collected': job.payload['rows_collected'] + len(output['services'])},
            'schema_version': job.payload['schema_version'], 'page_index': job.payload['page_index']}))


class ServiceManagerActionView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def _endpoint(self, request, endpoint_id):
        if not _operator_allowed(request.user):
            return None, _no_store(Response({'error': 'service_manager_forbidden'}, status=403))
        endpoint = AgentMachine.objects.filter(pk=endpoint_id).first()
        if endpoint is None:
            return None, _no_store(Response({'error': 'endpoint_not_found'}, status=404))
        return endpoint, None

    def post(self, request, endpoint_id):
        endpoint, denied = self._endpoint(request, endpoint_id)
        if denied:
            return denied
        body = request.data
        if not isinstance(body, dict) or set(body) != {'request_id', 'action_id', 'service_name'}:
            return _no_store(Response({'error': 'invalid_request'}, status=400))
        try:
            request_id = str(uuid.UUID(body['request_id']))
            if request_id != body['request_id']:
                raise ValueError
            payload = validate_action_payload({'action_id': body['action_id'], 'action_version': 1,
                'parameters': {'service_name': body['service_name']}})
            if payload['action_id'] not in admin.SERVICES:
                raise ValueError('unsupported_service_action')
            job = create_action_job(request.user, endpoint.pk, payload, request_id=request_id)
        except PermissionError:
            return _no_store(Response({'error': 'action_forbidden'}, status=403))
        except AgentMachine.DoesNotExist:
            return _no_store(Response({'error': 'endpoint_not_found'}, status=404))
        except (ValueError, TypeError, AttributeError) as exc:
            code = str(exc)
            if code not in {'service_control_disabled', 'administrative_actions_disabled',
                'operational_actions_disabled', 'admin_resource_not_authorized', 'action_forbidden',
                'action_scope_forbidden', 'action_capability_unconfirmed', 'service_endpoint_ineligible',
                'endpoint_inactive', 'identity_unavailable', 'job_conflict', 'protected_service', 'request_id_conflict', 'invalid_service_name',
                'invalid_action_envelope', 'unknown_action', 'unsupported_action_version',
                'invalid_action_parameters', 'protected_or_invalid_service', 'unsupported_service_action'}:
                code = 'invalid_request'
            status_code = 409 if code in {'service_control_disabled', 'administrative_actions_disabled',
                'operational_actions_disabled', 'admin_resource_not_authorized', 'action_scope_forbidden',
                'action_capability_unconfirmed', 'service_endpoint_ineligible', 'endpoint_inactive',
                'identity_unavailable', 'job_conflict'} else 400
            return _no_store(Response({'error': code}, status=status_code))
        return _no_store(Response({'job_id': str(job.pk), 'status': job.status,
            'request_id': request_id, 'action_id': payload['action_id'],
            'service_name': payload['parameters']['service_name']}, status=202))

    def get(self, request, endpoint_id, job_id):
        endpoint, denied = self._endpoint(request, endpoint_id)
        if denied:
            return denied
        job = AgentJob.objects.filter(pk=job_id, endpoint=endpoint,
            job_type='operational_action', created_by=request.user.get_username()).first()
        if not job or not isinstance(job.payload, dict) or job.payload.get('action_id') not in admin.SERVICES:
            return _no_store(Response({'error': 'service_action_not_found'}, status=404))
        payload = job.payload
        service_name = payload.get('parameters', {}).get('service_name') if isinstance(payload.get('parameters'), dict) else None
        response = {'job_id': str(job.pk), 'status': job.status, 'action_id': payload.get('action_id'),
            'service_name': service_name, 'created_at': job.created_at.isoformat(),
            'dispatched_at': job.dispatched_at.isoformat() if job.dispatched_at else None,
            'started_at': job.started_at.isoformat() if job.started_at else None,
            'finished_at': job.finished_at.isoformat() if job.finished_at else None,
            'result_confirmed': False, 'result': None}
        if job.status == AgentJob.STATUS_COMPLETED:
            try:
                from .operational_actions import validate_completion
                validate_completion(job, job.result)
                output = job.result['output']
                response['result_confirmed'] = True
                response['result'] = {'state': output['state'], 'changed': output['changed']}
            except (ValueError, TypeError, KeyError):
                response['status'] = AgentJob.STATUS_FAILED
                response['error_code'] = 'SERVICE_RESULT_UNCONFIRMED'
        elif job.status in FINAL_STATUSES:
            code = str(job.error_code or '').upper()
            response['error_code'] = code if re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', code, re.ASCII) else 'SERVICE_ACTION_FAILED'
        return _no_store(Response(response))
