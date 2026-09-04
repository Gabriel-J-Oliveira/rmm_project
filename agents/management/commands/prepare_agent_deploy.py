import csv
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from agents.audit import create_audit_event
from agents.models import AgentMachine, AuditEvent
from agents.services import build_fqdn


class Command(BaseCommand):
    help = 'Prepare a CSV with per-endpoint agent tokens for controlled batch deployment.'

    def add_arguments(self, parser):
        parser.add_argument('--input', required=True, help='Path to .txt or .csv with hostnames.')
        parser.add_argument('--output', required=True, help='Output CSV path.')
        parser.add_argument('--domain', default='', help='Default domain when input has no domain column.')
        parser.add_argument('--server-url', required=True)
        parser.add_argument('--source-path', default=r'\\192.168.104.120\controlsul\Comum\_Agents')
        parser.add_argument('--install-path', default=r'C:\RMM')
        parser.add_argument('--force-rotate-token', action='store_true')
        parser.add_argument(
            '--allow-plaintext-agent-token-export',
            action='store_true',
            help='Allow high-risk plaintext agent token export for legacy deployments.',
        )
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        input_path = Path(options['input'])
        output_path = Path(options['output'])
        if not input_path.exists():
            raise CommandError(f'Input file not found: {input_path}')

        rows = self.read_input(input_path, options['domain'])
        seen = set()
        unique_rows = []
        duplicates = 0
        for row in rows:
            hostname = row['hostname'].strip().upper()
            domain = row['domain'].strip().lower()
            if not hostname:
                continue
            key = (hostname, domain)
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            unique_rows.append({'hostname': hostname, 'domain': domain})

        if options['dry_run']:
            created = 0
            existing = 0
            for row in unique_rows:
                if AgentMachine.objects.filter(hostname__iexact=row['hostname'], domain__iexact=row['domain']).exists():
                    existing += 1
                else:
                    created += 1
            self.stdout.write(self.style.WARNING('Dry run: no records or CSV created.'))
            self.stdout.write(f'total processed: {len(unique_rows)}')
            self.stdout.write(f'would create: {created}')
            self.stdout.write(f'would reuse existing: {existing}')
            self.stdout.write(f'duplicates ignored: {duplicates}')
            return

        output_rows = []
        created = 0
        existing = 0
        rotated = 0
        token_unavailable = 0
        plaintext_export = bool(options['allow_plaintext_agent_token_export'])

        if plaintext_export:
            self.stdout.write(self.style.WARNING(
                'SECURITY WARNING: plaintext agent token export enabled for legacy deployment CSV.'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                'Plaintext agent token export blocked by default; agent_token column will be empty.'
            ))

        for row in unique_rows:
            machine = AgentMachine.objects.filter(
                hostname__iexact=row['hostname'],
                domain__iexact=row['domain'],
            ).first()
            token = ''
            status = 'existing'
            if machine:
                existing += 1
                if options['force_rotate_token']:
                    generated_token = AgentMachine.generate_token()
                    machine.set_agent_token(generated_token)
                    machine.save(update_fields=['agent_token_hash', 'updated_at'])
                    status = 'existing_rotated'
                    rotated += 1
                    if plaintext_export:
                        token = generated_token
                else:
                    token_unavailable += 1
            else:
                generated_token = AgentMachine.generate_token()
                machine = AgentMachine(
                    hostname=row['hostname'],
                    domain=row['domain'],
                    fqdn=build_fqdn(row['hostname'], row['domain']),
                )
                machine.set_agent_token(generated_token)
                machine.save()
                status = 'created'
                created += 1
                if plaintext_export:
                    token = generated_token

            create_audit_event(
                event_type='agent.deploy_prepared',
                title='Deploy de agente preparado',
                description=f'Deploy preparado para {row["hostname"]}.',
                severity=AuditEvent.SEVERITY_INFO,
                actor_type=AuditEvent.ACTOR_SYSTEM,
                actor_name='prepare_agent_deploy',
                endpoint=machine,
                metadata={
                    'hostname': row['hostname'],
                    'domain': row['domain'],
                    'output': str(output_path),
                    'created_or_existing': status,
                    'token_included': bool(token),
                    'plaintext_token_export_allowed': plaintext_export,
                },
            )

            output_rows.append({
                'hostname': row['hostname'],
                'domain': row['domain'],
                'machine_id': str(machine.id),
                'agent_token': token,
                'server_url': options['server_url'],
                'source_path': options['source_path'],
                'install_path': options['install_path'],
                'created_or_existing': status,
            })

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                'hostname',
                'domain',
                'machine_id',
                'agent_token',
                'server_url',
                'source_path',
                'install_path',
                'created_or_existing',
            ])
            writer.writeheader()
            writer.writerows(output_rows)
        if plaintext_export:
            self.restrict_output_file(output_path)

        self.stdout.write(self.style.SUCCESS('Agent deploy CSV prepared.'))
        if plaintext_export:
            self.stdout.write(self.style.WARNING('Plaintext tokens were written only to the CSV. Protect and delete this file after use.'))
        else:
            self.stdout.write('Plaintext tokens were not exported.')
        self.stdout.write(f'output: {output_path}')
        self.stdout.write(f'total processed: {len(unique_rows)}')
        self.stdout.write(f'created: {created}')
        self.stdout.write(f'existing: {existing}')
        self.stdout.write(f'tokens rotated: {rotated}')
        self.stdout.write(f'duplicates ignored: {duplicates}')
        self.stdout.write(f'existing rows without token: {token_unavailable}')
        if token_unavailable:
            self.stdout.write(self.style.WARNING(
                'Existing endpoints keep token hashes only; use --force-rotate-token with --allow-plaintext-agent-token-export only for legacy deployments that require fresh plaintext tokens.'
            ))

    def read_input(self, path, default_domain):
        if path.suffix.lower() == '.csv':
            with path.open('r', encoding='utf-8-sig', newline='') as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames or 'hostname' not in [name.lower() for name in reader.fieldnames]:
                    raise CommandError('CSV input must contain a hostname column.')
                rows = []
                for item in reader:
                    normalized = {str(key).lower(): value for key, value in item.items()}
                    rows.append({
                        'hostname': normalized.get('hostname', ''),
                        'domain': normalized.get('domain') or default_domain,
                    })
                return rows

        rows = []
        with path.open('r', encoding='utf-8-sig') as handle:
            for line in handle:
                hostname = line.strip()
                if hostname and not hostname.startswith('#'):
                    rows.append({'hostname': hostname, 'domain': default_domain})
        return rows

    def restrict_output_file(self, path):
        try:
            if os.name == 'nt':
                return
            path.chmod(0o600)
        except OSError:
            self.stdout.write(self.style.WARNING('Could not restrict output CSV permissions automatically.'))
