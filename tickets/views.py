from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .mock_data import CATEGORIES, MOCK_TICKETS, PRIORITY_LABELS, STATUS_LABELS, filter_tickets, get_ticket, summary_for
from .services.dashboard_context import build_ticket_dashboard_context
from .services.ticket_detail_context import build_ticket_detail_context


def _base_context(active_section='queue'):
    return {
        'active_nav': 'tickets',
        'body_class': 'page-tickets',
        'desk_active_section': active_section,
        'categories': CATEGORIES,
        'status_labels': STATUS_LABELS,
        'priority_labels': PRIORITY_LABELS,
    }


def _open_tickets():
    return [
        ticket for ticket in MOCK_TICKETS
        if ticket.status not in {'resolved', 'closed', 'canceled'}
    ]


def _priority_rank(ticket):
    priority_rank = {'critical': 0, 'high': 1, 'normal': 2, 'low': 3}
    return priority_rank.get(ticket.priority, 9)


def _ticket_sort_key(ticket):
    return (
        _priority_rank(ticket),
        0 if ticket.partner else 1,
        0 if not ticket.assigned_to else 1,
        ticket.number * -1,
    )


def _count_by(items, attr_name):
    counts = {}
    for item in items:
        value = getattr(item, attr_name) or 'Sem responsavel'
        counts[value] = counts.get(value, 0) + 1
    return counts


def _central_context(request, active_section='central', assigned_to=None):
    tickets = filter_tickets(request.GET)
    if assigned_to:
        tickets = filter_tickets(request.GET, assigned_to=assigned_to)
    open_tickets = _open_tickets()
    selected_number = request.GET.get('ticket') or (tickets[0].number if tickets else None)
    selected_ticket = get_ticket(selected_number) if selected_number else None
    sorted_tickets = sorted(tickets, key=_ticket_sort_key)
    status_groups = [
        ('new', 'Novo'),
        ('in_progress', 'Em atendimento'),
        ('waiting_user', 'Aguardando usuario'),
        ('waiting_third_party', 'Aguardando terceiro'),
        ('resolved', 'Resolvidos'),
    ]
    kanban_columns = [
        {
            'status': status,
            'label': label,
            'tickets': [ticket for ticket in sorted_tickets if ticket.status == status],
            'count': len([ticket for ticket in sorted_tickets if ticket.status == status]),
        }
        for status, label in status_groups
    ]
    grouped_by_owner = [
        {
            'owner': owner,
            'tickets': [ticket for ticket in sorted_tickets if (ticket.assigned_to or 'Sem responsavel') == owner],
        }
        for owner in sorted(_count_by(sorted_tickets, 'assigned_to').keys())
    ]
    workload_rows = [
        {
            'label': owner,
            'count': count,
            'critical': len([ticket for ticket in open_tickets if (ticket.assigned_to or 'Sem responsavel') == owner and ticket.priority == 'critical']),
        }
        for owner, count in sorted(_count_by(open_tickets, 'assigned_to').items())
    ]
    filter_counts = {
        'status': _count_by(MOCK_TICKETS, 'status'),
        'priority': _count_by(MOCK_TICKETS, 'priority'),
        'assigned_to': _count_by(MOCK_TICKETS, 'assigned_to'),
        'sector': _count_by(MOCK_TICKETS, 'sector'),
        'category': _count_by(MOCK_TICKETS, 'category'),
    }
    filter_options = {
        'status': [
            {'value': value, 'label': label, 'count': filter_counts['status'].get(value, 0)}
            for value, label in STATUS_LABELS.items()
        ],
        'priority': [
            {'value': value, 'label': label, 'count': filter_counts['priority'].get(value, 0)}
            for value, label in PRIORITY_LABELS.items()
        ],
        'assigned_to': [
            {'value': value, 'label': value, 'count': count}
            for value, count in sorted(filter_counts['assigned_to'].items())
        ],
        'sector': [
            {'value': value, 'label': value, 'count': count}
            for value, count in sorted(filter_counts['sector'].items())
        ],
        'category': [
            {'value': value, 'label': value, 'count': count}
            for value, count in sorted(filter_counts['category'].items())
        ],
    }
    context = {
        **_base_context(active_section),
        'tickets': sorted_tickets,
        'summary': summary_for(tickets),
        'global_summary': summary_for(),
        'selected_ticket': selected_ticket,
        'kanban_columns': kanban_columns,
        'grouped_by_owner': grouped_by_owner,
        'workload_rows': workload_rows,
        'filter_counts': filter_counts,
        'filter_options': filter_options,
        'unassigned_count': len([ticket for ticket in MOCK_TICKETS if not ticket.assigned_to]),
        'saved_views': [
            {'name': 'Criticos agora', 'query': '?priority=critical'},
            {'name': 'Sem dono', 'query': '?unassigned=1'},
            {'name': 'Socios / VIP', 'query': '?vip=1'},
        ],
        'view_mode': request.GET.get('view', 'list'),
        'new_ticket_count': 3,
        'page_title': 'Central de Atendimento',
        'page_subtitle': 'Trabalhe a fila, acompanhe o kanban e abra detalhes sem perder o contexto.',
    }
    return context


def ticket_central(request):
    return render(request, 'tickets/central.html', _central_context(request))


def ticket_list(request):
    return render(request, 'tickets/central.html', _central_context(request))


def ticket_queue(request):
    return render(request, 'tickets/central.html', _central_context(request))


def ticket_my(request):
    context = _central_context(request, active_section='my', assigned_to='Gabriel')
    context['page_title'] = 'Meus chamados'
    context['page_subtitle'] = 'Atendimentos atribuidos ao tecnico atual dentro da Central.'
    context['is_my_queue'] = True
    return render(request, 'tickets/central.html', context)


def ticket_create(request):
    if request.method == 'POST':
        messages.success(request, 'Chamado criado com sucesso. (Preview)')
        return redirect('tickets:list')

    context = {
        **_base_context('new'),
        'mock_endpoints': [ticket.endpoint for ticket in MOCK_TICKETS if ticket.endpoint],
    }
    return render(request, 'tickets/form.html', context)


def ticket_detail(request, number):
    ticket = get_ticket(number) or MOCK_TICKETS[0]
    context = {
        **_base_context('queue'),
        'ticket': ticket,
        **build_ticket_detail_context(ticket),
        'related_alerts': [
            {'title': 'Alerta RMM relacionado', 'description': 'Espaco reservado para vinculo futuro com EndpointAlert.'},
            {'title': 'Historico de inventario', 'description': 'Preview visual sem integracao real nesta fase.'},
        ] if ticket.endpoint else [],
    }
    return render(request, 'tickets/detail.html', context)


def _service_panel_context():
    open_tickets = [
        ticket for ticket in MOCK_TICKETS
        if ticket.status not in {'resolved', 'closed', 'canceled'}
    ]
    priority_rank = {'critical': 0, 'high': 1, 'normal': 2, 'low': 3}
    status_groups = [
        ('new', 'Novo'),
        ('in_progress', 'Em atendimento'),
        ('waiting_user', 'Aguardando usuario'),
        ('waiting_third_party', 'Aguardando terceiro'),
        ('resolved', 'Resolvidos hoje'),
    ]
    kanban_columns = [
        {
            'status': status,
            'label': label,
            'tickets': [ticket for ticket in MOCK_TICKETS if ticket.status == status][:5],
            'count': len([ticket for ticket in MOCK_TICKETS if ticket.status == status]),
        }
        for status, label in status_groups
    ]
    attention_tickets = [
        ticket for ticket in open_tickets
        if ticket.priority == 'critical' or ticket.partner or not ticket.assigned_to or ticket.updated_for in {'2h 44min', '2h 40min', '2h 15min', '3h'}
    ][:6]
    operational_tickets = sorted(
        open_tickets,
        key=lambda ticket: (
            priority_rank.get(ticket.priority, 9),
            0 if ticket.partner else 1,
            0 if not ticket.assigned_to else 1,
            ticket.number * -1,
        ),
    )
    return {
        **_base_context('service_panel'),
        'summary': {
            'open': 18,
            'unassigned': 4,
            'critical': 3,
            'waiting_user': 5,
            'in_progress': 9,
            'avg_first_response': '18 min',
        },
        'time_metrics': [
            {'label': 'Primeira resposta media', 'value': '18 min', 'icon': 'timer'},
            {'label': 'Atribuicao media', 'value': '9 min', 'icon': 'user-check'},
            {'label': 'Resolucao media hoje', 'value': '2h14', 'icon': 'check-circle'},
            {'label': 'Mais antigo aberto', 'value': '1d 4h', 'icon': 'clock'},
            {'label': 'Sem atualizacao > 2h', 'value': '3', 'icon': 'alert-circle'},
        ],
        'attention_tickets': attention_tickets,
        'workload_rows': [
            {'label': 'Gabriel', 'count': 7, 'critical': 2, 'avg': '1h12', 'value': 78},
            {'label': 'Renan', 'count': 5, 'critical': 1, 'avg': '48min', 'value': 56},
            {'label': 'Sem responsavel', 'count': 4, 'critical': 2, 'avg': '--', 'value': 44},
        ],
        'kanban_columns': kanban_columns,
        'operational_tickets': operational_tickets,
        'category_rows': [
            {'label': 'Acesso', 'value': 80, 'count': 5},
            {'label': 'Software', 'value': 64, 'count': 4},
            {'label': 'Hardware', 'value': 48, 'count': 3},
            {'label': 'Impressora', 'value': 36, 'count': 2},
            {'label': 'Seguranca', 'value': 36, 'count': 2},
        ],
        'sector_rows': [
            {'label': 'Juridico', 'value': 86, 'count': 6},
            {'label': 'Financeiro', 'value': 64, 'count': 4},
            {'label': 'Diretoria', 'value': 48, 'count': 3},
            {'label': 'Comercial', 'value': 32, 'count': 2},
            {'label': 'TI', 'value': 18, 'count': 1},
        ],
    }


def ticket_dashboard(request):
    context = {
        **_base_context('dashboard'),
        **build_ticket_dashboard_context(request),
    }
    return render(request, 'tickets/dashboard.html', context)


def ticket_service_panel(request):
    context = _central_context(request)
    context['view_mode'] = request.GET.get('view', 'kanban')
    return render(request, 'tickets/central.html', context)


def ticket_categories(request):
    context = {
        **_base_context('categories'),
        'categories': CATEGORIES,
    }
    return render(request, 'tickets/categories.html', context)


def ticket_settings(request):
    context = {
        **_base_context('settings'),
        'settings_cards': [
            {'icon': 'timer', 'title': 'SLA', 'description': 'Metas de resposta e resolucao por prioridade.'},
            {'icon': 'users', 'title': 'Integracao AD', 'description': 'Sincronizacao futura de solicitantes, setores e tecnicos.'},
            {'icon': 'mail', 'title': 'E-mail', 'description': 'Abertura e resposta de chamados por caixa compartilhada.'},
            {'icon': 'alert-triangle', 'title': 'Regras de prioridade', 'description': 'Elevacao automatica para socios, seguranca e indisponibilidade.'},
            {'icon': 'message-square', 'title': 'Canais de abertura', 'description': 'Portal, agente local, e-mail e alertas RMM.'},
            {'icon': 'calendar-clock', 'title': 'Horario de atendimento', 'description': 'Calendario operacional e janelas de plantao.'},
        ],
    }
    return render(request, 'tickets/settings.html', context)


@require_POST
def ticket_fake_action(request, number=None, action='updated'):
    messages.success(request, f'Acao registrada no preview: {action}.')
    if number:
        return redirect('tickets:detail', number=number)
    return redirect('tickets:list')
