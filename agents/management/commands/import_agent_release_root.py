import hashlib
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from agents.models import AgentReleaseAudit, AgentReleaseRootKey


PRIVATE_RSA_PARAMETERS = ('P', 'Q', 'DP', 'DQ', 'InverseQ', 'D')


class Command(BaseCommand):
    help = 'Importa uma raiz publica confiavel para validar bundles de chaves de release.'

    def add_arguments(self, parser):
        parser.add_argument('--roots-file', required=True, help='Caminho local do release-trust-roots.json publico.')
        parser.add_argument('--root-key-id', required=True)
        parser.add_argument(
            '--status',
            default=AgentReleaseRootKey.STATUS_ACTIVE,
            choices=[choice[0] for choice in AgentReleaseRootKey.STATUS_CHOICES],
        )
        parser.add_argument('--actor', default='release-operator')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        roots_file = Path(options['roots_file'])
        root_key_id = str(options['root_key_id'] or '').strip()
        status = options['status']
        if not root_key_id:
            raise CommandError('TRUST_ROOT_KEY_ID_REQUIRED: informe --root-key-id.')

        document = self._read_roots_file(roots_file)
        root = self._select_root(document, root_key_id)
        public_key_xml = str(root.get('public_key_xml') or '').strip()
        algorithm = str(root.get('algorithm') or '').strip()
        source_status = str(root.get('status') or AgentReleaseRootKey.STATUS_ACTIVE).strip().lower()
        if algorithm != 'RSA-PSS-SHA256':
            raise CommandError(f'TRUST_ROOT_ALGORITHM_INVALID: algoritmo nao permitido para {root_key_id}.')
        if source_status not in {AgentReleaseRootKey.STATUS_ACTIVE, AgentReleaseRootKey.STATUS_REVOKED, AgentReleaseRootKey.STATUS_RETIRED}:
            raise CommandError(f'TRUST_ROOT_STATUS_INVALID: status invalido para {root_key_id}.')
        self._assert_public_key(public_key_xml, root_key_id)
        fingerprint = hashlib.sha256(public_key_xml.encode('utf-8')).hexdigest()

        existing = AgentReleaseRootKey.objects.filter(root_key_id=root_key_id).first()
        if existing:
            if existing.public_key_xml and existing.public_key_xml != public_key_xml:
                raise CommandError(f'TRUST_ROOT_IMMUTABILITY_VIOLATION: root_key_id {root_key_id} ja existe com outro material publico.')
            if existing.status == AgentReleaseRootKey.STATUS_REVOKED and status == AgentReleaseRootKey.STATUS_ACTIVE:
                raise CommandError(f'TRUST_ROOT_REVOKED: raiz {root_key_id} esta revogada e nao pode voltar a ativa automaticamente.')
            if existing.algorithm != algorithm:
                raise CommandError(f'TRUST_ROOT_IMMUTABILITY_VIOLATION: algoritmo divergente para {root_key_id}.')
            if existing.status == status:
                self.stdout.write(self.style.SUCCESS(
                    f'Raiz {root_key_id} ja registrada; no-op idempotente. fingerprint={fingerprint[:16]}...'
                ))
                return

        if options['dry_run']:
            action = 'update' if existing else 'create'
            self.stdout.write(self.style.SUCCESS(
                f'DRY RUN: raiz {root_key_id} validada para {action}. status={status} fingerprint={fingerprint[:16]}...'
            ))
            return

        with transaction.atomic():
            root_key, created = AgentReleaseRootKey.objects.update_or_create(
                root_key_id=root_key_id,
                defaults={
                    'algorithm': algorithm,
                    'public_key_xml': public_key_xml,
                    'status': status,
                },
            )
            AgentReleaseAudit.objects.create(
                action=AgentReleaseAudit.ACTION_UPDATED,
                version=f'trust-root-{root_key.root_key_id}',
                reason='trust root imported',
                metadata={
                    'event_type': 'trust.root.imported',
                    'actor': options['actor'],
                    'root_key_id': root_key.root_key_id,
                    'status': root_key.status,
                    'created': created,
                    'fingerprint_sha256': fingerprint,
                    'source': str(roots_file),
                },
            )

        self.stdout.write(self.style.SUCCESS(
            f'Raiz {root_key_id} importada com status {status}. fingerprint={fingerprint[:16]}...'
        ))

    def _read_roots_file(self, path: Path) -> dict:
        if not path.exists():
            raise CommandError(f'TRUST_ROOTS_FILE_MISSING: arquivo nao encontrado: {path}')
        data = path.read_bytes()
        if data.startswith(b'\xef\xbb\xbf'):
            raise CommandError('TRUST_ROOTS_FILE_BOM: release-trust-roots.json deve ser UTF-8 sem BOM.')
        if not data.strip():
            raise CommandError('TRUST_ROOTS_FILE_EMPTY: arquivo vazio.')
        try:
            document = json.loads(data.decode('utf-8'))
        except UnicodeDecodeError as exc:
            raise CommandError(f'TRUST_ROOTS_FILE_ENCODING_INVALID: {exc}') from exc
        except json.JSONDecodeError as exc:
            raise CommandError(f'TRUST_ROOTS_FILE_INVALID: JSON invalido: {exc}') from exc
        if int(document.get('schema_version') or 0) != 1:
            raise CommandError('TRUST_ROOTS_SCHEMA_INVALID: schema_version deve ser 1.')
        roots = document.get('roots') or []
        if not roots:
            raise CommandError('TRUST_ROOTS_EMPTY: arquivo sem roots.')
        seen = set()
        for item in roots:
            key_id = str(item.get('key_id') or '').strip()
            if not key_id:
                raise CommandError('TRUST_ROOT_KEY_ID_INVALID: root com key_id vazio.')
            if key_id in seen:
                raise CommandError(f'TRUST_ROOT_DUPLICATE: root_key_id duplicado {key_id}.')
            seen.add(key_id)
        return document

    def _select_root(self, document: dict, root_key_id: str) -> dict:
        for item in document.get('roots') or []:
            if str(item.get('key_id') or '').strip() == root_key_id:
                return item
        raise CommandError(f'TRUST_ROOT_NOT_FOUND: root_key_id {root_key_id} nao encontrado no arquivo.')

    def _assert_public_key(self, public_key_xml: str, root_key_id: str) -> None:
        if not public_key_xml:
            raise CommandError(f'TRUST_ROOT_PUBLIC_KEY_MISSING: raiz {root_key_id} sem public_key_xml.')
        for name in PRIVATE_RSA_PARAMETERS:
            if f'<{name}>' in public_key_xml:
                raise CommandError(f'TRUST_ROOT_PRIVATE_PARAMETERS: raiz {root_key_id} contem parametro privado {name}.')
