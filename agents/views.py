import logging

from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .audit import create_audit_event, get_client_ip
from .authentication import authenticate_agent_token
from .models import (
    AgentEnrollmentLog,
    AgentEnrollmentToken,
    AgentMachine,
    AgentManualValidationToken,
    AuditEvent,
    hash_enrollment_token,
    hash_manual_validation_token,
)
from .serializers import AgentEnrollmentSerializer, HeartbeatSerializer
from .services import build_fqdn, record_heartbeat


logger = logging.getLogger(__name__)


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
                'machine_id': str(machine.id),
                'snapshot_id': str(snapshot.id),
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

    def _find_machine(self, hostname, domain, serial_number):
        machine = AgentMachine.objects.filter(hostname__iexact=hostname, domain__iexact=domain).first()
        if machine:
            return machine, False

        if serial_number:
            serial_matches = AgentMachine.objects.filter(serial_number__iexact=serial_number)
            if serial_matches.count() == 1:
                return serial_matches.first(), False

        return None, True

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
        token_value = payload['enrollment_token'].strip()
        manual_token_value = payload.get('manual_validation_token', '').strip()
        manual_validation_token = None
        domain_validation = 'not_required'

        if not token_value.startswith('enroll_'):
            return self._error(
                request,
                payload,
                AgentEnrollmentLog.STATUS_INVALID_TOKEN,
                'invalid_enrollment_token',
                'Enrollment token invalido.',
                status.HTTP_401_UNAUTHORIZED,
            )

        token_hash = hash_enrollment_token(token_value)
        enrollment_token = AgentEnrollmentToken.objects.filter(token_hash=token_hash).first()
        if enrollment_token is None:
            return self._error(
                request,
                payload,
                AgentEnrollmentLog.STATUS_INVALID_TOKEN,
                'invalid_enrollment_token',
                'Enrollment token invalido.',
                status.HTTP_401_UNAUTHORIZED,
            )

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

        if enrollment_token.allowed_domain:
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

                if not manual_token_value.startswith('manual_'):
                    return self._error(
                        request,
                        payload,
                        AgentEnrollmentLog.STATUS_INVALID_MANUAL_VALIDATION_TOKEN,
                        'invalid_manual_validation_token',
                        'Token de validacao manual invalido.',
                        status.HTTP_403_FORBIDDEN,
                        enrollment_token=enrollment_token,
                        metadata={'reason': domain_reason},
                    )

                manual_validation_token = AgentManualValidationToken.objects.filter(
                    token_hash=hash_manual_validation_token(manual_token_value),
                ).select_for_update().first()
                if manual_validation_token is None:
                    return self._error(
                        request,
                        payload,
                        AgentEnrollmentLog.STATUS_INVALID_MANUAL_VALIDATION_TOKEN,
                        'invalid_manual_validation_token',
                        'Token de validacao manual invalido.',
                        status.HTTP_403_FORBIDDEN,
                        enrollment_token=enrollment_token,
                        metadata={'reason': domain_reason},
                    )

                if manual_validation_token.enrollment_token_id and manual_validation_token.enrollment_token_id != enrollment_token.id:
                    return self._error(
                        request,
                        payload,
                        AgentEnrollmentLog.STATUS_INVALID_MANUAL_VALIDATION_TOKEN,
                        'invalid_manual_validation_token',
                        'Token de validacao manual nao pertence a este enrollment token.',
                        status.HTTP_403_FORBIDDEN,
                        enrollment_token=enrollment_token,
                        metadata={'reason': domain_reason, 'manual_validation_token_prefix': manual_validation_token.prefix},
                    )

                if not manual_validation_token.is_active:
                    return self._error(
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
                    return self._error(
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
                    return self._error(
                        request,
                        payload,
                        AgentEnrollmentLog.STATUS_MANUAL_VALIDATION_TOKEN_USED,
                        'manual_validation_token_used',
                        'Token de validacao manual ja utilizado.',
                        status.HTTP_403_FORBIDDEN,
                        enrollment_token=enrollment_token,
                        metadata={'reason': domain_reason, 'manual_validation_token_prefix': manual_validation_token.prefix},
                    )

                domain_validation = 'manual'

        try:
            machine, should_create = self._find_machine(hostname, domain, serial_number)
            agent_token = AgentMachine.generate_token()
            now = timezone.now()
            if should_create:
                machine = AgentMachine(
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
                machine.set_agent_token(agent_token)
                created_or_existing = 'existing_rotated'

            machine.hostname = hostname
            machine.domain = domain
            machine.fqdn = build_fqdn(hostname, domain)
            if serial_number:
                machine.serial_number = serial_number
            machine.agent_version = payload.get('agent_version', '')
            machine.agent_mode = payload.get('agent_mode', '')
            machine.agent_install_path = payload.get('install_path', '')
            machine.agent_task_name = payload.get('task_name', '')
            machine.agent_reported_at = now
            if machine.first_seen_at is None:
                machine.first_seen_at = now
            machine.save()

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
                    'domain_validation': domain_validation,
                    'manual_validation_used': manual_validation_token is not None,
                    'manual_validation_token_prefix': manual_validation_token.prefix if manual_validation_token else '',
                    'domain_reason': 'domain_not_allowed' if manual_validation_token else 'domain_allowed',
                },
            )
            create_audit_event(
                event_type='agent.enrolled',
                title='Agente cadastrado via enrollment',
                description=f'{hostname} foi cadastrado usando enrollment token.',
                severity=AuditEvent.SEVERITY_INFO,
                actor_type=AuditEvent.ACTOR_AGENT,
                actor_name='RmmAgent installer',
                endpoint=machine,
                metadata={
                    'hostname': hostname,
                    'domain': domain,
                    'created_or_existing': created_or_existing,
                    'enrollment_token_prefix': enrollment_token.prefix,
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
        return Response(
            {
                'status': 'ok',
                'machine_id': str(machine.id),
                'agent_token': agent_token,
                'heartbeat_url': heartbeat_url,
                'recommended_interval_minutes': 15,
            },
            status=status.HTTP_200_OK,
        )
