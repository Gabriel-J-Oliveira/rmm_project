"""Small HTTPS signals and explicit starts, independent of heartbeat delivery."""
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import authenticate_agent_token
from .models import AgentJob, AgentMachine, AuditEvent
from .services import create_audit_event
from .identity import validate_runtime_identity, IdentityConflict


def delivery_candidates(machine, now, include_expired_queued=False):
    query = machine.jobs.filter(
        Q(status=AgentJob.STATUS_QUEUED) |
        Q(status=AgentJob.STATUS_SENT, dispatched_at__lte=now-timedelta(seconds=30))
    )
    valid=Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    if include_expired_queued:
        valid |= Q(status=AgentJob.STATUS_QUEUED)
    return query.filter(valid)


class AgentJobSignalView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        machine = authenticate_agent_token(request)
        response = Response({'protocol': 2, 'available': delivery_candidates(machine, timezone.now(),include_expired_queued=True).exists(),
                             'poll_seconds': 2})
        response['Cache-Control'] = 'no-store'
        return response


class AgentJobStartedView(APIView):
    authentication_classes = []
    permission_classes = []

    @transaction.atomic
    def post(self, request, job_id):
        machine = authenticate_agent_token(request)
        # Match result/reconciliation lock order: job before endpoint.
        job = AgentJob.objects.select_for_update().filter(pk=job_id, endpoint=machine).first()
        if not job:
            return Response({'error': 'job_not_found'}, status=404)
        machine = AgentMachine.objects.select_for_update().get(pk=machine.pk)
        if not isinstance(request.data, dict) or set(request.data) != {'machine_id'}:
            return Response({'error': 'invalid_start'}, status=400)
        try:
            validate_runtime_identity(machine, request.data)
        except IdentityConflict:
            return Response({'error': 'identity_conflict'}, status=409)
        if str(request.data['machine_id']).lower() != str(machine.machine_id).lower():
            return Response({'error': 'identity_conflict'}, status=409)
        if job.status not in (AgentJob.STATUS_SENT, AgentJob.STATUS_RUNNING):
            return Response({'error': 'job_not_startable'}, status=409)
        now = timezone.now()
        from .automation import validate_scheduled_dispatch
        try:
            validate_scheduled_dispatch(job)
        except (PermissionError, ValueError):
            return Response({'error':'automation_authorization_revoked'},status=409)
        if job.status != AgentJob.STATUS_RUNNING and job.expires_at and job.expires_at <= now:
            return Response({'error': 'job_expired'}, status=409)
        if job.status == AgentJob.STATUS_SENT:
            job.status = AgentJob.STATUS_RUNNING
            job.started_at = now
            job.save(update_fields=['status', 'started_at', 'updated_at'])
            create_audit_event(event_type='job.started', title='Execucao aceita pelo agente',
                description='Inicio confirmado pelo protocolo de jobs v2.', endpoint=machine,
                severity=AuditEvent.SEVERITY_INFO, actor_type=AuditEvent.ACTOR_AGENT,
                actor_name='NightOwlAgent', metadata={'job_id': str(job.pk), 'protocol': 2})
        return Response({'status': 'ok', 'job_id': str(job.pk), 'state': 'running',
                         'started_at': job.started_at.isoformat()})
