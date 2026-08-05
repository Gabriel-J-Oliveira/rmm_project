from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from agents.models import AgentRelease
from agents.services import revoke_agent_release


class Command(BaseCommand):
    help = 'Revoga uma release do NightOwl Agent e bloqueia novos jobs.'

    def add_arguments(self, parser):
        parser.add_argument('--agent-version', '--release-version', dest='version', required=True)
        parser.add_argument('--reason', required=True)
        parser.add_argument('--replacement-version', default='')
        parser.add_argument('--actor', default='release-bot')

    def handle(self, *args, **options):
        release = AgentRelease.objects.filter(version=options['version']).first()
        if release is None:
            raise CommandError(f'Release {options["version"]} nao encontrada.')
        replacement = None
        if options['replacement_version']:
            replacement = AgentRelease.objects.filter(version=options['replacement_version']).first()
            if replacement is None:
                raise CommandError(f'Release substituta {options["replacement_version"]} nao encontrada.')
        actor = self._actor(options['actor'])
        try:
            revoke_agent_release(release, actor, options['reason'], replacement)
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f'Release {release.version} revogada.'))

    def _actor(self, username):
        User = get_user_model()
        user, _ = User.objects.get_or_create(username=username, defaults={'is_staff': True, 'is_superuser': True})
        if not user.is_staff or not user.is_superuser:
            user.is_staff = True
            user.is_superuser = True
            user.save(update_fields=['is_staff', 'is_superuser'])
        return user
