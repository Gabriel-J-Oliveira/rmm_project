"""Read-only identity evidence; deliberately excludes credentials and raw payloads."""

import json
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.models import Count, Max, Min
from django.utils import timezone

from agents.identity import meaningful_serial, normalized_domain, normalized_hostname
from agents.models import AgentEnrollmentLog, AgentMachine, AuditEvent, InventorySnapshot


INCIDENT_ENDPOINTS = [
    '610fb016-5485-4aa2-a7d0-10892c92bdbd',
    'ab9352a5-6e6f-4652-90e5-b3dee5a6f4e3',
]


class Command(BaseCommand):
    help = 'Audit identity collisions without writes or credential/raw-payload output.'

    def add_arguments(self, parser):
        parser.add_argument('--endpoint', action='append', default=[], type=str,
                            help='Endpoint UUID; repeatable. Defaults to the two incident endpoints.')

    @transaction.atomic
    def handle(self, *args, **options):
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        try:
            report = self.build_report(options['endpoint'] or INCIDENT_ENDPOINTS)
        except (ValueError, TypeError, ValidationError) as exc:
            raise CommandError('Invalid endpoint identifier.') from exc
        self.stdout.write(json.dumps(report, cls=DjangoJSONEncoder, indent=2))

    def build_report(self, endpoint_ids):
        mid_groups, name_groups, serial_groups = defaultdict(list), defaultdict(list), defaultdict(list)
        missing_mid = []
        rows = AgentMachine.objects.values('id', 'machine_id', 'hostname', 'domain', 'serial_number')
        total = 0
        for row in rows.iterator():
            total += 1
            endpoint = str(row['id'])
            mid = (row['machine_id'] or '').strip().casefold()
            if not mid:
                missing_mid.append(endpoint)
            else:
                mid_groups[mid].append(endpoint)
            domain = normalized_domain(row['domain'])
            name_groups[(normalized_hostname(row['hostname'], domain), domain)].append(endpoint)
            if serial := meaningful_serial(row['serial_number']):
                serial_groups[serial].append(endpoint)
        report = {
            'collected_at_utc': timezone.now(), 'mode': 'READ_ONLY',
            'total_endpoints': total, 'missing_machine_id': missing_mid,
            'duplicate_machine_id_endpoints': [ids for ids in mid_groups.values() if len(ids) > 1],
            'duplicate_hostname_domain_endpoints': [ids for ids in name_groups.values() if len(ids) > 1],
            'duplicate_meaningful_serial_endpoints': [ids for ids in serial_groups.values() if len(ids) > 1],
            'endpoints': [],
        }
        for endpoint_id in endpoint_ids:
            machine = AgentMachine.objects.filter(pk=endpoint_id).values(
                'id', 'machine_id', 'hostname', 'fqdn', 'domain', 'is_active',
                'first_seen_at', 'last_seen_at', 'serial_number').first()
            if machine is None:
                report['endpoints'].append({'id': endpoint_id, 'found': False})
                continue
            serial = machine.pop('serial_number')
            machine['serial_is_auxiliary_only'] = True
            machine['serial_generic_or_missing'] = not bool(meaningful_serial(serial))
            # Never dump messages, full metadata, raw payloads or authentication columns.
            logs = []
            for row in AgentEnrollmentLog.objects.filter(endpoint_id=endpoint_id).order_by('created_at').values(
                    'id', 'hostname', 'domain', 'status', 'created_at', 'metadata'):
                metadata = row.pop('metadata') or {}
                row['identity_source'] = metadata.get('identity_source') if metadata.get('identity_source') in {
                    'new', 'machine_id', 'hostname_domain', 'serial_number'} else None
                row['credential_transition'] = metadata.get('created_or_existing') if metadata.get('created_or_existing') in {
                    'created', 'existing_rotated', 'existing_recovered', 'existing_preserved'} else None
                logs.append(row)
            snapshots = list(InventorySnapshot.objects.filter(machine_id=endpoint_id).values(
                'hostname', 'domain').annotate(count=Count('pk'), first=Min('created_at'), last=Max('created_at')))
            events = list(AuditEvent.objects.filter(endpoint_id=endpoint_id,
                event_type__in=['agent.enrolled', 'agent.deployment.enrolled',
                                'endpoint.identity_conflict', 'endpoint.identity_renamed']).order_by('created_at').values(
                                    'id', 'created_at', 'event_type', 'severity'))
            machine.update(enrollments=logs, snapshot_identities=snapshots, audit_events=events)
            report['endpoints'].append(machine)
        return report
