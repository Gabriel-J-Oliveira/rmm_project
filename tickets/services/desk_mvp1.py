from types import SimpleNamespace

from django.db.models import Q
from django.utils import timezone
from django.utils.timesince import timesince

from ..mock_data import PRIORITY_LABELS, STATUS_LABELS
from .desk_mvp2 import normalize_category_color, normalize_category_icon
from ..models import Ticket, TicketAuditEvent, TicketCategory


CURRENT_TECHNICIAN = 'Técnico'
OPEN_STATUSES = {
    Ticket.STATUS_NEW,
    Ticket.STATUS_IN_PROGRESS,
    Ticket.STATUS_WAITING_USER,
    Ticket.STATUS_WAITING_THIRD_PARTY,
}


def relative_time(value):
    if not value:
        return '--'
    now = timezone.now()
    local = timezone.localtime(value)
    delta = now - value
    if delta.total_seconds() < 60:
        return 'agora'
    if delta.days == 0:
        return f'ha {timesince(value, now).split(",")[0]}'
    if delta.days == 1:
        return 'ontem'
    return f'ha {timesince(value, now).split(",")[0]}'


def display_time(value):
    if not value:
        return '--'
    local = timezone.localtime(value)
    if local.date() == timezone.localdate():
        return f'Hoje, {local:%H:%M}'
    return local.strftime('%d/%m/%Y, %H:%M')


def _endpoint_view(ticket):
    endpoint = ticket.endpoint
    hostname = endpoint.hostname if endpoint else ticket.endpoint_name
    if not hostname:
        return None
    return SimpleNamespace(
        hostname=hostname,
        status=getattr(endpoint, 'status', 'unknown') or 'unknown',
        domain=getattr(endpoint, 'domain', ''),
        last_user=getattr(endpoint, 'last_user', '') or '-',
        last_heartbeat=relative_time(getattr(endpoint, 'last_heartbeat', None)) if endpoint else 'sem check-in',
        url=f'/endpoints/{getattr(endpoint, "pk", "")}/' if endpoint else '/endpoints/',
    )


def _comment_view(comment):
    return SimpleNamespace(
        id=str(comment.pk),
        author=comment.author_name or 'Sistema',
        author_name=comment.author_name or 'Sistema',
        body=comment.body,
        when=relative_time(comment.created_at),
        visibility='Publico' if comment.visibility == 'public' else 'Interno',
        visibility_value=comment.visibility,
        created_at=comment.created_at,
    )


def adapt_ticket(ticket):
    comments = [_comment_view(comment) for comment in ticket.comments.all()]
    attachments = [
        SimpleNamespace(
            id=str(attachment.pk),
            name=attachment.original_name,
            original_name=attachment.original_name,
            size=attachment.size,
            visibility='Publico' if attachment.visibility == 'public' else 'Interno',
            visibility_value=attachment.visibility,
            when=relative_time(attachment.created_at),
        )
        for attachment in getattr(ticket, 'attachments').all()
    ] if hasattr(ticket, 'attachments') else []
    category = ticket.category.name if ticket.category else 'Sem categoria'
    category_icon = normalize_category_icon(ticket.category.icon) if ticket.category else 'bi-folder'
    category_color = normalize_category_color(ticket.category.color) if ticket.category else 'gray'
    return SimpleNamespace(
        record=ticket,
        id=str(ticket.pk),
        number=ticket.number,
        title=ticket.title,
        description=ticket.description,
        requester=ticket.requester_name or ticket.requester_email or 'Solicitante nao informado',
        requester_name=ticket.requester_name,
        requester_email=ticket.requester_email,
        sector=ticket.requester_department or 'Sem setor',
        role=ticket.requester_role or '',
        partner=ticket.requester_is_partner,
        priority=ticket.priority,
        priority_label=PRIORITY_LABELS.get(ticket.priority, ticket.get_priority_display()),
        status=ticket.status,
        status_label=STATUS_LABELS.get(ticket.status, ticket.get_status_display()),
        category=category,
        category_icon=category_icon,
        category_color=category_color,
        queue=ticket.queue or 'N1 - Atendimento',
        sla=ticket.sla.name if ticket.sla else '',
        due_at=ticket.due_at,
        assigned_to=ticket.assigned_to or '',
        endpoint=_endpoint_view(ticket),
        endpoint_name=ticket.endpoint_name,
        source=ticket.source,
        opened_for=relative_time(ticket.created_at).removeprefix('ha '),
        updated_for=relative_time(ticket.updated_at).removeprefix('ha '),
        created_at=display_time(ticket.created_at),
        first_response_at=display_time(ticket.first_response_at),
        assigned_at=display_time(ticket.assigned_at),
        resolved_at=display_time(ticket.resolved_at),
        comments=comments,
        attachments=attachments,
        attachments_count=len(attachments),
    )


def ticket_queryset():
    return Ticket.objects.select_related('category', 'endpoint', 'sla').prefetch_related('comments', 'attachments', 'audit_events')


def get_ticket_view(number):
    ticket = ticket_queryset().filter(number=number).first()
    return adapt_ticket(ticket) if ticket else None


def _split_values(params, name):
    values = params.getlist(name) if hasattr(params, 'getlist') else [params.get(name)]
    return [part for value in values if value for part in str(value).split(',') if part]


def _assigned_to_q(values):
    query = Q()
    for value in values:
        if value == '':
            query |= Q(assigned_to='')
        elif value:
            query |= Q(assigned_to__iexact=value)
    return query


def _assignee_values(value, current_user_aliases=None):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = [value]
    aliases = [alias for alias in (current_user_aliases or []) if alias]
    values = []
    for item in raw_values:
        item = str(item or '').strip()
        if item == '__me__':
            values.extend(aliases or [CURRENT_TECHNICIAN])
        elif item == '__unassigned__':
            values.append('')
        elif item:
            values.append(item)
    deduped = []
    for item in values:
        key = item.casefold()
        if key not in {existing.casefold() for existing in deduped}:
            deduped.append(item)
    return deduped


def filtered_ticket_views(params, assigned_to=None, current_user_aliases=None):
    queryset = ticket_queryset()
    query = (params.get('q') or params.get('search') or '').strip()
    if query:
        number_query = Q()
        if query.lstrip('#').isdigit():
            number_query = Q(number=int(query.lstrip('#')))
        queryset = queryset.filter(
            number_query
            | Q(title__icontains=query)
            | Q(requester_name__icontains=query)
            | Q(requester_email__icontains=query)
            | Q(requester_department__icontains=query)
            | Q(category__name__icontains=query)
            | Q(endpoint__hostname__icontains=query)
            | Q(endpoint_name__icontains=query)
        )

    statuses = _split_values(params, 'status')
    priorities = _split_values(params, 'priority')
    categories = _split_values(params, 'category')
    queues = _split_values(params, 'queue')
    technicians = _split_values(params, 'technician')
    owner = assigned_to or (params.get('assigned_to') or '').strip()
    origins = _split_values(params, 'origin')
    flags = _split_values(params, 'flag')
    sectors = _split_values(params, 'sector')

    if statuses:
        queryset = queryset.filter(status__in=statuses)
    elif params.get('include_all_statuses') != '1':
        queryset = queryset.filter(status__in=OPEN_STATUSES)
    if priorities:
        queryset = queryset.filter(priority__in=priorities)
    if categories:
        queryset = queryset.filter(category__name__in=categories)
    if queues:
        queryset = queryset.filter(queue__in=queues)
    if sectors:
        queryset = queryset.filter(requester_department__in=sectors)
    if owner:
        owner_values = _assignee_values(owner, current_user_aliases=current_user_aliases)
        queryset = queryset.filter(_assigned_to_q(owner_values))
    if technicians and '__all__' not in technicians:
        normalized = _assignee_values(technicians, current_user_aliases=current_user_aliases)
        queryset = queryset.filter(_assigned_to_q(normalized))
    if params.get('unassigned') == '1' or 'unassigned' in flags:
        queryset = queryset.filter(assigned_to='')
    if params.get('critical') == '1':
        queryset = queryset.filter(priority=Ticket.PRIORITY_CRITICAL)
    if params.get('vip') == '1':
        queryset = queryset.filter(requester_is_partner=True)
    if origins:
        if 'rmm' in origins:
            queryset = queryset.filter(source__in=[Ticket.SOURCE_RMM_ALERT, Ticket.SOURCE_MONITORING])
        elif 'manual' in origins:
            queryset = queryset.exclude(source__in=[Ticket.SOURCE_RMM_ALERT, Ticket.SOURCE_MONITORING])

    sla = _split_values(params, 'sla')
    if params.get('stale') == '1' or sla:
        now = timezone.now()
        if 'critical' in sla or params.get('stale') == '1':
            queryset = queryset.filter(due_at__isnull=False, due_at__lte=now)
        elif 'warning' in sla:
            queryset = queryset.filter(
                due_at__isnull=False,
                due_at__gt=now,
                due_at__lte=now + timezone.timedelta(hours=2),
            )

    return [adapt_ticket(ticket) for ticket in queryset]


def ticket_summary(items):
    items = list(items)
    today = timezone.localdate()
    return {
        'new': sum(ticket.status == Ticket.STATUS_NEW for ticket in items),
        'in_progress': sum(ticket.status == Ticket.STATUS_IN_PROGRESS for ticket in items),
        'waiting_user': sum(ticket.status == Ticket.STATUS_WAITING_USER for ticket in items),
        'critical': sum(ticket.priority == Ticket.PRIORITY_CRITICAL and ticket.status in OPEN_STATUSES for ticket in items),
        'resolved_today': sum(
            ticket.status == Ticket.STATUS_RESOLVED
            and ticket.record.resolved_at
            and timezone.localtime(ticket.record.resolved_at).date() == today
            for ticket in items
        ),
        'avg_first_response': '18 min',
        'open': sum(ticket.status in OPEN_STATUSES for ticket in items),
    }


def active_category_context():
    categories = TicketCategory.objects.select_related('default_queue', 'default_sla').filter(is_active=True).order_by('name')
    return [
        {
            'id': str(category.pk),
            'name': category.name,
            'description': category.description,
            'icon': normalize_category_icon(category.icon),
            'color': normalize_category_color(category.color),
            'active': category.is_active,
            'default_priority': category.default_priority or Ticket.PRIORITY_NORMAL,
            'default_priority_label': dict(Ticket.PRIORITY_CHOICES).get(category.default_priority, 'Normal'),
            'default_queue': category.default_queue.name if category.default_queue else '',
            'default_queue_id': str(category.default_queue_id or ''),
            'default_sla': category.default_sla.name if category.default_sla else '',
            'default_sla_id': str(category.default_sla_id or ''),
            'allowed_types': category.allowed_types or [],
            'subcategories': category.subcategories or [],
        }
        for category in categories
    ]


def create_audit_event(ticket, *, actor, event_type, action, field_name='', old_value='', new_value='', metadata=None):
    return TicketAuditEvent.objects.create(
        ticket=ticket,
        actor=actor or 'Sistema',
        event_type=event_type,
        action=action,
        field_name=field_name,
        old_value='' if old_value is None else str(old_value),
        new_value='' if new_value is None else str(new_value),
        metadata=metadata or {},
    )
