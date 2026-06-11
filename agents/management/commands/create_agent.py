from django.core.management.base import BaseCommand

from agents.models import AgentMachine
from agents.services import build_fqdn


class Command(BaseCommand):
    help = 'Create an AgentMachine and print its bearer token once.'

    def add_arguments(self, parser):
        parser.add_argument('--hostname', required=True)
        parser.add_argument('--domain', default='')

    def handle(self, *args, **options):
        hostname = options['hostname']
        domain = options['domain']
        machine, token = AgentMachine.create_with_token(
            hostname=hostname,
            domain=domain,
            fqdn=build_fqdn(hostname, domain),
        )

        self.stdout.write(self.style.SUCCESS('Agent created. Store this token now; it will not be shown again.'))
        self.stdout.write(f'Machine ID: {machine.id}')
        self.stdout.write(f'Token: {token}')
