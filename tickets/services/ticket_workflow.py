from dataclasses import dataclass
from typing import Optional

from django.db import transaction

from ..models import Ticket, TicketComment
from .automation_outbox import prepare_ticket_notification
from .desk_mvp1 import create_audit_event


ACTIVE_STATUSES = {
    Ticket.STATUS_NEW,
    Ticket.STATUS_IN_PROGRESS,
    Ticket.STATUS_WAITING_USER,
    Ticket.STATUS_WAITING_THIRD_PARTY,
}

READ_ONLY_STATUSES = {
    Ticket.STATUS_CLOSED,
    Ticket.STATUS_CANCELED,
}

PUBLIC_STATUS_MESSAGES = {
    Ticket.STATUS_IN_PROGRESS: 'Nossa equipe iniciou o atendimento deste chamado.',
    Ticket.STATUS_WAITING_USER: 'A equipe precisa de mais informacoes para continuar o atendimento.',
    Ticket.STATUS_WAITING_THIRD_PARTY: 'O atendimento aguarda retorno de terceiro ou fornecedor.',
    Ticket.STATUS_RESOLVED: 'Chamado resolvido pela equipe de atendimento.',
    Ticket.STATUS_CLOSED: 'Chamado encerrado e disponivel apenas para consulta.',
}

ALLOWED_TRANSITIONS = {
    Ticket.STATUS_NEW: {
        Ticket.STATUS_IN_PROGRESS,
        Ticket.STATUS_WAITING_USER,
        Ticket.STATUS_WAITING_THIRD_PARTY,
        Ticket.STATUS_RESOLVED,
        Ticket.STATUS_CANCELED,
    },
    Ticket.STATUS_IN_PROGRESS: {
        Ticket.STATUS_WAITING_USER,
        Ticket.STATUS_WAITING_THIRD_PARTY,
        Ticket.STATUS_RESOLVED,
        Ticket.STATUS_CANCELED,
    },
    Ticket.STATUS_WAITING_USER: {
        Ticket.STATUS_IN_PROGRESS,
        Ticket.STATUS_RESOLVED,
        Ticket.STATUS_CANCELED,
    },
    Ticket.STATUS_WAITING_THIRD_PARTY: {
        Ticket.STATUS_IN_PROGRESS,
        Ticket.STATUS_WAITING_USER,
        Ticket.STATUS_RESOLVED,
        Ticket.STATUS_CANCELED,
    },
    Ticket.STATUS_RESOLVED: {
        Ticket.STATUS_CLOSED,
        Ticket.STATUS_IN_PROGRESS,
    },
    Ticket.STATUS_CLOSED: set(),
    Ticket.STATUS_CANCELED: set(),
}


class WorkflowError(ValueError):
    pass


@dataclass(frozen=True)
class WorkflowResult:
    ticket: Ticket
    old_status: str
    new_status: str
    public_comment: Optional[TicketComment] = None


def can_assume(ticket, user=None):
    return ticket.status in {
        Ticket.STATUS_NEW,
        Ticket.STATUS_IN_PROGRESS,
        Ticket.STATUS_WAITING_USER,
        Ticket.STATUS_WAITING_THIRD_PARTY,
    }


def can_comment_public(ticket, user=None):
    return ticket.status not in READ_ONLY_STATUSES and ticket.status != Ticket.STATUS_RESOLVED


def can_resolve(ticket, user=None):
    return ticket.status in {
        Ticket.STATUS_NEW,
        Ticket.STATUS_IN_PROGRESS,
        Ticket.STATUS_WAITING_USER,
        Ticket.STATUS_WAITING_THIRD_PARTY,
    }


def can_close(ticket, user=None):
    return ticket.status == Ticket.STATUS_RESOLVED


def can_reopen(ticket, user=None):
    return ticket.status == Ticket.STATUS_RESOLVED


def _status_label(status):
    return dict(Ticket.STATUS_CHOICES).get(status, status)


def _create_public_comment(ticket, actor, body):
    body = str(body or '').strip()
    if not body:
        return None
    return TicketComment.objects.create(
        ticket=ticket,
        author_name=actor or 'Sistema',
        body=body,
        visibility=TicketComment.VISIBILITY_PUBLIC,
    )


def _notification_for_transition(old_status, new_status):
    if new_status == Ticket.STATUS_WAITING_USER:
        return 'waiting_requester'
    if new_status == Ticket.STATUS_RESOLVED:
        return 'ticket_resolved'
    if new_status == Ticket.STATUS_CLOSED:
        return 'ticket_closed'
    if new_status == Ticket.STATUS_IN_PROGRESS and old_status == Ticket.STATUS_RESOLVED:
        return 'ticket_reopened'
    return None


def transition_ticket(ticket, new_status, *, actor, reason='', public_message='', source='Web', notify=True, extra_context=None):
    new_status = str(new_status or '').strip()
    if new_status not in dict(Ticket.STATUS_CHOICES):
        raise WorkflowError('Status invalido.')

    old_status = ticket.status
    if old_status == new_status:
        return WorkflowResult(ticket=ticket, old_status=old_status, new_status=new_status)

    allowed = ALLOWED_TRANSITIONS.get(old_status, set())
    if new_status not in allowed:
        raise WorkflowError(f'Transicao de {_status_label(old_status)} para {_status_label(new_status)} nao permitida.')

    reason = str(reason or '').strip()
    public_message = str(public_message or '').strip()

    if new_status in {Ticket.STATUS_WAITING_USER, Ticket.STATUS_WAITING_THIRD_PARTY} and not (reason or public_message):
        raise WorkflowError('Informe uma mensagem para colocar o chamado em espera.')
    if new_status == Ticket.STATUS_RESOLVED and not (reason or public_message):
        raise WorkflowError('Informe a solucao antes de resolver o chamado.')
    if new_status == Ticket.STATUS_CLOSED and old_status != Ticket.STATUS_RESOLVED:
        raise WorkflowError('Somente chamados resolvidos podem ser encerrados.')
    if new_status == Ticket.STATUS_IN_PROGRESS and old_status == Ticket.STATUS_RESOLVED and not (reason or public_message):
        raise WorkflowError('Informe o motivo da reabertura.')

    if new_status == Ticket.STATUS_RESOLVED and not can_resolve(ticket):
        raise WorkflowError('Este chamado nao pode ser resolvido neste status.')
    if new_status == Ticket.STATUS_CLOSED and not can_close(ticket):
        raise WorkflowError('Este chamado so pode ser encerrado apos resolucao.')

    with transaction.atomic():
        old_label = ticket.get_status_display()
        ticket.status = new_status
        if new_status == Ticket.STATUS_IN_PROGRESS and old_status == Ticket.STATUS_RESOLVED:
            ticket.resolved_at = None
            ticket.closed_at = None
        if new_status != Ticket.STATUS_RESOLVED and old_status == Ticket.STATUS_RESOLVED:
            ticket.resolved_at = None
        if new_status != Ticket.STATUS_CLOSED and old_status == Ticket.STATUS_CLOSED:
            ticket.closed_at = None
        ticket.save()

        comment_body = public_message
        if not comment_body and new_status in {
            Ticket.STATUS_WAITING_USER,
            Ticket.STATUS_WAITING_THIRD_PARTY,
            Ticket.STATUS_RESOLVED,
            Ticket.STATUS_CLOSED,
        }:
            base = PUBLIC_STATUS_MESSAGES.get(new_status, '')
            comment_body = f'{base}\n\n{reason}'.strip() if reason else base
        elif not comment_body and new_status == Ticket.STATUS_IN_PROGRESS and old_status == Ticket.STATUS_RESOLVED:
            comment_body = f'Chamado reaberto para atendimento.\n\n{reason}'.strip()

        comment = _create_public_comment(ticket, actor, comment_body)

        event_type = 'ticket_status_changed'
        if new_status == Ticket.STATUS_WAITING_USER:
            event_type = 'ticket_waiting_requester'
        elif new_status == Ticket.STATUS_RESOLVED:
            event_type = 'ticket_resolved'
        elif new_status == Ticket.STATUS_CLOSED:
            event_type = 'ticket_closed'
        elif new_status == Ticket.STATUS_IN_PROGRESS and old_status == Ticket.STATUS_RESOLVED:
            event_type = 'ticket_reopened'

        create_audit_event(
            ticket,
            actor=actor,
            event_type=event_type,
            action=f'Alterou status para {ticket.get_status_display()}',
            field_name='status',
            old_value=old_label,
            new_value=ticket.get_status_display(),
            metadata={
                'origin': source,
                'reason': reason,
                'public_comment_id': str(comment.pk) if comment else '',
            },
        )

        notification_event = _notification_for_transition(old_status, new_status)
        if notify and notification_event:
            prepare_ticket_notification(ticket, notification_event, user=actor, extra_context=extra_context or {'solucao': reason or public_message})

    return WorkflowResult(ticket=ticket, old_status=old_status, new_status=new_status, public_comment=comment)


def assign_ticket(ticket, *, actor, assignee=None, source='Web', notify=True):
    if not can_assume(ticket):
        raise WorkflowError('Este chamado nao pode ser assumido neste status.')
    assignee = str(assignee or actor or '').strip()
    if not assignee:
        raise WorkflowError('Responsavel invalido.')

    with transaction.atomic():
        old_assignee = ticket.assigned_to or 'Sem responsavel'
        old_status = ticket.status
        old_status_label = ticket.get_status_display()
        ticket.assigned_to = assignee
        if ticket.status == Ticket.STATUS_NEW:
            ticket.status = Ticket.STATUS_IN_PROGRESS
        ticket.save()

        create_audit_event(
            ticket,
            actor=actor,
            event_type='ticket_assigned',
            action='Assumiu chamado',
            field_name='assigned_to',
            old_value=old_assignee,
            new_value=assignee,
            metadata={'origin': source, 'status_before': old_status_label, 'status_after': ticket.get_status_display()},
        )
        if old_status == Ticket.STATUS_NEW and ticket.status == Ticket.STATUS_IN_PROGRESS:
            _create_public_comment(ticket, actor, 'Chamado assumido pela equipe de atendimento.')
            create_audit_event(
                ticket,
                actor=actor,
                event_type='ticket_status_changed',
                action='Iniciou atendimento ao assumir',
                field_name='status',
                old_value=old_status_label,
                new_value=ticket.get_status_display(),
                metadata={'origin': source},
            )
        if notify and old_assignee == 'Sem responsavel':
            prepare_ticket_notification(ticket, 'ticket_assigned', user=actor)
    return ticket


def add_ticket_comment(ticket, *, actor, body, visibility=TicketComment.VISIBILITY_INTERNAL, source='Web'):
    body = str(body or '').strip()
    if not body:
        raise WorkflowError('Comentario e obrigatorio.')
    if visibility not in dict(TicketComment.VISIBILITY_CHOICES):
        raise WorkflowError('Visibilidade invalida.')
    comment = TicketComment.objects.create(
        ticket=ticket,
        author_name=actor or 'Sistema',
        body=body,
        visibility=visibility,
    )
    create_audit_event(
        ticket,
        actor=actor,
        event_type='comment_created',
        action='Criou comentario publico' if visibility == TicketComment.VISIBILITY_PUBLIC else 'Criou comentario interno',
        field_name='comments',
        new_value=body,
        metadata={'origin': source, 'visibility': visibility},
    )
    return comment


def requester_reply(ticket, *, actor, body, source='Portal'):
    if not can_comment_public(ticket):
        raise WorkflowError('Este chamado nao aceita novas respostas neste status.')
    with transaction.atomic():
        comment = add_ticket_comment(
            ticket,
            actor=actor,
            body=body,
            visibility=TicketComment.VISIBILITY_PUBLIC,
            source=source,
        )
        if ticket.status == Ticket.STATUS_WAITING_USER:
            old_label = ticket.get_status_display()
            ticket.status = Ticket.STATUS_IN_PROGRESS
            ticket.save(update_fields=['status', 'updated_at'])
            create_audit_event(
                ticket,
                actor=actor,
                event_type='ticket_status_changed',
                action='Retornou para atendimento apos resposta do solicitante',
                field_name='status',
                old_value=old_label,
                new_value=ticket.get_status_display(),
                metadata={'origin': source, 'comment_id': str(comment.pk)},
            )
    return comment


def requester_reopen(ticket, *, actor, reason, source='Portal'):
    if ticket.status != Ticket.STATUS_RESOLVED:
        raise WorkflowError('Somente chamados resolvidos podem ser reabertos pelo portal.')
    return transition_ticket(
        ticket,
        Ticket.STATUS_IN_PROGRESS,
        actor=actor,
        reason=reason,
        public_message=f'Reabertura solicitada: {reason}',
        source=source,
        notify=True,
    )
