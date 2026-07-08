import re

from django.utils import timezone

from ..models import DeskTemplate


TEMPLATE_TOKEN_RE = re.compile(r'{{\s*([a-zA-Z0-9_]+)\s*}}')


def _user_name(user):
    if user is None:
        return ''
    if isinstance(user, str):
        return user
    if getattr(user, 'is_authenticated', False):
        return user.get_full_name() or user.get_username()
    return ''


def template_context(ticket, user=None, extra_context=None):
    endpoint = ticket.endpoint.hostname if ticket.endpoint else ticket.endpoint_name
    action_url = ''
    if extra_context and extra_context.get('action_url'):
        action_url = str(extra_context.get('action_url') or '').strip()
    context = {
        'ticket_code': f'#{ticket.number}',
        'titulo': ticket.title,
        'solicitante': ticket.requester_name or ticket.requester_email,
        'tecnico': _user_name(user) or ticket.assigned_to or 'Equipe Night Owl',
        'categoria': ticket.category.name if ticket.category else '',
        'prioridade': ticket.get_priority_display(),
        'fila': ticket.queue,
        'endpoint': endpoint or '',
        'solucao': '',
        'status': ticket.get_status_display(),
        'responsavel': ticket.assigned_to or 'Equipe Night Owl',
        'mensagem': '',
        'motivo': '',
        'resumo': ticket.description,
        'action_url': action_url,
        'link_acompanhamento': action_url or 'Acesse o NightOwl Desk para acompanhar este chamado.',
        'data': timezone.localtime().strftime('%d/%m/%Y %H:%M'),
        'data_abertura': timezone.localtime(ticket.created_at).strftime('%d/%m/%Y %H:%M') if getattr(ticket, 'created_at', None) else '',
        'data_resolucao': timezone.localtime(ticket.resolved_at).strftime('%d/%m/%Y %H:%M') if getattr(ticket, 'resolved_at', None) else '',
    }
    if extra_context:
        context.update({key: '' if value is None else str(value) for key, value in extra_context.items()})
    return context


def render_template(template, ticket, user=None, extra_context=None):
    context = template_context(ticket, user=user, extra_context=extra_context)
    return render_text(template.content, context)


def render_text(value, context):
    def replace_token(match):
        return str(context.get(match.group(1), ''))

    return TEMPLATE_TOKEN_RE.sub(replace_token, value or '')


def render_subject(template, ticket, user=None, extra_context=None):
    context = template_context(ticket, user=user, extra_context=extra_context)
    return render_text(template.subject, context)


def template_payload(template, ticket, user=None, extra_context=None):
    context = template_context(ticket, user, extra_context)
    return {
        'id': str(template.pk),
        'name': template.name,
        'subject': render_text(template.subject, context),
        'content': render_template(template, ticket, user, extra_context),
        'channel': template.channel,
        'type': template.get_template_type_display(),
        'application': template.application,
    }


def templates_for_ticket(ticket, applications, user=None):
    templates = DeskTemplate.objects.filter(
        is_active=True,
        application__in=applications,
    ).filter(category__isnull=True) | DeskTemplate.objects.filter(
        is_active=True,
        application__in=applications,
        category=ticket.category,
    )
    return [
        template_payload(template, ticket, user=user)
        for template in templates.select_related('category').distinct().order_by('name')
    ]
