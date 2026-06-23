from .dashboard_rmm_context import get_dashboard_fleet_context
from ..mock_data import MOCK_TICKETS


def _open_tickets():
    return [ticket for ticket in MOCK_TICKETS if ticket.status not in {'closed', 'canceled'}]


def _central_url(**params):
    query = '&'.join(f'{key}={value}' for key, value in params.items() if value)
    return f'/tickets/central/{f"?{query}" if query else ""}'


def _sparkline(seed):
    base = [22, 34, 29, 45, 38, 52, 48]
    return [min(96, max(8, value + seed)) for value in base]


def _kpis():
    resolved = len([ticket for ticket in MOCK_TICKETS if ticket.status == 'resolved'])
    open_items = _open_tickets()
    critical = len([ticket for ticket in open_items if ticket.priority == 'critical'])
    return [
        {
            'label': 'Chamados resolvidos',
            'value': resolved or 3,
            'delta': '+12%',
            'trend': 'positive',
            'context': 'positive',
            'sparkline': _sparkline(4),
            'url': _central_url(status='resolved'),
        },
        {
            'label': '% SLA cumprido',
            'value': '91%',
            'delta': '-3 pts',
            'trend': 'negative',
            'context': 'negative',
            'sparkline': _sparkline(-2),
            'url': _central_url(stale='1'),
        },
        {
            'label': 'Tempo medio resolucao',
            'value': '3h42',
            'delta': '-18%',
            'trend': 'positive',
            'context': 'positive',
            'sparkline': _sparkline(-8),
            'url': _central_url(status='resolved'),
        },
        {
            'label': 'Satisfacao CSAT',
            'value': '4.7',
            'delta': '+0.3',
            'trend': 'positive',
            'context': 'positive',
            'sparkline': _sparkline(10),
            'url': _central_url(),
        },
        {
            'label': 'Criticos ativos',
            'value': critical,
            'delta': '+2',
            'trend': 'negative',
            'context': 'negative',
            'sparkline': _sparkline(14),
            'url': _central_url(priority='critical'),
        },
    ]


def _heatmap():
    days = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sab']
    hours = ['08-10', '10-12', '12-14', '14-16', '16-18']
    rows = []
    for day_index, day in enumerate(days):
        cells = []
        for hour_index, hour in enumerate(hours):
            value = ((day_index + 2) * (hour_index + 3)) % 17 + (6 if day in {'Ter', 'Qui'} else 2)
            intensity = min(100, value * 5)
            cells.append({'hour': hour, 'value': value, 'intensity': intensity})
        rows.append({'day': day, 'cells': cells})
    return {'hours': hours, 'rows': rows}


def _technician_ranking():
    owners = {}
    for ticket in MOCK_TICKETS:
        owner = ticket.assigned_to or 'Sem responsavel'
        if owner == 'Sem responsavel':
            continue
        owners.setdefault(owner, {'name': owner, 'resolved': 0, 'open': 0})
        if ticket.status == 'resolved':
            owners[owner]['resolved'] += 1
        elif ticket.status not in {'closed', 'canceled'}:
            owners[owner]['open'] += 1
    rows = []
    for index, row in enumerate(sorted(owners.values(), key=lambda item: (item['resolved'], -item['open']), reverse=True), start=1):
        rows.append({
            'rank': index,
            'name': row['name'],
            'resolved': row['resolved'] or max(1, 7 - index),
            'avg_resolution': f'{38 + (index * 11)} min',
            'reopen_rate': f'{index + 1}%',
            'csat': f'{4.9 - (index * 0.1):.1f}',
            'is_best': index == 1,
            'url': _central_url(assigned_to=row['name']),
        })
    return rows


def _mode_widgets():
    return {
        'operational': ['anomaly', 'heatmap', 'ranking'],
        'management': ['kpis', 'fleet-summary', 'sla-gauge', 'annotations'],
        'infrastructure': ['fleet-health', 'fleet-alerts', 'sla-gauge'],
    }


def build_ticket_dashboard_context(request):
    mode = request.GET.get('mode', 'operational')
    if mode not in {'operational', 'management', 'infrastructure'}:
        mode = 'operational'
    fleet = get_dashboard_fleet_context()
    return {
        'dashboard_mode': mode,
        'period': request.GET.get('period', 'week'),
        'compare_enabled': request.GET.get('compare', '1') == '1',
        'modes': [
            {'key': 'operational', 'label': 'Operacional', 'icon': 'activity'},
            {'key': 'management', 'label': 'Gerencial', 'icon': 'line-chart'},
            {'key': 'infrastructure', 'label': 'Infraestrutura', 'icon': 'server'},
        ],
        'dashboard_kpis': _kpis(),
        'heatmap': _heatmap(),
        'technician_ranking': _technician_ranking(),
        'anomaly': {
            'active': True,
            'title': 'Volume 42% acima da media para tercas-feiras',
            'description': 'Padrao simples calculado contra o historico do mesmo recorte de dia/horario.',
            'url': _central_url(stale='1'),
        },
        'fleet': fleet,
        'mode_widgets': _mode_widgets().get(mode, []),
        'annotations': [
            {'date': '12/06', 'label': 'Migracao de servidor', 'description': 'Janela de mudanca impactou chamados de acesso.'},
            {'date': '18/06', 'label': 'Treinamento equipe', 'description': 'Reducao esperada no tempo de triagem.'},
        ],
        'layout_cards': [
            {'label': 'Heatmap', 'size': 'Grande'},
            {'label': 'Ranking', 'size': 'Medio'},
            {'label': 'Saude da frota', 'size': 'Medio'},
        ],
        'sector_options': sorted({ticket.sector for ticket in MOCK_TICKETS}),
        'team_options': sorted({ticket.assigned_to for ticket in MOCK_TICKETS if ticket.assigned_to}),
    }
