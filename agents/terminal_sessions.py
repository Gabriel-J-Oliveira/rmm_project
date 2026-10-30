"""DEC-04D.1 control plane. Tickets are never persisted in plaintext or URLs."""
import hashlib
import hmac
import secrets
from datetime import timedelta
from ipaddress import ip_address

from django.conf import settings
from django.db import connection, transaction, IntegrityError, OperationalError
from django.utils import timezone
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.authz import is_nightowl_technical_user
from .authentication import authenticate_agent_token
from .identity import validate_runtime_identity
from .internal_jobs import internal_capability
from .models import AgentMachine, TerminalSession, TerminalAccessGrant, AuditEvent
from .services import create_audit_event

MAX_SESSIONS = 4
LIFETIME_SECONDS = 300
TICKET_SECONDS = 30


def enabled():
    return (getattr(settings, 'NIGHTOWL_TERMINAL_ENABLED', False) and
            getattr(settings, 'NIGHTOWL_TERMINAL_SINGLE_PROCESS', False))


def loopback_allowed(address):
    try:
        return getattr(settings, 'NIGHTOWL_TERMINAL_TEST_LOOPBACK', False) and ip_address(address).is_loopback
    except ValueError:
        return False


def require_control(request):
    if not enabled():
        raise ValueError('terminal_disabled')
    if not request.is_secure() and not loopback_allowed(request.META.get('REMOTE_ADDR', '')):
        raise PermissionError('tls_required')


def audit(session, event, reason=''):
    actor = (AuditEvent.ACTOR_USER if event in ('requested', 'operator_connected', 'cancelled') else
             AuditEvent.ACTOR_AGENT if event == 'agent_connected' else AuditEvent.ACTOR_SYSTEM)
    create_audit_event(event_type='terminal.' + event, title='Terminal transport ' + event,
        description=reason, endpoint=session.endpoint, actor_type=actor,
        actor_name=session.operator.get_username() if actor == AuditEvent.ACTOR_USER else
            'NightOwlAgent' if actor == AuditEvent.ACTOR_AGENT else 'Sistema',
        metadata={'session_id': str(session.pk), 'operator_id': session.operator_id, 'mode': 'synthetic'})


def record_rejection(session_id):
    session = TerminalSession.objects.select_related('operator', 'endpoint').filter(pk=session_id).first()
    if session:
        audit(session, 'connection_rejected', 'credential_or_transport_rejected')


def authorize(operator, endpoint):
    if (not operator.is_active or not is_nightowl_technical_user(operator) or
            not operator.has_perm('agents.open_terminalsession') or
            not TerminalAccessGrant.objects.filter(operator=operator, endpoint=endpoint, enabled=True,
                expires_at__gt=timezone.now()).exists()):
        raise PermissionError('terminal_forbidden')
    if (not endpoint.is_active or endpoint.has_terminal_lifecycle or not endpoint.machine_id or
            AgentMachine.objects.filter(machine_id__iexact=endpoint.machine_id).count() != 1):
        raise PermissionError('endpoint_unavailable')


def validate_session(session):
    if not enabled() or session.status not in TerminalSession.ACTIVE:
        raise PermissionError('session_closed')
    if session.expires_at <= timezone.now():
        raise PermissionError('session_expired')
    authorize(session.operator, session.endpoint)
    if (session.machine_id != session.endpoint.machine_id or
            not hmac.compare_digest(session.agent_identity, session.endpoint.agent_token_hash)):
        raise PermissionError('identity_changed')


def issue_ticket(session, role):
    ticket = secrets.token_urlsafe(32)
    setattr(session, role + '_ticket_hash', hashlib.sha256(ticket.encode()).hexdigest())
    setattr(session, role + '_ticket_expires', timezone.now() + timedelta(seconds=TICKET_SECONDS))
    return ticket


def connection_info(session, role, ticket):
    return {'session_id': str(session.pk), 'credential': ticket, 'expires_at': session.expires_at.isoformat(),
        'ticket_seconds': TICKET_SECONDS, 'websocket_path': f'/ws/terminal/{session.pk}/{role}/',
        'mode': 'synthetic', 'execution': False, 'protocol': 1}


@transaction.atomic
def close_session(session_id, reason, status='closed'):
    session = TerminalSession.objects.select_for_update(of=('self',)).select_related('endpoint', 'operator').get(pk=session_id)
    if session.status in TerminalSession.ACTIVE:
        session.status, session.reason, session.closed_at = status, reason, timezone.now()
        session.save(update_fields=['status', 'reason', 'closed_at'])
        audit(session, status, reason)
    return session


@transaction.atomic
def create_session(operator, endpoint_id):
    if not enabled():
        raise ValueError('terminal_disabled')
    # Serialize global admission on PostgreSQL; conditional unique constraints also
    # enforce one active session per endpoint and per operator on SQLite.
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [734625190284])
    endpoint = AgentMachine.objects.select_for_update().get(pk=endpoint_id)
    authorize(operator, endpoint)
    if not internal_capability(endpoint, 'remote_terminal_transport:1'):
        raise ValueError('transport_capability_unconfirmed')
    for stale in TerminalSession.objects.filter(status__in=TerminalSession.ACTIVE, expires_at__lte=timezone.now()):
        close_session(stale.pk, 'session_expired', 'expired')
    if TerminalSession.objects.filter(status__in=TerminalSession.ACTIVE).count() >= MAX_SESSIONS:
        raise ValueError('session_limit')
    session = TerminalSession(operator=operator, endpoint=endpoint, machine_id=endpoint.machine_id,
        agent_identity=endpoint.agent_token_hash, expires_at=timezone.now() + timedelta(seconds=LIFETIME_SECONDS))
    ticket = issue_ticket(session, 'operator')
    session.save()
    audit(session, 'requested')
    audit(session, 'authorized')
    return connection_info(session, 'operator', ticket)


@transaction.atomic
def consume_ticket(session_id, role, ticket, owner):
    session = TerminalSession.objects.select_for_update(of=('self',)).select_related('operator', 'endpoint').get(pk=session_id)
    validate_session(session)
    if session.transport_owner and session.transport_owner != owner:
        raise PermissionError('wrong_transport_worker')
    digest = hashlib.sha256(ticket.encode()).hexdigest()
    expires = getattr(session, role + '_ticket_expires')
    if (getattr(session, role + '_ticket_used') or not expires or expires <= timezone.now() or
            not hmac.compare_digest(digest, getattr(session, role + '_ticket_hash'))):
        raise PermissionError('invalid_credential')
    # Conditional update prevents concurrent replay even where row locks are unavailable.
    updated = TerminalSession.objects.filter(pk=session.pk, **{role + '_ticket_used': False}).update(
        **{role + '_ticket_used': True, 'transport_owner': owner, 'status': 'connecting'})
    if updated != 1:
        raise PermissionError('credential_replayed')
    audit(session, role + '_connected')
    return session.expires_at


def check_live(session_id):
    session = TerminalSession.objects.select_related('operator', 'endpoint').get(pk=session_id)
    validate_session(session)


@transaction.atomic
def mark_ready(session_id):
    session = TerminalSession.objects.select_for_update(of=('self',)).select_related('operator', 'endpoint').get(pk=session_id)
    validate_session(session)
    if session.status != 'ready':
        session.status = 'ready'
        session.save(update_fields=['status'])
        audit(session, 'ready')


class PrivateTerminalView(APIView):
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store, private'
        return response


class TerminalOperatorView(PrivateTerminalView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, endpoint_id):
        try:
            require_control(request)
            if request.data != {'type': 'request', 'protocol': 1} or type(request.data.get('protocol')) is not int:
                raise ValueError('invalid_request')
            return Response(create_session(request.user, endpoint_id), status=201)
        except PermissionError:
            # Record rejection metadata only; never tickets, payloads, or headers.
            create_audit_event(event_type='terminal.rejected', title='Terminal request rejected', description='authorization_denied',
                actor_type=AuditEvent.ACTOR_USER, actor_name=request.user.get_username(), metadata={'endpoint_id': str(endpoint_id)})
            return Response({'error': 'terminal_forbidden'}, status=403)
        except AgentMachine.DoesNotExist:
            return Response({'error': 'endpoint_not_found'}, status=404)
        except (ValueError, IntegrityError, OperationalError):
            return Response({'error': 'terminal_unavailable_or_invalid'}, status=409)


class TerminalCancelView(TerminalOperatorView):
    def post(self, request, session_id):
        try:
            require_control(request)
            if request.data != {'type': 'cancel'}:
                raise ValueError('invalid_request')
            session = TerminalSession.objects.get(pk=session_id, operator=request.user)
            close_session(session.pk, 'operator_cancelled', 'cancelled')
            return Response({'session_id': str(session.pk), 'status': 'cancelled'})
        except TerminalSession.DoesNotExist:
            return Response({'error': 'session_not_found'}, status=404)
        except PermissionError:
            return Response({'error': 'terminal_forbidden'}, status=403)
        except ValueError:
            return Response({'error': 'terminal_disabled_or_invalid'}, status=409)


class TerminalAgentPollView(PrivateTerminalView):
    authentication_classes = []
    permission_classes = []

    @transaction.atomic
    def post(self, request):
        endpoint = authenticate_agent_token(request)
        try:
            require_control(request)
            if not isinstance(request.data, dict) or set(request.data) != {'machine_id'} or request.data['machine_id'] != endpoint.machine_id:
                raise ValueError('invalid_identity')
            validate_runtime_identity(endpoint, request.data)
            session = TerminalSession.objects.select_for_update(of=('self',)).select_related('operator', 'endpoint').filter(
                endpoint=endpoint, status__in=TerminalSession.ACTIVE, agent_ticket_hash='').first()
            if not session:
                return Response({'session': None})
            validate_session(session)
            ticket = issue_ticket(session, 'agent')
            session.save(update_fields=['agent_ticket_hash', 'agent_ticket_expires'])
            return Response({'session': connection_info(session, 'agent', ticket)})
        except PermissionError:
            return Response({'error': 'terminal_forbidden'}, status=403)
        except ValueError:
            return Response({'error': 'terminal_disabled_or_invalid'}, status=409)
