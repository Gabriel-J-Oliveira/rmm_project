import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from access_inventory.services.import_ad_inventory import import_ad_inventory_data


class Command(BaseCommand):
    help = 'Importa OUs, usuarios, grupos e memberships do AD a partir de um arquivo JSON.'

    def add_arguments(self, parser):
        parser.add_argument('json_path', help='Caminho do arquivo JSON de inventario AD.')

    def handle(self, *args, **options):
        json_path = Path(options['json_path'])
        if not json_path.exists():
            raise CommandError(f'Arquivo nao encontrado: {json_path}')

        try:
            data = json.loads(json_path.read_text(encoding='utf-8-sig'))
        except json.JSONDecodeError as error:
            raise CommandError(f'JSON invalido: {error}') from error

        result = import_ad_inventory_data(data)

        self.stdout.write(self.style.SUCCESS('Importacao AD concluida.'))
        for label, item in result.stats.items():
            self.stdout.write(
                f'{label}: criados={item.created}, atualizados={item.updated}, ignorados={item.ignored}'
            )
        if result.errors_count:
            self.stdout.write(f'errors: {result.errors_count}')
