from django.core.management.base import BaseCommand
from django.db import transaction

from tickets.models import Ticket
from tickets.services.user_directory import find_ad_user_for_ticket


class Command(BaseCommand):
    help = 'Link existing Desk tickets to AD users using requester username, UPN or e-mail.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Persist matches. Dry-run is the default.')

    def handle(self, *args, **options):
        apply = bool(options['apply'])
        totals = {
            'associated': 0,
            'ignored_existing': 0,
            'ambiguous': 0,
            'no_match': 0,
            'no_identity': 0,
        }
        updates = []
        queryset = Ticket.objects.select_related('requester_ad_user').order_by('number')
        for ticket in queryset:
            if ticket.requester_ad_user_id:
                totals['ignored_existing'] += 1
                continue
            match = find_ad_user_for_ticket(ticket)
            if match.user:
                totals['associated'] += 1
                updates.append((ticket, match.user))
            elif match.status == 'ambiguous':
                totals['ambiguous'] += 1
                self.stdout.write(
                    f'AMBIGUO #{ticket.number}: {ticket.requester_username or ticket.requester_email} -> '
                    f'{", ".join(user.sam_account_name for user in match.candidates)}'
                )
            elif match.status == 'no_identity':
                totals['no_identity'] += 1
            else:
                totals['no_match'] += 1

        if apply and updates:
            with transaction.atomic():
                for ticket, user in updates:
                    ticket.requester_ad_user = user
                    ticket.save(update_fields=['requester_ad_user', 'updated_at'])

        mode = 'APPLY' if apply else 'DRY-RUN'
        self.stdout.write(self.style.SUCCESS(f'{mode} concluido.'))
        for key, value in totals.items():
            self.stdout.write(f'{key}: {value}')
        if not apply:
            self.stdout.write('Use --apply para persistir as associacoes sem sobrescrever vinculos existentes.')
