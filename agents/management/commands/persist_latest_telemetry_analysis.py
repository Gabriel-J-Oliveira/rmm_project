import json
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from agents.models import AgentMachine
from agents.telemetry_persisted_analysis import analyze_endpoint


class Command(BaseCommand):
    help = 'Preview or explicitly persist one latest eligible continuous-v2 telemetry analysis per endpoint.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Persist the result; preview is read-only by default.')
        parser.add_argument('--endpoint', help='Limit to one endpoint UUID.')
        parser.add_argument('--limit', type=int, default=20)

    def handle(self, *args, **options):
        if options['apply'] and not getattr(settings, 'NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_MANUAL_APPLY_ENABLED', False):
            raise CommandError('manual_apply_disabled')
        if not 1 <= options['limit'] <= 100:
            raise CommandError('limit must be between 1 and 100')
        endpoint_id = options['endpoint']
        if endpoint_id:
            try:
                endpoint_id = uuid.UUID(endpoint_id)
            except (ValueError, TypeError, AttributeError):
                raise CommandError('endpoint must be a UUID') from None
            ids = [endpoint_id]
        else:
            ids = list(AgentMachine.objects.filter(is_active=True).order_by('pk').values_list('pk', flat=True)[:options['limit']])
        results = [analyze_endpoint(item, apply=options['apply']) for item in ids]
        self.stdout.write(json.dumps({'mode': 'apply' if options['apply'] else 'dry_run',
                                      'processed': len(results), 'results': results}, sort_keys=True))
