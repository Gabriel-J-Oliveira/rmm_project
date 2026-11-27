"""Validation and capability contracts for typed internal AgentJobs."""
import json
import re
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from config.authz import is_nightowl_technical_user
from .models import AgentMachine, AgentJob, InventorySnapshot, AuditEvent
from .services import create_audit_event
from .service_collection_policy import service_collection_endpoint_allowed

SOFTWARE_OPERATION_CAPABILITIES = {
    'install': 'software_msi_install:1',
    'update': 'software_msi_update:1',
    'uninstall': 'software_msi_uninstall:1',
}


def _ordinal_sort_key(value):
    return value.encode('utf-16-be', errors='surrogatepass')


def validate_internal_payload(kind, value):
    if not isinstance(value, dict):
        raise ValueError('invalid_payload')
    if kind == 'collect_services':
        if value.get('schema_version') == 2:
            if set(value) != {'schema_version', 'page_size', 'after_name', 'expected_snapshot_id',
                              'page_index', 'rows_collected', 'expected_total_services',
                              'expected_collected_at', 'endpoint_machine_id'}:
                raise ValueError('invalid_fields')
            after_name = value.get('after_name')
            snapshot_id = value.get('expected_snapshot_id')
            page_index = value.get('page_index')
            rows_collected = value.get('rows_collected')
            expected_total = value.get('expected_total_services')
            expected_at = value.get('expected_collected_at')
            if (type(value.get('schema_version')) is not int or value['schema_version'] != 2
                    or type(value.get('page_size')) is not int or value['page_size'] != 20
                    or not isinstance(after_name, str) or len(after_name) > 256
                    or not isinstance(snapshot_id, str) or (snapshot_id and not re.fullmatch(r'[0-9a-f]{32}', snapshot_id))
                    or bool(after_name) != bool(snapshot_id)
                    or type(page_index) is not int or not 1 <= page_index <= 40
                    or type(rows_collected) is not int or not 0 <= rows_collected < 800
                    or rows_collected != (page_index - 1) * 20
                    or type(expected_total) is not int or not 0 <= expected_total <= 800
                    or not isinstance(expected_at, str) or len(expected_at) > 40
                    or (page_index == 1 and (expected_total != 0 or expected_at != ''))
                    or (page_index > 1 and (expected_total < 1 or not expected_at))
                    or not isinstance(value.get('endpoint_machine_id'), str)
                    or len(value['endpoint_machine_id']) > 255):
                raise ValueError('invalid_service_page')
            return dict(value)
        if value.get('schema_version') == 3:
            allowed = {'schema_version', 'page_size', 'max_page_bytes', 'after_name', 'expected_snapshot_id',
                       'page_index', 'rows_collected', 'expected_total_services', 'expected_collected_at',
                       'endpoint_machine_id'}
            after_name = value.get('after_name')
            snapshot_id = value.get('expected_snapshot_id')
            page_index = value.get('page_index')
            rows_collected = value.get('rows_collected')
            expected_total = value.get('expected_total_services')
            expected_at = value.get('expected_collected_at')
            if (set(value) != allowed
                    or type(value.get('schema_version')) is not int or value['schema_version'] != 3
                    or type(value.get('page_size')) is not int or value['page_size'] != 100
                    or type(value.get('max_page_bytes')) is not int or not 8192 <= value['max_page_bytes'] <= 60 * 1024
                    or not isinstance(after_name, str) or len(after_name) > 256
                    or not isinstance(snapshot_id, str) or (snapshot_id and not re.fullmatch(r'[0-9a-f]{32}', snapshot_id))
                    or bool(after_name) != bool(snapshot_id)
                    or type(page_index) is not int or not 1 <= page_index <= 800
                    or type(rows_collected) is not int or not 0 <= rows_collected < 800
                    or (page_index == 1 and rows_collected != 0)
                    or (page_index > 1 and not page_index - 1 <= rows_collected <= (page_index - 1) * 100)
                    or type(expected_total) is not int or not 0 <= expected_total <= 800
                    or (page_index == 1 and (expected_total != 0 or expected_at != ''))
                    or (page_index > 1 and (expected_total < 1 or rows_collected >= expected_total
                                            or not isinstance(expected_at, str) or not expected_at))
                    or not isinstance(expected_at, str) or len(expected_at) > 40
                    or not isinstance(value.get('endpoint_machine_id'), str)
                    or not 1 <= len(value['endpoint_machine_id']) <= 255):
                raise ValueError('invalid_service_page')
            return dict(value)
        if set(value) - {'names', 'limit', 'include_details'}:
            raise ValueError('invalid_fields')
        names = value.get('names', [])
        limit = value.get('limit', 50)
        details = value.get('include_details', False)
        if (not isinstance(names, list) or len(names)>10 or
            any(not isinstance(name,str) or not re.fullmatch(r'[\w.-]{1,256}',name) for name in names) or
            type(limit) is not int or not 1<=limit<=100 or type(details) is not bool):
            raise ValueError('invalid_service_query')
        return {'names':names,'limit':limit,'include_details':details}
    if kind == 'install_software':
        if value.get('schema_version') == 2:
            allowed = {
                'schema_version', 'operation_id', 'operation', 'request_id', 'package_uuid',
                'artifact_sha256', 'artifact_revision', 'profile_revision', 'profile_sha256',
            }
            if set(value) != allowed:
                raise ValueError('invalid_fields')
            try:
                import uuid
                parsed_ids = {key: str(uuid.UUID(value[key])) for key in ('operation_id', 'request_id', 'package_uuid')}
            except (ValueError, TypeError, AttributeError):
                raise ValueError('invalid_operation_identity') from None
            if (type(value.get('schema_version')) is not int or value['schema_version'] != 2
                    or value.get('operation') not in {'install', 'update', 'uninstall'}
                    or any(parsed_ids[key] != value[key] for key in parsed_ids)
                    or not re.fullmatch(r'[0-9a-f]{64}', str(value.get('artifact_sha256', '')))
                    or not re.fullmatch(r'[0-9a-f]{64}', str(value.get('profile_sha256', '')))
                    or type(value.get('artifact_revision')) is not int or value['artifact_revision'] < 1
                    or type(value.get('profile_revision')) is not int or value['profile_revision'] < 1):
                raise ValueError('invalid_software_operation')
            return dict(value)
        if set(value) != {'package_id'} or not isinstance(value['package_id'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}',value['package_id']):
            raise ValueError('invalid_package_identity')
        if value['package_id'] not in getattr(settings,'NIGHTOWL_APPROVED_SOFTWARE_IDS',()):
            raise ValueError('package_not_approved')
        return dict(value)
    raise ValueError('unsupported_internal_job')


def required_internal_capability(kind, payload):
    if kind == 'install_software' and isinstance(payload, dict) and payload.get('schema_version') == 2:
        return SOFTWARE_OPERATION_CAPABILITIES.get(payload.get('operation'), 'software_operation_invalid')
    if kind == 'collect_services' and isinstance(payload, dict) and payload.get('schema_version') == 3:
        return 'collect_services_paged:3'
    if kind == 'collect_services' and isinstance(payload, dict) and payload.get('schema_version') == 2:
        return 'collect_services_paged:2'
    return kind


def internal_capability(endpoint,kind):
    heartbeat=InventorySnapshot.objects.filter(machine=endpoint,raw_payload__snapshot_source='heartbeat',
        received_at__gte=timezone.now()-timedelta(minutes=15)).order_by('-received_at').first()
    raw=heartbeat.raw_payload if heartbeat else {}
    agent=raw.get('agent') if isinstance(raw.get('agent'),dict) else {}
    return (not endpoint.has_terminal_lifecycle and raw.get('machine_id')==endpoint.machine_id and
            raw.get('agent_version')==endpoint.agent_version and
            isinstance(agent.get('capabilities'),list) and kind in agent['capabilities'])


def validate_internal_completion(job,result):
    if not isinstance(result,dict) or result.get('machine_id')!=job.endpoint.machine_id or result.get('output_truncated'):
        raise ValueError('internal_identity_or_output_invalid')
    output=result.get('output')
    if not isinstance(output,dict):
        raise ValueError('internal_confirmation_missing')
    if job.job_type=='collect_services':
        rows=output.get('services')
        if output.get('schema_version') == 2:
            allowed_output = {'schema_version', 'read_only', 'snapshot_id', 'collected_at', 'total_services',
                              'services', 'next_after_name', 'has_more'}
            states = {'Running', 'Stopped', 'StartPending', 'StopPending', 'ContinuePending', 'PausePending', 'Paused', 'unknown'}
            startup = {'Boot', 'System', 'Automatic', 'Manual', 'Disabled', 'unknown'}
            if (set(output) != allowed_output or type(output.get('schema_version')) is not int
                    or output.get('schema_version') != 2 or output.get('read_only') is not True
                    or not isinstance(output.get('snapshot_id'), str)
                    or not re.fullmatch(r'[0-9a-f]{32}', output['snapshot_id'])
                    or not isinstance(output.get('collected_at'), str) or len(output['collected_at']) > 40
                    or type(output.get('total_services')) is not int or not 0 <= output['total_services'] <= 800
                    or not isinstance(rows, list) or len(rows) > 20
                    or output.get('has_more') is not (output.get('next_after_name') is not None)
                    or (output.get('next_after_name') is not None and not isinstance(output.get('next_after_name'), str))):
                raise ValueError('service_confirmation_invalid')
            names = []
            for row in rows:
                if (not isinstance(row, dict) or set(row) != {'name', 'display_name', 'status', 'startup_type'}
                        or not isinstance(row.get('name'), str) or not row['name'] or len(row['name']) > 256
                        or not isinstance(row.get('display_name'), str) or len(row['display_name']) > 256
                        or row.get('status') not in states or row.get('startup_type') not in startup):
                    raise ValueError('service_confirmation_invalid')
                names.append(row['name'].casefold())
            if names != sorted(names) or len(names) != len(set(names)):
                raise ValueError('service_order_invalid')
            if output['has_more'] and (not rows or output['next_after_name'] != rows[-1]['name']):
                raise ValueError('service_cursor_invalid')
            if not output['has_more'] and output['next_after_name'] is not None:
                raise ValueError('service_cursor_invalid')
            from django.utils.dateparse import parse_datetime
            collected_at = parse_datetime(output['collected_at']) if isinstance(output.get('collected_at'), str) else None
            cumulative = job.payload['rows_collected'] + len(rows)
            if (collected_at is None or not timezone.is_aware(collected_at)
                    or output['has_more'] is not (cumulative < output['total_services'])
                    or cumulative > output['total_services']
                    or (output['has_more'] and cumulative >= output['total_services'])
                    or (not output['has_more'] and cumulative != output['total_services'])
                    or (job.payload['expected_total_services'] and job.payload['expected_total_services'] != output['total_services'])
                    or (job.payload['expected_collected_at'] and job.payload['expected_collected_at'] != output['collected_at'])
                    or (job.payload['expected_snapshot_id'] and job.payload['expected_snapshot_id'] != output['snapshot_id'])):
                raise ValueError('service_timestamp_invalid')
        elif output.get('schema_version') == 3:
            allowed_output = {'schema_version', 'read_only', 'snapshot_id', 'collected_at', 'total_services',
                              'services', 'next_after_name', 'has_more', 'page_index', 'rows_collected', 'page_size'}
            states = {'Running', 'Stopped', 'StartPending', 'StopPending', 'ContinuePending', 'PausePending', 'Paused', 'unknown'}
            startup = {'Boot', 'System', 'Automatic', 'Manual', 'Disabled', 'unknown'}
            payload = job.payload if isinstance(job.payload, dict) else {}
            if (set(output) != allowed_output or type(output.get('schema_version')) is not int
                    or output.get('schema_version') != 3 or output.get('read_only') is not True
                    or not isinstance(output.get('snapshot_id'), str)
                    or not re.fullmatch(r'[0-9a-f]{32}', output['snapshot_id'])
                    or not isinstance(output.get('collected_at'), str) or len(output['collected_at']) > 40
                    or type(output.get('total_services')) is not int or not 0 <= output['total_services'] <= 800
                    or type(output.get('page_index')) is not int or output['page_index'] != payload.get('page_index')
                    or type(output.get('page_size')) is not int or output['page_size'] != 100
                    or not isinstance(rows, list) or len(rows) > payload.get('page_size', 0)
                    or type(output.get('rows_collected')) is not int
                    or output.get('has_more') is not (output.get('next_after_name') is not None)
                    or (output.get('next_after_name') is not None and not isinstance(output.get('next_after_name'), str))):
                raise ValueError('service_confirmation_invalid')
            names = []
            folded_names = []
            for row in rows:
                if (not isinstance(row, dict) or set(row) != {'name', 'display_name', 'status', 'startup_type'}
                        or not isinstance(row.get('name'), str) or not row['name'] or len(row['name']) > 256
                        or not isinstance(row.get('display_name'), str) or len(row['display_name']) > 256
                        or row.get('status') not in states or row.get('startup_type') not in startup):
                    raise ValueError('service_confirmation_invalid')
                names.append(row['name'])
                folded_names.append(row['name'].casefold())
            if names != sorted(names, key=_ordinal_sort_key) or len(folded_names) != len(set(folded_names)):
                raise ValueError('service_order_invalid')
            if (output['has_more'] and (not rows or output['next_after_name'] != rows[-1]['name'])
                    or not output['has_more'] and output['next_after_name'] is not None):
                raise ValueError('service_cursor_invalid')
            if payload.get('after_name') and rows and _ordinal_sort_key(names[0]) <= _ordinal_sort_key(payload['after_name']):
                raise ValueError('service_cursor_invalid')
            from django.utils.dateparse import parse_datetime
            collected_at = parse_datetime(output['collected_at'])
            cumulative = payload.get('rows_collected', -1) + len(rows)
            if (collected_at is None or not timezone.is_aware(collected_at)
                    or output['rows_collected'] != cumulative
                    or output['has_more'] is not (cumulative < output['total_services'])
                    or cumulative > output['total_services']
                    or (output['has_more'] and not rows)
                    or (not output['has_more'] and cumulative != output['total_services'])
                    or (payload.get('expected_total_services') and payload['expected_total_services'] != output['total_services'])
                    or (payload.get('expected_collected_at') and payload['expected_collected_at'] != output['collected_at'])
                    or (payload.get('expected_snapshot_id') and payload['expected_snapshot_id'] != output['snapshot_id'])):
                raise ValueError('service_snapshot_changed')
            encoded_size = len(json.dumps(output, ensure_ascii=True, separators=(',', ':')).encode('utf-8'))
            if encoded_size > payload.get('max_page_bytes', 0):
                raise ValueError('service_page_too_large')
        elif (output.get('schema_version')!=1 or output.get('read_only') is not True or not isinstance(rows,list) or
              len(rows)>job.payload.get('limit',50) or any(not isinstance(row,dict) or not isinstance(row.get('name'),str) for row in rows)):
            raise ValueError('service_confirmation_invalid')
    elif job.job_type=='install_software':
        if isinstance(job.payload, dict) and job.payload.get('schema_version') == 2:
            from .software_operation_contract import validate_software_operation_completion
            validate_software_operation_completion(job, result)
            return
        if (output.get('package_id')!=job.payload.get('package_id') or output.get('verified') is not True or
            output.get('installed') is not True or output.get('scope')!='machine'):
            raise ValueError('software_confirmation_invalid')


@transaction.atomic
def create_internal_job(operator, endpoint_id, kind, payload, *, request_id=''):
    if not is_nightowl_technical_user(operator) or not operator.has_perm('agents.add_agentjob'):
        raise PermissionError('job_creation_forbidden')
    flag = 'NIGHTOWL_SERVICE_COLLECTION_ENABLED' if kind=='collect_services' else 'NIGHTOWL_SOFTWARE_INSTALL_ENABLED'
    if not getattr(settings,flag,False):
        raise ValueError('internal_job_disabled')
    endpoint=AgentMachine.objects.select_for_update().get(pk=endpoint_id)
    if kind == 'collect_services':
        allowed, reason_code = service_collection_endpoint_allowed(
            endpoint.pk, getattr(settings, 'NIGHTOWL_SERVICE_COLLECTION_ALLOWED_ENDPOINT_IDS', ()))
        if not allowed:
            raise ValueError('service_collection_' + reason_code)
    if (endpoint.has_terminal_lifecycle or not endpoint.machine_id or
        AgentMachine.objects.filter(machine_id__iexact=endpoint.machine_id).count()!=1):
        raise ValueError('identity_unavailable')
    payload = validate_internal_payload(kind, payload)
    if kind == 'collect_services' and payload.get('schema_version') in {2, 3} and payload['endpoint_machine_id'] != endpoint.machine_id:
        raise ValueError('identity_unavailable')
    if not internal_capability(endpoint,required_internal_capability(kind,payload)):
        raise ValueError('capability_unconfirmed')
    if endpoint.jobs.filter(status__in=('queued','sent','running')).exists():
        raise ValueError('job_conflict')
    job=AgentJob.objects.create(endpoint=endpoint,job_type=kind,payload=payload,
        created_by=operator.get_username(),correlation_id=request_id,
        expires_at=timezone.now()+timedelta(minutes=15))
    create_audit_event(event_type='job.created',title='Job interno autorizado',description=kind,
        endpoint=endpoint,actor_type=AuditEvent.ACTOR_USER,actor_name=operator.get_username(),
        metadata={'job_id':str(job.pk),'job_type':kind,'schema_version':payload.get('schema_version',1),
                  'request_id':request_id})
    return job
