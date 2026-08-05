import hashlib
import json
import urllib.request
from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError

from agents.models import AgentRelease


class Command(BaseCommand):
    help = 'Verifica consistencia basica de uma release importada.'

    def add_arguments(self, parser):
        parser.add_argument('--agent-version', '--release-version', dest='version', required=True)
        parser.add_argument('--skip-remote', action='store_true')

    def handle(self, *args, **options):
        release = AgentRelease.objects.filter(version=options['version']).first()
        if release is None:
            raise CommandError(f'Release {options["version"]} nao encontrada.')
        errors = []
        if release.revoked and release.status != AgentRelease.STATUS_REVOKED:
            errors.append('revoked=true com status diferente de revoked')
        if release.status == AgentRelease.STATUS_REVOKED and not release.revocation_reason:
            errors.append('release revogada sem motivo')
        if release.status in {AgentRelease.STATUS_PUBLISHED, AgentRelease.STATUS_PAUSED} and not release.legacy_unsigned and not release.signature_valid:
            errors.append('release assinada sem assinatura valida registrada')
        if release.channel == AgentRelease.CHANNEL_STABLE and release.legacy_unsigned:
            errors.append('release stable nao pode ser legacy_unsigned')
        if not options['skip_remote']:
            self._verify_remote_json('manifest', release.manifest_url, release.manifest_sha256, errors)
            self._verify_remote_json('signature', release.signature_url, release.signature_sha256, errors, allow_json=False)
        if errors:
            raise CommandError('; '.join(errors))
        self.stdout.write(self.style.SUCCESS(
            f'Release {release.version} OK: channel={release.channel} status={release.status} sha256={release.sha256[:12]}...'
        ))

    def _verify_remote_json(self, label, url, expected_sha, errors, allow_json=True):
        if not url:
            return
        parsed = urlparse(url)
        if parsed.scheme != 'https':
            errors.append(f'{label}: URL nao HTTPS')
            return
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                content = response.read()
        except OSError as exc:
            errors.append(f'{label}: falha ao baixar: {exc}')
            return
        if expected_sha:
            actual = hashlib.sha256(content).hexdigest()
            if actual.lower() != expected_sha.lower():
                errors.append(f'{label}: SHA256 divergente')
        if allow_json:
            try:
                json.loads(content.decode('utf-8'))
            except Exception as exc:
                errors.append(f'{label}: JSON invalido: {exc}')
