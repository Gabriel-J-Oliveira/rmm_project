from django.core.management.base import BaseCommand

from agents.models import AgentMachine
from tickets.models import Ticket, TicketCategory


class Command(BaseCommand):
    help = 'Cria chamados demo para validar o Night Owl Desk.'

    def handle(self, *args, **options):
        endpoint = AgentMachine.objects.order_by('hostname').first()
        categories = {category.name: category for category in TicketCategory.objects.all()}
        demo_rows = [
            ('DEMO - Computador lento', 'Usuario relata lentidao ao abrir aplicativos.', 'Hardware', 'normal', 'Financeiro'),
            ('DEMO - Bitdefender ausente', 'Protecao de endpoint nao identificada.', 'Seguranca', 'critical', 'TI'),
            ('DEMO - Impressora nao imprime', 'Fila travada na impressora do setor.', 'Impressora', 'normal', 'Administrativo'),
            ('DEMO - Solicitacao de acesso', 'Liberar acesso ao sistema interno.', 'Acesso', 'high', 'RH'),
            ('DEMO - Chamado critico de socio', 'Atendimento prioritario para socio/VIP.', 'Solicitacao', 'critical', 'Diretoria'),
        ]

        created = 0
        for title, description, category_name, priority, department in demo_rows:
            ticket, was_created = Ticket.objects.get_or_create(
                title=title,
                defaults={
                    'description': description,
                    'category': categories.get(category_name),
                    'priority': priority,
                    'requester_name': 'Usuario Demo',
                    'requester_department': department,
                    'requester_is_partner': department == 'Diretoria',
                    'endpoint': endpoint,
                    'source': Ticket.SOURCE_MANUAL,
                },
            )
            if was_created:
                created += 1

        self.stdout.write(self.style.SUCCESS(f'Chamados demo criados: {created}'))
