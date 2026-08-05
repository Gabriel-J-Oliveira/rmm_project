import json
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from agents.models import AgentRelease, AgentReleaseAudit
from agents.services import assert_release_immutable_compatible
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
        parser.add_argument('--release-status', default=AgentRelease.STATUS_PAUSED, choices=[choice[0] for choice in AgentRelease.STATUS_CHOICES])
        parser.add_argument('--release-notes', default='')
        parser.add_argument('--minimum-updater-version', default='')
        parser.add_argument('--force', action='store_true', help='Permite recriar draft local; nunca sobrescreve release publicada com metadados diferentes.')

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
        manifest_url = str(manifest.get('manifestUrl') or manifest.get('manifest_url') or '').strip()
        signature_url = str(manifest.get('signatureUrl') or manifest.get('signature_url') or '').strip()
        sha256 = str(manifest.get('sha256') or '').strip().lower()
        size = int(manifest.get('size') or 0)
        signature_key_id = str(manifest.get('key_id') or manifest.get('signature_key_id') or '').strip()
        signature_sha256 = str(manifest.get('signature_sha256') or '').strip().lower()
        manifest_sha256 = str(manifest.get('manifest_sha256') or '').strip().lower()
        minimum_updater_version = (options['minimum_updater_version'] or manifest.get('minimum_updater_version') or '').strip()
        self._validate_https('packageUrl', package_url)
        if checksum_url:
            self._validate_https('checksumUrl', checksum_url)
        if len(sha256) != 64 or any(char not in '0123456789abcdef' for char in sha256):
            raise CommandError('SHA-256 invalido no version.json.')
        if size <= 0:
            raise CommandError('Tamanho do pacote invalido no version.json.')

        existing = AgentRelease.objects.filter(version=version).first()
        if existing:
            incoming = {
                'package_url': package_url,
                'checksum_url': checksum_url,
                'sha256': sha256,
                'size': size,
                'manifest_url': manifest_url,
                'manifest_sha256': manifest_sha256,
                'signature_url': signature_url,
                'signature_sha256': signature_sha256,
                'signature_key_id': signature_key_id,
                'minimum_updater_version': minimum_updater_version,
            }
            try:
                assert_release_immutable_compatible(existing, incoming)
            except ValidationError as exc:
                raise CommandError(str(exc)) from exc
            if existing.status in AgentRelease.IMMUTABLE_STATUSES:
                self.stdout.write(self.style.SUCCESS(
                    f'Release {version} ja importada com mesmos metadados; operacao idempotente.'
                ))
                return
            if not options['force']:
                raise CommandError(f'Release draft {version} ja existe. Use --force para atualizar draft local.')

        release, created = AgentRelease.objects.update_or_create(
            version=version,
            defaults={
                'channel': channel,
                'source_channel': channel,
                'status': options['release_status'],
                'package_url': package_url,
                'checksum_url': checksum_url,
                'sha256': sha256,
                'size': size,
                'manifest_url': manifest_url,
                'manifest_sha256': manifest_sha256,
                'signature_url': signature_url,
                'signature_sha256': signature_sha256,
                'signature_key_id': signature_key_id,
                'signature_valid': bool(signature_key_id and signature_url),
                'legacy_unsigned': not bool(signature_key_id and signature_url),
                'released_at': timezone.now() if options['release_status'] != AgentRelease.STATUS_DRAFT else None,
                'published_by': None,
                'minimum_updater_version': minimum_updater_version,
                'release_notes': options['release_notes'] or manifest.get('notes') or '',
                'rollout_percentage': 0,
                'rollout_paused': options['release_status'] == AgentRelease.STATUS_PAUSED,
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
            f'Release {release.version} importada em {release.channel}, status {release.status}, rollout {release.rollout_percentage}%, jobs nao criados.'
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
