"""Closed administrative contracts; no passwords, shell or domain operations."""
import re
import uuid

from django.conf import settings

from .automation import endpoint_identity
from .models import AuditEvent


PERMISSIONS = {
    'start_service': 'start_windows_service', 'stop_service': 'stop_windows_service',
    'restart_service': 'restart_windows_service', 'gpupdate': 'refresh_computer_policy',
    'create_local_user': 'create_local_account', 'set_local_user_enabled': 'set_local_account_enabled',
    'reset_local_password': 'reset_local_account_password',
}
SERVICES = frozenset(('start_service', 'stop_service', 'restart_service'))
ACCOUNTS = frozenset(('create_local_user', 'set_local_user_enabled', 'reset_local_password'))
PROTECTED_SERVICES = frozenset(name.casefold() for name in (
    'RpcSs', 'RpcEptMapper', 'DcomLaunch', 'SamSs', 'EventLog', 'WinDefend', 'Sense', 'WdNisSvc',
    'SecurityHealthService', 'wscsvc', 'MpsSvc', 'BFE', 'Winmgmt', 'Schedule', 'WinRM', 'Netlogon',
    'Dnscache', 'Dhcp', 'NlaSvc', 'nsi', 'ProfSvc', 'LSM', 'UserManager', 'Power', 'PlugPlay',
    'EventSystem', 'Appinfo', 'KeyIso', 'CryptSvc', 'gpsvc', 'TrustedInstaller', 'WaaSMedicSvc',
    'wuauserv', 'TermService', 'RemoteRegistry'))
PROTECTED_USERS = frozenset(('administrator', 'administrador', 'guest', 'convidado', 'defaultaccount',
    'wdagutilityaccount', 'krbtgt', 'system', 'localservice', 'networkservice'))


def validate_parameters(action, params):
    fields = ({'service_name'} if action in SERVICES else set() if action == 'gpupdate' else
        {'user_name', 'secret_reference'} if action == 'create_local_user' else
        {'user_name', 'account_sid', 'enabled'} if action == 'set_local_user_enabled' else
        {'user_name', 'account_sid', 'secret_reference'})
    if not isinstance(params, dict) or set(params) != fields:
        raise ValueError('invalid_admin_parameters')
    if action in SERVICES:
        name = params['service_name']
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', name, re.ASCII):
            raise ValueError('invalid_service_name')
        if name.casefold() in PROTECTED_SERVICES or name.casefold().startswith('nightowl'):
            raise ValueError('protected_service')
    if action in ACCOUNTS:
        name = params['user_name']
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,19}', name, re.ASCII)
                or name.casefold() in PROTECTED_USERS):
            raise ValueError('protected_or_invalid_local_account')
        if 'account_sid' in params:
            sid = params['account_sid']
            if (not isinstance(sid, str) or not re.fullmatch(r'S-1-5-21-(?:[0-9]{1,10}-){3}[0-9]{1,10}', sid)
                    or any(int(part) > 4294967295 for part in sid.split('-')[4:]) or int(sid.rsplit('-', 1)[1]) <= 1000):
                raise ValueError('protected_or_invalid_account_sid')
        if 'enabled' in params and type(params['enabled']) is not bool:
            raise ValueError('invalid_account_enabled')
        if 'secret_reference' in params:
            try:
                if not isinstance(params['secret_reference'], str) or str(uuid.UUID(params['secret_reference'])) != params['secret_reference']:
                    raise ValueError
            except ValueError:
                raise ValueError('invalid_secret_reference') from None
    return dict(params)


def binding(job, operator):
    return {'operator_id': str(operator.pk), 'operator': operator.get_username(),
        'target': endpoint_identity(job.endpoint), 'payload': job.payload,
        'timeout_seconds': job.timeout_seconds, 'expires_at': job.expires_at.isoformat() if job.expires_at else None}


def validate_dispatch(job, operator, creating=False):
    action = job.payload['action_id']
    if not getattr(settings, 'NIGHTOWL_ADMIN_ACTIONS_ENABLED', False):
        raise ValueError('administrative_actions_disabled')
    if action in ACCOUNTS:
        # No credential is issued, queued, resolved or executed until these gates exist.
        raise ValueError('local_account_isolation_unavailable' if action == 'set_local_user_enabled'
                         else 'protected_secret_delivery_unavailable')
    scopes = getattr(settings, 'NIGHTOWL_ADMIN_ACTION_SCOPES', {})
    if not isinstance(scopes, dict):
        raise ValueError('invalid_admin_scope')
    scope = scopes.get(str(job.endpoint.pk), {})
    allowed = scope.get(action) if isinstance(scope, dict) else None
    if action in SERVICES:
        name = job.payload['parameters']['service_name']
        if not isinstance(allowed, list) or not any(isinstance(v, str) and v.casefold() == name.casefold() for v in allowed):
            raise ValueError('admin_resource_not_authorized')
    elif allowed is not True:
        raise ValueError('admin_resource_not_authorized')
    expected = binding(job, operator)
    if not creating:
        evidence = list(AuditEvent.objects.filter(event_type='job.created', endpoint=job.endpoint,
            metadata__job_id=str(job.pk)).values_list('metadata', flat=True)[:2])
        if len(evidence) != 1 or evidence[0].get('authorization') != expected:
            raise ValueError('admin_authorization_binding_changed')


def validate_completion(action, params, output):
    if action in ACCOUNTS:
        raise ValueError('local_account_execution_blocked')
    common = {'schema_version', 'action_id', 'action_version', 'success'}
    fields = {'service_name', 'state', 'changed'} if action in SERVICES else {'request_accepted', 'processing_confirmed'}
    if (not isinstance(output, dict) or set(output) != common | fields
            or type(output['schema_version']) is not int or output['schema_version'] != 1
            or output['action_id'] != action or type(output['action_version']) is not int
            or output['action_version'] != 1 or output['success'] is not True):
        raise ValueError('invalid_admin_confirmation')
    if action in SERVICES:
        if (output['service_name'] != params['service_name'] or type(output['changed']) is not bool
                or output['state'] != ('Stopped' if action == 'stop_service' else 'Running')):
            raise ValueError('invalid_service_confirmation')
    elif output['request_accepted'] is not True or output['processing_confirmed'] is not False:
        raise ValueError('invalid_policy_confirmation')
