from django.db.models import Count, Q

from .desk_mvp2 import VALID_CATEGORY_ICONS, normalize_category_color, normalize_category_icon, minutes_label, priority_label
from ..models import DeskQueue, DeskSLA, Ticket, TicketCategory


def _sparkline_for(index):
    base = [18, 24, 16, 31, 26, 34]
    return [max(4, value - (index * 2) + ((index + pos) % 4) * 3) for pos, value in enumerate(base)]


def _checklist_for(category):
    defaults = {
        'Acesso': ['Validar solicitante', 'Confirmar aprovacao', 'Registrar permissao aplicada'],
        'Seguranca': ['Isolar risco', 'Validar alerta', 'Registrar evidencia'],
        'RMM / Alerta': ['Validar endpoint', 'Confirmar alerta', 'Resolver ou escalar'],
        'VPN': ['Validar usuario e dispositivo', 'Testar conectividade', 'Registrar evidencias'],
    }
    if category.subcategories:
        return [f'Subcategoria: {item}' for item in category.subcategories[:4]]
    return defaults.get(category.name, ['Entender demanda', 'Executar atendimento', 'Validar com usuario'])


def build_category_settings_context():
    categories = list(
        TicketCategory.objects.select_related('default_queue', 'default_sla')
        .annotate(
            open_count=Count(
                'tickets',
                filter=Q(tickets__status__in=[
                    Ticket.STATUS_NEW,
                    Ticket.STATUS_IN_PROGRESS,
                    Ticket.STATUS_WAITING_USER,
                    Ticket.STATUS_WAITING_THIRD_PARTY,
                ]),
            ),
            period_count=Count('tickets'),
        )
        .order_by('name')
    )
    queues = list(DeskQueue.objects.filter(is_active=True).order_by('name'))
    slas = list(DeskSLA.objects.filter(is_active=True).order_by('resolution_minutes', 'name'))
    rows = []
    for index, category in enumerate(categories):
        sla = category.default_sla
        rows.append({
            'id': str(category.pk),
            'order': index + 1,
            'name': category.name,
            'description': category.description,
            'icon': normalize_category_icon(category.icon),
            'color': normalize_category_color(category.color),
            'active': category.is_active,
            'status': 'active' if category.is_active else 'inactive',
            'default_priority': category.default_priority or Ticket.PRIORITY_NORMAL,
            'default_priority_label': priority_label(category.default_priority),
            'default_queue': category.default_queue.name if category.default_queue else '',
            'default_sla': sla.name if sla else '',
            'first_response_sla': minutes_label(sla.first_response_minutes) if sla else '--',
            'resolution_sla': minutes_label(sla.resolution_minutes) if sla else '--',
            'allowed_type_values': ','.join(category.allowed_types or []),
            'allowed_types': category.allowed_types or [],
            'subcategories': ', '.join(category.subcategories or []),
            'period_count': category.period_count,
            'open_count': category.open_count,
            'sparkline': _sparkline_for(index),
            'checklist': _checklist_for(category),
            'has_open_tickets': category.open_count > 0,
        })
    return {
        'category_rows': rows,
        'category_icon_options': sorted(VALID_CATEGORY_ICONS),
        'category_color_options': ['green', 'blue', 'purple', 'amber', 'red', 'cyan', 'gray'],
        'category_type_options': [
            {'value': TicketCategory.TYPE_INCIDENT, 'label': 'Incidente'},
            {'value': TicketCategory.TYPE_REQUEST, 'label': 'Solicitacao'},
            {'value': TicketCategory.TYPE_RMM_ALERT, 'label': 'Alerta RMM'},
            {'value': TicketCategory.TYPE_GMUD, 'label': 'GMUD'},
        ],
        'category_priority_options': [
            {'value': value, 'label': label}
            for value, label in Ticket.PRIORITY_CHOICES
        ],
        'category_queue_options': queues,
        'category_sla_options': slas,
    }
