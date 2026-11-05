"""Typed internal preparation only. No new frontend dispatch entry point."""
import re
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from config.authz import is_nightowl_technical_user
from .models import AgentMachine, AgentJob, InventorySnapshot, AuditEvent
from .services import create_audit_event


def validate_internal_payload(kind, value):
    if not isinstance(value, dict):
        raise ValueError('invalid_payload')
    if kind == 'collect_services':
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
        if set(value) != {'package_id'} or not isinstance(value['package_id'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}',value['package_id']):
            raise ValueError('invalid_package_identity')
        if value['package_id'] not in getattr(settings,'NIGHTOWL_APPROVED_SOFTWARE_IDS',()):
            raise ValueError('package_not_approved')
        return dict(value)
    raise ValueError('unsupported_internal_job')


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
        if (output.get('schema_version')!=1 or output.get('read_only') is not True or not isinstance(rows,list) or
            len(rows)>job.payload.get('limit',50) or any(not isinstance(row,dict) or not isinstance(row.get('name'),str) for row in rows)):
            raise ValueError('service_confirmation_invalid')
    elif job.job_type=='install_software':
        if (output.get('package_id')!=job.payload.get('package_id') or output.get('verified') is not True or
            output.get('installed') is not True or output.get('scope')!='machine'):
            raise ValueError('software_confirmation_invalid')


@transaction.atomic
def create_internal_job(operator, endpoint_id, kind, payload):
    if not is_nightowl_technical_user(operator) or not operator.has_perm('agents.add_agentjob'):
        raise PermissionError('job_creation_forbidden')
    flag = 'NIGHTOWL_SERVICE_COLLECTION_ENABLED' if kind=='collect_services' else 'NIGHTOWL_SOFTWARE_INSTALL_ENABLED'
    if not getattr(settings,flag,False):
        raise ValueError('internal_job_disabled')
    endpoint=AgentMachine.objects.select_for_update().get(pk=endpoint_id)
    if (endpoint.has_terminal_lifecycle or not endpoint.machine_id or
        AgentMachine.objects.filter(machine_id__iexact=endpoint.machine_id).count()!=1):
        raise ValueError('identity_unavailable')
    if not internal_capability(endpoint,kind):
        raise ValueError('capability_unconfirmed')
    if endpoint.jobs.filter(status__in=('queued','sent','running')).exists():
        raise ValueError('job_conflict')
    job=AgentJob.objects.create(endpoint=endpoint,job_type=kind,payload=validate_internal_payload(kind,payload),
        created_by=operator.get_username(),expires_at=timezone.now()+timedelta(minutes=15))
    create_audit_event(event_type='job.created',title='Job interno autorizado',description=kind,
        endpoint=endpoint,actor_type=AuditEvent.ACTOR_USER,actor_name=operator.get_username(),
        metadata={'job_id':str(job.pk),'job_type':kind})
    return job
