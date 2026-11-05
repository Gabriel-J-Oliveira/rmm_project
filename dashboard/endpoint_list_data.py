"""Read-only, bounded inventory selection exclusively for the endpoint list."""

from django.db.models import BooleanField, Case, F, Func, IntegerField, OuterRef, Q, Subquery, When
from django.db.models.fields.json import KeyTransform
from django.db.models.functions import Coalesce

from agents.models import AgentMachine, InventorySnapshot


class JsonHasContent(Func):
    output_field = BooleanField()

    def __init__(self, expression, *, object_only=False):
        super().__init__(expression)
        self.object_only = object_only

    def as_postgresql(self, compiler, connection, **extra_context):
        sql, params = compiler.compile(self.source_expressions[0])
        if self.object_only:
            return f"(jsonb_typeof({sql}) = 'object' AND {sql} <> '{{}}'::jsonb)", params * 2
        return (f"({sql} NOT IN ('null'::jsonb, '{{}}'::jsonb, '[]'::jsonb, "
                "'false'::jsonb, '0'::jsonb, '\"\"'::jsonb))"), params

    def as_sqlite(self, compiler, connection, **extra_context):
        expression = self.source_expressions[0]
        if self.object_only:
            sql, params, keys = expression.preprocess_lhs(compiler, connection)
            path = connection.ops.compile_json_path(keys)
            return (f"(JSON_TYPE({sql}, %s) = 'object' AND JSON_EXTRACT({sql}, %s) <> '{{}}')",
                    (*params, path, *params, path))
        sql, params = compiler.compile(expression)
        return f"({sql} NOT IN ('null', '{{}}', '[]', 'false', '\"\"', '', 0))", params


class SoftwareCount(Func):
    output_field = IntegerField()

    def as_postgresql(self, compiler, connection, **extra_context):
        sql, params = compiler.compile(self.source_expressions[0])
        return (f"CASE WHEN jsonb_typeof({sql}) = 'array' THEN jsonb_array_length({sql}) "
                f"WHEN {sql} IS NULL OR {sql} = 'null'::jsonb THEN 0 ELSE 1 END"), params * 4

    def as_sqlite(self, compiler, connection, **extra_context):
        sql, params = compiler.compile(self.source_expressions[0])
        return (f"CASE WHEN JSON_TYPE({sql}) = 'array' THEN JSON_ARRAY_LENGTH({sql}) "
                f"WHEN {sql} IS NULL OR JSON_TYPE({sql}) = 'null' THEN 0 ELSE 1 END"), params * 4


def load_endpoint_list_data():
    # Match the existing detail-selection horizon, without transferring 30 full
    # historical payloads per machine. The detail screen itself stays unchanged.
    recent = InventorySnapshot.objects.filter(machine_id=OuterRef('machine_id')).order_by(
        '-received_at', '-pk',
    ).values('pk')[:30]
    history = InventorySnapshot.objects.filter(machine_id=OuterRef('pk'))
    usable = history.filter(pk__in=Subquery(recent)).alias(
        collections_present=JsonHasContent(KeyTransform('collections', 'raw_payload'), object_only=True),
        disks_present=JsonHasContent(F('disks')),
        software_present=JsonHasContent(F('installed_software')),
        defender_present=JsonHasContent(F('defender_status')),
    ).filter(
        Q(collections_present=True) | Q(disks_present=True)
        | Q(software_present=True) | Q(defender_present=True),
    ).order_by('-received_at', '-pk').values('pk')[:1]
    latest = history.order_by('-received_at', '-pk').values('pk')[:1]
    endpoints = list(AgentMachine.objects.annotate(
        list_snapshot_id=Coalesce(Subquery(usable), Subquery(latest)),
    ).order_by('hostname', 'domain', 'pk'))
    ids = [endpoint.list_snapshot_id for endpoint in endpoints if endpoint.list_snapshot_id]
    snapshots = InventorySnapshot.objects.filter(pk__in=ids).only(
        'id', 'machine_id', 'received_at', 'disks', 'defender_status',
        'os_name', 'domain', 'logged_user', 'ips',
    ).annotate(list_software_count=SoftwareCount(F('installed_software')), list_system=Case(
        When(raw_payload__collections__has_key='system', then=KeyTransform(
            'system', KeyTransform('collections', 'raw_payload'),
        )),
        default=KeyTransform('system', KeyTransform('full_inventory', KeyTransform('collections', 'raw_payload'))),
    ))
    by_machine = {}
    for snapshot in snapshots:
        # Classification needs only this fragment, never raw software/telemetry.
        snapshot.raw_payload = {'collections': {'system': snapshot.list_system}}
        # The list uses the count only; Defender is classified from observed
        # security data, never from the presence of antivirus software (PR #9).
        snapshot.installed_software = []
        by_machine[snapshot.machine_id] = snapshot
    return endpoints, by_machine
