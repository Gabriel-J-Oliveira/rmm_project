import logging

from django.utils import timezone

from ..models import DeskTemplate, NotificationOutbox, TicketAuditEvent
from .desk_templates import render_subject, render_template
from .email_outbox import queue_email
from .email_renderer import render_ticket_email_html, ticket_action_url


logger = logging.getLogger(__name__)

EVENT_TEMPLATE_MAP = {
    'ticket_created': (DeskTemplate.APP_TICKET_CREATED, 'Confirmacao de chamado criado'),
    'ticket_assigned': (DeskTemplate.APP_COMPOSER_PUBLIC, 'Chamado assumido'),
    'waiting_requester': (DeskTemplate.APP_WAITING_REQUESTER, 'Aguardando solicitante'),
    'ticket_resolved': (DeskTemplate.APP_RESOLVE_TICKET, 'Chamado resolvido'),
    'ticket_reopened': (DeskTemplate.APP_TICKET_REOPENED, 'Chamado reaberto por contestacao'),
    'ticket_public_reply': (DeskTemplate.APP_COMPOSER_PUBLIC, 'Resposta publica do chamado'),
}

SUPPORTED_WORKFLOW_EVENTS = tuple(EVENT_TEMPLATE_MAP.keys())

DEFAULT_EVENT_SUBJECTS = {
    'ticket_created': 'Chamado #{{ticket_number}} recebido',
    'ticket_assigned': 'Chamado #{{ticket_number}} em atendimento',
    'waiting_requester': 'Precisamos da sua resposta no chamado #{{ticket_number}}',
    'ticket_resolved': 'Chamado #{{ticket_number}} resolvido',
    'ticket_reopened': 'Chamado #{{ticket_number}} reaberto',
    'ticket_public_reply': 'Nova resposta no chamado #{{ticket_number}}',
}


def _actor_name(user):
    if not user:
        return 'Equipe NightOwl'
    if hasattr(user, 'is_authenticated'):
        if user.is_authenticated:
            return user.get_full_name() or user.get_username()
        return 'Equipe NightOwl'
    return str(user or 'Equipe NightOwl')


def _event_context(ticket, event_type, extra_context=None):
    context = {
        'action_url': ticket_action_url(ticket),
        'mensagem': '',
        'motivo': '',
        'solucao': '',
    }
    if event_type == 'ticket_created':
        context['mensagem'] = 'Recebemos sua solicitacao e ela esta aguardando triagem pela equipe.'
    elif event_type == 'ticket_assigned':
        context['mensagem'] = 'Sua solicitacao foi assumida pela equipe e esta em atendimento.'
    elif event_type == 'waiting_requester':
        context['mensagem'] = 'A equipe precisa de mais informacoes para continuar o atendimento.'
    elif event_type == 'ticket_resolved':
        context['mensagem'] = 'Seu chamado foi resolvido pela equipe. Caso o problema continue ou a solucao nao atenda sua solicitacao, responda este e-mail ou acesse o portal para reabrir o chamado.'
    elif event_type == 'ticket_reopened':
        context['mensagem'] = 'O chamado foi reaberto e voltou para atendimento.'
    elif event_type == 'ticket_public_reply':
        context['mensagem'] = 'A equipe enviou uma nova resposta publica no chamado.'

    if extra_context:
        context.update({key: '' if value is None else str(value) for key, value in extra_context.items()})
    if not context.get('motivo') and context.get('reason'):
        context['motivo'] = context['reason']
    if not context.get('solucao') and context.get('reason') and event_type == 'ticket_resolved':
        context['solucao'] = context['reason']
    return context


def get_notification_template(event_type):
    mapping = EVENT_TEMPLATE_MAP.get(event_type)
    if not mapping:
        return None

    application, preferred_name = mapping
    template = DeskTemplate.objects.filter(
        name=preferred_name,
        application=application,
        is_active=True,
    ).first()
    if not template and event_type == 'ticket_public_reply':
        return None
    if not template:
        template = DeskTemplate.objects.filter(application=application, is_active=True).order_by('name').first()
    return template


def render_ticket_notification(ticket, event_type, user=None, extra_context=None):
    template = get_notification_template(event_type)
    if not template:
        return None

    context = _event_context(ticket, event_type, extra_context=extra_context)
    subject = render_subject(template, ticket, user=user, extra_context=context)
    if not str(subject or '').strip():
        subject = DEFAULT_EVENT_SUBJECTS.get(event_type, 'Atualizacao no chamado #{{ticket_number}}').replace('{{ticket_number}}', str(ticket.number))
    if f'[NightOwl #{ticket.number}]' not in subject:
        subject = f'[NightOwl #{ticket.number}] {subject}'
    body_text = render_template(template, ticket, user=user, extra_context=context)
    return {
        'template': template,
        'subject': subject,
        'body_text': body_text,
        'body_html': render_ticket_email_html(
            ticket=ticket,
            event_type=event_type,
            subject=subject,
            body_text=body_text,
        ),
        'recipient_name': ticket.requester_name,
        'recipient_email': ticket.requester_email,
        'action_url': context.get('action_url', ''),
        'metadata': {
            'email_sent': False,
            'template_application': template.application,
            'action_url': context.get('action_url', ''),
            'headers': {
                'X-NightOwl-Ticket-ID': str(ticket.number),
                'X-NightOwl-Event': event_type,
            },
        },
    }


def _audit_notification_skipped(ticket, event_type, user, reason):
    TicketAuditEvent.objects.create(
        ticket=ticket,
        actor=_actor_name(user),
        event_type='notification_skipped',
        action=reason,
        field_name='notification',
        new_value=event_type,
        metadata={'event_type': event_type, 'status': NotificationOutbox.STATUS_SKIPPED},
    )


def _recent_duplicate_exists(ticket, event_type, recipient_email, template):
    since = timezone.now() - timezone.timedelta(seconds=20)
    return (
        NotificationOutbox.objects
        .filter(
            ticket=ticket,
            event_type=event_type,
            recipient_email__iexact=recipient_email or '',
            template=template,
            created_at__gte=since,
        )
        .exclude(status=NotificationOutbox.STATUS_FAILED)
        .exists()
    )


def prepare_ticket_notification(ticket, event_type, user=None, extra_context=None):
    if event_type not in EVENT_TEMPLATE_MAP:
        _audit_notification_skipped(ticket, event_type, user, 'Notificacao nao preparada: evento sem template mapeado')
        logger.info('Unsupported Desk notification event skipped: %s', event_type)
        return None

    rendered = render_ticket_notification(ticket, event_type, user=user, extra_context=extra_context)
    if not rendered:
        _audit_notification_skipped(ticket, event_type, user, 'Notificacao nao preparada: template ativo nao encontrado')
        return None

    template = rendered['template']
    recipient_email = rendered['recipient_email']
    if _recent_duplicate_exists(ticket, event_type, recipient_email, template):
        _audit_notification_skipped(ticket, event_type, user, 'Notificacao nao preparada: duplicidade recente detectada')
        return None

    notification = queue_email(
        source_app=NotificationOutbox.SOURCE_DESK,
        source_model='tickets.Ticket',
        source_id=str(ticket.pk),
        ticket=ticket,
        template=template,
        event_type=event_type,
        recipient_name=rendered['recipient_name'],
        recipient_email=recipient_email,
        subject=rendered['subject'],
        body_text=rendered['body_text'],
        body_html=rendered['body_html'],
        priority=(
            NotificationOutbox.PRIORITY_HIGH
            if ticket.priority in {ticket.PRIORITY_HIGH, ticket.PRIORITY_CRITICAL}
            else NotificationOutbox.PRIORITY_NORMAL
        ),
        metadata=rendered['metadata'],
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
            'recipient_email': recipient_email,
            'status': notification.status,
            'email_sent': False,
            'action_url': rendered['action_url'],
        },
    )
    return notification
