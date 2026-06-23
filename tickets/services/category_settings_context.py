from ..mock_data import CATEGORIES, MOCK_TICKETS, PRIORITY_LABELS


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


def _sparkline_for(index):
    base = [18, 24, 16, 31, 26, 34]
    return [max(4, value - (index * 2) + ((index + pos) % 4) * 3) for pos, value in enumerate(base)]


def _checklist_for(name):
    defaults = {
        'Acesso': ['Validar solicitante', 'Confirmar aprovacao', 'Registrar permissao aplicada'],
        'Seguranca': ['Isolar risco', 'Validar alerta', 'Registrar evidencia'],
        'RMM / Alerta': ['Validar endpoint', 'Confirmar alerta', 'Resolver ou escalar'],
    }
    return defaults.get(name, ['Entender demanda', 'Executar atendimento', 'Validar com usuario'])


def build_category_settings_context():
    rows = []
    for index, category in enumerate(CATEGORIES):
        name = category['name']
        open_count = len([
            ticket for ticket in MOCK_TICKETS
            if ticket.category == name and ticket.status not in {'resolved', 'closed', 'canceled'}
        ])
        period_count = len([ticket for ticket in MOCK_TICKETS if ticket.category == name])
        rows.append({
            'order': index + 1,
            'name': name,
            'description': category['description'],
            'icon': CATEGORY_ICONS.get(name, 'circle'),
            'active': category.get('active', True),
            'first_response_sla': '30 min' if name in {'Seguranca', 'Servidor', 'RMM / Alerta'} else '1h',
            'resolution_sla': '4h' if name in {'Seguranca', 'Servidor', 'RMM / Alerta'} else '8h',
            'period_count': period_count,
            'open_count': open_count,
            'sparkline': _sparkline_for(index),
            'checklist': _checklist_for(name),
            'has_open_tickets': open_count > 0,
        })
    return {
        'category_rows': rows,
        'category_icon_options': [
            'key-round', 'monitor-cog', 'package', 'wifi', 'printer', 'server',
            'shield-alert', 'activity', 'inbox', 'database', 'mail', 'phone',
        ],
        'priority_labels': PRIORITY_LABELS,
    }
