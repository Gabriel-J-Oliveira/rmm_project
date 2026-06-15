from django.core.management.base import BaseCommand

from access_inventory.services.resolve_acl_identities import resolve_acl_identities


class Command(BaseCommand):
    help = 'Resolve AclEntry.identity_sid contra usuarios e grupos do AD importados.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, help='Numero maximo de ACLs para processar.')
        parser.add_argument('--dry-run', action='store_true', help='Calcula o resultado sem gravar alteracoes.')
        parser.add_argument('--only-unknown', action='store_true', help='Processa apenas ACLs atualmente marcadas como unknown.')
        parser.add_argument('--force', action='store_true', help='Reprocessa ACLs mesmo quando resolved_at ja esta preenchido.')

    def handle(self, *args, **options):
        result = resolve_acl_identities(
            limit=options.get('limit'),
            dry_run=options['dry_run'],
            only_unknown=options['only_unknown'],
            force=options['force'],
        )

        prefix = 'Dry run concluido' if options['dry_run'] else 'Resolucao concluida'
        self.stdout.write(self.style.SUCCESS(prefix + '.'))
        self.stdout.write(f"processed={result.processed}")
        self.stdout.write(f"updated={result.updated}")
        self.stdout.write(f"resolved_users={result.resolved_users}")
        self.stdout.write(f"resolved_groups={result.resolved_groups}")
        self.stdout.write(f"unknown={result.unknown}")
