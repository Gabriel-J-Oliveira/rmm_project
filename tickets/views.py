from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .mock_data import CATEGORIES, MOCK_TICKETS, PRIORITY_LABELS, STATUS_LABELS, filter_tickets, get_ticket, summary_for
from .services.automation_rules_context import build_automation_rules_context
from .services.category_settings_context import build_category_settings_context
from .services.dashboard_context import build_ticket_dashboard_context
from .services.settings_context import build_ticket_settings_context
from .services.ticket_create_context import build_ticket_create_context
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


def _decorate_central_ticket(ticket):
    is_rmm = ticket.category == 'RMM / Alerta' or 'alerta' in ticket.title.casefold() or 'bitdefender' in ticket.title.casefold()
    is_manual = not is_rmm
    is_waiting = ticket.status in {'waiting_user', 'waiting_third_party'}
    is_unassigned = not ticket.assigned_to
    stale_times = {'2h 44min', '2h 40min', '2h 15min', '3h'}
    is_stale = ticket.updated_for in stale_times
    has_recent_internal = any(comment.visibility == 'Interno' for comment in ticket.comments)
    has_attachment = ticket.number in {1048, 1045, 1042}
    is_merged = ticket.number in {1046}
    sla_state = 'critical' if ticket.priority == 'critical' or is_stale else 'warning' if ticket.priority == 'high' else 'ok'
    origin = 'rmm' if is_rmm else 'manual'
    origin_label = 'RMM Alert' if is_rmm else 'Manual'
    origin_icon = 'activity' if is_rmm else 'user-round'
    alert_label = 'Antivirus desativado' if 'Bitdefender' in ticket.title else 'Disco cheio' if 'disco' in ticket.title.casefold() else 'Alerta RMM critico'
    endpoint_status = ticket.endpoint.status if ticket.endpoint else 'none'
    endpoint_online = endpoint_status == 'online'
    rmm_card = None
    if ticket.endpoint:
        rmm_card = {
            'hostname': ticket.endpoint.hostname,
            'online': endpoint_online,
            'status_label': 'Online' if endpoint_online else 'Offline',
            'last_seen': ticket.endpoint.last_heartbeat,
            'logged_user': ticket.endpoint.last_user,
            'cpu': 38 if endpoint_online else 0,
            'memory': 67 if endpoint_online else 0,
            'disk': 91 if is_rmm else 58,
            'active_alerts': 2 if is_rmm else 0,
            'risk': 'critical' if is_rmm else 'normal',
        }
    suggestion = 'Este chamado esta sem responsavel. Atribua a alguem para iniciar o atendimento.'
    if is_rmm:
        suggestion = 'Chamado veio de alerta RMM critico. Valide o endpoint antes de resolver.'
    elif sla_state == 'critical':
        suggestion = f'SLA em atencao: chamado aberto ha {ticket.opened_for}.'
    elif is_waiting:
        suggestion = 'Chamado pausado aguardando retorno externo. Revise se precisa de follow-up.'
    elif ticket.endpoint:
        suggestion = f'Endpoint {ticket.endpoint.hostname} vinculado. Verifique telemetria antes da proxima acao.'

    ticket.central = {
        'origin': origin,
        'origin_label': origin_label,
        'origin_icon': origin_icon,
        'is_rmm': is_rmm,
        'is_manual': is_manual,
        'is_waiting': is_waiting,
        'is_unassigned': is_unassigned,
        'is_stale': is_stale,
        'has_recent_internal': has_recent_internal,
        'has_attachment': has_attachment,
        'is_merged': is_merged,
        'sla_state': sla_state,
        'sla_label': 'vence agora' if sla_state == 'critical' else 'vence em breve' if sla_state == 'warning' else 'no prazo',
        'alert_label': alert_label,
        'endpoint_status': endpoint_status,
        'rmm': rmm_card,
        'suggestion': suggestion,
        'comments_count': len(ticket.comments),
        'attachments_count': 2 if has_attachment else 0,
        'watchers': ['Gabriel', 'Renan'] if ticket.priority in {'critical', 'high'} else ['Ana'],
    }
    return ticket


def _central_ticket_payload(ticket):
    central = getattr(ticket, 'central', {})
    endpoint = ticket.endpoint
    return {
        'number': ticket.number,
        'title': ticket.title,
        'requester': ticket.requester,
        'sector': ticket.sector,
        'partner': ticket.partner,
        'priority': ticket.priority,
        'priority_label': ticket.priority_label,
        'status': ticket.status,
        'status_label': ticket.status_label,
        'category': ticket.category,
        'assigned_to': ticket.assigned_to or '',
        'opened_for': ticket.opened_for,
        'updated_for': ticket.updated_for,
        'description': ticket.description,
        'origin': central.get('origin'),
        'origin_label': central.get('origin_label'),
        'origin_icon': central.get('origin_icon'),
        'sla_state': central.get('sla_state'),
        'sla_label': central.get('sla_label'),
        'suggestion': central.get('suggestion'),
        'endpoint': {
            'hostname': endpoint.hostname,
            'status': endpoint.status,
            'last_user': endpoint.last_user,
            'last_heartbeat': endpoint.last_heartbeat,
        } if endpoint else None,
        'rmm': central.get('rmm'),
        'comments': [
            {
                'author': comment.author,
                'when': comment.when,
                'visibility': comment.visibility,
                'body': comment.body,
            }
            for comment in ticket.comments[:2]
        ],
    }


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
    selected_number = request.GET.get('ticket')
    selected_ticket = get_ticket(selected_number) if selected_number else None
    sorted_tickets = [_decorate_central_ticket(ticket) for ticket in sorted(tickets, key=_ticket_sort_key)]
    if selected_ticket:
        selected_ticket = _decorate_central_ticket(selected_ticket)
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
        'client': [
            {'value': value, 'label': value, 'count': count}
            for value, count in sorted(filter_counts['sector'].items())
        ],
        'category': [
            {'value': value, 'label': value, 'count': count}
            for value, count in sorted(filter_counts['category'].items())
        ],
    }
    current_summary = summary_for(tickets)
    global_summary = summary_for()
    context = {
        **_base_context(active_section),
        'tickets': sorted_tickets,
        'summary': current_summary,
        'global_summary': global_summary,
        'selected_ticket': selected_ticket,
        'central_ticket_payloads': [_central_ticket_payload(ticket) for ticket in sorted_tickets],
        'kanban_columns': kanban_columns,
        'grouped_by_owner': grouped_by_owner,
        'workload_rows': workload_rows,
        'filter_counts': filter_counts,
        'filter_options': filter_options,
        'unassigned_count': len([ticket for ticket in MOCK_TICKETS if not ticket.assigned_to]),
        'saved_views': [
            {'name': 'Fila geral', 'query': '?view=list', 'active': True},
            {'name': 'Criticos sem dono', 'query': '?priority=critical&unassigned=1', 'active': False},
            {'name': 'Minha fila', 'query': '?assigned_to=Gabriel', 'active': False},
            {'name': 'RMM Alertas', 'query': '?origin=rmm', 'active': False},
            {'name': 'Financeiro', 'query': '?sector=Financeiro', 'active': False},
        ],
        'central_kpis': [
            {'label': 'Abertos', 'value': global_summary['open'], 'url': '?view=list', 'icon': 'inbox', 'trend': '+8%', 'spark': [12, 16, 14, 18, 22, 21]},
            {'label': 'Criticos', 'value': global_summary['critical'], 'url': '?priority=critical', 'icon': 'alert-triangle', 'trend': '+2', 'spark': [4, 5, 5, 6, 8, 7], 'variant': 'critical'},
            {'label': 'Sem dono', 'value': len([ticket for ticket in MOCK_TICKETS if not ticket.assigned_to]), 'url': '?unassigned=1', 'icon': 'user-x', 'trend': '-1', 'spark': [6, 5, 7, 5, 4, 4], 'variant': 'warning'},
            {'label': 'SLA vencendo', 'value': 3, 'url': '?stale=1', 'icon': 'timer', 'trend': '+1', 'spark': [1, 1, 2, 2, 3, 3], 'variant': 'warning'},
            {'label': '1a resposta', 'value': global_summary['avg_first_response'], 'url': '?view=list', 'icon': 'clock-3', 'trend': '-6%', 'spark': [28, 25, 24, 22, 20, 18]},
            {'label': 'Resolucao', 'value': '2h14', 'url': '?status=resolved', 'icon': 'check-circle', 'trend': '-11%', 'spark': [34, 31, 29, 28, 25, 22]},
            {'label': 'Em atendimento', 'value': global_summary['in_progress'], 'url': '?status=in_progress', 'icon': 'radio', 'trend': '+4', 'spark': [4, 6, 7, 8, 9, 9]},
            {'label': 'Alertas RMM', 'value': len([ticket for ticket in MOCK_TICKETS if ticket.category == 'RMM / Alerta' or 'alerta' in ticket.title.casefold() or 'Bitdefender' in ticket.title]), 'url': '?origin=rmm', 'icon': 'activity', 'trend': '+3', 'spark': [1, 2, 2, 4, 4, 5], 'variant': 'rmm'},
        ],
        'origin_options': [
            {'value': 'manual', 'label': 'Manual', 'count': len([ticket for ticket in MOCK_TICKETS if ticket.category != 'RMM / Alerta'])},
            {'value': 'rmm', 'label': 'RMM Alert', 'count': len([ticket for ticket in MOCK_TICKETS if ticket.category == 'RMM / Alerta' or 'alerta' in ticket.title.casefold() or 'Bitdefender' in ticket.title])},
            {'value': 'email', 'label': 'E-mail', 'count': 2},
            {'value': 'phone', 'label': 'Telefone', 'count': 1},
        ],
        'command_actions': [
            {'icon': 'ticket-plus', 'label': 'Novo chamado', 'shortcut': 'Ctrl N'},
            {'icon': 'activity', 'label': 'Abrir RMM Alertas', 'shortcut': 'R M M'},
            {'icon': 'user-check', 'label': 'Ver meus chamados', 'shortcut': 'G M'},
            {'icon': 'bar-chart-3', 'label': 'Abrir dashboard', 'shortcut': 'G D'},
            {'icon': 'columns-3', 'label': 'Alternar Kanban', 'shortcut': 'V K'},
        ],
        'view_mode': request.GET.get('view', 'list'),
        'new_ticket_count': 3,
        'page_title': 'Central de Atendimento',
        'page_subtitle': 'Trabalhe a fila, acompanhe o kanban e abra detalhes sem perder o contexto.',
    }
    return context


def ticket_central(request):
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
        return redirect('tickets:central')

    context = {
        **_base_context('new'),
        **build_ticket_create_context(request),
    }
    return render(request, 'tickets/form.html', context)


def ticket_detail(request, number):
    ticket = get_ticket(number) or MOCK_TICKETS[0]
    detail_context = build_ticket_detail_context(ticket)
    device_context = detail_context.get('device_context') or {}
    sla = detail_context.get('sla') or {}
    related_tickets = detail_context.get('related_tickets') or {}
    related_count = sum(len(items) for items in related_tickets.values())
    context = {
        **_base_context('queue'),
        'ticket': ticket,
        **detail_context,
        'related_count': related_count,
        'ticket_detail_state': {
            'number': ticket.number,
            'title': ticket.title,
            'requester': ticket.requester,
            'sector': ticket.sector,
            'status': ticket.status,
            'statusLabel': ticket.status_label,
            'priority': ticket.priority,
            'priorityLabel': ticket.priority_label,
            'category': ticket.category,
            'assignedTo': ticket.assigned_to or '',
            'hasEndpoint': bool(ticket.endpoint),
            'hasRmmAlert': bool(device_context.get('alerts_count') or device_context.get('active_alerts')),
            'slaLevel': sla.get('level', 'ok'),
            'slaLabel': sla.get('label', ''),
            'resolved': ticket.status == 'resolved',
        },
        'related_alerts': [
            {'title': 'Alerta RMM relacionado', 'description': 'Espaco reservado para vinculo futuro com EndpointAlert.'},
            {'title': 'Historico de inventario', 'description': 'Preview visual sem integracao real nesta fase.'},
        ] if ticket.endpoint else [],
    }
    return render(request, 'tickets/detail.html', context)


def ticket_dashboard(request):
    context = {
        **_base_context('dashboard'),
        **build_ticket_dashboard_context(request),
    }
    return render(request, 'tickets/dashboard.html', context)


def ticket_categories(request):
    context = {
        **_base_context('categories'),
        **build_category_settings_context(),
    }
    return render(request, 'tickets/categories.html', context)


def ticket_automation_rules(request):
    context = {
        **_base_context('automation'),
        **build_automation_rules_context(),
    }
    return render(request, 'tickets/automation_rules.html', context)


def ticket_settings(request):
    context = {
        **_base_context('settings'),
        **build_ticket_settings_context(request),
    }
    return render(request, 'tickets/settings.html', context)


@require_POST
def ticket_fake_action(request, number=None, action='updated'):
    messages.success(request, f'Acao registrada no preview: {action}.')
    if number:
        return redirect('tickets:detail', number=number)
    return redirect('tickets:central')
