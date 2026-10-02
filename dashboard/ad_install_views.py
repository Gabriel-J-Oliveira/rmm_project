from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from config.authz import is_nightowl_technical_user
from dashboard.ad_install_discovery import build_install_discovery


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
