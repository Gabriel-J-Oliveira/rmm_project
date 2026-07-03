from django.db.models import Count, Q
from django.utils import timezone

from ..models import DeskQueue, DeskSLA, DeskTemplate, Ticket, TicketCategory
from .desk_templates import render_template, templates_for_ticket


TYPE_LABELS = {
    TicketCategory.TYPE_INCIDENT: 'Incidente',
    TicketCategory.TYPE_REQUEST: 'Solicitação',
    TicketCategory.TYPE_RMM_ALERT: 'Alerta RMM',
    TicketCategory.TYPE_GMUD: 'GMUD',
}

PRIORITY_BADGE = {
    Ticket.PRIORITY_LOW: 'is-muted',
    Ticket.PRIORITY_NORMAL: 'is-ok',
    Ticket.PRIORITY_HIGH: 'is-warning',
    Ticket.PRIORITY_CRITICAL: 'is-danger',
}

VALID_CATEGORY_ICONS = {
    'bi-folder',
    'bi-key',
    'bi-person-lock',
    'bi-pc-display',
    'bi-laptop',
    'bi-printer',
    'bi-envelope',
    'bi-wifi',
    'bi-hdd-network',
    'bi-shield-check',
    'bi-bug',
    'bi-tools',
    'bi-terminal',
    'bi-database',
    'bi-cloud',
    'bi-arrow-repeat',
    'bi-gear',
    'bi-kanban',
    'bi-diagram-3',
    'bi-exclamation-triangle',
    'bi-clipboard-check',
    'bi-file-earmark-text',
    'bi-lock',
    'bi-router',
    'bi-window',
}

VALID_CATEGORY_COLORS = {'green', 'blue', 'purple', 'amber', 'red', 'cyan', 'gray'}

LEGACY_ICON_MAP = {
    'key-round': 'bi-key',
    'cpu': 'bi-pc-display',
    'package': 'bi-window',
    'network': 'bi-hdd-network',
    'shield-alert': 'bi-shield-check',
    'activity': 'bi-exclamation-triangle',
    'mail': 'bi-envelope',
    'printer': 'bi-printer',
    'git-branch': 'bi-diagram-3',
    'folder': 'bi-folder',
}

LEGACY_COLOR_MAP = {
    '#22c55e': 'green',
    '#38bdf8': 'cyan',
    '#f59e0b': 'amber',
    '#ef4444': 'red',
    '#a855f7': 'purple',
    'Verde': 'green',
    'Ciano': 'cyan',
    'Âmbar': 'amber',
    'Ã‚mbar': 'amber',
    'Roxo': 'purple',
}


def normalize_category_icon(value):
    value = (value or '').strip()
    value = LEGACY_ICON_MAP.get(value, value)
    return value if value in VALID_CATEGORY_ICONS else 'bi-folder'


def normalize_category_color(value):
    value = (value or '').strip()
    value = LEGACY_COLOR_MAP.get(value, value)
    return value if value in VALID_CATEGORY_COLORS else 'gray'


def priority_label(value):
    return dict(Ticket.PRIORITY_CHOICES).get(value or '', value or 'Normal')


def minutes_label(minutes):
    minutes = int(minutes or 0)
    if minutes < 60:
        return f'{minutes}min'
    if minutes % 1440 == 0:
        return f'{minutes // 1440}d'
    if minutes % 60 == 0:
        return f'{minutes // 60}h'
    return f'{minutes // 60}h {minutes % 60}min'


def _status_label(active):
    return 'Ativa' if active else 'Inativa'


def _status_value(active):
    return 'active' if active else 'inactive'


def _updated_label(value):
    if not value:
        return 'Agora'
    delta = timezone.now() - value
    if delta.days == 0:
        return 'Hoje'
    if delta.days == 1:
        return 'Ontem'
    if delta.days < 7:
        return f'{delta.days} dias'
    return '1 semana'


def build_settings_context():
    categories = list(
        TicketCategory.objects.select_related('default_queue', 'default_sla')
        .annotate(open_count=Count('tickets', filter=Q(tickets__status__in=[
            Ticket.STATUS_NEW,
            Ticket.STATUS_IN_PROGRESS,
            Ticket.STATUS_WAITING_USER,
            Ticket.STATUS_WAITING_THIRD_PARTY,
        ])))
        .order_by('name')
    )
    queues = list(DeskQueue.objects.order_by('name'))
    slas = list(
        DeskSLA.objects.annotate(category_count=Count('default_categories', distinct=True))
        .order_by('resolution_minutes')
    )
    templates = list(
        DeskTemplate.objects.select_related('category').order_by('template_type', 'name')
    )

    category_rows = []
    for category in categories:
        allowed_types = category.allowed_types or [TicketCategory.TYPE_INCIDENT]
        has_sla = bool(category.default_sla)
        has_queue = bool(category.default_queue)
        is_gmud = TicketCategory.TYPE_GMUD in allowed_types or 'GMUD' in category.name.upper()
        category_rows.append({
            'id': str(category.pk),
            'name': category.name,
            'description': category.description,
            'icon': normalize_category_icon(category.icon),
            'color': normalize_category_color(category.color),
            'status': _status_value(category.is_active),
            'status_label': _status_label(category.is_active),
            'allowed_types': [TYPE_LABELS.get(value, value) for value in allowed_types],
            'allowed_type_values': ','.join(allowed_types),
            'default_queue': category.default_queue.name if category.default_queue else '—',
            'default_sla': category.default_sla.name if category.default_sla else '—',
            'default_sla_id': str(category.default_sla_id or ''),
            'default_queue_id': str(category.default_queue_id or ''),
            'default_priority': category.default_priority or Ticket.PRIORITY_NORMAL,
            'default_priority_label': priority_label(category.default_priority),
            'priority_badge': PRIORITY_BADGE.get(category.default_priority, 'is-ok'),
            'sla_badge': PRIORITY_BADGE.get(category.default_sla.priority if category.default_sla else '', 'is-muted') if has_sla else 'is-warning',
            'subcategories': ', '.join(category.subcategories or []),
            'subcategories_count': len(category.subcategories or []),
            'open_count': category.open_count,
            'filter_tags': ' '.join(filter(None, [
                _status_value(category.is_active),
                'with-sla' if has_sla else 'without-sla',
                'without-queue' if not has_queue else '',
                'gmud' if is_gmud else '',
            ])),
        })

    queue_rows = []
    for queue in queues:
        open_count = Ticket.objects.filter(queue=queue.name, status__in=[
            Ticket.STATUS_NEW,
            Ticket.STATUS_IN_PROGRESS,
            Ticket.STATUS_WAITING_USER,
            Ticket.STATUS_WAITING_THIRD_PARTY,
        ]).count()
        receives = []
        if queue.receives_tickets:
            receives.append('Chamados')
        if queue.receives_rmm:
            receives.append('RMM')
        if queue.receives_gmud:
            receives.append('GMUD')
        capacity = queue.capacity or 0
        overloaded = bool(capacity and open_count >= capacity * 0.8)
        queue_rows.append({
            'id': str(queue.pk),
            'name': queue.name,
            'description': queue.description,
            'responsible': queue.responsible or 'Sem responsável',
            'members': queue.members or [],
            'members_count': len(queue.members or []),
            'business_hours': queue.business_hours or 'Comercial',
            'capacity': capacity,
            'capacity_label': f'{open_count}/{capacity}' if capacity else f'{open_count}/—',
            'capacity_badge': 'is-warning' if overloaded else 'is-ok',
            'receives': receives or ['—'],
            'receives_tickets': queue.receives_tickets,
            'receives_rmm': queue.receives_rmm,
            'receives_gmud': queue.receives_gmud,
            'status': _status_value(queue.is_active),
            'status_label': _status_label(queue.is_active),
            'filter_tags': ' '.join(filter(None, [
                _status_value(queue.is_active),
                'gmud' if queue.receives_gmud else '',
                'overloaded' if overloaded else '',
                'without-owner' if not queue.responsible else '',
            ])),
        })

    sla_rows = []
    for sla in slas:
        is_critical = sla.priority == Ticket.PRIORITY_CRITICAL or 'crítica' in sla.name.casefold()
        sla_rows.append({
            'id': str(sla.pk),
            'name': sla.name,
            'description': sla.description,
            'priority': sla.priority,
            'priority_label': priority_label(sla.priority),
            'first_response': minutes_label(sla.first_response_minutes),
            'resolution': minutes_label(sla.resolution_minutes),
            'calendar': '24x7' if sla.calendar_type == DeskSLA.CALENDAR_24X7 else 'Comercial',
            'pause': 'Sim' if sla.pause_on_waiting_requester else 'Não',
            'category_count': sla.category_count,
            'status': _status_value(sla.is_active),
            'status_label': _status_label(sla.is_active),
            'badge': PRIORITY_BADGE.get(sla.priority, 'is-ok'),
            'filter_tags': ' '.join(filter(None, [
                _status_value(sla.is_active),
                '24x7' if sla.calendar_type == DeskSLA.CALENDAR_24X7 else 'business',
                'critical' if is_critical else '',
                'without-category' if not sla.category_count else '',
            ])),
        })

    template_rows = []
    for template in templates:
        template_rows.append({
            'id': str(template.pk),
            'name': template.name,
            'description': template.description,
            'template_type': template.template_type,
            'template_type_label': template.get_template_type_display(),
            'application': template.application,
            'application_label': template.get_application_display(),
            'category': template.category.name if template.category else 'Geral',
            'category_id': str(template.category_id or ''),
            'channel': template.channel,
            'channel_label': template.get_channel_display(),
            'trigger': template.trigger,
            'subject': template.subject,
            'content': template.content,
            'variables': template.variables or [],
            'variables_count': len(template.variables or []),
            'status': _status_value(template.is_active),
            'status_label': 'Ativo' if template.is_active else 'Inativo',
            'updated': _updated_label(template.updated_at),
            'filter_tags': f'{_status_value(template.is_active)} {template.template_type}',
        })

    context = {
        'desk_categories': category_rows,
        'desk_queues': queue_rows,
        'desk_slas': sla_rows,
        'desk_templates': template_rows,
        'desk_queue_options': queues,
        'desk_sla_options': slas,
        'settings_counts': {
            'categories': len(category_rows),
            'queues': len(queue_rows),
            'slas': len(sla_rows),
            'templates': len(template_rows),
        },
        'settings_metrics': {
            'categories_active': sum(row['status'] == 'active' for row in category_rows),
            'categories_without_sla': sum('without-sla' in row['filter_tags'] for row in category_rows),
            'categories_gmud': sum('gmud' in row['filter_tags'] for row in category_rows),
            'queues_active': sum(row['status'] == 'active' for row in queue_rows),
            'queues_gmud': sum('gmud' in row['filter_tags'] for row in queue_rows),
            'queues_overloaded': sum('overloaded' in row['filter_tags'] for row in queue_rows),
            'slas_active': sum(row['status'] == 'active' for row in sla_rows),
            'slas_critical': sum('critical' in row['filter_tags'] for row in sla_rows),
            'slas_24x7': sum('24x7' in row['filter_tags'] for row in sla_rows),
            'templates_active': sum(row['status'] == 'active' for row in template_rows),
            'templates_resolution': sum(row['template_type'] == DeskTemplate.TYPE_RESOLUTION for row in template_rows),
            'templates_gmud': sum(row['template_type'] == DeskTemplate.TYPE_GMUD for row in template_rows),
        },
    }
    return context


def render_template_content(template, ticket_view):
    record = getattr(ticket_view, 'record', None)
    replacements = {
        '{{solicitante}}': getattr(ticket_view, 'requester', '') or '',
        '{{titulo}}': getattr(ticket_view, 'title', '') or '',
        '{{categoria}}': getattr(ticket_view, 'category', '') or '',
        '{{prioridade}}': getattr(ticket_view, 'priority_label', '') or '',
        '{{endpoint}}': getattr(ticket_view, 'endpoint_name', '') or '',
        '{{tecnico}}': getattr(ticket_view, 'assigned_to', '') or 'Equipe NightOwl',
        '{{fila}}': getattr(ticket_view, 'queue', '') or '',
        '{{data}}': timezone.localdate().strftime('%d/%m/%Y'),
        '{{sla}}': getattr(getattr(record, 'sla', None), 'name', '') if record else '',
    }
    content = template.content
    for token, value in replacements.items():
        content = content.replace(token, value)
    return content


def composer_template_context(ticket_view):
    templates = DeskTemplate.objects.filter(
        is_active=True,
        application__in=['Composer público', 'Composer interno', 'Composer', 'Resolver chamado'],
    ).select_related('category').order_by('template_type', 'name')
    return [
        {
            'name': template.name,
            'content': render_template_content(template, ticket_view),
            'channel': template.channel,
            'type': template.get_template_type_display(),
        }
        for template in templates
    ]


def render_template_content(template, ticket_view):
    record = getattr(ticket_view, 'record', ticket_view)
    return render_template(template, record, getattr(ticket_view, 'assigned_to', ''))


def composer_template_context(ticket_view, user=None):
    record = getattr(ticket_view, 'record', ticket_view)
    if not isinstance(record, Ticket):
        return []
    return templates_for_ticket(
        record,
        [DeskTemplate.APP_COMPOSER_PUBLIC, DeskTemplate.APP_COMPOSER_INTERNAL],
        user=user or getattr(ticket_view, 'assigned_to', ''),
    )
