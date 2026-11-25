import re
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.utils import timezone

from .models import AdminTerminalAccessGrant, AgentMachine, AuditEvent, TerminalSession
from .services import create_audit_event

MAX_ADMIN_TERMINAL_GRANT_MINUTES = 60


def _context(value, label, maximum):
    value = str(value or '').strip()
    if not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValidationError(f'{label} é obrigatório.')
    if re.search(r'(?i)(password|passwd|token|credential|bearer|private key)', value):
        raise ValidationError(f'{label} parece conter dado sensível.')
    return value


@transaction.atomic
def change_admin_terminal_grant(*, actor, endpoint_id, machine_id, action, reason='', approval_ref='', ttl_minutes=None):
    if action not in {'grant', 'revoke'}:
        raise ValidationError('Ação inválida.')
    if not actor.is_active or not actor.has_perm('agents.manage_admin_terminal_grant'):
        raise PermissionDenied('Permissão agents.manage_admin_terminal_grant obrigatória.')
    reason = _context(reason, 'Motivo', 500)
    approval_ref = _context(approval_ref, 'Referência', 200)
    if action == 'grant':
        if type(ttl_minutes) is not int or not 1 <= ttl_minutes <= MAX_ADMIN_TERMINAL_GRANT_MINUTES:
            raise ValidationError(f'Validade deve ser de 1 a {MAX_ADMIN_TERMINAL_GRANT_MINUTES} minutos.')
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '3s'")
    endpoint = AgentMachine.objects.select_for_update().filter(pk=endpoint_id).first()
    if not endpoint or str(endpoint.machine_id or '').casefold() != str(machine_id or '').strip().casefold():
        raise PermissionDenied('Identidade UUID/Machine ID divergente.')
    if AgentMachine.objects.filter(machine_id__iexact=endpoint.machine_id).exclude(pk=endpoint.pk).exists():
        raise PermissionDenied('Machine ID ambíguo.')
    grant = AdminTerminalAccessGrant.objects.select_for_update().filter(endpoint=endpoint, operator=actor).first()
    previous = {'enabled': grant.enabled, 'expires_at': grant.expires_at.isoformat()} if grant else None
    if action == 'grant':
        if not endpoint.is_active or endpoint.has_terminal_lifecycle:
            raise PermissionDenied('Endpoint indisponível.')
        if not actor.has_perm('agents.open_admin_terminalsession'):
            raise PermissionDenied('Operador sem autorização RBAC para abrir terminal administrativo.')
        expiry = timezone.now() + timedelta(minutes=ttl_minutes)
        if grant:
            grant.enabled, grant.expires_at = True, expiry
            grant.created_by, grant.reason, grant.approval_ref = actor, reason, approval_ref
            grant.save()
        else:
            grant = AdminTerminalAccessGrant.objects.create(operator=actor, endpoint=endpoint,
                created_by=actor, expires_at=expiry, reason=reason, approval_ref=approval_ref)
        event = 'terminal.admin_grant_granted'
    else:
        if not grant:
            raise ValidationError('Grant administrativo inexistente.')
        grant.enabled = False
        grant.save(update_fields=['enabled', 'updated_at'])
        event = 'terminal.admin_grant_revoked'
        from .terminal_sessions import close_session
        closed = 0
        for session_id in TerminalSession.objects.filter(endpoint=endpoint, operator=actor,
                mode='admin_auth', status__in=TerminalSession.ACTIVE).values_list('pk', flat=True):
            closed += close_session(session_id, 'admin_grant_revoked', status='cancelled').status == 'cancelled'
    create_audit_event(event_type=event, title=event.replace('.', ' '), description=reason if action == 'grant' else 'Revogação administrativa do grant.',
        endpoint=endpoint, actor_type=AuditEvent.ACTOR_USER, actor_name=actor.get_username(),
        metadata={'grant_id': str(grant.pk), 'operator_id': str(actor.pk), 'endpoint_uuid': str(endpoint.pk),
            'machine_id': endpoint.machine_id, 'action': action, 'expires_at': grant.expires_at.isoformat(),
            'approval_ref': approval_ref, 'previous': previous, 'reason': reason,
            'closed_admin_sessions': closed if action == 'revoke' else 0})
    return {'action': action, 'grant_id': str(grant.pk), 'enabled': grant.enabled,
        'expires_at': grant.expires_at.isoformat()}
