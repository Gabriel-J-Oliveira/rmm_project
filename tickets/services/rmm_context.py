from django.urls import reverse


def _bytes_to_gb(value):
    if not value:
        return None
    try:
        return round(int(value) / (1024 ** 3), 1)
    except (TypeError, ValueError):
        return None


def _metric_level(value):
    if value is None:
        return 'unknown'
    if value >= 90:
        return 'critical'
    if value >= 75:
        return 'warning'
    return 'ok'


def _disk_usage(disks):
    rows = []
    for disk in disks or []:
        size = int(disk.get('size_bytes') or 0)
        free = int(disk.get('free_bytes') or 0)
        used_percent = 0
        if size > 0:
            used_percent = round(((size - free) / size) * 100)
        rows.append({
            'name': disk.get('name') or 'Disco',
            'value': used_percent,
            'level': _metric_level(used_percent),
            'summary': f'{used_percent}% usado',
        })
    return rows


def _defender_label(defender_status):
    status = defender_status or {}
    if not status:
        return {'label': 'Sem dados', 'level': 'unknown'}
    enabled = status.get('enabled')
    realtime = status.get('real_time_protection_enabled')
    if enabled is False or realtime is False:
        return {'label': 'Alerta', 'level': 'critical'}
    return {'label': 'OK', 'level': 'ok'}


def _mock_context(endpoint):
    if not endpoint:
        return {
            'available': False,
            'source': 'none',
            'hostname': 'Sem dispositivo vinculado',
            'status': 'unknown',
            'status_label': 'Sem dispositivo',
            'last_seen': '--',
            'url': '',
            'metrics': [],
            'disks': [],
            'antivirus': {'label': 'Sem dados', 'level': 'unknown'},
            'updates_pending': '--',
            'remote_actions': [],
        }

    disk_value = 93 if endpoint.hostname.startswith('SRV') else 68
    memory_value = 74 if endpoint.status == 'online' else 0
    cpu_value = 41 if endpoint.status == 'online' else 0
    return {
        'available': True,
        'source': 'mock',
        'hostname': endpoint.hostname,
        'domain': endpoint.domain,
        'status': endpoint.status,
        'status_label': endpoint.status.title(),
        'last_seen': endpoint.last_heartbeat,
        'last_user': endpoint.last_user,
        'url': endpoint.url,
        'icon': 'monitor',
        'metrics': [
            {'name': 'CPU', 'value': cpu_value, 'level': _metric_level(cpu_value), 'summary': f'{cpu_value}% em uso'},
            {'name': 'Memoria', 'value': memory_value, 'level': _metric_level(memory_value), 'summary': f'{memory_value}% em uso'},
            {'name': 'Disco', 'value': disk_value, 'level': _metric_level(disk_value), 'summary': f'{disk_value}% usado'},
        ],
        'disks': [{'name': 'C:', 'value': disk_value, 'level': _metric_level(disk_value), 'summary': f'{disk_value}% usado'}],
        'antivirus': {'label': 'OK' if endpoint.status == 'online' else 'Sem dados', 'level': 'ok' if endpoint.status == 'online' else 'unknown'},
        'updates_pending': 4 if endpoint.hostname.startswith('FIN') else 1,
        'remote_actions': [
            {'label': 'Reiniciar remotamente', 'icon': 'refresh-cw'},
            {'label': 'Executar diagnostico', 'icon': 'terminal'},
            {'label': 'Abrir acesso remoto', 'icon': 'screen-share'},
            {'label': 'Ver no RMM', 'icon': 'external-link', 'url': endpoint.url},
        ],
    }


def get_ticket_device_context(ticket):
    endpoint = getattr(ticket, 'endpoint', None)
    if not endpoint:
        return _mock_context(None)

    try:
        from agents.models import AgentMachine, EndpointAlert
    except Exception:
        return _mock_context(endpoint)

    machine = (
        AgentMachine.objects.filter(hostname__iexact=endpoint.hostname).order_by('-last_seen_at').first()
    )
    if not machine:
        return _mock_context(endpoint)

    snapshot = machine.inventory_snapshots.order_by('-received_at').first()
    raw = snapshot.raw_payload if snapshot and snapshot.raw_payload else {}
    cpu_value = raw.get('cpu_usage_percent') or raw.get('cpu_percent')
    memory_value = raw.get('memory_used_percent')
    disks = _disk_usage(snapshot.disks if snapshot else [])
    primary_disk = disks[0] if disks else {'name': 'Disco', 'value': None, 'level': 'unknown', 'summary': 'Sem dados'}
    alerts_count = EndpointAlert.objects.filter(endpoint=machine, status__in=['open', 'acknowledged']).count()

    metrics = [
        {'name': 'CPU', 'value': int(cpu_value or 0), 'level': _metric_level(int(cpu_value or 0)), 'summary': f'{int(cpu_value or 0)}% em uso' if cpu_value is not None else snapshot.cpu if snapshot else 'Sem dados'},
        {'name': 'Memoria', 'value': int(memory_value or 0), 'level': _metric_level(int(memory_value or 0)), 'summary': f'{int(memory_value or 0)}% em uso' if memory_value is not None else f'{_bytes_to_gb(snapshot.memory_total_bytes)} GB total' if snapshot and snapshot.memory_total_bytes else 'Sem dados'},
        {'name': 'Disco', 'value': primary_disk['value'] or 0, 'level': primary_disk['level'], 'summary': primary_disk['summary']},
    ]

    return {
        'available': True,
        'source': 'agents',
        'hostname': machine.hostname,
        'domain': machine.domain,
        'status': machine.status,
        'status_label': machine.get_status_display(),
        'last_seen': machine.last_seen_at,
        'last_user': machine.last_logged_user,
        'url': reverse('endpoint-detail', args=[machine.pk]),
        'icon': 'monitor',
        'metrics': metrics,
        'disks': disks,
        'antivirus': _defender_label(snapshot.defender_status if snapshot else {}),
        'updates_pending': raw.get('updates_pending', '--'),
        'alerts_count': alerts_count,
        'remote_actions': [
            {'label': 'Reiniciar remotamente', 'icon': 'refresh-cw'},
            {'label': 'Executar diagnostico', 'icon': 'terminal'},
            {'label': 'Abrir acesso remoto', 'icon': 'screen-share'},
            {'label': 'Ver no RMM', 'icon': 'external-link', 'url': reverse('endpoint-detail', args=[machine.pk])},
        ],
    }
