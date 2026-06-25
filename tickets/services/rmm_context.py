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
            'link_candidates': [
                {'hostname': 'FIN-012', 'user': 'mariana.souza', 'status': 'online', 'last_seen': 'ha 4 min', 'ip': '192.168.101.88'},
                {'hostname': 'DIR-NOTE-002', 'user': 'eduardo.campos', 'status': 'online', 'last_seen': 'ha 1 min', 'ip': '192.168.101.44'},
                {'hostname': 'SRV-FILES-01', 'user': 'system', 'status': 'online', 'last_seen': 'ha 1 min', 'ip': '192.168.100.12'},
                {'hostname': 'REC-004', 'user': 'patricia.alves', 'status': 'online', 'last_seen': 'ha 2 min', 'ip': '192.168.101.62'},
            ],
        }

    disk_value = 93 if endpoint.hostname.startswith('SRV') else 68
    memory_value = 74 if endpoint.status == 'online' else 0
    cpu_value = 41 if endpoint.status == 'online' else 0
    online = endpoint.status == 'online'
    alerts = []
    if endpoint.hostname.startswith('FIN') or 'NOTE' in endpoint.hostname:
        alerts.append({
            'severity': 'critical',
            'title': 'Antivirus sem protecao ativa',
            'description': f'O endpoint {endpoint.hostname} apareceu sem protecao identificada no painel de monitoramento.',
            'origin': 'RMM',
            'when': 'ha 18 min',
            'suggestion': 'Verificar servico de seguranca.',
            'action': 'Executar verificacao',
            'action_key': 'antivirus-scan',
            'icon': 'shield-alert',
        })
        alerts.append({
            'severity': 'medium',
            'title': '4 atualizacoes pendentes',
            'description': 'Atualizacoes do sistema aguardando instalacao.',
            'origin': 'RMM',
            'when': 'ha 1h',
            'suggestion': 'Planejar janela de atualizacao.',
            'action': 'Ver patches',
            'action_key': 'view-patches',
            'icon': 'package-search',
        })
        alerts.append({
            'severity': 'info',
            'title': 'Diagnostico recomendado',
            'description': 'Colete logs antes de encerrar o chamado.',
            'origin': 'NightOwl Desk',
            'when': 'agora',
            'suggestion': 'Coletar diagnostico.',
            'action': 'Coletar logs',
            'action_key': 'collect-logs',
            'icon': 'file-search',
        })
    if endpoint.hostname.startswith('SRV'):
        alerts.append({
            'severity': 'high',
            'title': 'Disco acima de 90%',
            'description': 'Unidade C: com pouco espaco livre.',
            'origin': 'Inventario RMM',
            'when': 'ha 8 min',
            'suggestion': 'Executar limpeza de temporarios.',
            'action': 'Limpar temporarios',
            'action_key': 'cleanup-temp',
            'icon': 'hard-drive',
        })
        alerts.append({
            'severity': 'medium',
            'title': 'Backup falhou',
            'description': 'Ultima rotina de backup nao concluiu.',
            'origin': 'Backup monitor',
            'when': 'ha 31 min',
            'suggestion': 'Coletar logs do job.',
            'action': 'Coletar logs',
            'action_key': 'collect-logs',
            'icon': 'database-backup',
        })
    risk_level = 'low'
    risk_label = 'Baixo'
    risk_summary = 'Endpoint sem alertas criticos no momento.'
    if any(alert['severity'] in {'critical', 'high'} for alert in alerts):
        risk_level = 'critical'
        risk_label = 'Alto'
        risk_summary = 'Antivirus sem protecao ativa · atualizacoes pendentes · ultimo check-in recente'
    elif any(alert['severity'] == 'medium' for alert in alerts):
        risk_level = 'warning'
        risk_label = 'Medio'
        risk_summary = 'Alertas de atencao aguardando revisao.'
    return {
        'available': True,
        'source': 'mock',
        'hostname': endpoint.hostname,
        'domain': endpoint.domain,
        'status': endpoint.status,
        'status_label': endpoint.status.title(),
        'last_seen': endpoint.last_heartbeat,
        'last_user': endpoint.last_user,
        'os': 'Windows 11 Pro' if not endpoint.hostname.startswith('SRV') else 'Windows Server 2022',
        'ip_address': '192.168.101.88' if endpoint.hostname.startswith('FIN') else '192.168.101.44' if 'NOTE' in endpoint.hostname else '192.168.100.12',
        'agent_version': 'NightOwl Agent 1.8.2',
        'uptime': '3d 4h' if online else 'desconhecido',
        'client': endpoint.domain,
        'tags': ['Financeiro' if endpoint.hostname.startswith('FIN') else 'Diretoria' if 'NOTE' in endpoint.hostname else 'TI', 'Notebook' if 'NOTE' in endpoint.hostname or endpoint.hostname.startswith('FIN') else 'Servidor', 'Monitorado'],
        'url': endpoint.url,
        'icon': 'monitor',
        'metrics': [
            {'name': 'CPU', 'value': cpu_value, 'level': _metric_level(cpu_value), 'summary': f'{cpu_value}% em uso'},
            {'name': 'Memoria', 'value': memory_value, 'level': _metric_level(memory_value), 'summary': f'{memory_value}% em uso'},
            {'name': 'Disco', 'value': disk_value, 'level': _metric_level(disk_value), 'summary': f'{disk_value}% usado'},
        ],
        'disks': [{'name': 'C:', 'value': disk_value, 'level': _metric_level(disk_value), 'summary': f'{disk_value}% usado'}],
        'antivirus': {'label': 'Protecao ausente' if endpoint.hostname.startswith('FIN') or 'NOTE' in endpoint.hostname else 'OK' if endpoint.status == 'online' else 'Sem dados', 'level': 'critical' if endpoint.hostname.startswith('FIN') or 'NOTE' in endpoint.hostname else 'ok' if endpoint.status == 'online' else 'unknown'},
        'updates_pending': 4 if endpoint.hostname.startswith('FIN') else 1,
        'backup': {'label': 'OK' if not endpoint.hostname.startswith('SRV') else 'Falhou', 'level': 'ok' if not endpoint.hostname.startswith('SRV') else 'critical'},
        'critical_services': {'label': 'Todos ativos' if online else 'Sem comunicacao', 'level': 'ok' if online else 'warning'},
        'last_reboot': 'ha 3d 4h',
        'patches': {'count': 4 if endpoint.hostname.startswith('FIN') else 1, 'level': 'warning' if endpoint.hostname.startswith('FIN') else 'ok'},
        'active_alerts': alerts,
        'alerts_count': len(alerts),
        'risk': {
            'level': risk_level,
            'label': risk_label,
            'summary': risk_summary,
        },
        'remote_actions': [
            {'key': 'remote-access', 'label': 'Acesso remoto', 'icon': 'screen-share', 'group': 'safe', 'sensitive': False, 'requires_online': True},
            {'key': 'processes', 'label': 'Ver processos', 'icon': 'list-tree', 'group': 'safe', 'sensitive': False, 'requires_online': True},
            {'key': 'services', 'label': 'Ver servicos', 'icon': 'settings-2', 'group': 'safe', 'sensitive': False, 'requires_online': True},
            {'key': 'sync-inventory', 'label': 'Sincronizar inventario', 'icon': 'refresh-ccw', 'group': 'safe', 'sensitive': False, 'requires_online': False},
            {'key': 'collect-diagnostics', 'label': 'Coletar diagnostico', 'icon': 'file-search', 'group': 'diagnostic', 'sensitive': False, 'requires_online': True},
            {'key': 'collect-logs', 'label': 'Coletar logs', 'icon': 'file-text', 'group': 'diagnostic', 'sensitive': False, 'requires_online': True},
            {'key': 'antivirus-scan', 'label': 'Verificar antivirus', 'icon': 'shield-check', 'group': 'diagnostic', 'sensitive': False, 'requires_online': True},
            {'key': 'send-message', 'label': 'Enviar mensagem', 'icon': 'message-square', 'group': 'diagnostic', 'sensitive': False, 'requires_online': True},
            {'key': 'restart-device', 'label': 'Reiniciar dispositivo', 'icon': 'refresh-cw', 'group': 'sensitive', 'sensitive': True, 'requires_online': True},
            {'key': 'lock-session', 'label': 'Bloquear sessao', 'icon': 'lock', 'group': 'sensitive', 'sensitive': True, 'requires_online': True},
            {'key': 'restart-agent', 'label': 'Reiniciar agente RMM', 'icon': 'rotate-cw', 'group': 'sensitive', 'sensitive': True, 'requires_online': True},
            {'key': 'cleanup-temp', 'label': 'Limpar temporarios', 'icon': 'trash-2', 'group': 'sensitive', 'sensitive': True, 'requires_online': True},
        ],
        'quick_scripts': [
            {'key': 'disk-cleanup', 'name': 'Limpeza de disco', 'description': 'Remove temporarios e caches comuns.', 'duration': '2-5 min', 'risk': 'low'},
            {'key': 'collect-logs', 'name': 'Coletar logs', 'description': 'Agrupa logs do agente e eventos do Windows.', 'duration': '1 min', 'risk': 'low'},
            {'key': 'restart-spooler', 'name': 'Reiniciar spooler', 'description': 'Reinicia fila de impressao local.', 'duration': '30 s', 'risk': 'medium'},
            {'key': 'gpupdate', 'name': 'Atualizar politicas', 'description': 'Executa atualizacao de politicas.', 'duration': '2 min', 'risk': 'low'},
            {'key': 'av-check', 'name': 'Verificar antivirus', 'description': 'Consulta protecao e assinatura.', 'duration': '1-2 min', 'risk': 'low'},
            {'key': 'repair-office', 'name': 'Reparar Office', 'description': 'Executa rotina de reparo rapido.', 'duration': '5-10 min', 'risk': 'medium'},
            {'key': 'flush-dns', 'name': 'Flush DNS', 'description': 'Limpa cache DNS local.', 'duration': '10 s', 'risk': 'low'},
            {'key': 'renew-ip', 'name': 'Renovar IP', 'description': 'Renova concessao DHCP.', 'duration': '30 s', 'risk': 'medium'},
            {'key': 'check-disk', 'name': 'Verificar disco', 'description': 'Confere espaco e saude basica.', 'duration': '1 min', 'risk': 'low'},
        ],
        'technical_history': [
            {'kind': 'alert', 'icon': 'bell', 'title': 'Alerta recebido', 'when': 'ha 12 min', 'origin': 'RMM', 'status': 'aberto'},
            {'kind': 'system', 'icon': 'refresh-ccw', 'title': 'Agente sincronizado', 'when': endpoint.last_heartbeat, 'origin': 'Agent', 'status': 'ok'},
            {'kind': 'script', 'icon': 'terminal', 'title': 'Coleta de inventario executada', 'when': 'ha 1h', 'origin': 'NightOwl', 'status': 'concluido'},
            {'kind': 'remote', 'icon': 'screen-share', 'title': 'Acesso remoto iniciado', 'when': 'ontem', 'origin': 'Gabriel', 'status': 'registrado'},
            {'kind': 'alert', 'icon': 'hard-drive', 'title': 'Disco acima do limite', 'when': 'ontem', 'origin': 'Inventario', 'status': 'tratado'},
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
        'active_alerts': [],
        'backup': {'label': 'Sem dados', 'level': 'unknown'},
        'critical_services': {'label': 'Sem dados', 'level': 'unknown'},
        'last_reboot': raw.get('last_boot_time', 'Sem dados'),
        'patches': {'count': raw.get('updates_pending', '--'), 'level': 'unknown'},
        'os': machine.os_name or 'Sem dados',
        'ip_address': machine.last_ip or 'Sem dados',
        'agent_version': machine.agent_version or 'Sem dados',
        'uptime': 'Sem dados',
        'client': machine.domain,
        'tags': ['Monitorado', 'Agente real'],
        'remote_actions': [
            {'key': 'remote-access', 'label': 'Acesso remoto', 'icon': 'screen-share', 'sensitive': False, 'requires_online': True},
            {'key': 'restart-device', 'label': 'Reiniciar dispositivo', 'icon': 'refresh-cw', 'sensitive': True, 'requires_online': True},
            {'key': 'collect-diagnostics', 'label': 'Coletar diagnostico', 'icon': 'file-search', 'sensitive': False, 'requires_online': True},
            {'key': 'sync-inventory', 'label': 'Sincronizar inventario', 'icon': 'refresh-ccw', 'sensitive': False, 'requires_online': False},
            {'key': 'restart-agent', 'label': 'Reiniciar agente RMM', 'icon': 'rotate-cw', 'sensitive': True, 'requires_online': True},
        ],
        'quick_scripts': _mock_context(endpoint)['quick_scripts'],
        'technical_history': _mock_context(endpoint)['technical_history'],
    }
