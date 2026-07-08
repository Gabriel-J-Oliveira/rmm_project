from urllib.parse import urlsplit

from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse


DESK_EVENT_LABELS = {
    'ticket_created': 'Chamado criado',
    'ticket_assigned': 'Chamado assumido',
    'waiting_requester': 'Aguardando solicitante',
    'ticket_resolved': 'Chamado resolvido',
    'ticket_reopened': 'Chamado reaberto',
    'ticket_public_reply': 'Nova resposta',
}


def _public_url():
    value = str(getattr(settings, 'NIGHTOWL_PUBLIC_URL', '') or '').strip().rstrip('/')
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return ''
    return value


def ticket_action_url(ticket):
    public_url = _public_url()
    if not public_url or ticket is None:
        return ''
    return f"{public_url}{reverse('requester-ticket-detail', kwargs={'number': ticket.number})}"


def render_base_email(
    *,
    email_title,
    email_body,
    email_subtitle='',
    email_badge='Night Owl',
    action_url='',
    action_label='Abrir chamado',
    ticket=None,
    footer_text='',
):
    return render_to_string(
        'emails/base_email.html',
        {
            'email_title': email_title,
            'email_subtitle': email_subtitle,
            'email_badge': email_badge,
            'email_body': email_body,
            'action_url': action_url,
            'action_label': action_label,
            'ticket': ticket,
            'footer_text': footer_text,
            'show_desk_hint': bool(ticket and not action_url),
        },
    )


def render_ticket_email_html(*, ticket, event_type, subject, body_text):
    return render_base_email(
        email_title=subject,
        email_subtitle='Atualizacao do seu atendimento no Night Owl Desk.',
        email_badge=DESK_EVENT_LABELS.get(event_type, 'Night Owl Desk'),
        email_body=body_text,
        action_url=ticket_action_url(ticket),
        action_label='Acompanhar chamado',
        ticket=ticket,
        footer_text='Mensagem automatica do Night Owl Desk. Por favor, nao responda a este e-mail.',
    )
