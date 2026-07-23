import hashlib
import json
import logging
from datetime import timedelta

from django.db import models, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .audit import create_audit_event, get_client_ip
from .authentication import authenticate_agent_token
from .models import (
    AgentEnrollmentLog,
    AgentEnrollmentToken,
    AgentJob,
    AgentJobResultReceipt,
    AgentMachine,
    AgentManualValidationToken,
    AuditEvent,
    hash_enrollment_token,
    hash_manual_validation_token,
)
from .serializers import AgentEnrollmentSerializer, HeartbeatSerializer
from .services import (
    build_fqdn,
    evaluate_agent_update_policy,
    record_agent_operational_status,
    record_collection,
    record_heartbeat,
)


logger = logging.getLogger(__name__)


RESULT_FINAL_STATUSES = {
    AgentJob.STATUS_COMPLETED,
    AgentJob.STATUS_FAILED,
    AgentJob.STATUS_EXPIRED,
    AgentJob.STATUS_CANCELLED,
    AgentJob.STATUS_TIMED_OUT,
    AgentJob.STATUS_DUPLICATE,
    AgentJob.STATUS_UNSUPPORTED,
    AgentJob.STATUS_INVALID_PARAMETERS,
    AgentJob.STATUS_INTERRUPTED,
    AgentJob.STATUS_ROLLED_BACK,
    AgentJob.STATUS_ROLLBACK_FAILED,
}


def _payload_sha256(payload):
    encoded = json.dumps(payload or {}, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _result_id_from_request(request, payload):
    return (
        request.headers.get('Idempotency-Key')
        or request.META.get('HTTP_IDEMPOTENCY_KEY')
        or payload.get('result_id')
        or payload.get('resultId')
        or ''
    ).strip()[:80]


def _normalize_job_status(value):
    job_status = str(value or 'unknown').strip().lower()
    aliases = {
        'pending': AgentJob.STATUS_QUEUED,
        'dispatched': AgentJob.STATUS_SENT,
        'canceled': AgentJob.STATUS_CANCELLED,
        'interrupted': AgentJob.STATUS_INTERRUPTED,
    }
    return aliases.get(job_status, job_status)


def _parse_agent_datetime(value, default=None):
    if value is None:
        return default
    if hasattr(value, 'tzinfo'):
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value
    parsed = parse_datetime(str(value))
    if parsed is None:
        return default
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


class AgentHeartbeatView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        machine = authenticate_agent_token(request)
        serializer = HeartbeatSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning('Invalid heartbeat payload: %s', serializer.errors)
            return Response(
                {
                    'error': 'invalid_payload',
                    'detail': serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            snapshot = record_heartbeat(
                machine=machine,
                payload=serializer.validated_data,
                raw_payload=request.data,
            )
            diagnostic_payload = request.data.get('diagnostics') or request.data.get('operational_status') or request.data.get('status')
            if isinstance(diagnostic_payload, dict):
                diagnostic_payload.setdefault('machine_id', request.data.get('machine_id') or serializer.validated_data.get('machine_id'))
                diagnostic_payload.setdefault('agent_version', request.data.get('agent_version') or serializer.validated_data.get('agent_version'))
                diagnostic_payload.setdefault('last_heartbeat_at', request.data.get('heartbeat_at') or request.data.get('timestamp'))
                record_agent_operational_status(machine, diagnostic_payload)
        except Exception as exc:
            logger.exception('Failed to record heartbeat for machine_id=%s', machine.id)
            return Response(
                {
                    'error': 'heartbeat_record_failed',
                    'detail': str(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'status': 'ok',
                'machine_id': machine.machine_id or str(machine.id),
                'snapshot_id': str(snapshot.id),
            },
            status=status.HTTP_200_OK,
        )


class AgentStatusView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        machine = authenticate_agent_token(request)
        payload = request.data if isinstance(request.data, dict) else {}
        if not payload:
            return Response(
                {'error': 'invalid_payload', 'detail': 'Payload de status vazio.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            status_record = record_agent_operational_status(machine, payload)
        except ValueError as exc:
            return Response(
                {'error': 'machine_id_mismatch', 'detail': str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except Exception as exc:
            logger.exception('Failed to record agent status for endpoint_id=%s', machine.id)
            return Response(
                {'error': 'status_record_failed', 'detail': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'status': 'ok',
                'machine_id': machine.machine_id or str(machine.id),
                'health_indicator': status_record.health_indicator if status_record else 'unknown',
            },
            status=status.HTTP_200_OK,
        )


class AgentUpdatePolicyView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        machine = authenticate_agent_token(request)
        machine_id = (request.query_params.get('machine_id') or '').strip()
        if machine_id and machine.machine_id and machine_id.lower() != machine.machine_id.lower():
            return Response(
                {
                    'error': 'machine_id_mismatch',
                    'detail': 'machine_id nao corresponde ao endpoint autenticado.',
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        decision = evaluate_agent_update_policy(machine, for_agent=True)
        auto_job_id = ''
        if decision.eligible and decision.release:
            pending_update = machine.jobs.filter(
                job_type=AgentJob.TYPE_UPDATE_AGENT,
                status__in=[AgentJob.STATUS_QUEUED, AgentJob.STATUS_SENT, AgentJob.STATUS_RUNNING],
            ).order_by('-created_at').first()
            if pending_update:
                auto_job_id = str(pending_update.id)
            else:
                release = decision.release
                auto_job = AgentJob.objects.create(
                    endpoint=machine,
                    agent_release=release,
                    job_type=AgentJob.TYPE_UPDATE_AGENT,
                    created_by='update_policy',
                    payload={
                        'target_version': release.version,
                        'channel': decision.channel,
                        'source_channel': decision.channel,
                        'release_id': str(release.id),
                        'policy_reason': decision.reason_code,
                        'package_url': release.package_url,
                        'checksum_url': release.checksum_url,
                        'sha256': release.sha256,
                        'size': release.size,
                        'minimum_updater_version': release.minimum_updater_version,
                        'mandatory': release.mandatory,
                        'force': False,
                        'source': 'update_policy',
                    },
                    correlation_id=str(release.id),
                    attempt=1,
                    timeout_seconds=900,
                    expires_at=timezone.now() + timedelta(minutes=30),
                )
                auto_job_id = str(auto_job.id)
                create_audit_event(
                    event_type='job.created',
                    title='Job automatico de update criado',
                    description=f'Politica de update criou job para {machine.hostname}.',
                    severity=AuditEvent.SEVERITY_INFO,
                    actor_type=AuditEvent.ACTOR_SCHEDULER,
                    actor_name='UpdatePolicy',
                    endpoint=machine,
                    metadata={'job_id': auto_job_id, 'release_id': str(release.id), 'target_version': release.version},
                )
        create_audit_event(
            event_type='agent.update_policy_evaluated',
            title='Politica de update avaliada',
            description=f'Politica de update avaliada para {machine.hostname}.',
            severity=AuditEvent.SEVERITY_DEBUG,
            actor_type=AuditEvent.ACTOR_AGENT,
            actor_name='NightOwlAgent',
            endpoint=machine,
            metadata={
                'eligible': decision.eligible,
                'reason_code': decision.reason_code,
                'release_id': decision.selected_release_id,
                'target_version': decision.target_version,
                'channel': decision.channel,
                'rollout_bucket': decision.rollout_bucket,
            },
        )
        payload = decision.as_agent_payload()
        if auto_job_id:
            payload['job_id'] = auto_job_id
        return Response(payload, status=status.HTTP_200_OK)


class AgentInventoryCollectionView(APIView):
    authentication_classes = []
    permission_classes = []
    collection_type = 'inventory'

    def post(self, request):
        machine = authenticate_agent_token(request)
        payload = request.data if isinstance(request.data, dict) else {}
        if not payload:
            return Response(
                {'error': 'invalid_payload', 'detail': 'Payload de coleta vazio.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            snapshot = record_collection(
                machine=machine,
                collection_type=self.collection_type,
                payload=payload,
            )
        except Exception as exc:
            logger.exception('Failed to record %s collection for machine_id=%s', self.collection_type, machine.id)
            return Response(
                {
                    'error': 'collection_record_failed',
                    'detail': str(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'status': 'ok',
                'collection_type': self.collection_type,
                'machine_id': machine.machine_id or str(machine.id),
                'snapshot_id': str(snapshot.id),
            },
            status=status.HTTP_200_OK,
        )


class AgentJobsPullView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        machine = authenticate_agent_token(request)
        now = timezone.now()
        expired = machine.jobs.filter(
            status=AgentJob.STATUS_QUEUED,
            expires_at__isnull=False,
            expires_at__lte=now,
        )
        for job in expired:
            job.status = AgentJob.STATUS_EXPIRED
            job.finished_at = now
            job.error_message = 'Job expirou antes do pull do agente.'
            job.save(update_fields=['status', 'finished_at', 'error_message', 'updated_at'])
            create_audit_event(
                event_type='job.expired',
                title='Job expirado',
                description=f'Job {job.job_type} expirou antes de ser enviado ao agente.',
                severity=AuditEvent.SEVERITY_WARNING,
                actor_type=AuditEvent.ACTOR_SYSTEM,
                actor_name='Sistema',
                endpoint=machine,
                metadata={'job_id': str(job.id), 'job_type': job.job_type},
            )

        jobs = list(machine.jobs.filter(status=AgentJob.STATUS_QUEUED).order_by('queued_at')[:5])
        response_jobs = []
        for job in jobs:
            job.status = AgentJob.STATUS_SENT
            job.dispatched_at = now
            job.started_at = now
            job.save(update_fields=['status', 'dispatched_at', 'started_at', 'updated_at'])
            response_jobs.append({
                'id': str(job.id),
                'job_id': str(job.id),
                'type': job.job_type,
                'job_type': job.job_type,
                'payload': job.payload,
                'parameters': job.payload,
                'created_at': job.created_at.isoformat(),
                'timeout_seconds': job.payload.get('timeout_seconds') or 300,
                'attempt': job.attempt or 1,
                'max_attempts': job.payload.get('max_attempts') or 1,
                'priority': job.payload.get('priority') or 0,
                'correlation_id': job.correlation_id or '',
                'expires_at': job.expires_at.isoformat() if job.expires_at else None,
            })
            create_audit_event(
                event_type='job.dispatched_to_agent',
                title='Job enviado ao agente',
                description=f'Job {job.job_type} enviado para {machine.hostname}.',
                severity=AuditEvent.SEVERITY_INFO,
                actor_type=AuditEvent.ACTOR_AGENT,
                actor_name='NightOwlAgent',
                endpoint=machine,
                metadata={'job_id': str(job.id), 'job_type': job.job_type},
            )
            create_audit_event(
                event_type='job.dispatched',
                title='Job despachado',
                description=f'Job {job.job_type} despachado para {machine.hostname}.',
                severity=AuditEvent.SEVERITY_INFO,
                actor_type=AuditEvent.ACTOR_AGENT,
                actor_name='NightOwlAgent',
                endpoint=machine,
                metadata={'job_id': str(job.id), 'job_type': job.job_type},
            )
            create_audit_event(
                event_type='job.started',
                title='Job iniciado pelo agente',
                description=f'Job {job.job_type} iniciou execucao em {machine.hostname}.',
                severity=AuditEvent.SEVERITY_INFO,
                actor_type=AuditEvent.ACTOR_AGENT,
                actor_name='NightOwlAgent',
                endpoint=machine,
                metadata={'job_id': str(job.id), 'job_type': job.job_type},
            )
            logger.info(
                'job.dispatched endpoint_id=%s job_id=%s job_type=%s hostname=%s',
                machine.id,
                job.id,
                job.job_type,
                machine.hostname,
            )

        create_audit_event(
            event_type='job.pull_requested',
            title='Agente consultou fila de jobs',
            description=f'{machine.hostname} consultou jobs pendentes.',
            severity=AuditEvent.SEVERITY_DEBUG,
            actor_type=AuditEvent.ACTOR_AGENT,
            actor_name='NightOwlAgent',
            endpoint=machine,
            metadata={'jobs_returned': len(response_jobs)},
        )
        return Response({'jobs': response_jobs}, status=status.HTTP_200_OK)


class AgentJobsResultView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        machine = authenticate_agent_token(request)
        payload = request.data if isinstance(request.data, dict) else {}
        job_id = payload.get('job_id') or payload.get('id') or ''
        result_id = _result_id_from_request(request, payload)
        payload_hash = _payload_sha256(payload)
        job_status = _normalize_job_status(payload.get('status'))
        job = None
        if job_id:
            job = AgentJob.objects.filter(pk=job_id, endpoint=machine).first()
        if job_id and job is None:
            create_audit_event(
                event_type='job.result_rejected',
                title='Resultado de job rejeitado',
                description=f'Resultado recebido para job desconhecido em {machine.hostname}.',
                severity=AuditEvent.SEVERITY_WARNING,
                actor_type=AuditEvent.ACTOR_AGENT,
                actor_name='NightOwlAgent',
                endpoint=machine,
                metadata={'job_id': str(job_id), 'status': job_status},
            )
            return Response(
                {
                    'error': 'job_not_found',
                    'detail': 'Job nao encontrado para este endpoint.',
                    'machine_id': machine.machine_id or str(machine.id),
                    'job_id': str(job_id),
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        if result_id:
            receipt = AgentJobResultReceipt.objects.filter(result_id=result_id).select_related('job', 'endpoint').first()
            if receipt:
                receipt.last_seen_at = timezone.now()
                if receipt.payload_sha256 != payload_hash:
                    receipt.conflict_count += 1
                    receipt.last_conflict_at = timezone.now()
                    receipt.save(update_fields=['last_seen_at', 'conflict_count', 'last_conflict_at'])
                    create_audit_event(
                        event_type='job.result_conflict',
                        title='Conflito de idempotencia em resultado de job',
                        description=f'Resultado {result_id} reenviado com payload diferente.',
                        severity=AuditEvent.SEVERITY_CRITICAL,
                        actor_type=AuditEvent.ACTOR_AGENT,
                        actor_name='NightOwlAgent',
                        endpoint=machine,
                        metadata={'job_id': str(job_id), 'result_id': result_id},
                    )
                    return Response(
                        {
                            'error': 'idempotency_conflict',
                            'detail': 'result_id ja recebido com payload diferente.',
                            'result_id': result_id,
                            'job_id': str(job_id),
                        },
                        status=status.HTTP_409_CONFLICT,
                    )
                receipt.save(update_fields=['last_seen_at'])
                return Response(
                    {
                        'status': 'ok',
                        'duplicate': True,
                        'result_id': result_id,
                        'machine_id': machine.machine_id or str(machine.id),
                        'job_id': str(receipt.job_id or job_id),
                        'job_status': receipt.job.status if receipt.job else job_status,
                        'updated': False,
                    },
                    status=status.HTTP_200_OK,
                )
        if job:
            job.status = job_status if job_status in dict(AgentJob.STATUS_CHOICES) else AgentJob.STATUS_FAILED
            job.started_at = _parse_agent_datetime(payload.get('started_at'), job.started_at)
            if job.status in RESULT_FINAL_STATUSES:
                job.finished_at = _parse_agent_datetime(payload.get('finished_at'), timezone.now())
            job.duration_seconds = payload.get('duration_seconds')
            job.exit_code = payload.get('exit_code')
            job.stdout = payload.get('stdout') or ''
            job.stderr = payload.get('stderr') or ''
            job.result = payload.get('result') or {}
            job.error_message = payload.get('error_message') or ''
            job.result_id = result_id or job.result_id
            job.correlation_id = payload.get('correlation_id') or job.correlation_id
            job.attempt = payload.get('attempt') or job.attempt or 1
            job.timeout_seconds = payload.get('timeout_seconds') or job.timeout_seconds
            job.error_code = payload.get('error_code') or (job.result.get('error_code') if isinstance(job.result, dict) else '') or ''
            job.output_truncated = bool(payload.get('output_truncated') or (job.result.get('output_truncated') if isinstance(job.result, dict) else False))
            job.result_received_at = timezone.now()
            job.save(update_fields=[
                'status',
                'started_at',
                'finished_at',
                'duration_seconds',
                'exit_code',
                'stdout',
                'stderr',
                'result',
                'result_id',
                'correlation_id',
                'attempt',
                'timeout_seconds',
                'error_code',
                'output_truncated',
                'error_message',
                'result_received_at',
                'updated_at',
            ])
            if job.status == AgentJob.STATUS_COMPLETED and isinstance(job.result, dict):
                collection_type = {
                    AgentJob.TYPE_FORCE_INVENTORY: 'full_inventory',
                    AgentJob.TYPE_COLLECT_DISKS: 'disk',
                    AgentJob.TYPE_COLLECT_SECURITY: 'security',
                    AgentJob.TYPE_COLLECT_SOFTWARE: 'software',
                    AgentJob.TYPE_WINDOWS_UPDATE_SCAN: 'patches',
                }.get(job.job_type)
                if collection_type:
                    try:
                        record_collection(machine=machine, collection_type=collection_type, payload=job.result)
                    except Exception:
                        logger.exception('Failed to persist job result collection for job_id=%s', job.id)
            if job.job_type == AgentJob.TYPE_UPDATE_AGENT:
                logger.info(
                    'update_agent job result endpoint_id=%s job_id=%s status=%s exit_code=%s',
                    machine.id,
                    job.id,
                    job.status,
                    job.exit_code,
                )
            logger.info(
                'job.result.received endpoint_id=%s job_id=%s job_type=%s status=%s exit_code=%s',
                machine.id,
                job.id,
                job.job_type,
                job.status,
                job.exit_code,
            )
            if job.status == AgentJob.STATUS_FAILED:
                logger.warning(
                    'job.failed endpoint_id=%s job_id=%s job_type=%s error=%s',
                    machine.id,
                    job.id,
                    job.job_type,
                    job.error_message,
                )
        if result_id:
            AgentJobResultReceipt.objects.create(
                result_id=result_id,
                job=job,
                endpoint=machine,
                payload_sha256=payload_hash,
                first_payload=payload,
            )
        event_type = (
            'job.completed' if job_status == 'completed'
            else 'job.interrupted' if job_status == 'interrupted'
            else 'update.rolled_back' if job_status == 'rolled_back'
            else 'update.rollback_failed' if job_status == 'rollback_failed'
            else 'job.failed' if job_status in {'failed', 'timed_out', 'expired', 'unsupported', 'invalid_parameters'}
            else 'job.result_received'
        )
        severity = (
            AuditEvent.SEVERITY_SUCCESS if job_status in {'completed', 'duplicate'}
            else AuditEvent.SEVERITY_CRITICAL if job_status in {'rollback_failed'}
            else AuditEvent.SEVERITY_WARNING if job_status in {'failed', 'expired', 'timed_out', 'interrupted', 'rolled_back', 'unsupported', 'invalid_parameters'}
            else AuditEvent.SEVERITY_INFO
        )
        create_audit_event(
            event_type=event_type,
            title='Resultado de job recebido',
            description=f'Resultado {job_status} recebido de {machine.hostname}.',
            severity=severity,
            actor_type=AuditEvent.ACTOR_AGENT,
            actor_name='NightOwlAgent',
            endpoint=machine,
            metadata={
                'job_id': str(job_id),
                'result_id': result_id,
                'correlation_id': payload.get('correlation_id') or '',
                'job_type': payload.get('job_type') or '',
                'status': job_status,
                'attempt': payload.get('attempt'),
                'timeout_seconds': payload.get('timeout_seconds'),
                'duration_seconds': payload.get('duration_seconds'),
                'exit_code': payload.get('exit_code'),
                'error_code': payload.get('error_code') or '',
                'output_truncated': bool(payload.get('output_truncated')),
                'result': payload.get('result') or {},
                'error_message': payload.get('error_message') or '',
            },
        )
        return Response(
            {
                'status': 'ok',
                'machine_id': machine.machine_id or str(machine.id),
                'job_id': str(job_id),
                'result_id': result_id,
                'job_status': job.status if job else job_status,
                'updated': bool(job),
                'finished_at': job.finished_at.isoformat() if job and job.finished_at else None,
            },
            status=status.HTTP_200_OK,
        )


class AgentEnrollView(APIView):
    authentication_classes = []
    permission_classes = []

    def _log_enrollment(self, request, payload, enrollment_token=None, endpoint=None, status_value='error', message='', metadata=None):
        return AgentEnrollmentLog.objects.create(
            enrollment_token=enrollment_token,
            endpoint=endpoint,
            hostname=(payload or {}).get('hostname', ''),
            domain=(payload or {}).get('domain', ''),
            serial_number=(payload or {}).get('serial_number', ''),
            ip_address=get_client_ip(request),
            status=status_value,
            message=message,
            metadata=metadata or {},
        )

    def _error(self, request, payload, status_value, error_code, detail, http_status, enrollment_token=None, endpoint=None, metadata=None):
        self._log_enrollment(
            request=request,
            payload=payload,
            enrollment_token=enrollment_token,
            endpoint=endpoint,
            status_value=status_value,
            message=str(detail),
            metadata=metadata,
        )
        body = {'error': error_code, 'detail': detail}
        if metadata and metadata.get('reason'):
            body['reason'] = metadata['reason']
        return Response(body, status=http_status)

    def _find_machine(self, machine_id, hostname, domain, serial_number):
        if machine_id:
            machine = AgentMachine.objects.filter(machine_id__iexact=machine_id).first()
            if machine:
                return machine, False, 'machine_id'

        machine = AgentMachine.objects.filter(hostname__iexact=hostname, domain__iexact=domain).first()
        if machine:
            return machine, False, 'hostname_domain'

        if serial_number:
            serial_matches = AgentMachine.objects.filter(serial_number__iexact=serial_number)
            if serial_matches.count() == 1:
                return serial_matches.first(), False, 'serial_number'

        return None, True, 'new'

    def _active_enrollment_tokens(self):
        now = timezone.now()
        return AgentEnrollmentToken.objects.select_for_update().filter(is_active=True).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now),
        ).filter(
            Q(max_uses__isnull=True) | Q(used_count__lt=models.F('max_uses')),
        )

    def _auto_enrollment_token_for_domain(self, domain):
        normalized_domain = (domain or '').strip().lower()
        if not normalized_domain:
            return None
        return self._active_enrollment_tokens().filter(allowed_domain__iexact=normalized_domain).order_by('-created_at').first()

    def _validate_enrollment_token(self, request, payload, token_value):
        if not token_value.startswith('enroll_'):
            return None, self._error(
                request,
                payload,
                AgentEnrollmentLog.STATUS_INVALID_TOKEN,
                'invalid_enrollment_token',
                'Enrollment token invalido.',
                status.HTTP_401_UNAUTHORIZED,
            )

        token_hash = hash_enrollment_token(token_value)
        enrollment_token = AgentEnrollmentToken.objects.select_for_update().filter(token_hash=token_hash).first()
        if enrollment_token is None:
            return None, self._error(
                request,
                payload,
                AgentEnrollmentLog.STATUS_INVALID_TOKEN,
                'invalid_enrollment_token',
                'Enrollment token invalido.',
                status.HTTP_401_UNAUTHORIZED,
            )

        token_error = self._validate_enrollment_token_state(request, payload, enrollment_token)
        if token_error:
            return None, token_error
        return enrollment_token, None

    def _validate_enrollment_token_state(self, request, payload, enrollment_token):
        if not enrollment_token.is_active:
            return self._error(
                request,
                payload,
                AgentEnrollmentLog.STATUS_INACTIVE,
                'enrollment_token_inactive',
                'Enrollment token inativo.',
                status.HTTP_403_FORBIDDEN,
                enrollment_token=enrollment_token,
            )

        if enrollment_token.is_expired:
            return self._error(
                request,
                payload,
                AgentEnrollmentLog.STATUS_EXPIRED,
                'enrollment_token_expired',
                'Enrollment token expirado.',
                status.HTTP_403_FORBIDDEN,
                enrollment_token=enrollment_token,
            )

        if enrollment_token.usage_limit_reached:
            return self._error(
                request,
                payload,
                AgentEnrollmentLog.STATUS_USAGE_LIMIT_REACHED,
                'enrollment_token_usage_limit_reached',
                'Enrollment token atingiu o limite de uso.',
                status.HTTP_403_FORBIDDEN,
                enrollment_token=enrollment_token,
            )
        return None

    def _validate_manual_token(self, request, payload, manual_token_value, enrollment_token=None, domain_reason='domain_not_allowed'):
        if not manual_token_value.startswith('manual_'):
            return None, self._error(
                request,
                payload,
                AgentEnrollmentLog.STATUS_INVALID_MANUAL_VALIDATION_TOKEN,
                'invalid_manual_validation_token',
                'Token de validacao manual invalido.',
                status.HTTP_403_FORBIDDEN,
                enrollment_token=enrollment_token,
                metadata={'reason': domain_reason},
            )

        manual_validation_token = AgentManualValidationToken.objects.select_for_update().filter(
            token_hash=hash_manual_validation_token(manual_token_value),
        ).first()
        if manual_validation_token is None:
            return None, self._error(
                request,
                payload,
                AgentEnrollmentLog.STATUS_INVALID_MANUAL_VALIDATION_TOKEN,
                'invalid_manual_validation_token',
                'Token de validacao manual invalido.',
                status.HTTP_403_FORBIDDEN,
                enrollment_token=enrollment_token,
                metadata={'reason': domain_reason},
            )

        if enrollment_token and manual_validation_token.enrollment_token_id and manual_validation_token.enrollment_token_id != enrollment_token.id:
            return None, self._error(
                request,
                payload,
                AgentEnrollmentLog.STATUS_INVALID_MANUAL_VALIDATION_TOKEN,
                'invalid_manual_validation_token',
                'Token de validacao manual nao pertence a este enrollment token.',
                status.HTTP_403_FORBIDDEN,
                enrollment_token=enrollment_token,
                metadata={'reason': domain_reason, 'manual_validation_token_prefix': manual_validation_token.prefix},
            )

        if not enrollment_token and manual_validation_token.enrollment_token_id:
            enrollment_token = manual_validation_token.enrollment_token
            token_error = self._validate_enrollment_token_state(request, payload, enrollment_token)
            if token_error:
                return None, token_error

        if not manual_validation_token.is_active:
            return None, self._error(
                request,
                payload,
                AgentEnrollmentLog.STATUS_INVALID_MANUAL_VALIDATION_TOKEN,
                'invalid_manual_validation_token',
                'Token de validacao manual inativo.',
                status.HTTP_403_FORBIDDEN,
                enrollment_token=enrollment_token,
                metadata={'reason': domain_reason, 'manual_validation_token_prefix': manual_validation_token.prefix},
            )

        if manual_validation_token.is_expired:
            return None, self._error(
                request,
                payload,
                AgentEnrollmentLog.STATUS_MANUAL_VALIDATION_TOKEN_EXPIRED,
                'manual_validation_token_expired',
                'Token de validacao manual expirado.',
                status.HTTP_403_FORBIDDEN,
                enrollment_token=enrollment_token,
                metadata={'reason': domain_reason, 'manual_validation_token_prefix': manual_validation_token.prefix},
            )

        if manual_validation_token.is_used:
            return None, self._error(
                request,
                payload,
                AgentEnrollmentLog.STATUS_MANUAL_VALIDATION_TOKEN_USED,
                'manual_validation_token_used',
                'Token de validacao manual ja utilizado.',
                status.HTTP_403_FORBIDDEN,
                enrollment_token=enrollment_token,
                metadata={'reason': domain_reason, 'manual_validation_token_prefix': manual_validation_token.prefix},
            )
        return (manual_validation_token, enrollment_token), None

    @transaction.atomic
    def post(self, request):
        serializer = AgentEnrollmentSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning('Invalid enrollment payload: %s', serializer.errors)
            payload = request.data if isinstance(request.data, dict) else {}
            return self._error(
                request,
                payload,
                AgentEnrollmentLog.STATUS_DENIED,
                'invalid_payload',
                serializer.errors,
                status.HTTP_400_BAD_REQUEST,
            )

        payload = serializer.validated_data
        hostname = payload['hostname'].strip().upper()
        domain = payload.get('domain', '').strip().lower()
        serial_number = payload.get('serial_number', '').strip()
        machine_id = payload.get('machine_id', '').strip()
        token_value = payload.get('enrollment_token', '').strip()
        manual_token_value = payload.get('manual_validation_token', '').strip()
        manual_validation_token = None
        domain_validation = 'not_required'
        enrollment_token = None

        if token_value:
            enrollment_token, token_error = self._validate_enrollment_token(request, payload, token_value)
            if token_error:
                return token_error
        elif manual_token_value:
            manual_result, manual_error = self._validate_manual_token(
                request,
                payload,
                manual_token_value,
                enrollment_token=None,
                domain_reason='manual_validation',
            )
            if manual_error:
                return manual_error
            manual_validation_token, enrollment_token = manual_result
            domain_validation = 'manual'
        else:
            enrollment_token = self._auto_enrollment_token_for_domain(domain)
            if enrollment_token is None:
                return self._error(
                    request,
                    payload,
                    AgentEnrollmentLog.STATUS_MANUAL_VALIDATION_REQUIRED,
                    'manual_validation_required',
                    'Esta maquina nao esta no dominio autorizado. Informe um token de validacao manual.',
                    status.HTTP_403_FORBIDDEN,
                    metadata={
                        'reason': 'domain_not_allowed',
                        'reported_domain': domain,
                    },
                )
            domain_validation = 'domain'

        if enrollment_token and enrollment_token.allowed_domain:
            allowed_domain = enrollment_token.allowed_domain.lower()
            domain_reason = 'domain_allowed'
            if domain == allowed_domain:
                domain_validation = 'domain'
            else:
                domain_reason = 'domain_not_allowed'
                if not manual_token_value:
                    return self._error(
                        request,
                        payload,
                        AgentEnrollmentLog.STATUS_MANUAL_VALIDATION_REQUIRED,
                        'manual_validation_required',
                        'Esta maquina nao esta no dominio autorizado. Informe um token de validacao manual.',
                        status.HTTP_403_FORBIDDEN,
                        enrollment_token=enrollment_token,
                        metadata={
                            'reason': domain_reason,
                            'allowed_domain': enrollment_token.allowed_domain,
                            'reported_domain': domain,
                        },
                    )

                manual_result, manual_error = self._validate_manual_token(
                    request,
                    payload,
                    manual_token_value,
                    enrollment_token=enrollment_token,
                    domain_reason=domain_reason,
                )
                if manual_error:
                    return manual_error
                manual_validation_token, enrollment_token = manual_result

                domain_validation = 'manual'

        try:
            machine, should_create, identity_source = self._find_machine(machine_id, hostname, domain, serial_number)
            agent_token = AgentMachine.generate_token()
            now = timezone.now()
            if should_create:
                machine = AgentMachine(
                    machine_id=machine_id,
                    hostname=hostname,
                    domain=domain,
                    fqdn=build_fqdn(hostname, domain),
                    serial_number=serial_number,
                    first_seen_at=now,
                    is_active=True,
                )
                machine.set_agent_token(agent_token)
                created_or_existing = 'created'
            else:
                if machine_id and not machine.machine_id:
                    machine.machine_id = machine_id
                machine.set_agent_token(agent_token)
                created_or_existing = 'existing_rotated'
                if machine_id and machine.machine_id and machine.machine_id != machine_id:
                    create_audit_event(
                        event_type='endpoint.identity_conflict',
                        title='Conflito de identidade no enrollment',
                        description=f'{hostname} tentou enrollment com machine_id diferente do endpoint encontrado.',
                        severity=AuditEvent.SEVERITY_WARNING,
                        actor_type=AuditEvent.ACTOR_AGENT,
                        actor_name='NightOwlAgent installer',
                        endpoint=machine,
                        metadata={
                            'stored_machine_id': machine.machine_id,
                            'reported_machine_id': machine_id,
                            'identity_source': identity_source,
                        },
                        request=request,
                    )

            machine.hostname = hostname
            machine.domain = domain
            machine.fqdn = payload.get('fqdn', '').strip() or build_fqdn(hostname, domain)
            if serial_number:
                machine.serial_number = serial_number
            if payload.get('os_name'):
                machine.os_name = payload.get('os_name', '')
            machine.agent_version = payload.get('agent_version', '')
            machine.agent_mode = payload.get('agent_mode', '')
            machine.agent_install_path = payload.get('install_path', '')
            machine.agent_task_name = payload.get('task_name', '')
            machine.agent_reported_at = now
            if machine.first_seen_at is None:
                machine.first_seen_at = now
            machine.save()

            if enrollment_token is not None:
                enrollment_token.mark_used()
            if manual_validation_token is not None:
                manual_validation_token.mark_used(hostname, domain)
            self._log_enrollment(
                request=request,
                payload=payload,
                enrollment_token=enrollment_token,
                endpoint=machine,
                status_value=AgentEnrollmentLog.STATUS_SUCCESS,
                message='Enrollment realizado com sucesso.',
                metadata={
                    'created_or_existing': created_or_existing,
                    'identity_source': identity_source,
                    'machine_id': machine.machine_id,
                    'domain_validation': domain_validation,
                    'manual_validation_used': manual_validation_token is not None,
                    'manual_validation_token_prefix': manual_validation_token.prefix if manual_validation_token else '',
                    'domain_reason': 'domain_not_allowed' if manual_validation_token else 'domain_allowed',
                },
            )
            create_audit_event(
                event_type='agent.enrolled',
                title='Agente cadastrado via enrollment',
                description=f'{hostname} foi cadastrado pelo fluxo de enrollment.',
                severity=AuditEvent.SEVERITY_INFO,
                actor_type=AuditEvent.ACTOR_AGENT,
                actor_name='RmmAgent installer',
                endpoint=machine,
                metadata={
                    'hostname': hostname,
                    'domain': domain,
                    'created_or_existing': created_or_existing,
                    'identity_source': identity_source,
                    'machine_id': machine.machine_id,
                    'enrollment_token_prefix': enrollment_token.prefix if enrollment_token else '',
                    'domain_validation': domain_validation,
                    'manual_validation_used': manual_validation_token is not None,
                    'manual_validation_token_prefix': manual_validation_token.prefix if manual_validation_token else '',
                },
                request=request,
            )
        except Exception as exc:
            logger.exception('Failed to enroll agent hostname=%s domain=%s', hostname, domain)
            return self._error(
                request,
                payload,
                AgentEnrollmentLog.STATUS_ERROR,
                'enrollment_failed',
                str(exc),
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                enrollment_token=enrollment_token,
            )

        heartbeat_url = request.build_absolute_uri(reverse('agent-heartbeat'))
        server_time = timezone.now()
        return Response(
            {
                'status': 'ok',
                'endpoint_id': str(machine.id),
                'machine_id': machine.machine_id or str(machine.id),
                'agent_token': agent_token,
                'heartbeat_url': heartbeat_url,
                'server_time': server_time.isoformat(),
                'config': {
                    'heartbeat_seconds': 300,
                    'jobs_seconds': 10,
                    'collect_seconds': 3600,
                },
                'recommended_interval_minutes': 15,
            },
            status=status.HTTP_200_OK,
        )
