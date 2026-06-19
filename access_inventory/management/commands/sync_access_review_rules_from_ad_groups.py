from django.core.management.base import BaseCommand, CommandError

from access_inventory.models import AccessReviewPlan
from access_inventory.services.sync_access_review_rules_from_ad_groups import (
    sync_access_review_rules_from_ad_groups,
)


class Command(BaseCommand):
    help = 'Gera regras propostas de reestruturacao a partir de grupos AD existentes.'

    def add_arguments(self, parser):
        parser.add_argument('--plan-id', type=int, required=True, help='ID do AccessReviewPlan.')
        parser.add_argument('--area', default='', help='Area/ramo do plano. Ex.: Administrativo.')
        parser.add_argument(
            '--group-prefix',
            action='append',
            default=[],
            help='Prefixo de grupo AD. Pode ser informado mais de uma vez.',
        )
        parser.add_argument(
            '--default-permission',
            default='',
            help='Permissao padrao quando o grupo nao indicar RO/RW/FULL/CUSTOM.',
        )
        parser.add_argument('--dry-run', action='store_true', help='Mostra o que seria feito sem gravar.')
        parser.add_argument(
            '--clear-existing',
            action='store_true',
            help='Remove AccessReviewRule das pastas afetadas antes de recriar.',
        )

    def handle(self, *args, **options):
        try:
            plan = AccessReviewPlan.objects.get(pk=options['plan_id'])
        except AccessReviewPlan.DoesNotExist as exc:
            raise CommandError(f'AccessReviewPlan nao encontrado: {options["plan_id"]}') from exc

        result = sync_access_review_rules_from_ad_groups(
            plan=plan,
            area=options['area'],
            group_prefixes=options['group_prefix'],
            default_permission=options['default_permission'],
            dry_run=options['dry_run'],
            clear_existing=options['clear_existing'],
        )

        mode = 'DRY-RUN' if result.dry_run else 'EXECUCAO'
        self.stdout.write(self.style.SUCCESS(f'Sync de regras por grupos AD concluido ({mode}).'))
        self.stdout.write(f'Plano: {plan.id} - {plan.name}')
        self.stdout.write(f'Area: {result.area or "todas"}')
        self.stdout.write(f'Grupos candidatos encontrados: {result.groups_found}')
        self.stdout.write(f'Grupos mapeados: {result.groups_mapped}')
        self.stdout.write(f'Grupos ambiguos: {result.groups_ambiguous}')
        self.stdout.write(f'Grupos sem pasta: {result.groups_without_folder}')
        self.stdout.write(f'Grupos sem permissao: {result.groups_without_permission}')
        self.stdout.write(f'Principals criados: {result.principals_created}')
        self.stdout.write(f'Principals atualizados: {result.principals_updated}')
        self.stdout.write(f'Regras criadas: {result.rules_created}')
        self.stdout.write(f'Regras atualizadas: {result.rules_updated}')
        self.stdout.write(f'Regras removidas por clear-existing: {result.rules_deleted}')
        self.stdout.write(f'Ignoradas: {result.ignored}')
        self.stdout.write(f'Erros: {len(result.errors)}')

        ok_decisions = [decision for decision in result.decisions if decision.status == 'ok']
        if ok_decisions:
            self.stdout.write('')
            self.stdout.write('Exemplos:')
            for decision in ok_decisions[:12]:
                self.stdout.write(
                    f'- {decision.group.name or decision.group.sam_account_name} -> '
                    f'{decision.folder.proposed_path} -> {decision.permission_level.upper()} ({decision.action})'
                )

        ambiguous = [decision for decision in result.decisions if decision.status == 'ambiguous']
        if ambiguous:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('Ambiguos:'))
            for decision in ambiguous[:12]:
                candidates = ', '.join(folder.proposed_path for folder in decision.candidates[:5])
                self.stdout.write(f'- {decision.group.name}: {candidates}')

        without_folder = [decision for decision in result.decisions if decision.status == 'without_folder']
        if without_folder:
            self.stdout.write('')
            self.stdout.write('Sem pasta:')
            for decision in without_folder[:12]:
                self.stdout.write(f'- {decision.group.name}')

        without_permission = [decision for decision in result.decisions if decision.status == 'without_permission']
        if without_permission:
            self.stdout.write('')
            self.stdout.write('Sem permissao:')
            for decision in without_permission[:12]:
                self.stdout.write(f'- {decision.group.name}')
