"""Fail-closed preparation contract for future typed software AgentJobs.

This module deliberately does not create AgentJob rows or expose an HTTP action.
RC49-03C-B must add the compatible agent download/execute capability first.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from config.authz import is_nightowl_technical_user
from .internal_jobs import internal_capability
from .models import AgentJob, AgentMachine, SoftwarePackageRecord
from .software_execution_profiles import package_profile_binding_matches, operation_is_configured
from .software_package_storage import open_private


OPERATION_CAPABILITIES = {
    'install': 'software_install_profile:1',
    'update': 'software_update_profile:1',
    'uninstall': 'software_uninstall_profile:1',
}
ACTIVE_STATES = (AgentJob.STATUS_QUEUED, AgentJob.STATUS_SENT, AgentJob.STATUS_RUNNING)


class SoftwareOperationBlocked(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _artifact_matches(package):
    if not package.artifact_key or not package.sha256 or package.size_bytes is None:
        return False
    try:
        handle = open_private(package.artifact_key)
    except (OSError, ValueError):
        return False
    if handle is None:
        return False
    digest = hashlib.sha256()
    size = 0
    try:
        with handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                size += len(chunk)
                digest.update(chunk)
    except (OSError, ValueError):
        return False
    return size == package.size_bytes and digest.hexdigest() == package.sha256


@transaction.atomic
def prepare_software_operation_snapshot(operator, endpoint_id, package_id, operation, request_id):
    """Validate a future operation snapshot without queueing a job.

    A future materializer must wrap this call and AgentJob creation in one outer
    ``transaction.atomic()`` block. This nested atomic block then retains these
    row locks until the outer materialization transaction commits.
    """
    if not (operator and operator.is_authenticated and operator.is_active
            and is_nightowl_technical_user(operator)
            and (operator.is_superuser or operator.has_perm('agents.execute_softwarepackageoperation'))):
        raise PermissionDenied('software_operation_forbidden')
    try:
        normalized_request_id = str(uuid.UUID(str(request_id)))
    except (ValueError, TypeError, AttributeError):
        raise SoftwareOperationBlocked('request_id_invalid') from None
    if operation not in OPERATION_CAPABILITIES:
        raise SoftwareOperationBlocked('operation_unsupported')
    if not getattr(settings, 'NIGHTOWL_SOFTWARE_OPERATION_DISPATCH_ENABLED', False):
        raise SoftwareOperationBlocked('software_dispatch_not_enabled')

    endpoint = AgentMachine.objects.select_for_update().filter(pk=endpoint_id).first()
    package = SoftwarePackageRecord.objects.select_for_update().filter(pk=package_id).first()
    if endpoint is None or package is None:
        raise SoftwareOperationBlocked('target_not_found')
    now = timezone.now()
    if (not endpoint.is_active or endpoint.has_terminal_lifecycle or not endpoint.machine_id
            or AgentMachine.objects.filter(machine_id__iexact=endpoint.machine_id).count() != 1):
        raise SoftwareOperationBlocked('endpoint_identity_invalid')
    if (endpoint.status != AgentMachine.STATUS_ONLINE or not endpoint.last_seen_at
            or endpoint.last_seen_at < now - timedelta(minutes=15)):
        raise SoftwareOperationBlocked('endpoint_offline')
    if (package.approval_status != SoftwarePackageRecord.STATUS_APPROVED
            or not package.approved_at or not package.approved_by_id):
        raise SoftwareOperationBlocked('package_not_approved')
    if (package.execution_profile_status != 'approved'
            or not package.execution_profile_approved_at
            or not package.execution_profile_approved_by_id
            or not package_profile_binding_matches(package)):
        raise SoftwareOperationBlocked('execution_profile_not_approved_for_artifact')
    if not operation_is_configured(package.execution_profile, operation):
        raise SoftwareOperationBlocked('operation_not_configured')
    if not _artifact_matches(package):
        raise SoftwareOperationBlocked('artifact_integrity_failed')
    if AgentJob.objects.filter(endpoint=endpoint, status__in=ACTIVE_STATES).exists():
        raise SoftwareOperationBlocked('endpoint_job_conflict')
    capability = OPERATION_CAPABILITIES[operation]
    if not internal_capability(endpoint, capability):
        raise SoftwareOperationBlocked('agent_capability_unconfirmed')

    # This complete snapshot is the input contract for the later materializer.
    # It carries no URL, command string, credential, or caller-controlled path.
    return {
        'schema_version': 1,
        'operation': operation,
        'request_id': normalized_request_id,
        'endpoint': {'uuid': str(endpoint.pk), 'machine_id': endpoint.machine_id},
        'package': {
            'uuid': str(package.pk), 'package_type': package.package_type,
            'approval_status': package.approval_status,
            'artifact_approved_at': package.approved_at.isoformat(),
            'artifact_approved_by': str(package.approved_by_id),
            'artifact_sha256': package.sha256, 'artifact_revision': package.artifact_revision,
            'profile_revision': package.execution_profile_revision,
            'profile_sha256': package.execution_profile_sha256,
            'profile_status': package.execution_profile_status,
            'profile_approved_at': package.execution_profile_approved_at.isoformat(),
            'profile_approved_by': str(package.execution_profile_approved_by_id),
            'profile': package.execution_profile,
        },
        'operator': {
            'id': str(operator.pk), 'username': operator.get_username(),
            'authorization': 'agents.execute_softwarepackageoperation',
        },
        'created_at': now.isoformat(),
    }
