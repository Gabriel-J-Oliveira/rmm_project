from django.core.management.base import BaseCommand
from django.utils import timezone

from agents.audit import create_audit_event
from agents.models import AuditEvent, EndpointAlert


class Command(BaseCommand):
    help = 'Resolve expired temporary change alerts.'

    def handle(self, *args, **options):
        now = timezone.now()
        active = EndpointAlert.objects.filter(
            is_temporary=True,
            status__in=[EndpointAlert.STATUS_OPEN, EndpointAlert.STATUS_ACKNOWLEDGED],
        )
        evaluated = active.count()
        expired = active.filter(expires_at__isnull=False, expires_at__lte=now)
        expired_count = expired.count()

        for alert in expired.select_related('endpoint'):
            alert.status = EndpointAlert.STATUS_RESOLVED
            alert.resolved_at = now
            alert.resolution_type = EndpointAlert.RESOLUTION_AUTOMATIC
            alert.save(update_fields=['status', 'resolved_at', 'resolution_type', 'updated_at'])
            create_audit_event(
                event_type='alert.expired',
                title='Alerta temporario expirado',
                description='O alerta temporario de mudanca expirou apos 72 horas.',
                severity=AuditEvent.SEVERITY_INFO,
                actor_type=AuditEvent.ACTOR_SYSTEM,
                actor_name='expire_temporary_alerts',
                endpoint=alert.endpoint,
                alert=alert,
                metadata={
                    'alert_type': alert.alert_type,
                    'alert_id': str(alert.id),
                    'expires_at': alert.expires_at.isoformat() if alert.expires_at else None,
                    'source': alert.source,
                },
            )

        self.stdout.write(self.style.SUCCESS('Temporary alert expiration complete.'))
        self.stdout.write(f'temporary alerts evaluated: {evaluated}')
        self.stdout.write(f'expired/resolved: {expired_count}')
        self.stdout.write(f'still active: {evaluated - expired_count}')
