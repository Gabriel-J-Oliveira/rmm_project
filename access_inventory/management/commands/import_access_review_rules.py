from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from access_inventory.models import AccessReviewPlan
from access_inventory.services.import_access_review_rules import import_access_review_rules_from_csv


class Command(BaseCommand):
    help = 'Importa regras propostas de reestruturacao de acessos a partir de CSV.'

    def add_arguments(self, parser):
        plan_group = parser.add_mutually_exclusive_group(required=True)
        plan_group.add_argument('--plan-id', type=int, help='ID do AccessReviewPlan.')
        plan_group.add_argument('--plan-name', help='Nome exato do AccessReviewPlan.')
        parser.add_argument('--file', required=True, help='Caminho do CSV separado por virgula.')
        parser.add_argument('--dry-run', action='store_true', help='Valida e mostra resumo sem gravar.')

    def get_plan(self, options):
        if options.get('plan_id'):
            return AccessReviewPlan.objects.get(pk=options['plan_id'])
        return AccessReviewPlan.objects.get(name=options['plan_name'])

    def handle(self, *args, **options):
        csv_path = Path(options['file'])
        if not csv_path.exists():
            raise CommandError(f'Arquivo nao encontrado: {csv_path}')

        try:
            plan = self.get_plan(options)
        except AccessReviewPlan.DoesNotExist as error:
            raise CommandError('AccessReviewPlan nao encontrado.') from error
        except AccessReviewPlan.MultipleObjectsReturned as error:
            raise CommandError('Mais de um AccessReviewPlan encontrado com esse nome.') from error

        try:
            result = import_access_review_rules_from_csv(
                plan=plan,
                csv_path=csv_path,
                dry_run=options['dry_run'],
            )
        except ValueError as error:
            raise CommandError(str(error)) from error

        mode = 'DRY-RUN' if result.dry_run else 'EXECUCAO'
        self.stdout.write(self.style.SUCCESS(f'Importacao de regras propostas concluida ({mode}).'))
        self.stdout.write(f'plan: {result.plan.id} - {result.plan.name}')
        self.stdout.write(f'linhas lidas: {result.rows_read}')
        self.stdout.write(f'principals criados: {result.principals_created}')
        self.stdout.write(f'principals atualizados: {result.principals_updated}')
        self.stdout.write(f'regras criadas: {result.rules_created}')
        self.stdout.write(f'regras atualizadas: {result.rules_updated}')
        self.stdout.write(f'ignorados: {result.ignored}')
        self.stdout.write(f'erros: {len(result.errors)}')

        if result.examples:
            self.stdout.write('exemplos:')
            for item in result.examples[:10]:
                self.stdout.write(f'  - {item}')

        for error in result.errors[:20]:
            self.stderr.write(self.style.WARNING(error))
