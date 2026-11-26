from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.utils import timezone

from config.authz import is_nightowl_technical_user
from .models import AdminTerminalAccessGrant, AgentMachine, AuditEvent, TerminalSession
from .services import create_audit_event
from .terminal_grants import validate_grant_context
from . import terminal_grants as pilot

MAX_ADMIN_TERMINAL_GRANT_MINUTES = 60


@transaction.atomic
def change_admin_terminal_grant(*, actor, endpoint_id, machine_id=None, action, reason='', approval_ref='', ttl_minutes=None, beneficiary=None):
    if action not in ('grant', 'revoke'):
        raise ValidationError('Ação inválida.')
    from .terminal_sessions import has_explicit_admin_permission
    if (not is_nightowl_technical_user(actor) or
            not has_explicit_admin_permission(actor, 'manage_admin_terminal_grant')):
        raise PermissionDenied('Permissão agents.manage_admin_terminal_grant obrigatória.')
    pilot_only = settings.NIGHTOWL_TERMINAL_ADMIN_PILOT_ONLY
    if not isinstance(reason, str) or (approval_ref is not None and not isinstance(approval_ref, str)):
        raise ValidationError('Motivo inválido.')
    reason, reference = validate_grant_context(reason, approval_ref or ('' if pilot_only else 'not_required'))
    approval_ref = reference if approval_ref else ''
    if pilot_only and beneficiary is not None:
        raise ValidationError('O beneficiário do piloto é fixo.')
    if pilot_only and action == 'grant':
        if type(ttl_minutes) is not int or not 1 <= ttl_minutes <= MAX_ADMIN_TERMINAL_GRANT_MINUTES:
            raise ValidationError(f'Validade deve ser de 1 a {MAX_ADMIN_TERMINAL_GRANT_MINUTES} minutos.')
    elif ttl_minutes is not None:
        raise ValidationError('Validade não se aplica à revogação.')
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '3s'")
    if not pilot_only:
        if not isinstance(beneficiary, str) or not beneficiary.strip() or len(beneficiary) > 150:
            raise ValidationError('Informe o usuário NightOwl beneficiário.')
        endpoint = AgentMachine.objects.select_for_update().filter(pk=endpoint_id).first()
        users = list(get_user_model().objects.select_for_update().filter(username__iexact=beneficiary.strip())[:2])
        if endpoint is None or len(users) != 1:
            raise ValidationError('Endpoint ou beneficiário indisponível.')
        operator = users[0]
        if action == 'grant' and (not endpoint.machine_id or AgentMachine.objects.filter(
                machine_id__iexact=endpoint.machine_id).exclude(pk=endpoint.pk).exists()
                or not is_nightowl_technical_user(operator) or not operator.has_perm('agents.open_terminalsession')):
            raise PermissionDenied('Identidade ou beneficiário não autorizado.')
    elif action == 'grant':
        endpoint, operator = pilot._resolve_pilot(endpoint_id=endpoint_id, machine_id=machine_id,
            operator_username=pilot.PILOT_OPERATOR_USERNAME, lock=True)
    else:
        if str(machine_id or '').strip().casefold() != pilot.PILOT_MACHINE_ID:
            raise PermissionDenied('Machine ID esperado obrigatório.')
        endpoint, operator = pilot._resolve_pilot_for_revocation(endpoint_id=endpoint_id,
            operator_username=pilot.PILOT_OPERATOR_USERNAME, lock=True)
    if action == 'grant' and operator.pk == actor.pk:
        raise PermissionDenied('Autoconcessão não permitida.')
    grant = AdminTerminalAccessGrant.objects.select_for_update().filter(endpoint=endpoint, operator=operator).first()
    def expiry_text(value):
        return value.isoformat() if value is not None else None

    previous = {'enabled': grant.enabled, 'expires_at': expiry_text(grant.expires_at)} if grant else None
    if action == 'grant':
        if not endpoint.is_active or endpoint.has_terminal_lifecycle:
            raise PermissionDenied('Endpoint indisponível.')
        if not has_explicit_admin_permission(operator):
            raise PermissionDenied('Operador sem autorização RBAC para abrir terminal administrativo.')
        expiry = timezone.now() + timedelta(minutes=ttl_minutes) if pilot_only else None
        if grant:
            grant.enabled, grant.expires_at = True, expiry
            grant.created_by, grant.reason, grant.approval_ref = actor, reason, approval_ref
            grant.machine_id = endpoint.machine_id
            grant.save()
        else:
            grant = AdminTerminalAccessGrant.objects.create(operator=operator, endpoint=endpoint,
                created_by=actor, machine_id=endpoint.machine_id, expires_at=expiry, reason=reason, approval_ref=approval_ref)
        event = 'terminal.admin_grant_granted'
    else:
        if not grant:
            raise ValidationError('Grant administrativo inexistente.')
        grant.enabled = False
        grant.save(update_fields=['enabled', 'updated_at'])
        event = 'terminal.admin_grant_revoked'
        from .terminal_sessions import close_session
        closed = 0
        for session_id in TerminalSession.objects.filter(endpoint=endpoint, operator=operator,
                mode='admin_auth', status__in=TerminalSession.ACTIVE).values_list('pk', flat=True):
            closed += close_session(session_id, 'admin_grant_revoked', status='cancelled').status == 'cancelled'
    create_audit_event(event_type=event, title=event.replace('.', ' '), description=reason,
        endpoint=endpoint, actor_type=AuditEvent.ACTOR_USER, actor_name=actor.get_username(),
        metadata={'grant_id': str(grant.pk), 'operator_id': str(operator.pk), 'executor_id': str(actor.pk), 'endpoint_uuid': str(endpoint.pk),
            'machine_id': endpoint.machine_id, 'action': action, 'expires_at': expiry_text(grant.expires_at),
            'approval_ref': approval_ref, 'previous': previous, 'reason': reason,
            'closed_admin_sessions': closed if action == 'revoke' else 0})
    return {'action': action, 'grant_id': str(grant.pk), 'enabled': grant.enabled,
        'expires_at': expiry_text(grant.expires_at)}
