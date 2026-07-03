from django.core.management.base import BaseCommand

from tickets.services.email_outbox import process_pending_emails


class Command(BaseCommand):
    help = 'Processa e envia e-mails pendentes da fila global do Night Owl.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)

    def handle(self, *args, **options):
        result = process_pending_emails(limit=options['limit'], actor='process_email_outbox')
        self.stdout.write(
            self.style.SUCCESS(
                f"Processados: {result['processed']} | Enviados: {result['sent']} | Falhas: {result['failed']}"
            )
        )
