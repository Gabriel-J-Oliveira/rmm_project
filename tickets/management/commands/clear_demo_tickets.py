from django.core.management.base import BaseCommand

from tickets.models import Ticket


class Command(BaseCommand):
    help = 'Remove apenas chamados demo criados pelo seed_demo_tickets.'

    def handle(self, *args, **options):
        deleted, _ = Ticket.objects.filter(title__startswith='DEMO - ').delete()
        self.stdout.write(self.style.WARNING(f'Registros demo removidos: {deleted}'))
