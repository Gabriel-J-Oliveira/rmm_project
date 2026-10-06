"""Staff-only batch APIs. GET projections never reconcile or probe targets."""
import json
import uuid

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_http_methods, require_POST

from dashboard.models import RemoteInstallBatch
from dashboard.remote_install import InstallFailure
from dashboard.remote_install_preflight import ProbeFailure
from dashboard.remote_install_batch import batch_payload, create_batch, resolve_stall, retry_batch, start_batch


def _response(data, status=200):
    response = JsonResponse(data, status=status)
    response['Cache-Control'] = 'no-store'
    response['Pragma'] = 'no-cache'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _authorized(request):
    return request.user.is_active and (request.user.is_staff or request.user.is_superuser)


@sensitive_variables('data')
def _body(request, keys):
    if not request.is_secure():
        raise InstallFailure('HTTPS_REQUIRED')
    length = request.META.get('CONTENT_LENGTH', '')
    if (not length.isascii() or not length.isdecimal() or len(length) > 6
            or not 0 < int(length) <= 32768 or request.content_type != 'application/json'):
        raise InstallFailure('INVALID_REQUEST')
    try:
        if len(request.body) > 32768:
            raise ValueError()
        def no_duplicate_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        data = json.loads(request.body, object_pairs_hook=no_duplicate_keys)
        if not isinstance(data, dict) or set(data) != keys:
            raise ValueError()
        return data
    except (ValueError, UnicodeError):
        raise InstallFailure('INVALID_REQUEST') from None


def _accepted(batch):
    return _response({'batch_id': str(batch.pk), 'status_url': reverse('agent-install-batch-detail', args=[batch.pk]),
                      'list_url': reverse('agent-install-batches')}, 202)


@login_required
@csrf_protect
@require_http_methods(['GET', 'POST'])
@sensitive_post_parameters('password')
@sensitive_variables('data')
def batches(request):
    if not _authorized(request):
        return _response({'error_code': 'ACCESS_DENIED'}, 403)
    if request.method == 'GET':
        try:
            page = int(request.GET.get('page', '1'))
            if not 1 <= page <= 10000:
                raise ValueError()
        except ValueError:
            return _response({'error_code': 'INVALID_PAGE'}, 400)
        result = Paginator(RemoteInstallBatch.objects.prefetch_related('items__remote_install_job'), 20).get_page(page)
        return _response({'results': [batch_payload(batch) for batch in result],
                          'page': result.number, 'pages': result.paginator.num_pages})
    data = None
    try:
        data = _body(request, {'targets', 'username', 'password'})
        batch = create_batch(data['targets'], request.user, username=data['username'], password=data['password'])
        start_batch(batch, data['username'], data['password'])
        return _accepted(batch)
    except (InstallFailure, ProbeFailure) as exc:
        from dashboard.remote_install_batch import safe_code
        return _response({'error_code': safe_code(exc.code)}, 403 if exc.code == 'HTTPS_REQUIRED' else 409)
    except Exception:
        return _response({'error_code': 'BATCH_UNAVAILABLE'}, 503)
    finally:
        if data:
            data.clear()


@login_required
@require_http_methods(['GET'])
def batch_detail(request, pk):
    if not _authorized(request):
        return _response({'error_code': 'ACCESS_DENIED'}, 403)
    batch = RemoteInstallBatch.objects.filter(pk=pk).first()
    if not batch:
        return _response({'error_code': 'BATCH_NOT_FOUND'}, 404)
    return _response(batch_payload(batch, detail=True))


@login_required
@csrf_protect
@require_POST
@sensitive_post_parameters('password')
@sensitive_variables('data')
def batch_retry(request, pk):
    if not _authorized(request):
        return _response({'error_code': 'ACCESS_DENIED'}, 403)
    parent = RemoteInstallBatch.objects.filter(pk=pk).first()
    if not parent:
        return _response({'error_code': 'BATCH_NOT_FOUND'}, 404)
    data = None
    try:
        data = _body(request, {'username', 'password'})
        batch = retry_batch(parent, request.user, data['username'], data['password'])
        start_batch(batch, data['username'], data['password'])
        return _accepted(batch)
    except (InstallFailure, ProbeFailure) as exc:
        from dashboard.remote_install_batch import safe_code
        return _response({'error_code': safe_code(exc.code)}, 409)
    except Exception:
        return _response({'error_code': 'BATCH_UNAVAILABLE'}, 503)
    finally:
        if data:
            data.clear()


@login_required
@csrf_protect
@require_POST
def batch_mark_stalled(request, pk):
    if not _authorized(request):
        return _response({'error_code': 'ACCESS_DENIED'}, 403)
    try:
        data = _body(request, {'item_id'})
        item = resolve_stall(pk, uuid.UUID(data['item_id']), actor=request.user)
        return _response({'item_id': str(item.pk), 'status': item.status})
    except InstallFailure as exc:
        return _response({'error_code': exc.code}, 409)
    except (ValueError, TypeError, AttributeError):
        return _response({'error_code': 'INVALID_REQUEST'}, 400)
    except Exception:
        return _response({'error_code': 'ACTION_NOT_AVAILABLE'}, 409)
