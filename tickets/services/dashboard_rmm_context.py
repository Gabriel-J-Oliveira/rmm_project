def _percent(part, total):
    if not total:
        return 0
    return round((part / total) * 100)


def _mock_fleet_context():
    return {
        'source': 'mock',
        'total_devices': 42,
        'online_count': 35,
        'online_percent': 83,
        'offline_count': 5,
        'unknown_count': 2,
        'critical_alerts': 4,
        'top_alert_type': 'Disco cheio',
        'top_alert_count': 3,
        'sla_target': 95,
        'sla_actual': 91,
        'health_rows': [
            {'label': 'Online', 'value': 83, 'count': 35, 'level': 'ok'},
            {'label': 'Com alerta critico', 'value': 10, 'count': 4, 'level': 'critical'},
            {'label': 'Offline', 'value': 12, 'count': 5, 'level': 'warning'},
        ],
    }


def get_dashboard_fleet_context():
    try:
        from agents.models import AgentMachine, EndpointAlert
    except Exception:
        return _mock_fleet_context()

    total = AgentMachine.objects.count()
    if total == 0:
        return _mock_fleet_context()

    online = AgentMachine.objects.filter(status=AgentMachine.STATUS_ONLINE).count()
    offline = AgentMachine.objects.filter(status=AgentMachine.STATUS_OFFLINE).count()
    unknown = AgentMachine.objects.filter(status=AgentMachine.STATUS_UNKNOWN).count()
    open_alerts = EndpointAlert.objects.filter(status__in=[EndpointAlert.STATUS_OPEN, EndpointAlert.STATUS_ACKNOWLEDGED])
    critical = open_alerts.filter(severity=EndpointAlert.SEVERITY_CRITICAL).count()

    top_alert = None
    top_count = 0
    counts = {}
    for item in open_alerts.values_list('alert_type', flat=True):
        counts[item] = counts.get(item, 0) + 1
    if counts:
        top_alert, top_count = sorted(counts.items(), key=lambda item: item[1], reverse=True)[0]

    online_percent = _percent(online, total)
    sla_actual = max(0, min(100, online_percent - (critical * 2)))
    return {
        'source': 'agents',
        'total_devices': total,
        'online_count': online,
        'online_percent': online_percent,
        'offline_count': offline,
        'unknown_count': unknown,
        'critical_alerts': critical,
        'top_alert_type': (top_alert or 'Sem alerta').replace('_', ' ').title(),
        'top_alert_count': top_count,
        'sla_target': 95,
        'sla_actual': sla_actual,
        'health_rows': [
            {'label': 'Online', 'value': online_percent, 'count': online, 'level': 'ok'},
            {'label': 'Com alerta critico', 'value': _percent(critical, total), 'count': critical, 'level': 'critical'},
            {'label': 'Offline', 'value': _percent(offline, total), 'count': offline, 'level': 'warning'},
        ],
    }
