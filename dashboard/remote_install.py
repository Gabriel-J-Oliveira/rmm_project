"""Single-target remote install runner; credentials exist only in the request and pipe."""

import base64
import json
import subprocess
import sys
import threading
import time
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone

from access_inventory.services.ad_computer_discovery import normalize_fqdn
from agents.models import AgentEnrollmentToken
from dashboard.ad_install_discovery import _machine_maps, _match_machine
from dashboard.models import RemoteInstallJob
from dashboard.remote_install_preflight import (
    ProbeFailure, _ad_target, _new_winrm_protocol, run_remote_install_preflight,
)


TERMINAL = {'COMPLETED', 'INSTALLED_UNVERIFIED', 'OUTCOME_UNKNOWN', 'FAILED', 'INTERRUPTED'}
INSTALL_STARTED_STAGES = {'INSTALLING', 'VALIDATING_SERVICE', 'WAITING_ENROLLMENT', 'WAITING_HEARTBEAT'}
STALE_AFTER = timedelta(seconds=45)
HEARTBEAT_INTERVAL = 5
INSTALL_TIMEOUT = settings.NIGHTOWL_REMOTE_INSTALL_TIMEOUT_SECONDS
ENROLLMENT_TIMEOUT = settings.NIGHTOWL_REMOTE_ENROLLMENT_TIMEOUT_SECONDS
FIRST_HEARTBEAT_TIMEOUT = settings.NIGHTOWL_REMOTE_FIRST_HEARTBEAT_TIMEOUT_SECONDS
UNKNOWN_SLOT_HOLD = timedelta(seconds=INSTALL_TIMEOUT + ENROLLMENT_TIMEOUT + FIRST_HEARTBEAT_TIMEOUT + 300)


class InstallFailure(Exception):
    def __init__(self, code, *, installed=False):
        self.code = code
        self.installed = installed
        super().__init__(code)


def _update(job_id, stage, *, status='RUNNING', error_code='', endpoint=None):
    fields = {'status': status, 'stage': stage, 'error_code': error_code,
              'runner_heartbeat_at': timezone.now(), 'updated_at': timezone.now()}
    if endpoint is not None:
        fields['endpoint'] = endpoint
    if status in TERMINAL:
        fields['active_slot'] = None
        fields['finished_at'] = timezone.now()
    RemoteInstallJob.objects.filter(pk=job_id, active_slot='global').update(**fields)


def reconcile_stale_jobs():
    cutoff = timezone.now() - STALE_AFTER
    stale = RemoteInstallJob.objects.filter(active_slot='global').filter(
        Q(runner_heartbeat_at__lt=cutoff) |
        Q(runner_heartbeat_at__isnull=True, created_at__lt=cutoff))
    stale.filter(status='OUTCOME_UNKNOWN', created_at__lt=timezone.now() - UNKNOWN_SLOT_HOLD).update(
        active_slot=None, updated_at=timezone.now())
    stale.filter(status='RUNNING', stage__in=INSTALL_STARTED_STAGES).update(
        status='OUTCOME_UNKNOWN', stage='OUTCOME_UNKNOWN', error_code='RUNNER_INTERRUPTED_DURING_INSTALL',
        finished_at=timezone.now(), updated_at=timezone.now())
    stale.exclude(status='OUTCOME_UNKNOWN').exclude(stage__in=INSTALL_STARTED_STAGES).update(
        status='INTERRUPTED', stage='INTERRUPTED', error_code='RUNNER_NOT_STARTED',
        active_slot=None, finished_at=timezone.now(), updated_at=timezone.now(),
    )


def create_remote_install_job(fqdn, actor):
    computer = _ad_target(fqdn)
    reconcile_stale_jobs()
    normalized = normalize_fqdn(computer['fqdn'])
    if RemoteInstallJob.objects.filter(target_fqdn=normalized,
                                       status__in=('COMPLETED', 'INSTALLED_UNVERIFIED', 'OUTCOME_UNKNOWN')).exists():
        raise InstallFailure('INSTALL_ALREADY_ATTEMPTED')
    try:
        with transaction.atomic():
            return RemoteInstallJob.objects.create(
                target_hostname=computer['hostname'], target_fqdn=normalized,
                target_ad_dn=computer['distinguished_name'], requested_by=actor,
                active_slot='global', runner_heartbeat_at=timezone.now(),
            )
    except IntegrityError:
        raise InstallFailure('INSTALL_ALREADY_RUNNING') from None


def start_remote_install(job, username, password):
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, '-m', 'dashboard.remote_install_runner', str(job.pk)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, start_new_session=True, cwd=settings.BASE_DIR,
        )
        payload = json.dumps({'username': username, 'password': password}).encode('utf-8')
        process.stdin.write(payload)
        process.stdin.close()
    except Exception:
        _update(job.pk, 'INTERRUPTED', status='INTERRUPTED', error_code='RUNNER_START_FAILED')
        if process and process.stdin and not process.stdin.closed:
            process.stdin.close()
        raise InstallFailure('RUNNER_START_FAILED') from None


def _heartbeat(job_id, stop):
    close_old_connections()
    try:
        while not stop.wait(HEARTBEAT_INTERVAL):
            if not RemoteInstallJob.objects.filter(pk=job_id, active_slot='global').update(
                runner_heartbeat_at=timezone.now(), updated_at=timezone.now(),
            ):
                break
    finally:
        close_old_connections()


def _trusted_urls():
    base = str(settings.NIGHTOWL_AGENT_PUBLIC_SERVER_URL).rstrip('/')
    installer = str(settings.NIGHTOWL_AGENT_INSTALLER_URL)
    parsed_base, parsed_installer = urlsplit(base), urlsplit(installer)
    for parsed in (parsed_base, parsed_installer):
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or "'" in parsed.geturl()):
            raise InstallFailure('INSTALLER_URL_INVALID')
    if parsed_base.netloc != parsed_installer.netloc or not parsed_installer.path.endswith('/Install-NightOwlAgentDotNet.ps1'):
        raise InstallFailure('INSTALLER_URL_INVALID')
    return base, installer


def _remote_script(fqdn, username, password, script, timeout):
    from winrm.exceptions import AuthenticationError, WinRMOperationTimeoutError, WinRMTransportError

    try:
        protocol = _new_winrm_protocol(fqdn, username, password)
    except ProbeFailure as exc:
        raise InstallFailure(exc.code) from None
    except Exception:
        raise InstallFailure('WINRM_UNAVAILABLE') from None
    shell_id = command_id = None
    try:
        shell_id = protocol.open_shell()
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        command_id = protocol.run_command(shell_id, 'powershell.exe',
                                          ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                _stdout, _stderr, exit_code, done = protocol.get_command_output_raw(shell_id, command_id)
            except WinRMOperationTimeoutError:
                continue
            if done:
                return exit_code
        raise InstallFailure('REMOTE_COMMAND_TIMEOUT')
    except InstallFailure:
        raise
    except ProbeFailure as exc:
        raise InstallFailure(exc.code) from None
    except AuthenticationError:
        raise InstallFailure('AUTHENTICATION_FAILED') from None
    except WinRMTransportError as exc:
        raise InstallFailure('AUTHENTICATION_FAILED' if exc.code in (401, 403) else 'WINRM_UNAVAILABLE') from None
    except Exception:
        raise InstallFailure('WINRM_UNAVAILABLE') from None
    finally:
        if shell_id:
            if command_id:
                try:
                    protocol.cleanup_command(shell_id, command_id)
                except Exception:
                    pass
            try:
                protocol.close_shell(shell_id)
            except Exception:
                pass


def _installer_script():
    base, installer = _trusted_urls()
    return f"""
$ErrorActionPreference = 'Stop'
if ((Get-Service -Name 'NightOwlAgentDotNet' -ErrorAction SilentlyContinue) -or
    (Test-Path -LiteralPath (Join-Path $env:ProgramData 'NightOwl'))) {{ exit 25 }}
$dir = Join-Path $env:TEMP ('NightOwlRemoteInstall-' + [guid]::NewGuid().ToString('N'))
try {{
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $script = Join-Path $dir 'Install-NightOwlAgentDotNet.ps1'
    Invoke-WebRequest -Uri '{installer}' -OutFile $script -UseBasicParsing
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $script -ServerUrl '{base}' -InstallAsService -RunCheck -NoGui -NonInteractive
    if ($LASTEXITCODE -ne 0) {{ exit 1 }}
}} finally {{ Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue }}
"""


def _enrollment_available(fqdn):
    from django.db.models import F, Q

    domain = fqdn.split('.', 1)[1]
    return AgentEnrollmentToken.objects.filter(is_active=True, allowed_domain__iexact=domain).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()),
    ).filter(Q(max_uses__isnull=True) | Q(used_count__lt=F('max_uses'))).exists()


def _matching_endpoint(computer, *, installed=False):
    machine, correlation, _method = _match_machine(computer, _machine_maps())
    if correlation == 'CONFLICT':
        raise InstallFailure('ENDPOINT_CONFLICT', installed=installed)
    return machine


def run_remote_install(job_id, username, password):
    stop = threading.Event()
    pulse = threading.Thread(target=_heartbeat, args=(job_id, stop), daemon=True)
    pulse.start()
    installed = False
    install_started = False
    try:
        job = RemoteInstallJob.objects.get(pk=job_id, active_slot='global')
        RemoteInstallJob.objects.filter(pk=job_id).update(started_at=timezone.now())
        _update(job_id, 'VALIDATING_TARGET')
        try:
            computer = _ad_target(job.target_fqdn)
        except ProbeFailure as exc:
            raise InstallFailure(exc.code) from None
        if computer['distinguished_name'] != job.target_ad_dn:
            raise InstallFailure('TARGET_CHANGED')
        _update(job_id, 'CONNECTING')
        preflight = run_remote_install_preflight(job.target_fqdn, username, password)
        if preflight['status'] != 'READY':
            failed = next((item for item in preflight['checks'].values() if item['status'] == 'FAIL'), {})
            raise InstallFailure(failed.get('code', 'PREFLIGHT_FAILED'))
        try:
            computer = _ad_target(job.target_fqdn)
        except ProbeFailure as exc:
            raise InstallFailure(exc.code) from None
        if computer['distinguished_name'] != job.target_ad_dn:
            raise InstallFailure('TARGET_CHANGED')
        _update(job_id, 'AUTHENTICATED')
        _update(job_id, 'PREFLIGHT_OK')
        if not _enrollment_available(job.target_fqdn):
            raise InstallFailure('ENROLLMENT_UNAVAILABLE')
        _update(job_id, 'PREPARING_ENROLLMENT')
        if _matching_endpoint(computer):
            raise InstallFailure('ALREADY_MANAGED')
        installer_script = _installer_script()
        _update(job_id, 'INSTALLING')
        install_started = True
        code = _remote_script(job.target_fqdn, username, password, installer_script, INSTALL_TIMEOUT)
        if code == 25:
            raise InstallFailure('NIGHTOWL_INSTALLATION_DETECTED')
        if code != 0:
            raise InstallFailure('INSTALLER_FAILED')
        installed = True
        _update(job_id, 'VALIDATING_SERVICE')
        service_check = "if ((Get-Service -Name 'NightOwlAgentDotNet' -ErrorAction SilentlyContinue).Status -eq 'Running') { exit 0 } else { exit 1 }"
        if _remote_script(job.target_fqdn, username, password, service_check, 30) != 0:
            raise InstallFailure('SERVICE_NOT_RUNNING', installed=True)
        _update(job_id, 'WAITING_ENROLLMENT')
        deadline = time.monotonic() + ENROLLMENT_TIMEOUT
        machine = None
        while time.monotonic() < deadline:
            machine = _matching_endpoint(computer, installed=True)
            if machine:
                break
            time.sleep(3)
        if not machine:
            raise InstallFailure('ENROLLMENT_TIMEOUT', installed=True)
        _update(job_id, 'WAITING_HEARTBEAT', endpoint=machine)
        deadline = time.monotonic() + FIRST_HEARTBEAT_TIMEOUT
        while time.monotonic() < deadline:
            machine = _matching_endpoint(computer, installed=True)
            if machine and machine.last_seen_at and machine.last_seen_at >= job.created_at and machine.status == 'online':
                _update(job_id, 'COMPLETED', status='COMPLETED', endpoint=machine)
                return
            time.sleep(3)
        raise InstallFailure('HEARTBEAT_TIMEOUT', installed=True)
    except InstallFailure as exc:
        state = ('INSTALLED_UNVERIFIED' if exc.installed or installed else
                 'OUTCOME_UNKNOWN' if install_started else 'FAILED')
        _update(job_id, state, status=state, error_code=exc.code)
    except Exception:
        state = 'OUTCOME_UNKNOWN' if install_started else 'INTERRUPTED'
        _update(job_id, state, status=state, error_code='RUNNER_INTERRUPTED')
    finally:
        stop.set()
        pulse.join(timeout=2)
        close_old_connections()
