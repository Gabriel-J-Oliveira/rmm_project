import io
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from agents.models import AgentMachine
from dashboard.models import RemoteInstallJob
from dashboard import remote_install
from dashboard.remote_install_preflight import ProbeFailure


SENTINEL = 'SUPER_SECRET_REMOTE_INSTALL_91827'
COMPUTER = {
    'hostname': 'lab-01', 'fqdn': 'lab-01.control.local',
    'distinguished_name': 'CN=lab-01,OU=Lab,DC=control,DC=local',
}
READY = {'status': 'READY', 'checks': {}}


class RemoteInstallJobTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('install-admin', password='synthetic', is_staff=True)

    def job(self, **changes):
        fields = dict(target_hostname='lab-01', target_fqdn=COMPUTER['fqdn'],
                      target_ad_dn=COMPUTER['distinguished_name'], requested_by=self.user,
                      active_slot='global', runner_heartbeat_at=timezone.now())
        fields.update(changes)
        return RemoteInstallJob.objects.create(**fields)

    def test_global_single_active_job_and_stale_reconciliation(self):
        with mock.patch.object(remote_install, '_ad_target', side_effect=lambda fqdn: {
                **COMPUTER, 'fqdn': fqdn}):
            first = remote_install.create_remote_install_job(COMPUTER['fqdn'], self.user)
            with self.assertRaisesMessage(remote_install.InstallFailure, 'INSTALL_ALREADY_RUNNING'):
                remote_install.create_remote_install_job(COMPUTER['fqdn'], self.user)
            RemoteInstallJob.objects.filter(pk=first.pk).update(
                runner_heartbeat_at=timezone.now() - timedelta(minutes=2))
            remote_install.reconcile_stale_jobs()
            first.refresh_from_db()
            self.assertEqual(first.status, 'INTERRUPTED')
            self.assertIsNone(first.active_slot)
            second = remote_install.create_remote_install_job(COMPUTER['fqdn'], self.user)
            self.assertNotEqual(first.pk, second.pk)

    def test_stale_install_outcome_blocks_reinstallation(self):
        job = self.job(stage='INSTALLING', status='RUNNING',
                       runner_heartbeat_at=timezone.now() - timedelta(minutes=2))
        remote_install.reconcile_stale_jobs()
        job.refresh_from_db()
        self.assertEqual(job.status, 'OUTCOME_UNKNOWN')
        self.assertEqual(job.active_slot, 'global')
        with mock.patch.object(remote_install, '_ad_target', side_effect=lambda fqdn: {
                **COMPUTER, 'fqdn': fqdn}):
            with self.assertRaisesMessage(remote_install.InstallFailure, 'INSTALL_ALREADY_ATTEMPTED'):
                remote_install.create_remote_install_job(COMPUTER['fqdn'], self.user)
            with self.assertRaisesMessage(remote_install.InstallFailure, 'INSTALL_ALREADY_RUNNING'):
                remote_install.create_remote_install_job('another.control.local', self.user)
        RemoteInstallJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - remote_install.UNKNOWN_SLOT_HOLD - timedelta(minutes=1))
        remote_install.reconcile_stale_jobs()
        job.refresh_from_db()
        self.assertIsNone(job.active_slot)

    def test_credential_pipe_only_and_no_secret_in_job_argv_or_env(self):
        job = self.job()
        class CapturePipe(io.BytesIO):
            def close(self):
                self.captured = self.getvalue()
                super().close()

        proc = SimpleNamespace(stdin=CapturePipe())
        with mock.patch.object(remote_install.subprocess, 'Popen', return_value=proc) as spawn:
            remote_install.start_remote_install(job, 'CONTROL\\Admin', SENTINEL)
        self.assertIn(SENTINEL.encode(), proc.stdin.captured)
        self.assertNotIn(SENTINEL, repr(spawn.call_args))
        self.assertNotIn(SENTINEL, json.dumps(list(RemoteInstallJob.objects.values()), default=str))
        self.assertNotIn('password', [field.name for field in RemoteInstallJob._meta.fields])

    def test_runner_spawn_failure_releases_slot(self):
        job = self.job()
        with mock.patch.object(remote_install.subprocess, 'Popen', side_effect=OSError(SENTINEL)):
            with self.assertRaisesMessage(remote_install.InstallFailure, 'RUNNER_START_FAILED'):
                remote_install.start_remote_install(job, 'admin', SENTINEL)
        job.refresh_from_db()
        self.assertEqual(job.status, 'INTERRUPTED')
        self.assertIsNone(job.active_slot)

    def test_remote_command_uses_only_static_script_in_argv(self):
        protocol = mock.Mock()
        protocol.open_shell.return_value = 'synthetic-shell'
        protocol.run_command.return_value = 'synthetic-command'
        protocol.get_command_output_raw.return_value = (b'synthetic-output', b'synthetic-error', 0, True)
        with mock.patch.object(remote_install, '_new_winrm_protocol', return_value=protocol):
            code = remote_install._remote_script(COMPUTER['fqdn'], 'admin', SENTINEL, 'exit 0', 3)
        self.assertEqual(code, 0)
        self.assertNotIn(SENTINEL, repr(protocol.run_command.call_args))
        protocol.cleanup_command.assert_called_once()
        protocol.close_shell.assert_called_once()
        for code in ('WINRM_REDIRECT_BLOCKED', 'WINRM_CA_TRUST_INVALID'):
            with self.subTest(code=code), mock.patch.object(
                    remote_install, '_new_winrm_protocol', side_effect=ProbeFailure(code)):
                with self.assertRaisesMessage(remote_install.InstallFailure, code):
                    remote_install._remote_script(COMPUTER['fqdn'], 'admin', SENTINEL, 'exit 0', 3)

    def test_failed_revalidation_never_invokes_installer(self):
        job = self.job()
        failed = {'status': 'NOT_READY', 'checks': {'AUTHENTICATION': {'status': 'FAIL', 'code': 'AUTHENTICATION_FAILED'}}}
        with mock.patch.object(remote_install.threading, 'Thread'), \
                mock.patch.object(remote_install, '_ad_target', return_value=COMPUTER), \
                mock.patch.object(remote_install, 'run_remote_install_preflight', return_value=failed), \
                mock.patch.object(remote_install, '_remote_script') as command:
            remote_install.run_remote_install(job.pk, 'admin', SENTINEL)
        command.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, 'FAILED')
        self.assertEqual(job.error_code, 'AUTHENTICATION_FAILED')

    def test_target_and_preflight_gates_never_invoke_installer(self):
        failures = ('AD_COMPUTER_DISABLED', 'TARGET_MANAGED_OR_CONFLICT',
                    'UNSAFE_TARGET_ADDRESS', 'ADMIN_REQUIRED', 'NIGHTOWL_INSTALLATION_DETECTED')
        for code in failures:
            with self.subTest(code=code):
                job = self.job()
                target_failure = code in ('AD_COMPUTER_DISABLED', 'TARGET_MANAGED_OR_CONFLICT')
                preflight = {'status': 'NOT_READY', 'checks': {
                    'GATE': {'status': 'FAIL', 'code': code}}}
                with mock.patch.object(remote_install.threading, 'Thread'), \
                        mock.patch.object(remote_install, '_ad_target',
                                          side_effect=ProbeFailure(code) if target_failure else None,
                                          return_value=None if target_failure else COMPUTER), \
                        mock.patch.object(remote_install, 'run_remote_install_preflight', return_value=preflight), \
                        mock.patch.object(remote_install, '_remote_script') as command:
                    remote_install.run_remote_install(job.pk, 'admin', SENTINEL)
                command.assert_not_called()
                job.refresh_from_db()
                self.assertEqual(job.status, 'FAILED')
                self.assertEqual(job.error_code, code)
                job.delete()

    def test_ad_identity_change_after_preflight_blocks_install(self):
        job = self.job()
        changed = {**COMPUTER, 'distinguished_name': 'CN=lab-01,OU=Other,DC=control,DC=local'}
        with mock.patch.object(remote_install.threading, 'Thread'), \
                mock.patch.object(remote_install, '_ad_target', side_effect=[COMPUTER, changed]), \
                mock.patch.object(remote_install, 'run_remote_install_preflight', return_value=READY), \
                mock.patch.object(remote_install, '_remote_script') as command:
            remote_install.run_remote_install(job.pk, 'admin', SENTINEL)
        command.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, 'FAILED')
        self.assertEqual(job.error_code, 'TARGET_CHANGED')

    @override_settings(NIGHTOWL_AGENT_PUBLIC_SERVER_URL='https://nightowl.example.test',
                       NIGHTOWL_AGENT_INSTALLER_URL='https://nightowl.example.test/downloads/nightowl-agent/Install-NightOwlAgentDotNet.ps1')
    def test_success_requires_service_enrollment_and_heartbeat(self):
        job = self.job()
        machine = AgentMachine.objects.create(
            hostname='lab-01', fqdn=COMPUTER['fqdn'], domain='control.local',
            agent_token_hash='synthetic-install-result', status='online',
            last_seen_at=timezone.now() + timedelta(seconds=2))
        with mock.patch.object(remote_install.threading, 'Thread'), \
                mock.patch.object(remote_install, '_ad_target', return_value=COMPUTER), \
                mock.patch.object(remote_install, 'run_remote_install_preflight', return_value=READY), \
                mock.patch.object(remote_install, '_enrollment_available', return_value=True), \
                mock.patch.object(remote_install, '_matching_endpoint', side_effect=[None, machine, machine]), \
                mock.patch.object(remote_install, '_remote_script', side_effect=[0, 0]) as command:
            remote_install.run_remote_install(job.pk, 'admin', SENTINEL)
        job.refresh_from_db()
        self.assertEqual(job.status, 'COMPLETED')
        self.assertEqual(job.endpoint_id, machine.pk)
        self.assertIsNone(job.active_slot)
        self.assertEqual(command.call_count, 2)
        self.assertNotIn(SENTINEL, command.call_args_list[0].args[3])

    @override_settings(NIGHTOWL_AGENT_PUBLIC_SERVER_URL='https://nightowl.example.test',
                       NIGHTOWL_AGENT_INSTALLER_URL='https://nightowl.example.test/downloads/nightowl-agent/Install-NightOwlAgentDotNet.ps1')
    def test_installer_failure_and_installed_unverified(self):
        for outputs, expected in [([1], 'OUTCOME_UNKNOWN'), ([0, 1], 'INSTALLED_UNVERIFIED')]:
            job = self.job()
            with mock.patch.object(remote_install.threading, 'Thread'), \
                    mock.patch.object(remote_install, '_ad_target', return_value=COMPUTER), \
                    mock.patch.object(remote_install, 'run_remote_install_preflight', return_value=READY), \
                    mock.patch.object(remote_install, '_enrollment_available', return_value=True), \
                    mock.patch.object(remote_install, '_matching_endpoint', return_value=None), \
                    mock.patch.object(remote_install, '_remote_script', side_effect=outputs):
                remote_install.run_remote_install(job.pk, 'admin', SENTINEL)
            job.refresh_from_db()
            self.assertEqual(job.status, expected)
            self.assertEqual(job.active_slot, 'global' if expected == 'OUTCOME_UNKNOWN' else None)
            job.delete()

    @override_settings(NIGHTOWL_AGENT_PUBLIC_SERVER_URL='https://nightowl.example.test',
                       NIGHTOWL_AGENT_INSTALLER_URL='https://nightowl.example.test/downloads/nightowl-agent/Install-NightOwlAgentDotNet.ps1')
    def test_enrollment_timeout_remains_unverified(self):
        job = self.job()
        with mock.patch.object(remote_install.threading, 'Thread'), \
                mock.patch.object(remote_install, '_ad_target', return_value=COMPUTER), \
                mock.patch.object(remote_install, 'run_remote_install_preflight', return_value=READY), \
                mock.patch.object(remote_install, '_enrollment_available', return_value=True), \
                mock.patch.object(remote_install, '_matching_endpoint', return_value=None), \
                mock.patch.object(remote_install, '_remote_script', side_effect=[0, 0]), \
                mock.patch.object(remote_install, 'ENROLLMENT_TIMEOUT', 0):
            remote_install.run_remote_install(job.pk, 'admin', SENTINEL)
        job.refresh_from_db()
        self.assertEqual(job.status, 'INSTALLED_UNVERIFIED')
        self.assertEqual(job.error_code, 'ENROLLMENT_TIMEOUT')

    @override_settings(NIGHTOWL_AGENT_PUBLIC_SERVER_URL='https://nightowl.example.test',
                       NIGHTOWL_AGENT_INSTALLER_URL='https://nightowl.example.test/downloads/nightowl-agent/Install-NightOwlAgentDotNet.ps1')
    def test_heartbeat_timeout_remains_unverified(self):
        job = self.job()
        machine = AgentMachine.objects.create(hostname='lab-01', agent_token_hash='synthetic-heartbeat')
        with mock.patch.object(remote_install.threading, 'Thread'), \
                mock.patch.object(remote_install, '_ad_target', return_value=COMPUTER), \
                mock.patch.object(remote_install, 'run_remote_install_preflight', return_value=READY), \
                mock.patch.object(remote_install, '_enrollment_available', return_value=True), \
                mock.patch.object(remote_install, '_matching_endpoint', side_effect=[None, machine]), \
                mock.patch.object(remote_install, '_remote_script', side_effect=[0, 0]), \
                mock.patch.object(remote_install, 'FIRST_HEARTBEAT_TIMEOUT', 0):
            remote_install.run_remote_install(job.pk, 'admin', SENTINEL)
        job.refresh_from_db()
        self.assertEqual(job.status, 'INSTALLED_UNVERIFIED')
        self.assertEqual(job.error_code, 'HEARTBEAT_TIMEOUT')


class RemoteInstallRouteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('route-admin', password='synthetic', is_staff=True)
        self.client.force_login(self.user)
        self.url = reverse('agent-install-ad-install')
        self.data = {'csrfmiddlewaretoken': 'synthetic-csrf', 'fqdn': COMPUTER['fqdn'],
                     'username': 'admin', 'password': SENTINEL}

    def test_authorization_csrf_method_and_no_batch(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        outsider = get_user_model().objects.create_user('outsider', password='synthetic')
        self.client.force_login(outsider)
        self.assertIn(self.client.post(self.url, self.data, secure=True).status_code, (302, 403))
        self.client.force_login(self.user)
        guarded = Client(enforce_csrf_checks=True)
        guarded.force_login(self.user)
        self.assertEqual(guarded.post(self.url, self.data, secure=True).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 403)
        self.assertEqual(self.client.post(self.url, {**self.data, 'ip': '127.0.0.1'}, secure=True).status_code, 400)
        self.assertEqual(RemoteInstallJob.objects.count(), 0)

    def test_target_failure_and_duplicate_request_are_safe(self):
        with mock.patch('dashboard.remote_install._ad_target', side_effect=ProbeFailure('TARGET_MANAGED_OR_CONFLICT')):
            result = self.client.post(self.url, self.data, secure=True)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(RemoteInstallJob.objects.count(), 0)
        with mock.patch('dashboard.remote_install._ad_target', return_value=COMPUTER), \
                mock.patch('dashboard.ad_install_views.start_remote_install'):
            first = self.client.post(self.url, self.data, secure=True)
            second = self.client.post(self.url, self.data, secure=True)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(RemoteInstallJob.objects.count(), 1)
        self.assertNotIn(SENTINEL, first.content.decode())
        self.assertNotIn(SENTINEL, second.content.decode())
        self.assertNotIn(SENTINEL, json.dumps(dict(self.client.session)))

    def test_status_endpoint_only_exposes_safe_fields(self):
        job = RemoteInstallJob.objects.create(
            target_hostname='lab-01', target_fqdn=COMPUTER['fqdn'],
            target_ad_dn=COMPUTER['distinguished_name'], requested_by=self.user,
            active_slot='global', runner_heartbeat_at=timezone.now())
        url = reverse('agent-install-job-status', args=[job.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertNotIn(SENTINEL, response.content.decode())
        self.assertNotIn('password', response.json())
        outsider = get_user_model().objects.create_user('status-outsider', password='synthetic')
        self.client.force_login(outsider)
        self.assertIn(self.client.get(url).status_code, (302, 403))
