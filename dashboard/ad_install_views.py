import json
import ipaddress

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from config.authz import is_nightowl_technical_user
from dashboard.ad_install_discovery import build_install_discovery
from dashboard.remote_install_preflight import run_remote_install_preflight
from dashboard.remote_install import InstallFailure, create_remote_install_job, start_remote_install
from dashboard.remote_install_preflight import ProbeFailure, _valid_fqdn
from dashboard.models import RemoteInstallJob


@login_required
@csrf_protect
@require_POST
def scan_ad_computers(request):
    if not is_nightowl_technical_user(request.user):
        return JsonResponse({'error': 'Acesso negado.'}, status=403)
    try:
        return JsonResponse(build_install_discovery())
    except Exception:
        return JsonResponse({'error': 'Nao foi possivel concluir a busca de computadores AD.'}, status=503)


@login_required
@csrf_protect
@require_POST
def preflight_ad_computer(request):
    if not request.user.is_active or not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Acesso negado.'}, status=403)
    if request.content_type != 'application/json' or len(request.body) > 4096:
        return JsonResponse({'error': 'Requisicao invalida.'}, status=400)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict) or set(data) != {'fqdn', 'username', 'password'}:
            raise ValueError
        result = run_remote_install_preflight(data.get('fqdn'), data.get('username'), data.get('password'))
    except (ValueError, UnicodeError):
        return JsonResponse({'error': 'Requisicao invalida.'}, status=400)
    except Exception:
        return JsonResponse({'error': 'Nao foi possivel concluir o preflight remoto.'}, status=503)
    response = JsonResponse(result)
    response['Cache-Control'] = 'no-store'
    response['Pragma'] = 'no-cache'
    return response


@login_required
@csrf_protect
@require_POST
@sensitive_post_parameters('password')
def install_ad_computer(request):
    if not request.user.is_active or not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Acesso negado.'}, status=403)
    if not request.is_secure():
        return JsonResponse({'error_code': 'HTTPS_REQUIRED'}, status=403)
    fields = {'csrfmiddlewaretoken', 'fqdn', 'username', 'password'}
    # CSRF has already parsed multipart data; never re-read the consumed stream.
    content_length = request.META.get('CONTENT_LENGTH', '')
    if (not isinstance(content_length, str) or not content_length.isascii() or
            not content_length.isdecimal() or len(content_length) > 10 or
            not 0 < int(content_length) <= 4096 or
            request.content_type not in ('multipart/form-data', 'application/x-www-form-urlencoded') or
            request.FILES or set(request.POST) != fields or
            any(len(request.POST.getlist(field)) != 1 for field in fields)):
        return JsonResponse({'error': 'Requisicao invalida.'}, status=400)
    fqdn = request.POST.get('fqdn', '')
    username = request.POST.get('username', '')
    password = request.POST.get('password', '')
    try:
        ipaddress.ip_address(fqdn.strip().rstrip('.'))
        target_is_ip = True
    except ValueError:
        target_is_ip = False
    if target_is_ip or len(fqdn) > 253 or not _valid_fqdn(fqdn) or len(request.POST['csrfmiddlewaretoken']) > 128:
        return JsonResponse({'error_code': 'INVALID_TARGET_OR_REQUEST'}, status=400)
    if not username.strip() or not password or len(username) > 256 or len(password) > 512:
        return JsonResponse({'error_code': 'CREDENTIAL_REQUIRED'}, status=400)
    try:
        job = create_remote_install_job(fqdn, request.user, username=username, password=password)
        start_remote_install(job, username, password)
    except (ProbeFailure, InstallFailure) as exc:
        return JsonResponse({'error_code': exc.code}, status=409)
    except Exception:
        return JsonResponse({'error_code': 'INSTALL_UNAVAILABLE'}, status=503)
    finally:
        password = ''
    response = JsonResponse({'job_id': str(job.pk), 'status': job.status,
                             'status_url': reverse('agent-install-job-status', args=[job.pk])}, status=202)
    response['Cache-Control'] = 'no-store'
    response['Pragma'] = 'no-cache'
    return response


@login_required
@require_GET
def remote_install_status(request, pk):
    if not request.user.is_active or not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Acesso negado.'}, status=403)
    try:
        job = RemoteInstallJob.objects.select_related('endpoint').get(pk=pk)
    except RemoteInstallJob.DoesNotExist:
        return JsonResponse({'error': 'Operacao nao encontrada.'}, status=404)
    data = {'id': str(job.pk), 'status': job.status, 'stage': job.stage,
            'diagnostics': job.diagnostics,
            'error_code': job.error_code, 'created_at': job.created_at.isoformat(),
            'started_at': job.started_at.isoformat() if job.started_at else None,
            'finished_at': job.finished_at.isoformat() if job.finished_at else None,
            'hostname': job.target_hostname, 'fqdn': job.target_fqdn,
            'endpoint_id': str(job.endpoint_id) if job.endpoint_id else None,
            'first_heartbeat_at': job.endpoint.last_seen_at.isoformat() if job.endpoint and job.status == 'COMPLETED' and job.endpoint.last_seen_at else None,
            'endpoint_url': reverse('endpoint-detail', args=[job.endpoint_id]) if job.endpoint_id and job.status == 'COMPLETED' else None}
    response = JsonResponse(data)
    response['Cache-Control'] = 'no-store'
    response['Pragma'] = 'no-cache'
    return response
