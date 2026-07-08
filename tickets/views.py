import json
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import OperationalError, ProgrammingError, transaction
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from config.authz import is_nightowl_technical_user

from .mock_data import CATEGORIES, MOCK_TICKETS, PRIORITY_LABELS, STATUS_LABELS, filter_tickets, get_ticket, summary_for
from .models import DeskQueue, DeskSLA, DeskTemplate, Ticket, TicketAttachment, TicketCategory, TicketComment
from .services.automation_rules_context import build_automation_rules_context
from .services.category_settings_context import build_category_settings_context
from .services.dashboard_context import build_ticket_dashboard_context
from .services.desk_mvp1 import (
    CURRENT_TECHNICIAN,
    active_category_context,
    adapt_ticket,
    create_audit_event,
    display_time,
    filtered_ticket_views,
    get_ticket_view,
    ticket_queryset,
    ticket_summary,
)
from .services.desk_mvp2 import (
    build_settings_context,
    composer_template_context,
    normalize_category_color,
    normalize_category_icon,
)
from .services.ticket_create_context import build_ticket_create_context
from .services.ticket_detail_context import build_ticket_detail_context
from .services.desk_templates import templates_for_ticket
from .services.automation_outbox import prepare_ticket_notification
from .services.ticket_conversation import build_public_conversation, create_public_reply
from .services.ticket_workflow import (
    WorkflowError,
    add_ticket_comment,
    assign_ticket,
    can_comment_public,
    requester_reopen,
    requester_reply,
    transition_ticket,
)


def _base_context(active_section='queue'):
    try:
        categories = active_category_context() or CATEGORIES
        desk_queues = DeskQueue.objects.filter(is_active=True, receives_tickets=True).order_by('name')
        desk_slas = DeskSLA.objects.filter(is_active=True).order_by('resolution_minutes', 'name')
    except (OperationalError, ProgrammingError):
        categories = CATEGORIES
        desk_queues = []
        desk_slas = []
    return {
        'active_nav': 'tickets',
        'body_class': 'page-tickets',
        'desk_active_section': active_section,
        'categories': categories,
        'desk_queue_options': desk_queues,
        'desk_sla_options': desk_slas,
        'status_labels': STATUS_LABELS,
        'priority_labels': PRIORITY_LABELS,
    }


def _open_tickets():
    try:
        return [
            adapt_ticket(ticket)
            for ticket in ticket_queryset().filter(status__in=['new', 'in_progress', 'waiting_user', 'waiting_third_party'])
        ]
    except (OperationalError, ProgrammingError):
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
    attachments_count = int(getattr(ticket, 'attachments_count', 0) or 0)
    has_attachment = attachments_count > 0
    is_merged = ticket.number in {1046}
    record = getattr(ticket, 'record', None)
    is_reopened = bool(record and record.audit_events.filter(event_type='ticket_reopened').exists())
    sla_state = 'critical' if ticket.priority == 'critical' or is_stale else 'warning' if ticket.priority == 'high' else 'ok'
    origin = 'rmm' if is_rmm else 'manual'
    origin_label = 'RMM Alert' if is_rmm else 'Manual'
    origin_icon = 'activity' if is_rmm else 'user-round'
    alert_label = 'Antivirus desativado' if 'Bitdefender' in ticket.title else 'Disco cheio' if 'disco' in ticket.title.casefold() else 'Alerta RMM critico'
    endpoint_status = ticket.endpoint.status if ticket.endpoint else 'none'
    endpoint_online = endpoint_status == 'online'
    rmm_card = None
    due_label = ''
    if getattr(ticket, 'due_at', None):
        due_label = timezone.localtime(ticket.due_at).strftime('%d/%m %H:%M')
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
        'is_reopened': is_reopened,
        'sla_state': sla_state,
        'sla_label': 'vence agora' if sla_state == 'critical' else 'vence em breve' if sla_state == 'warning' else 'no prazo',
        'sla_due_label': due_label,
        'alert_label': alert_label,
        'endpoint_status': endpoint_status,
        'rmm': rmm_card,
        'suggestion': suggestion,
        'comments_count': len(ticket.comments),
        'attachments_count': attachments_count,
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
        'category_icon': getattr(ticket, 'category_icon', 'bi-folder'),
        'category_color': getattr(ticket, 'category_color', 'gray'),
        'queue': getattr(ticket, 'queue', ''),
        'sla': getattr(ticket, 'sla', ''),
        'due_at': ticket.record.due_at.isoformat() if getattr(ticket, 'record', None) and ticket.record.due_at else '',
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
        'is_reopened': central.get('is_reopened', False),
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
        'attachments_count': central.get('attachments_count', getattr(ticket, 'attachments_count', 0)),
        'attachments': [
            {
                'id': attachment.id,
                'name': attachment.name,
                'size': attachment.size,
                'visibility': attachment.visibility,
                'when': attachment.when,
            }
            for attachment in getattr(ticket, 'attachments', [])[:4]
        ],
    }


def _count_by(items, attr_name):
    counts = {}
    for item in items:
        value = getattr(item, attr_name) or 'Sem responsavel'
        counts[value] = counts.get(value, 0) + 1
    return counts


def _request_user_display(request):
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        return user.get_full_name() or user.get_username()
    return CURRENT_TECHNICIAN


def _request_user_aliases(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return [CURRENT_TECHNICIAN]
    aliases = [
        user.get_username(),
        user.get_full_name(),
        getattr(user, 'email', ''),
        getattr(user, 'first_name', ''),
    ]
    cleaned = []
    for alias in aliases:
        alias = str(alias or '').strip()
        if alias and alias.casefold() not in {item.casefold() for item in cleaned}:
            cleaned.append(alias)
    return cleaned or [CURRENT_TECHNICIAN]


def _is_technical_request(request):
    return is_nightowl_technical_user(getattr(request, 'user', None))


def _require_technical_access(request):
    if not _is_technical_request(request):
        raise PermissionDenied('Acesso restrito a equipe tecnica.')


def _attachment_allowed_for_request(request, attachment):
    if _is_technical_request(request):
        return True
    user = getattr(request, 'user', None)
    requester_email = str(getattr(user, 'email', '') or '').strip().casefold()
    ticket_email = str(getattr(attachment.ticket, 'requester_email', '') or '').strip().casefold()
    return (
        bool(requester_email and ticket_email and requester_email == ticket_email)
        and attachment.visibility == TicketAttachment.VISIBILITY_PUBLIC
    )


def _central_url_with_view(request, view):
    params = request.GET.copy()
    params['view'] = view
    query = params.urlencode()
    return f'?{query}' if query else f'?view={view}'


def _central_context(request, active_section='central', assigned_to=None):
    using_persisted_data = True
    current_user_name = _request_user_display(request)
    current_user_aliases = _request_user_aliases(request)
    view_mode = request.GET.get('view', 'list')
    if view_mode not in {'list', 'kanban'}:
        view_mode = 'list'
    try:
        tickets = filtered_ticket_views(
            request.GET,
            assigned_to=assigned_to,
            current_user_aliases=current_user_aliases,
        )
        all_tickets = filtered_ticket_views({'include_all_statuses': '1'}, current_user_aliases=current_user_aliases)
    except (OperationalError, ProgrammingError):
        using_persisted_data = False
        mock_assignee = assigned_to[0] if isinstance(assigned_to, (list, tuple, set)) and assigned_to else assigned_to
        tickets = filter_tickets(request.GET, assigned_to=mock_assignee)
        all_tickets = list(MOCK_TICKETS)
    open_tickets = _open_tickets()
    selected_number = request.GET.get('ticket')
    selected_ticket = (get_ticket_view(selected_number) if using_persisted_data else get_ticket(selected_number)) if selected_number else None
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
    workload_rows = [
        {
            'label': owner,
            'count': count,
            'critical': len([ticket for ticket in open_tickets if (ticket.assigned_to or 'Sem responsavel') == owner and ticket.priority == 'critical']),
        }
        for owner, count in sorted(_count_by(open_tickets, 'assigned_to').items())
    ]
    filter_counts = {
        'status': _count_by(all_tickets, 'status'),
        'priority': _count_by(all_tickets, 'priority'),
        'assigned_to': _count_by(all_tickets, 'assigned_to'),
        'sector': _count_by(all_tickets, 'sector'),
        'category': _count_by(all_tickets, 'category'),
        'queue': _count_by(all_tickets, 'queue'),
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
        'queue': [
            {'value': value, 'label': value, 'count': count}
            for value, count in sorted(filter_counts['queue'].items())
        ],
    }
    current_summary = ticket_summary(tickets) if using_persisted_data else summary_for(tickets)
    global_summary = ticket_summary(all_tickets) if using_persisted_data else summary_for(all_tickets)
    unassigned_count = len([ticket for ticket in all_tickets if not ticket.assigned_to])
    rmm_count = len([
        ticket for ticket in all_tickets
        if getattr(ticket, 'source', '') in {Ticket.SOURCE_RMM_ALERT, Ticket.SOURCE_MONITORING}
        or ticket.category == 'RMM / Alerta'
    ])
    context = {
        **_base_context(active_section),
        'tickets': sorted_tickets,
        'summary': current_summary,
        'global_summary': global_summary,
        'selected_ticket': selected_ticket,
        'central_ticket_payloads': [_central_ticket_payload(ticket) for ticket in sorted_tickets],
        'kanban_columns': kanban_columns,
        'workload_rows': workload_rows,
        'filter_counts': filter_counts,
        'filter_options': filter_options,
        'unassigned_count': unassigned_count,
        'saved_views': [
            {'name': 'Fila geral', 'query': '?view=list', 'active': True},
            {'name': 'Criticos sem dono', 'query': '?priority=critical&unassigned=1', 'active': False},
            {'name': 'Atribuídos a mim', 'query': '?assigned_to=__me__', 'active': False},
            {'name': 'RMM Alertas', 'query': '?origin=rmm', 'active': False},
            {'name': 'Financeiro', 'query': '?sector=Financeiro', 'active': False},
        ],
        'central_kpis': [
            {'label': 'Abertos', 'value': global_summary['open'], 'url': '?view=list', 'icon': 'inbox', 'trend': '+8%', 'spark': [12, 16, 14, 18, 22, 21]},
            {'label': 'Criticos', 'value': global_summary['critical'], 'url': '?priority=critical', 'icon': 'alert-triangle', 'trend': '+2', 'spark': [4, 5, 5, 6, 8, 7], 'variant': 'critical'},
            {'label': 'Sem dono', 'value': unassigned_count, 'url': '?unassigned=1', 'icon': 'user-x', 'trend': '-1', 'spark': [6, 5, 7, 5, 4, 4], 'variant': 'warning'},
            {'label': 'SLA vencendo', 'value': 3, 'url': '?stale=1', 'icon': 'timer', 'trend': '+1', 'spark': [1, 1, 2, 2, 3, 3], 'variant': 'warning'},
            {'label': '1a resposta', 'value': global_summary['avg_first_response'], 'url': '?view=list', 'icon': 'clock-3', 'trend': '-6%', 'spark': [28, 25, 24, 22, 20, 18]},
            {'label': 'Resolvidos hoje', 'value': global_summary['resolved_today'], 'url': '?status=resolved', 'icon': 'check-circle', 'trend': 'real', 'spark': [1, 1, 2, 2, 3, global_summary['resolved_today']]},
            {'label': 'Em atendimento', 'value': global_summary['in_progress'], 'url': '?status=in_progress', 'icon': 'radio', 'trend': '+4', 'spark': [4, 6, 7, 8, 9, 9]},
            {'label': 'Alertas RMM', 'value': rmm_count, 'url': '?origin=rmm', 'icon': 'activity', 'trend': '+3', 'spark': [1, 2, 2, 4, 4, 5], 'variant': 'rmm'},
        ],
        'origin_options': [
            {'value': 'manual', 'label': 'Manual', 'count': len(all_tickets) - rmm_count},
            {'value': 'rmm', 'label': 'RMM Alert', 'count': rmm_count},
            {'value': 'email', 'label': 'E-mail', 'count': 2},
            {'value': 'phone', 'label': 'Telefone', 'count': 1},
        ],
        'command_actions': [
            {'icon': 'ticket-plus', 'label': 'Novo chamado', 'shortcut': 'Ctrl N'},
            {'icon': 'activity', 'label': 'Abrir RMM Alertas', 'shortcut': 'R M M'},
            {'icon': 'user-check', 'label': 'Atribuídos a mim', 'shortcut': 'G M'},
            {'icon': 'bar-chart-3', 'label': 'Abrir dashboard', 'shortcut': 'G D'},
            {'icon': 'columns-3', 'label': 'Alternar Kanban', 'shortcut': 'V K'},
        ],
        'view_mode': view_mode,
        'list_view_url': _central_url_with_view(request, 'list'),
        'kanban_view_url': _central_url_with_view(request, 'kanban'),
        'refresh_url': _central_url_with_view(request, view_mode),
        'new_ticket_count': current_summary['new'],
        'using_persisted_tickets': using_persisted_data,
        'current_user_name': current_user_name,
        'current_user_aliases': current_user_aliases,
        'page_title': 'Central de Atendimento',
        'page_subtitle': 'Trabalhe a fila, acompanhe o kanban e abra detalhes sem perder o contexto.',
    }
    return context


def ticket_central(request):
    _require_technical_access(request)
    return render(request, 'tickets/central.html', _central_context(request))


def ticket_my(request):
    _require_technical_access(request)
    context = _central_context(request, active_section='my', assigned_to=_request_user_aliases(request))
    context['page_title'] = 'Meus chamados'
    context['page_subtitle'] = 'Atendimentos atribuidos ao usuario atual dentro da Central.'
    context['is_my_queue'] = True
    return render(request, 'tickets/central.html', context)


def ticket_create(request):
    _require_technical_access(request)
    if request.method == 'POST':
        messages.success(request, 'Chamado criado com sucesso. (Preview)')
        return redirect('tickets:central')

    context = {
        **_base_context('new'),
        **build_ticket_create_context(request),
        'current_user_name': _request_user_display(request),
        'current_user_aliases': _request_user_aliases(request),
    }
    return render(request, 'tickets/form.html', context)


def ticket_detail(request, number):
    _require_technical_access(request)
    try:
        ticket = get_ticket_view(number)
    except (OperationalError, ProgrammingError):
        ticket = None
    ticket = ticket or get_ticket(number)
    if not ticket:
        raise Http404('Chamado nao encontrado.')
    detail_context = build_ticket_detail_context(ticket)
    device_context = detail_context.get('device_context') or {}
    sla = detail_context.get('sla') or {}
    related_count = len(detail_context.get('related_items') or [])
    audit_events = []
    if getattr(ticket, 'record', None):
        audit_events = [
            {
                'id': str(event.pk),
                'actor': event.actor or 'Sistema',
                'event_type': event.event_type,
                'filter_type': {
                    'ticket_created': 'field',
                    'field_changed': 'field',
                    'comment_created': 'comment',
                }.get(event.event_type, event.event_type),
                'action': event.action,
                'field_name': event.field_name,
                'old_value': event.old_value or '—',
                'new_value': event.new_value or '—',
                'metadata': event.metadata or {},
                'metadata_json': json.dumps(event.metadata or {}, ensure_ascii=False, indent=2),
                'severity': (event.metadata or {}).get('severity', 'info'),
                'origin': (event.metadata or {}).get('origin', 'Web'),
                'when': display_time(event.created_at),
            }
            for event in ticket.record.audit_events.all()
        ]
    context = {
        **_base_context('queue'),
        'ticket': ticket,
        **detail_context,
        'audit_events': audit_events,
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
            'categoryIcon': getattr(ticket, 'category_icon', 'bi-folder'),
            'categoryColor': getattr(ticket, 'category_color', 'gray'),
            'queue': getattr(ticket, 'queue', 'N1 - Atendimento'),
            'assignedTo': ticket.assigned_to or '',
            'hasEndpoint': bool(ticket.endpoint),
            'hasRmmAlert': bool(device_context.get('alerts_count') or device_context.get('active_alerts')),
            'slaLevel': sla.get('level', 'ok'),
            'slaLabel': sla.get('label', ''),
            'slaName': getattr(ticket, 'sla', '') or 'Sem SLA',
            'dueAt': ticket.record.due_at.isoformat() if getattr(ticket, 'record', None) and ticket.record.due_at else '',
            'resolved': ticket.status == 'resolved',
        },
        'related_alerts': [
            {'title': 'Alerta RMM relacionado', 'description': 'Espaco reservado para vinculo futuro com EndpointAlert.'},
            {'title': 'Historico de inventario', 'description': 'Preview visual sem integracao real nesta fase.'},
        ] if ticket.endpoint else [],
        'composer_templates': composer_template_context(ticket, user=request.user),
        'resolution_templates': templates_for_ticket(
            ticket.record,
            [DeskTemplate.APP_RESOLVE_TICKET],
            user=request.user,
        ) if getattr(ticket, 'record', None) else [],
        'escalation_templates': templates_for_ticket(
            ticket.record,
            [DeskTemplate.APP_ESCALATE_TICKET],
            user=request.user,
        ) if getattr(ticket, 'record', None) else [],
        'public_conversation': build_public_conversation(ticket.record if getattr(ticket, 'record', None) else None),
        'current_user_name': _request_user_display(request),
        'current_user_aliases': _request_user_aliases(request),
    }
    return render(request, 'tickets/detail.html', context)


def _request_actor(request):
    return _request_user_display(request)


def _audit_config_event(request, event_type, title, metadata=None):
    try:
        from agents.audit import create_audit_event as create_global_audit_event

        create_global_audit_event(
            event_type=event_type,
            title=title,
            severity='info',
            actor_type='system',
            actor_name=_request_actor(request),
            metadata=metadata or {},
            request=request,
        )
    except Exception:
        pass


def _json_payload(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _source_value(value):
    normalized = (value or '').strip().casefold()
    return {
        'manual': Ticket.SOURCE_MANUAL,
        'portal': Ticket.SOURCE_PORTAL,
        'e-mail': Ticket.SOURCE_EMAIL,
        'email': Ticket.SOURCE_EMAIL,
        'telefone': Ticket.SOURCE_PHONE,
        'rmm alert': Ticket.SOURCE_RMM_ALERT,
        'rmm': Ticket.SOURCE_RMM_ALERT,
        'monitoramento': Ticket.SOURCE_MONITORING,
    }.get(normalized, Ticket.SOURCE_MANUAL)


def _api_ticket_payload(ticket):
    view = _decorate_central_ticket(adapt_ticket(ticket))
    payload = _central_ticket_payload(view)
    payload['detail_url'] = f'/tickets/{ticket.number}/'
    return payload


def _api_ticket_state(ticket):
    return {
        'status': ticket.status,
        'statusLabel': ticket.get_status_display(),
        'priority': ticket.priority,
        'priorityLabel': ticket.get_priority_display(),
        'category': ticket.category.name if ticket.category else 'Sem categoria',
        'categoryIcon': normalize_category_icon(ticket.category.icon) if ticket.category else 'bi-folder',
        'categoryColor': normalize_category_color(ticket.category.color) if ticket.category else 'gray',
        'queue': ticket.queue,
        'sla': ticket.sla.name if ticket.sla else '',
        'slaLabel': ticket.sla.name if ticket.sla else 'Sem SLA',
        'slaLevel': 'critical' if ticket.priority == Ticket.PRIORITY_CRITICAL else 'warning' if ticket.priority == Ticket.PRIORITY_HIGH else 'ok',
        'dueAt': ticket.due_at.isoformat() if ticket.due_at else '',
        'assignedTo': ticket.assigned_to,
        'title': ticket.title,
    }


@require_POST
def ticket_api_create(request):
    _require_technical_access(request)
    payload = _json_payload(request)
    if payload is None:
        return JsonResponse({'ok': False, 'errors': {'request': 'JSON invalido.'}}, status=400)

    required = {
        'requester': 'Solicitante',
        'title': 'Titulo',
        'description': 'Descricao',
        'category': 'Categoria',
        'priority': 'Prioridade',
    }
    errors = {
        field: f'{label} e obrigatorio.'
        for field, label in required.items()
        if not str(payload.get(field) or '').strip()
    }
    priority = payload.get('priority')
    if priority not in dict(Ticket.PRIORITY_CHOICES):
        errors['priority'] = 'Prioridade invalida.'
    if priority == Ticket.PRIORITY_CRITICAL and not str(payload.get('critical_reason') or '').strip():
        errors['critical_reason'] = 'Justificativa da prioridade critica e obrigatoria.'
    if errors:
        return JsonResponse({'ok': False, 'errors': errors}, status=400)

    category, _ = TicketCategory.objects.select_related('default_queue', 'default_sla').get_or_create(
        name=str(payload['category']).strip(),
        defaults={'description': 'Categoria criada pelo Backend MVP 1.', 'is_active': True},
    )
    sla = DeskSLA.objects.filter(name=str(payload.get('sla_name') or '').strip()).first() or category.default_sla
    if not sla:
        sla = DeskSLA.objects.filter(is_active=True, priority=priority).order_by('resolution_minutes').first()
    queue = str(payload.get('queue') or '').strip()
    if not queue:
        queue = category.default_queue.name if category.default_queue else 'N1 - Atendimento'
    mode = payload.get('mode') or 'create'
    actor = _request_actor(request)
    with transaction.atomic():
        ticket = Ticket.objects.create(
            title=str(payload['title']).strip(),
            description=str(payload['description']).strip(),
            requester_name=str(payload['requester']).strip(),
            requester_email=str(payload.get('requester_email') or '').strip(),
            requester_department=str(payload.get('requester_department') or 'Triagem').strip(),
            requester_is_partner=bool(payload.get('requester_is_partner')),
            status=Ticket.STATUS_NEW,
            priority=priority,
            category=category,
            queue=queue,
            sla=sla,
            assigned_to='',
            source=_source_value(payload.get('origin')),
            endpoint_name=str(payload.get('endpoint_name') or '').strip(),
        )
        create_audit_event(
            ticket,
            actor=actor,
            event_type='ticket_created',
            action='Criou chamado',
            field_name='ticket',
            new_value=f'#{ticket.number}',
            metadata={
                'source': 'quick_ticket_drawer',
                'mode': mode,
                'critical_reason': payload.get('critical_reason') or '',
                'related_ticket_ids': payload.get('related_ticket_ids') or [],
                'queue': queue,
                'sla': sla.name if sla else '',
                'origin': 'Web',
            },
        )
        prepare_ticket_notification(ticket, 'ticket_created', user=actor)
        if mode == 'assign':
            assign_ticket(ticket, actor=actor, assignee=actor, source='Web')
    return JsonResponse({'ok': True, 'ticket': _api_ticket_payload(ticket)}, status=201)


@require_POST
def ticket_api_update(request, number):
    _require_technical_access(request)
    payload = _json_payload(request)
    if payload is None:
        return JsonResponse({'ok': False, 'error': 'JSON invalido.'}, status=400)
    ticket = get_object_or_404(Ticket.objects.select_related('category', 'sla'), number=number)
    actor = _request_actor(request)
    field = payload.get('field')
    value = payload.get('value')
    previous_status = ticket.status
    previous_assigned_to = ticket.assigned_to
    allowed = {'status', 'priority', 'category', 'queue', 'sla', 'assigned_to', 'title'}
    if field not in allowed:
        return JsonResponse({'ok': False, 'error': 'Campo nao permitido.'}, status=400)

    if field == 'status':
        reason = str(payload.get('reason') or payload.get('public_message') or '').strip()
        try:
            transition_ticket(
                ticket,
                value,
                actor=actor,
                reason=reason,
                public_message=str(payload.get('public_message') or '').strip(),
                source='Central Tecnica',
            )
        except WorkflowError as exc:
            return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
        return JsonResponse({
            'ok': True,
            'field': field,
            'value': ticket.status,
            'display': ticket.get_status_display(),
            'ticket': _api_ticket_state(ticket),
        })

    if field == 'assigned_to' and str(value or '').strip():
        try:
            assign_ticket(ticket, actor=actor, assignee=str(value or '').strip(), source='Central Tecnica')
        except WorkflowError as exc:
            return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
        return JsonResponse({
            'ok': True,
            'field': field,
            'value': ticket.assigned_to,
            'display': ticket.assigned_to,
            'ticket': _api_ticket_state(ticket),
        })

    old_display = ''
    new_display = ''
    if field == 'priority':
        if value not in dict(Ticket.PRIORITY_CHOICES):
            return JsonResponse({'ok': False, 'error': 'Prioridade invalida.'}, status=400)
        old_display = ticket.get_priority_display()
        ticket.priority = value
        new_display = dict(Ticket.PRIORITY_CHOICES)[value]
    elif field == 'category':
        old_display = ticket.category.name if ticket.category else 'Sem categoria'
        category, _ = TicketCategory.objects.select_related('default_queue', 'default_sla').get_or_create(name=str(value).strip(), defaults={'is_active': True})
        ticket.category = category
        if category.default_priority:
            ticket.priority = category.default_priority
        if category.default_queue:
            ticket.queue = category.default_queue.name
        if category.default_sla:
            ticket.sla = category.default_sla
            ticket.due_at = ticket.created_at + timezone.timedelta(minutes=category.default_sla.resolution_minutes)
        else:
            ticket.sla = None
            ticket.due_at = None
        new_display = category.name
    elif field == 'queue':
        old_display = ticket.queue or 'Sem fila'
        queue_name = str(value or '').strip()
        if queue_name and not DeskQueue.objects.filter(name=queue_name, is_active=True).exists():
            return JsonResponse({'ok': False, 'error': 'Fila invalida ou inativa.'}, status=400)
        ticket.queue = queue_name
        new_display = ticket.queue or 'Sem fila'
    elif field == 'sla':
        old_display = ticket.sla.name if ticket.sla else 'Sem SLA'
        sla_name = str(value or '').strip()
        sla = DeskSLA.objects.filter(name=sla_name, is_active=True).first() if sla_name else None
        if sla_name and not sla:
            return JsonResponse({'ok': False, 'error': 'SLA invalido ou inativo.'}, status=400)
        ticket.sla = sla
        ticket.due_at = ticket.created_at + timezone.timedelta(minutes=sla.resolution_minutes) if sla else None
        new_display = sla.name if sla else 'Sem SLA'
    elif field == 'assigned_to':
        old_display = ticket.assigned_to or 'Sem responsavel'
        ticket.assigned_to = str(value or '').strip()
        new_display = ticket.assigned_to or 'Sem responsavel'
    else:
        old_display = ticket.title
        ticket.title = str(value or '').strip()
        if not ticket.title:
            return JsonResponse({'ok': False, 'error': 'Titulo e obrigatorio.'}, status=400)
        new_display = ticket.title

    ticket.save()
    create_audit_event(
        ticket,
        actor=actor,
        event_type='field_changed',
        action=f'Alterou {field}',
        field_name=field,
        old_value=old_display,
        new_value=new_display,
        metadata={
            'origin': 'Web',
            'severity': 'sensitive' if field == 'priority' and value == Ticket.PRIORITY_CRITICAL else 'info',
        },
    )
    return JsonResponse({
        'ok': True,
        'field': field,
        'value': value,
        'display': new_display,
        'ticket': _api_ticket_state(ticket),
    })


@require_POST
def ticket_api_comment(request, number):
    _require_technical_access(request)
    payload = _json_payload(request)
    if payload is None:
        return JsonResponse({'ok': False, 'error': 'JSON invalido.'}, status=400)
    body = str(payload.get('body') or '').strip()
    visibility = payload.get('visibility') or TicketComment.VISIBILITY_INTERNAL
    if not body:
        return JsonResponse({'ok': False, 'error': 'Comentario e obrigatorio.'}, status=400)
    if visibility not in dict(TicketComment.VISIBILITY_CHOICES):
        return JsonResponse({'ok': False, 'error': 'Visibilidade invalida.'}, status=400)

    ticket = get_object_or_404(Ticket, number=number)
    actor = _request_actor(request)
    if visibility == TicketComment.VISIBILITY_PUBLIC and not can_comment_public(ticket):
        return JsonResponse({'ok': False, 'error': 'Este chamado nao aceita comentarios publicos neste status.'}, status=400)
    selected_template = None
    template_id = str(payload.get('template_id') or '').strip()
    if template_id:
        selected_template = DeskTemplate.objects.filter(
            pk=template_id,
            is_active=True,
            application__in=[
                DeskTemplate.APP_COMPOSER_PUBLIC,
                DeskTemplate.APP_COMPOSER_INTERNAL,
            ],
        ).first()
    with transaction.atomic():
        comment = add_ticket_comment(
            ticket,
            actor=actor,
            body=body,
            visibility=visibility,
            source='Central Tecnica',
        )
        if visibility == TicketComment.VISIBILITY_PUBLIC:
            prepare_ticket_notification(
                ticket,
                'ticket_public_reply',
                user=actor,
                extra_context={
                    'mensagem': body,
                    'public_comment_id': str(comment.pk),
                },
            )
        if selected_template:
            create_audit_event(
                ticket,
                actor=actor,
                event_type='template_used',
                action=f'Usou template {selected_template.name}',
                field_name='template',
                new_value=selected_template.name,
                metadata={
                    'origin': 'Web',
                    'application': selected_template.application,
                    'template_id': str(selected_template.pk),
                },
            )
    return JsonResponse({
        'ok': True,
        'comment': {
            'id': str(comment.pk),
            'author': actor,
            'body': comment.body,
            'visibility': visibility,
            'visibilityLabel': comment.get_visibility_display(),
            'when': 'agora',
        },
    }, status=201)


@require_POST
def ticket_api_public_conversation(request, number):
    _require_technical_access(request)
    ticket = get_object_or_404(Ticket.objects.select_related('category', 'endpoint', 'sla'), number=number)
    actor = _request_actor(request)
    body = str(request.POST.get('body') or '').strip()
    if not body:
        return JsonResponse({'ok': False, 'error': 'Mensagem e obrigatoria.'}, status=400)

    try:
        with transaction.atomic():
            result = create_public_reply(
                ticket,
                actor=actor,
                body=body,
                files=request.FILES.getlist('attachments'),
                source='Conversa publica',
            )
    except WorkflowError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    return JsonResponse({
        'ok': True,
        'message': result['message'],
        'notificationStatus': result['notification_status'],
        'ticket': _api_ticket_state(ticket),
    }, status=201)


@require_POST
def ticket_api_attachment(request, number):
    _require_technical_access(request)
    ticket = get_object_or_404(Ticket.objects.select_related('category', 'endpoint', 'sla'), number=number)
    visibility = request.POST.get('visibility') or TicketAttachment.VISIBILITY_INTERNAL
    if visibility not in dict(TicketAttachment.VISIBILITY_CHOICES):
        return JsonResponse({'ok': False, 'error': 'Visibilidade invalida.'}, status=400)
    files = request.FILES.getlist('attachments')
    if not files:
        return JsonResponse({'ok': False, 'error': 'Nenhum arquivo enviado.'}, status=400)

    actor = _request_actor(request)
    created = []
    with transaction.atomic():
        for uploaded in files:
            attachment = TicketAttachment.objects.create(
                ticket=ticket,
                file=uploaded,
                original_name=uploaded.name,
                content_type=getattr(uploaded, 'content_type', '') or '',
                size=getattr(uploaded, 'size', 0) or 0,
                uploaded_by=actor,
                visibility=visibility,
            )
            created.append(attachment)
        create_audit_event(
            ticket,
            actor=actor,
            event_type='attachment_created',
            action='Anexou arquivo ao chamado',
            field_name='attachments',
            new_value=', '.join(item.original_name for item in created),
            metadata={
                'origin': 'Central Tecnica',
                'visibility': visibility,
                'count': len(created),
            },
        )

    return JsonResponse({
        'ok': True,
        'attachments': [
            {
                'id': str(item.pk),
                'name': item.original_name,
                'size': item.size,
                'visibility': item.get_visibility_display(),
                'when': 'agora',
            }
            for item in created
        ],
        'attachmentsCount': ticket.attachments.count(),
    }, status=201)


@require_POST
def ticket_api_action(request, number):
    _require_technical_access(request)
    payload = _json_payload(request)
    if payload is None:
        return JsonResponse({'ok': False, 'error': 'JSON invalido.'}, status=400)

    ticket = get_object_or_404(Ticket.objects.select_related('category', 'endpoint', 'sla'), number=number)
    actor = _request_actor(request)
    action = str(payload.get('action') or '').strip()
    template_id = str(payload.get('template_id') or '').strip()
    expected_application = {
        'resolve': DeskTemplate.APP_RESOLVE_TICKET,
        'escalate': DeskTemplate.APP_ESCALATE_TICKET,
    }.get(action)
    if not expected_application:
        return JsonResponse({'ok': False, 'error': 'Acao nao suportada.'}, status=400)

    selected_template = None
    if template_id:
        selected_template = DeskTemplate.objects.filter(
            pk=template_id,
            is_active=True,
            application=expected_application,
        ).first()
        if not selected_template:
            return JsonResponse({'ok': False, 'error': 'Template invalido ou inativo.'}, status=400)

    with transaction.atomic():
        if action == 'resolve':
            summary = str(payload.get('summary') or '').strip()
            public_comment = str(payload.get('public_comment') or '').strip()
            internal_comment = str(payload.get('internal_comment') or '').strip()
            if not summary:
                return JsonResponse({'ok': False, 'error': 'Resumo da solucao e obrigatorio.'}, status=400)
            try:
                transition_ticket(
                    ticket,
                    Ticket.STATUS_RESOLVED,
                    actor=actor,
                    reason=summary,
                    public_message=public_comment or f'Chamado resolvido.\n\nSolucao: {summary}',
                    source='Central Tecnica',
                    extra_context={'solucao': summary},
                )
            except WorkflowError as exc:
                return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
            if internal_comment:
                add_ticket_comment(
                    ticket=ticket,
                    actor=actor,
                    body=internal_comment,
                    visibility=TicketComment.VISIBILITY_INTERNAL,
                    source='Central Tecnica',
                )
            if selected_template:
                create_audit_event(
                    ticket,
                    actor=actor,
                    event_type='template_used',
                    action=f'Notificacao de resolucao preparada a partir do template {selected_template.name}.',
                    field_name='template',
                    new_value=selected_template.name,
                    metadata={
                        'origin': 'Web',
                        'application': selected_template.application,
                        'template_id': str(selected_template.pk),
                        'email_sent': False,
                    },
                )
        else:
            target = str(payload.get('target') or '').strip()
            reason = str(payload.get('reason') or '').strip()
            owner = str(payload.get('owner') or '').strip()
            priority = str(payload.get('priority') or '').strip()
            internal_comment = str(payload.get('internal_comment') or '').strip()
            if ticket.status in {Ticket.STATUS_RESOLVED, Ticket.STATUS_CLOSED, Ticket.STATUS_CANCELED}:
                return JsonResponse({'ok': False, 'error': 'Chamados resolvidos ou encerrados devem ser reabertos antes de escalonar.'}, status=400)
            if not target or not reason:
                return JsonResponse({'ok': False, 'error': 'Destino e motivo sao obrigatorios.'}, status=400)
            if priority and priority not in dict(Ticket.PRIORITY_CHOICES):
                return JsonResponse({'ok': False, 'error': 'Prioridade invalida.'}, status=400)
            old_queue = ticket.queue
            if ticket.status != Ticket.STATUS_IN_PROGRESS:
                try:
                    transition_ticket(
                        ticket,
                        Ticket.STATUS_IN_PROGRESS,
                        actor=actor,
                        reason=reason,
                        source='Central Tecnica',
                        notify=False,
                    )
                except WorkflowError as exc:
                    return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
            ticket.queue = target
            ticket.assigned_to = owner or ticket.assigned_to
            if priority:
                ticket.priority = priority
            ticket.save()
            add_ticket_comment(
                ticket=ticket,
                actor=actor,
                body=internal_comment or reason,
                visibility=TicketComment.VISIBILITY_INTERNAL,
                source='Central Tecnica',
            )
            create_audit_event(
                ticket,
                actor=actor,
                event_type='ticket_escalated',
                action=f'Escalou chamado para {target}',
                field_name='queue',
                old_value=old_queue,
                new_value=target,
                metadata={
                    'origin': 'Web',
                    'reason': reason,
                    'owner': owner,
                    'priority': priority,
                    'template_id': str(selected_template.pk) if selected_template else '',
                    'template_name': selected_template.name if selected_template else '',
                },
            )

    return JsonResponse({
        'ok': True,
        'action': action,
        'ticket': {
            'status': ticket.status,
            'statusLabel': ticket.get_status_display(),
            'priority': ticket.priority,
            'priorityLabel': ticket.get_priority_display(),
            'queue': ticket.queue,
            'assignedTo': ticket.assigned_to,
        },
    })


def ticket_dashboard(request):
    _require_technical_access(request)
    context = {
        **_base_context('dashboard'),
        **build_ticket_dashboard_context(request),
    }
    return render(request, 'tickets/dashboard.html', context)


def ticket_categories(request):
    _require_technical_access(request)
    context = {
        **_base_context('categories'),
        **build_category_settings_context(),
    }
    return render(request, 'tickets/categories.html', context)


def ticket_automation_rules(request):
    _require_technical_access(request)
    context = {
        **_base_context('automation'),
        **build_automation_rules_context(),
    }
    return render(request, 'tickets/automation_rules.html', context)


def _bool_from_value(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'sim', 'ativa', 'ativo', 'active'}


def _split_csv(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value or '').split(',') if part.strip()]


def _category_by_id_or_name(payload):
    item_id = payload.get('id')
    if item_id:
        found = TicketCategory.objects.filter(pk=item_id).first()
        if found:
            return found
    name = str(payload.get('name') or '').strip()
    return TicketCategory.objects.filter(name=name).first()


def _queue_by_id_or_name(payload):
    item_id = payload.get('id')
    if item_id:
        found = DeskQueue.objects.filter(pk=item_id).first()
        if found:
            return found
    name = str(payload.get('name') or '').strip()
    return DeskQueue.objects.filter(name=name).first()


def _sla_by_id_or_name(payload):
    item_id = payload.get('id')
    if item_id:
        found = DeskSLA.objects.filter(pk=item_id).first()
        if found:
            return found
    name = str(payload.get('name') or '').strip()
    return DeskSLA.objects.filter(name=name).first()


def _template_by_id_or_name(payload):
    item_id = payload.get('id')
    if item_id:
        found = DeskTemplate.objects.filter(pk=item_id).first()
        if found:
            return found
    name = str(payload.get('name') or '').strip()
    return DeskTemplate.objects.filter(name=name).first()


def ticket_settings(request):
    _require_technical_access(request)
    context = {
        **_base_context('settings'),
        **build_settings_context(),
    }
    return render(request, 'tickets/settings.html', context)


@require_POST
def ticket_settings_api(request):
    _require_technical_access(request)
    payload = _json_payload(request)
    if payload is None:
        return JsonResponse({'ok': False, 'error': 'JSON invalido.'}, status=400)

    kind = payload.get('kind')
    action = payload.get('action') or 'save'
    name = str(payload.get('name') or '').strip()
    if action in {'save', 'duplicate'} and not name:
        return JsonResponse({'ok': False, 'error': 'Nome e obrigatorio.'}, status=400)

    if kind == 'category':
        item = _category_by_id_or_name(payload)
        if action == 'duplicate' and item:
            name = f'{item.name} cópia'
            item = None
        if action == 'toggle' and item:
            item.is_active = not item.is_active
            item.save(update_fields=['is_active', 'updated_at'])
            _audit_config_event(
                request,
                'desk.category_toggled',
                'Categoria alterada',
                {'category_id': str(item.pk), 'category_name': item.name, 'is_active': item.is_active},
            )
            return JsonResponse({'ok': True, 'id': str(item.pk), 'active': item.is_active})
        queue = DeskQueue.objects.filter(name=str(payload.get('default_queue') or '').strip()).first()
        sla = DeskSLA.objects.filter(name=str(payload.get('default_sla') or '').strip()).first()
        allowed = _split_csv(payload.get('allowed_types')) or [TicketCategory.TYPE_INCIDENT]
        subcategories = _split_csv(payload.get('subcategories'))
        values = {
            'name': name,
            'description': payload.get('description') or '',
            'icon': normalize_category_icon(payload.get('icon')),
            'color': normalize_category_color(payload.get('color')),
            'default_priority': payload.get('default_priority') or Ticket.PRIORITY_NORMAL,
            'default_queue': queue,
            'default_sla': sla,
            'allowed_types': allowed,
            'subcategories': subcategories,
            'is_active': _bool_from_value(payload.get('is_active'), True),
        }
        was_existing = bool(item)
        if item:
            for field, field_value in values.items():
                setattr(item, field, field_value)
            item.save()
        else:
            item = TicketCategory.objects.create(**values)
        _audit_config_event(
            request,
            'desk.category_updated' if was_existing else 'desk.category_created',
            'Categoria salva',
            {
                'category_id': str(item.pk),
                'category_name': item.name,
                'icon': item.icon,
                'color': item.color,
                'default_priority': item.default_priority,
                'default_queue': item.default_queue.name if item.default_queue else '',
                'default_sla': item.default_sla.name if item.default_sla else '',
                'allowed_types': item.allowed_types,
                'subcategories': item.subcategories,
            },
        )
        return JsonResponse({'ok': True, 'id': str(item.pk), 'name': item.name})

    if kind == 'queue':
        item = _queue_by_id_or_name(payload)
        if action == 'duplicate' and item:
            name = f'{item.name} cópia'
            item = None
        if action == 'toggle' and item:
            item.is_active = not item.is_active
            item.save(update_fields=['is_active', 'updated_at'])
            _audit_config_event(
                request,
                'desk.queue_toggled',
                'Fila alterada',
                {'queue_id': str(item.pk), 'queue_name': item.name, 'is_active': item.is_active},
            )
            return JsonResponse({'ok': True, 'id': str(item.pk), 'active': item.is_active})
        values = {
            'name': name,
            'description': payload.get('description') or '',
            'responsible': payload.get('responsible') or '',
            'members': _split_csv(payload.get('members')),
            'business_hours': payload.get('business_hours') or 'Comercial',
            'capacity': int(payload.get('capacity') or 0) or None,
            'receives_tickets': _bool_from_value(payload.get('receives_tickets'), True),
            'receives_rmm': _bool_from_value(payload.get('receives_rmm'), False),
            'receives_gmud': _bool_from_value(payload.get('receives_gmud'), False),
            'is_active': _bool_from_value(payload.get('is_active'), True),
        }
        was_existing = bool(item)
        if item:
            for field, field_value in values.items():
                setattr(item, field, field_value)
            item.save()
        else:
            item = DeskQueue.objects.create(**values)
        _audit_config_event(
            request,
            'desk.queue_updated' if was_existing else 'desk.queue_created',
            'Fila salva',
            {
                'queue_id': str(item.pk),
                'queue_name': item.name,
                'responsible': item.responsible,
                'receives_tickets': item.receives_tickets,
                'receives_rmm': item.receives_rmm,
                'receives_gmud': item.receives_gmud,
                'capacity': item.capacity,
            },
        )
        return JsonResponse({'ok': True, 'id': str(item.pk), 'name': item.name})

    if kind == 'sla':
        item = _sla_by_id_or_name(payload)
        if action == 'duplicate' and item:
            name = f'{item.name} cópia'
            item = None
        if action == 'toggle' and item:
            item.is_active = not item.is_active
            item.save(update_fields=['is_active', 'updated_at'])
            _audit_config_event(
                request,
                'desk.sla_toggled',
                'SLA alterado',
                {'sla_id': str(item.pk), 'sla_name': item.name, 'is_active': item.is_active},
            )
            return JsonResponse({'ok': True, 'id': str(item.pk), 'active': item.is_active})
        values = {
            'name': name,
            'description': payload.get('description') or '',
            'priority': payload.get('priority') or Ticket.PRIORITY_NORMAL,
            'first_response_minutes': int(payload.get('first_response_minutes') or 240),
            'resolution_minutes': int(payload.get('resolution_minutes') or 1440),
            'calendar_type': DeskSLA.CALENDAR_24X7 if payload.get('calendar_type') == '24x7' else DeskSLA.CALENDAR_BUSINESS,
            'pause_on_waiting_requester': _bool_from_value(payload.get('pause_on_waiting_requester'), True),
            'pause_on_waiting_supplier': _bool_from_value(payload.get('pause_on_waiting_supplier'), False),
            'pause_on_waiting_approval': _bool_from_value(payload.get('pause_on_waiting_approval'), False),
            'is_active': _bool_from_value(payload.get('is_active'), True),
        }
        was_existing = bool(item)
        if item:
            for field, field_value in values.items():
                setattr(item, field, field_value)
            item.save()
        else:
            item = DeskSLA.objects.create(**values)
        _audit_config_event(
            request,
            'desk.sla_updated' if was_existing else 'desk.sla_created',
            'SLA salvo',
            {
                'sla_id': str(item.pk),
                'sla_name': item.name,
                'priority': item.priority,
                'first_response_minutes': item.first_response_minutes,
                'resolution_minutes': item.resolution_minutes,
                'calendar_type': item.calendar_type,
            },
        )
        return JsonResponse({'ok': True, 'id': str(item.pk), 'name': item.name})

    if kind == 'template':
        item = _template_by_id_or_name(payload)
        if action == 'duplicate' and item:
            name = f'{item.name} cópia'
            item = None
        if action == 'toggle' and item:
            item.is_active = not item.is_active
            item.save(update_fields=['is_active', 'updated_at'])
            return JsonResponse({'ok': True, 'id': str(item.pk), 'active': item.is_active})
        category = TicketCategory.objects.filter(name=str(payload.get('category') or '').strip()).first()
        values = {
            'name': name,
            'description': payload.get('description') or '',
            'template_type': payload.get('template_type') or DeskTemplate.TYPE_PUBLIC_REPLY,
            'application': payload.get('application') or '',
            'category': category,
            'channel': payload.get('channel') or DeskTemplate.CHANNEL_PUBLIC,
            'subject': payload.get('subject') or '',
            'trigger': payload.get('trigger') or '',
            'content': payload.get('content') or '',
            'variables': _split_csv(payload.get('variables')),
            'is_active': _bool_from_value(payload.get('is_active'), True),
        }
        if item:
            for field, field_value in values.items():
                setattr(item, field, field_value)
            item.save()
        else:
            item = DeskTemplate.objects.create(**values)
        return JsonResponse({'ok': True, 'id': str(item.pk), 'name': item.name})

    return JsonResponse({'ok': False, 'error': 'Tipo de configuracao nao suportado.'}, status=400)


@require_POST
def ticket_fake_action(request, number=None, action='updated'):
    _require_technical_access(request)
    messages.success(request, f'Acao registrada no preview: {action}.')
    if number:
        return redirect('tickets:detail', number=number)
    return redirect('tickets:central')


def ticket_attachment_download(request, attachment_id):
    attachment = get_object_or_404(
        TicketAttachment.objects.select_related('ticket'),
        pk=attachment_id,
    )
    if not _attachment_allowed_for_request(request, attachment):
        raise PermissionDenied('Anexo indisponivel para este usuario.')
    if not attachment.file:
        raise Http404('Arquivo nao encontrado.')
    try:
        return FileResponse(
            attachment.file.open('rb'),
            as_attachment=True,
            filename=attachment.original_name or 'anexo',
        )
    except FileNotFoundError:
        raise Http404('Arquivo nao encontrado.')


PORTAL_OPEN_STATUSES = {
    Ticket.STATUS_NEW,
    Ticket.STATUS_IN_PROGRESS,
    Ticket.STATUS_WAITING_USER,
    Ticket.STATUS_WAITING_THIRD_PARTY,
}


def _portal_requester_email(request):
    if request.user.is_authenticated:
        return (getattr(request.user, 'email', '') or '').strip()
    return ''


def _portal_queryset(request):
    queryset = Ticket.objects.select_related('category', 'endpoint', 'sla').prefetch_related('comments', 'attachments')
    requester_email = _portal_requester_email(request)
    if request.user.is_authenticated:
        if not requester_email:
            return queryset.none(), requester_email
        queryset = queryset.filter(requester_email__iexact=requester_email)
    elif requester_email:
        queryset = queryset.filter(requester_email__iexact=requester_email)
    return queryset, requester_email


def _portal_detail_redirect(ticket, request):
    if request.path_info.startswith('/meus-chamados/'):
        route_name = 'requester-ticket-detail'
    else:
        route_name = 'ticket-portal-detail'
    url = reverse(route_name, kwargs={'number': ticket.number})
    requester_email = _portal_requester_email(request)
    if requester_email and route_name == 'ticket-portal-detail':
        url = f'{url}?{urlencode({"email": requester_email})}'
    return redirect(url)


def _portal_actor(request, ticket):
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        return user.get_full_name() or user.get_username() or user.email or 'Solicitante'
    if ticket is None:
        return 'Solicitante'
    return ticket.requester_name or ticket.requester_email or 'Solicitante'


def _portal_status_message(ticket):
    messages_by_status = {
        Ticket.STATUS_NEW: 'Recebemos sua solicitacao e ela aguarda triagem.',
        Ticket.STATUS_IN_PROGRESS: 'Nossa equipe esta trabalhando neste chamado.',
        Ticket.STATUS_WAITING_USER: 'Precisamos do seu retorno para continuar o atendimento.',
        Ticket.STATUS_WAITING_THIRD_PARTY: 'O atendimento aguarda retorno de terceiro ou fornecedor.',
        Ticket.STATUS_RESOLVED: 'Este chamado foi resolvido. Se a solucao nao atendeu sua solicitacao, voce pode contestar e reabrir o atendimento.',
        Ticket.STATUS_CLOSED: 'Este chamado foi encerrado e esta disponivel apenas para consulta.',
        Ticket.STATUS_CANCELED: 'Este chamado foi cancelado.',
    }
    return messages_by_status.get(ticket.status, 'Acompanhe as atualizacoes deste atendimento.')


def _portal_filter_queryset(queryset, request):
    query = (request.GET.get('q') or '').strip()
    status = (request.GET.get('status') or 'all').strip()
    if query:
        q_filter = Q(title__icontains=query) | Q(category__name__icontains=query)
        if query.lstrip('#').isdigit():
            q_filter |= Q(number=int(query.lstrip('#')))
        queryset = queryset.filter(q_filter)
    if status == 'open':
        queryset = queryset.filter(status__in=PORTAL_OPEN_STATUSES)
    elif status == 'waiting_user':
        queryset = queryset.filter(status=Ticket.STATUS_WAITING_USER)
    elif status == 'resolved':
        queryset = queryset.filter(status=Ticket.STATUS_RESOLVED)
    elif status == 'closed':
        queryset = queryset.filter(status__in=[Ticket.STATUS_CLOSED, Ticket.STATUS_CANCELED])
    return queryset


def _portal_public_timeline(ticket):
    items = [
        {
            'kind': 'created',
            'icon': 'ticket-plus',
            'title': 'Chamado aberto',
            'body': ticket.description,
            'author': ticket.requester_name or ticket.requester_email or 'Solicitante',
            'created_at': ticket.created_at,
        }
    ]
    for comment in ticket.comments.filter(visibility=TicketComment.VISIBILITY_PUBLIC).order_by('created_at'):
        items.append({
            'kind': 'comment',
            'icon': 'message-square',
            'title': 'Comentario publico',
            'body': comment.body,
            'author': comment.author_name or 'NightOwl Desk',
            'created_at': comment.created_at,
            'attachments': list(comment.attachments.filter(visibility=TicketAttachment.VISIBILITY_PUBLIC)),
        })
    if ticket.status == Ticket.STATUS_RESOLVED and ticket.resolved_at:
        items.append({
            'kind': 'resolved',
            'icon': 'check-circle',
            'title': 'Chamado resolvido',
            'body': 'Atendimento marcado como resolvido.',
            'author': ticket.assigned_to or 'NightOwl Desk',
            'created_at': ticket.resolved_at,
        })
    if ticket.status == Ticket.STATUS_CLOSED and ticket.closed_at:
        items.append({
            'kind': 'closed',
            'icon': 'lock',
            'title': 'Chamado encerrado',
            'body': 'Este chamado foi encerrado e esta disponivel apenas para consulta.',
            'author': ticket.assigned_to or 'NightOwl Desk',
            'created_at': ticket.closed_at,
        })
    return sorted(items, key=lambda item: item['created_at'])


def _portal_solution(ticket):
    if ticket.status not in {Ticket.STATUS_RESOLVED, Ticket.STATUS_CLOSED}:
        return None
    resolution_event = ticket.audit_events.filter(event_type='ticket_resolved').order_by('-created_at').first()
    comment = None
    public_comment_id = (resolution_event.metadata or {}).get('public_comment_id') if resolution_event else ''
    if public_comment_id:
        comment = ticket.comments.filter(pk=public_comment_id, visibility=TicketComment.VISIBILITY_PUBLIC).first()
    if not comment:
        comment = (
            ticket.comments
            .filter(visibility=TicketComment.VISIBILITY_PUBLIC, body__icontains='Solucao')
            .order_by('-created_at')
            .first()
        )
    return {
        'summary': comment.body if comment else 'Chamado marcado como resolvido pela equipe de atendimento.',
        'resolved_at': ticket.resolved_at,
        'responsible': ticket.assigned_to or 'Equipe de atendimento',
    }


def _portal_route_context(request, requester_mode=False):
    if requester_mode or request.path_info.startswith('/meus-chamados/'):
        return {
            'portal_mode': 'requester',
            'portal_list_url_name': 'requester-ticket-list',
            'portal_detail_url_name': 'requester-ticket-detail',
            'portal_comment_url_name': 'requester-ticket-comment',
            'portal_reopen_url_name': 'requester-ticket-reopen',
            'portal_create_url_name': 'requester-ticket-create',
            'requester_can_create': True,
        }
    return {
        'portal_mode': 'portal',
        'portal_list_url_name': 'ticket-portal-list',
        'portal_detail_url_name': 'ticket-portal-detail',
        'portal_comment_url_name': 'ticket-portal-comment',
        'portal_reopen_url_name': 'ticket-portal-reopen',
        'portal_create_url_name': '',
        'requester_can_create': False,
    }


def _portal_category_options():
    return TicketCategory.objects.filter(is_active=True).select_related('default_queue', 'default_sla').order_by('name')


def _portal_list_context(request, requester_mode=False):
    queryset, requester_email = _portal_queryset(request)
    route_context = _portal_route_context(request, requester_mode=requester_mode)
    tickets = list(_portal_filter_queryset(queryset, request).order_by('-updated_at')[:100])
    selected_number = str(request.GET.get('selected') or '').lstrip('#')
    for ticket in tickets:
        ticket.portal_last_public_comment = (
            ticket.comments.filter(visibility=TicketComment.VISIBILITY_PUBLIC).order_by('-created_at').first()
        )
        ticket.portal_public_attachments = list(ticket.attachments.filter(visibility=TicketAttachment.VISIBILITY_PUBLIC).order_by('-created_at')[:5])
        ticket.portal_attachment_summary = ', '.join(attachment.original_name for attachment in ticket.portal_public_attachments) or 'Nenhum anexo publico.'
        ticket.portal_can_reply = can_comment_public(ticket)
        ticket.portal_can_reopen = ticket.status == Ticket.STATUS_RESOLVED
        ticket.portal_is_selected = bool(selected_number and selected_number == str(ticket.number))
        ticket.portal_comment_url = reverse(route_context['portal_comment_url_name'], kwargs={'number': ticket.number})
        ticket.portal_detail_url = reverse(route_context['portal_detail_url_name'], kwargs={'number': ticket.number})
        if requester_email and route_context['portal_mode'] != 'requester':
            ticket.portal_comment_url = f'{ticket.portal_comment_url}?{urlencode({"email": requester_email})}'
            ticket.portal_detail_url = f'{ticket.portal_detail_url}?{urlencode({"email": requester_email})}'
    if selected_number and not any(ticket.portal_is_selected for ticket in tickets):
        selected_number = ''
    if not selected_number and tickets:
        tickets[0].portal_is_selected = True
    counts_queryset = queryset
    context = {
        'active_nav': 'ticket_portal',
        'requester_email': requester_email,
        'portal_missing_email': request.user.is_authenticated and not requester_email,
        'portal_categories': _portal_category_options(),
        'tickets': tickets,
        'preview_ticket': next((ticket for ticket in tickets if ticket.portal_is_selected), tickets[0] if tickets else None),
        'filters': {
            'q': request.GET.get('q', ''),
            'status': request.GET.get('status', 'all'),
        },
        'counts': {
            'all': counts_queryset.count(),
            'open': counts_queryset.filter(status__in=PORTAL_OPEN_STATUSES).count(),
            'in_progress': counts_queryset.filter(status=Ticket.STATUS_IN_PROGRESS).count(),
            'waiting_user': counts_queryset.filter(status=Ticket.STATUS_WAITING_USER).count(),
            'resolved': counts_queryset.filter(status=Ticket.STATUS_RESOLVED).count(),
            'closed': counts_queryset.filter(status__in=[Ticket.STATUS_CLOSED, Ticket.STATUS_CANCELED]).count(),
        },
    }
    context.update(route_context)
    return context


def ticket_portal_list(request):
    context = _portal_list_context(request, requester_mode=False)
    return render(request, 'tickets/portal_list.html', context)


def _portal_detail_context(request, number, requester_mode=False):
    queryset, requester_email = _portal_queryset(request)
    ticket = get_object_or_404(queryset, number=number)
    public_attachments = ticket.attachments.filter(visibility=TicketAttachment.VISIBILITY_PUBLIC)
    route_context = _portal_route_context(request, requester_mode=requester_mode)
    context = {
        'active_nav': 'ticket_portal',
        'requester_email': requester_email,
        'ticket': ticket,
        'status_message': _portal_status_message(ticket),
        'public_comments': ticket.comments.filter(visibility=TicketComment.VISIBILITY_PUBLIC).order_by('created_at'),
        'public_attachments': public_attachments,
        'timeline': _portal_public_timeline(ticket),
        'solution': _portal_solution(ticket),
        'can_reply': can_comment_public(ticket),
        'can_reopen': ticket.status == Ticket.STATUS_RESOLVED,
    }
    context.update(route_context)
    return context


def ticket_portal_detail(request, number):
    context = _portal_detail_context(request, number, requester_mode=False)
    return render(request, 'tickets/portal_detail.html', context)


def ticket_requester_list(request):
    context = _portal_list_context(request, requester_mode=True)
    return render(request, 'tickets/portal_list.html', context)


def ticket_requester_detail(request, number):
    context = _portal_detail_context(request, number, requester_mode=True)
    return render(request, 'tickets/portal_detail.html', context)


@require_POST
def ticket_requester_create(request):
    requester_email = _portal_requester_email(request)
    if not requester_email:
        messages.error(request, 'Seu usuario precisa ter e-mail cadastrado para abrir chamados.')
        return redirect('requester-ticket-list')

    title = str(request.POST.get('title') or '').strip()
    description = str(request.POST.get('description') or '').strip()
    category_id = str(request.POST.get('category') or '').strip()
    priority = str(request.POST.get('priority') or Ticket.PRIORITY_NORMAL).strip()
    endpoint_name = str(request.POST.get('endpoint_name') or '').strip()
    if not title or not description:
        messages.error(request, 'Informe titulo e descricao para abrir o chamado.')
        return redirect('requester-ticket-list')
    if priority not in dict(Ticket.PRIORITY_CHOICES):
        priority = Ticket.PRIORITY_NORMAL

    category = None
    if category_id:
        category = TicketCategory.objects.filter(pk=category_id, is_active=True).select_related('default_queue', 'default_sla').first()
    if not category:
        category = TicketCategory.objects.filter(is_active=True).select_related('default_queue', 'default_sla').order_by('name').first()

    if category and category.default_priority:
        priority = category.default_priority
    sla = category.default_sla if category else None
    if not sla:
        sla = DeskSLA.objects.filter(is_active=True, priority=priority).order_by('resolution_minutes', 'name').first()
    queue = category.default_queue.name if category and category.default_queue else 'N1 - Atendimento'
    actor = _portal_actor(request, None)
    user = getattr(request, 'user', None)
    requester_name = user.get_full_name() if user and user.is_authenticated else ''
    requester_name = requester_name or (user.get_username() if user and user.is_authenticated else '') or requester_email

    with transaction.atomic():
        ticket = Ticket.objects.create(
            title=title,
            description=description,
            requester_name=requester_name,
            requester_email=requester_email,
            requester_username=user.get_username() if user and user.is_authenticated else '',
            status=Ticket.STATUS_NEW,
            priority=priority,
            category=category,
            queue=queue,
            sla=sla,
            source=Ticket.SOURCE_PORTAL,
            endpoint_name=endpoint_name,
        )
        opening_comment = TicketComment.objects.create(
            ticket=ticket,
            author_name=actor,
            body=f'Abertura pelo portal do solicitante:\n\n{description}',
            visibility=TicketComment.VISIBILITY_PUBLIC,
        )
        for uploaded in request.FILES.getlist('attachments'):
            TicketAttachment.objects.create(
                ticket=ticket,
                comment=opening_comment,
                file=uploaded,
                original_name=uploaded.name,
                content_type=getattr(uploaded, 'content_type', '') or '',
                size=getattr(uploaded, 'size', 0) or 0,
                uploaded_by=actor,
                visibility=TicketAttachment.VISIBILITY_PUBLIC,
            )
        create_audit_event(
            ticket,
            actor=actor,
            event_type='ticket_created',
            action='Solicitante abriu chamado',
            field_name='ticket',
            new_value=f'#{ticket.number}',
            metadata={'origin': 'RequesterPortal', 'queue': queue, 'sla': sla.name if sla else ''},
        )
        prepare_ticket_notification(ticket, 'ticket_created', user=actor)
    messages.success(request, 'Chamado aberto com sucesso.')
    url = f'{reverse("requester-ticket-list")}?{urlencode({"selected": ticket.number})}'
    return redirect(url)


@require_POST
def ticket_portal_comment(request, number):
    queryset, _ = _portal_queryset(request)
    ticket = get_object_or_404(queryset, number=number)
    if ticket.status in {Ticket.STATUS_RESOLVED, Ticket.STATUS_CLOSED, Ticket.STATUS_CANCELED}:
        messages.error(request, 'Este chamado nao aceita novas respostas neste status.')
        return _portal_detail_redirect(ticket, request)
    body = str(request.POST.get('body') or '').strip()
    if not body:
        messages.error(request, 'Informe uma mensagem para enviar sua resposta.')
        return _portal_detail_redirect(ticket, request)
    actor = _portal_actor(request, ticket)
    with transaction.atomic():
        try:
            comment = requester_reply(ticket, actor=actor, body=body, source='Portal')
        except WorkflowError as exc:
            messages.error(request, str(exc))
            return _portal_detail_redirect(ticket, request)
        for uploaded in request.FILES.getlist('attachments'):
            TicketAttachment.objects.create(
                ticket=ticket,
                comment=comment,
                file=uploaded,
                original_name=uploaded.name,
                content_type=getattr(uploaded, 'content_type', '') or '',
                size=getattr(uploaded, 'size', 0) or 0,
                uploaded_by=actor,
                visibility=TicketAttachment.VISIBILITY_PUBLIC,
            )
    messages.success(request, 'Resposta enviada.')
    return _portal_detail_redirect(ticket, request)


@require_POST
def ticket_requester_comment(request, number):
    return ticket_portal_comment(request, number)


@require_POST
def ticket_portal_reopen(request, number):
    queryset, _ = _portal_queryset(request)
    ticket = get_object_or_404(queryset, number=number)
    if ticket.status != Ticket.STATUS_RESOLVED:
        messages.error(request, 'Somente chamados resolvidos podem ser contestados pelo portal.')
        return _portal_detail_redirect(ticket, request)
    reason = str(request.POST.get('reason') or '').strip()
    if not reason:
        messages.error(request, 'Informe o motivo da contestacao para reabrir o chamado.')
        return _portal_detail_redirect(ticket, request)
    actor = _portal_actor(request, ticket)
    try:
        requester_reopen(ticket, actor=actor, reason=reason, source='Portal')
    except WorkflowError as exc:
        messages.error(request, str(exc))
        return _portal_detail_redirect(ticket, request)
    messages.success(request, 'Contestacao registrada. O chamado voltou para atendimento.')
    return _portal_detail_redirect(ticket, request)


@require_POST
def ticket_requester_reopen(request, number):
    return ticket_portal_reopen(request, number)
