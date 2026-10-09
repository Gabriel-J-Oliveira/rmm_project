"""Single-target remote install runner; credentials exist only in the request and pipe."""

import base64
import json
import re
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
from agents.models import AgentDeploymentToken, AgentMachine, InventorySnapshot
from dashboard.ad_install_discovery import _machine_maps, _match_machine
from dashboard.models import RemoteInstallJob
from dashboard.remote_install_admission import install_admission
from dashboard.installer_contract import InstallerContractFailure
from dashboard.remote_install_release import validate_install_release
from dashboard.remote_install_preflight import (
    CHECKS, ProbeFailure, _ad_target, _new_winrm_protocol, run_remote_install_preflight,
)


TERMINAL = {'COMPLETED', 'INSTALLED_UNVERIFIED', 'OUTCOME_UNKNOWN', 'FAILED', 'INTERRUPTED'}
INSTALL_STARTED_STAGES = {'INSTALLING', 'INSTALLER_STARTED', 'INSTALLER_FINISHED', 'VALIDATING_SERVICE', 'WAITING_ENROLLMENT', 'WAITING_HEARTBEAT'}
STAGES = INSTALL_STARTED_STAGES | {'QUEUED', 'VALIDATING_TARGET', 'VALIDATING_INSTALLER',
    'CONNECTING', 'AUTHENTICATED', 'PREFLIGHT_OK', 'PREPARING_ENROLLMENT', 'COMPLETED', 'INTERRUPTED'}
OUTCOMES = {'COMPLETED': 'SUCCESS', 'INSTALLED_UNVERIFIED': 'INSTALLED_UNVERIFIED',
            'OUTCOME_UNKNOWN': 'UNKNOWN', 'FAILED': 'FAILED', 'INTERRUPTED': 'INTERRUPTED'}
STALE_AFTER = timedelta(seconds=120)
HEARTBEAT_INTERVAL = 5
INSTALL_TIMEOUT = settings.NIGHTOWL_REMOTE_INSTALL_TIMEOUT_SECONDS
ENROLLMENT_TIMEOUT = settings.NIGHTOWL_REMOTE_ENROLLMENT_TIMEOUT_SECONDS
FIRST_HEARTBEAT_TIMEOUT = settings.NIGHTOWL_REMOTE_FIRST_HEARTBEAT_TIMEOUT_SECONDS
MAX_REMOTE_SCRIPT_BYTES = 32 * 1024
UNKNOWN_SLOT_HOLD = timedelta(seconds=INSTALL_TIMEOUT + ENROLLMENT_TIMEOUT + FIRST_HEARTBEAT_TIMEOUT + 300)


class InstallFailure(Exception):
    def __init__(self, code, *, installed=False, command_attempted=None):
        self.code = code
        self.installed = installed
        self.command_attempted = command_attempted
        super().__init__(code)


class InstallLeaseLost(Exception):
    """A reconciled runner must not resume dispatch or rewrite terminal history."""


def _initial_diagnostics():
    return {'installer_sha256': '', 'installer_contract_valid': False,
            'installer_started_at': None, 'installer_finished_at': None, 'installer_exit_code': None,
            'service_present': 'UNKNOWN', 'service_state': 'UNKNOWN',
            'service_validation_status': 'NOT_CHECKED', 'safe_to_retry': 'YES'}


def _update(job_id, stage, *, status='RUNNING', error_code='', endpoint=None, diagnostic=None):
    if stage not in STAGES:
        raise ValueError('Invalid install stage')
    if error_code and not re.fullmatch(r'[A-Z_]{1,64}', error_code):
        error_code = 'RUNNER_INTERRUPTED'
    fields = {'status': status, 'stage': stage, 'error_code': error_code,
              'runner_heartbeat_at': timezone.now(), 'updated_at': timezone.now()}
    if endpoint is not None:
        fields['endpoint'] = endpoint
    if status in TERMINAL:
        if status != 'OUTCOME_UNKNOWN':
            fields['active_slot'] = None
        fields['finished_at'] = timezone.now()
    with transaction.atomic():
        job = RemoteInstallJob.objects.select_for_update().filter(pk=job_id, active_slot='global').first()
        if not job:
            raise InstallLeaseLost()
        data = {**_initial_diagnostics(), **job.diagnostics}
        data.update(diagnostic or {})
        data['outcome'] = OUTCOMES.get(status, 'RUNNING')
        data['safe_error_code'] = error_code
        # Only fixed codes, timestamps and factual scalars enter diagnostics.
        data['safe_diagnostic'] = error_code or stage
        data['stage_history'] = [*data.get('stage_history', []),
            {'stage': stage, 'at': timezone.now().isoformat(), 'outcome': data['outcome']}][-32:]
        fields['diagnostics'] = data
        RemoteInstallJob.objects.filter(pk=job.pk).update(**fields)


def reconcile_stale_jobs(*, preserve_unknown_slots=False):
    cutoff = timezone.now() - STALE_AFTER
    stale = RemoteInstallJob.objects.filter(active_slot='global', remoteinstallbatchitem__isnull=True).filter(
        Q(runner_heartbeat_at__lt=cutoff) |
        Q(runner_heartbeat_at__isnull=True, created_at__lt=cutoff))
    for pk in list(stale.exclude(diagnostics={}).values_list('pk', flat=True)):
        with transaction.atomic():
            job = RemoteInstallJob.objects.select_for_update().get(pk=pk)
            last_activity = job.runner_heartbeat_at or job.created_at
            if job.status in ('QUEUED', 'RUNNING') and last_activity < cutoff:
                attempted = not job_is_retry_safe(job)
                _update(pk, job.stage, status='OUTCOME_UNKNOWN' if attempted else 'INTERRUPTED',
                        error_code='RUNNER_INTERRUPTED', diagnostic={'safe_to_retry': 'NO' if attempted else 'YES'})
    # Preserve the legacy reconciliation contract for jobs without new diagnostics.
    stale = stale.filter(diagnostics={})
    if not preserve_unknown_slots:
        stale.filter(status='OUTCOME_UNKNOWN', created_at__lt=timezone.now() - UNKNOWN_SLOT_HOLD).update(
            active_slot=None, updated_at=timezone.now())
    stale.filter(status='RUNNING', stage__in=INSTALL_STARTED_STAGES).update(
        status='OUTCOME_UNKNOWN', stage='OUTCOME_UNKNOWN', error_code='RUNNER_INTERRUPTED_DURING_INSTALL',
        finished_at=timezone.now(), updated_at=timezone.now())
    stale.exclude(status='OUTCOME_UNKNOWN').exclude(stage__in=INSTALL_STARTED_STAGES).update(
        status='INTERRUPTED', stage='INTERRUPTED', error_code='RUNNER_NOT_STARTED',
        active_slot=None, finished_at=timezone.now(), updated_at=timezone.now(),
    )


HISTORICAL_BLOCKERS = ('COMPLETED', 'INSTALLED_UNVERIFIED', 'OUTCOME_UNKNOWN')


def _absence_proof(computer, username, password):
    if not username or not password:
        raise InstallFailure('INSTALL_RECONCILIATION_REQUIRED')
    try:
        proof = run_remote_install_preflight(computer['fqdn'], username, password)
    except Exception:
        raise InstallFailure('INSTALL_RECONCILIATION_FAILED') from None
    if not isinstance(proof, dict):
        raise InstallFailure('INSTALL_RECONCILIATION_FAILED')
    checks = proof.get('checks')
    absence = proof.get('nightowl_absence')
    target = proof.get('target')
    if (proof.get('status') != 'READY' or not isinstance(checks, dict)
            or any(not isinstance(checks.get(key), dict) or checks[key].get('status') != 'PASS'
                   for key in CHECKS)
            or not isinstance(absence, dict)
            or absence.get('service_present') is not False
            or absence.get('directory_present') is not False
            or absence.get('correlation') != 'UNMANAGED'
            or not isinstance(target, dict)
            or normalize_fqdn(target.get('fqdn')) != normalize_fqdn(computer['fqdn'])):
        raise InstallFailure('INSTALL_RECONCILIATION_FAILED')
    # A fresh AD lookup also rechecks current endpoint correlation. Do not
    # let credentials/probe output enter the persisted reconciliation record.
    current = _ad_target(computer['fqdn'])
    if any(current.get(key) != computer.get(key)
           for key in ('fqdn', 'hostname', 'distinguished_name', 'sid')):
        raise InstallFailure('TARGET_CHANGED')


def job_is_retry_safe(job):
    return (job.diagnostics.get('safe_to_retry') == 'YES'
            and not job.diagnostics.get('installer_started_at')
            and not job.diagnostics.get('invocation_attempted')
            and (job.stage not in INSTALL_STARTED_STAGES or
                 (job.status in ('FAILED', 'INTERRUPTED') and
                  job.diagnostics.get('invocation_attempted') is False)))


def create_remote_install_job(fqdn, actor, *, username=None, password=None, batch_item_id=None):
    computer = _ad_target(fqdn)
    reconcile_stale_jobs(preserve_unknown_slots=True)
    normalized = normalize_fqdn(computer['fqdn'])
    active = RemoteInstallJob.objects.filter(Q(active_slot='global') | Q(status__in=('QUEUED', 'RUNNING')))
    if active.exclude(status='OUTCOME_UNKNOWN', target_fqdn=normalized).exists():
        raise InstallFailure('INSTALL_ALREADY_RUNNING')
    historical = RemoteInstallJob.objects.filter(target_fqdn=normalized, status__in=HISTORICAL_BLOCKERS)
    blocker_snapshot = dict(historical.values_list('pk', 'updated_at'))
    if blocker_snapshot:
        _absence_proof(computer, username, password)
    try:
        with install_admission():
            from dashboard.models import RemoteInstallBatch, RemoteInstallBatchItem
            batch = RemoteInstallBatch.objects.select_for_update().filter(active_slot='global').first()
            item = None
            if batch_item_id:
                item = RemoteInstallBatchItem.objects.select_for_update().filter(
                    pk=batch_item_id, batch=batch, status='PREFLIGHT', remote_install_job__isnull=True,
                    position=batch.current_index if batch else -1).first()
                if not item or batch.status != 'RUNNING' or item.target_fqdn != normalized:
                    raise InstallFailure('BATCH_LEASE_LOST')
                if item.target_ad_dn != computer['distinguished_name']:
                    raise InstallFailure('TARGET_CHANGED')
            elif batch:
                raise InstallFailure('INSTALL_ALREADY_RUNNING')
            blockers = list(historical.select_for_update().order_by('pk'))
            if {job.pk: job.updated_at for job in blockers} != blocker_snapshot:
                raise InstallFailure('INSTALL_RECONCILIATION_REQUIRED')
            active_jobs = list(active.select_for_update().order_by('pk'))
            if any(previous.status != 'OUTCOME_UNKNOWN' or previous.target_fqdn != normalized
                   or previous.pk not in blocker_snapshot for previous in active_jobs):
                raise InstallFailure('INSTALL_ALREADY_RUNNING')
            if blockers:
                try:
                    _, correlation, _ = _match_machine(computer, _machine_maps())
                except Exception:
                    raise InstallFailure('CORRELATION_UNAVAILABLE') from None
                if correlation != 'UNMANAGED':
                    raise InstallFailure('TARGET_MANAGED_OR_CONFLICT')
            # Transfer only this target's unknown slot after fresh proof. A
            # failed insert rolls the release back with the reconciliation.
            for previous in blockers:
                if previous.active_slot == 'global':
                    previous.active_slot = None
                    previous.save(update_fields=['active_slot'])
            # The unique global slot remains the final admission gate.
            job = RemoteInstallJob.objects.create(
                target_hostname=computer['hostname'], target_fqdn=normalized,
                target_ad_dn=computer['distinguished_name'], requested_by=actor,
                active_slot='global', runner_heartbeat_at=timezone.now(),
                diagnostics=_initial_diagnostics(),
            )
            if item:
                item.remote_install_job = job
                item.status = 'INSTALLING'
                item.save(update_fields=['remote_install_job', 'status', 'updated_at'])
            for previous in blockers:
                data = dict(previous.diagnostics or {})
                reconciliation = {
                    'status': 'TARGET_ABSENCE_CONFIRMED',
                    'reconciled_at': timezone.now().isoformat(),
                    'reconciled_by_user_id': actor.pk,
                    'new_job_id': str(job.pk),
                }
                data['reconciliation'] = reconciliation
                data['reconciliation_history'] = [*data.get('reconciliation_history', []), reconciliation]
                previous.diagnostics = data
                previous.save(update_fields=['diagnostics', 'updated_at'])
            return job
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
        _update(job.pk, 'INTERRUPTED', status='INTERRUPTED', error_code='RUNNER_START_FAILED',
                diagnostic={'safe_to_retry': 'YES'})
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
                or parsed.query or parsed.fragment or any(c in parsed.geturl() for c in "'\"`\r\n ")):
            raise InstallFailure('INSTALLER_URL_INVALID')
    if parsed_base.netloc != parsed_installer.netloc or not parsed_installer.path.endswith('/Install-NightOwlAgentDotNet.ps1'):
        raise InstallFailure('INSTALLER_URL_INVALID')
    return base, installer


def _remote_script(fqdn, username, password, script, timeout, *, on_event=None, before_send=None, lease_job_id=None, enrollment_token=None):
    from winrm.exceptions import AuthenticationError, WinRMOperationTimeoutError, WinRMTransportError

    try:
        # One block prevents stdin's line-by-line parser from executing pieces
        # of a multiline wrapper before it has received the whole statement.
        payload = ('& {\n' + script.replace('\r\n', '\n') + '\n}\n\n').encode('ascii')
        if not script.strip() or len(payload) > MAX_REMOTE_SCRIPT_BYTES:
            raise ValueError()
        arguments = ['-NoProfile', '-NonInteractive', '-Command', '-']
        if enrollment_token is not None:
            if not re.fullmatch(r'deploy_[A-Za-z0-9_-]{20,200}', enrollment_token):
                raise ValueError()
            # A fixed launcher reads code and credential as separate stdin records.
            # The credential is never interpolated into PowerShell source or argv.
            launcher = "$code = [Console]::In.ReadLine(); & ([ScriptBlock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($code))))"
            arguments = ['-NoProfile', '-NonInteractive', '-EncodedCommand',
                         base64.b64encode(launcher.encode('utf-16-le')).decode('ascii')]
            payload = base64.b64encode(script.encode('ascii')) + b'\n' + enrollment_token.encode('ascii') + b'\n'
    except (AttributeError, UnicodeError, ValueError):
        raise InstallFailure('REMOTE_SCRIPT_INVALID', command_attempted=False) from None
    try:
        protocol = _new_winrm_protocol(fqdn, username, password)
    except ProbeFailure as exc:
        raise InstallFailure(exc.code, command_attempted=False) from None
    except Exception:
        raise InstallFailure('WINRM_UNAVAILABLE', command_attempted=False) from None
    shell_id = command_id = None
    attempted = False
    try:
        shell_id = protocol.open_shell()
        command_id = protocol.run_command(shell_id, 'powershell.exe', arguments)
        if not command_id:
            raise InstallFailure('WINRM_UNAVAILABLE', command_attempted=False)
        if before_send:
            before_send()
        attempted = True
        if lease_job_id:
            # Keep the row lock across the bounded stdin send: a stall resolver
            # cannot release admission between lease verification and dispatch.
            with transaction.atomic():
                lease = RemoteInstallJob.objects.select_for_update().filter(
                    pk=lease_job_id, active_slot='global', status='RUNNING').first()
                if not lease:
                    raise InstallLeaseLost()
                protocol.send_command_input(shell_id, command_id, payload, end=True)
        else:
            protocol.send_command_input(shell_id, command_id, payload, end=True)
        deadline = time.monotonic() + timeout
        pending = b''
        while time.monotonic() < deadline:
            try:
                stdout, _stderr, exit_code, done = protocol.get_command_output_raw(shell_id, command_id)
            except WinRMOperationTimeoutError:
                continue
            if on_event:
                # A wrapper emits only these tiny frames; never store raw output/errors.
                pending = (pending + stdout)[-4096:]
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    match = re.fullmatch(rb'NIGHTOWL_INSTALL:(STARTED|FINISHED|NOT_STARTED)(?::(-?\d{1,10}))?\r?', line)
                    if match:
                        event = match[1].decode('ascii')
                        code = int(match[2]) if match[2] else None
                        if ((event == 'STARTED' and code is None) or
                                (event == 'FINISHED' and code is not None and -2147483648 <= code <= 2147483647) or
                                (event == 'NOT_STARTED' and code in (25, 26, 27, 28, 29))):
                            on_event(event, code)
            if done:
                return exit_code
        raise InstallFailure('REMOTE_COMMAND_TIMEOUT')
    except (InstallFailure, InstallLeaseLost):
        raise
    except ProbeFailure as exc:
        raise InstallFailure(exc.code, command_attempted=attempted) from None
    except AuthenticationError:
        raise InstallFailure('AUTHENTICATION_FAILED', command_attempted=attempted) from None
    except WinRMTransportError as exc:
        raise InstallFailure('AUTHENTICATION_FAILED' if exc.code in (401, 403) else 'WINRM_UNAVAILABLE',
                             command_attempted=attempted) from None
    except Exception:
        raise InstallFailure('WINRM_UNAVAILABLE', command_attempted=attempted) from None
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


def _installer_script(contract):
    installer_sha256 = contract['installer_sha256']
    if not re.fullmatch(r'[a-f0-9]{64}', installer_sha256):
        raise InstallFailure('INSTALLER_CONTRACT_MISMATCH')
    base = str(settings.NIGHTOWL_AGENT_PUBLIC_SERVER_URL).rstrip('/')
    parsed = urlsplit(base)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or
            parsed.query or parsed.fragment or any(c in base for c in "'\"`\r\n ")):
        raise InstallFailure('INSTALL_RELEASE_INVALID')
    installer = contract['installer_source']
    package = contract['package_url']
    for url in (installer, package):
        if (not url.startswith(base + '/downloads/nightowl-agent/releases/') or
                any(c in url for c in "'\"`\r\n ")):
            raise InstallFailure('INSTALL_RELEASE_INVALID')
    for name, pattern in (('version', r'[A-Za-z0-9.+-]{1,50}'), ('channel', r'(development|pilot|stable)'),
                          ('package_sha256', r'[a-f0-9]{64}'), ('git_commit', r'[a-f0-9]{40}')):
        if not re.fullmatch(pattern, contract[name]):
            raise InstallFailure('INSTALL_RELEASE_INVALID')
    trust = base64.b64encode(json.dumps(contract['trusted_public_keys']).encode()).decode('ascii')
    return f"""
$ErrorActionPreference = 'Stop'
function Stop-BeforeInstaller([int]$Code) {{
    [Console]::WriteLine('NIGHTOWL_INSTALL:NOT_STARTED:' + $Code)
    exit $Code
}}
$nightOwlRoot = Join-Path $env:ProgramData 'NightOwl'
$agentInstall = Join-Path $nightOwlRoot 'AgentDotNet'
$agentExe = Join-Path $agentInstall 'NightOwl.Agent.Windows.exe'
if ((Get-Service -Name 'NightOwlAgentDotNet' -ErrorAction SilentlyContinue) -or
    (Test-Path -LiteralPath $agentExe -PathType Leaf)) {{ Stop-BeforeInstaller 25 }}
$dir = Join-Path $env:TEMP ('NightOwlRemoteInstall-' + [guid]::NewGuid().ToString('N'))
try {{
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $script = Join-Path $dir 'Install-NightOwlAgentDotNet.ps1'
    try {{ Invoke-WebRequest -Uri '{installer}' -OutFile $script -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 30 }}
    catch {{ Stop-BeforeInstaller 26 }}
    if ((Get-FileHash -LiteralPath $script -Algorithm SHA256).Hash.ToLowerInvariant() -ne '{installer_sha256}') {{ Stop-BeforeInstaller 27 }}
    $trust = Join-Path $dir 'trusted-public-keys.json'
    [System.IO.File]::WriteAllBytes($trust, [Convert]::FromBase64String('{trust}'))
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($script, [ref]$null, [ref]$errors)
    if ($errors.Count -or -not $ast.ParamBlock) {{ Stop-BeforeInstaller 28 }}
    $names = @($ast.ParamBlock.Parameters | ForEach-Object {{ $_.Name.VariablePath.UserPath }})
    foreach ($name in @('ServerUrl', 'EnrollmentToken', 'InstallAsService', 'RunCheck', 'NoGui', 'NonInteractive', 'PackageUrl', 'TrustedPublicKeysPath', 'ExpectedVersion', 'ExpectedChannel', 'ExpectedPackageSha256', 'ExpectedGitCommit')) {{
        if ($names -notcontains $name) {{ Stop-BeforeInstaller 28 }}
    }}
    $enrollmentToken = [Console]::In.ReadLine()
    if ($enrollmentToken -notmatch '^deploy_[A-Za-z0-9_-]{{20,200}}$') {{ Stop-BeforeInstaller 28 }}
    $childScript = @'
$ErrorActionPreference = 'Stop'
$token = [Console]::In.ReadLine()
if ($token -notmatch '^deploy_[A-Za-z0-9_-]{{20,200}}$') {{ exit 28 }}
& '__INSTALLER_PATH__' -EnrollmentToken $token -ServerUrl '{base}' -PackageUrl '{package}' -TrustedPublicKeysPath '__TRUST_PATH__' -ExpectedVersion '{contract['version']}' -ExpectedChannel '{contract['channel']}' -ExpectedPackageSha256 '{contract['package_sha256']}' -ExpectedGitCommit '{contract['git_commit']}' -InstallAsService -RunCheck -NoGui -NonInteractive
exit $LASTEXITCODE
'@
    $childScript = $childScript.Replace('__INSTALLER_PATH__', $script.Replace("'", "''")).Replace('__TRUST_PATH__', $trust.Replace("'", "''"))
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = Join-Path $PSHOME 'powershell.exe'
    $info.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' + [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($childScript))
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.RedirectStandardInput = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    try {{ if (-not $process.Start()) {{ Stop-BeforeInstaller 29 }} }}
    catch {{ Stop-BeforeInstaller 29 }}
    [Console]::WriteLine('NIGHTOWL_INSTALL:STARTED')
    # Drain both streams concurrently, but never send installer output to the backend.
    $out = $process.StandardOutput.BaseStream.CopyToAsync([System.IO.Stream]::Null)
    $err = $process.StandardError.BaseStream.CopyToAsync([System.IO.Stream]::Null)
    $process.StandardInput.WriteLine($enrollmentToken)
    $process.StandardInput.Close()
    $enrollmentToken = $null
    $process.WaitForExit()
    $out.Wait()
    $err.Wait()
    $code = $process.ExitCode
    [Console]::WriteLine('NIGHTOWL_INSTALL:FINISHED:' + $code)
    $process.Dispose()
    exit $code
}} finally {{ Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue }}
"""


def _prepare_deployment(job, contract):
    with transaction.atomic():
        lease = RemoteInstallJob.objects.select_for_update().get(pk=job.pk)
        if lease.active_slot != 'global' or lease.status != 'RUNNING':
            raise InstallLeaseLost()
        deployment, token = AgentDeploymentToken.create_with_token(
            release_id=contract['release_id'], channel=contract['channel'], created_by=job.requested_by,
            expires_at=timezone.now() + timedelta(seconds=INSTALL_TIMEOUT + ENROLLMENT_TIMEOUT + FIRST_HEARTBEAT_TIMEOUT + 300),
            metadata={'remote_install_job_id': str(job.pk), 'target_fqdn': job.target_fqdn,
                      'target_hostname': job.target_hostname, 'target_ad_dn': job.target_ad_dn})
        _update(job.pk, 'PREPARING_ENROLLMENT', diagnostic={'deployment_id': str(deployment.pk)})
        return deployment, token


def _deployment_endpoint(deployment, computer):
    deployment.refresh_from_db()
    if not deployment.endpoint_id or not deployment.used_at:
        return None
    if deployment.status != AgentDeploymentToken.STATUS_INSTALLING or deployment.is_expired:
        raise InstallFailure('DEPLOYMENT_INVALID', installed=True)
    machine = _matching_endpoint(computer, installed=True)
    if not machine or machine.pk != deployment.endpoint_id:
        raise InstallFailure('DEPLOYMENT_ENDPOINT_MISMATCH', installed=True)
    return machine


def _matching_endpoint(computer, *, installed=False):
    machine, correlation, _method = _match_machine(computer, _machine_maps())
    if correlation == 'CONFLICT':
        raise InstallFailure('ENDPOINT_CONFLICT', installed=installed)
    return machine


def _deployment_heartbeat(deployment, machine, version, *, fresh=False):
    if not deployment.used_at or not machine.machine_id:
        return None
    snapshots = InventorySnapshot.objects.filter(
        machine=machine, received_at__gte=deployment.used_at,
        raw_payload__snapshot_source='heartbeat',
        raw_payload__machine_id=machine.machine_id,
        raw_payload__agent_version=version,
    )
    if fresh:
        snapshots = snapshots.filter(received_at__gte=timezone.now() - timedelta(seconds=FIRST_HEARTBEAT_TIMEOUT))
    return snapshots.order_by('received_at', 'pk').first()


def preview_install_confirmation(job_id):
    """Read-only evidence preview. Never repairs a historical job or deployment."""
    job = RemoteInstallJob.objects.get(pk=job_id)
    diagnostics = job.diagnostics or {}
    result = {'eligible': False, 'job_id': str(job.pk), 'reasons': [],
              'original_status': job.status, 'original_error_code': job.error_code}
    deployment = AgentDeploymentToken.objects.select_related('release', 'endpoint').filter(
        pk=diagnostics.get('deployment_id'),
    ).first()
    if (job.status != 'INSTALLED_UNVERIFIED' or job.error_code != 'HEARTBEAT_TIMEOUT' or
            not job.finished_at or diagnostics.get('installer_exit_code') != 0 or
            not diagnostics.get('installer_started_at') or not diagnostics.get('installer_finished_at') or
            diagnostics.get('service_state') != 'RUNNING' or
            diagnostics.get('service_validation_status') != 'PASS'):
        result['reasons'].append('installation_proof_missing')
    if (not deployment or deployment.status != AgentDeploymentToken.STATUS_FAILED or
            deployment.failure_code != 'remote_install_unverified' or
            not deployment.used_at or not deployment.endpoint_id or
            deployment.endpoint_id != job.endpoint_id or
            str(deployment.release_id) != diagnostics.get('release_id') or
            deployment.release.version != diagnostics.get('version') or
            deployment.metadata.get('remote_install_job_id') != str(job.pk) or
            normalize_fqdn(deployment.metadata.get('target_fqdn')) != normalize_fqdn(job.target_fqdn)):
        result['reasons'].append('deployment_binding_invalid')
        return result
    machine = deployment.endpoint
    try:
        matched = _matching_endpoint({'fqdn': job.target_fqdn, 'hostname': job.target_hostname})
    except InstallFailure:
        matched = None
    if (not matched or matched.pk != machine.pk or not machine.machine_id or
            AgentMachine.objects.filter(machine_id=machine.machine_id).count() != 1 or
            machine.has_terminal_lifecycle or machine.agent_version != deployment.release.version):
        result['reasons'].append('endpoint_identity_or_version_conflict')
    heartbeat = _deployment_heartbeat(deployment, machine, deployment.release.version)
    if not heartbeat:
        result['reasons'].append('confirmed_heartbeat_missing')
    result.update(deployment_id=str(deployment.pk), endpoint_id=str(machine.pk),
                  machine_id=machine.machine_id, release_id=str(deployment.release_id),
                  enrollment_at=deployment.used_at.isoformat(),
                  heartbeat_received_at=heartbeat.received_at.isoformat() if heartbeat else None,
                  heartbeat_snapshot_id=str(heartbeat.pk) if heartbeat else None)
    result['eligible'] = not result['reasons']
    return result


def run_remote_install(job_id, username, password):
    stop = threading.Event()
    pulse = threading.Thread(target=_heartbeat, args=(job_id, stop), daemon=True)
    pulse.start()
    installed = False
    install_started = False
    stage = 'VALIDATING_TARGET'
    installer_finished = False
    installer_exit_code = None
    not_started_code = None
    deployment = None
    def installer_event(event, code):
        nonlocal stage, install_started, installer_finished, installer_exit_code, not_started_code
        if event == 'NOT_STARTED' and not install_started and code in (25, 26, 27, 28, 29):
            not_started_code = code
            return
        if event == 'STARTED' and not install_started:
            install_started = True
            stage = 'INSTALLER_STARTED'
            _update(job_id, stage, diagnostic={'installer_started_at': timezone.now().isoformat(),
                                              'safe_to_retry': 'NO'})
        elif event == 'FINISHED' and install_started and not installer_finished:
            installer_finished = True
            installer_exit_code = code
            stage = 'INSTALLER_FINISHED'
            _update(job_id, stage, diagnostic={'installer_finished_at': timezone.now().isoformat(),
                                              'installer_exit_code': code})
    invocation_attempted = False
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
        stage = 'VALIDATING_INSTALLER'
        _update(job_id, stage, diagnostic={'safe_to_retry': 'YES', 'installer_contract_valid': False,
            'installer_started_at': None, 'installer_finished_at': None, 'installer_exit_code': None,
            'service_present': 'UNKNOWN', 'service_state': 'UNKNOWN', 'service_validation_status': 'NOT_CHECKED'})
        try:
            contract = validate_install_release()
        except InstallerContractFailure as exc:
            raise InstallFailure(exc.code) from None
        _update(job_id, stage, diagnostic={name: contract[name] for name in (
            'release_id', 'version', 'channel', 'package_sha256', 'installer_sha256',
            'installer_contract_valid', 'release_validated', 'installer_source', 'signing_key_id',
            'git_commit', 'build_id')})
        stage = 'CONNECTING'
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
        stage = 'AUTHENTICATED'
        _update(job_id, stage)
        stage = 'PREFLIGHT_OK'
        _update(job_id, stage)
        stage = 'PREPARING_ENROLLMENT'
        _update(job_id, stage)
        if _matching_endpoint(computer):
            raise InstallFailure('ALREADY_MANAGED')
        revalidated = validate_install_release()
        if revalidated != contract:
            raise InstallFailure('INSTALL_RELEASE_INVALID')
        installer_script = _installer_script(contract)
        deployment, enrollment_token = _prepare_deployment(job, contract)
        stage = 'INSTALLING'
        invocation_attempted = True
        _update(job_id, stage, diagnostic={'safe_to_retry': 'NO', 'invocation_attempted': True})
        code = _remote_script(job.target_fqdn, username, password, installer_script, INSTALL_TIMEOUT,
                              on_event=installer_event,
                              lease_job_id=job_id, enrollment_token=enrollment_token)
        enrollment_token = None
        if not install_started:
            # Reserved wrapper codes prove failure before Process.Start. An absent frame
            # alone never proves that the child did not start (transport can be lost).
            early = {25: 'NIGHTOWL_INSTALLATION_DETECTED', 26: 'INSTALLER_DOWNLOAD_FAILED',
                     27: 'INSTALLER_CONTRACT_MISMATCH', 28: 'INSTALLER_CONTRACT_MISMATCH',
                     29: 'INSTALLER_NOT_STARTED'}
            if code == not_started_code and code in early:
                invocation_attempted = False
                raise InstallFailure(early[code])
            raise InstallFailure('INSTALLER_RESULT_UNKNOWN')
        if not installer_finished or code != installer_exit_code:
            raise InstallFailure('INSTALLER_RESULT_UNKNOWN')
        if code == 25:
            raise InstallFailure('NIGHTOWL_INSTALLATION_DETECTED')
        if code != 0:
            raise InstallFailure('INSTALLER_EXIT_NONZERO')
        installed = True
        stage = 'VALIDATING_SERVICE'
        _update(job_id, 'VALIDATING_SERVICE')
        service_check = "$s = Get-Service -Name 'NightOwlAgentDotNet' -ErrorAction SilentlyContinue; if (-not $s) { exit 2 }; if ($s.Status -eq 'Running') { exit 0 }; if ($s.Status -eq 'Stopped') { exit 3 }; exit 4"
        service_code = _remote_script(job.target_fqdn, username, password, service_check, 30)
        _update(job_id, stage, diagnostic={'service_present': 'NO' if service_code == 2 else
            'YES' if service_code in (0, 3, 4) else 'UNKNOWN',
            'service_state': {0: 'RUNNING', 3: 'STOPPED'}.get(service_code, 'UNKNOWN'),
            'service_validation_status': 'PASS' if service_code == 0 else 'FAIL'})
        if service_code != 0:
            raise InstallFailure('SERVICE_NOT_FOUND' if service_code == 2 else 'SERVICE_NOT_RUNNING', installed=True)
        stage = 'WAITING_ENROLLMENT'
        _update(job_id, 'WAITING_ENROLLMENT')
        deadline = time.monotonic() + ENROLLMENT_TIMEOUT
        machine = None
        while time.monotonic() < deadline:
            machine = _deployment_endpoint(deployment, computer)
            if machine:
                break
            time.sleep(3)
        if not machine:
            raise InstallFailure('ENROLLMENT_TIMEOUT', installed=True)
        stage = 'WAITING_HEARTBEAT'
        _update(job_id, 'WAITING_HEARTBEAT', endpoint=machine)
        deadline = time.monotonic() + FIRST_HEARTBEAT_TIMEOUT
        while time.monotonic() < deadline:
            machine = _deployment_endpoint(deployment, computer)
            if machine and machine.agent_version == contract['version'] and _deployment_heartbeat(deployment, machine, contract['version'], fresh=True):
                from agents.views import mark_machine_installed_from_deployment
                from agents.audit import create_audit_event
                with transaction.atomic():
                    _assert_install_lease(job_id)
                    deployment = AgentDeploymentToken.objects.select_for_update().select_related('release').get(pk=deployment.pk)
                    machine = AgentMachine.objects.select_for_update().get(pk=machine.pk)
                    if (deployment.status != AgentDeploymentToken.STATUS_INSTALLING or deployment.is_expired or
                            deployment.endpoint_id != machine.pk or not deployment.used_at or
                            not _deployment_heartbeat(deployment, machine, contract['version'], fresh=True) or
                            machine.agent_version != contract['version']):
                        raise InstallFailure('DEPLOYMENT_INVALID', installed=True)
                    deployment.mark_completed(machine)
                    mark_machine_installed_from_deployment(machine, deployment)
                    create_audit_event(event_type='agent.deployment.completed', title='Remote deployment completed',
                        description='Service, enrollment and heartbeat confirmed.', endpoint=machine,
                        metadata={'deployment_id': str(deployment.pk), 'remote_install_job_id': str(job_id),
                                  'release_id': str(deployment.release_id)})
                    _update(job_id, 'COMPLETED', status='COMPLETED', endpoint=machine,
                            diagnostic={'safe_to_retry': 'NO'})
                    return
            time.sleep(3)
        raise InstallFailure('HEARTBEAT_TIMEOUT', installed=True)
    except InstallLeaseLost:
        return
    except InstallFailure as exc:
        if exc.command_attempted is False and not install_started:
            invocation_attempted = False
        state = ('INSTALLED_UNVERIFIED' if exc.installed or installed else
                 'OUTCOME_UNKNOWN' if invocation_attempted else 'FAILED')
        if RemoteInstallJob.objects.filter(pk=job_id, active_slot='global').exists():
            _update(job_id, stage, status=state, error_code=exc.code,
                    diagnostic={'safe_to_retry': 'NO' if installed or invocation_attempted or
                        exc.code == 'NIGHTOWL_INSTALLATION_DETECTED' else 'YES',
                        'invocation_attempted': invocation_attempted})
    except Exception:
        state = 'OUTCOME_UNKNOWN' if invocation_attempted else 'INTERRUPTED'
        if RemoteInstallJob.objects.filter(pk=job_id, active_slot='global').exists():
            _update(job_id, stage, status=state, error_code='RUNNER_INTERRUPTED',
                    diagnostic={'safe_to_retry': 'NO' if invocation_attempted else 'YES'})
    finally:
        try:
            if deployment is not None:
                # Never leave an unconsumed credential reusable after this attempt ends.
                with transaction.atomic():
                    current = AgentDeploymentToken.objects.select_for_update().get(pk=deployment.pk)
                    if current.status in (current.STATUS_WAITING, current.STATUS_INSTALLING):
                        current.mark_failed('remote_install_unverified')
        finally:
            stop.set()
            pulse.join(timeout=2)
            close_old_connections()


def _assert_install_lease(job_id):
    if not RemoteInstallJob.objects.filter(pk=job_id, active_slot='global', status='RUNNING').exists():
        raise InstallLeaseLost()
