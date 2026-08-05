from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from agents.models import AgentRelease
from agents.services import promote_agent_release


class Command(BaseCommand):
    help = 'Promove uma release do NightOwl Agent entre canais, sem reconstruir ou copiar artefatos.'

    def add_arguments(self, parser):
        parser.add_argument('--version', required=True)
        parser.add_argument('--to-channel', required=True, choices=[AgentRelease.CHANNEL_PILOT, AgentRelease.CHANNEL_STABLE])
        parser.add_argument('--rollout-percentage', type=int, default=0)
        parser.add_argument('--paused', action='store_true')
        parser.add_argument('--reason', required=True)
        parser.add_argument('--actor', default='release-bot')
        parser.add_argument('--allow-direct-stable', action='store_true')

    def handle(self, *args, **options):
        release = AgentRelease.objects.filter(version=options['version']).first()
        if release is None:
            raise CommandError(f'Release {options["version"]} nao encontrada.')
        actor = self._actor(options['actor'])
        try:
            promote_agent_release(
                release,
                options['to_channel'],
                actor,
                rollout_percentage=options['rollout_percentage'],
                rollout_paused=options['paused'],
                approval_reason=options['reason'],
                allow_direct_stable=options['allow_direct_stable'],
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f'Release {release.version} promovida para {release.channel}; rollout={release.rollout_percentage} paused={release.rollout_paused}.'
        ))

    def _actor(self, username):
        User = get_user_model()
        user, _ = User.objects.get_or_create(username=username, defaults={'is_staff': True, 'is_superuser': True})
        if not user.is_staff or not user.is_superuser:
            user.is_staff = True
            user.is_superuser = True
            user.save(update_fields=['is_staff', 'is_superuser'])
        return user
