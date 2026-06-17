from django.core.management.base import BaseCommand, CommandError

from access_inventory.models import AccessReviewPlan
from access_inventory.services.seed_access_review_folders import seed_access_review_folders


class Command(BaseCommand):
    help = 'Popula pastas planejadas de um AccessReviewPlan a partir dos Folders ja coletados.'

    def add_arguments(self, parser):
        plan_group = parser.add_mutually_exclusive_group(required=True)
        plan_group.add_argument('--plan-id', type=int, help='ID do AccessReviewPlan.')
        plan_group.add_argument('--plan-name', help='Nome exato do AccessReviewPlan.')
        parser.add_argument('--root-path', default='', help='Limita e normaliza a partir de uma raiz logica.')
        parser.add_argument('--share', default='', help='Limita por nome da share ou UNC path.')
        parser.add_argument('--area-name', default='', help='Area fixa para todas as pastas importadas.')
        parser.add_argument(
            '--area-mode',
            choices=['simple', 'share', 'general'],
            default='simple',
            help='Como preencher area_name quando --area-name nao for informado.',
        )
        parser.add_argument('--dry-run', action='store_true', help='Mostra o que seria feito sem gravar.')
        parser.add_argument('--replace', action='store_true', help='Remove pastas planejadas existentes antes de recriar.')
        parser.add_argument(
            '--force-replace',
            action='store_true',
            help='Permite --replace mesmo quando existem regras vinculadas. Use com cuidado.',
        )

    def get_plan(self, options):
        if options.get('plan_id'):
            return AccessReviewPlan.objects.get(pk=options['plan_id'])
        return AccessReviewPlan.objects.get(name=options['plan_name'])

    def handle(self, *args, **options):
        try:
            plan = self.get_plan(options)
        except AccessReviewPlan.DoesNotExist as error:
            raise CommandError('AccessReviewPlan nao encontrado.') from error
        except AccessReviewPlan.MultipleObjectsReturned as error:
            raise CommandError('Mais de um AccessReviewPlan encontrado com esse nome.') from error

        result = seed_access_review_folders(
            plan=plan,
            root_path=options['root_path'],
            share=options['share'],
            area_name=options['area_name'],
            area_mode=options['area_mode'],
            dry_run=options['dry_run'],
            replace=options['replace'],
            force_replace=options['force_replace'],
        )

        if result.errors:
            for item in result.errors:
                self.stderr.write(self.style.ERROR(item))
            raise CommandError('Seed de pastas planejadas bloqueado.')

        mode = 'DRY-RUN' if result.dry_run else 'EXECUCAO'
        self.stdout.write(self.style.SUCCESS(f'Seed de pastas planejadas concluido ({mode}).'))
        self.stdout.write(f'plan: {result.plan.id} - {result.plan.name}')
        self.stdout.write(f'folders encontrados: {result.found}')
        self.stdout.write(f'criados: {result.created}')
        self.stdout.write(f'atualizados: {result.updated}')
        self.stdout.write(f'ignorados: {result.ignored}')
        self.stdout.write(f'erros: {len(result.errors)}')
        self.stdout.write(f'parent warnings: {len(result.parent_warnings)}')

        if result.examples:
            self.stdout.write('exemplos:')
            for item in result.examples[:5]:
                self.stdout.write(
                    f'  - {item.proposed_path} | area={item.area_name} | parent={item.parent_path or "-"}'
                )

        for warning in result.parent_warnings[:10]:
            self.stdout.write(self.style.WARNING(warning))
