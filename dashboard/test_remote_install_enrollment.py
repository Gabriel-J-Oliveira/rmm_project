import base64
import hashlib
import json
import os
import subprocess
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, Client
from django.utils import timezone
from agents.models import AgentDeploymentToken, AgentEnrollmentToken, AgentMachine, AgentRelease
from dashboard.models import RemoteInstallJob
from dashboard import remote_install as runner
from dashboard.test_remote_install import COMPUTER, READY, CONTRACT


class RemoteDeploymentEnrollmentTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user('deployment-admin', is_staff=True)
        self.release = AgentRelease.objects.create(id=CONTRACT['release_id'], version=CONTRACT['version'],
            channel=CONTRACT['channel'], package_url=CONTRACT['package_url'], sha256=CONTRACT['package_sha256'])
        self.job = RemoteInstallJob.objects.create(target_hostname=COMPUTER['hostname'],
            target_fqdn=COMPUTER['fqdn'], target_ad_dn=COMPUTER['distinguished_name'],
            requested_by=self.actor, status='RUNNING', active_slot='global')

    def enroll(self, token, **changes):
        payload = dict(enrollment_token=token, hostname=COMPUTER['hostname'], fqdn=COMPUTER['fqdn'],
            domain='control.local', machine_id='synthetic-deployment-machine', agent_version=CONTRACT['version'])
        payload.update(changes)
        return Client().post('/api/agent/enroll/', data=json.dumps(payload), content_type='application/json')

    def run_attempt(self, command):
        with ExitStack() as stack:
            for name, value in [('_ad_target', COMPUTER), ('validate_install_release', CONTRACT),
                                ('run_remote_install_preflight', READY)]:
                stack.enter_context(mock.patch.object(runner, name, return_value=value))
            stack.enter_context(mock.patch.object(runner.threading, 'Thread'))
            stack.enter_context(mock.patch.object(runner, '_remote_script', side_effect=command))
            stack.enter_context(mock.patch.object(runner, 'ENROLLMENT_TIMEOUT', 0.03))
            stack.enter_context(mock.patch.object(runner.time, 'sleep'))
            runner.run_remote_install(self.job.pk, 'synthetic-admin', 'synthetic-password')
        self.job.refresh_from_db()

    def test_real_enrollment_without_generic_token_completes_expected_deployment(self):
        self.assertEqual(AgentEnrollmentToken.objects.count(), 0)
        secrets = []
        def command(*args, **kwargs):
            if 'enrollment_token' in kwargs:
                token = kwargs['enrollment_token']; secrets.append(token)
                self.assertNotIn(token, args[3])
                response = self.enroll(token)
                self.assertEqual(response.status_code, 200, response.content)
                machine = AgentMachine.objects.get(machine_id='synthetic-deployment-machine')
                machine.status = 'online'; machine.last_seen_at = timezone.now(); machine.save()
                kwargs['on_event']('STARTED', None); kwargs['on_event']('FINISHED', 0)
            return 0
        self.run_attempt(command)
        deployment = AgentDeploymentToken.objects.get()
        self.assertEqual(self.job.status, 'COMPLETED')
        self.assertEqual(deployment.status, 'completed')
        self.assertEqual(deployment.endpoint_id, self.job.endpoint_id)
        self.assertEqual(deployment.release_id, self.release.pk)
        self.assertEqual(deployment.created_by_id, self.actor.pk)
        self.assertEqual(deployment.metadata['remote_install_job_id'], str(self.job.pk))
        self.assertLess((deployment.expires_at - deployment.created_at).total_seconds(), 3600)
        self.assertEqual(self.job.endpoint.agent_lifecycle_status, 'installed')
        self.assertNotIn(secrets[0], json.dumps(self.job.diagnostics))
        self.assertNotEqual(deployment.token_hash, secrets[0])
        self.assertEqual(self.enroll(secrets[0]).status_code, 403)

    def test_mismatched_target_rejected_without_endpoint_or_token_consumption(self):
        deployment, token = runner._prepare_deployment(self.job, CONTRACT)
        response = self.enroll(token, hostname='another', fqdn='another.control.local')
        self.assertEqual(response.status_code, 403)
        deployment.refresh_from_db()
        self.assertIsNone(deployment.used_at)
        self.assertFalse(AgentMachine.objects.exists())

    def test_safe_failure_invalidates_token_and_next_attempt_has_new_credential(self):
        secrets = []
        def command(*args, **kwargs):
            secrets.append(kwargs['enrollment_token'])
            kwargs['on_event']('NOT_STARTED', 26)
            return 26
        self.run_attempt(command)
        self.assertEqual(self.job.status, 'FAILED')
        self.assertEqual(self.job.diagnostics['safe_to_retry'], 'YES')
        self.assertFalse(AgentDeploymentToken.objects.get().can_be_used())
        self.job.status = 'RUNNING'; self.job.active_slot = 'global'; self.job.save()
        self.run_attempt(command)
        self.assertEqual(AgentDeploymentToken.objects.count(), 2)
        self.assertNotEqual(*secrets)

    def test_wrong_release_or_explicit_fqdn_cannot_consume_deployment(self):
        deployment, token = runner._prepare_deployment(self.job, CONTRACT)
        for changes in ({'agent_version': '0.1.0.7'}, {'fqdn': 'another.control.local'}):
            self.assertEqual(self.enroll(token, **changes).status_code, 403)
        deployment.refresh_from_db()
        self.assertIsNone(deployment.used_at)
        self.assertFalse(AgentMachine.objects.exists())

    def test_completion_api_cannot_bypass_remote_heartbeat_confirmation(self):
        deployment, token = runner._prepare_deployment(self.job, CONTRACT)
        self.assertEqual(self.enroll(token).status_code, 200)
        response = Client().post('/api/agent/deployments/complete/',
            data=json.dumps({'deployment_id': str(deployment.pk), 'status': 'completed',
                             'health_check_confirmed': True}), content_type='application/json')
        self.assertEqual(response.status_code, 409)
        deployment.refresh_from_db()
        self.assertEqual(deployment.status, deployment.STATUS_INSTALLING)
        self.assertIsNone(deployment.completed_at)

    def test_target_gate_failure_creates_no_credential(self):
        with mock.patch.object(runner, '_ad_target', side_effect=runner.InstallFailure('TARGET_CHANGED')), \
                mock.patch.object(runner.threading, 'Thread'):
            runner.run_remote_install(self.job.pk, 'synthetic-admin', 'synthetic-password')
        self.assertFalse(AgentDeploymentToken.objects.exists())

    def test_unknown_outcome_remains_blocked_and_credential_revoked(self):
        def command(*args, **kwargs):
            kwargs['on_event']('STARTED', None)
            raise runner.InstallFailure('REMOTE_COMMAND_TIMEOUT')
        self.run_attempt(command)
        self.assertEqual(self.job.status, 'OUTCOME_UNKNOWN')
        self.assertFalse(runner.job_is_retry_safe(self.job))
        self.assertFalse(AgentDeploymentToken.objects.get().can_be_used())

    def test_missing_enrollment_never_announces_success(self):
        def command(*args, **kwargs):
            if kwargs.get('on_event'):
                kwargs['on_event']('STARTED', None); kwargs['on_event']('FINISHED', 0)
            return 0
        self.run_attempt(command)
        self.assertEqual(self.job.status, 'INSTALLED_UNVERIFIED')
        self.assertIsNone(AgentDeploymentToken.objects.get().completed_at)


class EnrollmentStdinTests(SimpleTestCase):
    def test_winrm_credential_is_data_only_with_short_static_launcher(self):
        token = 'deploy_' + 'SENTINEL_73192' * 4
        protocol = mock.Mock()
        protocol.open_shell.return_value = 'shell'; protocol.run_command.return_value = 'command'
        protocol.get_command_output_raw.return_value = (b'', b'', 0, True)
        script = runner._installer_script(CONTRACT)
        with mock.patch.object(runner, '_new_winrm_protocol', return_value=protocol):
            runner._remote_script(COMPUTER['fqdn'], 'admin', 'password', script, 30, enrollment_token=token)
        args = protocol.run_command.call_args.args[2]
        launcher = base64.b64decode(args[-1]).decode('utf-16-le')
        self.assertNotIn(token, launcher)
        self.assertLess(len(' '.join(args)), 1024)
        source, credential = protocol.send_command_input.call_args.args[2].splitlines()
        self.assertEqual(base64.b64decode(source).decode('ascii'), script)
        self.assertEqual(credential.decode('ascii'), token)
        self.assertNotIn(token, script)
        self.assertIn('$process.StandardInput.WriteLine($enrollmentToken)', script)

    @skipUnless(os.name == 'nt', 'Requires Windows PowerShell, synthetic child only')
    def test_real_static_launcher_reads_code_and_credential_separately(self):
        token = 'deploy_' + 'SENTINEL_73192' * 4
        digest = hashlib.sha256(token.encode()).hexdigest()
        script = "$token=[Console]::In.ReadLine(); $h=[BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($token))).Replace('-','').ToLowerInvariant(); if ($h -ne '" + digest + "') { exit 99 }; Write-Output 'LAUNCHER_PASS'"
        protocol = mock.Mock()
        protocol.get_command_output_raw.return_value = (b'', b'', 0, True)
        with mock.patch.object(runner, '_new_winrm_protocol', return_value=protocol):
            runner._remote_script(COMPUTER['fqdn'], 'admin', 'password', script, 30, enrollment_token=token)
        arguments = protocol.run_command.call_args.args[2]
        payload = protocol.send_command_input.call_args.args[2]
        result = subprocess.run(['powershell', *arguments], input=payload, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'LAUNCHER_PASS', result.stdout)
        self.assertNotIn(token.encode(), result.stdout + result.stderr)

    @skipUnless(os.name == 'nt', 'Requires Windows PowerShell, synthetic child only')
    def test_real_powershell_child_receives_token_without_script_or_argv_secret(self):
        token = 'deploy_' + 'SENTINEL_73192' * 4
        digest = hashlib.sha256(token.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / 'installer.ps1'
            fake.write_text("param([string]$EnrollmentToken)\n$h=[BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($EnrollmentToken))).Replace('-','').ToLowerInvariant()\nif ($h -ne '" + digest + "') { exit 99 }; Write-Output 'HANDOFF_PASS'; exit 0", encoding='ascii')
            script = runner._installer_script(CONTRACT)
            child = script.split("$childScript = @'\n", 1)[1].split("\n'@", 1)[0]
            child = child.replace('__INSTALLER_PATH__', str(fake).replace("'", "''")).replace('__TRUST_PATH__', 'synthetic')
            args = ['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', base64.b64encode(child.encode('utf-16-le')).decode()]
            self.assertNotIn(token, child + repr(args) + fake.read_text())
            result = subprocess.run(args, input=token+'\n', text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('HANDOFF_PASS', result.stdout)
            self.assertNotIn(token, result.stdout + result.stderr)
