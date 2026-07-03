import logging

from ..models import DeskTemplate, NotificationOutbox, TicketAuditEvent
from .desk_templates import render_subject, render_template
from .email_renderer import render_ticket_email_html
from .email_outbox import queue_email


logger = logging.getLogger(__name__)

EVENT_TEMPLATE_MAP = {
    'ticket_created': (DeskTemplate.APP_TICKET_CREATED, 'Confirmação de chamado criado'),
    'ticket_assigned': (DeskTemplate.APP_COMPOSER_PUBLIC, 'Chamado assumido'),
    'waiting_requester': (DeskTemplate.APP_WAITING_REQUESTER, 'Aguardando solicitante'),
    'ticket_resolved': (DeskTemplate.APP_RESOLVE_TICKET, 'Chamado resolvido'),
    'ticket_reopened': (DeskTemplate.APP_TICKET_REOPENED, 'Chamado reaberto por contestação'),
}


def _actor_name(user):
    if not user:
        return 'Equipe NightOwl'
    if hasattr(user, 'is_authenticated'):
        if user.is_authenticated:
            return user.get_full_name() or user.get_username()
        return 'Equipe NightOwl'
    return str(user or 'Equipe NightOwl')


def prepare_ticket_notification(ticket, event_type, user=None, extra_context=None):
    mapping = EVENT_TEMPLATE_MAP.get(event_type)
    if not mapping:
        logger.warning('Unsupported Desk notification event: %s', event_type)
        return None

    application, preferred_name = mapping
    template = DeskTemplate.objects.filter(
        name=preferred_name,
        application=application,
        is_active=True,
    ).first()
    if not template:
        template = DeskTemplate.objects.filter(application=application, is_active=True).order_by('name').first()
    if not template:
        TicketAuditEvent.objects.create(
            ticket=ticket,
            actor=_actor_name(user),
            event_type='notification_skipped',
            action='Notificacao nao preparada: template ativo nao encontrado',
            field_name='notification',
            new_value=event_type,
            metadata={'event_type': event_type, 'status': NotificationOutbox.STATUS_SKIPPED},
        )
        return None

    subject = render_subject(template, ticket, user=user, extra_context=extra_context)
    body_text = render_template(template, ticket, user=user, extra_context=extra_context)
    notification = queue_email(
        source_app=NotificationOutbox.SOURCE_DESK,
        source_model='tickets.Ticket',
        source_id=str(ticket.pk),
        ticket=ticket,
        template=template,
        event_type=event_type,
        recipient_name=ticket.requester_name,
        recipient_email=ticket.requester_email,
        subject=subject,
        body_text=body_text,
        body_html=render_ticket_email_html(
            ticket=ticket,
            event_type=event_type,
            subject=subject,
            body_text=body_text,
        ),
        priority=(
            NotificationOutbox.PRIORITY_HIGH
            if ticket.priority in {ticket.PRIORITY_HIGH, ticket.PRIORITY_CRITICAL}
            else NotificationOutbox.PRIORITY_NORMAL
        ),
        metadata={
            'email_sent': False,
            'template_application': template.application,
        },
        actor=_actor_name(user),
    )
    TicketAuditEvent.objects.create(
        ticket=ticket,
        actor=_actor_name(user),
        event_type='notification_prepared',
        action='Notificacao preparada',
        field_name='notification',
        new_value=template.name,
        metadata={
            'notification_id': str(notification.pk),
            'event_type': event_type,
            'template': template.name,
            'template_id': str(template.pk),
            'recipient_email': ticket.requester_email,
            'status': notification.status,
            'email_sent': False,
        },
    )
    return notification
