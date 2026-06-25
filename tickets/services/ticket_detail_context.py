from .rmm_context import get_ticket_device_context
from ..mock_data import MOCK_TICKETS


def _status_transitions(status):
    transitions = {
        'new': ['in_progress', 'waiting_user', 'canceled'],
        'in_progress': ['waiting_user', 'waiting_third_party', 'resolved'],
        'waiting_user': ['in_progress', 'resolved', 'canceled'],
        'waiting_third_party': ['in_progress', 'resolved', 'canceled'],
        'resolved': ['closed', 'in_progress'],
        'closed': [],
        'canceled': [],
    }
    return transitions.get(status, [])


def _sla_for(ticket):
    level = 'critical' if ticket.priority == 'critical' else 'warning' if ticket.priority == 'high' else 'ok'
    remaining = '32 min' if ticket.priority == 'critical' else '1h 18min' if ticket.priority == 'high' else '4h 20min'
    progress = 82 if ticket.priority == 'critical' else 64 if ticket.priority == 'high' else 38
    return {
        'label': f'SLA {remaining}',
        'remaining': remaining,
        'progress': progress,
        'level': level,
        'response_due': remaining,
        'resolution_due': 'Hoje, 17:30',
    }


def _activity_for(ticket):
    items = [
        {
            'kind': 'system',
            'icon': 'ticket',
            'title': 'Chamado criado',
            'body': f'{ticket.requester} abriu o chamado para o setor {ticket.sector}.',
            'when': ticket.created_at,
        },
    ]
    if ticket.assigned_to:
        items.append({
            'kind': 'system',
            'icon': 'user-check',
            'title': 'Responsavel atribuido',
            'body': f'{ticket.assigned_to} assumiu ou recebeu este chamado.',
            'when': ticket.assigned_at,
        })
    for comment in ticket.comments:
        items.append({
            'kind': 'comment',
            'icon': 'message-square',
            'title': comment.author,
            'body': comment.body,
            'when': f'{comment.visibility} - {comment.when}',
        })
    if ticket.priority == 'critical':
        items.append({
            'kind': 'sla',
            'icon': 'timer',
            'title': 'SLA em atencao',
            'body': 'Prazo de resposta/resolucao proximo do limite configurado.',
            'when': ticket.updated_for,
        })
    return items


def _similar_tickets(ticket):
    matches = [
        item for item in MOCK_TICKETS
        if item.number != ticket.number
        and (item.category == ticket.category or item.sector == ticket.sector)
    ]
    return matches[:4]


def _requester_history(ticket):
    return [
        item for item in MOCK_TICKETS
        if item.number != ticket.number and item.requester == ticket.requester
    ]


def _related_tickets(ticket):
    same_device = [
        item for item in MOCK_TICKETS
        if item.number != ticket.number
        and item.endpoint
        and ticket.endpoint
        and item.endpoint.hostname == ticket.endpoint.hostname
    ]
    same_requester = _requester_history(ticket)
    duplicates = [
        item for item in MOCK_TICKETS
        if item.number != ticket.number and item.title.lower() == ticket.title.lower()
    ]
    return {
        'same_device': same_device,
        'same_requester': same_requester,
        'duplicates': duplicates,
    }


def _related_items(ticket, related_tickets, similar_tickets):
    rows = []
    seen = set()

    def add_many(items, reason, score):
        for item in items:
            if item.number in seen:
                continue
            seen.add(item.number)
            rows.append({
                'ticket': item,
                'reason': reason,
                'score': score,
            })

    add_many(related_tickets.get('duplicates', []), 'Possivel duplicado', 94)
    add_many(related_tickets.get('same_device', []), 'Mesmo endpoint', 82)
    add_many(related_tickets.get('same_requester', []), 'Mesmo solicitante', 76)
    add_many(similar_tickets, 'Similar resolvido', 68)
    return rows


def _attachments_for(ticket):
    if not ticket.endpoint:
        return []
    return [
        {
            'id': f'att-{ticket.number}-evidence',
            'name': f'evidencia-{ticket.number}.png',
            'type': 'image',
            'kind': 'image',
            'size': '284 KB',
            'author': ticket.assigned_to or 'Gabriel',
            'origin': 'comentario',
            'uploaded_at': 'ha 18 min',
            'is_primary': True,
            'icon': 'image',
            'preview': 'Captura de tela com evidencias do atendimento.',
        },
        {
            'id': f'att-{ticket.number}-log',
            'name': 'log-diagnostico.txt',
            'type': 'log',
            'kind': 'log',
            'size': '18 KB',
            'author': 'NightOwl RMM',
            'origin': 'RMM',
            'uploaded_at': 'ha 12 min',
            'is_primary': False,
            'icon': 'file-terminal',
            'preview': '[08:41:02] Agent check started\n[08:41:03] Antivirus service missing\n[08:41:04] Inventory collected\n[08:41:07] Alert emitted',
        },
        {
            'id': f'att-{ticket.number}-pdf',
            'name': 'relatorio-antivirus.pdf',
            'type': 'pdf',
            'kind': 'pdf',
            'size': '620 KB',
            'author': 'Sistema',
            'origin': 'RMM',
            'uploaded_at': 'ha 8 min',
            'is_primary': False,
            'icon': 'file-text',
            'preview': 'Relatorio PDF gerado pelo monitoramento.',
        },
        {
            'id': f'att-{ticket.number}-user-print',
            'name': 'print-usuario.png',
            'type': 'image',
            'kind': 'image',
            'size': '410 KB',
            'author': ticket.requester,
            'origin': 'solicitante',
            'uploaded_at': 'ontem',
            'is_primary': False,
            'icon': 'image',
            'preview': 'Print enviado pelo solicitante.',
        },
    ]


def _workload_rows():
    rows = {}
    for ticket in MOCK_TICKETS:
        if ticket.status in {'closed', 'resolved', 'canceled'}:
            continue
        owner = ticket.assigned_to or 'Sem responsavel'
        rows[owner] = rows.get(owner, 0) + 1
    return [{'label': owner, 'count': count} for owner, count in sorted(rows.items())]


def build_ticket_detail_context(ticket):
    device = get_ticket_device_context(ticket)
    similar = _similar_tickets(ticket)
    requester_history = _requester_history(ticket)
    related_tickets = _related_tickets(ticket)
    return {
        'device_context': device,
        'sla': _sla_for(ticket),
        'activity_items': _activity_for(ticket),
        'similar_tickets': similar,
        'requester_history_count': len(requester_history),
        'related_tickets': related_tickets,
        'related_items': _related_items(ticket, related_tickets, similar),
        'attachments': _attachments_for(ticket),
        'watchers': [
            {'name': ticket.assigned_to or 'Gabriel', 'initials': (ticket.assigned_to or 'Gabriel')[:1]},
            {'name': 'Equipe TI', 'initials': 'TI'},
        ],
        'resolution_types': ['Resolvido definitivamente', 'Contorno aplicado', 'Encaminhado para fornecedor', 'Sem acao necessaria'],
        'resolution_checklist': [
            'Validar impacto com solicitante',
            'Registrar evidencia de resolucao',
            'Atualizar categoria e causa raiz',
        ],
        'status_transitions': _status_transitions(ticket.status),
        'workload_rows': _workload_rows(),
    }
