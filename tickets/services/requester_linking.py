from django.db import transaction
from django.utils import timezone

from access_inventory.models import ADUser

from ..models import Ticket
from .desk_mvp1 import create_audit_event


class RequesterLinkError(ValueError):
    """A request to change a requester link is invalid."""


class RequesterLinkConflict(RequesterLinkError):
    """The ticket changed after the client loaded its current requester."""


def requester_identity_snapshot(user):
    if not user:
        return None
    return {
        'id': str(user.pk),
        'display_name': user.display_name or '',
        'sam_account_name': user.sam_account_name or '',
        'user_principal_name': user.user_principal_name or '',
        'email': user.email or '',
    }


def _canonical_id(value):
    return None if value is None or str(value).strip() == '' else str(value)


def change_requester_link(
    ticket,
    *,
    new_user,
    expected_requester_ad_user_id,
    actor_name,
    actor_user=None,
    reason='',
):
    """Associate, replace, or remove a requester without changing snapshots."""
    expected_id = _canonical_id(expected_requester_ad_user_id)
    reason = str(reason or '').strip()

    with transaction.atomic():
        locked_ticket = (
            Ticket.objects.select_for_update()
            .select_related('requester_ad_user')
            .get(pk=ticket.pk)
        )
        current_user = locked_ticket.requester_ad_user
        current_id = _canonical_id(locked_ticket.requester_ad_user_id)
        if current_id != expected_id:
            raise RequesterLinkConflict('O vinculo do solicitante mudou. Atualize o chamado e tente novamente.')

        replacing = current_user is not None and new_user is not None
        removing = current_user is not None and new_user is None
        if (replacing or removing) and not reason:
            raise RequesterLinkError('Informe o motivo para trocar ou remover o vinculo.')
        if current_user is None and new_user is None:
            raise RequesterLinkError('O chamado nao possui vinculo para remover.')
        if current_id == _canonical_id(getattr(new_user, 'pk', None)):
            raise RequesterLinkError('Este usuario ja esta associado ao chamado.')

        previous_snapshot = requester_identity_snapshot(current_user)
        new_snapshot = requester_identity_snapshot(new_user)
        if new_user is None:
            event_type = 'requester_unlinked'
            action = 'Removeu solicitante corporativo'
            origin = ''
        elif current_user is None:
            event_type = 'requester_linked'
            action = 'Associou solicitante corporativo'
            origin = Ticket.REQUESTER_LINK_MANUAL
        else:
            event_type = 'requester_replaced'
            action = 'Trocou solicitante corporativo'
            origin = Ticket.REQUESTER_LINK_MANUAL

        locked_ticket.requester_ad_user = new_user
        locked_ticket.requester_link_origin = origin
        locked_ticket.requester_linked_at = timezone.now() if new_user else None
        locked_ticket.save(update_fields=[
            'requester_ad_user',
            'requester_link_origin',
            'requester_linked_at',
            'updated_at',
        ])

        event = create_audit_event(
            locked_ticket,
            actor=actor_name,
            event_type=event_type,
            action=action,
            field_name='requester_ad_user',
            old_value=current_id or '',
            new_value=_canonical_id(getattr(new_user, 'pk', None)) or '',
            metadata={
                'origin': 'manual',
                'previous_user': previous_snapshot,
                'new_user': new_snapshot,
                'reason': reason,
                'actor_user_id': str(actor_user.pk) if actor_user and actor_user.pk else '',
            },
        )
    return locked_ticket, event
