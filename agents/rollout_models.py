"""Persistent rollout decisions; no dispatch implementation exists here."""
import uuid

from django.conf import settings
from django.db import models


def default_auto_pause_policy():
    return {
        'schema': 1,
        'enabled': True,
        'failed_count': 1,
        'rolled_back_count': 1,
        'cancelled_count': 1,
        'stalled_count': 1,
        'offline_post_update_count': 1,
    }


class AgentRolloutCampaign(models.Model):
    STATES = ('draft', 'ready', 'running', 'paused', 'completed', 'aborted')
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    release = models.ForeignKey('AgentRelease', on_delete=models.PROTECT)
    release_snapshot = models.JSONField(default=dict)
    channel_snapshot = models.CharField(max_length=20)
    state = models.CharField(max_length=20, default='draft', choices=[(s, s) for s in STATES])
    cohort_schema = models.PositiveIntegerField()
    cohort_hash = models.CharField(max_length=64)
    preview_generated_at = models.DateTimeField()
    freshness_seconds = models.PositiveIntegerField()
    target_group_ids_snapshot = models.JSONField(default=list)
    wave_plan = models.JSONField(default=list)
    total_candidates = models.PositiveIntegerField()
    eligible_count = models.PositiveIntegerField()
    excluded_count = models.PositiveIntegerField()
    current_wave = models.ForeignKey('AgentRolloutWave', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    concurrency_limit = models.PositiveIntegerField(default=1)
    minimum_observation_seconds = models.PositiveIntegerField(default=0)
    auto_pause_policy = models.JSONField(default=default_auto_pause_policy)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='rollout_campaigns_created')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='rollout_campaigns_approved')
    administrative_reason = models.CharField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True)
    paused_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    aborted_at = models.DateTimeField(null=True)

    class Meta:
        app_label = 'agents'
        constraints = [
            models.UniqueConstraint(fields=['release'], condition=~models.Q(state__in=['completed', 'aborted']), name='rollout_one_live_campaign'),
            models.CheckConstraint(condition=models.Q(state__in=['draft', 'ready', 'running', 'paused', 'completed', 'aborted']), name='rollout_campaign_state'),
            models.CheckConstraint(condition=models.Q(concurrency_limit__gte=1), name='rollout_concurrency_positive'),
            models.CheckConstraint(condition=models.Q(total_candidates=models.F('eligible_count') + models.F('excluded_count')), name='rollout_candidate_counts'),
        ]


class AgentRolloutWave(models.Model):
    STATES = ('pending', 'ready', 'running', 'observing', 'completed', 'paused', 'cancelled')
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(AgentRolloutCampaign, on_delete=models.PROTECT, related_name='waves')
    sequence = models.PositiveIntegerField()
    state = models.CharField(max_length=20, default='pending', choices=[(s, s) for s in STATES])
    resume_state = models.CharField(max_length=20, blank=True)
    target_count = models.PositiveIntegerField()
    minimum_observation_seconds = models.PositiveIntegerField(default=0)
    eligible_target_count = models.PositiveIntegerField()
    excluded_target_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True)
    observation_started_at = models.DateTimeField(null=True)
    observation_accumulated_seconds = models.PositiveIntegerField(default=0)
    observation_resumed_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'agents'
        ordering = ['sequence']
        constraints = [
            models.UniqueConstraint(fields=['campaign', 'sequence'], name='rollout_wave_sequence'),
            models.UniqueConstraint(fields=['campaign'], condition=models.Q(state__in=['running', 'observing', 'paused']), name='rollout_one_active_wave'),
            models.CheckConstraint(condition=models.Q(state__in=['pending', 'ready', 'running', 'observing', 'completed', 'paused', 'cancelled']), name='rollout_wave_state'),
            models.CheckConstraint(condition=models.Q(sequence__gte=1, target_count__gte=1, excluded_target_count=0, eligible_target_count=models.F('target_count')), name='rollout_wave_counts'),
        ]


class AgentRolloutTarget(models.Model):
    STATES = ('excluded', 'eligible', 'queued', 'running', 'succeeded', 'failed', 'rolled_back', 'cancelled')
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(AgentRolloutCampaign, on_delete=models.PROTECT, related_name='targets')
    wave = models.ForeignKey(AgentRolloutWave, null=True, on_delete=models.PROTECT, related_name='targets')
    endpoint = models.ForeignKey('AgentMachine', on_delete=models.PROTECT)
    endpoint_machine_id_snapshot = models.CharField(max_length=255, blank=True)
    endpoint_hostname_snapshot = models.CharField(max_length=255, blank=True)
    current_version_snapshot = models.CharField(max_length=100, blank=True)
    updater_version_snapshot = models.CharField(max_length=100, blank=True)
    update_channel_snapshot = models.CharField(max_length=20)
    update_policy_snapshot = models.CharField(max_length=30)
    group_ids_snapshot = models.JSONField(default=list)
    rollout_bucket = models.PositiveIntegerField()
    eligibility_at_selection = models.BooleanField()
    reason_code = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=20, choices=[(s, s) for s in STATES])
    selected_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    agent_job = models.ForeignKey('AgentJob', null=True, on_delete=models.PROTECT)
    exclusion_metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'agents'
        constraints = [
            models.UniqueConstraint(fields=['campaign', 'endpoint'], name='rollout_target_endpoint'),
            models.CheckConstraint(condition=models.Q(rollout_bucket__lte=99), name='rollout_target_bucket'),
            models.UniqueConstraint(fields=['agent_job'], condition=models.Q(agent_job__isnull=False), name='rollout_target_unique_job'),
            models.CheckConstraint(condition=(
                models.Q(state='excluded', eligibility_at_selection=False, wave__isnull=True, agent_job__isnull=True, started_at__isnull=True, completed_at__isnull=True)
                | models.Q(state='eligible', eligibility_at_selection=True, wave__isnull=False, agent_job__isnull=True, started_at__isnull=True, completed_at__isnull=True)
                | (models.Q(eligibility_at_selection=True, wave__isnull=False, agent_job__isnull=False, started_at__isnull=False)
                   & (models.Q(state__in=['queued', 'running'], completed_at__isnull=True)
                      | models.Q(state__in=['succeeded', 'failed', 'rolled_back', 'cancelled'], completed_at__isnull=False)))
            ), name='rollout_target_runtime'),
        ]
