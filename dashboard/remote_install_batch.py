"""Persistent serial installation orchestration. No credentials enter models."""

import json
import ipaddress
import subprocess
import sys
import threading
import time
from collections import Counter

from django.conf import settings
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from dashboard import remote_install as install
from dashboard.models import RemoteInstallBatch, RemoteInstallBatchItem, RemoteInstallJob
from dashboard.remote_install_admission import install_admission
from dashboard.remote_install_preflight import ProbeFailure, _ad_target, _valid_fqdn, run_remote_install_preflight

MAX_TARGETS = 50
MANUAL_STALL_SECONDS = 60
AUTO_STALL_SECONDS = int(install.STALE_AFTER.total_seconds())
ITEM_TERMINAL = {'COMPLETED', 'FAILED', 'REVIEW_REQUIRED', 'SKIPPED'}
PROGRESS = {
    'WAITING': (0, 'Aguardando na fila'),
    'VALIDATING_TARGET': (8, 'Validando computador no Active Directory'),
    'VALIDATING_INSTALLER': (15, 'Validando instalador oficial'),
    'CONNECTING': (25, 'Conectando via WinRM HTTPS'),
    'AUTHENTICATED': (35, 'Credencial validada'),
    'PREFLIGHT_OK': (45, 'Computador pronto para instalacao'),
    'PREPARING_ENROLLMENT': (52, 'Preparando enrollment'),
    'INSTALLING': (60, 'Preparando instalador remoto'),
    'INSTALLER_STARTED': (68, 'Instalador NightOwl iniciado no computador'),
    'INSTALLER_FINISHED': (78, 'Instalador concluido; validando gerenciamento'),
    'VALIDATING_SERVICE': (86, 'Validando servico Windows'),
    'WAITING_ENROLLMENT': (92, 'Aguardando enrollment'),
    'WAITING_HEARTBEAT': (96, 'Aguardando primeiro heartbeat'),
    'COMPLETED': (100, 'NightOwl instalado e gerenciado'),
}
ERRORS = {
    'WINRM_UNAVAILABLE': 'WinRM HTTPS indisponivel; verifique conectividade e listener 5986.',
    'WINRM_REDIRECT_BLOCKED': 'O servidor redirecionou a conexao WinRM; acesso bloqueado por seguranca.',
    'WINRM_CA_TRUST_INVALID': 'O bundle de CAs do WinRM nao pode ser validado pelo NightOwl.',
    'AUTHENTICATION_FAILED': 'A credencial nao foi aceita pelo computador.',
    'ADMIN_REQUIRED': 'A credencial nao possui privilegios administrativos suficientes.',
    'UNSAFE_TARGET_ADDRESS': 'O endereco do computador foi bloqueado pelos controles de seguranca.',
    'INSTALLER_CONTRACT_MISMATCH': 'O instalador publicado nao corresponde ao contrato aprovado.',
    'INSTALL_RELEASE_INVALID': 'A release selecionada pelo servidor nao passou na validacao.',
    'INSTALLER_EXIT_NONZERO': 'O instalador retornou erro; o resultado precisa ser revisado.',
    'INSTALLER_RESULT_UNKNOWN': 'Nao foi possivel comprovar o resultado da instalacao.',
    'INSTALLER_NOT_STARTED': 'Nao foi possivel comprovar o inicio do instalador.',
    'INSTALLER_DOWNLOAD_FAILED': 'O computador nao conseguiu obter o instalador oficial.',
    'NIGHTOWL_INSTALLATION_DETECTED': 'Uma instalacao NightOwl existente foi detectada; reinstalacao bloqueada.',
    'ENROLLMENT_UNAVAILABLE': 'O enrollment nao esta disponivel para esta operacao.',
    'ENROLLMENT_TIMEOUT': 'O enrollment nao foi confirmado dentro da janela de verificacao.',
    'HEARTBEAT_TIMEOUT': 'O primeiro heartbeat nao foi confirmado; revise o resultado antes de repetir.',
    'SERVICE_NOT_FOUND': 'O servico NightOwl nao foi encontrado na verificacao.',
    'SERVICE_NOT_RUNNING': 'O servico NightOwl nao esta em execucao na verificacao.',
    'REMOTE_COMMAND_TIMEOUT': 'A resposta remota excedeu a janela; resultado pode exigir revisao.',
    'TARGET_MANAGED_OR_CONFLICT': 'Computador ja gerenciado ou com correlacao ambigua; instalacao bloqueada.',
    'RUNNER_INTERRUPTED': 'Runner sem heartbeat; revisao necessaria',
    'PREFLIGHT_FAILED': 'Computador nao passou no preflight',
    'TARGET_CHANGED': 'Identidade AD mudou; operacao bloqueada',
    'INSTALL_RECONCILIATION_REQUIRED': 'Requer nova prova de ausencia do NightOwl',
    'INSTALL_RECONCILIATION_FAILED': 'Ausencia do NightOwl nao comprovada',
    'ALREADY_MANAGED': 'Computador ja gerenciado; nao reinstalado',
    'RUNNER_START_FAILED': 'Nao foi possivel iniciar o runner',
}
SAFE_CODES = set(ERRORS) | {
    'INVALID_BATCH_TARGETS', 'DUPLICATE_BATCH_TARGETS', 'CREDENTIAL_REQUIRED',
    'INVALID_REQUEST', 'HTTPS_REQUIRED', 'ACTION_NOT_AVAILABLE', 'INSTALL_ALREADY_RUNNING',
    'BATCH_LEASE_LOST', 'BATCH_NOT_FINISHED', 'NO_RETRY_TARGETS',
    'INVALID_TARGET', 'AD_DISCOVERY_UNAVAILABLE', 'TARGET_NOT_UNIQUE_OR_MISSING',
    'AD_COMPUTER_DISABLED', 'CORRELATION_UNAVAILABLE', 'TARGET_MANAGED_OR_CONFLICT',
    'WINRM_UNAVAILABLE', 'WINRM_REDIRECT_BLOCKED', 'WINRM_CA_TRUST_INVALID',
    'AUTHENTICATION_FAILED', 'ADMIN_REQUIRED', 'UNSAFE_TARGET_ADDRESS',
    'INSTALLER_CONTRACT_MISMATCH', 'INSTALL_RELEASE_INVALID', 'INSTALLER_EXIT_NONZERO',
    'INSTALLER_RESULT_UNKNOWN', 'INSTALLER_NOT_STARTED', 'INSTALLER_DOWNLOAD_FAILED',
    'NIGHTOWL_INSTALLATION_DETECTED', 'ENROLLMENT_UNAVAILABLE', 'ENROLLMENT_TIMEOUT',
    'HEARTBEAT_TIMEOUT', 'SERVICE_NOT_FOUND', 'SERVICE_NOT_RUNNING', 'REMOTE_COMMAND_TIMEOUT',
}


def safe_code(code):
    # Codes are identifiers only, never remote error text.
    return code if isinstance(code, str) and code in SAFE_CODES else 'PREFLIGHT_FAILED'


def _target_name(value):
    if not isinstance(value, str):
        return ''
    name = _valid_fqdn(value)
    try:
        ipaddress.ip_address(name)
        return ''
    except ValueError:
        return name


def stale_seconds(job, now=None):
    return max(0, ((now or timezone.now()) - (job.runner_heartbeat_at or job.created_at)).total_seconds())


def _counts(batch):
    counts = Counter(batch.items.values_list('status', flat=True))
    batch.success_count = counts['COMPLETED']
    batch.failure_count = counts['FAILED']
    batch.review_count = counts['REVIEW_REQUIRED']
    batch.waiting_count = sum(counts[key] for key in ('WAITING', 'PREFLIGHT', 'INSTALLING'))
    batch.save(update_fields=['success_count', 'failure_count', 'review_count', 'waiting_count', 'updated_at'])


@sensitive_variables('username', 'password')
def create_batch(targets, actor, *, username, password, parent=None):
    if (not isinstance(targets, list) or not 1 <= len(targets) <= MAX_TARGETS
            or any(not _target_name(value) for value in targets)):
        raise install.InstallFailure('INVALID_BATCH_TARGETS')
    names = [_valid_fqdn(value) for value in targets]
    if len(set(names)) != len(names):
        raise install.InstallFailure('DUPLICATE_BATCH_TARGETS')
    if (not isinstance(username, str) or not username.strip() or len(username) > 256
            or not isinstance(password, str) or not password or len(password) > 512):
        raise install.InstallFailure('CREDENTIAL_REQUIRED')
    computers = [_ad_target(name) for name in names]
    try:
        with install_admission():
            if RemoteInstallBatch.objects.filter(active_slot='global').exists() or RemoteInstallJob.objects.filter(
                    Q(active_slot='global') | Q(status__in=('QUEUED', 'RUNNING'))).exists():
                raise install.InstallFailure('INSTALL_ALREADY_RUNNING')
            batch = RemoteInstallBatch.objects.create(requested_by=actor, active_slot='global',
                total_count=len(computers), waiting_count=len(computers), parent_batch=parent,
                runner_heartbeat_at=timezone.now())
            RemoteInstallBatchItem.objects.bulk_create([
                RemoteInstallBatchItem(batch=batch, position=index, target_hostname=computer['hostname'],
                    target_fqdn=name, target_ad_dn=computer['distinguished_name'],
                    progress_message=PROGRESS['WAITING'][1])
                for index, (name, computer) in enumerate(zip(names, computers), 1)])
            return batch
    except IntegrityError:
        raise install.InstallFailure('INSTALL_ALREADY_RUNNING') from None


@sensitive_variables('username', 'password', 'payload')
def start_batch(batch, username, password):
    process = None
    try:
        process = subprocess.Popen([sys.executable, '-m', 'dashboard.remote_install_batch_runner', str(batch.pk)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, start_new_session=True, cwd=settings.BASE_DIR)
        payload = json.dumps({'username': username, 'password': password}).encode('utf-8')
        process.stdin.write(payload)
        process.stdin.close()
    except Exception:
        interrupt_batch(batch.pk, 'RUNNER_START_FAILED')
        raise install.InstallFailure('RUNNER_START_FAILED') from None
    finally:
        if process and process.stdin and not process.stdin.closed:
            process.stdin.close()
        password = payload = None


def _finish_item(item, status, code=''):
    item.status = status
    item.error_code = safe_code(code) if code else ''
    item.finished_at = timezone.now()
    if status == 'COMPLETED':
        item.stage = 'COMPLETED'
        item.progress_percentage, item.progress_message = PROGRESS['COMPLETED']
    else:
        item.progress_message = ('Resultado requer revisao; nao repetir sem prova de ausencia'
                                 if status == 'REVIEW_REQUIRED' else 'Operacao encerrada sem instalacao confirmada')
    item.save()


def resolve_stall(batch_id, item_id, *, actor=None, automatic=False):
    with transaction.atomic():
        batch = RemoteInstallBatch.objects.select_for_update().get(pk=batch_id)
        item = RemoteInstallBatchItem.objects.select_for_update().get(pk=item_id, batch=batch)
        job = RemoteInstallJob.objects.select_for_update().filter(pk=item.remote_install_job_id).first()
        reason = 'AUTO_STALL_TIMEOUT' if automatic else 'MANUAL_STALL_RESOLUTION'
        previous = (job.diagnostics or {}).get('batch_stall_resolution') if job else None
        if item.status in ITEM_TERMINAL and previous:
            return item
        threshold = AUTO_STALL_SECONDS if automatic else MANUAL_STALL_SECONDS
        if (batch.status != 'RUNNING' or batch.active_slot != 'global'
                or item.position != batch.current_index or item.status != 'INSTALLING'
                or not job or job.status not in ('QUEUED', 'RUNNING')
                or job.active_slot != 'global' or stale_seconds(job) < threshold):
            raise install.InstallFailure('ACTION_NOT_AVAILABLE')
        safe = install.job_is_retry_safe(job)
        job.status = 'INTERRUPTED' if safe else 'OUTCOME_UNKNOWN'
        job.error_code = 'RUNNER_INTERRUPTED'
        job.finished_at = timezone.now()
        job.active_slot = None
        job.diagnostics = {**job.diagnostics, 'safe_to_retry': 'YES' if safe else 'NO',
            'outcome': 'INTERRUPTED' if safe else 'UNKNOWN',
            'batch_stall_resolution': {'reason': reason, 'at': timezone.now().isoformat(),
                                       'actor_user_id': actor.pk if actor else None}}
        job.save()
        _finish_item(item, 'FAILED' if safe else 'REVIEW_REQUIRED', job.error_code)
        _counts(batch)
        return item


def _reflect_job(batch_id, item_id):
    with transaction.atomic():
        batch = RemoteInstallBatch.objects.select_for_update().get(pk=batch_id)
        item = RemoteInstallBatchItem.objects.select_for_update().get(pk=item_id, batch=batch)
        if batch.status != 'RUNNING' or batch.active_slot != 'global':
            return True
        if item.status in ITEM_TERMINAL:
            return True
        job = RemoteInstallJob.objects.select_for_update().get(pk=item.remote_install_job_id)
        item.stage = job.stage if job.stage in PROGRESS else item.stage
        item.progress_percentage, item.progress_message = PROGRESS.get(item.stage, PROGRESS['WAITING'])
        item.save(update_fields=['stage', 'progress_percentage', 'progress_message', 'updated_at'])
        if job.status in install.TERMINAL:
            status = ('COMPLETED' if job.status == 'COMPLETED' else
                      'FAILED' if job.status in ('FAILED', 'INTERRUPTED') and install.job_is_retry_safe(job)
                      else 'REVIEW_REQUIRED')
            # Terminal ambiguity releases only global admission, not this target's history blocker.
            if job.active_slot:
                job.active_slot = None
                job.diagnostics = {**job.diagnostics, 'batch_slot_release': {
                    'batch_id': str(batch.pk), 'at': timezone.now().isoformat(), 'reason': status}}
                job.save(update_fields=['active_slot', 'diagnostics'])
            _finish_item(item, status, job.error_code)
            _counts(batch)
            return True
    if stale_seconds(job) >= AUTO_STALL_SECONDS:
        try:
            resolve_stall(batch_id, item_id, automatic=True)
        except install.InstallFailure as exc:
            if exc.code != 'ACTION_NOT_AVAILABLE':
                raise
    return False


def interrupt_batch(batch_id, code='RUNNER_INTERRUPTED'):
    with transaction.atomic():
        batch = RemoteInstallBatch.objects.select_for_update().get(pk=batch_id)
        if batch.active_slot != 'global':
            return
        # A batch crash must never orphan a possible invocation as retry-safe.
        for item in batch.items.select_for_update().exclude(status__in=ITEM_TERMINAL):
            if item.remote_install_job_id:
                job = RemoteInstallJob.objects.select_for_update().get(pk=item.remote_install_job_id)
                if job.status not in install.TERMINAL:
                    safe = install.job_is_retry_safe(job)
                    job.status = 'INTERRUPTED' if safe else 'OUTCOME_UNKNOWN'
                    job.diagnostics = {**job.diagnostics, 'safe_to_retry': 'YES' if safe else 'NO',
                        'batch_interruption': {'at': timezone.now().isoformat(), 'reason': safe_code(code)}}
                    job.finished_at = timezone.now()
                job.active_slot = None
                job.save()
                status = ('COMPLETED' if job.status == 'COMPLETED' else
                          'FAILED' if job.status in ('FAILED', 'INTERRUPTED') and install.job_is_retry_safe(job)
                          else 'REVIEW_REQUIRED')
                _finish_item(item, status, '' if status == 'COMPLETED' else code)
            else:
                _finish_item(item, 'FAILED', code)
        _counts(batch)
        batch.status = 'INTERRUPTED'
        batch.active_slot = None
        batch.finished_at = timezone.now()
        batch.diagnostics = {'error_code': safe_code(code)}
        batch.save()


def _pulse(batch_id, stop):
    close_old_connections()
    try:
        while not stop.wait(install.HEARTBEAT_INTERVAL):
            if not RemoteInstallBatch.objects.filter(pk=batch_id, active_slot='global').update(
                    runner_heartbeat_at=timezone.now()):
                break
    finally:
        close_old_connections()


def _fail_unlinked(batch_id, item_id, code):
    with transaction.atomic():
        current = RemoteInstallBatch.objects.select_for_update().get(pk=batch_id)
        item = RemoteInstallBatchItem.objects.select_for_update().get(pk=item_id)
        if current.status == 'RUNNING' and current.active_slot == 'global' and item.status == 'PREFLIGHT':
            _finish_item(item, 'FAILED', code)
            _counts(current)


@sensitive_variables('username', 'password')
def run_batch(batch_id, username, password):
    stop = threading.Event()
    pulse = threading.Thread(target=_pulse, args=(batch_id, stop), daemon=True)
    # A duplicate child cannot claim a batch already RUNNING.
    if not RemoteInstallBatch.objects.filter(pk=batch_id, status='QUEUED', active_slot='global').update(
            status='RUNNING', started_at=timezone.now(), runner_heartbeat_at=timezone.now()):
        return
    pulse.start()
    try:
        batch = RemoteInstallBatch.objects.select_related('requested_by').get(pk=batch_id)
        for item_id in batch.items.filter(status='WAITING').values_list('pk', flat=True):
            with transaction.atomic():
                current = RemoteInstallBatch.objects.select_for_update().get(pk=batch_id)
                if current.status != 'RUNNING' or current.active_slot != 'global':
                    return
                item = RemoteInstallBatchItem.objects.select_for_update().get(pk=item_id)
                current.current_index = item.position
                current.save(update_fields=['current_index', 'updated_at'])
                item.status = 'PREFLIGHT'
                item.stage = 'VALIDATING_TARGET'
                item.started_at = timezone.now()
                item.progress_percentage, item.progress_message = PROGRESS[item.stage]
                item.save()
            try:
                computer = _ad_target(item.target_fqdn)
                if computer['distinguished_name'] != item.target_ad_dn:
                    raise install.InstallFailure('TARGET_CHANGED')
                result = run_remote_install_preflight(item.target_fqdn, username, password)
                if result.get('status') != 'READY':
                    raise install.InstallFailure('PREFLIGHT_FAILED')
                job = install.create_remote_install_job(item.target_fqdn, batch.requested_by,
                    username=username, password=password, batch_item_id=item.pk)
                install.start_remote_install(job, username, password)
            except (install.InstallFailure, ProbeFailure) as exc:
                item.refresh_from_db()
                if item.remote_install_job_id:
                    _reflect_job(batch_id, item.pk)
                else:
                    _fail_unlinked(batch_id, item.pk, exc.code)
                continue
            except Exception:
                item.refresh_from_db()
                if item.remote_install_job_id:
                    raise
                _fail_unlinked(batch_id, item.pk, 'PREFLIGHT_FAILED')
                continue
            while not _reflect_job(batch_id, item.pk):
                time.sleep(1)
        with transaction.atomic():
            batch = RemoteInstallBatch.objects.select_for_update().get(pk=batch_id)
            if batch.status != 'RUNNING' or batch.active_slot != 'global':
                return
            _counts(batch)
            batch.status = 'COMPLETED_WITH_ERRORS' if batch.failure_count or batch.review_count else 'COMPLETED'
            batch.active_slot = None
            batch.finished_at = timezone.now()
            batch.save()
    except Exception:
        interrupt_batch(batch_id)
    finally:
        password = username = None
        stop.set()
        pulse.join(timeout=2)
        close_old_connections()


@sensitive_variables('username', 'password')
def retry_batch(parent, actor, username, password):
    if parent.status in ('QUEUED', 'RUNNING'):
        raise install.InstallFailure('BATCH_NOT_FINISHED')
    targets = []
    for item in parent.items.select_related('remote_install_job'):
        if item.status not in ('FAILED', 'REVIEW_REQUIRED'):
            continue
        job = item.remote_install_job
        if item.status == 'FAILED' and job and not install.job_is_retry_safe(job):
            continue
        computer = _ad_target(item.target_fqdn)
        if computer['distinguished_name'] != item.target_ad_dn:
            raise install.InstallFailure('TARGET_CHANGED')
        if item.status == 'REVIEW_REQUIRED':
            install._absence_proof(computer, username, password)
        targets.append(item.target_fqdn)
    if not targets:
        raise install.InstallFailure('NO_RETRY_TARGETS')
    # MANAGED/conflict is rejected by _ad_target; prior batch remains immutable.
    return create_batch(targets, actor, username=username, password=password, parent=parent)


def batch_payload(batch, *, detail=False):
    items = list(batch.items.select_related('remote_install_job'))
    data = {'id': str(batch.pk), 'status': batch.status, 'total_count': batch.total_count,
        'success_count': batch.success_count, 'failure_count': batch.failure_count,
        'review_count': batch.review_count, 'waiting_count': batch.waiting_count,
        'current_index': batch.current_index,
        'parent_batch_id': str(batch.parent_batch_id) if batch.parent_batch_id else None,
        'progress_percentage': round(sum(100 if item.status in ITEM_TERMINAL else item.progress_percentage
                                         for item in items) / max(1, batch.total_count)),
        'created_at': batch.created_at, 'started_at': batch.started_at, 'finished_at': batch.finished_at,
        'last_runner_heartbeat_at': batch.runner_heartbeat_at, 'manual_stall_action_available': False}
    projected = []
    for item in items:
        job = item.remote_install_job
        seconds = stale_seconds(job) if job else 0
        active = (batch.status == 'RUNNING' and batch.active_slot == 'global' and
                  item.position == batch.current_index and item.status == 'INSTALLING' and job and
                  job.status in ('QUEUED', 'RUNNING') and job.active_slot == 'global')
        stalled = bool(active and seconds >= MANUAL_STALL_SECONDS)
        data['manual_stall_action_available'] |= stalled
        code = safe_code(item.error_code) if item.error_code else ''
        projected.append({'id': str(item.pk), 'position': item.position, 'hostname': item.target_hostname,
            'fqdn': item.target_fqdn, 'status': item.status, 'stage': item.stage,
            'progress_percentage': item.progress_percentage,
            'progress_message': PROGRESS.get(item.stage, (0, 'Operacao em revisao'))[1]
                                if item.status not in ITEM_TERMINAL else
                                PROGRESS['COMPLETED'][1] if item.status == 'COMPLETED' else 'Operacao encerrada; revisar resultado',
            'error_code': code, 'error_message': ERRORS.get(code, 'Falha segura; revisar o computador') if code else '',
            'job_id': str(item.remote_install_job_id) if job else None,
            'updated_at': item.updated_at, 'started_at': item.started_at, 'finished_at': item.finished_at,
            'is_stalled': stalled, 'stale_seconds': round(seconds),
            'retry_eligible': item.status == 'FAILED' and (job is None or install.job_is_retry_safe(job)),
            'reconciliation_required': item.status == 'REVIEW_REQUIRED'})
    if detail:
        data['items'] = projected
    return data
