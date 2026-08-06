import hashlib
import json
import urllib.request
from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from agents.models import AgentReleaseAudit, AgentReleaseRootKey, AgentReleaseSigningKey, AgentReleaseTrustBundle


class Command(BaseCommand):
    help = 'Importa metadados de bundle assinado de chaves publicas de release.'

    def add_arguments(self, parser):
        parser.add_argument('--metadata-url', required=True)
        parser.add_argument('--bundle-url', default='')
        parser.add_argument('--signature-url', default='')
        parser.add_argument('--status', default=AgentReleaseTrustBundle.STATUS_PUBLISHED, choices=[choice[0] for choice in AgentReleaseTrustBundle.STATUS_CHOICES])
        parser.add_argument('--actor', default='release-pipeline')
        parser.add_argument('--force', action='store_true', help='Permite atualizar draft; nao altera bundle publicado divergente.')

    def handle(self, *args, **options):
        metadata_url = options['metadata_url'].strip()
        metadata = self._read_json(metadata_url)
        bundle_url = (options['bundle_url'] or metadata.get('bundle_url') or '').strip()
        signature_url = (options['signature_url'] or metadata.get('signature_url') or '').strip()
        if not self._is_https(metadata_url) or not self._is_https(bundle_url) or not self._is_https(signature_url):
            raise CommandError('TRUST_BUNDLE_URL_INVALID: metadata, bundle e assinatura devem usar HTTPS.')

        bundle_bytes = self._read_bytes(bundle_url)
        signature_bytes = self._read_bytes(signature_url)
        bundle_sha = hashlib.sha256(bundle_bytes).hexdigest()
        signature_sha = hashlib.sha256(signature_bytes).hexdigest()
        if bundle_sha.lower() != str(metadata.get('bundle_sha256') or '').lower():
            raise CommandError('TRUST_BUNDLE_HASH_MISMATCH: bundle_sha256 divergente.')
        if signature_sha.lower() != str(metadata.get('signature_sha256') or '').lower():
            raise CommandError('TRUST_SIGNATURE_HASH_MISMATCH: signature_sha256 divergente.')

        try:
            bundle = json.loads(bundle_bytes.decode('utf-8'))
        except Exception as exc:
            raise CommandError(f'TRUST_BUNDLE_INVALID: JSON invalido: {exc}') from exc

        bundle_version = int(bundle.get('bundle_version') or metadata.get('bundle_version') or 0)
        if bundle_version <= 0:
            raise CommandError('TRUST_BUNDLE_INVALID: bundle_version ausente.')
        if int(metadata.get('bundle_version') or 0) != bundle_version:
            raise CommandError('TRUST_BUNDLE_INVALID: bundle_version divergente entre metadata e bundle.')
        root_key_id = str(metadata.get('root_key_id') or '').strip()
        if not AgentReleaseRootKey.objects.filter(root_key_id=root_key_id, status=AgentReleaseRootKey.STATUS_ACTIVE).exists():
            raise CommandError(f'TRUST_ROOT_UNKNOWN: root_key_id {root_key_id} nao esta cadastrado como raiz ativa.')

        keys = bundle.get('keys') or []
        if not keys:
            raise CommandError('TRUST_BUNDLE_INVALID: bundle sem chaves.')
        key_ids = set()
        active_key_ids = []
        revoked_key_ids = []
        for item in keys:
            key_id = str(item.get('key_id') or '').strip()
            algorithm = str(item.get('algorithm') or '').strip() or 'RSA-PSS-SHA256'
            public_xml = str(item.get('public_key_xml') or '')
            status = str(item.get('status') or 'active').strip().lower()
            if not key_id:
                raise CommandError('TRUST_BUNDLE_INVALID: key_id vazio.')
            if key_id in key_ids:
                raise CommandError(f'TRUST_BUNDLE_INVALID: key_id duplicado {key_id}.')
            key_ids.add(key_id)
            if algorithm != 'RSA-PSS-SHA256':
                raise CommandError(f'TRUST_BUNDLE_INVALID: algoritmo nao permitido para {key_id}.')
            if any(f'<{name}>' in public_xml for name in ('P', 'Q', 'DP', 'DQ', 'InverseQ', 'D')):
                raise CommandError(f'TRUST_BUNDLE_PRIVATE_PARAMETERS: {key_id} contem parametros privados.')
            if status == 'active':
                active_key_ids.append(key_id)
            elif status == 'revoked':
                revoked_key_ids.append(key_id)
            elif status != 'retired':
                raise CommandError(f'TRUST_BUNDLE_INVALID: status invalido para {key_id}.')
            existing_key = AgentReleaseSigningKey.objects.filter(key_id=key_id).first()
            if existing_key and existing_key.public_key_xml and existing_key.public_key_xml != public_xml:
                raise CommandError(f'TRUST_RELEASE_KEY_IMMUTABILITY_VIOLATION: key_id existente com outro material: {key_id}.')
            AgentReleaseSigningKey.objects.update_or_create(
                key_id=key_id,
                defaults={
                    'algorithm': algorithm,
                    'public_key_xml': public_xml,
                    'status': AgentReleaseSigningKey.STATUS_ACTIVE if status == 'active' else AgentReleaseSigningKey.STATUS_REVOKED,
                    'valid_from': self._parse_dt(item.get('valid_from')),
                    'valid_until': self._parse_dt(item.get('valid_until')),
                    'revoked_at': self._parse_dt(item.get('revoked_at')),
                },
            )

        existing = AgentReleaseTrustBundle.objects.filter(bundle_version=bundle_version).first()
        defaults = {
            'status': options['status'],
            'schema_version': int(bundle.get('schema_version') or 1),
            'root_key_id': root_key_id,
            'bundle_url': bundle_url,
            'signature_url': signature_url,
            'metadata_url': metadata_url,
            'bundle_sha256': bundle_sha,
            'signature_sha256': signature_sha,
            'size': len(bundle_bytes),
            'generated_at': self._parse_dt(bundle.get('generated_at') or metadata.get('generated_at')),
            'published_at': timezone.now() if options['status'] == AgentReleaseTrustBundle.STATUS_PUBLISHED else None,
            'valid_from': self._parse_dt(bundle.get('valid_from')),
            'valid_until': self._parse_dt(bundle.get('valid_until')),
            'active_key_ids': active_key_ids,
            'revoked_key_ids': revoked_key_ids,
        }
        if existing and existing.status in AgentReleaseTrustBundle.IMMUTABLE_STATUSES:
            divergent = [
                field for field in AgentReleaseTrustBundle.IMMUTABLE_FIELDS
                if getattr(existing, field) != defaults.get(field)
            ]
            if divergent:
                raise CommandError(f'TRUST_BUNDLE_IMMUTABILITY_VIOLATION: {", ".join(divergent)}')
            self.stdout.write(self.style.SUCCESS(f'trust bundle v{bundle_version} ja importado; no-op idempotente.'))
            return
        if existing and not options['force'] and existing.status == AgentReleaseTrustBundle.STATUS_DRAFT:
            raise CommandError('TRUST_BUNDLE_EXISTS_AS_DRAFT: use --force para atualizar draft.')

        trust_bundle, created = AgentReleaseTrustBundle.objects.update_or_create(
            bundle_version=bundle_version,
            defaults=defaults,
        )
        AgentReleaseAudit.objects.create(
            action=AgentReleaseAudit.ACTION_UPDATED,
            version=f'trust-bundle-{bundle_version}',
            reason='trust bundle imported',
            metadata={
                'event_type': 'trust.bundle.imported',
                'bundle_id': str(trust_bundle.id),
                'bundle_version': bundle_version,
                'created': created,
                'actor': options['actor'],
                'root_key_id': trust_bundle.root_key_id,
                'bundle_sha256': bundle_sha,
                'signature_sha256': signature_sha,
                'active_key_ids': active_key_ids,
                'revoked_key_ids': revoked_key_ids,
            },
        )
        self.stdout.write(self.style.SUCCESS(f'trust bundle v{bundle_version} importado com status {trust_bundle.status}.'))

    @staticmethod
    def _is_https(value: str) -> bool:
        parsed = urlparse(value)
        return parsed.scheme == 'https' and bool(parsed.netloc)

    @staticmethod
    def _read_bytes(url: str) -> bytes:
        with urllib.request.urlopen(url, timeout=20) as response:
            return response.read()

    def _read_json(self, url: str) -> dict:
        try:
            return json.loads(self._read_bytes(url).decode('utf-8'))
        except Exception as exc:
            raise CommandError(f'TRUST_METADATA_INVALID: {exc}') from exc

    @staticmethod
    def _parse_dt(value):
        if not value:
            return None
        parsed = parse_datetime(str(value))
        return parsed
