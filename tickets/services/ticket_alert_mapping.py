ALERT_CATEGORY_RULES = {
    'disk_low': 'Servidor',
    'endpoint_offline': 'Rede',
    'security_antivirus': 'Seguranca',
    'remote_access_software': 'Seguranca',
    'admin_network_tool': 'Seguranca',
    'low_memory': 'Hardware',
    'high_uptime': 'Hardware',
    'stale_inventory': 'RMM / Alerta',
}

ALERT_PRIORITY_RULES = {
    'critical': 'critical',
    'security': 'critical',
    'warning': 'high',
    'info': 'normal',
}


def category_for_alert(alert_type):
    return ALERT_CATEGORY_RULES.get(alert_type, 'RMM / Alerta')


def priority_for_alert(severity):
    return ALERT_PRIORITY_RULES.get(severity, 'normal')


def alert_mapping_summary():
    return {
        'category_rules': ALERT_CATEGORY_RULES,
        'priority_rules': ALERT_PRIORITY_RULES,
    }
