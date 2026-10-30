import json
from django.core.management.base import BaseCommand, CommandError
from agents.automation import scheduler_round


class Command(BaseCommand):
    help = 'Process one bounded, transactional round of remote job automations.'

    def add_arguments(self, parser):
        parser.add_argument('--limit',type=int,default=20)

    def handle(self,*args,**options):
        try:
            result=scheduler_round(limit=options['limit'])
        except ValueError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(json.dumps(result,sort_keys=True))
        if result['failed']:
            raise CommandError('scheduler_partial_failure')
