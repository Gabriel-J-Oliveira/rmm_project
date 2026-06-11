from django.core.management.base import BaseCommand, CommandError

from access_inventory.agent_auth import create_inventory_agent_with_token


class Command(BaseCommand):
    help = 'Cria um agente de inventario e exibe o token uma unica vez.'

    def add_arguments(self, parser):
        parser.add_argument('--name', required=True, help='Nome amigavel do agente.')
        parser.add_argument('--hostname', required=True, help='Hostname do agente.')

    def handle(self, *args, **options):
        name = options['name'].strip()
        hostname = options['hostname'].strip()
        if not name or not hostname:
            raise CommandError('--name e --hostname sao obrigatorios.')

        agent, token = create_inventory_agent_with_token(name=name, hostname=hostname)

        self.stdout.write(self.style.SUCCESS('Inventory agent criado.'))
        self.stdout.write(f'ID: {agent.id}')
        self.stdout.write(f'Name: {agent.name}')
        self.stdout.write(f'Hostname: {agent.hostname}')
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('Guarde este token agora. Ele nao sera exibido novamente.'))
        self.stdout.write(token)
