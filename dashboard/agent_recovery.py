"""Explicit offline service recovery. Credentials remain request-local."""
import re
import time
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from agents.models import AgentMachine, InventorySnapshot
from dashboard.models import RemoteInstallJob
from dashboard.remote_install import InstallFailure, _matching_endpoint, _remote_script_candidates
from dashboard.remote_install_preflight import _ad_target, _transport_selection, run_remote_install_preflight


@sensitive_variables('password')
def recover_offline_agent(machine, actor, username, password, restart_running):
    if (not machine.is_active or machine.has_terminal_lifecycle or machine.status != AgentMachine.STATUS_OFFLINE or
            not machine.machine_id or not machine.agent_token_hash or not machine.agent_version or
            not machine.last_seen_at or machine.last_seen_at >= timezone.now() - timedelta(minutes=5)):
        raise InstallFailure('RECOVERY_ENDPOINT_NOT_OFFLINE')
    if (AgentMachine.objects.filter(machine_id__iexact=machine.machine_id).count() != 1 or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}', machine.machine_id)):
        raise InstallFailure('RECOVERY_IDENTITY_CONFLICT')
    fqdn = (machine.fqdn or '').strip().rstrip('.')
    computer = _ad_target(fqdn, expected_machine=machine)
    if not computer.get('sid') or _matching_endpoint(computer) != machine:
        raise InstallFailure('RECOVERY_IDENTITY_CONFLICT')
    try:
        with transaction.atomic():
            job = RemoteInstallJob.objects.create(target_hostname=computer['hostname'], target_fqdn=fqdn,
                target_ad_dn=computer['distinguished_name'], requested_by=actor, endpoint=machine,
                active_slot='global', status='RUNNING', stage='CONNECTING', started_at=timezone.now(),
                runner_heartbeat_at=timezone.now(), diagnostics={'operation':'offline_recovery'})
    except IntegrityError:
        raise InstallFailure('INSTALL_ALREADY_RUNNING') from None
    result = 'RECOVERY_FAILED'
    dispatched = False
    outcome_unknown = False
    try:
        preflight = run_remote_install_preflight(fqdn, username, password, expected_machine=machine)
        checks = preflight.get('checks') or {}
        failures = [v.get('code', 'WINRM_UNAVAILABLE') for k, v in checks.items()
                    if k != 'NIGHTOWL_ABSENCE' and v.get('status') != 'PASS']
        if failures: raise InstallFailure(failures[0])
        absence = preflight.get('nightowl_absence') or {}
        if absence.get('correlation') != 'MANAGED' or _matching_endpoint(computer) != machine:
            raise InstallFailure('RECOVERY_IDENTITY_CONFLICT')
        if absence.get('service_present') is not True: raise InstallFailure('RECOVERY_SERVICE_MISSING')
        target = preflight.get('target') or {}
        transport = _transport_selection(fqdn, target.get('winrm_transport'), target.get('winrm_port'))
        machine.refresh_from_db()
        if machine.status != AgentMachine.STATUS_OFFLINE or not machine.last_seen_at or machine.last_seen_at >= timezone.now() - timedelta(minutes=5):
            raise InstallFailure('RECOVERY_ENDPOINT_NOT_OFFLINE')
        checkpoint = timezone.now()
        # Strict machine_id alphabet above makes this fixed literal safe to embed.
        command = ("$ErrorActionPreference='Stop'; try { $c=Get-Content -Raw -LiteralPath "
            "'C:\\ProgramData\\NightOwl\\Config\\agent.config.json' | ConvertFrom-Json; "
            f"if ([string]$c.machineId -cne '{machine.machine_id}') {{ exit 31 }}; "
            "$s=Get-Service -Name 'NightOwlAgentDotNet' -ErrorAction SilentlyContinue; if (-not $s) { exit 11 }; "
            + ("if ($s.Status -eq 'Running') { Restart-Service -Name 'NightOwlAgentDotNet' -ErrorAction Stop } "
               if restart_running else "if ($s.Status -eq 'Running') { exit 10 } ")
            + "else { Start-Service -Name 'NightOwlAgentDotNet' -ErrorAction Stop }; exit 0 } catch { exit 12 }")
        dispatched = True
        code, _, _ = _remote_script_candidates(fqdn, username, password, command, 30,
            endpoint_candidates=[entry for entry in target.get('endpoint_attempts') or [] if entry.get('ip') == target.get('selected_ip')],
            selected_ip=target.get('selected_ip'),
            expected_computer=computer, winrm_transport=transport)
        dispatched = False  # Exit code is known; only transport loss is ambiguous.
        if code in (10, 11, 12, 31):
            raise InstallFailure({10:'RECOVERY_RESTART_CONFIRMATION_REQUIRED',11:'RECOVERY_SERVICE_MISSING',
                12:'RECOVERY_SERVICE_FAILED',31:'RECOVERY_IDENTITY_CONFLICT'}[code])
        if code != 0: raise InstallFailure('RECOVERY_REMOTE_FAILED')
        result = 'AWAITING_HEARTBEAT'
        RemoteInstallJob.objects.filter(pk=job.pk, active_slot='global').update(stage='WAITING_HEARTBEAT',
            diagnostics={'operation':'offline_recovery', 'service_action':'START_OR_RESTART_REQUESTED'}, updated_at=timezone.now())
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if InventorySnapshot.objects.filter(machine=machine, received_at__gt=checkpoint,
                raw_payload__snapshot_source='heartbeat', raw_payload__machine_id=machine.machine_id,
                raw_payload__agent_version=machine.agent_version).exists():
                result = 'RECOVERED'; break
            time.sleep(1)
        if result != 'RECOVERED': result = 'HEARTBEAT_TIMEOUT'
        return {'status':result, 'job_id':str(job.pk)}
    except InstallFailure as exc:
        if dispatched and exc.command_attempted is not False:
            outcome_unknown = True
            result = 'RECOVERY_OUTCOME_UNKNOWN'
            raise InstallFailure(result, command_attempted=True) from None
        result = exc.code
        raise
    except Exception:
        if dispatched:
            outcome_unknown = True
            result = 'RECOVERY_OUTCOME_UNKNOWN'
            raise InstallFailure(result, command_attempted=True) from None
        raise
    finally:
        RemoteInstallJob.objects.filter(pk=job.pk).update(status='OUTCOME_UNKNOWN' if outcome_unknown else 'COMPLETED' if result == 'RECOVERED' else 'FAILED',
            stage='COMPLETED' if result == 'RECOVERED' else 'WAITING_HEARTBEAT' if result == 'HEARTBEAT_TIMEOUT' else 'INTERRUPTED', error_code='' if result == 'RECOVERED' else result,
            active_slot='global' if outcome_unknown else None, finished_at=timezone.now(), updated_at=timezone.now())
