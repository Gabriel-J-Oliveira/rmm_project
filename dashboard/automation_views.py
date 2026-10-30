"""Authenticated, CSRF-protected UI API. No endpoint-supplied commands."""
import json
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from agents.automation import (ALLOWED, authorize, save_definition, control_definition,
                               cancel_occurrence, QUARANTINE)
from agents.models import (AgentMachine, AutomationDefinition, AutomationOccurrence,
                           AutomationSchedulerHealth)


def definition_row(item):
    return {'id':str(item.pk),'revision':item.revision,'state':item.state,'name':item.name,
        'job_type':item.job_type,'payload':item.payload,'endpoint_ids':[t['id'] for t in item.targets],
        'targets':item.targets,'schedule':item.schedule,'timezone':item.timezone_name,
        'starts_at':item.starts_at.isoformat(),'ends_at':item.ends_at.isoformat() if item.ends_at else None,
        'validity_seconds':item.validity_seconds,'offline_policy':item.offline_policy,
        'next_due_at':item.next_due_at.isoformat() if item.next_due_at else None,
        'retry_count':item.retry_count,'last_error':item.last_error}


@login_required
@require_http_methods(['GET','POST'])
def automations(request, definition_id=None):
    try:
        authorize(request.user)
        if request.method=='GET':
            authorize(request.user,'view_agentjob')
            definitions=AutomationDefinition.objects.order_by('-created_at')[:200]
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
