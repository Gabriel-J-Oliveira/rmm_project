from datetime import timedelta

from django.core.management.base import CommandError, BaseCommand
from django.utils import timezone

from agents.models import AgentEnrollmentToken, AgentManualValidationToken


class Command(BaseCommand):
    help = 'Create a short-lived single-use manual validation token and print it once.'

    def add_arguments(self, parser):
        parser.add_argument('--name', default='')
        parser.add_argument('--expires-minutes', type=int, default=5)
        parser.add_argument('--enrollment-token-prefix', default='')
        parser.add_argument('--notes', default='')

    def handle(self, *args, **options):
        expires_minutes = options['expires_minutes']
        if expires_minutes <= 0:
            raise CommandError('--expires-minutes must be greater than zero.')

        enrollment_token = None
        prefix = options['enrollment_token_prefix'].strip()
        if prefix:
            matches = AgentEnrollmentToken.objects.filter(prefix__startswith=prefix)
            if matches.count() == 0:
                raise CommandError('No enrollment token found with this prefix.')
            if matches.count() > 1:
                raise CommandError('More than one enrollment token matches this prefix. Use a longer prefix.')
            enrollment_token = matches.first()

        expires_at = timezone.now() + timedelta(minutes=expires_minutes)
        manual_token, token = AgentManualValidationToken.create_with_token(
            name=options['name'],
            expires_at=expires_at,
            enrollment_token=enrollment_token,
            notes=options['notes'],
        )

        self.stdout.write(self.style.SUCCESS('Manual validation token criado.'))
        self.stdout.write(f'ID: {manual_token.id}')
        self.stdout.write(f'Token: {token}')
        self.stdout.write(f'Prefixo: {manual_token.prefix}')
        self.stdout.write(f'Expira em: {expires_at.isoformat()}')
        if enrollment_token:
            self.stdout.write(f'Vinculado ao enrollment token: {enrollment_token.prefix}')
        self.stdout.write('Este token sera exibido apenas uma vez.')
