from django.core.management.base import BaseCommand, CommandError

from tickets.models import NotificationOutbox
from tickets.services.email_outbox import retry_failed_email, send_email_outbox_item


class Command(BaseCommand):
    help = 'Tenta enviar um item especifico da fila global de e-mails.'

    def add_arguments(self, parser):
        parser.add_argument('id')
        parser.add_argument('--reset-attempts', action='store_true')

    def handle(self, *args, **options):
        try:
            item = NotificationOutbox.objects.get(pk=options['id'])
        except (NotificationOutbox.DoesNotExist, ValueError):
            raise CommandError('Item de e-mail nao encontrado.')

        if item.status == NotificationOutbox.STATUS_FAILED:
            item = retry_failed_email(
                item.pk,
                actor='send_email_outbox_item',
                reset_attempts=options['reset_attempts'],
            )
        item = send_email_outbox_item(item.pk, actor='send_email_outbox_item')
        self.stdout.write(f'Status: {item.status} | Tentativas: {item.attempts}/{item.max_attempts}')
        if item.last_error:
            self.stdout.write(self.style.ERROR(item.last_error))
