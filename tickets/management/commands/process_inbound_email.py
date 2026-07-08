from django.core.management.base import BaseCommand

from tickets.services.inbound_email import inbound_configuration_status, process_inbound_mailbox


class Command(BaseCommand):
    help = 'Processa respostas recebidas por e-mail e vincula ao chamado pelo padrao [NightOwl #ID].'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=20)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--ticket', type=int, help='Restringe o processamento a um numero de chamado.')
        parser.add_argument('--verbose', action='store_true')

    def handle(self, *args, **options):
        status = inbound_configuration_status()
        if not status['configured']:
            self.stdout.write(self.style.WARNING(
                f"Configuracao inbound incompleta: {', '.join(status['missing'])}"
            ))
            if not options['dry_run']:
                return

        result = process_inbound_mailbox(
            limit=options['limit'],
            dry_run=options['dry_run'],
            ticket=options.get('ticket'),
            verbose=options['verbose'],
        )
        self.stdout.write(f"Processed: {result['processed']}")
        self.stdout.write(f"Skipped: {result['skipped']}")
        self.stdout.write(f"Failed: {result['failed']}")
        for error in result.get('errors') or []:
            self.stdout.write(self.style.WARNING(error))
