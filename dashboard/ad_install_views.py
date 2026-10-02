import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from config.authz import is_nightowl_technical_user
from dashboard.ad_install_discovery import build_install_discovery
from dashboard.remote_install_preflight import run_remote_install_preflight


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
        if not isinstance(data, dict):
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
