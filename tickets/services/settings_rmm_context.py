from django.utils import timezone


def get_settings_rmm_context():
    try:
        from agents.models import AgentMachine, EndpointAlert

        total = AgentMachine.objects.filter(is_active=True).count()
        last_seen = AgentMachine.objects.filter(last_seen_at__isnull=False).order_by('-last_seen_at').values_list('last_seen_at', flat=True).first()
        critical = EndpointAlert.objects.filter(status='open', severity__in=['critical', 'security']).count()
        if total:
            return {
                'source': 'RMM interno',
                'connected': True,
                'devices_synced': total,
                'last_sync': last_seen.strftime('%d/%m/%Y %H:%M') if last_seen else 'Sem sincronizacao recente',
                'critical_alerts': critical,
            }
    except Exception:
        pass
    return {
        'source': 'RMM interno',
        'connected': False,
        'devices_synced': 0,
        'last_sync': 'Aguardando primeira sincronizacao',
        'critical_alerts': 0,
    }
