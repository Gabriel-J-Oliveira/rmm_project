"""Authenticated, CSRF-protected UI API. No endpoint-supplied commands."""
import json
import uuid
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Case, CharField, F, OuterRef, Subquery, When
from django.views.decorators.http import require_http_methods
from agents.automation import (ALLOWED, authorize, save_definition, control_definition,
                               cancel_occurrence, QUARANTINE, ACTIVE, endpoint_identity,
                               known_v2, online, validate_payload)
from agents.models import (AgentMachine, AutomationDefinition, AutomationOccurrence,
                           AutomationSchedulerHealth, AuditEvent)
from agents.telemetry_persisted_analysis import (
    _health_snapshot, recover_telemetry_analysis_scheduler)


def definition_row(item):
    return {'id':str(item.pk),'revision':item.revision,'state':item.state,'name':item.name,
        'job_type':item.job_type,'payload':item.payload,'endpoint_ids':[t['id'] for t in item.targets],
        'targets':item.targets,'schedule':item.schedule,'timezone':item.timezone_name,
        'starts_at':item.starts_at.isoformat(),'ends_at':item.ends_at.isoformat() if item.ends_at else None,
        'validity_seconds':item.validity_seconds,'offline_policy':item.offline_policy,
        'next_due_at':item.next_due_at.isoformat() if item.next_due_at else None,
        'retry_count':item.retry_count,'last_error':item.last_error,
        'latest_occurrence_at':getattr(item,'latest_occurrence_at',None).isoformat() if getattr(item,'latest_occurrence_at',None) else None,
        'latest_occurrence_status':getattr(item,'latest_occurrence_status',None),
        'latest_occurrence_reason':getattr(item,'latest_occurrence_reason',None),
        'latest_occurrence_endpoint':getattr(item,'latest_occurrence_endpoint',None)}


@login_required
@require_http_methods(['GET','POST'])
def automations(request, definition_id=None):
    try:
        authorize(request.user)
        if request.method=='GET':
            authorize(request.user,'view_agentjob')
            latest=AutomationOccurrence.objects.filter(definition_id=OuterRef('pk')).order_by('-scheduled_for','-created_at')
            definitions=AutomationDefinition.objects.annotate(
                latest_occurrence_at=Subquery(latest.values('scheduled_for')[:1]),
                latest_occurrence_status=Subquery(latest.annotate(display_status=Case(
                    When(job__isnull=False,then=F('job__status')),
                    default=F('status'),output_field=CharField())).values('display_status')[:1]),
                latest_occurrence_reason=Subquery(latest.annotate(display_reason=Case(
                    When(job__isnull=True,then=F('reason')),
                    When(job__error_code='',then=F('reason')),
                    default=F('job__error_code'),output_field=CharField())).values('display_reason')[:1]),
                latest_occurrence_endpoint=Subquery(latest.values('endpoint__hostname')[:1]),
            ).order_by('-created_at')[:200]
            occurrences=AutomationOccurrence.objects.select_related('job','endpoint').order_by('-scheduled_for')
            if definition_id:
                occurrences=occurrences.filter(definition_id=definition_id)
            items=[]
            for occurrence in occurrences[:200]:
                job=occurrence.job
                items.append({'id':str(occurrence.pk),'definition_id':str(occurrence.definition_id),
                    'revision':occurrence.revision,'scheduled_for':occurrence.scheduled_for.isoformat(),
                    'expires_at':occurrence.expires_at.isoformat(),'hostname':occurrence.endpoint.hostname,
                    'status':job.status if job else occurrence.status,'reason':job.error_code if job else occurrence.reason,
                    'job_id':str(job.pk) if job else None,'snapshot':occurrence.snapshot})
            health=AutomationSchedulerHealth.objects.filter(key='jobs').first()
            response=JsonResponse({'definitions':[definition_row(x) for x in definitions], 'occurrences':items,
                'health':{'last_success_at':health.last_success_at.isoformat() if health and health.last_success_at else None,
                          'stale':not health or not health.last_success_at or (timezone.now()-health.last_success_at).total_seconds()>120,
                          'failed':health.failed if health else 0},
                'job_types':list(ALLOWED),'limit':200,
                'enabled':getattr(settings,'NIGHTOWL_JOB_AUTOMATIONS_ENABLED',False)})
            response['Cache-Control']='no-store'
            return response
        if len(request.body)>32768:
            return JsonResponse({'error':'request_too_large'},status=400)
        data=json.loads(request.body)
        if not isinstance(data,dict):
            raise ValueError('invalid_request')
        if definition_id and 'action' in data:
            if set(data)-{'action','revision','request_id'}:
                raise ValueError('invalid_request')
            item=control_definition(request.user,definition_id,data.get('action'),data.get('revision'),request_id=data.get('request_id'))
        else:
            revision=data.pop('revision',None)
            item=save_definition(request.user,data,definition_id,revision)
        return JsonResponse({'definition':definition_row(item)},status=200 if definition_id else 201)
    except PermissionError:
        return JsonResponse({'error':'forbidden'},status=403)
    except (AutomationDefinition.DoesNotExist,AutomationOccurrence.DoesNotExist):
        return JsonResponse({'error':'not_found'},status=404)
    except (ValueError,TypeError) as error:
        code=str(error) if type(error) is ValueError and str(error).replace('_','').isalnum() else 'invalid_request'
        return JsonResponse({'error':code},status=409 if code=='revision_conflict' else 400)


def _endpoint_policy_reason(user, definition, endpoint, target):
    """Return safe action eligibility for one assigned identity snapshot."""
    try:
        authorize(user, 'execute_automation')
        authorize(user, 'add_agentjob')
    except PermissionError:
        return 'permission_required'
    try:
        endpoint_identity(endpoint, target)
    except ValueError as error:
        return str(error)
    if definition.state != 'active':
        return 'policy_not_active'
    if not known_v2(endpoint):
        return 'jobs_v2_capability_unconfirmed'
    try:
        validate_payload(definition.job_type, definition.payload)
    except ValueError as error:
        return str(error)
    now = timezone.now()
    if not online(endpoint, now) and definition.offline_policy != 'wait':
        return 'endpoint_offline'
    if endpoint.jobs.filter(status__in=ACTIVE).exists():
        return 'overlap_blocked'
    return ''


def _endpoint_occurrence_row(occurrence):
    job = occurrence.job
    return {
        'id': str(occurrence.pk),
        'scheduled_for': occurrence.scheduled_for.isoformat(),
        'created_at': occurrence.created_at.isoformat(),
        'expires_at': occurrence.expires_at.isoformat(),
        'status': job.status if job else occurrence.status,
        'reason': (job.error_code or occurrence.reason) if job else occurrence.reason,
        'started_at': job.started_at.isoformat() if job and job.started_at else None,
        'finished_at': job.finished_at.isoformat() if job and job.finished_at else None,
        'job_id': str(job.pk) if job else None,
    }


@login_required
@require_http_methods(['GET'])
def endpoint_policies(request, endpoint_id):
    """Expose only policies whose stored target UUID is this endpoint UUID."""
    try:
        authorize(request.user)
        authorize(request.user, 'view_agentmachine')
        authorize(request.user, 'view_agentjob')
    except PermissionError:
        return JsonResponse({'error': 'forbidden'}, status=403)
    try:
        endpoint = AgentMachine.objects.get(pk=endpoint_id)
    except AgentMachine.DoesNotExist:
        return JsonResponse({'error': 'not_found'}, status=404)

    definitions = []
    # Match the persisted assignment snapshot by UUID, never by hostname.
    for definition in AutomationDefinition.objects.order_by('-updated_at').iterator(chunk_size=200):
        snapshots = definition.targets if isinstance(definition.targets, list) else []
        matches = [target for target in snapshots if isinstance(target, dict) and target.get('id') == str(endpoint.pk)]
        if len(matches) != 1:
            continue
        target = matches[0]
        reason = _endpoint_policy_reason(request.user, definition, endpoint, target)
        rows = AutomationOccurrence.objects.filter(definition=definition, endpoint=endpoint).select_related('job').order_by('-scheduled_for', '-created_at')[:10]
        definitions.append({
            'id': str(definition.pk),
            'revision': definition.revision,
            'name': definition.name,
            'job_type': definition.job_type,
            'state': definition.state,
            'schedule': definition.schedule,
            'timezone': definition.timezone_name,
            'next_due_at': definition.next_due_at.isoformat() if definition.next_due_at else None,
            'last_error': definition.last_error or '',
            'can_run_now': not reason,
            'run_block_reason': reason,
            'history': [_endpoint_occurrence_row(row) for row in rows],
        })
    response = JsonResponse({
        'endpoint': {'id': str(endpoint.pk), 'machine_id': endpoint.machine_id, 'hostname': endpoint.hostname},
        'policies': definitions,
        'scheduler_enabled': bool(getattr(settings, 'NIGHTOWL_JOB_AUTOMATIONS_ENABLED', False)),
    })
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@require_http_methods(['POST'])
def endpoint_policy_run(request, endpoint_id, definition_id):
    """Explicitly materialize a single assigned endpoint, never the fleet."""
    if len(request.body) > 4096:
        return JsonResponse({'error': 'request_too_large'}, status=400)
    try:
        authorize(request.user)
        authorize(request.user, 'view_agentmachine')
        authorize(request.user, 'view_agentjob')
        data = json.loads(request.body)
        if not isinstance(data, dict) or set(data) != {'revision', 'request_id'}:
            raise ValueError('invalid_request')
        endpoint = AgentMachine.objects.get(pk=endpoint_id)
        definition = control_definition(
            request.user, definition_id, 'run_now_endpoint', data.get('revision'),
            request_id=data.get('request_id'), endpoint_id=endpoint.pk)
        invocation_key = str(uuid.UUID(data['request_id']))
        occurrence_id = uuid.uuid5(definition.pk, invocation_key + str(endpoint.pk))
        occurrence = AutomationOccurrence.objects.select_related('job').get(
            pk=occurrence_id, definition=definition, endpoint=endpoint)
        response = JsonResponse({
            'endpoint': {'id': str(endpoint.pk), 'machine_id': endpoint.machine_id, 'hostname': endpoint.hostname},
            'policy': {'id': str(definition.pk), 'revision': definition.revision, 'name': definition.name,
                       'job_type': definition.job_type},
            'occurrence': _endpoint_occurrence_row(occurrence),
            'scheduler_enabled': bool(getattr(settings, 'NIGHTOWL_JOB_AUTOMATIONS_ENABLED', False)),
        }, status=200 if not occurrence.job_id else 202)
        response['Cache-Control'] = 'no-store'
        return response
    except PermissionError:
        return JsonResponse({'error': 'forbidden'}, status=403)
    except (AgentMachine.DoesNotExist, AutomationDefinition.DoesNotExist, AutomationOccurrence.DoesNotExist):
        return JsonResponse({'error': 'not_found'}, status=404)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        code = str(error) if type(error) is ValueError and str(error).replace('_', '').isalnum() else 'invalid_request'
        return JsonResponse({'error': code}, status=409 if code in (
            'revision_conflict', 'definition_not_active', 'endpoint_not_assigned',
            'endpoint_identity_unavailable', 'endpoint_identity_changed') else 400)


@login_required
@require_http_methods(['POST'])
def occurrence_cancel(request, occurrence_id):
    try:
        if not cancel_occurrence(request.user,occurrence_id):
            return JsonResponse({'error':'already_dispatched_or_final'},status=409)
        return JsonResponse({'status':'cancelled'})
    except PermissionError:
        return JsonResponse({'error':'forbidden'},status=403)
    except AutomationOccurrence.DoesNotExist:
        return JsonResponse({'error':'not_found'},status=404)


@login_required
@require_http_methods(['GET'])
def targets(request):
    try:
        authorize(request.user)
    except PermissionError:
        return JsonResponse({'error':'forbidden'},status=403)
    query=AgentMachine.objects.exclude(machine_id='').order_by('hostname')
    if request.GET.get('q'):
        query=query.filter(hostname__icontains=request.GET['q'][:100])
    rows=[{'id':str(e.pk),'hostname':e.hostname,'domain':e.domain} for e in query[:500]
          if not e.has_terminal_lifecycle and e.hostname.upper().split('.')[0] not in QUARANTINE]
    response=JsonResponse({'endpoints':rows,'limit':500})
    response['Cache-Control']='no-store'
    return response


@login_required
@require_http_methods(['GET', 'POST'])
def telemetry_analysis_scheduler_health(request):
    """Read scheduler health or perform an explicitly audited manual recovery."""
    try:
        if request.method == 'GET':
            authorize(request.user, 'view_agentjob')
            health = AutomationSchedulerHealth.objects.filter(key='telemetry_analysis').first()
            data = _health_snapshot(health) if health else {
                'last_attempt_at': None, 'last_success_at': None, 'last_duration_ms': None,
                'last_attempt_status': 'not_run', 'consecutive_systemic_failures': 0,
                'processed_endpoints': 0, 'blocked_endpoints': 0,
                'review_required_endpoints': 0, 'last_failure_class': '',
                'last_failure_code': '', 'suspended': False, 'suspended_at': None,
                'suspension_reason': '', 'recovered_at': None, 'recovered_by': '',
                'recovery_reason': '',
            }
            response = JsonResponse({'health': data})
            response['Cache-Control'] = 'no-store'
            return response

        if len(request.body) > 4096:
            return JsonResponse({'error': 'request_too_large'}, status=400)
        data = json.loads(request.body)
        if not isinstance(data, dict) or set(data) != {'action', 'reason'} or data.get('action') != 'resume':
            return JsonResponse({'error': 'invalid_request'}, status=400)
        health = recover_telemetry_analysis_scheduler(request.user, data.get('reason'))
        response = JsonResponse({'health': _health_snapshot(health)})
        response['Cache-Control'] = 'no-store'
        return response
    except PermissionError:
        return JsonResponse({'error': 'forbidden'}, status=403)
    except ValueError as error:
        code = str(error) if str(error).replace('_', '').isalnum() else 'invalid_request'
        return JsonResponse({'error': code}, status=409 if code == 'scheduler_not_suspended' else 400)
