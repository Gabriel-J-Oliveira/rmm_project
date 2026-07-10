import logging

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
    AgentMachine,
    AgentManualValidationToken,
    AuditEvent,
    hash_enrollment_token,
    hash_manual_validation_token,
)
from .serializers import AgentEnrollmentSerializer, HeartbeatSerializer
from .services import build_fqdn, record_collection, record_heartbeat


logger = logging.getLogger(__name__)


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
                'type': job.job_type,
                'payload': job.payload,
                'created_at': job.created_at.isoformat(),
                'timeout_seconds': job.payload.get('timeout_seconds') or 300,
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
                event_type='job.started',
                title='Job iniciado pelo agente',
                description=f'Job {job.job_type} iniciou execucao em {machine.hostname}.',
                severity=AuditEvent.SEVERITY_INFO,
                actor_type=AuditEvent.ACTOR_AGENT,
                actor_name='NightOwlAgent',
                endpoint=machine,
                metadata={'job_id': str(job.id), 'job_type': job.job_type},
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
        job_status = str(payload.get('status') or 'unknown').strip().lower()
        if job_status == 'dispatched':
            job_status = AgentJob.STATUS_SENT
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
        if job:
            job.status = job_status if job_status in dict(AgentJob.STATUS_CHOICES) else AgentJob.STATUS_FAILED
            job.started_at = _parse_agent_datetime(payload.get('started_at'), job.started_at)
            job.finished_at = _parse_agent_datetime(payload.get('finished_at'), timezone.now())
            job.duration_seconds = payload.get('duration_seconds')
            job.exit_code = payload.get('exit_code')
            job.stdout = payload.get('stdout') or ''
            job.stderr = payload.get('stderr') or ''
            job.result = payload.get('result') or {}
            job.error_message = payload.get('error_message') or ''
            job.save(update_fields=[
                'status',
                'started_at',
                'finished_at',
                'duration_seconds',
                'exit_code',
                'stdout',
                'stderr',
                'result',
                'error_message',
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
        event_type = 'job.completed' if job_status == 'completed' else 'job.failed' if job_status == 'failed' else 'job.result_received'
        severity = AuditEvent.SEVERITY_SUCCESS if job_status == 'completed' else AuditEvent.SEVERITY_WARNING if job_status in {'failed', 'expired'} else AuditEvent.SEVERITY_INFO
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
                'job_type': payload.get('job_type') or '',
                'status': job_status,
                'duration_seconds': payload.get('duration_seconds'),
                'exit_code': payload.get('exit_code'),
                'result': payload.get('result') or {},
                'error_message': payload.get('error_message') or '',
            },
        )
        return Response(
            {
                'status': 'ok',
                'machine_id': machine.machine_id or str(machine.id),
                'job_id': str(job_id),
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
