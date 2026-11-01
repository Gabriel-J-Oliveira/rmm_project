from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from agents.models import AgentMachine
from agents.services import record_collection, record_heartbeat
from dashboard.models import RemoteInstallJob
from dashboard.remote_install_preflight import ProbeFailure
from dashboard.winrm_observability import check_connectivity


class WinRmObservabilityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.machine, _ = AgentMachine.create_with_token(hostname='LAB', machine_id='lab-id')
        self.machine.fqdn = 'lab.example.local'; self.machine.domain = 'example.local'
        self.machine.last_ip = '10.0.0.10'; self.machine.status = AgentMachine.STATUS_OFFLINE
        self.machine.save()
        self.actor = get_user_model().objects.create_user('winrm-staff', is_staff=True)
        self.computer = {'fqdn': self.machine.fqdn, 'enabled': True, 'sid': 'S-1-5-21-1-2-3-1001'}
        self.patches = mock.patch.multiple('dashboard.winrm_observability', discover_ad_computers=mock.DEFAULT,
            resolve_computer_dns=mock.DEFAULT, _matching_endpoint=mock.DEFAULT, _tcp_available=mock.DEFAULT,
            _winrm_ca_trust_path=mock.DEFAULT)
        self.m = self.patches.start(); self.addCleanup(self.patches.stop)
        self.m['discover_ad_computers'].return_value = [self.computer]
        self.m['resolve_computer_dns'].return_value = {self.machine.fqdn: {'dns_status':'RESOLVED','ipv4_addresses':['10.0.0.10']}}
        self.m['_matching_endpoint'].return_value = self.machine
        self.m['_tcp_available'].return_value = True
        self.m['_winrm_ca_trust_path'].return_value = 'test-ca'
        self.domain = mock.patch('dashboard.winrm_observability.ad_ldap.ad_config', return_value={'DOMAIN':'example.local'})
        self.domain.start(); self.addCleanup(self.domain.stop)

    def test_tcp_unavailable_does_not_probe_wsman(self):
        self.m['_tcp_available'].side_effect = ProbeFailure('TCP_5986_TIMEOUT')
        with mock.patch('dashboard.winrm_observability.requests.Session') as session:
            result = check_connectivity(self.machine, self.actor)
        self.assertEqual((result['tcp'], result['wsman'], result['authentication']), ('unavailable','not_checked','not_checked'))
        session.assert_not_called()
        self.assertEqual(RemoteInstallJob.objects.get().diagnostics['result']['error'], 'TCP_5986_TIMEOUT')

    def test_tcp_open_does_not_imply_wsman(self):
        with mock.patch('dashboard.winrm_observability.requests.Session') as factory:
            response = factory.return_value.__enter__.return_value.post.return_value.__enter__.return_value
            response.status_code = 401
            result = check_connectivity(self.machine, self.actor)
        self.assertEqual((result['tcp'], result['wsman'], result['authentication']), ('reachable','not_verified','not_checked'))
        self.assertEqual(result['error'], 'HTTP_AUTH_CHALLENGE')

    def test_tcp_open_with_no_wsman_response(self):
        with mock.patch('dashboard.winrm_observability.requests.Session') as factory:
            response = factory.return_value.__enter__.return_value.post.return_value.__enter__.return_value
            response.status_code = 404
            result = check_connectivity(self.machine, self.actor)
        self.assertEqual((result['tcp'], result['wsman']), ('reachable', 'unavailable'))

    def test_wsman_identify_and_limit(self):
        with mock.patch('dashboard.winrm_observability.requests.Session') as factory:
            response = factory.return_value.__enter__.return_value.post.return_value.__enter__.return_value
            response.status_code = 200
            response.raw.read1.side_effect = [b'<IdentifyResponse xmlns="http://schemas.dmtf.org/wbem/wsman/identity/1/wsmanidentity.xsd"/>', b'']
            result = check_connectivity(self.machine, self.actor)
        self.assertEqual(result['wsman'], 'reachable')
        with self.assertRaisesMessage(ProbeFailure, 'CHECK_RATE_LIMITED'):
            check_connectivity(self.machine, self.actor)

    def test_unverified_target_is_not_probed(self):
        self.m['resolve_computer_dns'].return_value[self.machine.fqdn]['ipv4_addresses'] = ['127.0.0.1']
        self.assertEqual(check_connectivity(self.machine, self.actor)['error'], 'TARGET_ADDRESS_UNVERIFIED')
        self.m['_tcp_available'].assert_not_called()

    def test_local_state_survives_heartbeat_and_renders_as_stale(self):
        state = {'version':1, 'result':'partial', 'service':{'state':'Stopped','startup':'Auto'},
                 'listeners':[], 'profiles':['DomainAuthenticated'], 'firewall':[], 'errors':['listener_unavailable']}
        snapshot = record_collection(self.machine, 'full_inventory', {'machine_id':self.machine.machine_id,
            'collected_at':timezone.now().isoformat(), 'collection_scope':'scheduled', 'winrm':state})
        heartbeat = record_heartbeat(self.machine, {'machine_id':self.machine.machine_id}, {'machine_id':self.machine.machine_id})
        self.assertEqual(heartbeat.raw_payload['collections']['winrm']['data'], state)
        self.machine.status = AgentMachine.STATUS_OFFLINE; self.machine.save(update_fields=['status'])
        client = self.client; client.force_login(self.actor)
        response = client.get(reverse('endpoint-detail', args=[self.machine.pk]), secure=True)
        self.assertContains(response, 'informação desatualizada')
        self.assertContains(response, 'Stopped')
        self.assertContains(response, 'Verificar conectividade WinRM')

    def test_view_requires_staff_and_https(self):
        url = reverse('agent-winrm-check', args=[self.machine.pk])
        self.client.force_login(self.actor)
        self.assertEqual(self.client.post(url).status_code, 403)
        user = get_user_model().objects.create_user('ordinary')
        self.client.force_login(user)
        self.assertIn(self.client.post(url, secure=True).status_code, (302, 403))
        self.m['_tcp_available'].assert_not_called()

    def test_authorized_post_runs_check_without_target_parameters(self):
        url = reverse('agent-winrm-check', args=[self.machine.pk])
        self.client.force_login(self.actor)
        self.m['_tcp_available'].side_effect = ProbeFailure('TCP_5986_TIMEOUT')
        response = self.client.post(url, {'address':'127.0.0.1', 'port':'22'}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['tcp'], 'unavailable')
        self.m['_tcp_available'].assert_called_once_with('10.0.0.10', 5986, timeout=2)

    def test_preflight_target_only_allows_the_expected_managed_endpoint(self):
        from dashboard import remote_install_preflight as preflight
        with mock.patch.object(preflight.ad_ldap, 'ad_config', return_value={'DOMAIN':'example.local'}), \
             mock.patch.object(preflight, 'discover_ad_computers', return_value=[self.computer]), \
             mock.patch.object(preflight, '_machine_maps', return_value={}), \
             mock.patch.object(preflight, '_match_machine', return_value=(self.machine, 'MANAGED', 'FQDN')):
            with self.assertRaisesMessage(ProbeFailure, 'TARGET_MANAGED_OR_CONFLICT'):
                preflight._ad_target(self.machine.fqdn)
            self.assertEqual(preflight._ad_target(self.machine.fqdn, expected_machine=self.machine), self.computer)
            other, _ = AgentMachine.create_with_token(hostname='OTHER', machine_id='other-id')
            with self.assertRaisesMessage(ProbeFailure, 'TARGET_MANAGED_OR_CONFLICT'):
                preflight._ad_target(self.machine.fqdn, expected_machine=other)
