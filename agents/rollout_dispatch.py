"""Explicit transactional execution of an approved, already-running wave."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .fleet_policy import PolicyContractError
from .lifecycle_jobs import lock_lifecycle_endpoints
from .models import AgentJob, AgentRelease, AgentReleaseSigningKey, AgentRolloutCampaign, AgentRolloutTarget, AuditEvent
from .rollout_planning import build_rollout_dispatch_plan, rollout_orchestration_enabled
from .services import AgentUpdateDecision, build_update_agent_job_payload


def dispatch_rollout_campaign(campaign, *, now):
    if timezone.is_naive(now):
        raise ValueError('Dispatch requires an aware timestamp')
    if not rollout_orchestration_enabled():
        raise PolicyContractError('rollout_orchestrator_disabled', status=409)
    with transaction.atomic():
        campaign = AgentRolloutCampaign.objects.select_for_update().get(pk=getattr(campaign, 'pk', campaign))
        if campaign.state != 'running':
            raise PolicyContractError('campaign_not_running', status=409)
        wave = campaign.waves.select_for_update().filter(pk=campaign.current_wave_id, state='running').first()
        if wave is None:
            raise PolicyContractError('wave_not_running', status=409)
        list(AgentRolloutTarget.objects.select_for_update().filter(campaign=campaign, wave=wave).order_by('pk'))
        # Lock all approved endpoints, not just candidates, to freeze occupancy too.
        lock_lifecycle_endpoints(campaign.targets.filter(eligibility_at_selection=True).values('endpoint_id'))
        AgentRelease.objects.select_for_update().get(pk=campaign.release_id)
        list(AgentReleaseSigningKey.objects.select_for_update().filter(key_id=campaign.release.signature_key_id))
        plan = build_rollout_dispatch_plan(campaign, now=now, wave_id=wave.pk)
        selected = plan['selected_for_dispatch']
        targets = {str(t.pk): t for t in campaign.targets.filter(pk__in=selected).select_related('endpoint')}
        release = AgentRelease.objects.get(pk=campaign.release_id)
        jobs, changed = [], []
        for target_id in selected:
            target = targets[target_id]
            decision = AgentUpdateDecision(True, 'eligible_for_dispatch', target.endpoint, release=release,
                current_version=target.endpoint.agent_version, target_version=release.version,
                selected_release_id=str(release.pk), channel=release.channel, rollout_bucket=target.rollout_bucket)
            payload = build_update_agent_job_payload(target.endpoint, decision, force=False, source='rollout_campaign')
            payload['rollout_metadata'] = {'campaign_id': str(campaign.pk), 'wave_id': str(wave.pk),
                'wave_sequence': wave.sequence, 'target_id': target_id, 'cohort_hash': campaign.cohort_hash,
                'cohort_schema': campaign.cohort_schema}
            job = AgentJob(endpoint=target.endpoint, agent_release=release, job_type='update_agent',
                created_by='rollout_orchestrator', payload=payload, correlation_id=target_id, attempt=1,
                timeout_seconds=900, expires_at=now + timedelta(minutes=30))
            jobs.append(job)
            target.agent_job = job
            target.state = 'queued'
            target.started_at = now
            target.updated_at = now
            changed.append(target)
        AgentJob.objects.bulk_create(jobs)
        AgentRolloutTarget.objects.bulk_update(changed, ['agent_job', 'state', 'started_at', 'updated_at'])
        if jobs:
            AuditEvent.objects.create(event_type='rollout.dispatch_created', title='Rollout dispatch created',
                actor_type=AuditEvent.ACTOR_SCHEDULER, actor_name='RolloutOrchestrator',
                metadata={'campaign_id': str(campaign.pk), 'wave_id': str(wave.pk), 'wave_sequence': wave.sequence,
                    'count': len(jobs), 'target_ids': selected, 'job_ids': [str(j.pk) for j in jobs],
                    'release_id': str(release.pk), 'cohort_hash': campaign.cohort_hash})
        return {'campaign_id': str(campaign.pk), 'wave_id': str(wave.pk), 'created_count': len(jobs),
                'job_ids': [str(j.pk) for j in jobs], 'target_ids': selected, 'plan': plan}
