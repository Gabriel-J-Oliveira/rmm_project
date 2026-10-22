import datetime
import ssl
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import TestCase, mock

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from dashboard.remote_install import InstallFailure, _remote_script_candidates
from dashboard import remote_install_preflight as preflight
from dashboard.remote_install_preflight import _PinnedAddressAdapter, ProbeFailure


FQDN = 'lab-01.control.local'


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.seen_host = self.headers.get('Host')
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')

    def log_message(self, *_args):
        pass


class PinnedWinRMEndpointTransportTests(TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.mount('http://', _PinnedAddressAdapter('127.0.0.1'))

    def tearDown(self):
        self.session.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_http_routes_socket_to_selected_ip_but_preserves_fqdn_host(self):
        response = self.session.get(f'http://{FQDN}:{self.server.server_port}/wsman', timeout=2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.server.seen_host, f'{FQDN}:{self.server.server_port}')

    def _https_context(self):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, FQDN)])
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=2))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(FQDN)]), critical=False)
            .sign(key, hashes.SHA256()))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        cert_path = Path(directory.name) / 'server.pem'
        key_path = Path(directory.name) / 'server-key.pem'
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert_path), str(key_path))
        return context, str(cert_path)

    def test_https_pin_preserves_tls_hostname_validation_and_rejects_mismatch(self):
        context, ca_bundle = self._https_context()
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.session.mount('https://', _PinnedAddressAdapter('127.0.0.1'))
        self.session.verify = ca_bundle
        response = self.session.get(f'https://{FQDN}:{self.server.server_port}/wsman', timeout=2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.server.seen_host, f'{FQDN}:{self.server.server_port}')
        with self.assertRaises(requests.exceptions.SSLError):
            self.session.get(f'https://other.control.local:{self.server.server_port}/wsman', timeout=2)


class EndpointFailoverTests(TestCase):
    def setUp(self):
        self.target = {
            'fqdn': FQDN, 'hostname': 'lab-01', 'sid': 'S-1-5-21-1-2-3-1001',
            'distinguished_name': 'CN=lab-01,DC=control,DC=local',
        }
        self.candidates = [
            {'ip': '192.168.100.3', 'tcp_status': 'OPEN'},
            {'ip': '192.168.100.221', 'tcp_status': 'OPEN'},
        ]

    def test_pre_dispatch_failure_retries_next_pinned_ip_and_returns_actual_endpoint(self):
        with mock.patch('dashboard.remote_install._remote_script', side_effect=[
                InstallFailure('WINRM_CONNECTION_FAILED', command_attempted=False), 0]) as remote:
            code, selected, attempts = _remote_script_candidates(
                FQDN, 'admin', 'secret', 'script', 5, endpoint_candidates=self.candidates,
                selected_ip='192.168.100.3', expected_computer=self.target)
        self.assertEqual((code, selected), (0, '192.168.100.221'))
        self.assertEqual([call.kwargs['endpoint_ip'] for call in remote.call_args_list],
                         ['192.168.100.3', '192.168.100.221'])
        self.assertEqual([entry['status'] for entry in attempts], ['FAILED', 'COMPLETED'])

    def test_no_failover_after_remote_payload_may_have_been_sent(self):
        with mock.patch('dashboard.remote_install._remote_script', side_effect=
                InstallFailure('WINRM_REQUEST_TIMEOUT', command_attempted=True)) as remote:
            with self.assertRaises(InstallFailure):
                _remote_script_candidates(FQDN, 'admin', 'secret', 'script', 5,
                    endpoint_candidates=self.candidates, selected_ip='192.168.100.3',
                    expected_computer=self.target)
        remote.assert_called_once()

    def test_browser_or_stale_unverified_ip_is_not_an_endpoint_candidate(self):
        candidates = [{'ip': '127.0.0.1', 'tcp_status': 'OPEN'}]
        with mock.patch('dashboard.remote_install._remote_script') as remote:
            with self.assertRaises(InstallFailure):
                _remote_script_candidates(FQDN, 'admin', 'secret', 'script', 5,
                    endpoint_candidates=candidates, selected_ip='127.0.0.1',
                    expected_computer=self.target)
        remote.assert_not_called()

    def test_candidate_identity_mismatch_fails_over_only_before_dispatch(self):
        with mock.patch('dashboard.remote_install._remote_script', side_effect=[
                InstallFailure('REMOTE_IDENTITY_MISMATCH', command_attempted=False,
                               stage='IDENTITY_VALIDATION'), 0]) as remote:
            result = _remote_script_candidates(
                FQDN, 'admin', 'secret', 'script', 5, endpoint_candidates=self.candidates,
                selected_ip='192.168.100.3', expected_computer=self.target)
        self.assertEqual(result[1], '192.168.100.221')
        self.assertEqual([item['status'] for item in result[2]], ['FAILED', 'COMPLETED'])
        self.assertEqual(result[2][0]['code'], 'REMOTE_IDENTITY_MISMATCH')
        self.assertEqual(remote.call_count, 2)

    def test_executor_reuses_preflight_transport_for_every_pinned_address_attempt(self):
        selected_transport = {'scheme': 'http', 'port': 5985,
                              'message_encryption': 'always', 'label': 'HTTP_NTLM_ENCRYPTED'}
        with mock.patch('dashboard.remote_install._remote_script', side_effect=[
                InstallFailure('WINRM_CONNECTION_FAILED', command_attempted=False), 0]) as remote:
            _remote_script_candidates(FQDN, 'admin', 'secret', 'script', 5,
                endpoint_candidates=self.candidates, selected_ip='192.168.100.3',
                expected_computer=self.target, winrm_transport=selected_transport)
        self.assertEqual([call.kwargs['winrm_transport'] for call in remote.call_args_list],
                         [selected_transport, selected_transport])

    def test_tcp_candidates_use_short_timeout_and_bounded_concurrency(self):
        lock = threading.Lock()
        active = peak = 0

        def tcp(_address, _port, *, timeout):
            nonlocal active, peak
            self.assertEqual(timeout, preflight.ENDPOINT_TCP_TIMEOUT_SECONDS)
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return True

        addresses = [f'192.168.100.{value}' for value in range(1, 13)]
        with mock.patch.object(preflight, '_tcp_available', side_effect=tcp) as probe:
            results = preflight._tcp_candidate_results(addresses, 5985)
        self.assertEqual(len(results), 12)
        self.assertEqual(probe.call_count, 12)
        self.assertLessEqual(peak, preflight.ENDPOINT_TCP_WORKERS)
        self.assertEqual({entry['tcp_status'] for entry in results.values()}, {'OPEN'})

