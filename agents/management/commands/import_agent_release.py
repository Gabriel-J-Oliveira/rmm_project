import json
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from agents.models import AgentRelease, AgentReleaseAudit
from agents.versioning import parse_semver


class Command(BaseCommand):
    help = 'Importa uma release do NightOwl Agent a partir de version.json, sem liberar rollout automaticamente.'

    def run_from_argv(self, argv):
        rewritten = []
        for argument in argv:
            if argument == '--version':
                rewritten.append('--agent-version')
            elif argument.startswith('--version='):
                rewritten.append('--agent-version=' + argument.split('=', 1)[1])
            else:
                rewritten.append(argument)
        super().run_from_argv(rewritten)

    def add_arguments(self, parser):
        parser.add_argument('--agent-version', '--release-version', dest='version', required=True)
        parser.add_argument('--channel', default=AgentRelease.CHANNEL_DEVELOPMENT, choices=[choice[0] for choice in AgentRelease.CHANNEL_CHOICES])
        parser.add_argument('--version-json', required=True, help='Caminho local ou URL HTTPS do version.json gerado pelo pipeline.')
        parser.add_argument('--release-notes', default='')
        parser.add_argument('--minimum-updater-version', default='')
        parser.add_argument('--force', action='store_true', help='Atualiza uma release existente apenas em ambiente de desenvolvimento.')

    def handle(self, *args, **options):
        version = options['version'].strip()
        channel = options['channel']
        if parse_semver(version) is None:
            raise CommandError(f'Versao invalida: {version}')
        manifest = self._read_manifest(options['version_json'])
        manifest_version = str(manifest.get('version') or '').strip()
        if manifest_version != version:
            raise CommandError(f'version.json declara {manifest_version}, mas --version informou {version}.')

        package_url = str(manifest.get('packageUrl') or manifest.get('package_url') or '').strip()
        checksum_url = str(manifest.get('checksumUrl') or manifest.get('checksum_url') or '').strip()
        sha256 = str(manifest.get('sha256') or '').strip().lower()
        size = int(manifest.get('size') or 0)
        minimum_updater_version = (options['minimum_updater_version'] or manifest.get('minimum_updater_version') or '').strip()
        self._validate_https('packageUrl', package_url)
        if checksum_url:
            self._validate_https('checksumUrl', checksum_url)
        if len(sha256) != 64 or any(char not in '0123456789abcdef' for char in sha256):
            raise CommandError('SHA-256 invalido no version.json.')
        if size <= 0:
            raise CommandError('Tamanho do pacote invalido no version.json.')

        existing = AgentRelease.objects.filter(version=version).first()
        if existing and not options['force']:
            raise CommandError(f'Release {version} ja existe. Use --force apenas em desenvolvimento.')

        release, created = AgentRelease.objects.update_or_create(
            version=version,
            defaults={
                'channel': channel,
                'status': AgentRelease.STATUS_PAUSED,
                'package_url': package_url,
                'checksum_url': checksum_url,
                'sha256': sha256,
                'size': size,
                'released_at': timezone.now(),
                'minimum_updater_version': minimum_updater_version,
                'release_notes': options['release_notes'] or manifest.get('notes') or '',
                'rollout_percentage': 0,
                'rollout_paused': True,
                'mandatory': bool(manifest.get('mandatory') or manifest.get('force')),
                'revoked': False,
            },
        )
        AgentReleaseAudit.objects.create(
            action=AgentReleaseAudit.ACTION_CREATED if created else AgentReleaseAudit.ACTION_UPDATED,
            release=release,
            version=release.version,
            channel_after=release.channel,
            rollout_after=release.rollout_percentage,
            reason='import_agent_release',
            metadata={'version_json': self._sanitize_source(options['version_json']), 'created': created},
        )
        self.stdout.write(self.style.SUCCESS(
            f'Release {release.version} importada em {release.channel}, pausada, rollout {release.rollout_percentage}%, jobs nao criados.'
        ))

    def _read_manifest(self, source):
        parsed = urlparse(source)
        try:
            if parsed.scheme and parsed.netloc:
                if parsed.scheme != 'https':
                    raise CommandError('version-json remoto deve usar HTTPS.')
                with urllib.request.urlopen(source, timeout=15) as response:
                    return json.loads(response.read().decode('utf-8-sig'))
            path = Path(source)
            return json.loads(path.read_text(encoding='utf-8-sig'))
        except OSError as exc:
            raise CommandError(f'Falha ao ler version.json: {exc}') from exc
        except json.JSONDecodeError as exc:
            raise CommandError(f'version.json invalido: {exc}') from exc

    def _validate_https(self, field, value):
        parsed = urlparse(value)
        if parsed.scheme != 'https' or not parsed.netloc:
            raise CommandError(f'{field} deve ser uma URL HTTPS absoluta.')

    def _sanitize_source(self, source):
        return str(source).split('?', 1)[0]
