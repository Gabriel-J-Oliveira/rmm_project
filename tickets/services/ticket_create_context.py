from .desk_mvp2 import normalize_category_color, normalize_category_icon
from .ticket_alert_mapping import alert_mapping_summary, category_for_alert, priority_for_alert
from .ticket_creation_rmm import get_endpoint_preview, get_ticket_creation_endpoints
from ..mock_data import CATEGORIES, MOCK_TICKETS, PRIORITY_LABELS
from ..models import Ticket, TicketCategory


CATEGORY_ICONS = {
    'Acesso': 'key-round',
    'Hardware': 'monitor-cog',
    'Software': 'package',
    'Rede': 'wifi',
    'Impressora': 'printer',
    'Servidor': 'server',
    'Seguranca': 'shield-alert',
    'RMM / Alerta': 'activity',
    'Solicitacao': 'inbox',
}

CATEGORY_PRIORITY_HINTS = {
    'Acesso': 'normal',
    'Hardware': 'normal',
    'Software': 'normal',
    'Rede': 'high',
    'Impressora': 'normal',
    'Servidor': 'high',
    'Seguranca': 'critical',
    'RMM / Alerta': 'high',
    'Solicitacao': 'normal',
}

REQUESTER_ROWS = [
    {
        'name': 'Henrique Valente',
        'email': 'henrique.valente@empresa.com.br',
        'username': 'henrique.valente',
        'department': 'Diretoria',
        'role': 'Socio',
        'vip': True,
    },
    {
        'name': 'Mariana Souza',
        'email': 'mariana.souza@empresa.com.br',
        'username': 'mariana.souza',
        'department': 'Financeiro',
        'role': 'Analista financeiro',
        'vip': False,
    },
    {
        'name': 'Renata Lima',
        'email': 'renata.lima@empresa.com.br',
        'username': 'renata.lima',
        'department': 'Juridico',
        'role': 'Assistente juridico',
        'vip': False,
    },
    {
        'name': 'Daniel Ribeiro',
        'email': 'daniel.ribeiro@empresa.com.br',
        'username': 'daniel.ribeiro',
        'department': 'Comercial',
        'role': 'Supervisor comercial',
        'vip': False,
    },
]

TICKET_TEMPLATES = [
    {
        'name': 'Acesso a sistema',
        'category': 'Acesso',
        'priority': 'normal',
        'description': 'Solicitacao de liberacao, ajuste ou revisao de acesso.',
    },
    {
        'name': 'Alerta de seguranca',
        'category': 'Seguranca',
        'priority': 'critical',
        'description': 'Alerta de monitoramento exige validacao tecnica imediata.',
    },
    {
        'name': 'Equipamento lento',
        'category': 'Hardware',
        'priority': 'normal',
        'description': 'Usuario relata lentidao ou instabilidade em equipamento.',
    },
]


def _category_rows():
    rows = []
    categories = TicketCategory.objects.select_related('default_queue', 'default_sla').filter(is_active=True).order_by('name')
    if categories.exists():
        for category in categories:
            priority = category.default_priority or Ticket.PRIORITY_NORMAL
            rows.append({
                'id': str(category.pk),
                'name': category.name,
                'description': category.description,
                'color': normalize_category_color(category.color),
                'icon': normalize_category_icon(category.icon),
                'suggested_priority': priority,
                'suggested_priority_label': PRIORITY_LABELS.get(priority, priority),
                'default_priority': priority,
                'default_queue': category.default_queue.name if category.default_queue else '',
                'default_sla': category.default_sla.name if category.default_sla else '',
                'allowed_types': category.allowed_types or [],
                'subcategories': category.subcategories or [],
            })
        return rows

    for category in CATEGORIES:
        name = category['name']
        priority = CATEGORY_PRIORITY_HINTS.get(name, 'normal')
        rows.append({
            **category,
            'icon': CATEGORY_ICONS.get(name, 'circle'),
            'suggested_priority': priority,
            'suggested_priority_label': PRIORITY_LABELS.get(priority, priority),
        })
    return rows


def _requester_rows():
    seen = {row['username'] for row in REQUESTER_ROWS}
    rows = list(REQUESTER_ROWS)
    for ticket in MOCK_TICKETS:
        username = ticket.requester.lower().replace(' ', '.')
        if username in seen:
            continue
        seen.add(username)
        rows.append({
            'name': ticket.requester,
            'email': f'{username}@empresa.com.br',
            'username': username,
            'department': ticket.sector,
            'role': ticket.role,
            'vip': ticket.partner,
        })
    return rows[:12]


def _duplicates_for(category, endpoint_hostname):
    if not category or not endpoint_hostname:
        return []
    duplicates = []
    for ticket in MOCK_TICKETS:
        if ticket.status in {'resolved', 'closed', 'canceled'}:
            continue
        if ticket.category != category or not ticket.endpoint:
            continue
        if ticket.endpoint.hostname.casefold() != endpoint_hostname.casefold():
            continue
        duplicates.append({
            'number': ticket.number,
            'title': ticket.title,
            'status_label': ticket.status_label,
            'updated_for': ticket.updated_for,
        })
    return duplicates[:3]


def _alert_preview(alert_id):
    if not alert_id:
        return None
    try:
        from agents.models import EndpointAlert

        alert = EndpointAlert.objects.select_related('endpoint').get(id=alert_id)
    except Exception:
        return None

    category = category_for_alert(alert.alert_type)
    priority = priority_for_alert(alert.severity)
    endpoint = get_endpoint_preview(endpoint_id=alert.endpoint_id, hostname=alert.endpoint.hostname)
    return {
        'id': str(alert.id),
        'title': alert.title,
        'alert_type': alert.alert_type,
        'severity': alert.severity,
        'category': category,
        'priority': priority,
        'priority_label': PRIORITY_LABELS.get(priority, priority),
        'endpoint': endpoint,
        'description': (
            f'Alerta RMM: {alert.title}\n\n'
            f'Dispositivo: {alert.endpoint.hostname}\n'
            f'Tipo: {alert.alert_type}\n'
            f'Severidade: {alert.severity}\n'
            f'Detalhes: {alert.description}'
        ),
    }


def build_ticket_create_context(request):
    alert = _alert_preview(request.GET.get('alert'))
    endpoints = get_ticket_creation_endpoints()
    query_endpoint = request.GET.get('endpoint', '').strip()
    selected_endpoint = (
        alert['endpoint']
        if alert
        else get_endpoint_preview(hostname=query_endpoint) if query_endpoint
        else (endpoints[0] if endpoints else None)
    )
    initial_category = request.GET.get('category') or (alert['category'] if alert else 'Solicitacao')
    initial_priority = request.GET.get('priority') or (alert['priority'] if alert else CATEGORY_PRIORITY_HINTS.get(initial_category, 'normal'))
    initial_title = alert['title'] if alert else ''
    initial_description = alert['description'] if alert else ''

    return {
        'create_mode': request.GET.get('mode', 'complete'),
        'category_options': _category_rows(),
        'priority_options': [
            {'value': value, 'label': label}
            for value, label in PRIORITY_LABELS.items()
        ],
        'requester_options': _requester_rows(),
        'endpoint_options': endpoints,
        'selected_endpoint': selected_endpoint,
        'duplicate_candidates': _duplicates_for(
            initial_category,
            selected_endpoint['hostname'] if selected_endpoint else '',
        ),
        'ticket_templates': TICKET_TEMPLATES,
        'alert_preview': alert,
        'alert_mapping': alert_mapping_summary(),
        'initial_ticket': {
            'title': initial_title,
            'description': initial_description,
            'category': initial_category,
            'priority': initial_priority,
            'priority_label': PRIORITY_LABELS.get(initial_priority, initial_priority),
        },
        'draft_storage_key': 'nightowl.ticket.create.draft',
    }
