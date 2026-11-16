"""Read-only, bounded Windows service listing over the existing internal-job protocol."""
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

PAGE_SIZE = 20
MAX_PAGES = 40
MAX_SERVICES = PAGE_SIZE * MAX_PAGES
CURSOR_MAX_AGE_SECONDS = 600
PAGED_SERVICE_CAPABILITY = 'collect_services_paged:2'
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
    if not internal_capability(endpoint, PAGED_SERVICE_CAPABILITY):
        return False, 'capability_unconfirmed', 'O agente não anunciou a capacidade de consulta paginada. Pode ser necessária uma versão Windows mais recente.'
    return True, '', ''


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
    expected = {'endpoint_id', 'machine_id', 'operator_id', 'snapshot_id', 'next_after_name',
                'page_index', 'total_services', 'rows_collected', 'collected_at'}
    if (not isinstance(data, dict) or set(data) != expected
            or data.get('endpoint_id') != str(endpoint.pk)
            or data.get('machine_id') != endpoint.machine_id
            or data.get('operator_id') != str(user.pk)
            or not isinstance(data.get('snapshot_id'), str) or len(data['snapshot_id']) != 32
            or not isinstance(data.get('next_after_name'), str) or not data['next_after_name']
            or type(data.get('page_index')) is not int or not 1 <= data['page_index'] < MAX_PAGES
            or type(data.get('total_services')) is not int or not 0 <= data['total_services'] <= MAX_SERVICES
            or type(data.get('rows_collected')) is not int or not 1 <= data['rows_collected'] < MAX_SERVICES
            or data.get('rows_collected') != data.get('page_index') * PAGE_SIZE
            or data.get('rows_collected') >= data.get('total_services')
            or not isinstance(data.get('collected_at'), str)):
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
    if page_index >= MAX_PAGES or rows_collected >= MAX_SERVICES:
        raise ValueError('service_limit_exceeded')
    return signing.dumps({
        'endpoint_id': str(endpoint.pk), 'machine_id': endpoint.machine_id,
        'operator_id': str(user.pk), 'snapshot_id': output['snapshot_id'],
        'next_after_name': output['next_after_name'], 'page_index': page_index,
        'total_services': output['total_services'], 'rows_collected': rows_collected,
        'collected_at': output['collected_at'],
    }, salt=CURSOR_SALT, compress=True)


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
        return _no_store(Response({'available': available, 'reason_code': code, 'reason': reason,
                                   'page_size': PAGE_SIZE if available else None,
                                   'max_services': MAX_SERVICES if available else None}))


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

        page_index = cursor['page_index'] + 1 if cursor else 1
        payload = {'schema_version': 2, 'page_size': PAGE_SIZE,
            'after_name': cursor['next_after_name'] if cursor else '',
            'expected_snapshot_id': cursor['snapshot_id'] if cursor else '',
            'page_index': page_index, 'rows_collected': cursor['rows_collected'] if cursor else 0,
            'expected_total_services': cursor['total_services'] if cursor else 0,
            'expected_collected_at': cursor['collected_at'] if cursor else '',
            'endpoint_machine_id': endpoint.machine_id}
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
                                   'request_id': request_id}, status=202))

    def get(self, request, endpoint_id, job_id):
        endpoint, denied = self._endpoint(endpoint_id, request, require_available=False)
        if denied:
            return denied
        job = AgentJob.objects.filter(pk=job_id, endpoint=endpoint, job_type='collect_services',
            created_by=request.user.get_username(), payload__schema_version=2).first()
        if job is None:
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
                     'has_more': output['has_more'], 'cursor': next_cursor},
            'page_index': job.payload['page_index']}))
