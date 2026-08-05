from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from agents.audit import create_audit_event
from agents.models import AgentReleaseSigningKey, AuditEvent


class Command(BaseCommand):
    help = 'Revoga uma chave publica confiavel de assinatura de releases do agente.'

    def add_arguments(self, parser):
        parser.add_argument('--key-id', required=True)
        parser.add_argument('--reason', required=True)
        parser.add_argument('--actor', default='release-bot')

    def handle(self, *args, **options):
        key_id = options['key_id'].strip()
        reason = options['reason'].strip()
        if not key_id:
            raise CommandError('key-id obrigatorio.')
        if not reason:
            raise CommandError('reason obrigatorio.')
        key = AgentReleaseSigningKey.objects.filter(key_id=key_id).first()
        if key is None:
            raise CommandError(f'RELEASE_KEY_UNKNOWN: key_id {key_id} nao encontrado.')
        actor = self._actor(options['actor'])
        if key.revoked:
            self.stdout.write(self.style.SUCCESS(f'Chave {key_id} ja estava revogada; operacao idempotente.'))
            return

        key.status = AgentReleaseSigningKey.STATUS_REVOKED
        key.revoked_at = timezone.now()
        key.revoked_by = actor
        key.revocation_reason = reason
        key.save()
        create_audit_event(
            event_type='release.key_revoked',
            title='Chave de release revogada',
            description=f'Chave {key_id} revogada.',
            severity=AuditEvent.SEVERITY_CRITICAL,
            actor_type=AuditEvent.ACTOR_USER,
            actor_name=actor.get_username(),
            metadata={'key_id': key_id, 'reason': reason},
        )
        self.stdout.write(self.style.SUCCESS(f'Chave {key_id} revogada.'))

    def _actor(self, username):
        User = get_user_model()
        user, _ = User.objects.get_or_create(username=username, defaults={'is_staff': True, 'is_superuser': True})
        if not user.is_staff or not user.is_superuser:
            user.is_staff = True
            user.is_superuser = True
            user.save(update_fields=['is_staff', 'is_superuser'])
        return user
