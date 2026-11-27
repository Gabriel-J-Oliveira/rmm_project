"""Authenticated creation and agent-only artifact delivery for typed software jobs."""
from __future__ import annotations

import json
import uuid

from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db import IntegrityError, transaction
from django.http import FileResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.authz import is_nightowl_technical_user
from .audit import create_audit_event
from .authentication import authenticate_agent_token
from .models import AgentMachine, AuditEvent, SoftwarePackageRecord
from .software_operation_contract import (
    SoftwareOperationBlocked,
    create_software_operation_job,
    validate_software_operation_download,
    validate_software_operation_profile,
)




def _no_store(response):
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _operator_allowed(user):
    return bool(
        user and user.is_authenticated and user.is_active and is_nightowl_technical_user(user)
        and (user.is_superuser or user.has_perm('agents.execute_softwarepackageoperation'))
    )


@require_POST
@csrf_protect
@never_cache
def software_operation_create(request, package_id, endpoint_id):
    if not _operator_allowed(request.user):
        return _no_store(JsonResponse({'error': 'forbidden'}, status=403))
    if len(request.body) > 16 * 1024:
        return _no_store(JsonResponse({'error': 'request_too_large'}, status=413))
    try:
        body = json.loads(request.body or b'{}')
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _no_store(JsonResponse({'error': 'invalid_json'}, status=400))
    if not isinstance(body, dict) or set(body) != {'request_id', 'operation'}:
        return _no_store(JsonResponse({'error': 'invalid_request'}, status=400))
    try:
        request_id = str(uuid.UUID(body['request_id']))
        if request_id != body['request_id'] or body['operation'] not in {'install', 'update', 'uninstall'}:
            raise ValueError('invalid_request')
        operation, created = create_software_operation_job(
            request.user, endpoint_id, package_id, body['operation'], request_id,
        )
    except PermissionDenied:
        return _no_store(JsonResponse({'error': 'forbidden'}, status=403))
    except SoftwareOperationBlocked as exc:
        status_code = 409 if exc.code != 'request_id_invalid' else 400
        return _no_store(JsonResponse({'error': exc.code}, status=status_code))
    except (ValueError, TypeError, AttributeError):
        return _no_store(JsonResponse({'error': 'invalid_request'}, status=400))
    except (AgentMachine.DoesNotExist, SoftwarePackageRecord.DoesNotExist):
        return _no_store(JsonResponse({'error': 'endpoint_not_found'}, status=404))
    except IntegrityError:
        # A concurrent use of the same globally unique key for another target
        # loses the database uniqueness race and is reported as a conflict.
        return _no_store(JsonResponse({'error': 'request_id_conflict'}, status=409))
    return _no_store(JsonResponse({
        'operation_id': str(operation.pk),
        'job_id': str(operation.job_id) if operation.job_id else None,
        'request_id': str(operation.request_id),
        'status': operation.job.status if operation.job_id else 'history_retained',
        'created': created,
        'schema_version': 2,
    }, status=202 if created else 200))


class AgentSoftwareOperationArtifactView(APIView):
    """Machine bearer-authenticated, job-scoped streaming download; no URL credentials."""

    authentication_classes = []
    permission_classes = []

    @transaction.atomic
    def get(self, request, job_id):
        machine = authenticate_agent_token(request)
        if request.GET:
            # The download contract has no query parameters, especially no token.
            return _no_store(Response({'error': 'invalid_request'}, status=400))
        try:
            job, operation, package, handle = validate_software_operation_download(machine, job_id)
        except (SoftwareOperationBlocked, ValueError):
            # Avoid disclosing whether another endpoint owns the job.
            return _no_store(Response({'error': 'operation_artifact_not_found'}, status=404))
        except (ImproperlyConfigured, OSError):
            return _no_store(Response({'error': 'operation_artifact_unavailable'}, status=503))
        event = create_audit_event(
            event_type='software_package.operation_artifact_downloaded',
            title='Agente autenticado baixou artefato da operação',
            description='Download privado associado ao job e à identidade do endpoint.',
            actor_type=AuditEvent.ACTOR_AGENT,
            actor_name='NightOwlAgent', endpoint=machine,
            metadata={
                'job_id': str(job.pk), 'operation_id': str(operation.pk),
                'package_uuid': str(package.pk), 'artifact_sha256': operation.artifact_sha256,
                'artifact_revision': operation.artifact_revision,
            },
        )
        if event is None:
            handle.close()
            return _no_store(Response({'error': 'audit_unavailable'}, status=503))
        response = FileResponse(handle, as_attachment=True,
                                filename=f'{package.pk}.{package.package_type}')
        response['Content-Type'] = 'application/octet-stream'
        response['Content-Length'] = str(package.size_bytes or 0)
        response['X-Software-Artifact-SHA256'] = operation.artifact_sha256
        response['X-Software-Artifact-Revision'] = str(operation.artifact_revision)
        return _no_store(response)


class AgentSoftwareOperationProfileView(APIView):
    """Bearer-authenticated immutable job profile, reauthorized at read time."""

    authentication_classes = []
    permission_classes = []

    @transaction.atomic
    def get(self, request, job_id):
        machine = authenticate_agent_token(request)
        if request.GET:
            return _no_store(Response({'error': 'invalid_request'}, status=400))
        try:
            _job, _operation, profile = validate_software_operation_profile(machine, job_id)
        except (SoftwareOperationBlocked, ValueError):
            return _no_store(Response({'error': 'operation_profile_not_found'}, status=404))
        return _no_store(Response(profile, status=200))
