"""DEC-04C.1: closed, versioned actions over the existing jobs protocol."""
import re
from datetime import timedelta
from types import MappingProxyType

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.authz import is_nightowl_technical_user
from .internal_jobs import internal_capability
from .models import AgentJob, AgentMachine, AuditEvent
from .services import create_audit_event
from . import administrative_actions as admin

CATALOG = MappingProxyType({
    'flush_dns': (1, 'agents.change_agentmachine', {}),
    'query_service': (1, 'agents.view_agentmachine', {'service_name': r'[A-Za-z0-9_.-]{1,256}'}),
    'run_approved_script': (1, 'agents.view_agentmachine', None),
    **{action: (1, 'agents.' + permission, None) for action, permission in admin.PERMISSIONS.items()},
})
TIMEOUT_SECONDS = 15


def validate_payload(payload):
    if not isinstance(payload, dict) or set(payload) != {'action_id', 'action_version', 'parameters'}:
        raise ValueError('invalid_action_envelope')
    action = payload['action_id']
    if not isinstance(action, str) or action not in CATALOG:
        raise ValueError('unknown_action')
    version, _, schema = CATALOG[action]
    if type(payload['action_version']) is not int or payload['action_version'] != version:
        raise ValueError('unsupported_action_version')
    params = payload['parameters']
    if action in admin.PERMISSIONS:
        return {'action_id': action, 'action_version': version, 'parameters': admin.validate_parameters(action, params)}
    if action == 'run_approved_script':
        from .script_library import validate_reference
        return {'action_id': action, 'action_version': version, 'parameters': validate_reference(params)}
    if not isinstance(params, dict) or set(params) != set(schema):
        raise ValueError('invalid_action_parameters')
    for name, pattern in schema.items():
        if not isinstance(params[name], str) or not re.fullmatch(pattern, params[name], flags=re.ASCII):
            raise ValueError('invalid_action_parameters')
    return {'action_id': action, 'action_version': version, 'parameters': dict(params)}


def authorize(operator, action):
    if (not operator or not operator.is_active or not is_nightowl_technical_user(operator)
            or not operator.has_perm('agents.add_agentjob') or not operator.has_perm(CATALOG[action][1])):
        raise PermissionError('action_forbidden')


def authorize_scope(operator, endpoint, action, parameters=None):
    """When enabled, scope every action to an explicit endpoint, identity and operator."""
    if not getattr(settings, 'NIGHTOWL_OPERATIONAL_ACTION_SCOPED_ONLY', False):
        return
    scopes = getattr(settings, 'NIGHTOWL_OPERATIONAL_ACTION_SCOPES', {})
    scope = scopes.get(str(endpoint.pk)) if isinstance(scopes, dict) else None
    if (not isinstance(scope, dict) or not {'machine_id', 'operator_ids', 'actions'} <= set(scope)
            or set(scope) - {'machine_id', 'operator_ids', 'actions', 'scripts'}):
        raise PermissionError('action_scope_forbidden')
    if action == 'run_approved_script':
        scripts = scope.get('scripts')
        params = parameters if isinstance(parameters, dict) else {}
        identity = f"{params.get('script_id')}:{params.get('script_version')}:{params.get('sha256')}"
        if (not isinstance(scripts, list) or not scripts or
                any(not isinstance(item, str) or not re.fullmatch(r'[a-z0-9_]+:[1-9][0-9]*:[0-9a-f]{64}', item) for item in scripts)
                or identity not in scripts):
            raise PermissionError('action_scope_forbidden')
    operators, actions = scope['operator_ids'], scope['actions']
    if (not isinstance(scope['machine_id'], str) or not scope['machine_id'] or
            scope['machine_id'] != endpoint.machine_id or
            not isinstance(operators, list) or not operators or
            any(not isinstance(item, str) or not item.isascii() or not item.isdecimal() for item in operators) or
            not isinstance(actions, list) or not actions or
            any(not isinstance(item, str) or item not in CATALOG for item in actions) or
            str(operator.pk) not in operators or action not in actions):
        raise PermissionError('action_scope_forbidden')


def validate_dispatch(job, *, creating=False):
    payload = validate_payload(job.payload)
    operator = get_user_model().objects.filter(username=job.created_by).first()
    authorize(operator, payload['action_id'])
    if not getattr(settings, 'NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED', False):
        raise ValueError('operational_actions_disabled')
    authorize_scope(operator, job.endpoint, payload['action_id'], payload['parameters'])
    if payload['action_id'] in admin.PERMISSIONS:
        admin.validate_dispatch(job, operator, creating)
    endpoint = job.endpoint
    capability = f"operational_action:{payload['action_id']}:{payload['action_version']}"
    if (not endpoint.machine_id or AgentMachine.objects.filter(machine_id__iexact=endpoint.machine_id).count() != 1
            or not internal_capability(endpoint, capability)):
        raise ValueError('action_capability_unconfirmed')
    if job.timeout_seconds is None or not 5 <= job.timeout_seconds <= TIMEOUT_SECONDS:
        raise ValueError('invalid_action_timeout')
    if payload['action_id'] == 'run_approved_script':
        from .script_library import validate_dispatch as validate_script_dispatch
        validate_script_dispatch(job, payload['parameters'])


@transaction.atomic
def create_action_job(operator, endpoint_id, payload):
    payload = validate_payload(payload)
    authorize(operator, payload['action_id'])
    endpoint = AgentMachine.objects.select_for_update().get(pk=endpoint_id)
    job = AgentJob(endpoint=endpoint, job_type='operational_action', payload=payload,
        created_by=operator.get_username(), timeout_seconds=TIMEOUT_SECONDS,
        expires_at=timezone.now() + timedelta(minutes=15))
    validate_dispatch(job, creating=True)
    if endpoint.jobs.filter(status__in=('queued', 'sent', 'running')).exists():
        raise ValueError('job_conflict')
    job.save()
    create_audit_event(event_type='job.created', title='Acao operacional autorizada',
        description=payload['action_id'], endpoint=endpoint, actor_type=AuditEvent.ACTOR_USER,
        actor_name=operator.get_username(), metadata={'job_id': str(job.pk), **payload,
            **({'authorization': admin.binding(job, operator)} if payload['action_id'] in admin.PERMISSIONS else {})})
    return job


def validate_completion(job, result):
    if not isinstance(job.payload, dict):
        raise ValueError('invalid_action_envelope')
    # A revoked script cannot start again, but its final evidence must still ACK.
    if job.payload.get('action_id') == 'run_approved_script':
        from .script_library import validate_completion as validate_script_completion
        if (set(job.payload) != {'action_id', 'action_version', 'parameters'}
                or type(job.payload['action_version']) is not int or job.payload['action_version'] != 1):
            raise ValueError('invalid_action_envelope')
        if not isinstance(result, dict) or result.get('machine_id') != job.endpoint.machine_id or result.get('output_truncated'):
            raise ValueError('invalid_action_confirmation')
        validate_script_completion(job.payload['parameters'], result.get('output'))
        return
    payload = validate_payload(job.payload)
    if not isinstance(result, dict) or result.get('machine_id') != job.endpoint.machine_id or result.get('output_truncated'):
        raise ValueError('invalid_action_confirmation')
    output = result.get('output')
    if payload['action_id'] in admin.PERMISSIONS:
        admin.validate_completion(payload['action_id'], payload['parameters'], output)
        return
    common = {'schema_version', 'action_id', 'action_version', 'success'}
    fields = {'service_name', 'state'} if payload['action_id'] == 'query_service' else set()
    if (not isinstance(output, dict) or set(output) != common | fields
            or type(output['schema_version']) is not int or output['schema_version'] != 1
            or output['action_id'] != payload['action_id'] or type(output['action_version']) is not int
            or output['action_version'] != payload['action_version'] or output['success'] is not True):
        raise ValueError('invalid_action_confirmation')
    if fields and (output['service_name'] != payload['parameters']['service_name'] or
            output['state'] not in ('Stopped', 'StartPending', 'StopPending', 'Running', 'ContinuePending', 'PausePending', 'Paused')):
        raise ValueError('invalid_service_confirmation')


class OperationalActionView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, endpoint_id):
        try:
            job = create_action_job(request.user, endpoint_id, request.data)
        except PermissionError:
            return Response({'error': 'action_forbidden'}, status=403)
        except AgentMachine.DoesNotExist:
            return Response({'error': 'endpoint_not_found'}, status=404)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=400)
        return Response({'job_id': str(job.pk), 'status': job.status}, status=201)
