import json
import logging
import os
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from base64 import b64encode
from tempfile import TemporaryDirectory
from unittest import mock

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from requests.adapters import BaseAdapter
from requests.models import Response
from requests_ntlm import HttpNtlmAuth
from winrm.exceptions import WinRMOperationTimeoutError

from django.contrib.auth import get_user_model
from django.core.cache import cache
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
DNS_OK = {'lab-01.control.local': {'dns_status': 'RESOLVED', 'primary_ipv4': '192.168.104.20',
                                   'ipv4_addresses': ['192.168.104.20']}}


class SyntheticRaw:
    def release_conn(self):
        pass


class SyntheticWinRMAdapter(BaseAdapter):
    def __init__(self, redirect_status=None, redirect_url=None):
        self.redirect_status = redirect_status
        self.redirect_url = redirect_url
        self.requests = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        response = Response()
        response.request = request
        response.url = request.url
        response.raw = SyntheticRaw()
        response.connection = self
        response._content = b''
        if self.redirect_status:
            response.status_code = self.redirect_status
            response.headers['Location'] = self.redirect_url
        elif len(self.requests) == 1:
            response.status_code = 401
            response.headers['WWW-Authenticate'] = 'NTLM'
        elif len(self.requests) == 2:
            response.status_code = 401
            response.headers['WWW-Authenticate'] = 'NTLM ' + b64encode(b'synthetic-challenge').decode()
        else:
            response.status_code = 200
        return response

    def close(self):
        pass


@override_settings(AD_AUTH_CONFIG=DOMAIN_CONFIG, WINRM_CA_TRUST_PATH=requests.certs.where())
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

    @override_settings(WINRM_CA_TRUST_PATH='')
    def test_invalid_ca_bundle_fails_before_tcp_or_authentication(self):
        result = self.run_probe()
        self.assertEqual(result['checks']['REMOTE_TRANSPORT']['code'], 'WINRM_CA_TRUST_INVALID')
        self.tcp_mock.assert_not_called()
        self.remote_mock.assert_not_called()
        self.assertNotIn(SENTINEL, json.dumps(result))

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

    def test_unsafe_dns_addresses_never_connect(self):
        unsafe = ('127.0.0.1', '127.20.30.40', '0.0.0.0', '169.254.169.254',
                  '224.0.0.1', '255.255.255.255', '240.0.0.1', '192.0.2.1', 'not-an-ip')
        for address in unsafe:
            with self.subTest(address=address):
                self.dns_mock.return_value = {'lab-01.control.local': {
                    'dns_status': 'RESOLVED', 'primary_ipv4': address, 'ipv4_addresses': [address],
                }}
                result = self.run_probe()
                self.assertEqual(result['status'], 'NOT_READY')
                self.assertEqual(result['checks']['DNS']['code'], 'UNSAFE_TARGET_ADDRESS')
                self.tcp_mock.assert_not_called()
                self.remote_mock.assert_not_called()

    def test_rfc1918_addresses_remain_eligible(self):
        for address in ('192.168.104.2', '192.168.100.202', '10.10.10.10', '172.16.10.10'):
            with self.subTest(address=address):
                self.dns_mock.return_value = {'lab-01.control.local': {
                    'dns_status': 'RESOLVED', 'primary_ipv4': address, 'ipv4_addresses': [address],
                }}
                self.assertEqual(self.run_probe()['status'], 'READY')
                self.tcp_mock.assert_called_with(address)
                self.tcp_mock.reset_mock()
                self.remote_mock.reset_mock()

    def test_mixed_safe_and_unsafe_dns_fails_closed(self):
        self.dns_mock.return_value = {'lab-01.control.local': {
            'dns_status': 'RESOLVED', 'primary_ipv4': '192.168.104.20',
            'ipv4_addresses': ['192.168.104.20', '127.0.0.1'],
        }}
        self.assertEqual(self.run_probe()['checks']['DNS']['code'], 'UNSAFE_TARGET_ADDRESS')
        self.tcp_mock.assert_not_called()
        self.remote_mock.assert_not_called()

    def test_browser_ip_is_rejected(self):
        user = get_user_model().objects.create_user(username='preflight-ip-test', password='synthetic-test-only', is_staff=True)
        client = Client()
        client.force_login(user)
        response = client.post(reverse('agent-install-ad-preflight'), data=json.dumps({
            'fqdn': 'lab-01.control.local', 'ip': '127.0.0.1', 'url': 'http://localhost/',
            'username': 'Admin', 'password': SENTINEL,
        }), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.tcp_mock.assert_not_called()
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
        with CaptureQueriesContext(connection) as queries, self.assertLogs(level=logging.WARNING) as logs, \
                mock.patch.object(cache, 'set') as cache_set:
            response = client.post(url, data=body, content_type='application/json',
                                   HTTP_X_CSRFTOKEN='a' * 32)
            logging.getLogger(__name__).warning('preflight test completed')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'READY')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertNotIn(SENTINEL, response.content.decode())
        self.assertNotIn(SENTINEL, '\n'.join(logs.output))
        self.assertNotIn(SENTINEL, repr(dict(client.session)))
        cache_set.assert_not_called()
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


@override_settings(WINRM_CA_TRUST_PATH=requests.certs.where())
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
        self.assertEqual(kwargs['ca_trust_path'], os.path.realpath(requests.certs.where()))
        self.assertEqual(kwargs['proxy'], None)
        self.assertEqual(kwargs['password'], SENTINEL)
        self.assertGreater(kwargs['read_timeout_sec'], kwargs['operation_timeout_sec'])
        command = protocol.run_command.call_args
        self.assertEqual(command.args[1], 'powershell.exe')
        self.assertIn('-EncodedCommand', command.args[2])
        self.assertNotIn(SENTINEL, str(command))
        protocol.cleanup_command.assert_called_once()
        protocol.close_shell.assert_called_once()

    def test_untrusted_certificate_and_hostname_mismatch_fail_closed(self):
        for reason in ('untrusted certificate', 'hostname mismatch'):
            with self.subTest(reason=reason), mock.patch('winrm.protocol.Protocol') as protocol_class:
                protocol_class.return_value.open_shell.side_effect = ssl.SSLError(reason + SENTINEL)
                with self.assertRaises(preflight.ProbeFailure) as raised:
                    preflight._winrm_probe('lab-01.control.local', 'SyntheticAdmin', SENTINEL)
                self.assertEqual(raised.exception.code, 'WINRM_UNAVAILABLE')
                self.assertNotIn(SENTINEL, str(raised.exception))
                protocol_class.return_value.run_command.assert_not_called()

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


@override_settings(WINRM_CA_TRUST_PATH=requests.certs.where())
class WinRMTrustTests(TestCase):
    def test_dedicated_public_pem_bundle_is_used_without_network(self):
        with TemporaryDirectory() as directory:
            bundle = Path(directory) / 'corporate-current.pem'
            bundle.write_bytes(Path(requests.certs.where()).read_bytes())
            with override_settings(WINRM_CA_TRUST_PATH=str(bundle)):
                protocol = preflight._new_winrm_protocol('lab-01.control.local', 'SyntheticAdmin', SENTINEL)
                self.assertEqual(protocol.transport.session.verify, str(bundle.resolve()))
                self.assertEqual(protocol.transport.server_cert_validation, 'validate')
                protocol.transport.session.close()

    def test_non_ca_certificate_and_private_key_fail_closed(self):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'test-only')])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                       .public_key(key.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(now - timedelta(days=1))
                       .not_valid_after(now + timedelta(days=1))
                       .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                       .sign(key, hashes.SHA256()))
        leaf_pem = certificate.public_bytes(serialization.Encoding.PEM)
        private_key = key.private_bytes(serialization.Encoding.PEM,
                                        serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
        with TemporaryDirectory() as directory:
            for name, contents in (
                    ('non-ca.pem', leaf_pem),
                    ('private-key.pem', Path(requests.certs.where()).read_bytes() + private_key)):
                with self.subTest(name=name):
                    bundle = Path(directory) / name
                    bundle.write_bytes(contents)
                    with override_settings(WINRM_CA_TRUST_PATH=str(bundle)), \
                            mock.patch('winrm.protocol.Protocol') as protocol:
                        with self.assertRaisesMessage(preflight.ProbeFailure, 'WINRM_CA_TRUST_INVALID'):
                            preflight._new_winrm_protocol('lab-01.control.local', 'SyntheticAdmin', SENTINEL)
                        protocol.assert_not_called()

    def test_no_ca_or_only_expired_or_future_ca_fails_closed(self):
        dates = (
            [],
            [{'notBefore': 'Jan  1 00:00:00 2000 GMT', 'notAfter': 'Jan  1 00:00:00 2001 GMT'}],
            [{'notBefore': 'Jan  1 00:00:00 2090 GMT', 'notAfter': 'Jan  1 00:00:00 2091 GMT'}],
        )
        for certificates in dates:
            with self.subTest(certificates=certificates), mock.patch.object(preflight.ssl, 'SSLContext') as context, \
                    mock.patch('winrm.protocol.Protocol') as protocol:
                context.return_value.get_ca_certs.return_value = certificates
                with self.assertRaisesMessage(preflight.ProbeFailure, 'WINRM_CA_TRUST_INVALID'):
                    preflight._new_winrm_protocol('lab-01.control.local', 'SyntheticAdmin', SENTINEL)
                protocol.assert_not_called()

    def test_bundle_with_non_certificate_content_fails_closed(self):
        with TemporaryDirectory() as directory:
            bundle = Path(directory) / 'mixed.pem'
            bundle.write_bytes(Path(requests.certs.where()).read_bytes() + b'UNEXPECTED_CONTENT')
            with override_settings(WINRM_CA_TRUST_PATH=str(bundle)), mock.patch('winrm.protocol.Protocol') as protocol:
                with self.assertRaisesMessage(preflight.ProbeFailure, 'WINRM_CA_TRUST_INVALID'):
                    preflight._new_winrm_protocol('lab-01.control.local', 'SyntheticAdmin', SENTINEL)
                protocol.assert_not_called()

    def test_real_pywinrm_session_uses_explicit_bundle_and_validation(self):
        protocol = preflight._new_winrm_protocol('lab-01.control.local', 'SyntheticAdmin', SENTINEL)
        session = protocol.transport.session
        self.assertEqual(session.verify, os.path.realpath(requests.certs.where()))
        self.assertEqual(protocol.transport.server_cert_validation, 'validate')
        self.assertTrue(protocol.transport.endpoint.startswith('https://'))
        session.close()

    def test_missing_unreadable_empty_and_invalid_bundle_fail_closed(self):
        with TemporaryDirectory() as directory:
            missing = Path(directory) / 'missing.pem'
            empty = Path(directory) / 'empty.pem'
            invalid = Path(directory) / 'invalid.pem'
            empty.write_bytes(b'')
            invalid.write_bytes(b'not a certificate')
            for path in (missing, empty, invalid, Path('relative.pem')):
                with self.subTest(path=path), override_settings(WINRM_CA_TRUST_PATH=str(path)):
                    with mock.patch('winrm.protocol.Protocol') as protocol_class:
                        with self.assertRaisesMessage(preflight.ProbeFailure, 'WINRM_CA_TRUST_INVALID'):
                            preflight._new_winrm_protocol('lab-01.control.local', 'SyntheticAdmin', SENTINEL)
                        protocol_class.assert_not_called()
            with override_settings(WINRM_CA_TRUST_PATH=requests.certs.where()), \
                    mock.patch.object(Path, 'open', side_effect=PermissionError):
                with self.assertRaisesMessage(preflight.ProbeFailure, 'WINRM_CA_TRUST_INVALID'):
                    preflight._winrm_ca_trust_path()

    @override_settings(WINRM_CA_TRUST_PATH='')
    def test_empty_config_does_not_fall_back_to_certifi(self):
        with mock.patch('winrm.protocol.Protocol') as protocol_class:
            with self.assertRaisesMessage(preflight.ProbeFailure, 'WINRM_CA_TRUST_INVALID'):
                preflight._new_winrm_protocol('lab-01.control.local', 'SyntheticAdmin', SENTINEL)
            protocol_class.assert_not_called()

@override_settings(WINRM_CA_TRUST_PATH=requests.certs.where())
class RedirectProtectionTests(TestCase):
    def session_with_adapter(self, adapter):
        session = requests.Session()
        session.mount('https://', adapter)
        session.mount('http://', adapter)
        session.auth = HttpNtlmAuth('SYNTHETIC\\Admin', SENTINEL)
        preflight._block_redirects(session)
        return session

    def test_all_redirects_fail_without_second_request_or_ntlm_at_target(self):
        for status in (301, 302, 303, 307, 308):
            for destination in ('http://attacker.invalid/', 'https://other.invalid/'):
                with self.subTest(status=status, destination=destination):
                    adapter = SyntheticWinRMAdapter(status, destination)
                    session = self.session_with_adapter(adapter)
                    with self.assertRaises(preflight.ProbeFailure) as raised:
                        session.post('https://lab-01.control.local:5986/wsman', data=b'synthetic-probe', timeout=1)
                    self.assertEqual(raised.exception.code, 'WINRM_REDIRECT_BLOCKED')
                    self.assertEqual(len(adapter.requests), 1)
                    self.assertEqual(adapter.requests[0].url, 'https://lab-01.control.local:5986/wsman')
                    self.assertNotIn(SENTINEL, str(raised.exception))
                    session.close()

    def test_real_pywinrm_transport_uses_the_guarded_session(self):
        adapter = SyntheticWinRMAdapter(302, 'http://attacker.invalid/')
        session_class = requests.Session

        def session_factory():
            session = session_class()
            session.mount('https://', adapter)
            session.mount('http://', adapter)
            return session

        with mock.patch('winrm.transport.requests.Session', side_effect=session_factory):
            with self.assertRaises(preflight.ProbeFailure) as raised:
                preflight._winrm_probe('lab-01.control.local', 'SYNTHETIC\\Admin', SENTINEL)
        self.assertEqual(raised.exception.code, 'WINRM_REDIRECT_BLOCKED')
        self.assertNotIn(SENTINEL, str(raised.exception))
        self.assertEqual(len(adapter.requests), 1)
        self.assertTrue(adapter.requests[0].url.startswith('https://lab-01.control.local:5986/'))

    @mock.patch('requests_ntlm.requests_ntlm.spnego.client')
    @mock.patch.object(HttpNtlmAuth, '_get_server_cert', return_value=None)
    def test_normal_ntlm_401_handshake_still_works(self, _certificate, client_factory):
        client_factory.return_value.step.side_effect = [b'synthetic-negotiate', b'synthetic-authenticate']
        adapter = SyntheticWinRMAdapter()
        session = self.session_with_adapter(adapter)
        response = session.post('https://lab-01.control.local:5986/wsman', data=b'synthetic-probe', timeout=1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(adapter.requests), 3)
        self.assertTrue(all(request.url.startswith('https://lab-01.control.local:5986/')
                            for request in adapter.requests))
        self.assertTrue(adapter.requests[1].headers['Authorization'].startswith('NTLM '))
        self.assertTrue(adapter.requests[2].headers['Authorization'].startswith('NTLM '))
        session.close()
