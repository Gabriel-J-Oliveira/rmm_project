from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from agents.models import AgentEnrollmentToken


class Command(BaseCommand):
    help = 'Create a temporary enrollment token and print it once.'

    def add_arguments(self, parser):
        parser.add_argument('--name', required=True)
        parser.add_argument('--expires-hours', type=int, default=168)
        parser.add_argument('--max-uses', type=int, default=None)
        parser.add_argument('--allowed-domain', default='')
        parser.add_argument('--notes', default='')

    def handle(self, *args, **options):
        expires_hours = options['expires_hours']
        expires_at = timezone.now() + timedelta(hours=expires_hours) if expires_hours > 0 else None
        enrollment_token, token = AgentEnrollmentToken.create_with_token(
            name=options['name'],
            expires_at=expires_at,
            max_uses=options['max_uses'],
            allowed_domain=options['allowed_domain'].strip().lower(),
            notes=options['notes'],
        )

        self.stdout.write(self.style.SUCCESS('Enrollment token criado.'))
        self.stdout.write(f'ID: {enrollment_token.id}')
        self.stdout.write(f'Token: {token}')
        self.stdout.write(f'Prefixo: {enrollment_token.prefix}')
        self.stdout.write(f'Expira em: {expires_at.isoformat() if expires_at else "sem expiracao"}')
        self.stdout.write(f'Usos maximos: {enrollment_token.max_uses if enrollment_token.max_uses is not None else "sem limite"}')
        self.stdout.write('Guarde este token agora. Ele nao sera exibido novamente.')
