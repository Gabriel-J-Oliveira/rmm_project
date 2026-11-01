from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from agents.models import AgentMachine, AgentJob, InventorySnapshot
from dashboard.agent_recovery import recover_offline_agent
from dashboard.remote_install import InstallFailure
from dashboard.models import RemoteInstallJob


class OfflineRecoveryTests(TestCase):
    def setUp(self):
        self.machine, _ = AgentMachine.create_with_token(hostname='FIXTURE', machine_id='fixture-machine')
        self.machine.fqdn = 'fixture.example.local'; self.machine.domain = 'example.local'
        self.machine.status = AgentMachine.STATUS_OFFLINE
        self.machine.last_seen_at = timezone.now() - timedelta(hours=1); self.machine.agent_version = '1.0'
        self.machine.save()
        self.actor = get_user_model().objects.create_user('recovery-staff', is_staff=True)
        self.computer = {'hostname':'FIXTURE','fqdn':self.machine.fqdn,'distinguished_name':'CN=FIXTURE,DC=example,DC=local','sid':'S-1-5-21-1-2-3-1001'}
        self.preflight = {'checks':{'AUTHENTICATION':{'status':'PASS'}, 'NIGHTOWL_ABSENCE':{'status':'FAIL'}},
            'nightowl_absence':{'correlation':'MANAGED','service_present':True},
            'target':{'winrm_transport':'HTTPS','winrm_port':5986,'selected_ip':'10.0.0.10',
                      'endpoint_attempts':[{'ip':'10.0.0.10','tcp_status':'OPEN'}]}}
        self.stack = mock.patch.multiple('dashboard.agent_recovery', _ad_target=mock.DEFAULT,
            _matching_endpoint=mock.DEFAULT, run_remote_install_preflight=mock.DEFAULT,
            _transport_selection=mock.DEFAULT, _remote_script_candidates=mock.DEFAULT)
        self.mocks = self.stack.start(); self.addCleanup(self.stack.stop)
        self.mocks['_ad_target'].return_value = self.computer
        self.mocks['_matching_endpoint'].return_value = self.machine
        self.mocks['run_remote_install_preflight'].return_value = self.preflight
        self.mocks['_transport_selection'].return_value = {'label':'HTTPS','port':5986}

    def call(self, restart=False):
        return recover_offline_agent(self.machine, self.actor, 'fixture-admin', 'fixture-password', restart)

    def test_online_does_not_probe(self):
        self.machine.status = AgentMachine.STATUS_ONLINE
        with self.assertRaisesMessage(InstallFailure, 'RECOVERY_ENDPOINT_NOT_OFFLINE'): self.call()
        self.mocks['run_remote_install_preflight'].assert_not_called()

    def test_start_and_fresh_heartbeat(self):
        def run(*args, **kwargs):
            self.assertIn('Start-Service', args[3]); self.assertNotIn('fixture-password', args[3])
            self.assertNotIn('Set-Content', args[3]); self.assertNotIn('Remove-Item', args[3])
            InventorySnapshot.objects.create(machine=self.machine, hostname=self.machine.hostname, collected_at=timezone.now(),
                raw_payload={'snapshot_source':'heartbeat','machine_id':self.machine.machine_id,'agent_version':self.machine.agent_version})
            return 0, '10.0.0.10', []
        self.mocks['_remote_script_candidates'].side_effect = run
        before = self.machine.agent_token_hash
        self.assertEqual(self.call()['status'], 'RECOVERED')
        self.machine.refresh_from_db(); self.assertEqual(self.machine.agent_token_hash, before)
        self.assertEqual(self.machine.machine_id, 'fixture-machine')

    def test_running_requires_explicit_restart(self):
        self.mocks['_remote_script_candidates'].return_value = (10, '10.0.0.10', [])
        with self.assertRaisesMessage(InstallFailure, 'RECOVERY_RESTART_CONFIRMATION_REQUIRED'): self.call()
        self.assertNotIn('Restart-Service', self.mocks['_remote_script_candidates'].call_args.args[3])
        with self.assertRaisesMessage(InstallFailure, 'RECOVERY_RESTART_CONFIRMATION_REQUIRED'): self.call(True)
        self.assertIn('Restart-Service', self.mocks['_remote_script_candidates'].call_args.args[3])

    def test_explicit_restart_waits_for_matching_new_heartbeat(self):
        def run(*args, **kwargs):
            self.assertIn('Restart-Service', args[3])
            InventorySnapshot.objects.create(machine=self.machine, hostname=self.machine.hostname, collected_at=timezone.now(),
                raw_payload={'snapshot_source':'heartbeat','machine_id':self.machine.machine_id,'agent_version':self.machine.agent_version})
            return 0, '10.0.0.10', []
        self.mocks['_remote_script_candidates'].side_effect = run
        self.assertEqual(self.call(True)['status'], 'RECOVERED')

    def test_failures_and_timeout(self):
        self.mocks['_remote_script_candidates'].return_value = (31, '10.0.0.10', [])
        with self.assertRaisesMessage(InstallFailure, 'RECOVERY_IDENTITY_CONFLICT'): self.call()
        self.preflight['checks']['AUTHENTICATION'] = {'status':'FAIL','code':'WINRM_UNAVAILABLE'}
        with self.assertRaisesMessage(InstallFailure, 'WINRM_UNAVAILABLE'): self.call()
        self.preflight['checks']['AUTHENTICATION'] = {'status':'PASS'}
        self.preflight['nightowl_absence']['service_present'] = False
        with self.assertRaisesMessage(InstallFailure, 'RECOVERY_SERVICE_MISSING'): self.call()
        self.preflight['nightowl_absence']['service_present'] = True
        self.mocks['_remote_script_candidates'].return_value = (0, '10.0.0.10', [])
        old = timezone.now() - timedelta(hours=1)
        InventorySnapshot.objects.create(machine=self.machine, hostname=self.machine.hostname, collected_at=old, received_at=old,
            raw_payload={'snapshot_source':'heartbeat','machine_id':self.machine.machine_id,'agent_version':self.machine.agent_version})
        with mock.patch('dashboard.agent_recovery.time.monotonic', side_effect=[0,16]):
            self.assertEqual(self.call()['status'], 'HEARTBEAT_TIMEOUT')

    def test_conflict_and_concurrent_attempt_blocked(self):
        self.mocks['_matching_endpoint'].return_value = None
        with self.assertRaisesMessage(InstallFailure, 'RECOVERY_IDENTITY_CONFLICT'): self.call()
        self.mocks['_matching_endpoint'].return_value = self.machine
        RemoteInstallJob.objects.create(target_hostname='OTHER', target_fqdn='other.example.local', requested_by=self.actor, active_slot='global')
        with self.assertRaisesMessage(InstallFailure, 'INSTALL_ALREADY_RUNNING'): self.call()
        self.mocks['run_remote_install_preflight'].assert_not_called()

    def test_ambiguous_disconnect_reserves_slot(self):
        self.mocks['_remote_script_candidates'].side_effect = InstallFailure('WINRM_DISCONNECTED', command_attempted=True)
        with self.assertRaisesMessage(InstallFailure, 'RECOVERY_OUTCOME_UNKNOWN'): self.call(True)
        job = RemoteInstallJob.objects.get(active_slot='global')
        self.assertEqual(job.status, 'OUTCOME_UNKNOWN')

    def test_http_requires_staff_and_explicit_payload(self):
        client = Client(); client.force_login(self.actor)
        url = reverse('agent-offline-recovery', args=[self.machine.pk])
        self.assertEqual(client.post(url, {'username':'x','password':'x','restart_running':False}, content_type='application/json').status_code, 403)
        nonstaff = get_user_model().objects.create_user('nonstaff')
        client.force_login(nonstaff)
        self.assertIn(client.post(url, {}, content_type='application/json', secure=True).status_code, (302, 403))

    def test_repair_required_is_not_reported_as_success(self):
        AgentJob.objects.create(endpoint=self.machine, job_type=AgentJob.TYPE_UPDATE_AGENT,
            status=AgentJob.STATUS_FAILED, result={'details':{'reason':'repair_required'}})
        client = Client(); client.force_login(self.actor)
        url = reverse('agent-recovery-status', args=[self.machine.pk])
        result = client.get(url, secure=True).json()
        self.assertEqual(result['status'], 'REPAIR_REQUIRED')
        self.assertEqual(result['recommendation'], 'RECOVER_COMMUNICATION_OR_INTERVENTION')
        self.machine.status = AgentMachine.STATUS_ONLINE; self.machine.save(update_fields=['status'])
        self.assertEqual(client.get(url, secure=True).json()['recommendation'], 'AGENT_REPAIR')

    def test_authorized_http_recovery_does_not_persist_password(self):
        self.mocks['_remote_script_candidates'].return_value = (10, '10.0.0.10', [])
        client = Client(); client.force_login(self.actor)
        response = client.post(reverse('agent-offline-recovery', args=[self.machine.pk]),
            {'username':'fixture-admin','password':'fixture-password','restart_running':False}, content_type='application/json', secure=True)
        self.assertEqual(response.json()['error_code'], 'RECOVERY_RESTART_CONFIRMATION_REQUIRED')
        self.assertNotIn('fixture-password', response.content.decode())
        self.assertNotIn('fixture-password', str(list(RemoteInstallJob.objects.values())))
