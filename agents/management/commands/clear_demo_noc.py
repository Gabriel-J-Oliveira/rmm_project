from django.core.management.base import BaseCommand

from agents.models import AgentMachine, EndpointAlert


class Command(BaseCommand):
    help = 'Remove only demo NOC data created by seed_demo_noc.'

    def handle(self, *args, **options):
        demo_alerts = EndpointAlert.objects.filter(metadata__demo=True)
        demo_alert_count = demo_alerts.count()
        demo_alerts.delete()

        demo_endpoints = AgentMachine.objects.filter(
            hostname__startswith='DEMO-',
            domain='demo.local',
        )
        demo_endpoint_count = demo_endpoints.count()
        demo_endpoints.delete()

        self.stdout.write(self.style.SUCCESS(
            f'Demo data removed: {demo_endpoint_count} endpoints and {demo_alert_count} standalone alerts.'
        ))
