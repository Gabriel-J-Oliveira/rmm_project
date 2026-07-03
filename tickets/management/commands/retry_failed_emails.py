from django.core.management.base import BaseCommand
from tickets.services.email_outbox import retry_all_failed


class Command(BaseCommand):
    help = 'Reprocessa e-mails com falha que ainda nao atingiram o limite de tentativas.'

    def handle(self, *args, **options):
        result = retry_all_failed(actor='retry_failed_emails', send_now=True)
        self.stdout.write(
            self.style.SUCCESS(
                f"Reprocessados: {result['retried']} | Enviados: {result['sent']} | Falhas: {result['failed']}"
            )
        )
