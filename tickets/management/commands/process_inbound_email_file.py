from django.core.management.base import BaseCommand, CommandError

from tickets.services.inbound_email import process_inbound_email_file


class Command(BaseCommand):
    help = 'Processa um arquivo .eml local pelo pipeline inbound do NightOwl Desk.'

    def add_arguments(self, parser):
        parser.add_argument('fixture', help='Caminho do arquivo .eml.')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--apply', action='store_true', help='Aplica as alteracoes no banco.')
        parser.add_argument('--ticket', type=int, help='Restringe o teste a um numero de chamado.')

    def handle(self, *args, **options):
        if options['dry_run'] and options['apply']:
            raise CommandError('Use apenas --dry-run ou --apply.')
        dry_run = not options['apply']
        result = process_inbound_email_file(
            options['fixture'],
            dry_run=dry_run,
            ticket=options.get('ticket'),
        )
        self.stdout.write(f"Status: {result.get('status')}")
        if result.get('ticket'):
            self.stdout.write(f"Ticket: #{result['ticket']}")
        if result.get('message_id'):
            self.stdout.write(f"Message-ID: {result['message_id']}")
        if result.get('reason'):
            self.stdout.write(self.style.WARNING(f"Reason: {result['reason']}"))
        if result.get('body'):
            self.stdout.write('Body:')
            self.stdout.write(result['body'])
        if result.get('attachments'):
            self.stdout.write(f"Attachments: {', '.join(result['attachments'])}")
