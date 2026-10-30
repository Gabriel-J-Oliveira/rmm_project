"""Backend calendar and transactional materialization; execution stays in DEC-04A."""
import math
import json
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction, OperationalError
from django.db.models import Q
from django.utils import timezone
from config.authz import is_nightowl_technical_user
from .identity import machine_id_matches, machine_name_matches
from .models import (AgentJob, AgentMachine, AutomationDefinition, AutomationOccurrence,
                     AutomationSchedulerHealth, AuditEvent, InventorySnapshot)

ALLOWED = ('force_inventory', 'collect_disks', 'collect_security', 'collect_software',
           'collect_logs', 'ping', 'windows_update_scan')
ACTIVE = ('queued', 'sent', 'running')
QUARANTINE = frozenset({'CS-CVEL-0029', 'CS-CVEL-0059', 'CS-CVEL-0133', 'CS-CVEL-0262'})
UTC = dt_timezone.utc


def authorize(user, permission='view_automationdefinition'):
    if (not user or not user.is_active or not is_nightowl_technical_user(user) or
            not user.has_perm('agents.' + permission)):
        raise PermissionError('automation_forbidden')


def database_budget():
    if connection.vendor=='postgresql':
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '30s'")


def local_instant(value, zone):
    """First fold only; nonexistent wall times are never silently shifted."""
    first = value.replace(tzinfo=zone, fold=0)
    return first.astimezone(UTC) if first.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == value else None


def parse_instant(value, zone):
    if not isinstance(value, str):
        raise ValueError('invalid_datetime')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('invalid_datetime') from None
    if parsed.tzinfo:
        return parsed.astimezone(UTC)
    instant = local_instant(parsed, zone)
    if instant is None:
        raise ValueError('nonexistent_local_time')
    return instant


def slot(definition, after, inclusive=False):
    """Next calendar slot, O(1) intervals and bounded calendar search. No cron."""
    start = definition.starts_at
    after = after if inclusive else after + timedelta(microseconds=1)
    kind = definition.schedule['kind']
    if kind == 'once':
        answer = start if start >= after else None
    elif kind == 'interval':
        seconds = definition.schedule['minutes'] * 60
        n = max(0, math.ceil((after-start).total_seconds()/seconds))
        answer = start + timedelta(seconds=n*seconds)
    else:
        zone = ZoneInfo(definition.timezone_name)
        wall = start.astimezone(zone)
        day = max(start, after).astimezone(zone).date()
        answer = None
        for offset in range(370):
            date = day + timedelta(days=offset)
            if kind == 'weekly' and date.weekday() not in definition.schedule['weekdays']:
                continue
            if kind == 'monthly' and date.day != definition.schedule['monthday']:
                continue
            candidate = local_instant(datetime.combine(date, wall.time().replace(tzinfo=None)), zone)
            if candidate and candidate >= start and candidate >= after:
                answer = candidate
                break
    if answer and definition.ends_at and answer > definition.ends_at:
        return None
    return answer


def latest_due(definition, now):
    """Coalesce downtime to at most one latest slot, never replay all old slots."""
    kind = definition.schedule['kind']
    if kind == 'once':
        return definition.starts_at
    ceiling = min(now, definition.ends_at) if definition.ends_at else now
    if kind == 'interval':
        step = definition.schedule['minutes'] * 60
        return definition.starts_at + timedelta(seconds=max(0, int((ceiling-definition.starts_at).total_seconds()//step))*step)
    zone = ZoneInfo(definition.timezone_name)
    wall = definition.starts_at.astimezone(zone)
    for offset in range(370):
        date = ceiling.astimezone(zone).date() - timedelta(days=offset)
        if kind == 'weekly' and date.weekday() not in definition.schedule['weekdays']:
            continue
        if kind == 'monthly' and date.day != definition.schedule['monthday']:
            continue
        candidate = local_instant(datetime.combine(date, wall.time().replace(tzinfo=None)), zone)
        if candidate and definition.starts_at <= candidate <= ceiling:
            return candidate
    return definition.next_due_at


def validate_payload(kind, payload):
    if kind not in ALLOWED or not isinstance(payload, dict):
        raise ValueError('job_not_schedulable')
    if kind == 'collect_logs':
        if set(payload)-{'max_lines'} or type(payload.get('max_lines',120)) is not int or not 1 <= payload.get('max_lines',120) <= 500:
            raise ValueError('invalid_log_limit')
        return {'max_lines':payload.get('max_lines',120)}
    if kind == 'ping':
        if set(payload)-{'count'} or type(payload.get('count',2)) is not int or not 1 <= payload.get('count',2) <= 10:
            raise ValueError('invalid_ping_count')
        return {'count':payload.get('count',2)}
    if payload:
        raise ValueError('unexpected_parameters')
    return {}


def endpoint_identity(endpoint, expected=None):
    if (endpoint.has_terminal_lifecycle or not endpoint.machine_id or
            endpoint.hostname.upper().split('.')[0] in QUARANTINE or
            machine_id_matches(endpoint.machine_id).count()!=1 or
            machine_name_matches(endpoint.hostname, endpoint.domain).count()!=1):
        raise ValueError('endpoint_identity_unavailable')
    identity = {'id':str(endpoint.pk), 'machine_id':endpoint.machine_id,
                'hostname':endpoint.hostname, 'domain':endpoint.domain}
    if expected and identity != expected:
        raise ValueError('endpoint_identity_changed')
    return identity


def audit(definition, action, user=None, **metadata):
    AuditEvent.objects.create(event_type='automation.'+action, title='Automacao: '+action,
        description='Operacao tipada de agendamento.', actor_type=AuditEvent.ACTOR_USER if user else AuditEvent.ACTOR_SCHEDULER,
        actor_name=user.get_username() if user else 'process_job_automations',
        metadata=json.loads(json.dumps({'definition_id':str(definition.pk), 'revision':definition.revision, **metadata},cls=DjangoJSONEncoder)))


def clean_definition(data, user, now):
    authorize(user, 'add_agentjob')
    if not isinstance(data, dict) or set(data)-{'name','job_type','payload','endpoint_ids','schedule','timezone','starts_at','ends_at','validity_seconds','offline_policy'}:
        raise ValueError('invalid_definition')
    name = data.get('name','')
    if not isinstance(name,str) or not 1 <= len(name.strip()) <= 120:
        raise ValueError('invalid_name')
    kind = data.get('job_type')
    payload = validate_payload(kind, data.get('payload',{}))
    try:
        zone = ZoneInfo(data.get('timezone',''))
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise ValueError('invalid_timezone') from None
    start = parse_instant(data.get('starts_at'),zone)
    end = parse_instant(data['ends_at'],zone) if data.get('ends_at') else None
    if start < now-timedelta(seconds=5) or (end and end < start):
        raise ValueError('invalid_schedule_range')
    schedule = data.get('schedule')
    if not isinstance(schedule,dict) or schedule.get('kind') not in ('immediate','once','daily','weekly','monthly','interval'):
        raise ValueError('invalid_recurrence')
    recurrence = schedule['kind']
    expected = {'kind'} | ({'weekdays'} if recurrence=='weekly' else {'monthday'} if recurrence=='monthly' else {'minutes'} if recurrence=='interval' else set())
    if set(schedule)!=expected:
        raise ValueError('invalid_recurrence_fields')
    if recurrence=='weekly' and (not isinstance(schedule['weekdays'],list) or not schedule['weekdays'] or
            len(schedule['weekdays'])>7 or any(type(x) is not int or not 0<=x<=6 for x in schedule['weekdays'])):
        raise ValueError('invalid_weekdays')
    if recurrence=='monthly' and (type(schedule['monthday']) is not int or not 1<=schedule['monthday']<=31):
        raise ValueError('invalid_monthday')
    if recurrence=='interval' and (type(schedule['minutes']) is not int or not 15<=schedule['minutes']<=43200):
        raise ValueError('invalid_interval')
    validity = data.get('validity_seconds',900)
    if type(validity) is not int or not 60<=validity<=86400:
        raise ValueError('invalid_validity')
    policy = data.get('offline_policy','expire')
    if policy not in ('wait','skip','expire'):
        raise ValueError('invalid_offline_policy')
    ids = data.get('endpoint_ids')
    if not isinstance(ids,list) or not 1<=len(ids)<=50:
        raise ValueError('invalid_targets')
    try:
        ids = sorted({uuid.UUID(x) for x in ids})
    except (ValueError, TypeError, AttributeError):
        raise ValueError('invalid_targets') from None
    if len(ids)!=len(data['endpoint_ids']):
        raise ValueError('duplicate_targets')
    endpoints = list(AgentMachine.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
    if len(endpoints)!=len(ids):
        raise ValueError('endpoint_not_found')
    return dict(name=name.strip(), job_type=kind, payload=payload, targets=[endpoint_identity(e) for e in endpoints],
                schedule=schedule, timezone_name=zone.key, starts_at=start, ends_at=end,
                validity_seconds=validity, offline_policy=policy)


@transaction.atomic
def save_definition(user, data, definition_id=None, expected_revision=None, now=None):
    database_budget()
    now = now or timezone.now()
    authorize(user, 'change_automationdefinition' if definition_id else 'add_automationdefinition')
    definition = AutomationDefinition.objects.select_for_update().get(pk=definition_id) if definition_id else AutomationDefinition(owner=user)
    if definition_id:
        if type(expected_revision) is not int or expected_revision != definition.revision:
            raise ValueError('revision_conflict')
        if definition.state in ('cancelled','finished'):
            raise ValueError('definition_closed')
    values = clean_definition(data,user,now)
    immediate=values['schedule']['kind']=='immediate'
    if immediate:
        authorize(user,'execute_automation')
        if definition_id:
            raise ValueError('immediate_not_editable')
        values['schedule']={'kind':'once'}
        values['starts_at']=now
    for key,value in values.items():
        setattr(definition,key,value)
    if definition_id:
        definition.revision += 1
    definition.owner=user
    definition.next_due_at = slot(definition,values['starts_at'],inclusive=True)
    definition.retry_at=None
    definition.retry_count=0
    definition.last_error=''
    definition.save()
    audit(definition,'edited' if definition_id else 'created',user,configuration=values)
    if immediate:
        materialize(definition,now,now,operator=user)
        definition.state='finished'
        definition.next_due_at=None
        definition.save(update_fields=['state','next_due_at','updated_at'])
    return definition


def online(endpoint,now):
    return bool(endpoint.last_seen_at and endpoint.last_seen_at >= now-timedelta(minutes=15))


def known_v2(endpoint):
    heartbeat=InventorySnapshot.objects.filter(machine=endpoint,raw_payload__snapshot_source='heartbeat').order_by('-received_at').first()
    raw=heartbeat.raw_payload if heartbeat else {}
    agent=raw.get('agent') if isinstance(raw.get('agent'),dict) else {}
    capabilities=agent.get('capabilities')
    # Exact protocol aliases only, never a grant of operation-specific capabilities.
    return (raw.get('machine_id')==endpoint.machine_id and raw.get('agent_version')==endpoint.agent_version and
            isinstance(capabilities,list) and
            ('jobs_protocol_v2' in capabilities or 'job_protocol_v2' in capabilities))


def materialize(definition, due, now, invocation_key='scheduled', operator=None):
    expires=due+timedelta(seconds=definition.validity_seconds)
    for target in sorted(definition.targets,key=lambda t:t['id']):
        endpoint=AgentMachine.objects.select_for_update().get(pk=target['id'])
        identity = uuid.uuid5(definition.pk,invocation_key+str(endpoint.pk)) if invocation_key!='scheduled' else uuid.uuid5(definition.pk,f'{definition.revision}:{due.isoformat()}:{endpoint.pk}')
        occurrence,created=AutomationOccurrence.objects.get_or_create(pk=identity,
            defaults={'definition':definition,'revision':definition.revision,'scheduled_for':due,'endpoint':endpoint,
                'invocation_key':invocation_key,'expires_at':expires,'status':'pending',
                'snapshot':{'name':definition.name,'job_type':definition.job_type,'payload':definition.payload,
                            'target':target,'owner_id':(operator or definition.owner).pk if (operator or definition.owner) else None,
                            'schedule':definition.schedule,'timezone':definition.timezone_name,
                            'offline_policy':definition.offline_policy,'manual':operator is not None,
                            'coalesced_from':definition.next_due_at.isoformat() if definition.next_due_at and due>definition.next_due_at else None}})
        if not created:
            continue
        try:
            actor=operator or definition.owner
            authorize(actor,'add_agentjob')
            endpoint_identity(endpoint,target)
            payload=validate_payload(definition.job_type,definition.payload)
            if not known_v2(endpoint):
                raise ValueError('jobs_v2_capability_unconfirmed')
        except (PermissionError, ValueError) as error:
            occurrence.status='blocked'
            occurrence.reason=str(error)
        else:
            if now >= expires:
                occurrence.status='skipped' if definition.offline_policy=='skip' else 'expired'
                occurrence.reason='window_elapsed'
            elif not online(endpoint,now) and definition.offline_policy!='wait':
                occurrence.status='skipped' if definition.offline_policy=='skip' else 'expired'
                occurrence.reason='endpoint_offline'
            elif endpoint.jobs.filter(status__in=ACTIVE).exists():
                occurrence.status='skipped'
                occurrence.reason='overlap_blocked'
            else:
                if definition.job_type=='ping':
                    payload['target']=endpoint.hostname.rstrip('.') + ('.'+endpoint.domain.rstrip('.') if endpoint.domain and not endpoint.hostname.lower().endswith('.'+endpoint.domain.lower()) else '')
                job=AgentJob.objects.create(endpoint=endpoint,job_type=definition.job_type,payload=payload,
                    created_by=actor.get_username(),expires_at=expires,correlation_id=str(occurrence.pk),timeout_seconds=300)
                occurrence.job=job
                occurrence.status='enqueued'
                AuditEvent.objects.create(event_type='job.created',title='Job de automacao criado',description=definition.job_type,
                    endpoint=endpoint,actor_type=AuditEvent.ACTOR_USER if operator else AuditEvent.ACTOR_SCHEDULER,
                    actor_name=actor.get_username() if operator else 'process_job_automations',
                    metadata={'job_id':str(job.pk),'occurrence_id':str(occurrence.pk),'definition_id':str(definition.pk)})
        occurrence.save()
        audit(definition,'occurrence',operator,occurrence_id=str(occurrence.pk),endpoint_id=str(endpoint.pk),status=occurrence.status,reason=occurrence.reason)


@transaction.atomic
def control_definition(user, definition_id, action, revision, now=None, request_id=None):
    database_budget()
    now=now or timezone.now()
    authorize(user,'execute_automation' if action=='run_now' else 'control_automation')
    definition=AutomationDefinition.objects.select_for_update().get(pk=definition_id)
    if type(revision) is not int or revision != definition.revision:
        raise ValueError('revision_conflict')
    if action=='run_now':
        if definition.state!='active':
            raise ValueError('definition_not_active')
        authorize(user,'add_agentjob')
        try:
            key=str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('request_id_required') from None
        materialize(definition,now,now,invocation_key=key,operator=user)
    elif action=='cancel_next' and definition.state in ('active','paused') and definition.next_due_at:
        due=definition.next_due_at
        for target in definition.targets:
            identity=uuid.uuid5(definition.pk,f'{definition.revision}:{due.isoformat()}:{target["id"]}')
            AutomationOccurrence.objects.get_or_create(pk=identity,defaults={'definition':definition,'revision':definition.revision,
                'scheduled_for':due,'endpoint_id':target['id'],'expires_at':due+timedelta(seconds=definition.validity_seconds),
                'status':'cancelled','reason':'operator_cancelled','snapshot':{'target':target,'name':definition.name,'job_type':definition.job_type}})
    elif action=='pause' and definition.state=='active':
        definition.state='paused'
    elif action=='resume' and definition.state=='paused':
        definition.state='active'
        definition.retry_count=0
        definition.retry_at=None
        definition.last_error=''
        definition.next_due_at=slot(definition,now,inclusive=True)
        if not definition.next_due_at:
            definition.state='finished'
    elif action=='cancel' and definition.state not in ('cancelled','finished'):
        definition.state='cancelled'
        definition.next_due_at=None
        for job in AgentJob.objects.select_for_update().filter(automation_occurrence__definition=definition,status='queued').order_by('pk'):
            job.status='cancelled'
            job.finished_at=now
            job.save(update_fields=['status','finished_at','updated_at'])
            job.automation_occurrence.status='cancelled'
            job.automation_occurrence.reason='definition_cancelled'
            job.automation_occurrence.save(update_fields=['status','reason'])
            AuditEvent.objects.create(event_type='job.cancelled',title='Automacao cancelada antes do despacho',description='Cancelamento autorizado.',
                endpoint=job.endpoint,actor_type=AuditEvent.ACTOR_USER,actor_name=user.get_username(),metadata={'job_id':str(job.pk)})
    else:
        raise ValueError('invalid_state_transition')
    definition.save()
    audit(definition,action,user)
    return definition


@transaction.atomic
def cancel_occurrence(user, occurrence_id):
    database_budget()
    authorize(user,'control_automation')
    # Lock parent first, like scheduler/edit. Sent/running jobs are never claimed cancelled.
    parent=AutomationOccurrence.objects.values_list('definition_id',flat=True).get(pk=occurrence_id)
    definition=AutomationDefinition.objects.select_for_update().get(pk=parent)
    occurrence=AutomationOccurrence.objects.select_for_update().get(pk=occurrence_id)
    if occurrence.status not in ('pending','enqueued','cancelled'):
        return False
    if occurrence.job_id:
        job=AgentJob.objects.select_for_update().get(pk=occurrence.job_id)
        if job.status=='cancelled' and occurrence.status=='cancelled':
            return True
        if job.status != 'queued':
            audit(definition,'cancel_refused',user,occurrence_id=str(occurrence.pk),reason='already_dispatched')
            return False
        job.status='cancelled'
        job.finished_at=timezone.now()
        job.save(update_fields=['status','finished_at','updated_at'])
        AuditEvent.objects.create(event_type='job.cancelled',title='Job cancelado antes do despacho',description='Cancelamento de ocorrencia.',
            endpoint=job.endpoint,actor_type=AuditEvent.ACTOR_USER,actor_name=user.get_username(),metadata={'job_id':str(job.pk)})
    occurrence.status='cancelled'
    occurrence.reason='operator_cancelled'
    occurrence.save(update_fields=['status','reason'])
    audit(definition,'occurrence_cancelled',user,occurrence_id=str(occurrence.pk))
    return True


def scheduler_round(now=None, limit=20):
    if not getattr(settings,'NIGHTOWL_JOB_AUTOMATIONS_ENABLED',False):
        raise ValueError('scheduler_disabled')
    now=now or timezone.now()
    if type(limit) is not int or not 1<=limit<=100:
        raise ValueError('invalid_batch_limit')
    health,_=AutomationSchedulerHealth.objects.get_or_create(key='jobs')
    health.last_started_at=now
    health.save(update_fields=['last_started_at'])
    # Expire waiting/delivered work even if no endpoint reconnects to pull it.
    with transaction.atomic():
        database_budget()
        stale=AgentJob.objects.filter(automation_occurrence__isnull=False,status__in=('queued','sent'),expires_at__lte=now)
        stale=stale.select_for_update(skip_locked=True) if connection.features.has_select_for_update_skip_locked else stale.select_for_update()
        for job in stale.order_by('pk')[:100]:
            job.status='expired'
            job.error_code='AUTOMATION_WINDOW_ELAPSED'
            job.finished_at=now
            job.save(update_fields=['status','error_code','finished_at','updated_at'])
            AuditEvent.objects.create(event_type='job.expired',title='Janela da automacao encerrada',description='Nenhuma execucao tardia autorizada.',
                endpoint=job.endpoint,actor_type=AuditEvent.ACTOR_SCHEDULER,actor_name='process_job_automations',metadata={'job_id':str(job.pk)})
    ids=list(AutomationDefinition.objects.filter(Q(retry_at__isnull=True)|Q(retry_at__lte=now),state='active',next_due_at__lte=now).order_by('next_due_at').values_list('pk',flat=True)[:limit])
    processed=failed=0
    for pk in ids:
        try:
            with transaction.atomic():
                database_budget()
                query=AutomationDefinition.objects.filter(pk=pk,state='active',next_due_at__lte=now)
                definition=query.select_for_update(skip_locked=True).first() if connection.features.has_select_for_update_skip_locked else query.select_for_update().first()
                if not definition or (definition.retry_at and definition.retry_at>now):
                    continue
                due=latest_due(definition,now)
                materialize(definition,due,now)
                definition.next_due_at=slot(definition,due)
                definition.last_processed_at=now
                definition.retry_count=0
                definition.retry_at=None
                definition.last_error=''
                if not definition.next_due_at:
                    definition.state='finished'
                definition.save()
                processed+=1
        except AgentMachine.DoesNotExist:
            with transaction.atomic():
                database_budget()
                definition=AutomationDefinition.objects.select_for_update().get(pk=pk)
                definition.state='paused'
                definition.last_error='definition_target_unavailable'
                definition.save(update_fields=['state','last_error','updated_at'])
                audit(definition,'blocked',reason=definition.last_error)
            failed+=1
        except OperationalError:
            # No partial jobs survive the rolled-back transaction. No raw DB messages.
            with transaction.atomic():
                database_budget()
                definition=AutomationDefinition.objects.select_for_update().get(pk=pk)
                if definition.state!='active' or not definition.next_due_at or definition.next_due_at>now:
                    continue
                definition.retry_count+=1
                definition.retry_at=now+timedelta(seconds=min(900,15*2**min(definition.retry_count,6)))
                definition.last_error='database_transient_failure'
                if definition.retry_count>=5:
                    definition.state='paused'
                definition.save()
                audit(definition,'retry',attempt=definition.retry_count,reason=definition.last_error,state=definition.state)
            failed+=1
    changes={'processed':processed,'failed':failed}
    if not failed:
        changes['last_success_at']=now
    AutomationSchedulerHealth.objects.filter(key='jobs').update(**changes)
    return {'processed':processed,'failed':failed}


def validate_scheduled_dispatch(job):
    """Recheck immutable target and current rights before delivery and true start."""
    try:
        occurrence=job.automation_occurrence
    except AutomationOccurrence.DoesNotExist:
        return
    from django.contrib.auth import get_user_model
    user=get_user_model().objects.filter(pk=occurrence.snapshot.get('owner_id')).first()
    authorize(user,'add_agentjob')
    endpoint_identity(job.endpoint,occurrence.snapshot['target'])
    payload=validate_payload(job.job_type,occurrence.snapshot.get('payload',{}))
    if job.job_type=='ping':
        endpoint=job.endpoint
        payload['target']=endpoint.hostname.rstrip('.') + ('.'+endpoint.domain.rstrip('.') if endpoint.domain and not endpoint.hostname.lower().endswith('.'+endpoint.domain.lower()) else '')
    if (job.job_type!=occurrence.snapshot.get('job_type') or job.payload!=payload or
            job.expires_at!=occurrence.expires_at or timezone.now()<occurrence.scheduled_for):
        raise ValueError('automation_job_contract_changed')
    if not known_v2(job.endpoint):
        raise ValueError('jobs_v2_capability_unconfirmed')
