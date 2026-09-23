"""Shared endpoint lock protocol for lifecycle writers; never reconcile history."""
from django.db import connection

from .models import AgentJob, AgentMachine

LIFECYCLE_TYPES = ('update_agent', 'repair_agent', 'uninstall_agent')
ACTIVE_STATUSES = ('queued', 'sent', 'running')


def lock_lifecycle_endpoint(endpoint):
    endpoints = lock_lifecycle_endpoints([getattr(endpoint, 'pk', endpoint)])
    if not endpoints:
        raise AgentMachine.DoesNotExist
    return endpoints[0]


def lock_lifecycle_endpoints(endpoint_ids):
    if not connection.in_atomic_block:
        raise RuntimeError('Lifecycle endpoint locking requires an atomic transaction')
    return list(AgentMachine.objects.select_for_update().filter(pk__in=endpoint_ids).order_by('pk'))


def active_lifecycle_job(endpoint):
    return AgentJob.objects.filter(endpoint=endpoint, job_type__in=LIFECYCLE_TYPES,
                                   status__in=ACTIVE_STATUSES).order_by('-created_at').first()


def agent_job_parameters(job):
    # Policy/correlation metadata is stored for backend auditing, not sent to the strict agent contract.
    excluded = {'rollout_metadata', 'policy_channel'}
    if job.job_type == AgentJob.TYPE_REPAIR_AGENT:
        excluded.add('source_channel')
    return {key: value for key, value in job.payload.items() if key not in excluded}
