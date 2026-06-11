import logging

from agents.models import AuditEvent


logger = logging.getLogger(__name__)


def get_client_ip(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or None


def create_audit_event(
    event_type,
    title,
    description='',
    severity=AuditEvent.SEVERITY_INFO,
    actor_type=AuditEvent.ACTOR_SYSTEM,
    actor_name='Night Owl',
    endpoint=None,
    alert=None,
    metadata=None,
    request=None,
):
    try:
        ip_address = None
        user_agent = ''
        if request is not None:
            user = getattr(request, 'user', None)
            if user and user.is_authenticated:
                actor_type = AuditEvent.ACTOR_USER
                actor_name = user.get_username()
            ip_address = get_client_ip(request)
            user_agent = request.META.get('HTTP_USER_AGENT', '')

        return AuditEvent.objects.create(
            event_type=event_type,
            severity=severity,
            actor_type=actor_type,
            actor_name=actor_name or '',
            endpoint=endpoint,
            alert=alert,
            title=title,
            description=description or '',
            metadata=metadata or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except Exception:
        logger.exception('Failed to create audit event: %s', event_type)
        return None
