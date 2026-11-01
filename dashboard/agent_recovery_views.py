"""Explicit staff-only recovery contract; no credentials are persisted."""
import re

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_POST, require_GET

from agents.models import AgentMachine, AgentJob
from dashboard.agent_recovery import recover_offline_agent
from dashboard.remote_install import InstallFailure
from dashboard.remote_install_preflight import ProbeFailure
from dashboard.remote_install_batch_views import _body


@login_required
@csrf_protect
@require_POST
@sensitive_post_parameters('password')
@sensitive_variables('data', 'password')
def recover(request, pk):
    def response(value, status):
        result = JsonResponse(value, status=status)
        result['Cache-Control'] = 'no-store'
        return result
    if not request.user.is_active or not (request.user.is_staff or request.user.is_superuser):
        return response({'error_code':'ACCESS_DENIED'}, 403)
    data = None
    password = None
    try:
        data = _body(request, {'username','password','restart_running'})
        username, password = data['username'], data['password']
        if (not isinstance(username, str) or not username.strip() or len(username) > 256 or not username.isprintable() or
            not isinstance(password, str) or not password or len(password) > 512 or not password.isprintable() or
            type(data['restart_running']) is not bool):
            raise InstallFailure('INVALID_REQUEST')
        machine = AgentMachine.objects.filter(pk=pk).first()
        if not machine: return response({'error_code':'ENDPOINT_NOT_FOUND'}, 404)
        result = recover_offline_agent(machine, request.user, username.strip(), password, data['restart_running'])
        return response(result, 200 if result['status'] == 'RECOVERED' else 202)
    except (InstallFailure, ProbeFailure) as exc:
        code = exc.code if isinstance(exc.code, str) and re.fullmatch(r'[A-Z0-9_]{1,64}', exc.code) else 'RECOVERY_FAILED'
        return response({'error_code':code}, 403 if code == 'HTTPS_REQUIRED' else 409)
    except Exception:
        return response({'error_code':'RECOVERY_UNAVAILABLE'}, 503)
    finally:
        if data: data.clear()
        password = None


@login_required
@require_GET
def recovery_status(request, pk):
    if not request.user.is_active or not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error_code':'ACCESS_DENIED'}, status=403)
    machine = AgentMachine.objects.filter(pk=pk).first()
    if not machine: return JsonResponse({'error_code':'ENDPOINT_NOT_FOUND'}, status=404)
    job = AgentJob.objects.filter(endpoint=machine, job_type=AgentJob.TYPE_UPDATE_AGENT).order_by('-created_at').first()
    result = job.result if job and isinstance(job.result, dict) else {}
    details = result.get('details') if isinstance(result.get('details'), dict) else {}
    needs_repair = bool(job and (result.get('reason') == 'repair_required' or details.get('reason') == 'repair_required'))
    recommendation = ('AGENT_REPAIR' if machine.status == AgentMachine.STATUS_ONLINE else 'RECOVER_COMMUNICATION_OR_INTERVENTION') if needs_repair else 'NONE'
    response = JsonResponse({'status':'REPAIR_REQUIRED' if needs_repair else 'NO_REPAIR_REPORTED',
        'recommendation':recommendation})
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@csrf_protect
@require_POST
def connectivity(request, pk):
    from dashboard.winrm_observability import check_connectivity
    if not request.is_secure() or not request.user.is_active or not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error_code': 'ACCESS_DENIED'}, status=403)
    machine = AgentMachine.objects.filter(pk=pk).first()
    if not machine:
        return JsonResponse({'error_code': 'ENDPOINT_NOT_FOUND'}, status=404)
    try:
        result = check_connectivity(machine, request.user)
    except ProbeFailure as exc:
        return JsonResponse({'error_code': exc.code}, status=429 if exc.code == 'CHECK_RATE_LIMITED' else 409)
    response = JsonResponse(result)
    response['Cache-Control'] = 'no-store'
    return response
