import json
import os
import subprocess
from contextlib import ExitStack
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from agents.models import AgentMachine
from dashboard import remote_install as runner
from dashboard.installer_contract import InstallerContractFailure
from dashboard.models import RemoteInstallJob
from dashboard.test_remote_install import COMPUTER, READY, CONTRACT, recorded_outputs


PASSWORD = 'SUPER_SECRET_REMOTE_INSTALL_91827'
TOKEN = 'SUPER_SECRET_ENROLLMENT_TOKEN_73192'


class InstallDiagnosticTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('diagnostic-admin', is_staff=True)
        self.client.force_login(self.user)
        self.job = RemoteInstallJob.objects.create(target_hostname=COMPUTER['hostname'],
            target_fqdn=COMPUTER['fqdn'], target_ad_dn=COMPUTER['distinguished_name'],
            requested_by=self.user, active_slot='global', runner_heartbeat_at=timezone.now())

    def run_job(self, outputs, **patches):
        defaults = {'_ad_target': COMPUTER, 'validate_install_release': CONTRACT,
                    'run_remote_install_preflight': READY, '_enrollment_available': True,
                    '_matching_endpoint': None, 'ENROLLMENT_TIMEOUT': 0}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(runner.threading, 'Thread'))
            for name, value in {**defaults, **patches}.items():
                if name.endswith('TIMEOUT'):
                    stack.enter_context(mock.patch.object(runner, name, value))
                else:
                    stack.enter_context(mock.patch.object(runner, name,
                        side_effect=value if isinstance(value, Exception) else None,
                        return_value=None if isinstance(value, Exception) else value))
            command = stack.enter_context(mock.patch.object(runner, '_remote_script',
                side_effect=outputs if callable(outputs) else recorded_outputs(outputs)))
            with self.assertNoLogs(level='ERROR'):
                runner.run_remote_install(self.job.pk, 'admin', PASSWORD)
        self.job.refresh_from_db()
        return command

    def test_contract_failures_open_no_remote_connection_and_are_retry_safe(self):
        for error in ('INSTALLER_CONTRACT_MISMATCH', 'INSTALLER_DOWNLOAD_FAILED'):
            with self.subTest(error=error):
                command = self.run_job([], validate_install_release=InstallerContractFailure(error))
                command.assert_not_called()
                self.assertEqual(self.job.stage, 'VALIDATING_INSTALLER')
                self.assertEqual(self.job.status, 'FAILED')
                self.assertEqual(self.job.diagnostics['safe_to_retry'], 'YES')
                self.assertIsNone(self.job.diagnostics['installer_exit_code'])
                RemoteInstallJob.objects.filter(pk=self.job.pk).update(active_slot='global')

    def test_auth_failure_before_invocation_is_retry_safe(self):
        command = self.run_job([], run_remote_install_preflight={
            'status': 'NOT_READY', 'checks': {'AUTH': {'status': 'FAIL', 'code': 'AUTHENTICATION_FAILED'}}})
        command.assert_not_called()
        self.assertEqual(self.job.diagnostics['safe_to_retry'], 'YES')
        self.assertIsNone(self.job.diagnostics['installer_started_at'])

    def test_real_nonzero_exit_is_recorded_not_collapsed(self):
        self.run_job([73])
        d = self.job.diagnostics
        self.assertEqual(d['installer_exit_code'], 73)
        self.assertIsNotNone(d['installer_started_at'])
        self.assertIsNotNone(d['installer_finished_at'])
        self.assertEqual(d['installer_sha256'], CONTRACT['installer_sha256'])
        self.assertEqual(d['outcome'], 'UNKNOWN')
        self.assertEqual(d['safe_to_retry'], 'NO')
        self.assertEqual(self.job.stage, 'INSTALLER_FINISHED')
        self.assertEqual(self.job.error_code, 'INSTALLER_EXIT_NONZERO')

    def test_missing_completion_and_transport_loss_do_not_invent_exit(self):
        def lost(*args, **kwargs):
            kwargs['on_event']('STARTED', None)
            raise runner.InstallFailure('REMOTE_COMMAND_TIMEOUT')
        self.run_job(lost)
        self.assertIsNotNone(self.job.diagnostics['installer_started_at'])
        self.assertIsNone(self.job.diagnostics['installer_finished_at'])
        self.assertIsNone(self.job.diagnostics['installer_exit_code'])
        self.assertEqual(self.job.stage, 'INSTALLER_STARTED')
        self.assertEqual(self.job.diagnostics['safe_to_retry'], 'NO')

    def test_reserved_wrapper_failure_proves_no_start(self):
        def not_started(*args, **kwargs):
            kwargs['on_event']('NOT_STARTED', 26)
            return 26
        self.run_job(not_started)
        self.assertIsNone(self.job.diagnostics['installer_started_at'])
        self.assertIsNone(self.job.diagnostics['installer_exit_code'])
        self.assertEqual(self.job.diagnostics['safe_to_retry'], 'YES')
        self.assertEqual(self.job.error_code, 'INSTALLER_DOWNLOAD_FAILED')

    def test_no_frames_unknown_code_never_proves_no_start(self):
        self.run_job(lambda *args, **kwargs: 0)
        self.assertEqual(self.job.status, 'OUTCOME_UNKNOWN')
        self.assertEqual(self.job.diagnostics['safe_to_retry'], 'NO')
        self.assertIsNone(self.job.diagnostics['installer_exit_code'])

    def test_second_auth_failure_before_command_is_retry_safe(self):
        def unauthenticated(*args, **kwargs):
            raise runner.InstallFailure('AUTHENTICATION_FAILED', command_attempted=False)
        self.run_job(unauthenticated)
        self.assertEqual(self.job.status, 'FAILED')
        self.assertEqual(self.job.diagnostics['safe_to_retry'], 'YES')
        self.assertIsNone(self.job.diagnostics['installer_exit_code'])

    def test_heartbeat_success_has_separate_outcome_and_all_evidence(self):
        machine = AgentMachine.objects.create(hostname=COMPUTER['hostname'], fqdn=COMPUTER['fqdn'],
            agent_token_hash='synthetic-diagnostic', status='online', last_seen_at=timezone.now())
        values = iter([None, machine, machine])
        with mock.patch.object(runner.threading, 'Thread'), \
                mock.patch.object(runner, '_ad_target', return_value=COMPUTER), \
                mock.patch.object(runner, 'validate_install_release', return_value=CONTRACT), \
                mock.patch.object(runner, 'run_remote_install_preflight', return_value=READY), \
                mock.patch.object(runner, '_enrollment_available', return_value=True), \
                mock.patch.object(runner, '_matching_endpoint', side_effect=lambda *a, **kw: next(values)), \
                mock.patch.object(runner, '_remote_script', side_effect=recorded_outputs([0, 0])):
            runner.run_remote_install(self.job.pk, 'admin', PASSWORD)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'COMPLETED')
        self.assertEqual(self.job.diagnostics['outcome'], 'SUCCESS')
        self.assertEqual(self.job.diagnostics['service_state'], 'RUNNING')
        self.assertEqual(self.job.endpoint_id, machine.pk)

    def test_service_missing_stopped_and_running_are_distinct(self):
        for code, present, state in ((2, 'NO', 'UNKNOWN'), (3, 'YES', 'STOPPED'), (0, 'YES', 'RUNNING')):
            with self.subTest(code=code):
                self.run_job([0, code])
                self.assertEqual(self.job.diagnostics['service_present'], present)
                self.assertEqual(self.job.diagnostics['service_state'], state)
                self.assertEqual(self.job.diagnostics['installer_exit_code'], 0)
                self.assertEqual(self.job.status, 'INSTALLED_UNVERIFIED')
                self.assertEqual(self.job.diagnostics['safe_to_retry'], 'NO')
                RemoteInstallJob.objects.filter(pk=self.job.pk).update(active_slot='global')

    def test_history_is_bounded_and_stage_distinct_from_outcome(self):
        for _ in range(40):
            runner._update(self.job.pk, 'CONNECTING')
        self.job.refresh_from_db()
        self.assertEqual(len(self.job.diagnostics['stage_history']), 32)
        self.assertEqual(self.job.diagnostics['outcome'], 'RUNNING')
        self.assertEqual(self.job.stage, 'CONNECTING')

    def test_secret_exceptions_never_enter_db_logs_response_session_cache(self):
        from django.core.cache import cache
        with mock.patch.object(cache, 'set') as cached:
            self.run_job(lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(PASSWORD + TOKEN)))
            response = self.client.get(reverse('agent-install-job-status', args=[self.job.pk]))
        cached.assert_not_called()
        persisted = json.dumps(list(RemoteInstallJob.objects.values()), default=str)
        for sentinel in (PASSWORD, TOKEN):
            self.assertNotIn(sentinel, persisted)
            self.assertNotIn(sentinel, response.content.decode())
            self.assertNotIn(sentinel, json.dumps(dict(self.client.session)))
            self.assertNotIn(sentinel, runner._installer_script(CONTRACT))
        self.assertEqual(response.json()['diagnostics']['safe_to_retry'], 'NO')

    def test_status_get_does_not_reconcile_or_rewrite_legacy_job(self):
        RemoteInstallJob.objects.filter(pk=self.job.pk).update(
            status='OUTCOME_UNKNOWN', stage='OUTCOME_UNKNOWN', error_code='INSTALLER_FAILED',
            runner_heartbeat_at=timezone.now() - runner.UNKNOWN_SLOT_HOLD)
        before = RemoteInstallJob.objects.values().get(pk=self.job.pk)
        with self.assertNumQueries(3):
            response = self.client.get(reverse('agent-install-job-status', args=[self.job.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(before, RemoteInstallJob.objects.values().get(pk=self.job.pk))

    def test_only_safe_frames_are_consumed_and_actual_exit_preserved(self):
        protocol = mock.Mock()
        protocol.get_command_output_raw.side_effect = [
            (b'NIGHTOWL_INSTALL:STA', b'', None, False),
            (b'RTED\n' + PASSWORD.encode() + b'\nNIGHTOWL_INSTALL:FINISHED:37\n', TOKEN.encode(), 37, True)]
        events = []
        with mock.patch.object(runner, '_new_winrm_protocol', return_value=protocol):
            code = runner._remote_script(COMPUTER['fqdn'], 'admin', PASSWORD, 'static', 30,
                                         on_event=lambda event, value: events.append((event, value)))
        self.assertEqual(code, 37)
        self.assertEqual(events, [('STARTED', None), ('FINISHED', 37)])
        self.assertNotIn(PASSWORD, repr(protocol.run_command.call_args))
        self.assertNotIn(TOKEN, repr(events))

    def test_remote_script_pins_hash_ast_and_real_exit_before_execution(self):
        script = runner._installer_script(CONTRACT)
        self.assertLess(script.index('Get-FileHash'), script.index('$process.Start()'))
        self.assertLess(script.index('ParseFile'), script.index('$process.Start()'))
        self.assertIn('-MaximumRedirection 0', script)
        self.assertIn('exit $code', script)
        self.assertNotIn('exit 1', script)
        self.assertIn('Stream]::Null', script)
        self.assertIn(CONTRACT['installer_source'], script)
        self.assertIn(CONTRACT['package_url'], script)
        self.assertIn('-TrustedPublicKeysPath', script)
        self.assertIn('-ExpectedVersion "0.1.1.0-rc44"', script)
        self.assertIn('-ExpectedPackageSha256', script)
        self.assertNotIn('-AllowReleaseBundledTrustForLab', script)

    def test_release_changed_before_invocation_fails_without_remote_command(self):
        with mock.patch.object(runner.threading, 'Thread'), \
                mock.patch.object(runner, '_ad_target', return_value=COMPUTER), \
                mock.patch.object(runner, 'validate_install_release', side_effect=[CONTRACT, {**CONTRACT, 'installer_sha256': 'c' * 64}]), \
                mock.patch.object(runner, 'run_remote_install_preflight', return_value=READY), \
                mock.patch.object(runner, '_enrollment_available', return_value=True), \
                mock.patch.object(runner, '_matching_endpoint', return_value=None), \
                mock.patch.object(runner, '_remote_script') as command:
            runner.run_remote_install(self.job.pk, 'admin', PASSWORD)
        command.assert_not_called()
        self.job.refresh_from_db()
        self.assertEqual(self.job.error_code, 'INSTALL_RELEASE_INVALID')
        self.assertEqual(self.job.status, 'FAILED')
        self.assertEqual(self.job.diagnostics['release_id'], CONTRACT['release_id'])
        self.assertNotIn('trusted_public_keys', self.job.diagnostics)

    @skipUnless(os.name == 'nt', 'PowerShell AST is validated on the Windows build host')
    def test_generated_wrapper_parses_without_execution(self):
        command = "$s = [Console]::In.ReadToEnd(); $e = $null; [System.Management.Automation.Language.Parser]::ParseInput($s, [ref]$null, [ref]$e) | Out-Null; if ($e.Count) { exit 1 }; exit 0"
        result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', command],
            input=runner._installer_script(CONTRACT), text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, 'Generated remote wrapper must parse')

    def test_stale_new_runner_preserves_stage_and_retry_safety(self):
        runner._update(self.job.pk, 'INSTALLER_STARTED', diagnostic={'safe_to_retry': 'NO'})
        RemoteInstallJob.objects.filter(pk=self.job.pk).update(
            runner_heartbeat_at=timezone.now() - runner.STALE_AFTER * 2)
        runner.reconcile_stale_jobs()
        self.job.refresh_from_db()
        self.assertEqual(self.job.stage, 'INSTALLER_STARTED')
        self.assertEqual(self.job.diagnostics['outcome'], 'UNKNOWN')
        self.assertEqual(self.job.active_slot, 'global')
