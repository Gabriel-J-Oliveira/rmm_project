"""Allowlisted diagnostics shared by admission, preflight and the task drawer."""
import ipaddress
import logging
import uuid
from functools import wraps

from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from agents.models import AuditEvent


logger = logging.getLogger(__name__)
MESSAGES = {
    'TCP_5986_UNAVAILABLE': 'Nao foi possivel conectar a porta TCP 5986 do computador.',
    'TCP_5986_TIMEOUT': 'A conexao TCP 5986 excedeu o tempo limite.',
    'TCP_5986_REFUSED': 'O computador recusou a conexao TCP 5986.',
    'TLS_VALIDATION_FAILED': 'O certificado TLS nao passou na validacao de confianca, nome ou validade.',
    'TLS_HANDSHAKE_FAILED': 'Nao foi possivel concluir a negociacao TLS segura.',
    'WINRM_REQUEST_TIMEOUT': 'A requisicao WinRM excedeu o tempo limite.',
    'WINRM_CONNECTION_FAILED': 'A conexao HTTPS com o WinRM foi interrompida.',
    'WINRM_AUTHORIZATION_DENIED': 'O WinRM recusou a autorizacao para a operacao solicitada.',
    'WINRM_HTTP_ERROR': 'O endpoint WinRM retornou uma falha HTTP.',
    'WINRM_PROTOCOL_ERROR': 'O protocolo WinRM falhou; consulte a etapa e o tipo da excecao.',
    'PREFLIGHT_INTERNAL_ERROR': 'O preflight nao pode ser concluido; consulte o diagnostico sanitizado.',
}
STAGES = {'ADMISSION', 'TARGET', 'DNS', 'CA_TRUST', 'TCP_5986', 'WINRM_SESSION',
          'REMOTE_EXECUTION', 'REMOTE_INPUT', 'REMOTE_OUTPUT', 'AUTHENTICATION', 'ADMIN_PRIVILEGE',
          'WINDOWS_COMPATIBILITY', 'NIGHTOWL_ABSENCE', 'PREFLIGHT', 'REMOTE_TRANSPORT'}
STAGES |= {'VALIDATING_TARGET', 'VALIDATING_INSTALLER', 'CONNECTING', 'AUTHENTICATED',
           'PREFLIGHT_OK', 'PREPARING_ENROLLMENT', 'INSTALLING', 'INSTALLER_STARTED',
           'INSTALLER_FINISHED', 'VALIDATING_SERVICE', 'WAITING_ENROLLMENT', 'WAITING_HEARTBEAT'}
EXCEPTION_TYPES = {'Exception', 'OSError', 'PermissionError', 'TimeoutError', 'ConnectionRefusedError',
    'ConnectionError', 'SSLError', 'SSLCertVerificationError', 'ReadTimeout', 'ConnectTimeout', 'Timeout',
    'AuthenticationError', 'WinRMTransportError', 'WinRMOperationTimeoutError', 'WSManFaultError',
    'WinRMError', 'ValueError', 'TypeError', 'RuntimeError', 'ProbeFailure', 'InstallFailure', 'gaierror'}


def exception_type(exc):
    name = type(exc).__name__
    return name if name in EXCEPTION_TYPES else 'Exception'


def safe_code(value):
    from dashboard.remote_install_batch import SAFE_CODES
    return value if isinstance(value, str) and value in SAFE_CODES else 'PREFLIGHT_FAILED'


def sanitize_report(data):
    """Never project arbitrary JSON, exception text, credentials or remote output."""
    from dashboard.remote_install_preflight import CHECKS, _valid_fqdn
    data = data if isinstance(data, dict) else {}
    report = {}
    for key in ('correlation_id', 'batch_id', 'job_id'):
        try:
            report[key] = str(uuid.UUID(str(data.get(key))))
        except (ValueError, TypeError, AttributeError):
            report[key] = None
    fqdn = _valid_fqdn(data.get('fqdn'))
    requested = _valid_fqdn(data.get('requested_fqdn'))
    report.update(fqdn=fqdn, requested_fqdn=requested, hostname=(requested or fqdn).split('.')[0])
    try:
        report['resolved_ip'] = str(ipaddress.ip_address(data.get('resolved_ip')))
    except (ValueError, TypeError):
        report['resolved_ip'] = None
    for key in ('started_at', 'finished_at'):
        try:
            from datetime import datetime
            stamp = datetime.fromisoformat(data.get(key, ''))
            report[key] = stamp.isoformat() if stamp.tzinfo else None
        except (ValueError, TypeError):
            report[key] = None
    report['stage'] = data.get('stage') if isinstance(data.get('stage'), str) and data['stage'] in STAGES else 'PREFLIGHT'
    report['exception_type'] = data.get('exception_type') if isinstance(data.get('exception_type'), str) and data['exception_type'] in EXCEPTION_TYPES else ''
    report['result'] = data.get('result') if data.get('result') in ('STARTED', 'READY', 'NOT_READY', 'QUEUED') else 'NOT_READY'
    report['phase'] = data.get('phase') if data.get('phase') in ('admission', 'preflight', 'execution') else 'preflight'
    report['code'] = safe_code(data['code']) if data.get('code') else ''
    report['checks'] = {}
    checks = data.get('checks', {})
    if isinstance(checks, dict):
        for name in CHECKS:
            check = checks.get(name)
            if isinstance(check, dict):
                report['checks'][name] = {
                    'status': check.get('status') if check.get('status') in ('PASS', 'FAIL', 'SKIPPED') else 'SKIPPED',
                    'code': safe_code(check['code']) if check.get('code') else '',
                }
    return report


class InstallAttempt:
    def __init__(self, fqdn, actor=None, *, correlation_id=None, batch_id=None, job_id=None, phase='preflight'):
        self.actor = actor
        self.phase = phase
        self.data = dict(requested_fqdn=fqdn, fqdn=fqdn, correlation_id=str(correlation_id or uuid.uuid4()),
                         batch_id=batch_id, job_id=job_id, started_at=timezone.now().isoformat(),
                         stage='ADMISSION' if phase == 'admission' else 'TARGET', result='STARTED', phase=phase)

    def record(self, ending):
        report = sanitize_report(self.data)
        logger.info('remote_install.attempt correlation_id=%s fqdn=%s stage=%s result=%s code=%s exception_type=%s',
                    report['correlation_id'], report['fqdn'], report['stage'], report['result'],
                    report['code'], report['exception_type'])
        try:
            AuditEvent.objects.create(event_type='remote_install.' + self.phase + ('.finished' if ending else '.started'),
                title='Instalacao remota: ' + (report['hostname'] or 'alvo invalido'),
                severity='warning' if report['result'] == 'NOT_READY' else 'info',
                actor_type='user' if self.actor else 'system',
                actor_name=self.actor.get_username() if self.actor else 'NightOwl', metadata=report)
        except Exception as exc:
            # Audit storage errors must not leak SQL, connection strings or credentials.
            logger.error('remote_install.audit_unavailable correlation_id=%s exception_type=%s',
                         report['correlation_id'], exception_type(exc))
        return report

    def __enter__(self):
        self.record(False)
        return self

    def complete(self, result):
        checks = result.get('checks', {})
        failed = next(((name, check) for name, check in checks.items()
                       if isinstance(check, dict) and check.get('status') == 'FAIL'), ('PREFLIGHT', {}))
        target = result.get('target') or {}
        self.data.update(result=result.get('status'), checks=checks, fqdn=target.get('fqdn', self.data['fqdn']),
                         resolved_ip=target.get('ip'), code=failed[1].get('code', ''),
                         stage=failed[1].get('stage', failed[0]),
                         exception_type=failed[1].get('exception_type', ''))

    def __exit__(self, kind, exc, tb):
        if exc is not None:
            self.data.update(result='NOT_READY', code=getattr(exc, 'code', 'PREFLIGHT_INTERNAL_ERROR'),
                             stage=getattr(exc, 'stage', '') or self.data['stage'],
                             exception_type=getattr(exc, 'exception_type', '') or exception_type(exc))
        self.data['finished_at'] = timezone.now().isoformat()
        self.report = self.record(True)


def audit_admission(*, multiple=False):
    def decorate(function):
        @wraps(function)
        @sensitive_variables('args', 'kwargs')
        def wrapped(targets, actor, *args, **kwargs):
            values = targets[:50] if multiple and isinstance(targets, list) else [targets]
            attempts = [InstallAttempt(value, actor, phase='admission') for value in values]
            for attempt in attempts:
                attempt.__enter__()
            try:
                result = function(targets, actor, *args, **kwargs)
            except Exception as exc:
                for attempt in attempts:
                    attempt.__exit__(type(exc), exc, None)
                raise
            for attempt in attempts:
                attempt.data.update(result='QUEUED', **{'batch_id' if multiple else 'job_id': str(result.pk)})
                attempt.__exit__(None, None, None)
            return result
        return wrapped
    return decorate


@sensitive_variables('username', 'password')
def audited_preflight(probe, fqdn, username, password, *, actor=None, correlation_id=None, batch_id=None, job_id=None):
    with InstallAttempt(fqdn, actor, correlation_id=correlation_id, batch_id=batch_id, job_id=job_id) as attempt:
        result = probe(fqdn, username, password)
        attempt.complete(result)
    return {**result, 'diagnostic': attempt.report}


def record_execution_failure(job, exc):
    attempt = InstallAttempt(job.target_fqdn, job.requested_by, correlation_id=job.pk,
                             job_id=str(job.pk), phase='execution')
    try:
        previous = AuditEvent.objects.filter(event_type='remote_install.preflight.finished',
            metadata__correlation_id=str(job.pk)).order_by('-created_at').first()
    except Exception:
        previous = None
    if previous:
        report = sanitize_report(previous.metadata)
        attempt.data.update(resolved_ip=report['resolved_ip'], checks=report['checks'])
    attempt.data['started_at'] = (job.started_at or job.created_at).isoformat()
    attempt.__exit__(type(exc), exc, None)
