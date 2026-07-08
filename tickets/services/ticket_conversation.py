from django.utils import timezone
from django.urls import reverse

from ..models import NotificationOutbox, Ticket, TicketAttachment, TicketComment
from .automation_outbox import prepare_ticket_notification
from .desk_mvp1 import create_audit_event, display_time, relative_time
from .ticket_workflow import WorkflowError, add_ticket_comment, can_comment_public


def _normalize(value):
    return str(value or '').strip().casefold()


def _requester_aliases(ticket):
    return {
        _normalize(ticket.requester_name),
        _normalize(ticket.requester_email),
        _normalize(ticket.requester_username),
    } - {''}


def _message_kind(ticket, comment):
    author = _normalize(comment.author_name)
    if not author or author == 'sistema':
        return 'system'
    if author in _requester_aliases(ticket):
        return 'requester'
    return 'team'


def _attachment_payload(attachment):
    return {
        'id': str(attachment.pk),
        'name': attachment.original_name,
        'size': attachment.size,
        'url': reverse('tickets:attachment-download', kwargs={'attachment_id': attachment.pk}),
        'when': relative_time(attachment.created_at),
    }


def _message_payload(ticket, comment):
    kind = _message_kind(ticket, comment)
    inbound = comment.inbound_email_messages.order_by('-created_at').first()
    return {
        'id': str(comment.pk),
        'kind': kind,
        'source': 'email_inbound' if inbound else 'comment',
        'source_label': 'E-mail recebido' if inbound else 'Comentario publico',
        'author': comment.author_name or 'Sistema',
        'body': comment.body,
        'created_at': timezone.localtime(comment.created_at).isoformat(),
        'when': display_time(comment.created_at),
        'attachments': [
            _attachment_payload(attachment)
            for attachment in comment.attachments.filter(visibility=TicketAttachment.VISIBILITY_PUBLIC).order_by('created_at')
        ],
    }


def build_public_conversation(ticket):
    if not isinstance(ticket, Ticket):
        ticket = getattr(ticket, 'record', None)
    if not ticket:
        return {
            'messages': [],
            'can_reply': False,
            'readonly_message': 'Conversa publica indisponivel para chamados de preview.',
        }

    messages = [
        {
            'id': f'ticket-opened-{ticket.pk}',
            'kind': 'system',
            'author': 'NightOwl Desk',
            'body': f'Chamado aberto por {ticket.requester_name or ticket.requester_email or "solicitante"}.',
            'created_at': timezone.localtime(ticket.created_at).isoformat(),
            'when': display_time(ticket.created_at),
            'attachments': [],
        }
    ]
    comments = (
        ticket.comments
        .filter(visibility=TicketComment.VISIBILITY_PUBLIC)
        .prefetch_related('attachments')
        .order_by('created_at')
    )
    messages.extend(_message_payload(ticket, comment) for comment in comments)
    return {
        'messages': messages,
        'can_reply': can_comment_public(ticket),
        'readonly_message': 'Este chamado esta em modo somente leitura.',
    }


def create_public_reply(ticket, *, actor, body, files=None, source='Conversa publica'):
    if not can_comment_public(ticket):
        raise WorkflowError('Este chamado esta em modo somente leitura.')
    comment = add_ticket_comment(
        ticket,
        actor=actor,
        body=body,
        visibility=TicketComment.VISIBILITY_PUBLIC,
        source=source,
    )

    created_attachments = []
    for uploaded in files or []:
        created_attachments.append(
            TicketAttachment.objects.create(
                ticket=ticket,
                comment=comment,
                file=uploaded,
                original_name=uploaded.name,
                content_type=getattr(uploaded, 'content_type', '') or '',
                size=getattr(uploaded, 'size', 0) or 0,
                uploaded_by=actor,
                visibility=TicketAttachment.VISIBILITY_PUBLIC,
            )
        )

    if created_attachments:
        create_audit_event(
            ticket,
            actor=actor,
            event_type='attachment_created',
            action='Anexou arquivo publico ao comentario',
            field_name='attachments',
            new_value=', '.join(item.original_name for item in created_attachments),
            metadata={
                'origin': source,
                'visibility': TicketAttachment.VISIBILITY_PUBLIC,
                'comment_id': str(comment.pk),
                'count': len(created_attachments),
            },
        )

    notification = prepare_ticket_notification(
        ticket,
        'ticket_public_reply',
        user=actor,
        extra_context={
            'mensagem': body,
            'public_comment_id': str(comment.pk),
        },
    )
    return {
        'comment': comment,
        'message': _message_payload(ticket, comment),
        'notification': notification,
        'notification_status': notification.status if isinstance(notification, NotificationOutbox) else 'skipped',
    }
