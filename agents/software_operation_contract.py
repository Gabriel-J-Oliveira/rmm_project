"""Fail-closed contracts for versioned software AgentJobs and private artifacts."""
from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db import transaction
from django.utils import timezone

from config.authz import is_nightowl_technical_user
from .internal_jobs import SOFTWARE_OPERATION_CAPABILITIES, internal_capability
from .models import AgentJob, AgentMachine, AuditEvent, SoftwarePackageOperation, SoftwarePackageRecord
from .software_execution_profiles import package_profile_binding_matches, operation_is_configured
from .software_package_storage import open_private


OPERATION_CAPABILITIES = SOFTWARE_OPERATION_CAPABILITIES
ACTIVE_STATES = (AgentJob.STATUS_QUEUED, AgentJob.STATUS_SENT, AgentJob.STATUS_RUNNING)
SOFTWARE_JOB_SCHEMA_VERSION = 2


class SoftwareOperationBlocked(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _artifact_matches(package, *, handle=None):
    if not package.artifact_key or not package.sha256 or package.size_bytes is None:
        return False
    close_handle = handle is None
    if close_handle:
        try:
            handle = open_private(package.artifact_key)
        except (OSError, ValueError, ImproperlyConfigured):
            return False
        if handle is None:
            return False
    digest = hashlib.sha256()
    size = 0
    try:
        handle.seek(0)
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            size += len(chunk)
            digest.update(chunk)
        handle.seek(0)
    except (OSError, ValueError):
        return False
    finally:
        if close_handle:
            handle.close()
    return size == package.size_bytes and digest.hexdigest() == package.sha256


@transaction.atomic
def prepare_software_operation_snapshot(operator, endpoint_id, package_id, operation, request_id):
    """Validate a snapshot; a caller's outer transaction retains these row locks."""
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
    endpoint = AgentMachine.objects.select_for_update().filter(pk=endpoint_id).first()
    package = SoftwarePackageRecord.objects.select_for_update().filter(pk=package_id).first()
    if endpoint is None or package is None:
        raise SoftwareOperationBlocked('target_not_found')
    if package.package_type != SoftwarePackageRecord.TYPE_MSI:
        raise SoftwareOperationBlocked('agent_executor_not_supported')
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
        'schema_version': SOFTWARE_JOB_SCHEMA_VERSION,
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


def software_operation_payload(operation_record):
    """Return the narrow agent envelope; never include a URL, path, or credential."""
    return {
        'schema_version': SOFTWARE_JOB_SCHEMA_VERSION,
        'operation_id': str(operation_record.pk),
        'operation': operation_record.operation,
        'request_id': str(operation_record.request_id),
        'package_uuid': str(operation_record.package_id),
        'artifact_sha256': operation_record.artifact_sha256,
        'artifact_revision': operation_record.artifact_revision,
        'profile_revision': operation_record.profile_revision,
        'profile_sha256': operation_record.profile_sha256,
    }


@transaction.atomic
def create_software_operation_job(operator, endpoint_id, package_id, operation, request_id):
    """Create one approved operation and its AgentJob atomically, behind the dispatch gate."""
    try:
        normalized_request_id = str(uuid.UUID(str(request_id)))
    except (ValueError, TypeError, AttributeError):
        raise SoftwareOperationBlocked('request_id_invalid') from None
    if not (operator and operator.is_authenticated and operator.is_active
            and is_nightowl_technical_user(operator)
            and (operator.is_superuser or operator.has_perm('agents.execute_softwarepackageoperation'))):
        raise PermissionDenied('software_operation_forbidden')

    # Idempotency keys are global UUIDs. A replay for another intent is a conflict,
    # never a second job. This lookup also gives a stable answer after dispatch.
    existing = SoftwarePackageOperation.objects.select_related('job').filter(request_id=normalized_request_id).first()
    if existing:
        if (existing.endpoint_uuid_snapshot != uuid.UUID(str(endpoint_id))
                or existing.package_id != uuid.UUID(str(package_id))
                or existing.operation != operation or existing.operator_id != operator.pk):
            raise SoftwareOperationBlocked('request_id_conflict')
        return existing, False

    # Lock the endpoint before the second idempotency lookup. Requests for the
    # same endpoint then serialize before the active-job conflict check below.
    endpoint = AgentMachine.objects.select_for_update().get(pk=endpoint_id)
    existing = SoftwarePackageOperation.objects.select_related('job').filter(request_id=normalized_request_id).first()
    if existing:
        if (existing.endpoint_uuid_snapshot != endpoint.pk
                or existing.package_id != uuid.UUID(str(package_id))
                or existing.operation != operation or existing.operator_id != operator.pk):
            raise SoftwareOperationBlocked('request_id_conflict')
        return existing, False

    snapshot = prepare_software_operation_snapshot(
        operator, endpoint_id, package_id, operation, normalized_request_id,
    )
    package = SoftwarePackageRecord.objects.select_for_update().get(pk=package_id)
    operation_record = SoftwarePackageOperation.objects.create(
        request_id=normalized_request_id,
        endpoint=endpoint,
        endpoint_uuid_snapshot=endpoint.pk,
        machine_id_snapshot=endpoint.machine_id,
        package=package,
        operation=operation,
        artifact_sha256=package.sha256,
        artifact_revision=package.artifact_revision,
        profile_revision=package.execution_profile_revision,
        profile_sha256=package.execution_profile_sha256,
        operator=operator,
        authorization='agents.execute_softwarepackageoperation',
        snapshot=snapshot,
    )
    payload = software_operation_payload(operation_record)
    now = timezone.now()
    job = AgentJob.objects.create(
        endpoint=endpoint,
        job_type=AgentJob.TYPE_INSTALL_SOFTWARE,
        payload=payload,
        created_by=operator.get_username(),
        correlation_id=normalized_request_id,
        expires_at=now + timedelta(minutes=15),
    )
    operation_record.job = job
    operation_record.save(update_fields=['job'])
    from .audit import create_audit_event
    event = create_audit_event(
        event_type='software_package.operation_queued',
        title='Operação de software autorizada',
        description='Operação tipada registrada para despacho autenticado do agente.',
        actor_type=AuditEvent.ACTOR_USER,
        actor_name=operator.get_username(),
        endpoint=endpoint,
        metadata={
            'operation_id': str(operation_record.pk), 'job_id': str(job.pk),
            'request_id': normalized_request_id, 'operation': operation,
            'package_uuid': str(package.pk), 'artifact_sha256': package.sha256,
            'artifact_revision': package.artifact_revision,
            'profile_revision': package.execution_profile_revision,
        },
    )
    if event is None:
        raise RuntimeError('software_operation_audit_unavailable')
    return operation_record, True


@transaction.atomic
def validate_software_operation_dispatch(job, *, endpoint=None):
    """Revalidate the immutable operation against current authorization before pull/download."""
    from .internal_jobs import internal_capability, validate_internal_payload

    payload = validate_internal_payload(AgentJob.TYPE_INSTALL_SOFTWARE, job.payload)
    if payload.get('schema_version') != SOFTWARE_JOB_SCHEMA_VERSION:
        raise SoftwareOperationBlocked('software_operation_schema_invalid')
    record = SoftwarePackageOperation.objects.select_for_update().filter(
        pk=payload['operation_id'], job=job,
    ).first()
    if record is None:
        raise SoftwareOperationBlocked('software_operation_snapshot_missing')
    target_ref = endpoint or job.endpoint
    target = AgentMachine.objects.select_for_update().filter(pk=target_ref.pk).first() if target_ref else None
    package = SoftwarePackageRecord.objects.select_for_update().filter(pk=record.package_id).first()
    from django.contrib.auth import get_user_model
    operator = get_user_model().objects.select_for_update().filter(pk=record.operator_id).first()
    if package is None:
        raise SoftwareOperationBlocked('software_package_missing')
    record.package = package
    record.operator = operator
    if (target is None or record.endpoint_id != target.pk
            or record.endpoint_uuid_snapshot != target.pk
            or record.machine_id_snapshot != target.machine_id
            or not target.machine_id
            or AgentMachine.objects.filter(machine_id__iexact=target.machine_id).count() != 1
            or target.has_terminal_lifecycle or not target.is_active):
        raise SoftwareOperationBlocked('endpoint_identity_invalid')
    if target.status != AgentMachine.STATUS_ONLINE or not target.last_seen_at \
            or target.last_seen_at < timezone.now() - timedelta(minutes=15):
        raise SoftwareOperationBlocked('endpoint_offline')
    if job.expires_at and job.expires_at <= timezone.now():
        raise SoftwareOperationBlocked('software_operation_expired')
    if payload != software_operation_payload(record):
        raise SoftwareOperationBlocked('software_operation_payload_mismatch')
    snapshot = record.snapshot if isinstance(record.snapshot, dict) else {}
    if (snapshot.get('schema_version') != SOFTWARE_JOB_SCHEMA_VERSION
            or snapshot.get('request_id') != str(record.request_id)
            or snapshot.get('endpoint') != {'uuid': str(record.endpoint_uuid_snapshot),
                                             'machine_id': record.machine_id_snapshot}
            or snapshot.get('package', {}).get('uuid') != str(record.package_id)
            or snapshot.get('operator', {}).get('id') != str(record.operator_id)
            or snapshot.get('operator', {}).get('authorization') != record.authorization):
        raise SoftwareOperationBlocked('software_operation_snapshot_invalid')

    package = record.package
    if (package.approval_status != SoftwarePackageRecord.STATUS_APPROVED
            or not package.approved_at or not package.approved_by_id
            or package.approved_at.isoformat() != record.snapshot.get('package', {}).get('artifact_approved_at')
            or str(package.approved_by_id) != record.snapshot.get('package', {}).get('artifact_approved_by')
            or package.execution_profile_status != 'approved'
            or not package.execution_profile_approved_at or not package.execution_profile_approved_by_id
            or package.execution_profile_approved_at.isoformat() != record.snapshot.get('package', {}).get('profile_approved_at')
            or str(package.execution_profile_approved_by_id) != record.snapshot.get('package', {}).get('profile_approved_by')
            or not package_profile_binding_matches(package)
            or package.sha256 != record.artifact_sha256
            or package.artifact_revision != record.artifact_revision
            or package.execution_profile_revision != record.profile_revision
            or package.execution_profile_sha256 != record.profile_sha256
            or package.execution_profile != record.snapshot.get('package', {}).get('profile')
            or package.package_type != SoftwarePackageRecord.TYPE_MSI
            or not operation_is_configured(package.execution_profile, record.operation)):
        raise SoftwareOperationBlocked('software_package_authorization_changed')
    if not getattr(settings, 'NIGHTOWL_SOFTWARE_OPERATION_DISPATCH_ENABLED', False):
        raise SoftwareOperationBlocked('software_dispatch_not_enabled')
    if not _artifact_matches(package):
        raise SoftwareOperationBlocked('artifact_integrity_failed')
    if AgentJob.objects.filter(endpoint=target, status__in=ACTIVE_STATES).exclude(pk=job.pk).exists():
        raise SoftwareOperationBlocked('endpoint_job_conflict')
    if not (operator and operator.is_active and is_nightowl_technical_user(operator)
            and (operator.is_superuser or operator.has_perm(record.authorization))):
        raise SoftwareOperationBlocked('software_operation_authorization_revoked')
    if not internal_capability(target, OPERATION_CAPABILITIES[record.operation]):
        raise SoftwareOperationBlocked('agent_capability_unconfirmed')
    return record


@transaction.atomic
def validate_software_operation_download(machine, job_id):
    """Resolve a job artifact only for its authenticated endpoint and exact snapshot."""
    from .internal_jobs import validate_internal_payload

    job = AgentJob.objects.select_for_update().select_related('endpoint').filter(
        pk=job_id, endpoint=machine, job_type=AgentJob.TYPE_INSTALL_SOFTWARE,
        status__in=(AgentJob.STATUS_SENT, AgentJob.STATUS_RUNNING),
    ).first()
    if job is None:
        raise SoftwareOperationBlocked('software_operation_not_found')
    machine = AgentMachine.objects.select_for_update().get(pk=machine.pk)
    record = SoftwarePackageOperation.objects.select_for_update().select_related('package').filter(job=job).first()
    if record is None:
        raise SoftwareOperationBlocked('software_operation_not_found')
    # Lock the current artifact row until the response opens its file handle.
    SoftwarePackageRecord.objects.select_for_update().get(pk=record.package_id)
    handle = None
    try:
        handle = open_private(record.package.artifact_key)
        if handle is None:
            raise SoftwareOperationBlocked('artifact_unavailable')
        record = validate_software_operation_dispatch(job, endpoint=machine)
        if not _artifact_matches(record.package, handle=handle):
            raise SoftwareOperationBlocked('artifact_integrity_failed')
        if not isinstance(job.payload, dict) or validate_internal_payload(job.job_type, job.payload) != job.payload:
            raise SoftwareOperationBlocked('software_operation_payload_invalid')
        return job, record, record.package, handle
    except Exception:
        if handle is not None and not handle.closed:
            handle.close()
        raise


def validate_software_operation_completion(job, result):
    """Accept completed status only with a strict, versioned installed-state proof."""
    if not isinstance(result, dict) or result.get('machine_id') != job.endpoint.machine_id \
            or result.get('output_truncated'):
        raise ValueError('software_operation_identity_invalid')
    if set(result) - {'machine_id', 'output', 'output_truncated', 'error_code'}:
        raise ValueError('software_operation_result_fields_invalid')
    if result.get('error_code') not in (None, '') and not re.fullmatch(
            r'[A-Z][A-Z0-9_]{0,79}', str(result.get('error_code')), re.ASCII):
        raise ValueError('software_operation_error_code_invalid')
    payload = job.payload if isinstance(job.payload, dict) else {}
    output = result.get('output')
    fields = {
        'schema_version', 'operation_id', 'operation', 'request_id', 'package_uuid',
        'artifact_sha256', 'artifact_revision', 'profile_revision', 'profile_sha256',
        'phase', 'verified', 'success', 'exit_code', 'reboot_pending', 'evidence',
    }
    if not isinstance(output, dict) or set(output) != fields:
        raise ValueError('software_operation_confirmation_invalid')
    for key in ('schema_version', 'operation_id', 'operation', 'request_id', 'package_uuid',
                'artifact_sha256', 'artifact_revision', 'profile_revision', 'profile_sha256'):
        if output.get(key) != payload.get(key):
            raise ValueError('software_operation_confirmation_mismatch')
    if (type(output.get('schema_version')) is not int or output['schema_version'] != SOFTWARE_JOB_SCHEMA_VERSION
            or output.get('phase') != 'verified' or output.get('verified') is not True
            or output.get('success') is not True or type(output.get('exit_code')) is not int
            or output['exit_code'] not in (0, 3010) or type(output.get('reboot_pending')) is not bool
            or output['reboot_pending'] != (output['exit_code'] == 3010)):
        raise ValueError('software_operation_confirmation_invalid')
    evidence = output.get('evidence')
    if (not isinstance(evidence, dict) or set(evidence) != {'method', 'product_code', 'version', 'installed'}
            or evidence.get('method') != 'msi_product_code'
            or type(evidence.get('installed')) is not bool
            or not isinstance(evidence.get('product_code'), str)
            or not isinstance(evidence.get('version'), str) or len(evidence['version']) > 120):
        raise ValueError('software_operation_evidence_invalid')
    try:
        operation_record = job.software_operation
    except SoftwarePackageOperation.DoesNotExist:
        raise ValueError('software_operation_snapshot_missing') from None
    if (payload != software_operation_payload(operation_record)
            or operation_record.endpoint_uuid_snapshot != job.endpoint_id
            or operation_record.machine_id_snapshot != job.endpoint.machine_id):
        raise ValueError('software_operation_snapshot_mismatch')
    profile = operation_record.snapshot.get('package', {}).get('profile', {})
    if profile.get('detection', {}).get('method') != 'msi_product_code':
        raise ValueError('software_operation_evidence_invalid')
    expected_product_code = profile['detection'].get('product_code')
    if evidence.get('product_code', '').casefold() != str(expected_product_code or '').casefold():
        raise ValueError('software_operation_evidence_mismatch')
    if operation_record.operation == 'uninstall':
        valid_state = evidence['installed'] is False and evidence['version'] == ''
    else:
        expected_version = profile.get('expected_version')
        valid_state = evidence['installed'] is True and evidence['version'] == expected_version
    if not valid_state:
        raise ValueError('software_operation_evidence_mismatch')
