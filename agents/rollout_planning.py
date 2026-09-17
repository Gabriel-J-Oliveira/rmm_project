"""Read-only planning over approved targets, not a new fleet selection."""
import hashlib
import re
import uuid
from collections import Counter, defaultdict
from datetime import timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db.models import Count
from django.db.models.functions import Lower
from django.utils import timezone

from .models import AgentJob, AgentMachine, AgentReleaseSigningKey, AgentRolloutCampaign, AgentRolloutTarget
from .services import _is_now_inside_window, _release_domain_allowed, update_agent_requires_bootstrap
from .versioning import compare_versions


MATERIAL_RELEASE_FIELDS = ('version', 'channel', 'sha256', 'size', 'manifest_sha256',
                           'signature_sha256', 'signature_key_id', 'minimum_updater_version',
                           'legacy_unsigned', 'mandatory')
LIFECYCLE_JOB_TYPES = ('update_agent', 'repair_agent', 'uninstall_agent')


def rollout_orchestration_enabled():
    """Future automatic execution requires BOTH switches. No executor exists yet."""
    return bool(settings.NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED and settings.NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED)


def campaign_release_snapshot(release, *, allowed_groups=None):
    fields = MATERIAL_RELEASE_FIELDS + ('status', 'rollout_percentage', 'rollout_paused', 'revoked', 'signature_valid')
    snapshot = {field: getattr(release, field) for field in fields}
    snapshot['allowed_groups'] = sorted(str(value) for value in
        (allowed_groups if allowed_groups is not None else release.allowed_groups.values_list('pk', flat=True)))
    snapshot['artifact_url_hashes'] = [hashlib.sha256((url or '').encode()).hexdigest() for url in
        (release.package_url, release.checksum_url, release.manifest_url, release.signature_url)]
    return snapshot


def validate_campaign_release_contract(campaign, release, *, allowed_groups=None):
    current = campaign_release_snapshot(release, allowed_groups=allowed_groups)
    fields = MATERIAL_RELEASE_FIELDS + ('artifact_url_hashes', 'allowed_groups')
    changed = [field for field in fields if field not in campaign.release_snapshot or campaign.release_snapshot[field] != current[field]]
    return {'valid': not changed, 'reason_code': 'release_contract_changed' if changed else '', 'changed_fields': changed}


def release_safety(release, key, now):
    if release.revoked or release.status == 'revoked':
        return 'release_revoked'
    if release.rollout_paused or release.status == 'paused':
        return 'release_paused'
    if release.status != 'published':
        return 'release_not_available'
    if release.legacy_unsigned or not release.signature_valid:
        return 'signature_invalid'
    if key is None:
        return 'key_unknown'
    if key.revoked or key.status != 'active':
        return 'key_revoked'
    if key.valid_from and now < key.valid_from:
        return 'key_not_yet_valid'
    if key.valid_until and now > key.valid_until:
        return 'key_expired'
    if key.algorithm != 'RSA-PSS-SHA256' or not key.public_key_xml:
        return 'signature_invalid'
    urls = (release.package_url, release.checksum_url, release.manifest_url, release.signature_url)
    for url in urls:
        try:
            parsed = urlsplit(url or '')
            if not parsed.hostname or parsed.scheme != 'https' or parsed.username or parsed.password or not _release_domain_allowed(url):
                return 'release_domain_invalid'
        except ValueError:
            return 'release_domain_invalid'
    if release.size <= 0 or any(not re.fullmatch('[a-fA-F0-9]{64}', value or '') for value in
                               (release.sha256, release.manifest_sha256, release.signature_sha256)):
        return 'release_metadata_incomplete'
    return ''


def build_rollout_planning_context(campaign, *, wave_id=None):
    pk = getattr(campaign, 'pk', campaign)
    campaign = AgentRolloutCampaign.objects.select_related('release').get(pk=pk)
    waves = list(campaign.waves.order_by('sequence'))
    active = [w for w in waves if w.state in {'running', 'observing', 'paused'}]
    next_wave = next((w for w in waves if w.state not in {'completed', 'cancelled'}), None)
    operational = active[0] if active else next_wave
    wave = next((w for w in waves if str(w.pk) == str(wave_id)), None) if wave_id is not None else operational
    wave_valid = bool(wave and operational and wave.pk == operational.pk and wave.state in {'ready', 'running'}
                      and all(w.state == 'completed' for w in waves if w.sequence < wave.sequence)
                      and (not campaign.current_wave_id or campaign.current_wave_id == wave.pk)
                      and (wave.state != 'running' or campaign.current_wave_id == wave.pk))
    targets = list(AgentRolloutTarget.objects.filter(campaign=campaign, wave=wave).select_related('endpoint')
                   .defer('endpoint__agent_token_hash').prefetch_related('endpoint__rollout_groups')) if wave else []
    jobs = list(AgentJob.objects.filter(endpoint_id__in=campaign.targets.filter(eligibility_at_selection=True).values('endpoint_id'),
                   job_type__in=LIFECYCLE_JOB_TYPES, status__in=['queued', 'sent', 'running'])
                   .only('id', 'endpoint_id', 'created_at', 'expires_at'))
    by_endpoint = defaultdict(list)
    for job in jobs:
        by_endpoint[job.endpoint_id].append(job)
    duplicates = set(AgentMachine.objects.annotate(identity=Lower('machine_id')).values('identity')
                     .annotate(n=Count('id')).filter(n__gt=1).values_list('identity', flat=True))
    release = campaign.release
    key = AgentReleaseSigningKey.objects.filter(key_id=release.signature_key_id).first()
    allowed = list(release.allowed_groups.values_list('pk', flat=True))
    return {'campaign': campaign, 'wave': wave, 'wave_valid': wave_valid, 'targets': targets,
            'jobs': by_endpoint, 'occupied_endpoints': len(by_endpoint), 'duplicates': duplicates,
            'key': key, 'allowed_groups': allowed}


def evaluate_rollout_target_dispatch_safety(campaign, target, *, now, context=None):
    if timezone.is_naive(now):
        raise ValueError('Dispatch planning requires an aware timestamp')
    if context is None:
        context = build_rollout_planning_context(campaign, wave_id=target.wave_id)
        target = next((t for t in context['targets'] if t.pk == target.pk), target)
    campaign, wave = context['campaign'], context['wave']
    endpoint, release = target.endpoint, campaign.release
    result = {'dispatchable': False, 'reason_code': '', 'campaign_id': str(campaign.pk),
              'wave_id': str(target.wave_id) if target.wave_id else None, 'target_id': str(target.pk),
              'endpoint_id': str(target.endpoint_id), 'release_id': str(release.pk),
              'snapshot_version': target.current_version_snapshot, 'current_version': endpoint.agent_version,
              'updater_version': endpoint.updater_version, 'hostname': target.endpoint_hostname_snapshot,
              'bucket': target.rollout_bucket, 'endpoint_status': endpoint.status,
              'endpoint_lifecycle': endpoint.agent_lifecycle_status, 'channel': endpoint.update_channel,
              'policy': endpoint.update_policy, 'last_seen': endpoint.last_seen_at.isoformat() if endpoint.last_seen_at else None}

    def blocked(reason):
        result['reason_code'] = reason
        return result

    if campaign.state != 'running':
        return blocked('campaign_not_running')
    if target.campaign_id != campaign.pk or not wave or target.wave_id != wave.pk or not context['wave_valid']:
        return blocked('wave_not_dispatchable')
    if target.state in {'succeeded', 'failed', 'rolled_back', 'cancelled'}:
        return blocked('target_runtime_terminal')
    if target.agent_job_id or target.state in {'queued', 'running'}:
        return blocked('target_already_dispatched')
    if target.state != 'eligible' or not target.eligibility_at_selection:
        return blocked('target_not_approved')
    safety = release_safety(release, context['key'], now)
    if safety:
        return blocked(safety)
    contract = validate_campaign_release_contract(campaign, release, allowed_groups=context['allowed_groups'])
    if not contract['valid']:
        result['changed_fields'] = contract['changed_fields']
        return blocked('release_contract_changed')
    try:
        identity = uuid.UUID(endpoint.machine_id)
        if identity.int == 0 or identity != uuid.UUID(target.endpoint_machine_id_snapshot):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        return blocked('endpoint_identity_changed')
    if endpoint.machine_id.lower() in context['duplicates']:
        return blocked('machine_identity_ambiguous')
    if not endpoint.is_active:
        return blocked('endpoint_inactive')
    if endpoint.agent_lifecycle_status != 'installed':
        return blocked('endpoint_lifecycle_terminal' if endpoint.has_terminal_lifecycle else 'endpoint_lifecycle_unknown')
    if endpoint.status != 'online':
        return blocked('endpoint_offline')
    if not endpoint.last_seen_at or not timedelta(0) <= now - endpoint.last_seen_at <= timedelta(seconds=campaign.freshness_seconds):
        return blocked('endpoint_stale')
    if endpoint.update_paused:
        return blocked('endpoint_paused')
    groups = list(endpoint.rollout_groups.all())
    if {g.slug for g in groups} & {'critical', 'servers'}:
        return blocked('protected_endpoint_group')
    if endpoint.update_channel != target.update_channel_snapshot:
        return blocked('endpoint_channel_changed')
    if endpoint.update_policy != target.update_policy_snapshot:
        return blocked('endpoint_policy_changed')
    if sorted(str(g.pk) for g in groups) != sorted(target.group_ids_snapshot):
        return blocked('endpoint_groups_changed')
    if endpoint.pinned_agent_version and endpoint.pinned_agent_version != release.version:
        return blocked('pinned_release_mismatch')
    policy_snapshot = target.exclusion_metadata.get('dispatch_policy_snapshot')
    required = {'pinned_agent_version', 'auto_update_enabled', 'maintenance_window_start', 'maintenance_window_end', 'maintenance_window_timezone'}
    if not isinstance(policy_snapshot, dict) or not required <= policy_snapshot.keys():
        return blocked('target_policy_snapshot_incomplete')
    if endpoint.pinned_agent_version != policy_snapshot['pinned_agent_version']:
        return blocked('endpoint_policy_changed')
    if endpoint.update_policy == 'automatic' and not endpoint.auto_update_enabled:
        return blocked('endpoint_policy_changed')
    if endpoint.update_policy not in {'automatic', 'maintenance_window'}:
        return blocked('endpoint_policy_changed')
    if endpoint.update_policy == 'maintenance_window':
        window_fields = ('maintenance_window_start', 'maintenance_window_end', 'maintenance_window_timezone')
        current_window = {field: getattr(endpoint, field).isoformat() if hasattr(getattr(endpoint, field), 'isoformat') else getattr(endpoint, field) for field in window_fields}
        if any(current_window[field] != policy_snapshot[field] for field in window_fields):
            return blocked('endpoint_policy_changed')
        if not endpoint.maintenance_window_start or not endpoint.maintenance_window_end or endpoint.maintenance_window_start == endpoint.maintenance_window_end:
            return blocked('maintenance_window_invalid')
        try:
            ZoneInfo(endpoint.maintenance_window_timezone or settings.TIME_ZONE)
        except (ValueError, ZoneInfoNotFoundError):
            return blocked('maintenance_timezone_invalid')
        if not _is_now_inside_window(endpoint.maintenance_window_start, endpoint.maintenance_window_end, now, endpoint.maintenance_window_timezone):
            return blocked('outside_maintenance_window')
    jobs = context['jobs'][endpoint.pk]
    if jobs:
        return blocked('update_job_stale' if any((j.expires_at and j.expires_at <= now) or j.created_at < now - timedelta(seconds=900) for j in jobs) else 'update_job_active')
    if not endpoint.updater_version or compare_versions(endpoint.updater_version, endpoint.updater_version) is None:
        return blocked('updater_version_unknown')
    if update_agent_requires_bootstrap(endpoint, release):
        return blocked('updater_bootstrap_required')
    if release.minimum_updater_version:
        comparison = compare_versions(endpoint.updater_version, release.minimum_updater_version)
        if comparison is None or comparison < 0:
            return blocked('minimum_updater_incompatible')
    comparison = compare_versions(endpoint.agent_version, release.version)
    if not endpoint.agent_version or comparison is None:
        return blocked('agent_version_unknown')
    if comparison == 0:
        return blocked('target_already_current')
    if comparison > 0:
        return blocked('downgrade_requires_force')
    result.update(dispatchable=True, reason_code='eligible_for_dispatch')
    return result


def build_rollout_dispatch_plan(campaign, *, now=None, wave_id=None):
    now = now or timezone.now()
    if timezone.is_naive(now):
        raise ValueError('Dispatch planning requires an aware timestamp')
    context = build_rollout_planning_context(campaign, wave_id=wave_id)
    campaign, wave = context['campaign'], context['wave']
    targets = sorted(context['targets'], key=lambda t: (t.rollout_bucket, str(t.endpoint_id)))
    results = [evaluate_rollout_target_dispatch_safety(campaign, t, now=now, context=context) for t in targets]
    dispatchable = [t for t in results if t['dispatchable']]
    capacity = max(0, campaign.concurrency_limit - context['occupied_endpoints'])
    return {'campaign_id': str(campaign.pk), 'campaign_state': campaign.state,
            'wave_id': str(wave.pk) if wave else None, 'wave_sequence': wave.sequence if wave else None,
            'concurrency_limit': campaign.concurrency_limit, 'available_capacity': capacity,
            'occupied_capacity': context['occupied_endpoints'], 'total_wave_targets': len(results),
            'dispatchable_count': len(dispatchable), 'blocked_count': len(results) - len(dispatchable),
            'selected_for_dispatch': [t['target_id'] for t in dispatchable[:capacity]],
            'targets': results, 'reason_counts': dict(sorted(Counter(t['reason_code'] for t in results).items())),
            'generated_at': now.isoformat()}
