from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from agents.models import AgentMachine, AuditEvent
from agents.audit import create_audit_event


class Command(BaseCommand):
    help = 'Update AgentMachine status based on last heartbeat time.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--threshold-minutes',
            type=int,
            default=15,
            help='Minutes since last heartbeat before an active agent is marked offline.',
        )

    def handle(self, *args, **options):
        threshold_minutes = options['threshold_minutes']
        if threshold_minutes < 1:
            self.stderr.write(self.style.ERROR('--threshold-minutes must be greater than zero.'))
            return

        threshold_at = timezone.now() - timedelta(minutes=threshold_minutes)
        counts = {
            AgentMachine.STATUS_ONLINE: 0,
            AgentMachine.STATUS_OFFLINE: 0,
            AgentMachine.STATUS_UNKNOWN: 0,
        }

        machines = AgentMachine.objects.all()
        for machine in machines:
            if not machine.is_active:
                new_status = AgentMachine.STATUS_UNKNOWN
            elif machine.last_seen_at is None:
                new_status = AgentMachine.STATUS_UNKNOWN
            elif machine.last_seen_at >= threshold_at:
                new_status = AgentMachine.STATUS_ONLINE
            else:
                new_status = AgentMachine.STATUS_OFFLINE

            counts[new_status] += 1

            if machine.status != new_status:
                old_status = machine.status
                machine.status = new_status
                machine.save(update_fields=['status', 'updated_at'])
                if new_status == AgentMachine.STATUS_ONLINE:
                    severity = AuditEvent.SEVERITY_SUCCESS
                elif new_status == AgentMachine.STATUS_OFFLINE:
                    severity = AuditEvent.SEVERITY_WARNING
                else:
                    severity = AuditEvent.SEVERITY_INFO
                create_audit_event(
                    event_type='endpoint.status_changed',
                    title='Status do endpoint alterado',
                    description=f'Status alterado de {old_status} para {new_status}.',
                    severity=severity,
                    actor_type=AuditEvent.ACTOR_SCHEDULER,
                    actor_name='mark_offline_agents',
                    endpoint=machine,
                    metadata={
                        'old_status': old_status,
                        'new_status': new_status,
                        'last_seen_at': machine.last_seen_at.isoformat() if machine.last_seen_at else None,
                    },
                )

        self.stdout.write(self.style.SUCCESS('Agent status update complete.'))
        self.stdout.write(f'online: {counts[AgentMachine.STATUS_ONLINE]}')
        self.stdout.write(f'offline: {counts[AgentMachine.STATUS_OFFLINE]}')
        self.stdout.write(f'unknown: {counts[AgentMachine.STATUS_UNKNOWN]}')
