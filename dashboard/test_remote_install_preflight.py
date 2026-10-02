import json
import logging
from unittest import mock

from winrm.exceptions import WinRMOperationTimeoutError

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from agents.models import AgentMachine
from dashboard import remote_install_preflight as preflight
from dashboard.test_ad_install_discovery import computer


SENTINEL = 'SUPER_SECRET_TEST_PASSWORD_91827'
DOMAIN_CONFIG = {'DOMAIN': 'control.local'}
REMOTE_OK = {
    'hostname': 'lab-01', 'windows_name': 'Windows 11 Pro', 'build': 22631,
    'architecture': 'x64', 'powershell_major': 5, 'admin': True,
    'nightowl_service_present': False, 'nightowl_directory_present': False,
}
DNS_OK = {'lab-01.control.local': {'dns_status': 'RESOLVED', 'primary_ipv4': '192.0.2.10'}}


@override_settings(AD_AUTH_CONFIG=DOMAIN_CONFIG)
class RemoteInstallPreflightTests(TestCase):
    def setUp(self):
        self.ad = mock.patch.object(preflight, 'discover_ad_computers', return_value=[computer()])
        self.dns = mock.patch.object(preflight, 'resolve_computer_dns', return_value=DNS_OK)
        self.tcp = mock.patch.object(preflight, '_tcp_available', return_value=True)
        self.remote = mock.patch.object(preflight, '_winrm_probe', return_value=REMOTE_OK)
        self.ad_mock = self.ad.start()
        self.dns_mock = self.dns.start()
        self.tcp_mock = self.tcp.start()
        self.remote_mock = self.remote.start()
        self.addCleanup(self.ad.stop)
        self.addCleanup(self.dns.stop)
        self.addCleanup(self.tcp.stop)
        self.addCleanup(self.remote.stop)

    def run_probe(self):
        return preflight.run_remote_install_preflight('LAB-01.CONTROL.LOCAL.', 'CONTROL\\Admin', SENTINEL)

    def test_ready_requires_every_check_and_returns_no_password(self):
        result = self.run_probe()
        self.assertEqual(result['status'], 'READY')
        self.assertTrue(all(item['status'] == 'PASS' for item in result['checks'].values()))
        self.assertNotIn(SENTINEL, json.dumps(result))
        self.remote_mock.assert_called_once_with('lab-01.control.local', 'CONTROL\\Admin', SENTINEL)

    def test_disabled_or_missing_target_never_connects(self):
        for items in ([computer(enabled=False)], []):
            with self.subTest(items=items):
                self.ad_mock.return_value = items
                self.assertEqual(self.run_probe()['checks']['TARGET']['status'], 'FAIL')
                self.dns_mock.assert_not_called()
                self.tcp_mock.assert_not_called()
                self.remote_mock.assert_not_called()

    def test_managed_and_conflict_never_connect(self):
        for count in (1, 2):
            AgentMachine.objects.create(hostname='lab-01', domain='control.local',
                                        fqdn='lab-01.control.local', agent_token_hash=f'{count:064x}')
            self.assertEqual(self.run_probe()['checks']['TARGET']['status'], 'FAIL')
            self.remote_mock.assert_not_called()

    def test_terminal_lifecycle_is_candidate(self):
        from agents.models import AgentMachine
        machine = AgentMachine.objects.create(hostname='lab-01', domain='control.local',
                                              fqdn='lab-01.control.local', agent_token_hash='a' * 64)
        machine.agent_lifecycle_status = 'uninstalled'
        machine.save(update_fields=['agent_lifecycle_status'])
        self.assertEqual(self.run_probe()['status'], 'READY')

    def test_dns_and_transport_fail_closed(self):
        self.dns_mock.return_value = {}
        self.assertEqual(self.run_probe()['checks']['DNS']['code'], 'DNS_UNRESOLVED')
        self.remote_mock.assert_not_called()
        self.dns_mock.return_value = DNS_OK
        self.tcp_mock.return_value = False
        self.assertEqual(self.run_probe()['checks']['REMOTE_TRANSPORT']['code'], 'WINRM_UNAVAILABLE')
        self.remote_mock.assert_not_called()

    def test_authentication_and_timeout_fail_closed(self):
        for code, check in [('AUTHENTICATION_FAILED', 'AUTHENTICATION'),
                            ('REMOTE_TIMEOUT', 'REMOTE_TRANSPORT')]:
            with self.subTest(code=code):
                self.remote_mock.side_effect = preflight.ProbeFailure(code)
                result = self.run_probe()
                self.assertEqual(result['status'], 'NOT_READY')
                self.assertEqual(result['checks'][check]['code'], code)

    def test_admin_windows_and_installation_detection(self):
        for changes, check in [({'admin': False}, 'ADMIN_PRIVILEGE'),
                               ({'architecture': 'x86'}, 'WINDOWS_COMPATIBILITY'),
                               ({'hostname': 'other'}, 'WINDOWS_COMPATIBILITY'),
                               ({'nightowl_service_present': True}, 'NIGHTOWL_ABSENCE'),
                               ({'nightowl_directory_present': True}, 'NIGHTOWL_ABSENCE'),
                               ({'nightowl_directory_present': 'false'}, 'NIGHTOWL_ABSENCE')]:
            with self.subTest(changes=changes):
                self.remote_mock.return_value = {**REMOTE_OK, **changes}
                result = self.run_probe()
                self.assertEqual(result['status'], 'NOT_READY')
                self.assertEqual(result['checks'][check]['status'], 'FAIL')

    def test_backend_correlation_is_rechecked_before_ready(self):
        with mock.patch.object(preflight, '_match_machine', side_effect=[
            (None, 'UNMANAGED', 'NONE'), (None, 'MANAGED', 'FQDN'),
        ]):
            result = self.run_probe()
        self.assertEqual(result['status'], 'NOT_READY')
        self.assertEqual(result['checks']['NIGHTOWL_ABSENCE']['code'], 'TARGET_MANAGED_OR_CONFLICT')

    def test_route_auth_csrf_post_and_ephemeral_password(self):
        url = reverse('agent-install-ad-preflight')
        body = json.dumps({'fqdn': 'lab-01.control.local', 'username': 'Admin', 'password': SENTINEL})
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(url, data=body, content_type='application/json').status_code, 302)
        user = get_user_model().objects.create_user(username='preflight-staff', password='synthetic-test-only', is_staff=True)
        client.force_login(user)
        self.assertEqual(client.get(url).status_code, 405)
        self.assertEqual(client.post(url, data=body, content_type='application/json').status_code, 403)
        client.cookies['csrftoken'] = 'a' * 32
        with CaptureQueriesContext(connection) as queries, self.assertLogs(level=logging.WARNING) as logs:
            response = client.post(url, data=body, content_type='application/json',
                                   HTTP_X_CSRFTOKEN='a' * 32)
            logging.getLogger(__name__).warning('preflight test completed')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'READY')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertNotIn(SENTINEL, response.content.decode())
        self.assertNotIn(SENTINEL, '\n'.join(logs.output))
        self.assertTrue(all(query['sql'].lstrip().upper().startswith('SELECT') for query in queries))
        self.assertNotIn(SENTINEL, '\n'.join(query['sql'] for query in queries))

    def test_route_rejects_nonstaff_and_does_not_probe(self):
        user = get_user_model().objects.create_user(username='preflight-user', password='synthetic-test-only')
        client = Client()
        client.force_login(user)
        response = client.post(reverse('agent-install-ad-preflight'),
                               data=json.dumps({'fqdn': 'lab-01.control.local', 'username': 'Admin', 'password': SENTINEL}),
                               content_type='application/json')
        self.assertIn(response.status_code, (302, 403))
        self.ad_mock.assert_not_called()
        self.remote_mock.assert_not_called()


class WinRMProbeTests(TestCase):
    @mock.patch('winrm.protocol.Protocol')
    def test_https_tls_timeouts_read_only_command_and_cleanup(self, protocol_class):
        protocol = protocol_class.return_value
        protocol.open_shell.return_value = 'synthetic-shell'
        protocol.run_command.return_value = 'synthetic-command'
        protocol.get_command_output_raw.return_value = (json.dumps(REMOTE_OK).encode(), b'synthetic-stderr', 0, True)
        result = preflight._winrm_probe('lab-01.control.local', 'SyntheticAdmin', SENTINEL)
        self.assertEqual(result, REMOTE_OK)
        kwargs = protocol_class.call_args.kwargs
        self.assertEqual(kwargs['endpoint'], 'https://lab-01.control.local:5986/wsman')
        self.assertEqual(kwargs['transport'], 'ntlm')
        self.assertEqual(kwargs['server_cert_validation'], 'validate')
        self.assertEqual(kwargs['proxy'], None)
        self.assertEqual(kwargs['password'], SENTINEL)
        self.assertGreater(kwargs['read_timeout_sec'], kwargs['operation_timeout_sec'])
        command = protocol.run_command.call_args
        self.assertEqual(command.args[1], 'powershell.exe')
        self.assertIn('-EncodedCommand', command.args[2])
        self.assertNotIn(SENTINEL, str(command))
        protocol.cleanup_command.assert_called_once()
        protocol.close_shell.assert_called_once()

    @mock.patch('winrm.protocol.Protocol')
    def test_timeout_and_raw_error_never_escape(self, protocol_class):
        protocol = protocol_class.return_value
        protocol.open_shell.return_value = 'synthetic-shell'
        protocol.run_command.return_value = 'synthetic-command'
        protocol.get_command_output_raw.side_effect = WinRMOperationTimeoutError(SENTINEL)
        with mock.patch.object(preflight, 'WINRM_DEADLINE_SECONDS', 0.001):
            with self.assertRaises(preflight.ProbeFailure) as raised:
                preflight._winrm_probe('lab-01.control.local', 'SyntheticAdmin', SENTINEL)
        self.assertEqual(raised.exception.code, 'REMOTE_TIMEOUT')
        self.assertNotIn(SENTINEL, str(raised.exception))
        protocol.cleanup_command.assert_called_once()
        protocol.close_shell.assert_called_once()
