import csv
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from agents.models import AgentMachine, hash_agent_token


AGENT_TOKEN_PATTERN = re.compile(r'rmm_live_[A-Za-z0-9_\-]{8,}')


class Command(BaseCommand):
    help = 'Rotaciona tokens de agentes comprometidos a partir de artefatos locais, sem exibir valores.'

    def add_arguments(self, parser):
        parser.add_argument('--token-csv', action='append', default=[], help='CSV legado com coluna agent_token.')
        parser.add_argument('--token-script', action='append', default=[], help='Script legado contendo token literal.')
        parser.add_argument('--apply', action='store_true', help='Aplica a rotacao. Sem esta flag, executa apenas dry-run.')

    def handle(self, *args, **options):
        tokens = self._load_unique_tokens(options['token_csv'], options['token_script'])
        if not tokens:
            self.stdout.write('tokens_loaded=0')
            return

        apply_changes = bool(options['apply'])
        matched = 0
        rotated = 0
        unmatched = 0

        for token in tokens:
            token_hash = hash_agent_token(token)
            with transaction.atomic():
                machine = AgentMachine.objects.select_for_update().filter(agent_token_hash=token_hash).first()
                if machine is None:
                    unmatched += 1
                    self.stdout.write('endpoint_id=; hostname=; matched=false; rotated=false')
                    continue
                matched += 1
                if apply_changes:
                    new_token = AgentMachine.generate_token()
                    machine.set_agent_token(new_token)
                    machine.save(update_fields=['agent_token_hash', 'updated_at'])
                    rotated += 1
                self.stdout.write(
                    f'endpoint_id={machine.id}; hostname={machine.hostname}; matched=true; rotated={str(apply_changes).lower()}'
                )

        self.stdout.write(f'tokens_loaded={len(tokens)}')
        self.stdout.write(f'matched={matched}')
        self.stdout.write(f'rotated={rotated}')
        self.stdout.write(f'unmatched={unmatched}')

    def _load_unique_tokens(self, csv_paths, script_paths):
        tokens = []
        seen = set()
        for item in csv_paths:
            path = Path(item)
            if not path.exists():
                raise CommandError(f'Token CSV not found: {path}')
            for token in self._tokens_from_csv(path):
                if token not in seen:
                    seen.add(token)
                    tokens.append(token)
        for item in script_paths:
            path = Path(item)
            if not path.exists():
                raise CommandError(f'Token script not found: {path}')
            for token in self._tokens_from_script(path):
                if token not in seen:
                    seen.add(token)
                    tokens.append(token)
        return tokens

    def _tokens_from_csv(self, path):
        with path.open('r', encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return
            fields = {name.lower(): name for name in reader.fieldnames}
            token_field = fields.get('agent_token')
            if not token_field:
                return
            for row in reader:
                token = str(row.get(token_field) or '').strip()
                if token:
                    yield token

    def _tokens_from_script(self, path):
        content = path.read_text(encoding='utf-8', errors='ignore')
        for match in AGENT_TOKEN_PATTERN.finditer(content):
            yield match.group(0)
