"""Authenticated API for private software-package binaries and lifecycle."""
import json
import logging
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from agents.models import AgentJob, AuditEvent, AutomationDefinition, SoftwarePackageRecord
from agents.software_package_storage import (
    PackageUploadError, configured_upload_limit, open_private, promote_staged,
    private_root, stage_upload, unlink_private,
)
from config.authz import is_nightowl_technical_user


ACTIVE_JOB_STATES = (AgentJob.STATUS_QUEUED, AgentJob.STATUS_SENT, AgentJob.STATUS_RUNNING)
PACKAGE_JOB_TYPES = (AgentJob.TYPE_INSTALL_SOFTWARE,)
PACKAGE_OPERATION_AUDIT_TYPES = (
    'software_package.operation_started', 'software.package.install_started',
    'software.package.install_dispatched', 'software.install.started',
)
logger = logging.getLogger(__name__)


class PackageActionError(Exception):
    def __init__(self, code, status=400):
        self.code = code
        self.status = status
        super().__init__(code)


def _authorized(request, permission=None):
    user = request.user
    if not (user.is_authenticated and user.is_active and is_nightowl_technical_user(user)):
        return False
    if permission is None:
        return True
    return bool(user.is_superuser or user.has_perm(permission))


def _deny(permission=None):
    return JsonResponse({'error': 'forbidden'}, status=403)


def _check_permission(request, permission=None):
    if not _authorized(request, permission):
        return _deny(permission)
    return None


def _body(request):
    if len(request.body) > 64 * 1024:
        raise PackageActionError('request_too_large', 413)
    try:
        payload = json.loads(request.body or b'{}')
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PackageActionError('invalid_json')
    if not isinstance(payload, dict):
        raise PackageActionError('invalid_request')
    return payload


def _required_text(value, field, max_length):
    value = str(value or '').strip()
    if not value:
        raise PackageActionError(field + '_required')
    if len(value) > max_length:
        raise PackageActionError(field + '_too_long')
    return value


def _reason(value):
    return _required_text(value, 'reason', 500)


def _snapshot(package):
    return {
        'id': str(package.pk), 'friendly_name': package.friendly_name,
        'original_filename': package.original_filename, 'description': package.description,
        'package_type': package.package_type, 'version': package.version,
        'size_bytes': package.size_bytes, 'sha256': package.sha256,
        'signature_status': package.signature_status, 'signer': package.signer,
        'approval_status': package.approval_status, 'revision': package.revision,
        'artifact_uploaded_at': package.artifact_uploaded_at.isoformat() if package.artifact_uploaded_at else None,
    }


def _audit(request, event_type, title, *, package_id, before=None, after=None, reason=''):
    AuditEvent.objects.create(
        event_type=event_type, actor_type=AuditEvent.ACTOR_USER,
        actor_name=request.user.get_username(), title=title, description=reason,
        metadata={
            'software_package_id': str(package_id),
            'before': before,
            'after': after,
            'reason': reason,
        },
    )


def _job_package_query(package):
    from django.db.models import Q
    identity = str(package.pk)
    return AgentJob.objects.filter(job_type__in=PACKAGE_JOB_TYPES).filter(
        Q(payload__software_package_id=identity) | Q(payload__package_id=identity)
    )


def _has_active_operation(package):
    return _job_package_query(package).filter(status__in=ACTIVE_JOB_STATES).exists()


def _has_been_used(package):
    from django.db.models import Q
    identity = str(package.pk)
    if _job_package_query(package).exists():
        return True
    if AutomationDefinition.objects.filter(job_type=AgentJob.TYPE_INSTALL_SOFTWARE).filter(
        Q(payload__software_package_id=identity) | Q(payload__package_id=identity)
    ).exists():
        return True
    return AuditEvent.objects.filter(event_type__in=PACKAGE_OPERATION_AUDIT_TYPES).filter(
        Q(metadata__software_package_id=identity) | Q(metadata__package_id=identity)
    ).exists()


def _package_data(package, request=None):
    return {
        **_snapshot(package),
        'metadata': package.metadata or {},
        'has_file': bool(package.artifact_key),
        'created_at': package.created_at.isoformat(),
        'created_by': package.created_by.get_username() if package.created_by else None,
        'uploaded_by': package.uploaded_by.get_username() if package.uploaded_by else None,
        'last_modified_by': package.last_modified_by.get_username() if package.last_modified_by else None,
        'approved_at': package.approved_at.isoformat() if package.approved_at else None,
        'approved_by': package.approved_by.get_username() if package.approved_by else None,
        'revoked_at': package.revoked_at.isoformat() if package.revoked_at else None,
        'revoked_by': package.revoked_by.get_username() if package.revoked_by else None,
        'updated_at': package.updated_at.isoformat(),
        'download_url': f'/software/packages/{package.pk}/download/',
        'active_operations': _has_active_operation(package),
        'used': _has_been_used(package),
    }


def _response_error(error):
    return JsonResponse({'error': error.code}, status=error.status)


def _upload_size_error(request, max_bytes):
    raw_length = request.META.get('CONTENT_LENGTH')
    if not raw_length:
        return None
    try:
        length = int(raw_length)
    except (TypeError, ValueError):
        return JsonResponse({'error': 'invalid_content_length'}, status=400)
    if length < 0:
        return JsonResponse({'error': 'invalid_content_length'}, status=400)
    if length > max_bytes + 1024 * 1024:
        return JsonResponse({'error': 'upload_too_large', 'max_bytes': max_bytes}, status=413)
    return None


def _cleanup_private_file(key):
    try:
        unlink_private(key)
    except (ImproperlyConfigured, OSError):
        # Leave an orphan rather than report a committed mutation as failed or
        # risk removing the artifact now referenced by the database.
        logger.warning('Private software package cleanup deferred for a generated artifact key.')
        return


def _revision(payload, package):
    try:
        revision = int(payload.get('revision'))
    except (TypeError, ValueError):
        raise PackageActionError('revision_required')
    if revision != package.revision:
        raise PackageActionError('stale_revision', 409)


def _verify_storage_artifact(package):
    if not package.artifact_key or not package.sha256 or package.size_bytes is None:
        return False
    try:
        handle = open_private(package.artifact_key)
    except ImproperlyConfigured:
        raise
    except (OSError, ValueError):
        return False
    if handle is None:
        return False
    import hashlib
    digest = hashlib.sha256()
    size = 0
    with handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            size += len(chunk)
            digest.update(chunk)
    return size == package.size_bytes and digest.hexdigest() == package.sha256


@login_required
@require_http_methods(['POST'])
@never_cache
def software_package_create(request):
    denied = _check_permission(request, 'agents.add_softwarepackagerecord')
    if denied:
        return denied
    try:
        max_bytes = configured_upload_limit()
    except ImproperlyConfigured:
        return JsonResponse({'error': 'upload_limit_invalid'}, status=503)
    size_error = _upload_size_error(request, max_bytes)
    if size_error:
        return size_error
    uploaded = request.FILES.get('file')
    if uploaded is None:
        return JsonResponse({'error': 'file_required'}, status=400)
    staged = None
    final_key = ''
    package_id = None
    committed = False
    try:
        friendly_name = _required_text(request.POST.get('friendly_name'), 'friendly_name', 180)
        version = str(request.POST.get('version') or '').strip()
        description = str(request.POST.get('description') or '').strip()
        if len(version) > 120:
            raise PackageActionError('version_too_long')
        if len(description) > 10000:
            raise PackageActionError('description_too_long')
        staged = stage_upload(uploaded)
        from uuid import uuid4
        package_id = uuid4()
        final_key = promote_staged(staged, package_id)
        now = timezone.now()
        with transaction.atomic():
            package = SoftwarePackageRecord.objects.create(
                id=package_id, friendly_name=friendly_name, original_filename=staged.original_filename,
                description=description, package_type=staged.package_type, version=version,
                artifact_key=final_key, artifact_uploaded_at=now, uploaded_by=request.user,
                last_modified_by=request.user, revision=1, size_bytes=staged.size_bytes,
                sha256=staged.sha256, signature_status=SoftwarePackageRecord.SIGNATURE_UNKNOWN,
                signer='', approval_status=SoftwarePackageRecord.STATUS_DRAFT, created_by=request.user,
                metadata={'detected_format': staged.detected_format, 'authenticode': 'not_verified'},
            )
            _audit(request, 'software_package.created', 'Instalador cadastrado', package_id=package.pk,
                   after=_snapshot(package), reason='Cadastro inicial; aprovação pendente.')
            response_item = _package_data(package, request)
        committed = True
        return JsonResponse({'item': response_item}, status=201)
    except PackageUploadError as exc:
        if final_key and not committed:
            unlink_private(final_key)
        return JsonResponse({'error': exc.code}, status=413 if exc.code == 'upload_too_large' else 400)
    except PackageActionError as exc:
        if final_key and not committed:
            unlink_private(final_key)
        return _response_error(exc)
    except ImproperlyConfigured:
        if final_key and not committed:
            unlink_private(final_key)
        return JsonResponse({'error': 'storage_unconfigured'}, status=503)
    except Exception:
        if final_key:
            unlink_private(final_key)
        return JsonResponse({'error': 'package_storage_failed'}, status=503)
    finally:
        if staged and staged.path.exists():
            staged.path.unlink(missing_ok=True)


@login_required
@require_http_methods(['GET'])
@never_cache
def software_package_detail(request, pk):
    denied = _check_permission(request, 'agents.view_softwarepackagerecord')
    if denied:
        return denied
    package = get_object_or_404(SoftwarePackageRecord.objects.select_related(
        'created_by', 'uploaded_by', 'last_modified_by', 'approved_by', 'revoked_by'), pk=pk)
    events = AuditEvent.objects.filter(metadata__software_package_id=str(package.pk),
        event_type__startswith='software_package.').order_by('-created_at')[:100]
    return JsonResponse({'item': _package_data(package, request), 'audit': [{
        'event_type': event.event_type, 'title': event.title, 'reason': event.description,
        'actor': event.actor_name, 'created_at': event.created_at.isoformat(),
        'before': event.metadata.get('before'), 'after': event.metadata.get('after'),
    } for event in events]})


@login_required
@require_http_methods(['POST'])
@never_cache
def software_package_update(request, pk):
    denied = _check_permission(request, 'agents.change_softwarepackagerecord')
    if denied:
        return denied
    try:
        payload = _body(request)
        allowed = {'revision', 'friendly_name', 'version', 'description'}
        if set(payload) - allowed or not (set(payload) & (allowed - {'revision'})):
            raise PackageActionError('invalid_fields')
        with transaction.atomic():
            package = get_object_or_404(SoftwarePackageRecord.objects.select_for_update(), pk=pk)
            _revision(payload, package)
            if not package.artifact_key:
                raise PackageActionError('file_required', 409)
            before = _snapshot(package)
            if 'friendly_name' in payload:
                package.friendly_name = _required_text(payload['friendly_name'], 'friendly_name', 180)
            if 'version' in payload:
                package.version = str(payload['version'] or '').strip()
                if len(package.version) > 120:
                    raise PackageActionError('version_too_long')
            if 'description' in payload:
                package.description = str(payload['description'] or '').strip()
                if len(package.description) > 10000:
                    raise PackageActionError('description_too_long')
            package.revision += 1
            package.last_modified_by = request.user
            package.save(update_fields=['friendly_name', 'version', 'description', 'revision',
                                        'last_modified_by', 'updated_at'])
            _audit(request, 'software_package.metadata_updated', 'Cadastro de instalador atualizado',
                   package_id=package.pk, before=before, after=_snapshot(package))
        return JsonResponse({'item': _package_data(package, request)})
    except PackageActionError as exc:
        return _response_error(exc)


@login_required
@require_http_methods(['POST'])
@never_cache
def software_package_replace(request, pk):
    denied = _check_permission(request, 'agents.change_softwarepackagerecord')
    if denied:
        return denied
    try:
        max_bytes = configured_upload_limit()
    except ImproperlyConfigured:
        return JsonResponse({'error': 'upload_limit_invalid'}, status=503)
    size_error = _upload_size_error(request, max_bytes)
    if size_error:
        return size_error
    uploaded = request.FILES.get('file')
    if uploaded is None:
        return JsonResponse({'error': 'file_required'}, status=400)
    staged = None
    final_key = ''
    old_key = ''
    committed = False
    try:
        payload = {'revision': request.POST.get('revision'), 'reason': request.POST.get('reason')}
        reason = _reason(payload['reason'])
        staged = stage_upload(uploaded)
        now = timezone.now()
        with transaction.atomic():
            package = get_object_or_404(SoftwarePackageRecord.objects.select_for_update(), pk=pk)
            _revision(payload, package)
            if _has_active_operation(package):
                raise PackageActionError('package_in_use_by_active_operation', 409)
            before = _snapshot(package)
            old_key = package.artifact_key
            final_key = promote_staged(staged, package.pk)
            package.original_filename = staged.original_filename
            package.package_type = staged.package_type
            package.size_bytes = staged.size_bytes
            package.sha256 = staged.sha256
            package.artifact_key = final_key
            package.artifact_uploaded_at = now
            package.uploaded_by = request.user
            package.signature_status = SoftwarePackageRecord.SIGNATURE_UNKNOWN
            package.signer = ''
            package.approval_status = SoftwarePackageRecord.STATUS_DRAFT
            package.approved_at = None
            package.approved_by = None
            package.revoked_at = None
            package.revoked_by = None
            package.last_modified_by = request.user
            package.revision += 1
            package.metadata = {**(package.metadata or {}), 'detected_format': staged.detected_format,
                                'authenticode': 'not_verified'}
            package.save()
            _audit(request, 'software_package.artifact_replaced', 'Instalador substituído; aprovação invalidada',
                   package_id=package.pk, before=before, after=_snapshot(package), reason=reason)
            transaction.on_commit(lambda key=old_key: _cleanup_private_file(key))
            response_item = _package_data(package, request)
        committed = True
        return JsonResponse({'item': response_item})
    except PackageUploadError as exc:
        if final_key and not committed:
            unlink_private(final_key)
        return JsonResponse({'error': exc.code}, status=413 if exc.code == 'upload_too_large' else 400)
    except PackageActionError as exc:
        if final_key and not committed:
            unlink_private(final_key)
        return _response_error(exc)
    except ImproperlyConfigured:
        if final_key and not committed:
            unlink_private(final_key)
        return JsonResponse({'error': 'storage_unconfigured'}, status=503)
    except Exception:
        if final_key:
            unlink_private(final_key)
        return JsonResponse({'error': 'package_storage_failed'}, status=503)
    finally:
        if staged and staged.path.exists():
            staged.path.unlink(missing_ok=True)


def _status_action(request, pk, action):
    permission = 'agents.approve_softwarepackage' if action == 'approve' else 'agents.revoke_softwarepackage'
    denied = _check_permission(request, permission)
    if denied:
        return denied
    try:
        payload = _body(request)
        if set(payload) != {'revision', 'reason'}:
            raise PackageActionError('invalid_fields')
        reason = _reason(payload['reason'])
        with transaction.atomic():
            package = get_object_or_404(SoftwarePackageRecord.objects.select_for_update(), pk=pk)
            _revision(payload, package)
            before = _snapshot(package)
            if action == 'approve':
                try:
                    artifact_valid = _verify_storage_artifact(package)
                except ImproperlyConfigured:
                    return JsonResponse({'error': 'storage_unconfigured'}, status=503)
                if not artifact_valid:
                    raise PackageActionError('artifact_missing_or_integrity_failed', 409)
                package.approval_status = SoftwarePackageRecord.STATUS_APPROVED
                package.approved_at = timezone.now()
                package.approved_by = request.user
                package.revoked_at = None
                package.revoked_by = None
            else:
                package.approval_status = SoftwarePackageRecord.STATUS_REVOKED
                package.revoked_at = timezone.now()
                package.revoked_by = request.user
            package.revision += 1
            package.last_modified_by = request.user
            package.save()
            _audit(request, 'software_package.approved' if action == 'approve' else 'software_package.revoked',
                   'Instalador aprovado' if action == 'approve' else 'Instalador revogado',
                   package_id=package.pk, before=before, after=_snapshot(package), reason=reason)
        return JsonResponse({'item': _package_data(package, request)})
    except PackageActionError as exc:
        return _response_error(exc)


@login_required
@require_http_methods(['POST'])
@never_cache
def software_package_approve(request, pk):
    return _status_action(request, pk, 'approve')


@login_required
@require_http_methods(['POST'])
@never_cache
def software_package_revoke(request, pk):
    return _status_action(request, pk, 'revoke')


@login_required
@require_http_methods(['POST'])
@never_cache
def software_package_delete(request, pk):
    denied = _check_permission(request, 'agents.delete_softwarepackagerecord')
    if denied:
        return denied
    old_key = ''
    try:
        payload = _body(request)
        if set(payload) != {'revision', 'reason'}:
            raise PackageActionError('invalid_fields')
        reason = _reason(payload['reason'])
        with transaction.atomic():
            package = get_object_or_404(SoftwarePackageRecord.objects.select_for_update(), pk=pk)
            _revision(payload, package)
            if _has_active_operation(package):
                raise PackageActionError('package_in_use_by_active_operation', 409)
            if _has_been_used(package):
                raise PackageActionError('package_has_operational_history', 409)
            before = _snapshot(package)
            old_key = package.artifact_key
            _audit(request, 'software_package.deleted', 'Cadastro de instalador excluído',
                   package_id=package.pk, before=before, after=None, reason=reason)
            package.delete()
            transaction.on_commit(lambda key=old_key: _cleanup_private_file(key))
        return JsonResponse({'deleted': True, 'id': str(pk)})
    except PackageActionError as exc:
        return _response_error(exc)


@login_required
@require_http_methods(['GET'])
@never_cache
def software_package_download(request, pk):
    denied = _check_permission(request, 'agents.download_softwarepackage')
    if denied:
        return denied
    package = get_object_or_404(SoftwarePackageRecord, pk=pk)
    if not package.artifact_key:
        return JsonResponse({'error': 'artifact_missing'}, status=404)
    try:
        handle = open_private(package.artifact_key)
    except (ImproperlyConfigured, OSError, ValueError):
        handle = None
    if handle is None:
        return JsonResponse({'error': 'artifact_missing'}, status=404)
    try:
        _audit(request, 'software_package.downloaded', 'Arquivo de instalador baixado', package_id=package.pk,
               after={'sha256': package.sha256, 'size_bytes': package.size_bytes},
               reason='Download autenticado por operador.')
    except Exception:
        handle.close()
        logger.exception('Private software package download audit failed.')
        return JsonResponse({'error': 'audit_unavailable'}, status=503)
    response = FileResponse(handle, as_attachment=True, filename=package.original_filename or f'{package.pk}.{package.package_type}')
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Type'] = 'application/octet-stream'
    response['Content-Length'] = str(package.size_bytes or 0)
    return response
