import hashlib
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_ipv46_address
from django.db import transaction
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from .audit import create_audit_event
from .models import AuditEvent
from .models import AgentMachine
from .models import AgentOperationalStatus
from .models import AgentRelease
from .models import AgentReleaseSigningKey
from .models import InventorySnapshot
from .versioning import compare_versions, normalize_agent_version, parse_semver, sort_releases_by_version


AGENT_DIAGNOSTIC_STAGES = {
    'received',
    'checking_version',
    'downloading',
    'downloaded',
    'validating',
    'validated',
    'staging',
    'staged',
    'stopping_service',
    'service_stopped',
    'creating_backup',
    'backup_created',
    'quiescing',
    'replacing_files',
    'files_replaced',
    'starting_service',
    'service_started',
    'waiting_health_check',
    'completed',
    'failed',
    'rollback_required',
    'rollback_starting',
    'rollback_stopping_service',
    'rollback_restoring_files',
    'rollback_starting_service',
    'rollback_waiting_health_check',
    'rolled_back',
    'rollback_failed',
}

AGENT_DIAGNOSTIC_STATUSES = {'idle', 'running', 'completed', 'failed', 'rolled_back', 'rollback_failed', 'pending'}

AGENT_DIAGNOSTIC_ERROR_CODES = {
    '',
    'UPDATE_ALREADY_RUNNING',
    'UPDATE_STATE_INVALID',
    'UPDATE_DOWNLOAD_FAILED',
    'UPDATE_HASH_MISMATCH',
    'UPDATE_PACKAGE_INVALID',
    'UPDATE_SERVICE_STOP_TIMEOUT',
    'UPDATE_BACKUP_FAILED',
    'UPDATE_FILE_REPLACE_FAILED',
    'UPDATE_FILE_LOCK_TIMEOUT',
    'UPDATE_FILE_ACCESS_DENIED',
    'UPDATE_SERVICE_START_FAILED',
    'UPDATE_INTERRUPTED',
    'UPDATE_UNEXPECTED_ERROR',
    'UPDATE_HEALTHCHECK_TIMEOUT',
    'UPDATE_PROCESS_EXITED_EARLY',
    'UPDATE_HEALTHCHECK_VERSION_MISMATCH',
    'ROLLBACK_BACKUP_INVALID',
    'ROLLBACK_SERVICE_STOP_FAILED',
    'ROLLBACK_FILE_RESTORE_FAILED',
    'ROLLBACK_SERVICE_START_FAILED',
    'ROLLBACK_HEALTHCHECK_TIMEOUT',
    'ROLLBACK_VERSION_MISMATCH',
    'ROLLBACK_FAILED',
    'JOB_ID_INVALID',
    'JOB_DUPLICATE',
    'JOB_EXPIRED',
    'JOB_NOT_READY',
    'JOB_UNSUPPORTED',
    'JOB_INVALID_PARAMETERS',
    'JOB_TIMEOUT',
    'JOB_CANCELLED',
    'JOB_EXECUTION_FAILED',
    'JOB_RESULT_TOO_LARGE',
    'JOB_STATE_INVALID',
    'JOB_CONCURRENCY_LIMIT',
    'JOB_EXCLUSIVE_CONFLICT',
    'JOB_INTERRUPTED',
    'RESULT_SEND_FAILED',
    'RESULT_QUEUE_CORRUPTED',
    'RESULT_QUEUE_FULL',
    'RESULT_PAYLOAD_INVALID',
    'RESULT_RETRY_EXHAUSTED',
    'TRUST_METADATA_INVALID',
    'TRUST_BUNDLE_INVALID',
    'TRUST_BUNDLE_EXPIRED',
    'TRUST_BUNDLE_DOWNGRADE',
    'TRUST_BUNDLE_SAME_VERSION_DIVERGENT',
    'TRUST_SIGNATURE_INVALID',
    'TRUST_ROOT_UNKNOWN',
    'TRUST_ROOT_REVOKED',
    'TRUST_PRIVATE_PARAMETERS',
    'TRUST_KEY_DUPLICATE',
    'TRUST_KEY_REVOCATION_REGRESSION',
    'TRUST_INSTALL_FAILED',
    'TRUST_DOWNLOAD_FAILED',
}


UPDATE_POLICY_REASON_ELIGIBLE = 'eligible'
UPDATE_POLICY_REASON_ALREADY_CURRENT = 'already_current'
UPDATE_POLICY_REASON_NO_RELEASE = 'no_release'
UPDATE_POLICY_REASON_CHANNEL_NO_RELEASE = 'channel_no_release'
UPDATE_POLICY_REASON_RELEASE_NOT_AVAILABLE = 'release_not_available'
UPDATE_POLICY_REASON_RELEASE_REVOKED = 'release_revoked'
UPDATE_POLICY_REASON_RELEASE_PAUSED = 'release_paused'
UPDATE_POLICY_REASON_ENDPOINT_PAUSED = 'endpoint_paused'
UPDATE_POLICY_REASON_MANUAL_POLICY = 'manual_policy'
UPDATE_POLICY_REASON_NOTIFY_ONLY = 'notify_only'
UPDATE_POLICY_REASON_OUTSIDE_MAINTENANCE_WINDOW = 'outside_maintenance_window'
UPDATE_POLICY_REASON_MINIMUM_UPDATER_INCOMPATIBLE = 'minimum_updater_incompatible'
UPDATE_POLICY_REASON_GROUP_NOT_ALLOWED = 'group_not_allowed'
UPDATE_POLICY_REASON_ROLLOUT_NOT_SELECTED = 'rollout_not_selected'
UPDATE_POLICY_REASON_PINNED_RELEASE_NOT_FOUND = 'pinned_release_not_found'
UPDATE_POLICY_REASON_PINNED_RELEASE_UNAVAILABLE = 'pinned_release_unavailable'
UPDATE_POLICY_REASON_INVALID_VERSION = 'invalid_version'
UPDATE_POLICY_REASON_DOWNGRADE_REQUIRES_FORCE = 'downgrade_requires_force'
UPDATE_POLICY_REASON_SIGNATURE_INVALID = 'signature_invalid'
UPDATE_POLICY_REASON_KEY_UNKNOWN = 'key_unknown'
UPDATE_POLICY_REASON_KEY_REVOKED = 'key_revoked'
UPDATE_POLICY_REASON_UPDATER_BOOTSTRAP_REQUIRED = 'updater_bootstrap_required'
AGENT_RELEASE_AVAILABLE_STATUSES = {AgentRelease.STATUS_PUBLISHED, AgentRelease.STATUS_AVAILABLE, 'active'}
AGENT_RELEASE_AUTOMATIC_STATUSES = {AgentRelease.STATUS_PUBLISHED, AgentRelease.STATUS_AVAILABLE, 'active'}
UPDATE_AGENT_EXPLICIT_RELEASE_MIN_VERSION = '0.1.1.0-rc6'


@dataclass(frozen=True)
class AgentUpdateDecision:
    eligible: bool
    reason_code: str
    endpoint: AgentMachine
    release: AgentRelease | None = None
    current_version: str = ''
    target_version: str = ''
    selected_release_id: str = ''
    channel: str = AgentMachine.UPDATE_CHANNEL_STABLE
    rollout_bucket: int | None = None

    def as_agent_payload(self) -> dict:
        release = self.release
        return {
            'update_available': bool(self.eligible and release),
            'current_version': self.current_version or '',
            'target_version': self.target_version or '',
            'channel': self.channel or AgentMachine.UPDATE_CHANNEL_STABLE,
            'package_url': release.package_url if release and self.eligible else '',
            'checksum_url': release.checksum_url if release and self.eligible else '',
            'sha256': release.sha256 if release and self.eligible else '',
            'size': release.size if release and self.eligible else 0,
            'minimum_updater_version': release.minimum_updater_version if release and self.eligible else '',
            'mandatory': bool(release.mandatory) if release and self.eligible else False,
            'reason_code': self.reason_code,
            'release_id': self.selected_release_id if self.eligible else '',
        }

    def as_panel_payload(self) -> dict:
        release = self.release
        return {
            'eligible': self.eligible,
            'reason_code': self.reason_code,
            'current_version': self.current_version or '',
            'target_version': self.target_version or '',
            'channel': self.channel or AgentMachine.UPDATE_CHANNEL_STABLE,
            'selected_release_id': self.selected_release_id,
            'rollout_bucket': self.rollout_bucket,
            'rollout_percentage': release.rollout_percentage if release else None,
            'rollout_paused': release.rollout_paused if release else False,
            'mandatory': release.mandatory if release else False,
            'pinned_version': self.endpoint.pinned_agent_version or '',
            'update_paused': self.endpoint.update_paused,
            'update_policy': self.endpoint.update_policy or AgentMachine.UPDATE_POLICY_MANUAL,
            'auto_update_enabled': self.endpoint.auto_update_enabled,
            'maintenance_window_start': self.endpoint.maintenance_window_start.isoformat() if self.endpoint.maintenance_window_start else '',
            'maintenance_window_end': self.endpoint.maintenance_window_end.isoformat() if self.endpoint.maintenance_window_end else '',
            'package_url': release.package_url if release and self.eligible else '',
            'checksum_url': release.checksum_url if release and self.eligible else '',
            'sha256': release.sha256 if release and self.eligible else '',
            'minimum_updater_version': release.minimum_updater_version if release else '',
        }


def update_agent_requires_bootstrap(endpoint, release=None) -> bool:
    if release is not None:
        release_comparison = compare_versions(release.version or '', UPDATE_AGENT_EXPLICIT_RELEASE_MIN_VERSION)
        if release_comparison is None or release_comparison < 0:
            return False
    updater_comparison = compare_versions(_updater_version(endpoint), UPDATE_AGENT_EXPLICIT_RELEASE_MIN_VERSION)
    return updater_comparison is None or updater_comparison < 0


def update_agent_uses_legacy_bootstrap_payload(endpoint, *, manual_explicit=False, channel='') -> bool:
    # Kept as a compatibility shim for older callers/tests. The backend no longer
    # creates update_agent jobs that rely on pre-RC6 updater behavior.
    if not manual_explicit:
        return False
    return False


def build_update_agent_job_payload(endpoint, decision: AgentUpdateDecision, *, force=False, source='manual_panel', manual_explicit=False) -> dict:
    release = decision.release
    if release is None:
        raise ValueError('AgentUpdateDecision sem release nao pode gerar payload update_agent.')

    payload = {
        'release_id': str(release.id),
        'target_version': release.version,
        'channel': decision.channel,
        'package_url': release.package_url,
        'checksum_url': release.checksum_url,
        'sha256': release.sha256,
        'size': release.size,
        'minimum_updater_version': release.minimum_updater_version,
        'force': bool(force),
        'mandatory': bool(release.mandatory),
        'timeout_seconds': 900,
        'source': source,
    }

    payload.update({
        'source_channel': decision.channel,
        'policy_reason': decision.reason_code,
        'manifest_url': release.manifest_url,
        'manifest_sha256': release.manifest_sha256,
        'signature_url': release.signature_url,
        'signature_sha256': release.signature_sha256,
        'signature_key_id': release.signature_key_id,
        'signature_valid': release.signature_valid,
        'legacy_unsigned': release.legacy_unsigned,
    })
    return payload


def find_repair_agent_release(endpoint):
    version = (endpoint.agent_version or '').strip()
    if not version:
        return None
    candidates = AgentRelease.objects.filter(
        version=version,
        revoked=False,
        status__in=set(AGENT_RELEASE_AVAILABLE_STATUSES) | {AgentRelease.STATUS_PAUSED, AgentRelease.STATUS_SUPERSEDED},
    )
    endpoint_channel = (endpoint.update_channel or '').strip()
    if endpoint_channel:
        channel_match = candidates.filter(channel=endpoint_channel).first()
        if channel_match:
            return channel_match
    return candidates.order_by('-released_at', '-created_at').first()


def build_repair_agent_job_payload(endpoint, release, *, source='manual_panel') -> dict:
    if release is None:
        raise ValueError('Release instalada nao encontrada para repair_agent.')
    if release.revoked or release.status == AgentRelease.STATUS_REVOKED:
        raise ValidationError('REPAIR_RELEASE_REVOKED: release instalada revogada.')
    if release.status not in set(AGENT_RELEASE_AVAILABLE_STATUSES) | {AgentRelease.STATUS_PAUSED, AgentRelease.STATUS_SUPERSEDED}:
        raise ValidationError('REPAIR_RELEASE_NOT_AVAILABLE: release instalada nao esta disponivel para repair.')
    if (endpoint.agent_version or '').strip() != release.version:
        raise ValidationError('REPAIR_RELEASE_VERSION_MISMATCH: release nao corresponde a versao instalada.')
    if not release.package_url or not release.sha256 or not release.size:
        raise ValidationError('REPAIR_RELEASE_METADATA_INCOMPLETE: release sem pacote/hash/tamanho.')
    if not _release_domain_allowed(release.package_url):
        raise ValidationError('REPAIR_RELEASE_DOMAIN_BLOCKED: dominio de pacote nao permitido.')
    ensure_release_signature_policy(release)
    return {
        'operation': 'repair',
        'release_id': str(release.id),
        'target_version': release.version,
        'current_version': endpoint.agent_version or '',
        'channel': release.channel,
        'package_url': release.package_url,
        'checksum_url': release.checksum_url,
        'sha256': release.sha256,
        'size': release.size,
        'manifest_url': release.manifest_url,
        'manifest_sha256': release.manifest_sha256,
        'signature_url': release.signature_url,
        'signature_sha256': release.signature_sha256,
        'signature_key_id': release.signature_key_id,
        'signature_valid': release.signature_valid,
        'legacy_unsigned': release.legacy_unsigned,
        'minimum_updater_version': release.minimum_updater_version,
        'force': False,
        'mandatory': False,
        'timeout_seconds': 900,
        'source': source,
        'identity_preservation_required': True,
        'enrollment_allowed': False,
    }


def build_fqdn(hostname: str, domain: str) -> str:
    if hostname and domain:
        return f'{hostname}.{domain}'
    return hostname


def first_ip(ips: list[str]) -> str | None:
    return ips[0] if ips else None


def _heartbeat_ips(payload: dict) -> list[str]:
    ips = payload.get('ips') or []
    if ips:
        return ips
    ip_address = payload.get('ip_address')
    return [ip_address] if ip_address else []


def _heartbeat_os(payload: dict) -> dict:
    os_data = payload.get('os') or {}
    if os_data:
        return os_data
    return {
        'name': payload.get('os_name') or '',
        'version': payload.get('os_version') or '',
        'build': payload.get('windows_build') or '',
    }


def _heartbeat_agent(payload: dict) -> dict:
    agent_data = payload.get('agent') or {}
    if not any((
        payload.get('agent_version'),
        payload.get('tray_version'),
        payload.get('updater_version'),
        payload.get('agent_mode'),
        payload.get('install_mode'),
    )):
        return agent_data
    merged = dict(agent_data)
    merged.setdefault('version', payload.get('agent_version') or '')
    merged.setdefault('tray_version', payload.get('tray_version') or '')
    merged.setdefault('updater_version', payload.get('updater_version') or '')
    merged.setdefault('mode', payload.get('agent_mode') or '')
    merged.setdefault('install_mode', payload.get('install_mode') or '')
    return merged


def _parse_agent_datetime(value):
    if value is None:
        return timezone.now()
    if hasattr(value, 'tzinfo'):
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value
    parsed = parse_datetime(str(value))
    if parsed is None:
        return timezone.now()
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _normalize_disk_rows(disks):
    rows = []
    for disk in disks or []:
        if not isinstance(disk, dict):
            continue
        size = disk.get('size_bytes') or disk.get('total_bytes') or 0
        free = disk.get('free_bytes') or 0
        used = disk.get('used_bytes')
        if used is None:
            try:
                used = int(size or 0) - int(free or 0)
            except (TypeError, ValueError):
                used = 0
        rows.append({
            'name': disk.get('name') or disk.get('letter') or disk.get('device_id') or '-',
            'letter': disk.get('letter') or disk.get('name') or '',
            'label': disk.get('label') or disk.get('volume_name') or '',
            'size_bytes': size,
            'total_bytes': disk.get('total_bytes') or size,
            'free_bytes': free,
            'used_bytes': used,
            'filesystem': disk.get('filesystem') or '',
            'drive_type': disk.get('drive_type'),
            'volume_name': disk.get('volume_name') or '',
            'used_percent': disk.get('used_percent'),
            'is_system_drive': disk.get('is_system_drive'),
            'bitlocker_status': disk.get('bitlocker_status') or '',
            'health_status': disk.get('health_status') or '',
            'collected_at': disk.get('collected_at'),
        })
    return rows


def _defender_status_from_security(security_payload):
    security = security_payload or {}
    defender = security.get('defender') or {}
    if not defender:
        return {}
    enabled = (
        defender.get('antivirus_enabled')
        if defender.get('antivirus_enabled') is not None
        else defender.get('defender_enabled')
    )
    realtime = (
        defender.get('real_time_protection_enabled')
        if defender.get('real_time_protection_enabled') is not None
        else defender.get('realtime_protection_enabled')
    )
    return {
        'enabled': enabled,
        'real_time_protection_enabled': realtime,
        'engine_version': defender.get('engine_version') or '',
        'product_version': defender.get('product_version') or '',
        'signatures_age_days': defender.get('signatures_age_days'),
        'raw': defender,
    }


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _as_list(value):
    return value if isinstance(value, list) else []


def _clip(value, limit=500):
    text = str(value or '')
    return text[:limit]


def _safe_int(value, default=0):
    try:
        if value is None or value == '':
            return default
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _safe_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'sim', 'on'}
    return bool(value)


def _safe_datetime(value):
    if value in (None, ''):
        return None
    if hasattr(value, 'tzinfo'):
        return timezone.make_aware(value, timezone.get_current_timezone()) if timezone.is_naive(value) else value
    parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    return timezone.make_aware(parsed, timezone.get_current_timezone()) if timezone.is_naive(parsed) else parsed


def _safe_version(value):
    return normalize_agent_version(value)


def _release_domain_allowed(url):
    allowed_hosts = getattr(settings, 'NIGHTOWL_AGENT_PACKAGE_ALLOWED_HOSTS', None)
    if allowed_hosts is None:
        allowed_hosts = ['nightowl.controlsul.com.br', 'rmm.controlsul.com', 'nightowl.controlsul.com']
    if not allowed_hosts:
        return True
    try:
        parsed = urlparse(str(url or ''))
    except ValueError:
        return False
    return parsed.scheme == 'https' and parsed.hostname in set(allowed_hosts)


def _actor_name(actor):
    if actor is None:
        return 'system'
    if hasattr(actor, 'get_username'):
        return actor.get_username() or 'system'
    return str(actor) or 'system'


def _release_audit(release, action, *, actor=None, reason='', channel_before='', rollout_before=None, metadata=None):
    from .models import AgentReleaseAudit

    AgentReleaseAudit.objects.create(
        user=actor if getattr(actor, 'is_authenticated', False) else None,
        action=action,
        release=release,
        version=release.version,
        channel_before=channel_before,
        channel_after=release.channel,
        rollout_before=rollout_before,
        rollout_after=release.rollout_percentage,
        reason=reason,
        metadata=metadata or {},
    )


def _release_audit_event_type(action):
    return {
        'created': 'release.created',
        'updated': 'release.updated',
        'published': 'release.published',
        'promoted': 'release.promoted',
        'paused': 'release.paused',
        'resumed': 'release.resumed',
        'revoked': 'release.revoked',
        'superseded': 'release.superseded',
        'rollout_changed': 'release.rollout_changed',
        'immutability_blocked': 'release.immutability_blocked',
        'signature_failed': 'release.signature_validation_failed',
    }.get(action, f'release.{action}')


def audit_release_event(release, action, *, actor=None, reason='', channel_before='', rollout_before=None, endpoint=None, job=None, metadata=None):
    _release_audit(
        release,
        action,
        actor=actor,
        reason=reason,
        channel_before=channel_before,
        rollout_before=rollout_before,
        metadata=metadata,
    )
    create_audit_event(
        event_type=_release_audit_event_type(action),
        title=f'Release {release.version}: {action}',
        description=reason or f'Acao {action} registrada para release {release.version}.',
        severity=AuditEvent.SEVERITY_WARNING if action in {'revoked', 'superseded', 'signature_failed', 'immutability_blocked'} else AuditEvent.SEVERITY_INFO,
        actor_type=AuditEvent.ACTOR_USER if actor is not None else AuditEvent.ACTOR_SYSTEM,
        actor_name=_actor_name(actor),
        endpoint=endpoint,
        metadata={
            'release_id': str(release.id),
            'version': release.version,
            'channel_before': channel_before,
            'channel_after': release.channel,
            'rollout_before': rollout_before,
            'rollout_after': release.rollout_percentage,
            'reason': reason,
            'job_id': str(job.id) if job is not None else '',
            **(metadata or {}),
        },
    )


def assert_release_immutable_compatible(existing, metadata):
    critical = {
        'package_url': metadata.get('package_url') or metadata.get('packageUrl') or '',
        'checksum_url': metadata.get('checksum_url') or metadata.get('checksumUrl') or '',
        'sha256': (metadata.get('sha256') or '').lower(),
        'size': int(metadata.get('size') or 0),
        'manifest_url': metadata.get('manifest_url') or metadata.get('manifestUrl') or '',
        'manifest_sha256': (metadata.get('manifest_sha256') or metadata.get('manifestSha256') or '').lower(),
        'signature_url': metadata.get('signature_url') or metadata.get('signatureUrl') or '',
        'signature_sha256': (metadata.get('signature_sha256') or metadata.get('signatureSha256') or '').lower(),
        'signature_key_id': metadata.get('signature_key_id') or metadata.get('signatureKeyId') or '',
        'minimum_updater_version': metadata.get('minimum_updater_version') or metadata.get('minimumUpdaterVersion') or '',
    }
    differences = {
        field: {'existing': getattr(existing, field), 'incoming': value}
        for field, value in critical.items()
        if value not in ('', 0) and getattr(existing, field) != value
    }
    if differences and existing.status in AgentRelease.IMMUTABLE_STATUSES:
        _release_audit(
            existing,
            'immutability_blocked',
            reason='RELEASE_IMMUTABILITY_VIOLATION',
            metadata={'differences': differences},
        )
        raise ValidationError(f'RELEASE_IMMUTABILITY_VIOLATION: release {existing.version} ja publicada com metadados diferentes.')


def _release_signing_key(release):
    key_id = (release.signature_key_id or '').strip()
    if not key_id:
        return None
    return AgentReleaseSigningKey.objects.filter(key_id=key_id).first()


def ensure_release_signature_policy(release):
    if release.legacy_unsigned:
        if release.channel == AgentRelease.CHANNEL_STABLE:
            raise ValidationError('Release stable nao pode ser legacy_unsigned.')
        return
    if not release.signature_valid:
        raise ValidationError('Release assinada precisa ter assinatura valida registrada.')
    for field in ('manifest_url', 'manifest_sha256', 'signature_url', 'signature_sha256', 'signature_key_id'):
        if not getattr(release, field):
            raise ValidationError(f'Release assinada sem {field}.')
    key = _release_signing_key(release)
    if key is None:
        raise ValidationError(f'RELEASE_KEY_UNKNOWN: key_id {release.signature_key_id} nao cadastrado.')
    if key.revoked:
        raise ValidationError(f'RELEASE_KEY_REVOKED: key_id {release.signature_key_id} revogado.')
    now = timezone.now()
    if key.valid_from and key.valid_from > now:
        raise ValidationError(f'RELEASE_KEY_NOT_YET_VALID: key_id {release.signature_key_id}.')
    if key.valid_until and key.valid_until < now:
        raise ValidationError(f'RELEASE_KEY_EXPIRED: key_id {release.signature_key_id}.')


@transaction.atomic
def publish_agent_release(release, actor, reason='', *, rollout_percentage=None, rollout_paused=True):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Publicacao exige motivo.')
    if release.status in {AgentRelease.STATUS_PUBLISHED, AgentRelease.STATUS_PAUSED}:
        return release
    if release.status in {AgentRelease.STATUS_REVOKED, AgentRelease.STATUS_SUPERSEDED} or release.revoked:
        raise ValidationError('Release revogada ou superseded nao pode ser publicada.')
    if not getattr(actor, 'has_perm', lambda perm: False)('agents.publish_agentrelease'):
        raise ValidationError('Permissao agents.publish_agentrelease obrigatoria.')
    ensure_release_signature_policy(release)
    channel_before = release.channel
    rollout_before = release.rollout_percentage
    if rollout_percentage is not None:
        release.rollout_percentage = min(100, max(0, int(rollout_percentage or 0)))
    release.rollout_paused = bool(rollout_paused)
    release.status = AgentRelease.STATUS_PAUSED if release.rollout_paused else AgentRelease.STATUS_PUBLISHED
    release.released_at = release.released_at or timezone.now()
    release.published_by = actor if getattr(actor, 'is_authenticated', False) else None
    release.save()
    audit_release_event(
        release,
        'published',
        actor=actor,
        reason=reason,
        channel_before=channel_before,
        rollout_before=rollout_before,
        metadata={'signature_key_id': release.signature_key_id, 'sha256': release.sha256},
    )
    return release


@transaction.atomic
def promote_agent_release(
    release,
    target_channel,
    actor,
    rollout_percentage=0,
    rollout_paused=True,
    approval_reason='',
    allow_direct_stable=False,
    allow_prerelease_stable=False,
):
    target_channel = (target_channel or '').strip()
    approval_reason = (approval_reason or '').strip()
    if target_channel not in {choice[0] for choice in AgentRelease.CHANNEL_CHOICES}:
        raise ValidationError('Canal alvo invalido.')
    channel_before = release.channel
    rollout_before = release.rollout_percentage
    allowed = {
        AgentRelease.CHANNEL_DEVELOPMENT: {AgentRelease.CHANNEL_PILOT},
        AgentRelease.CHANNEL_PILOT: {AgentRelease.CHANNEL_STABLE},
        AgentRelease.CHANNEL_STABLE: set(),
    }
    if target_channel == channel_before:
        return release
    if target_channel not in allowed.get(channel_before, set()):
        if not (allow_direct_stable and channel_before == AgentRelease.CHANNEL_DEVELOPMENT and target_channel == AgentRelease.CHANNEL_STABLE):
            raise ValidationError('Transicao de canal nao permitida.')
    if release.revoked or release.status == AgentRelease.STATUS_REVOKED:
        raise ValidationError('Release revogada nao pode ser promovida.')
    if release.status == AgentRelease.STATUS_SUPERSEDED:
        raise ValidationError('Release superseded nao pode ser promovida.')
    if release.status == AgentRelease.STATUS_DRAFT:
        raise ValidationError('Release draft precisa ser publicada antes da promocao.')
    if not approval_reason:
        raise ValidationError('Promocao exige motivo/aprovacao.')
    if release.legacy_unsigned:
        raise ValidationError('Release legacy_unsigned nao pode ser promovida.')
    ensure_release_signature_policy(release)
    if target_channel == AgentRelease.CHANNEL_STABLE:
        if not getattr(actor, 'has_perm', lambda perm: False)('agents.promote_agentrelease_stable'):
            raise ValidationError('Permissao agents.promote_agentrelease_stable obrigatoria.')
        parsed_version = parse_semver(release.version)
        if parsed_version and parsed_version[1] and not allow_prerelease_stable:
            raise ValidationError('Promocao de prerelease para stable exige confirmacao administrativa explicita.')
        release.stable_approval_reason = approval_reason
    elif not getattr(actor, 'has_perm', lambda perm: False)('agents.promote_agentrelease'):
        raise ValidationError('Permissao agents.promote_agentrelease obrigatoria.')

    release.channel = target_channel
    release.status = AgentRelease.STATUS_PAUSED if rollout_paused else AgentRelease.STATUS_PUBLISHED
    release.rollout_paused = bool(rollout_paused)
    release.rollout_percentage = min(100, max(0, int(rollout_percentage or 0)))
    release.save()
    audit_release_event(
        release,
        'promoted',
        actor=actor,
        reason=approval_reason,
        channel_before=channel_before,
        rollout_before=rollout_before,
        metadata={'target_channel': target_channel, 'actor': _actor_name(actor), 'sha256': release.sha256},
    )
    return release


@transaction.atomic
def revoke_agent_release(release, actor, reason, replacement_release=None):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Revogacao exige motivo.')
    if not getattr(actor, 'has_perm', lambda perm: False)('agents.revoke_agentrelease'):
        raise ValidationError('Permissao agents.revoke_agentrelease obrigatoria.')
    if release.revoked or release.status == AgentRelease.STATUS_REVOKED:
        return release
    channel_before = release.channel
    rollout_before = release.rollout_percentage
    release.revoked = True
    release.revoked_at = timezone.now()
    release.revoked_by = actor if getattr(actor, 'is_authenticated', False) else None
    release.revocation_reason = reason
    release.replacement_release = replacement_release
    release.status = AgentRelease.STATUS_REVOKED
    release.rollout_paused = True
    release.save()
    from .models import AgentJob
    AgentJob.objects.filter(
        agent_release=release,
        status__in=[AgentJob.STATUS_QUEUED, AgentJob.STATUS_SENT],
    ).update(
        status=AgentJob.STATUS_CANCELLED,
        finished_at=timezone.now(),
        error_code='RELEASE_REVOKED',
        error_message='Release revogada antes da execucao.',
    )
    audit_release_event(
        release,
        'revoked',
        actor=actor,
        reason=reason,
        channel_before=channel_before,
        rollout_before=rollout_before,
        metadata={'replacement_release': str(replacement_release.id) if replacement_release else ''},
    )
    return release


@transaction.atomic
def supersede_agent_release(release, replacement_release, actor, reason=''):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Supersedencia exige motivo.')
    if replacement_release is None:
        raise ValidationError('Release substituta obrigatoria.')
    if release.pk == replacement_release.pk:
        raise ValidationError('Release nao pode substituir a si mesma.')
    if release.revoked or release.status == AgentRelease.STATUS_REVOKED:
        raise ValidationError('Release revogada nao pode ser supersedida.')
    if release.status == AgentRelease.STATUS_SUPERSEDED:
        if release.replacement_release_id == replacement_release.id:
            return release
        raise ValidationError('Release ja supersedida por outra substituta.')
    if replacement_release.revoked or replacement_release.status == AgentRelease.STATUS_REVOKED:
        raise ValidationError('Release substituta revogada nao pode ser usada.')
    channel_before = release.channel
    rollout_before = release.rollout_percentage
    release.status = AgentRelease.STATUS_SUPERSEDED
    release.rollout_paused = True
    release.superseded_at = timezone.now()
    release.superseded_by = actor if getattr(actor, 'is_authenticated', False) else None
    release.superseded_reason = reason
    release.replacement_release = replacement_release
    release.save()
    audit_release_event(
        release,
        'superseded',
        actor=actor,
        reason=reason,
        channel_before=channel_before,
        rollout_before=rollout_before,
        metadata={'replacement_release': str(replacement_release.id), 'replacement_version': replacement_release.version},
    )
    return release


@transaction.atomic
def change_agent_release_rollout(release, actor, rollout_percentage, paused=None, reason=''):
    reason = (reason or '').strip()
    if release.revoked or release.status == AgentRelease.STATUS_REVOKED:
        raise ValidationError('Release revogada nao pode ter rollout alterado.')
    if release.status == AgentRelease.STATUS_SUPERSEDED:
        raise ValidationError('Release superseded nao pode ter rollout alterado.')
    rollout_before = release.rollout_percentage
    channel_before = release.channel
    previous_paused = release.rollout_paused
    release.rollout_percentage = min(100, max(0, int(rollout_percentage or 0)))
    if paused is not None:
        release.rollout_paused = bool(paused)
        if release.rollout_paused and release.status == AgentRelease.STATUS_PUBLISHED:
            release.status = AgentRelease.STATUS_PAUSED
        elif not release.rollout_paused and release.status == AgentRelease.STATUS_PAUSED:
            release.status = AgentRelease.STATUS_PUBLISHED
    release.save()
    action = 'rollout_changed'
    if paused is not None and bool(paused) != previous_paused:
        action = 'paused' if paused else 'resumed'
    audit_release_event(
        release,
        action,
        actor=actor,
        reason=reason,
        channel_before=channel_before,
        rollout_before=rollout_before,
    )
    return release


def deterministic_rollout_bucket(endpoint, release):
    stable_identity = endpoint.machine_id or str(endpoint.id)
    release_key = str(release.id or release.version)
    digest = hashlib.sha256(f'{stable_identity}:{release_key}'.encode('utf-8')).hexdigest()
    return int(digest[:8], 16) % 100


def _is_now_inside_window(start, end, now):
    if not start or not end:
        return False
    local_time = timezone.localtime(now).time()
    if start <= end:
        return start <= local_time <= end
    return local_time >= start or local_time <= end


def _release_query_for_channel(channel):
    return AgentRelease.objects.filter(
        channel=channel,
        status__in=AGENT_RELEASE_AVAILABLE_STATUSES,
        revoked=False,
    ).order_by('-released_at', '-created_at')


def _latest_available_release_for_endpoint(endpoint, channel):
    pinned = (endpoint.pinned_agent_version or '').strip()
    if pinned:
        return _release_query_for_channel(channel).filter(version=pinned).first()
    releases = list(_release_query_for_channel(channel))
    ordered = sort_releases_by_version(releases, reverse=True)
    return ordered[0] if ordered else None


def _endpoint_group_ids(endpoint):
    if not getattr(endpoint, 'pk', None):
        return set()
    return set(endpoint.rollout_groups.values_list('id', flat=True))


def _release_group_allowed(endpoint, release):
    release_groups = set(release.allowed_groups.values_list('id', flat=True))
    if not release_groups:
        return True
    return bool(release_groups & _endpoint_group_ids(endpoint))


def _updater_version(endpoint):
    try:
        diagnostic = endpoint.operational_status
    except AgentOperationalStatus.DoesNotExist:
        return endpoint.agent_version or ''
    return getattr(diagnostic, 'updater_version', '') or endpoint.agent_version or ''


def evaluate_agent_update_policy(endpoint, *, now=None, manual=False, for_agent=False, explicit_release=None, record_evaluation=True, allow_downgrade=False):
    now = now or timezone.now()
    channel = endpoint.update_channel or AgentMachine.UPDATE_CHANNEL_STABLE
    current_version = endpoint.agent_version or ''
    release = explicit_release or _latest_available_release_for_endpoint(endpoint, channel)
    if record_evaluation:
        endpoint.last_update_policy_evaluation_at = now
        endpoint.save(update_fields=['last_update_policy_evaluation_at', 'updated_at'])

    def decision(eligible, reason_code, selected_release=release, bucket=None):
        return AgentUpdateDecision(
            eligible=eligible,
            reason_code=reason_code,
            endpoint=endpoint,
            release=selected_release,
            current_version=current_version,
            target_version=(selected_release.version if selected_release else ''),
            selected_release_id=str(selected_release.id) if selected_release else '',
            channel=(selected_release.channel if explicit_release and selected_release else channel),
            rollout_bucket=bucket,
        )

    if endpoint.update_paused:
        return decision(False, UPDATE_POLICY_REASON_ENDPOINT_PAUSED, release)

    if endpoint.pinned_agent_version and release is None:
        return decision(False, UPDATE_POLICY_REASON_PINNED_RELEASE_NOT_FOUND, None)

    if release is None:
        return decision(False, UPDATE_POLICY_REASON_CHANNEL_NO_RELEASE, None)

    if explicit_release is not None and explicit_release.channel != channel:
        return decision(False, UPDATE_POLICY_REASON_CHANNEL_NO_RELEASE, release)

    if release.channel == AgentRelease.CHANNEL_PILOT and not getattr(endpoint, 'is_pilot_endpoint', False):
        return decision(False, UPDATE_POLICY_REASON_GROUP_NOT_ALLOWED, release)

    if release.revoked or release.status == AgentRelease.STATUS_REVOKED:
        return decision(False, UPDATE_POLICY_REASON_RELEASE_REVOKED, release)
    manual_explicit_release = manual and explicit_release is not None
    if release.status not in AGENT_RELEASE_AVAILABLE_STATUSES and not (
        manual_explicit_release and release.status in {AgentRelease.STATUS_PAUSED, AgentRelease.STATUS_SUPERSEDED}
    ):
        return decision(False, UPDATE_POLICY_REASON_RELEASE_NOT_AVAILABLE, release)
    if not release.package_url or not release.sha256:
        return decision(False, UPDATE_POLICY_REASON_RELEASE_NOT_AVAILABLE, release)
    if (release.rollout_paused or release.status == AgentRelease.STATUS_PAUSED) and not manual_explicit_release:
        return decision(False, UPDATE_POLICY_REASON_RELEASE_PAUSED, release)
    if not _release_domain_allowed(release.package_url):
        return decision(False, UPDATE_POLICY_REASON_RELEASE_NOT_AVAILABLE, release)
    try:
        ensure_release_signature_policy(release)
    except ValidationError as exc:
        text = str(exc)
        if 'RELEASE_KEY_REVOKED' in text:
            return decision(False, UPDATE_POLICY_REASON_KEY_REVOKED, release)
        if 'RELEASE_KEY_UNKNOWN' in text:
            return decision(False, UPDATE_POLICY_REASON_KEY_UNKNOWN, release)
        return decision(False, UPDATE_POLICY_REASON_SIGNATURE_INVALID, release)

    comparison = compare_versions(current_version, release.version)
    if comparison is None and current_version:
        return decision(False, UPDATE_POLICY_REASON_INVALID_VERSION, release)
    if comparison is not None and comparison == 0:
        return decision(False, UPDATE_POLICY_REASON_ALREADY_CURRENT, release)
    if comparison is not None and comparison > 0 and not allow_downgrade:
        return decision(False, UPDATE_POLICY_REASON_DOWNGRADE_REQUIRES_FORCE, release)

    minimum_updater = (release.minimum_updater_version or '').strip()
    if update_agent_requires_bootstrap(endpoint, release):
        return decision(False, UPDATE_POLICY_REASON_UPDATER_BOOTSTRAP_REQUIRED, release)
    if minimum_updater:
        updater_version = _updater_version(endpoint)
        updater_comparison = compare_versions(updater_version, minimum_updater)
        if updater_comparison is None or updater_comparison < 0:
            return decision(False, UPDATE_POLICY_REASON_MINIMUM_UPDATER_INCOMPATIBLE, release)

    if not _release_group_allowed(endpoint, release):
        return decision(False, UPDATE_POLICY_REASON_GROUP_NOT_ALLOWED, release)

    bucket = deterministic_rollout_bucket(endpoint, release)
    rollout_percentage = 100 if release.mandatory else min(100, max(0, release.rollout_percentage or 0))
    if not manual and bucket >= rollout_percentage:
        return decision(False, UPDATE_POLICY_REASON_ROLLOUT_NOT_SELECTED, release, bucket)

    policy = endpoint.update_policy or AgentMachine.UPDATE_POLICY_MANUAL
    if policy == AgentMachine.UPDATE_POLICY_MANUAL and not manual:
        return decision(False, UPDATE_POLICY_REASON_MANUAL_POLICY, release, bucket)
    if policy == AgentMachine.UPDATE_POLICY_NOTIFY_ONLY and not manual:
        return decision(False, UPDATE_POLICY_REASON_NOTIFY_ONLY, release, bucket)
    if policy == AgentMachine.UPDATE_POLICY_MAINTENANCE_WINDOW and not manual:
        if not _is_now_inside_window(endpoint.maintenance_window_start, endpoint.maintenance_window_end, now):
            return decision(False, UPDATE_POLICY_REASON_OUTSIDE_MAINTENANCE_WINDOW, release, bucket)
    if policy == AgentMachine.UPDATE_POLICY_AUTOMATIC and not endpoint.auto_update_enabled and not release.mandatory and not manual:
        return decision(False, UPDATE_POLICY_REASON_MANUAL_POLICY, release, bucket)

    return decision(True, UPDATE_POLICY_REASON_ELIGIBLE, release, bucket)


def _safe_ip(value):
    text = str(value or '').strip()
    if not text:
        return None
    try:
        validate_ipv46_address(text)
        return text
    except ValidationError:
        return None


def _allowed(value, allowed, default=''):
    text = _clip(value, 80).strip()
    return text if text in allowed else default


def _sanitize_url(value):
    text = _clip(value, 500).strip()
    if not text:
        return ''
    for marker in ('token=', 'agent_token=', 'enrollment_token=', 'manual_validation_token='):
        if marker in text.lower():
            return text.split('?', 1)[0]
    return text


def _strip_secrets(value):
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if any(secret in normalized for secret in ('token', 'secret', 'password', 'credential')):
                continue
            clean[str(key)[:80]] = _strip_secrets(item)
        return clean
    if isinstance(value, list):
        return [_strip_secrets(item) for item in value[:50]]
    if isinstance(value, str):
        return _clip(value, 1000)
    return value


def _dedupe_agent_event(endpoint, event_type, title, description, severity, metadata=None, cooldown_seconds=300):
    since = timezone.now() - timedelta(seconds=cooldown_seconds)
    exists = endpoint.audit_events.filter(
        event_type=event_type,
        created_at__gte=since,
        metadata__code=(metadata or {}).get('code', ''),
    ).exists()
    if exists:
        return None
    return create_audit_event(
        event_type=event_type,
        title=title,
        description=description,
        severity=severity,
        actor_type=AuditEvent.ACTOR_AGENT,
        actor_name='NightOwlAgent',
        endpoint=endpoint,
        metadata=metadata or {},
    )


def _normalize_machine_id(value):
    candidate = str(value or '').strip()
    if not candidate:
        return ''
    if candidate.upper() in {'HOSTNAME', 'MACHINE_ID'}:
        return ''
    return candidate


def _sync_machine_identity(machine, payload_machine_id, source):
    machine_id = _normalize_machine_id(payload_machine_id)
    if not machine_id:
        return []
    if machine.machine_id and machine.machine_id != machine_id:
        create_audit_event(
            event_type='endpoint.identity_conflict',
            title='Conflito de identidade do endpoint',
            description=f'{machine.hostname} reportou machine_id diferente do cadastrado.',
            severity=AuditEvent.SEVERITY_WARNING,
            actor_type=AuditEvent.ACTOR_AGENT,
            actor_name='NightOwlAgent',
            endpoint=machine,
            metadata={
                'source': source,
                'stored_machine_id': machine.machine_id,
                'reported_machine_id': machine_id,
            },
        )
        return []
    if not machine.machine_id:
        machine.machine_id = machine_id
        return ['machine_id']
    return []


def _section(payload, collection_type, section_name, aliases=()):
    if collection_type == section_name or collection_type in aliases:
        return payload
    for key in (section_name, *aliases):
        value = payload.get(key)
        if value is not None:
            return value
    return {}


def _software_rows(software_payload):
    if isinstance(software_payload, list):
        return software_payload
    if isinstance(software_payload, dict):
        return (
            software_payload.get('installed_software')
            or software_payload.get('items')
            or software_payload.get('software')
            or software_payload.get('rows')
            or []
        )
    return []


def _disk_rows(disk_payload):
    if isinstance(disk_payload, list):
        return disk_payload
    if isinstance(disk_payload, dict):
        return disk_payload.get('disks') or disk_payload.get('items') or []
    return []


def _network_ips(network_payload):
    network = _as_dict(network_payload)
    ips = network.get('ips') or []
    if ips:
        return ips
    primary_ip = network.get('primary_ip')
    if primary_ip:
        return [primary_ip]
    for adapter in _as_list(network.get('adapters') or network.get('interfaces')):
        if not isinstance(adapter, dict):
            continue
        adapter_ips = adapter.get('ipv4_addresses') or adapter.get('ips') or []
        if adapter_ips:
            return adapter_ips
    return []


def _snapshot_defaults(machine, latest=None):
    latest = latest or None
    return {
        'hostname': getattr(latest, 'hostname', None) or machine.hostname,
        'domain': getattr(latest, 'domain', None) or machine.domain or '',
        'logged_user': getattr(latest, 'logged_user', None) or machine.last_logged_user or '',
        'ips': getattr(latest, 'ips', None) or ([str(machine.last_ip)] if machine.last_ip else []),
        'os_name': getattr(latest, 'os_name', None) or machine.os_name or '',
        'os_version': getattr(latest, 'os_version', None) or machine.os_version or '',
        'windows_build': getattr(latest, 'windows_build', None) or machine.windows_build or '',
        'cpu': getattr(latest, 'cpu', None) or '',
        'memory_total_bytes': getattr(latest, 'memory_total_bytes', None),
        'disks': getattr(latest, 'disks', None) or [],
        'manufacturer': getattr(latest, 'manufacturer', None) or machine.manufacturer or '',
        'model': getattr(latest, 'model', None) or machine.model or '',
        'serial_number': getattr(latest, 'serial_number', None) or machine.serial_number or '',
        'uptime_seconds': getattr(latest, 'uptime_seconds', None),
        'installed_software': getattr(latest, 'installed_software', None) or [],
        'defender_status': getattr(latest, 'defender_status', None) or {},
        'raw_payload': getattr(latest, 'raw_payload', None) or {},
    }


def _raw_payload_with_previous_collections(machine, raw_payload):
    payload = dict(raw_payload or {})
    if payload.get('collections'):
        return payload
    for latest in machine.inventory_snapshots.order_by('-received_at')[:30]:
        latest_raw = getattr(latest, 'raw_payload', None) or {}
        latest_collections = latest_raw.get('collections') if isinstance(latest_raw, dict) else None
        if latest_collections:
            payload['collections'] = latest_collections
            payload['latest_collection_type'] = latest_raw.get('latest_collection_type')
            payload['latest_collection_received_at'] = latest_raw.get('latest_collection_received_at')
            break
    return payload


@transaction.atomic
def record_heartbeat(machine, payload: dict, raw_payload: dict) -> InventorySnapshot:
    received_at = timezone.now()
    os_data = _heartbeat_os(payload)
    hardware = payload.get('hardware') or {}
    hostname = payload['hostname']
    domain = payload.get('domain', '')
    ips = _heartbeat_ips(payload)
    logged_user = payload.get('logged_user') or payload.get('username') or ''
    agent_data = _heartbeat_agent(payload)
    heartbeat_at = payload.get('heartbeat_at') or payload.get('timestamp') or received_at
    old_agent_version = machine.agent_version
    identity_update_fields = _sync_machine_identity(machine, payload.get('machine_id') or payload.get('agent_id'), 'heartbeat')

    machine.hostname = hostname
    machine.domain = domain
    machine.fqdn = payload.get('fqdn') or build_fqdn(hostname, domain)
    machine.last_ip = first_ip(ips)
    machine.last_logged_user = logged_user
    machine.os_name = os_data.get('name', '')
    machine.os_version = os_data.get('version', '')
    machine.windows_build = os_data.get('build', '')
    machine.manufacturer = hardware.get('manufacturer', '')
    machine.model = hardware.get('model', '')
    machine.serial_number = hardware.get('serial_number', '')
    agent_update_fields = []
    if agent_data:
        machine.agent_version = _safe_version(agent_data.get('version'))
        machine.agent_mode = agent_data.get('mode', '')
        machine.agent_install_path = agent_data.get('install_path', '')
        machine.agent_task_name = agent_data.get('task_name', '')
        machine.agent_runtime = agent_data.get('runtime', '')
        machine.agent_runtime_version = agent_data.get('runtime_version', '')
        machine.agent_update_source = agent_data.get('update_source', '')
        machine.agent_reported_at = received_at
        agent_update_fields = [
            'agent_version',
            'agent_mode',
            'agent_install_path',
            'agent_task_name',
            'agent_runtime',
            'agent_runtime_version',
            'agent_update_source',
            'agent_reported_at',
        ]
    machine.mark_seen(received_at)
    machine.save(
        update_fields=[
            'hostname',
            'domain',
            'fqdn',
            'first_seen_at',
            'last_seen_at',
            'last_ip',
            'last_logged_user',
            'os_name',
            'os_version',
            'windows_build',
            'manufacturer',
            'model',
            'serial_number',
            *identity_update_fields,
            *agent_update_fields,
            'updated_at',
        ],
    )

    if agent_data and old_agent_version != machine.agent_version:
        create_audit_event(
            event_type='agent.version_changed',
            title='Versao do agente alterada',
            description=f'Versao do agente mudou de {old_agent_version or "-"} para {machine.agent_version or "-"}.',
            severity=AuditEvent.SEVERITY_INFO,
            actor_type=AuditEvent.ACTOR_AGENT,
            actor_name='RmmAgent',
            endpoint=machine,
            metadata={
                'old_version': old_agent_version,
                'new_version': machine.agent_version,
            },
        )

    snapshot_raw_payload = _raw_payload_with_previous_collections(machine, raw_payload)

    return InventorySnapshot.objects.create(
        machine=machine,
        collected_at=heartbeat_at,
        received_at=received_at,
        hostname=hostname,
        domain=domain,
        logged_user=logged_user,
        ips=ips,
        os_name=os_data.get('name', ''),
        os_version=os_data.get('version', ''),
        windows_build=os_data.get('build', ''),
        cpu=hardware.get('cpu', ''),
        memory_total_bytes=hardware.get('memory_total_bytes'),
        disks=payload.get('disks', []),
        manufacturer=hardware.get('manufacturer', ''),
        model=hardware.get('model', ''),
        serial_number=hardware.get('serial_number', ''),
        uptime_seconds=payload.get('uptime_seconds'),
        installed_software=payload.get('installed_software', []),
        defender_status=payload.get('defender_status', {}),
        raw_payload=snapshot_raw_payload,
    )


@transaction.atomic
def record_collection(machine, collection_type: str, payload: dict) -> InventorySnapshot:
    payload = _as_dict(payload)
    latest = machine.inventory_snapshots.order_by('-received_at').first()
    data = _snapshot_defaults(machine, latest)
    identity_update_fields = _sync_machine_identity(machine, payload.get('machine_id') or payload.get('agent_id'), 'collection')

    system = _as_dict(_section(payload, collection_type, 'system'))
    network = _as_dict(_section(payload, collection_type, 'network'))
    hardware = _as_dict(_section(payload, collection_type, 'hardware'))
    disk = _section(payload, collection_type, 'disk', aliases=('disks',))
    software = _section(payload, collection_type, 'software')
    security = _as_dict(_section(payload, collection_type, 'security'))
    patches = _as_dict(_section(payload, collection_type, 'patches', aliases=('patch',)))

    os_data = _as_dict(system.get('os'))
    cpu_data = hardware.get('cpu') or {}
    bios_data = _as_dict(hardware.get('bios'))
    cpu_name = cpu_data.get('name') if isinstance(cpu_data, dict) else str(cpu_data or '')

    data['hostname'] = payload.get('hostname') or data['hostname']
    data['domain'] = system.get('domain') or data['domain']
    data['logged_user'] = system.get('logged_user') or data['logged_user']
    data['ips'] = _network_ips(network) or data['ips']
    data['os_name'] = os_data.get('name') or data['os_name']
    data['os_version'] = os_data.get('version') or data['os_version']
    data['windows_build'] = os_data.get('build') or system.get('os_build') or data['windows_build']
    data['manufacturer'] = hardware.get('manufacturer') or system.get('manufacturer') or data['manufacturer']
    data['model'] = hardware.get('model') or system.get('model') or data['model']
    data['serial_number'] = hardware.get('serial_number') or system.get('serial_number') or bios_data.get('serial_number') or data['serial_number']
    data['uptime_seconds'] = system.get('uptime_seconds') or data['uptime_seconds']
    data['cpu'] = cpu_name or data['cpu']
    data['memory_total_bytes'] = hardware.get('memory_total_bytes') or data['memory_total_bytes']

    disk_items = _disk_rows(disk)
    if disk_items:
        data['disks'] = _normalize_disk_rows(disk_items)
    software_items = _software_rows(software)
    if software_items:
        data['installed_software'] = software_items
    if security:
        data['defender_status'] = _defender_status_from_security(security) or data['defender_status']

    raw_payload = dict(data['raw_payload'] or {})
    collections = dict(raw_payload.get('collections') or {})
    collections[collection_type] = payload
    collected_at = payload.get('collected_at')
    if collection_type == 'full_inventory':
        if system:
            collections['system'] = {**system, 'collected_at': system.get('collected_at') or collected_at}
        if hardware:
            collections['hardware'] = {**hardware, 'collected_at': hardware.get('collected_at') or collected_at}
        if network:
            collections['network'] = {**network, 'collected_at': network.get('collected_at') or collected_at}
        if disk_items:
            collections['disk'] = {
                'disks': disk_items,
                'collected_at': collected_at,
            }
        if software_items:
            collections['software'] = {
                'installed_software': software_items,
                'collected_at': collected_at,
            }
        if security:
            collections['security'] = {**security, 'collected_at': security.get('collected_at') or collected_at}
    if patches:
        collections['patches'] = patches
    raw_payload['collections'] = collections
    raw_payload['latest_collection_type'] = collection_type
    raw_payload['latest_collection_received_at'] = timezone.now().isoformat()
    data['raw_payload'] = raw_payload

    machine.hostname = data['hostname']
    machine.domain = data['domain']
    machine.fqdn = build_fqdn(machine.hostname, machine.domain)
    machine.last_ip = first_ip(data['ips'])
    machine.last_logged_user = data['logged_user']
    machine.os_name = data['os_name']
    machine.os_version = data['os_version']
    machine.windows_build = data['windows_build']
    machine.manufacturer = data['manufacturer']
    machine.model = data['model']
    machine.serial_number = data['serial_number']
    if payload.get('agent_version'):
        machine.agent_version = payload.get('agent_version') or machine.agent_version
    if payload.get('agent_mode'):
        machine.agent_mode = payload.get('agent_mode') or machine.agent_mode
    elif payload.get('agent_version') and not machine.agent_mode:
        machine.agent_mode = 'dotnet-service'
    machine.mark_seen()
    machine.save(update_fields=[
        'hostname',
        'domain',
        'fqdn',
        'first_seen_at',
        'last_seen_at',
        'last_ip',
        'last_logged_user',
        'os_name',
        'os_version',
        'windows_build',
        'manufacturer',
        'model',
        'serial_number',
        *identity_update_fields,
        'agent_version',
        'agent_mode',
        'updated_at',
    ])

    snapshot = InventorySnapshot.objects.create(
        machine=machine,
        collected_at=_parse_agent_datetime(payload.get('collected_at')),
        hostname=data['hostname'],
        domain=data['domain'],
        logged_user=data['logged_user'],
        ips=data['ips'],
        os_name=data['os_name'],
        os_version=data['os_version'],
        windows_build=data['windows_build'],
        cpu=data['cpu'],
        memory_total_bytes=data['memory_total_bytes'],
        disks=data['disks'],
        manufacturer=data['manufacturer'],
        model=data['model'],
        serial_number=data['serial_number'],
        uptime_seconds=data['uptime_seconds'],
        installed_software=data['installed_software'],
        defender_status=data['defender_status'],
        raw_payload=data['raw_payload'],
    )

    event_type = {
        'full_inventory': 'agent.inventory_received',
        'system': 'agent.system_inventory_received',
        'hardware': 'agent.hardware_inventory_received',
        'network': 'agent.network_inventory_received',
        'disk': 'agent.disk_inventory_received',
        'disks': 'agent.disk_inventory_received',
        'security': 'agent.security_inventory_received',
        'software': 'agent.software_inventory_received',
        'patches': 'agent.patch_status_received',
        'patch': 'agent.patch_status_received',
    }.get(collection_type, f'agent.{collection_type}_received')
    create_audit_event(
        event_type=event_type,
        title='Coleta do agente recebida',
        description=f'Coleta {collection_type} recebida de {machine.hostname}.',
        severity=AuditEvent.SEVERITY_INFO,
        actor_type=AuditEvent.ACTOR_AGENT,
        actor_name='NightOwlAgent',
        endpoint=machine,
        metadata={
            'collection_type': collection_type,
            'collection_status': payload.get('status') or 'ok',
            'snapshot_id': str(snapshot.id),
        },
    )
    if collection_type == 'full_inventory':
        section_events = [
            ('system', system, 'agent.system_inventory_received'),
            ('hardware', hardware, 'agent.hardware_inventory_received'),
            ('network', network, 'agent.network_inventory_received'),
            ('software', software, 'agent.software_inventory_received'),
            ('security', security, 'agent.security_inventory_received'),
            ('disks', disk, 'agent.disk_inventory_received'),
            ('patches', patches, 'agent.patch_status_received'),
        ]
        for section_name, section_payload, section_event_type in section_events:
            has_payload = bool(_as_list(section_payload) or _as_dict(section_payload))
            if not has_payload:
                continue
            create_audit_event(
                event_type=section_event_type,
                title='Secao de coleta do agente recebida',
                description=f'Secao {section_name} recebida de {machine.hostname}.',
                severity=AuditEvent.SEVERITY_INFO,
                actor_type=AuditEvent.ACTOR_AGENT,
                actor_name='NightOwlAgent',
                endpoint=machine,
                metadata={
                    'collection_type': collection_type,
                    'section': section_name,
                    'snapshot_id': str(snapshot.id),
                },
            )
    return snapshot


@transaction.atomic
def record_agent_operational_status(machine, payload: dict):
    payload = _as_dict(payload)
    if not payload:
        return None

    machine_id = _normalize_machine_id(payload.get('machine_id') or payload.get('agent_id'))
    if machine_id and machine.machine_id and machine_id != machine.machine_id:
        raise ValueError('machine_id does not match authenticated endpoint.')

    agent = _as_dict(payload.get('agent') or payload.get('agent_status') or payload)
    updater = _as_dict(payload.get('updater') or payload.get('update_state'))
    queue = _as_dict(payload.get('result_queue') or payload.get('pending_results') or payload.get('queue'))
    last_error = _as_dict(payload.get('last_error') or agent.get('last_error'))

    installed_version = _safe_version(
        agent.get('installed_version') or agent.get('agent_version') or payload.get('agent_version') or machine.agent_version
    )
    available_version = _safe_version(agent.get('available_version') or payload.get('available_version'))
    service_status = _clip(agent.get('service_status') or payload.get('service_status'), 80)
    current_user = _clip(agent.get('current_user') or payload.get('current_user') or payload.get('username') or payload.get('logged_user'), 255)
    current_ip = _safe_ip(agent.get('current_ip') or payload.get('current_ip') or payload.get('ip_address'))

    current_stage = _allowed(updater.get('current_stage'), AGENT_DIAGNOSTIC_STAGES)
    update_status = _allowed(updater.get('status'), AGENT_DIAGNOSTIC_STATUSES)
    rollback_status = _allowed(updater.get('rollback_status'), AGENT_DIAGNOSTIC_STATUSES)
    last_error_code = _allowed(last_error.get('code') or agent.get('last_error_code') or payload.get('last_error_code'), AGENT_DIAGNOSTIC_ERROR_CODES)
    update_error_code = _allowed(updater.get('error_code'), AGENT_DIAGNOSTIC_ERROR_CODES)
    rollback_error_code = _allowed(updater.get('rollback_error_code'), AGENT_DIAGNOSTIC_ERROR_CODES)

    result_queue_full = _safe_bool(queue.get('queue_full'))
    pending_count = _safe_int(queue.get('pending_count'))
    running_job_count = _safe_int(agent.get('running_job_count') or payload.get('running_job_count'))

    indicator = AgentOperationalStatus.HEALTH_HEALTHY
    heartbeat_at = _safe_datetime(agent.get('last_heartbeat_at') or payload.get('last_heartbeat_at')) or machine.last_seen_at
    if machine.has_terminal_lifecycle or machine.status in {machine.STATUS_OFFLINE, machine.STATUS_UNINSTALLED}:
        indicator = AgentOperationalStatus.HEALTH_OFFLINE
    if service_status and service_status.lower() not in {'running', 'started'}:
        indicator = AgentOperationalStatus.HEALTH_CRITICAL
    if update_status == 'rollback_failed' or rollback_status == 'rollback_failed':
        indicator = AgentOperationalStatus.HEALTH_CRITICAL
    if result_queue_full:
        indicator = AgentOperationalStatus.HEALTH_CRITICAL
    if indicator == AgentOperationalStatus.HEALTH_HEALTHY and (pending_count or last_error_code or available_version and installed_version and available_version != installed_version):
        indicator = AgentOperationalStatus.HEALTH_ATTENTION
    if heartbeat_at and timezone.now() - heartbeat_at > timedelta(minutes=15):
        indicator = AgentOperationalStatus.HEALTH_OFFLINE

    status, _ = AgentOperationalStatus.objects.select_for_update().get_or_create(endpoint=machine)
    status.installed_version = installed_version or status.installed_version
    status.available_version = available_version or status.available_version
    status.last_heartbeat_at = heartbeat_at or status.last_heartbeat_at
    status.last_inventory_at = _safe_datetime(agent.get('last_inventory_at') or payload.get('last_inventory_at')) or status.last_inventory_at
    status.last_agent_start_at = _safe_datetime(agent.get('last_agent_start_at') or payload.get('last_agent_start_at')) or status.last_agent_start_at
    status.agent_uptime_seconds = _safe_int(agent.get('agent_uptime_seconds') or payload.get('agent_uptime_seconds'), status.agent_uptime_seconds or 0)
    status.service_status = service_status or status.service_status
    status.current_user = current_user or status.current_user
    status.current_ip = current_ip or status.current_ip
    status.pending_result_count = pending_count
    status.running_job_count = running_job_count
    status.last_error_code = last_error_code
    status.last_error_message = _clip(last_error.get('message') or agent.get('last_error_message') or payload.get('last_error_message'), 1000)
    status.last_error_component = _clip(last_error.get('component') or agent.get('last_error_component') or payload.get('last_error_component'), 80)
    status.last_error_at = _safe_datetime(last_error.get('at') or agent.get('last_error_at') or payload.get('last_error_at'))

    status.update_id = _clip(updater.get('update_id'), 80)
    status.update_job_id = _clip(updater.get('job_id'), 80)
    status.from_version = _safe_version(updater.get('from_version'))
    status.target_version = _safe_version(updater.get('target_version'))
    status.update_current_stage = current_stage
    status.update_status = update_status
    status.update_started_at = _safe_datetime(updater.get('started_at'))
    status.update_completed_at = _safe_datetime(updater.get('completed_at'))
    status.rollback_status = rollback_status
    status.rollback_attempt = _safe_int(updater.get('rollback_attempt'))
    status.health_check_confirmed = _safe_bool(updater.get('health_check_confirmed'))
    status.update_error_code = update_error_code
    status.update_error_message = _clip(updater.get('error_message'), 1000)
    status.rollback_error_code = rollback_error_code
    status.rollback_error_message = _clip(updater.get('rollback_error_message'), 1000)
    status.package_url_sanitized = _sanitize_url(updater.get('package_url'))

    status.result_pending_count = pending_count
    status.result_oldest_pending_at = _safe_datetime(queue.get('oldest_pending_at'))
    status.result_retrying_count = _safe_int(queue.get('retrying_count'))
    status.result_quarantined_count = _safe_int(queue.get('quarantined_count'))
    status.result_queue_full = result_queue_full
    status.result_last_send_error = _clip(queue.get('last_send_error'), 1000)
    status.health_indicator = indicator
    status.raw_payload = _strip_secrets(payload)
    status.save()

    if last_error_code:
        _dedupe_agent_event(
            machine,
            'agent.error',
            'Erro reportado pelo agente',
            status.last_error_message or last_error_code,
            AuditEvent.SEVERITY_WARNING,
            metadata={'code': last_error_code, 'component': status.last_error_component},
        )
    if result_queue_full:
        _dedupe_agent_event(
            machine,
            'result.queue_full',
            'Fila de resultados cheia',
            'O agente reportou fila local de resultados cheia.',
            AuditEvent.SEVERITY_CRITICAL,
            metadata={'code': 'RESULT_QUEUE_FULL'},
        )
    if status.result_quarantined_count:
        _dedupe_agent_event(
            machine,
            'result.quarantined',
            'Resultado pendente em quarentena',
            'O agente reportou arquivo corrompido na fila de resultados.',
            AuditEvent.SEVERITY_WARNING,
            metadata={'code': 'RESULT_QUEUE_CORRUPTED', 'count': status.result_quarantined_count},
        )
    if update_status in {'completed', 'failed', 'rolled_back', 'rollback_failed'} or rollback_status in {'rolled_back', 'rollback_failed'}:
        event_type = {
            'completed': 'update.completed',
            'failed': 'update.failed',
            'rolled_back': 'update.rolled_back',
            'rollback_failed': 'update.rollback_failed',
        }.get(rollback_status if rollback_status in {'rolled_back', 'rollback_failed'} else update_status)
        severity = AuditEvent.SEVERITY_CRITICAL if event_type == 'update.rollback_failed' else AuditEvent.SEVERITY_WARNING if event_type in {'update.failed', 'update.rolled_back'} else AuditEvent.SEVERITY_SUCCESS
        _dedupe_agent_event(
            machine,
            event_type,
            'Estado de update reportado',
            f'Update {update_status or rollback_status}: {current_stage or "-"}',
            severity,
            metadata={'code': update_error_code or rollback_error_code or '', 'update_id': status.update_id, 'job_id': status.update_job_id},
        )

    return status
