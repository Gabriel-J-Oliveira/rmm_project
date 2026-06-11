from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .mock_data import CATEGORIES, MOCK_TICKETS, PRIORITY_LABELS, STATUS_LABELS, filter_tickets, get_ticket, summary_for


def _base_context(active_section='queue'):
    return {
        'active_nav': 'tickets',
        'body_class': 'page-tickets',
        'desk_active_section': active_section,
        'categories': CATEGORIES,
        'status_labels': STATUS_LABELS,
        'priority_labels': PRIORITY_LABELS,
    }


def ticket_list(request):
    tickets = filter_tickets(request.GET)
    context = {
        **_base_context('queue'),
        'tickets': tickets,
        'summary': summary_for(),
        'page_title': 'Night Owl Desk',
        'page_subtitle': 'Fila interna de atendimento da TI',
    }
    return render(request, 'tickets/list.html', context)


def ticket_queue(request):
    return ticket_list(request)


def ticket_my(request):
    tickets = filter_tickets(request.GET, assigned_to='Gabriel')
    context = {
        **_base_context('my'),
        'tickets': tickets,
        'summary': summary_for(tickets),
        'page_title': 'Meus chamados',
        'page_subtitle': 'Atendimentos atribuidos ao tecnico atual.',
        'is_my_queue': True,
    }
    return render(request, 'tickets/list.html', context)


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
        'summary': summary_for(),
        'category_rows': [
            {'label': 'Acesso', 'value': 80, 'count': 5},
            {'label': 'Software', 'value': 64, 'count': 4},
            {'label': 'Hardware', 'value': 48, 'count': 3},
            {'label': 'Impressora', 'value': 36, 'count': 2},
            {'label': 'Seguranca', 'value': 36, 'count': 2},
        ],
        'tech_rows': [
            {'label': 'Gabriel', 'value': 78, 'count': 7},
            {'label': 'Renan', 'value': 56, 'count': 5},
            {'label': 'Sem responsavel', 'value': 44, 'count': 4},
        ],
        'status_rows': [
            {'label': 'Novo', 'value': 55, 'count': 5},
            {'label': 'Em atendimento', 'value': 80, 'count': 9},
            {'label': 'Aguardando usuario', 'value': 48, 'count': 5},
            {'label': 'Aguardando terceiro', 'value': 24, 'count': 2},
            {'label': 'Resolvido hoje', 'value': 36, 'count': 3},
        ],
        'sector_rows': [
            {'label': 'Juridico', 'value': 86, 'count': 6},
            {'label': 'Financeiro', 'value': 64, 'count': 4},
            {'label': 'Diretoria', 'value': 48, 'count': 3},
            {'label': 'Comercial', 'value': 32, 'count': 2},
            {'label': 'TI', 'value': 18, 'count': 1},
        ],
        'critical_tickets': [ticket for ticket in MOCK_TICKETS if ticket.priority == 'critical'][:5],
        'partner_tickets': [ticket for ticket in MOCK_TICKETS if ticket.partner],
    }
    return render(request, 'tickets/dashboard.html', context)


def ticket_service_panel(request):
    return render(request, 'tickets/service_panel.html', _service_panel_context())


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
