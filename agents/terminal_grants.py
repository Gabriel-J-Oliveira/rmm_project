"""Narrowly scoped, auditable management of the assisted ConPTY pilot grant."""
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.utils import timezone

from config.authz import is_nightowl_technical_user
from .models import AgentMachine, AuditEvent, TerminalAccessGrant, TerminalSession


PILOT_ENDPOINT_ID = uuid.UUID('c97045f2-35ff-48f1-afa5-095173fbb3dd')
PILOT_MACHINE_ID = '1eca4af2-4950-4a2d-9b9f-fa7010a62464'
PILOT_OPERATOR_USERNAME = 'gabriel.oliveira'
MAX_GRANT_MINUTES = 60
_SECRET_LIKE = re.compile(r'(?i)(password|passwd|token|credential|bearer|rmm_live_|deploy_|enroll_|private key)')


@dataclass(frozen=True)
class GrantExecutionIdentity:
    username: str
    uid: int
    effective_uid: int


def verify_execution_identity(*, effective_uid, sudo_user, sudo_uid, allowed_users):
    """Require root via sudo and a matching, explicitly allowlisted human caller."""
    if (type(effective_uid) is not int or effective_uid != 0 or not sudo_user or
            not str(sudo_uid).isdigit() or int(sudo_uid) <= 0):
        raise PermissionDenied('Execute como root via sudo, com SUDO_USER e SUDO_UID verificáveis.')
    normalized = str(sudo_user).strip().casefold()
    allowed = {str(item).strip().casefold() for item in allowed_users if str(item).strip()}
    if normalized not in allowed:
        raise PermissionDenied('Identidade Unix não autorizada para administrar grants ConPTY.')
    try:
        import pwd
        resolved_uid = pwd.getpwnam(sudo_user).pw_uid
    except (ImportError, KeyError):
        raise PermissionDenied('Não foi possível verificar a identidade Unix invocadora.') from None
    if resolved_uid != int(sudo_uid):
        raise PermissionDenied('SUDO_USER e SUDO_UID não correspondem à identidade Unix.')
    return GrantExecutionIdentity(username=sudo_user, uid=resolved_uid, effective_uid=effective_uid)


def _validate_text(value, label, maximum):
    value = str(value or '').strip()
    if not value or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise ValidationError(f'{label} é obrigatório e deve conter até {maximum} caracteres imprimíveis.')
    if _SECRET_LIKE.search(value):
        raise ValidationError(f'{label} parece conter material sensível; nenhuma alteração foi feita.')
    return value


def validate_grant_context(reason, approval_ref):
    return (_validate_text(reason, 'Motivo', 500),
            _validate_text(approval_ref, 'Referência de aprovação', 200))


def _resolve_pilot(*, endpoint_id, machine_id, operator_username, lock=False,
                   require_operator_authorization=True, require_endpoint_available=True):
    try:
        requested_endpoint = uuid.UUID(str(endpoint_id))
    except (TypeError, ValueError, AttributeError):
        raise ValidationError('UUID completo do endpoint inválido.') from None
    if requested_endpoint != PILOT_ENDPOINT_ID or str(machine_id).strip().casefold() != PILOT_MACHINE_ID:
        raise PermissionDenied('O alvo não corresponde à identidade fixa autorizada do piloto.')

    endpoints = AgentMachine.objects
    if lock:
        endpoints = endpoints.select_for_update()
    endpoint = endpoints.filter(pk=PILOT_ENDPOINT_ID).first()
    if endpoint is None:
        raise ValidationError('Endpoint piloto não encontrado.')
    if str(endpoint.machine_id or '').casefold() != PILOT_MACHINE_ID:
        raise PermissionDenied('Identidade ou estado do endpoint não permite administrar o grant.')
    if require_endpoint_available and (not endpoint.is_active or endpoint.has_terminal_lifecycle):
        raise PermissionDenied('Identidade ou estado do endpoint não permite administrar o grant.')
    if AgentMachine.objects.filter(machine_id__iexact=PILOT_MACHINE_ID).exclude(pk=endpoint.pk).exists():
        raise PermissionDenied('Machine ID duplicado; identidade ambígua.')
    if str(operator_username).strip().casefold() != PILOT_OPERATOR_USERNAME:
        raise PermissionDenied('Somente o operador fixo autorizado pode ser beneficiário.')
    User = get_user_model()
    users = User.objects
    if lock:
        users = users.select_for_update()
    matches = list(users.filter(username__iexact=PILOT_OPERATOR_USERNAME)[:2])
    if len(matches) != 1:
        raise ValidationError('Operador piloto ausente ou não único.')
    operator = matches[0]
    if require_operator_authorization and (not operator.is_active or
            not is_nightowl_technical_user(operator) or
            not operator.has_perm('agents.open_terminalsession')):
        raise PermissionDenied('Operador inativo, não técnico ou sem agents.open_terminalsession.')
    return endpoint, operator


def _resolve_pilot_for_revocation(*, endpoint_id, operator_username, lock=False):
    """Resolve only the immutable pilot UUID and beneficiary for safe revocation."""
    try:
        requested_endpoint = uuid.UUID(str(endpoint_id))
    except (TypeError, ValueError, AttributeError):
        raise ValidationError('UUID completo do endpoint inválido.') from None
    if requested_endpoint != PILOT_ENDPOINT_ID:
        raise PermissionDenied('A revogação aceita somente o UUID fixo do piloto.')
    if str(operator_username).strip().casefold() != PILOT_OPERATOR_USERNAME:
        raise PermissionDenied('A revogação aceita somente o beneficiário fixo do piloto.')

    endpoints = AgentMachine.objects
    if lock:
        endpoints = endpoints.select_for_update()
    endpoint = endpoints.filter(pk=PILOT_ENDPOINT_ID).first()
    if endpoint is None:
        raise ValidationError('Endpoint piloto não encontrado; grant não pode ser localizado.')

    User = get_user_model()
    users = User.objects
    if lock:
        users = users.select_for_update()
    matches = list(users.filter(username__iexact=PILOT_OPERATOR_USERNAME)[:2])
    if len(matches) != 1:
        raise ValidationError('Beneficiário do piloto ausente ou não único; grant não pode ser localizado.')
    return endpoint, matches[0]


def inspect_conpty_grant(*, endpoint_id, machine_id, operator_username):
    endpoint, operator = _resolve_pilot(endpoint_id=endpoint_id, machine_id=machine_id,
        operator_username=operator_username, require_operator_authorization=False,
        require_endpoint_available=False)
    grant = TerminalAccessGrant.objects.filter(endpoint=endpoint, operator=operator).first()
    now = timezone.now()
    return {
        'endpoint_id': str(endpoint.pk),
        'machine_id': endpoint.machine_id,
        'operator': operator.get_username(),
        'endpoint_active': endpoint.is_active,
        'endpoint_has_terminal_lifecycle': endpoint.has_terminal_lifecycle,
        'operator_active': operator.is_active,
        'operator_has_permission': operator.has_perm('agents.open_terminalsession'),
        'grant_exists': grant is not None,
        'grant_enabled': bool(grant and grant.enabled),
        'allow_conpty': bool(grant and grant.allow_conpty),
        'allow_admin_auth': bool(grant and grant.allow_admin_auth),
        'expires_at': grant.expires_at.isoformat() if grant else None,
        'grant_current': bool(grant and grant.enabled and grant.allow_conpty and grant.expires_at > now),
        'authorization_ready': bool(endpoint.is_active and not endpoint.has_terminal_lifecycle and operator.is_active and
            is_nightowl_technical_user(operator) and operator.has_perm('agents.open_terminalsession') and
            grant and grant.enabled and grant.allow_conpty and grant.expires_at > now),
        'active_conpty_sessions': TerminalSession.objects.filter(
            endpoint=endpoint, operator=operator, mode='conpty', status__in=TerminalSession.ACTIVE
        ).count(),
    }


def inspect_conpty_grant_for_revocation(*, endpoint_id, machine_id, operator_username):
    """Preview a revoke by fixed UUID/beneficiary even if eligibility has changed."""
    if str(machine_id).strip().casefold() != PILOT_MACHINE_ID:
        raise PermissionDenied('Informe o machine_id esperado do piloto para confirmar o alvo fixo.')
    endpoint, operator = _resolve_pilot_for_revocation(
        endpoint_id=endpoint_id, operator_username=operator_username)
    grant = TerminalAccessGrant.objects.filter(endpoint=endpoint, operator=operator).first()
    now = timezone.now()
    observed_machine_id = str(endpoint.machine_id or '')
    return {
        'endpoint_id': str(endpoint.pk),
        'machine_id': observed_machine_id,
        'expected_machine_id': PILOT_MACHINE_ID,
        'machine_id_diverged': observed_machine_id.casefold() != PILOT_MACHINE_ID,
        'endpoint_active': endpoint.is_active,
        'endpoint_has_terminal_lifecycle': endpoint.has_terminal_lifecycle,
        'operator': operator.get_username(),
        'operator_active': operator.is_active,
        'operator_has_permission': operator.has_perm('agents.open_terminalsession'),
        'grant_exists': grant is not None,
        'grant_enabled': bool(grant and grant.enabled),
        'allow_conpty': bool(grant and grant.allow_conpty),
        'allow_admin_auth': bool(grant and grant.allow_admin_auth),
        'expires_at': grant.expires_at.isoformat() if grant else None,
        'grant_current': bool(grant and grant.enabled and grant.allow_conpty and grant.expires_at > now),
        'active_conpty_sessions': TerminalSession.objects.filter(
            endpoint=endpoint, operator=operator, mode='conpty', status__in=TerminalSession.ACTIVE
        ).count(),
    }


def _audit_grant_change(*, action, actor, endpoint, operator, grant, previous, reason, approval_ref):
    observed_machine_id = str(endpoint.machine_id or '')
    AuditEvent.objects.create(
        event_type=f'terminal.grant_{action}',
        severity=AuditEvent.SEVERITY_SECURITY if action == 'revoked' else AuditEvent.SEVERITY_SUCCESS,
        actor_type=AuditEvent.ACTOR_USER,
        actor_name=actor.username[:150],
        endpoint=endpoint,
        title=f'Terminal ConPTY grant {action}',
        description=reason,
        metadata={
            'operator': operator.get_username(),
            'operator_id': str(operator.pk),
            'endpoint_uuid': str(endpoint.pk),
            'machine_id': observed_machine_id,
            'expected_machine_id': PILOT_MACHINE_ID,
            'machine_id_diverged': observed_machine_id.casefold() != PILOT_MACHINE_ID,
            'endpoint_active': endpoint.is_active,
            'endpoint_has_terminal_lifecycle': endpoint.has_terminal_lifecycle,
            'operator_active': operator.is_active,
            'operator_has_permission': operator.has_perm('agents.open_terminalsession'),
            'grant_id': str(grant.pk),
            'expires_at': grant.expires_at.isoformat(),
            'allow_conpty': grant.allow_conpty,
            'allow_admin_auth': grant.allow_admin_auth,
            'approval_ref': approval_ref,
            'executor_os_user': actor.username,
            'executor_uid': actor.uid,
            'executor_effective_uid': actor.effective_uid,
            'previous': previous,
        },
    )


@transaction.atomic
def change_conpty_grant(*, action, endpoint_id, machine_id, operator_username,
                        actor, reason, approval_ref, ttl_minutes=None):
    if action not in {'grant', 'revoke'}:
        raise ValidationError('Ação de alteração inválida.')
    reason, approval_ref = validate_grant_context(reason, approval_ref)
    if action == 'grant' and (type(ttl_minutes) is not int or not 1 <= ttl_minutes <= MAX_GRANT_MINUTES):
        raise ValidationError(f'A validade deve ser de 1 a {MAX_GRANT_MINUTES} minutos.')
    if (not isinstance(actor, GrantExecutionIdentity) or type(actor.uid) is not int or actor.uid <= 0 or
            type(actor.effective_uid) is not int or actor.effective_uid != 0 or
            actor.username.strip().casefold() not in {
                str(item).strip().casefold()
                for item in getattr(settings, 'NIGHTOWL_TERMINAL_GRANT_ADMIN_USERS', set())
            }):
        raise PermissionDenied('Executor Unix não autorizado pela allowlist de grants ConPTY.')
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '3s'")

    if action == 'revoke':
        # Revocation must remain possible when current eligibility has been withdrawn.
        # The CLI still requires the expected machine ID as an explicit confirmation;
        # the current database value is recorded as observed identity in the audit.
        if str(machine_id).strip().casefold() != PILOT_MACHINE_ID:
            raise PermissionDenied('Informe o machine_id esperado do piloto para confirmar o alvo fixo.')
        endpoint, operator = _resolve_pilot_for_revocation(
            endpoint_id=endpoint_id, operator_username=operator_username, lock=True)
    else:
        endpoint, operator = _resolve_pilot(endpoint_id=endpoint_id, machine_id=machine_id,
            operator_username=operator_username, lock=True)
    grant = (TerminalAccessGrant.objects.select_for_update()
             .filter(endpoint=endpoint, operator=operator).first())
    if grant and grant.allow_admin_auth:
        raise PermissionDenied('O grant contém allow_admin_auth=true; revisão manual obrigatória.')
    if action == 'revoke' and grant is None:
        raise ValidationError('Não existe grant para revogar; nenhuma alteração foi feita.')
    previous = ({'enabled': grant.enabled, 'allow_conpty': grant.allow_conpty,
                 'allow_admin_auth': grant.allow_admin_auth, 'expires_at': grant.expires_at.isoformat()}
                if grant else None)

    now = timezone.now()
    if action == 'grant':
        if grant is None:
            grant = TerminalAccessGrant(operator=operator, endpoint=endpoint,
                enabled=True, allow_conpty=True, allow_admin_auth=False,
                expires_at=now + timedelta(minutes=ttl_minutes))
            event_action = 'granted'
        else:
            event_action = 'renewed'
            grant.enabled = True
            grant.allow_conpty = True
            grant.expires_at = now + timedelta(minutes=ttl_minutes)
        grant.save()
    else:
        event_action = 'revoked'
        grant.enabled = False
        grant.allow_conpty = False
        grant.save(update_fields=['enabled', 'allow_conpty'])

    _audit_grant_change(action=event_action, actor=actor, endpoint=endpoint,
        operator=operator, grant=grant, previous=previous, reason=reason, approval_ref=approval_ref)
    closed_sessions = 0
    if action == 'revoke':
        from .terminal_sessions import close_session
        active_ids = list(TerminalSession.objects.filter(
            endpoint=endpoint, operator=operator, mode='conpty', status__in=TerminalSession.ACTIVE
        ).values_list('pk', flat=True))
        for session_id in active_ids:
            session = close_session(session_id, 'grant_revoked', status='cancelled')
            closed_sessions += session.status == 'cancelled'
    return {'action': event_action, 'grant_id': str(grant.pk),
            'expires_at': grant.expires_at.isoformat(), 'closed_sessions': closed_sessions}
