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
        parser.add_argument('--file', required=True, help='Caminho do arquivo CSV.')
        parser.add_argument('--dry-run', action='store_true', help='Valida sem gravar alteracoes.')

    def handle(self, *args, **options):
        plan = self.get_plan(options)
        csv_path = Path(options['file'])
        if not csv_path.exists():
            raise CommandError(f'Arquivo CSV nao encontrado: {csv_path}')

        try:
            result = import_access_review_rules_from_csv(
                plan,
                csv_path,
                dry_run=options['dry_run'],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        mode = 'DRY-RUN' if result.dry_run else 'EXECUCAO'
        self.stdout.write(self.style.SUCCESS(f'Importacao de regras propostas concluida ({mode}).'))
        self.stdout.write(f'plan: {plan.id} - {plan.name}')
        self.stdout.write(f'linhas lidas: {result.rows_read}')
        self.stdout.write(f'principals criados: {result.principals_created}')
        self.stdout.write(f'principals atualizados: {result.principals_updated}')
        self.stdout.write(f'regras criadas: {result.rules_created}')
        self.stdout.write(f'regras atualizadas: {result.rules_updated}')
        self.stdout.write(f'ignorados: {result.ignored}')
        self.stdout.write(f'erros: {len(result.errors)}')

        if result.resolution_messages:
            self.stdout.write('')
            self.stdout.write('Resolucao de usuarios:')
            for message in result.resolution_messages[:20]:
                self.stdout.write(f'- {message}')
            if len(result.resolution_messages) > 20:
                self.stdout.write(f'- ... +{len(result.resolution_messages) - 20} mensagens')

        if result.examples:
            self.stdout.write('')
            self.stdout.write('Exemplos:')
            for example in result.examples:
                self.stdout.write(f'- {example}')

        if result.errors:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('Erros encontrados:'))
            for error in result.errors:
                self.stdout.write(f'- {error}')

    def get_plan(self, options):
        if options.get('plan_id'):
            try:
                return AccessReviewPlan.objects.get(pk=options['plan_id'])
            except AccessReviewPlan.DoesNotExist as exc:
                raise CommandError(f'AccessReviewPlan nao encontrado: {options["plan_id"]}') from exc

        try:
            return AccessReviewPlan.objects.get(name=options['plan_name'])
        except AccessReviewPlan.DoesNotExist as exc:
            raise CommandError(f'AccessReviewPlan nao encontrado: {options["plan_name"]}') from exc
        except AccessReviewPlan.MultipleObjectsReturned as exc:
            raise CommandError(
                f'Mais de um AccessReviewPlan encontrado com nome: {options["plan_name"]}'
            ) from exc
