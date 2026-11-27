"""Opt-in Stable profile for the existing rollout engine."""
import re

from django.conf import settings
from django.core.exceptions import ValidationError

DESKTOP_GROUP = 'stable-desktops'
EXCLUDED_GROUPS = {'servers', 'critical', 'lab', 'laboratory', 'pilot', 'virtual-machines'}
VIRTUAL = re.compile(r'virtual|vmware|qemu|kvm|hyper-v|xen|parallels|bhyve|hvm', re.I)


def enabled(channel):
    return channel == 'stable' and settings.NIGHTOWL_STABLE_DESKTOP_ROLLOUT_ENABLED


def classification_reason(endpoint, slugs):
    os_name = (endpoint.os_name or '').strip().lower()
    if not re.match(r'^(?:microsoft )?windows\s+(?:10|11)(?:\s|$)', os_name) or 'server' in os_name:
        return 'stable_not_windows_desktop'
    if slugs & EXCLUDED_GROUPS or VIRTUAL.search(f'{endpoint.manufacturer} {endpoint.model}'):
        return 'stable_protected_device'
    if DESKTOP_GROUP not in slugs or not endpoint.manufacturer.strip() or not endpoint.model.strip():
        return 'stable_desktop_unconfirmed'
    return ''


def start_on_publication(release, actor, reason):
    if not enabled(release.channel) or release.rollout_paused or release.status != 'published':
        return
    from django.utils import timezone
    from .fleet_policy import PolicyContractError
    from .models import AgentRolloutCampaign
    from .rollout_campaigns import create_agent_rollout_campaign_from_preview, transition_wave
    from .rollout_control import control_rollout_campaign
    from .rollout_planning import release_safety, rollout_orchestration_enabled
    from .models import AgentReleaseSigningKey
    from .services import build_agent_rollout_preview

    if not rollout_orchestration_enabled() or release.rollout_percentage != 100:
        raise ValidationError('Stable integral exige orquestrador ativo e rollout de 100%.')
    batch = settings.NIGHTOWL_STABLE_DESKTOP_ROLLOUT_BATCH_SIZE
    if not 1 <= batch <= 500:
        raise ValidationError('Tamanho de lote Stable deve estar entre 1 e 500.')
    if AgentRolloutCampaign.objects.filter(release=release).exists():
        return  # Never recreate a cancelled or completed campaign implicitly.
    now = timezone.now()
    blocker = release_safety(release, AgentReleaseSigningKey.objects.filter(key_id=release.signature_key_id).first(), now)
    if blocker:
        raise ValidationError(blocker)
    preview = build_agent_rollout_preview(release, now=now)
    try:
        has_targets = bool(preview['eligible_count'])
        campaign = create_agent_rollout_campaign_from_preview(release, {
            'reason': reason, 'expected_cohort_hash': preview['cohort_hash'], 'cohort_schema': preview['cohort_schema'],
            'wave_plan': [{'remaining': True}] if has_targets else [], 'concurrency_limit': batch,
            'ready': has_targets,
        }, actor, now=now)
        if not has_targets:
            from .rollout_campaigns import transition_campaign
            transition_campaign(campaign, 'completed', actor,
                                f'{reason}; approved Stable coverage anchor with zero eligible endpoints', now=now)
            return
        control_rollout_campaign(campaign, {'action': 'start', 'reason': reason, 'expected_state': 'ready'}, actor, now=now)
        wave = transition_wave(campaign.waves.get(), 'ready', actor, reason, now=now)
        transition_wave(wave, 'running', actor, reason, now=now)
    except PolicyContractError as exc:
        raise ValidationError(str(exc)) from exc
