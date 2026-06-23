from django.utils import timezone


MOCK_ENDPOINTS = [
    {
        'id': 'mock-dir-note-011',
        'hostname': 'DIR-NOTE-011',
        'status': 'online',
        'status_label': 'Online',
        'last_seen': 'ha 3 min',
        'last_user': 'henrique.valente',
        'domain': 'control.local',
        'source': 'mock',
    },
    {
        'id': 'mock-fin-012',
        'hostname': 'FIN-012',
        'status': 'online',
        'status_label': 'Online',
        'last_seen': 'ha 4 min',
        'last_user': 'mariana.souza',
        'domain': 'control.local',
        'source': 'mock',
    },
    {
        'id': 'mock-srv-files-01',
        'hostname': 'SRV-FILES-01',
        'status': 'online',
        'status_label': 'Online',
        'last_seen': 'ha 1 min',
        'last_user': 'system',
        'domain': 'control.local',
        'source': 'mock',
    },
]


def _relative_seen(value):
    if not value:
        return 'Sem sincronizacao recente'
    delta = timezone.now() - value
    minutes = max(1, int(delta.total_seconds() // 60))
    if minutes < 60:
        return f'ha {minutes} min'
    hours = minutes // 60
    if hours < 24:
        return f'ha {hours}h'
    return f'ha {hours // 24}d'


def _endpoint_from_agent(agent):
    status_label = {
        'online': 'Online',
        'offline': 'Offline',
        'unknown': 'Desconhecido',
    }.get(agent.status, agent.status or 'Desconhecido')
    return {
        'id': str(agent.id),
        'hostname': agent.hostname,
        'status': agent.status or 'unknown',
        'status_label': status_label,
        'last_seen': _relative_seen(agent.last_seen_at),
        'last_user': agent.last_logged_user or 'Sem usuario',
        'domain': agent.domain or '',
        'source': 'rmm',
    }


def get_ticket_creation_endpoints(limit=10):
    try:
        from agents.models import AgentMachine

        agents = list(
            AgentMachine.objects.filter(is_active=True)
            .order_by('hostname', 'domain')[:limit]
        )
        if agents:
            return [_endpoint_from_agent(agent) for agent in agents]
    except Exception:
        pass
    return MOCK_ENDPOINTS[:limit]


def get_endpoint_preview(endpoint_id=None, hostname=None):
    endpoints = get_ticket_creation_endpoints(limit=25)
    for endpoint in endpoints:
        if endpoint_id and endpoint['id'] == str(endpoint_id):
            return endpoint
        if hostname and endpoint['hostname'].casefold() == hostname.casefold():
            return endpoint
    return endpoints[0] if endpoints else None
