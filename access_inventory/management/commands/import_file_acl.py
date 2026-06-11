import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from access_inventory.services.import_file_acl import import_file_acl_data


class Command(BaseCommand):
    help = 'Importa file servers, shares, folders e ACLs NTFS a partir de um arquivo JSON.'

    def add_arguments(self, parser):
        parser.add_argument('json_path', help='Caminho do arquivo JSON de ACLs.')

    def handle(self, *args, **options):
        json_path = Path(options['json_path'])
        if not json_path.exists():
            raise CommandError(f'Arquivo nao encontrado: {json_path}')

        try:
            data = json.loads(json_path.read_text(encoding='utf-8-sig'))
        except json.JSONDecodeError as error:
            raise CommandError(f'JSON invalido: {error}') from error

        result = import_file_acl_data(data)

        self.stdout.write(self.style.SUCCESS('Importacao de ACL concluida.'))
        for label, item in result.stats.items():
            self.stdout.write(
                f'{label}: criados={item.created}, atualizados={item.updated}, ignorados={item.ignored}'
            )
        if result.errors_count:
            self.stdout.write(f'errors: {result.errors_count}')
