from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone

from .models import AgentJob


PUBLIC_STATUS_BY_MODEL_STATUS = {
    AgentJob.STATUS_QUEUED: 'queued',
    AgentJob.STATUS_SENT: 'dispatched',
    AgentJob.STATUS_RUNNING: 'running',
    AgentJob.STATUS_COMPLETED: 'completed',
    AgentJob.STATUS_FAILED: 'failed',
    AgentJob.STATUS_EXPIRED: 'timed_out',
    AgentJob.STATUS_CANCELLED: 'cancelled',
    AgentJob.STATUS_TIMED_OUT: 'timed_out',
    AgentJob.STATUS_DUPLICATE: 'completed',
    AgentJob.STATUS_UNSUPPORTED: 'failed',
    AgentJob.STATUS_INVALID_PARAMETERS: 'failed',
    AgentJob.STATUS_INTERRUPTED: 'failed',
    AgentJob.STATUS_ROLLED_BACK: 'failed',
    AgentJob.STATUS_ROLLBACK_FAILED: 'failed',
}

FINAL_MODEL_STATUSES = {
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

UPDATE_PROGRESS_BY_STAGE = {
    'queued': 5,
    'dispatched': 10,
    'runner_started': 20,
    'checking_version': 25,
    'downloading': 35,
    'downloaded': 45,
    'validating': 50,
    'validated': 55,
    'staging': 60,
    'staged': 65,
    'creating_backup': 70,
    'backup_created': 75,
    'stopping_service': 80,
    'service_stopped': 82,
    'replacing_files': 85,
    'files_replaced': 88,
    'starting_service': 90,
    'service_started': 93,
    'restarting': 94,
    'awaiting_reconciliation': 95,
    'waiting_health_check': 96,
    'completed': 100,
    'failed': 100,
    'rolled_back': 100,
    'rollback_failed': 100,
}

SIMPLE_PROGRESS_BY_STATUS = {
    'queued': 10,
    'dispatched': 25,
    'running': 60,
    'completed': 100,
    'failed': 100,
    'cancelled': 100,
    'timed_out': 100,
}

UPDATE_MESSAGES = {
    'queued': 'Aguardando o agente.',
    'dispatched': 'Job entregue ao endpoint.',
    'runner_started': 'Atualizador iniciado.',
    'checking_version': 'Verificando versao disponivel.',
    'downloading': 'Baixando pacote da versao {target_version}.',
    'downloaded': 'Pacote baixado.',
    'validating': 'Validando integridade do pacote.',
    'validated': 'Pacote validado.',
    'staging': 'Preparando arquivos em staging.',
    'staged': 'Staging pronto.',
    'creating_backup': 'Criando backup da versao atual.',
    'backup_created': 'Backup criado.',
    'stopping_service': 'Parando servico do agente.',
    'service_stopped': 'Servico parado.',
    'replacing_files': 'Substituindo arquivos do agente.',
    'files_replaced': 'Arquivos substituidos.',
    'starting_service': 'Iniciando servico do agente.',
    'service_started': 'Servico iniciado.',
    'restarting': 'Agente reiniciando / aguardando confirmacao.',
    'awaiting_reconciliation': 'Agente reiniciando / aguardando confirmacao.',
    'waiting_health_check': 'Aguardando confirmacao de saude do agente.',
    'completed': 'Atualizado para {installed_version}.',
    'failed': 'A atualizacao falhou.',
    'rolled_back': 'Falha detectada; versao anterior restaurada.',
    'rollback_failed': 'Falha na atualizacao e no rollback.',
}

SIMPLE_MESSAGES = {
    'queued': 'Aguardando o agente.',
    'dispatched': 'Job entregue ao endpoint.',
    'running': 'Executando no endpoint.',
    'completed': 'Job concluido.',
    'failed': 'Job falhou.',
    'cancelled': 'Job cancelado.',
    'timed_out': 'Job expirou ou excedeu o tempo limite.',
}

SENSITIVE_KEYS = {'token', 'authorization', 'password', 'secret', 'cookie', 'api_key', 'agent_token', 'enrollment_token'}


def public_job_status(job: AgentJob) -> str:
    return PUBLIC_STATUS_BY_MODEL_STATUS.get(job.status, job.status or 'failed')


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ''


def job_target_version(job: AgentJob) -> str:
    payload = _as_dict(job.payload)
    result = _as_dict(job.result)
    details = _as_dict(result.get('details'))
    return _first_text(
        payload.get('target_version'),
        result.get('target_version'),
        result.get('targetVersion'),
        details.get('target_version'),
        details.get('targetVersion'),
        job.agent_release.version if job.agent_release_id else '',
    )


def job_previous_version(job: AgentJob) -> str:
    result = _as_dict(job.result)
    details = _as_dict(result.get('details'))
    return _first_text(
        result.get('previous_version'),
        result.get('previousVersion'),
        result.get('from_version'),
        result.get('fromVersion'),
        details.get('previous_version'),
        details.get('from_version'),
    )


def job_installed_version(job: AgentJob) -> str:
    result = _as_dict(job.result)
    details = _as_dict(result.get('details'))
    return _first_text(
        result.get('installed_version'),
        result.get('installedVersion'),
        details.get('installed_version'),
        details.get('installedVersion'),
        result.get('version'),
    )


def job_stage(job: AgentJob) -> str:
    result = _as_dict(job.result)
    details = _as_dict(result.get('details'))
    stage = _first_text(
        result.get('stage'),
        result.get('current_stage'),
        result.get('currentStage'),
        result.get('update_status'),
        details.get('stage'),
        details.get('current_stage'),
        details.get('reason'),
    ).lower()
    if stage in {'success', 'already_current', 'no_update_available'} and job.status == AgentJob.STATUS_COMPLETED:
        return 'completed'
    if job.status == AgentJob.STATUS_QUEUED:
        return 'queued'
    if job.status == AgentJob.STATUS_SENT:
        return 'dispatched'
    if job.status in FINAL_MODEL_STATUSES:
        if job.status == AgentJob.STATUS_ROLLED_BACK:
            return 'rolled_back'
        if job.status == AgentJob.STATUS_ROLLBACK_FAILED:
            return 'rollback_failed'
        if job.status == AgentJob.STATUS_COMPLETED:
            return 'completed'
        return 'failed'
    return stage or 'running'


def job_progress_percentage(job: AgentJob) -> int:
    status = public_job_status(job)
    stage = job_stage(job)
    if job.job_type == AgentJob.TYPE_UPDATE_AGENT:
        return UPDATE_PROGRESS_BY_STAGE.get(stage, SIMPLE_PROGRESS_BY_STATUS.get(status, 60))
    return SIMPLE_PROGRESS_BY_STATUS.get(status, 0)


def job_progress_message(job: AgentJob) -> str:
    status = public_job_status(job)
    stage = job_stage(job)
    result = _as_dict(job.result)
    target_version = job_target_version(job) or 'selecionada'
    installed_version = job_installed_version(job) or target_version
    if job.job_type == AgentJob.TYPE_UPDATE_AGENT:
        template = UPDATE_MESSAGES.get(stage) or UPDATE_MESSAGES.get(status) or SIMPLE_MESSAGES.get(status) or 'Atualizacao em andamento.'
        message = template.format(target_version=target_version, installed_version=installed_version)
        if stage == 'failed' and (job.error_message or result.get('message')):
            return _first_text(job.error_message, result.get('message'), message)
        return message
    if job.error_message and status == 'failed':
        return job.error_message
    return SIMPLE_MESSAGES.get(status, 'Job em andamento.')


def job_last_update_at(job: AgentJob):
    return job.result_received_at or job.updated_at or job.started_at or job.dispatched_at or job.created_at


def job_expected_timeout_at(job: AgentJob):
    seconds = job.timeout_seconds or (_as_dict(job.payload).get('timeout_seconds') if isinstance(job.payload, dict) else None)
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        seconds = 0
    if not seconds:
        return None
    anchor = job.started_at or job.dispatched_at or job.queued_at or job.created_at
    return anchor + timedelta(seconds=seconds)


def job_stale_info(job: AgentJob, *, now=None) -> dict:
    now = now or timezone.now()
    if job.status in FINAL_MODEL_STATUSES:
        return {'is_stale': False, 'stale_reason': '', 'stale_since': None, 'expected_timeout_at': job_expected_timeout_at(job)}
    expected_timeout = job_expected_timeout_at(job)
    last_update = job_last_update_at(job)
    stale_since = None
    reason = ''
    if job.status in {AgentJob.STATUS_QUEUED, AgentJob.STATUS_SENT}:
        anchor = job.queued_at if job.status == AgentJob.STATUS_QUEUED else (job.dispatched_at or job.updated_at)
        if anchor and now - anchor > timedelta(minutes=5):
            stale_since = anchor + timedelta(minutes=5)
            reason = 'queued_too_long' if job.status == AgentJob.STATUS_QUEUED else 'dispatched_too_long'
    elif job.job_type == AgentJob.TYPE_UPDATE_AGENT and job_stage(job) == 'waiting_health_check' and last_update and now - last_update > timedelta(minutes=5):
        stale_since = last_update + timedelta(minutes=5)
        reason = 'waiting_health_check_too_long'
    elif job.status == AgentJob.STATUS_RUNNING and last_update and now - last_update > timedelta(minutes=15):
        stale_since = last_update + timedelta(minutes=15)
        reason = 'running_without_update'
    if expected_timeout and now > expected_timeout:
        stale_since = expected_timeout
        reason = 'timeout_exceeded'
    return {
        'is_stale': bool(reason),
        'stale_reason': reason,
        'stale_since': stale_since,
        'expected_timeout_at': expected_timeout,
    }


def sanitize_job_value(value: Any, *, max_string=2000) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            if any(marker in key_text.lower() for marker in SENSITIVE_KEYS):
                sanitized[key_text] = '[REDACTED]'
            else:
                sanitized[key_text] = sanitize_job_value(item, max_string=max_string)
        return sanitized
    if isinstance(value, list):
        return [sanitize_job_value(item, max_string=max_string) for item in value[:100]]
    if isinstance(value, str):
        text = value
        for marker in ('Authorization:', 'Bearer ', 'agent_token=', 'token=', 'password=', 'secret='):
            if marker.lower() in text.lower():
                return '[REDACTED]'
        return text[:max_string]
    return value
