import json
import logging
import os
import socket
import ssl
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from base64 import b64encode
from tempfile import TemporaryDirectory
from unittest import mock

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from requests.adapters import BaseAdapter
from requests.models import Response
from requests_ntlm import HttpNtlmAuth
from winrm.exceptions import WinRMOperationTimeoutError

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from agents.models import AgentMachine
from dashboard import remote_install_preflight as preflight
from dashboard.remote_install_preflight import ProbeFailure
from dashboard.test_ad_install_discovery import computer


SENTINEL = 'SUPER_SECRET_TEST_PASSWORD_91827'
DOMAIN_CONFIG = {'DOMAIN': 'control.local'}
REMOTE_OK = {
    'hostname': 'lab-01', 'windows_name': 'Windows 11 Pro', 'build': 22631,
    'architecture': 'x64', 'powershell_major': 5, 'admin': True,
    'nightowl_service_present': False, 'nightowl_directory_present': False,
    'part_of_domain': True, 'domain': 'control.local',
    'computer_sid': 'S-1-5-21-1111111111-2222222222-3333333333-4444',
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
        self.assertEqual(result['nightowl_absence'], {
            'service_present': False, 'directory_present': False, 'correlation': 'UNMANAGED',
        })
        self.assertNotIn(SENTINEL, json.dumps(result))
        args, kwargs = self.remote_mock.call_args
        self.assertEqual(args, ('lab-01.control.local', 'CONTROL\\Admin', SENTINEL))
        self.assertEqual(kwargs['pinned_address'], '192.168.104.20')
        self.assertEqual(kwargs['expected_computer']['sid'], computer()['sid'])
        self.assertIsInstance(kwargs['deadline'], float)
        self.assertEqual(result['target']['selected_ip'], '192.168.104.20')

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
        self.assertEqual(self.run_probe()['checks']['REMOTE_TRANSPORT']['code'], 'TCP_5986_UNAVAILABLE')
        self.remote_mock.assert_not_called()

    @override_settings(REMOTE_INSTALL_WINRM_HTTP_ENABLED=True,
                       REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=[
                           'cs-cvel-0289.control.local', 'cs-cvel-0290.control.local'])
    def test_authorized_pilot_tries_https_then_falls_back_to_encrypted_http_on_tcp_failure(self):
        fqdn = 'CS-CVEL-0289.CONTROL.LOCAL'
        self.ad_mock.return_value = [computer(hostname='CS-CVEL-0289', fqdn='cs-cvel-0289.control.local')]
        dns = {'cs-cvel-0289.control.local': {'dns_status': 'RESOLVED', 'primary_ipv4': '192.168.102.54',
                                               'ipv4_addresses': ['192.168.102.54']}}
        self.dns_mock.return_value = dns
        remote = {**REMOTE_OK, 'hostname': 'cs-cvel-0289'}
        self.remote_mock.return_value = remote
        self.tcp_mock.side_effect = [ProbeFailure('TCP_5986_TIMEOUT', stage='TCP_5986'), True]
        result = preflight.run_remote_install_preflight(fqdn, 'CONTROL\\Admin', SENTINEL)
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['target']['winrm_transport'], 'HTTP_NTLM_ENCRYPTED')
        self.assertEqual(result['target']['winrm_port'], 5985)
        self.assertEqual([call.args[1] for call in self.tcp_mock.call_args_list], [5986, 5985])
        self.assertEqual([call.kwargs['transport']['label'] for call in self.remote_mock.call_args_list],
                         ['HTTP_NTLM_ENCRYPTED'])
        self.assertEqual(self.remote_mock.call_args.kwargs['pinned_address'], '192.168.102.54')
        self.assertEqual([item['transport'] for item in result['target']['transport_attempts']],
                         ['HTTPS_TLS_VALIDATED', 'HTTP_NTLM_ENCRYPTED'])

    @override_settings(REMOTE_INSTALL_WINRM_HTTP_ENABLED=True,
                       REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=['cs-cvel-0289.control.local'])
    def test_https_success_does_not_probe_or_use_http(self):
        self.ad_mock.return_value = [computer(hostname='CS-CVEL-0289', fqdn='cs-cvel-0289.control.local')]
        self.dns_mock.return_value = {'cs-cvel-0289.control.local': {
            'dns_status': 'RESOLVED', 'ipv4_addresses': ['192.168.102.54']}}
        self.remote_mock.return_value = {**REMOTE_OK, 'hostname': 'cs-cvel-0289'}
        result = preflight.run_remote_install_preflight('cs-cvel-0289.control.local', 'CONTROL\\Admin', SENTINEL)
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['target']['winrm_transport'], 'HTTPS_TLS_VALIDATED')
        self.tcp_mock.assert_called_once_with('192.168.102.54', 5986,
            timeout=preflight.ENDPOINT_TCP_TIMEOUT_SECONDS)
        self.assertEqual(self.remote_mock.call_args.kwargs['transport']['scheme'], 'https')

    def test_multiple_a_records_probe_all_ports_and_select_working_interface(self):
        addresses = ['192.168.100.3', '192.168.100.221']
        self.dns_mock.return_value = {'lab-01.control.local': {
            'dns_status': 'RESOLVED', 'primary_ipv4': addresses[0], 'ipv4_addresses': addresses}}

        def tcp(address, port, *, timeout):
            self.assertEqual(port, 5986)
            self.assertLessEqual(timeout, 2)
            if address == addresses[0]:
                raise ProbeFailure('TCP_5986_TIMEOUT', stage='TCP_5986')
            return True

        self.tcp_mock.side_effect = tcp
        result = self.run_probe()
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['target']['ipv4_addresses'], addresses)
        self.assertEqual(result['target']['selected_ip'], addresses[1])
        self.assertEqual([call.kwargs['pinned_address'] for call in self.remote_mock.call_args_list], [addresses[1]])
        self.assertEqual(result['target']['endpoint_attempts'][0]['code'], 'TCP_5986_TIMEOUT')
        self.assertEqual(result['target']['endpoint_attempts'][1]['status'], 'SELECTED')

    def test_open_port_with_wrong_remote_identity_is_rejected_and_next_a_record_is_checked(self):
        addresses = ['192.168.100.3', '192.168.100.221']
        self.dns_mock.return_value = {'lab-01.control.local': {
            'dns_status': 'RESOLVED', 'primary_ipv4': addresses[0], 'ipv4_addresses': addresses}}
        wrong = {**REMOTE_OK, 'hostname': 'other-pc', 'computer_sid': 'S-1-5-21-1-2-3-999'}

        def remote(_fqdn, _username, _password, *, pinned_address, **_kwargs):
            return wrong if pinned_address == addresses[0] else REMOTE_OK

        self.remote_mock.side_effect = remote
        result = self.run_probe()
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['target']['selected_ip'], addresses[1])
        self.assertEqual(result['target']['endpoint_attempts'][0]['status'], 'IDENTITY_MISMATCH')
        self.assertEqual(result['target']['endpoint_attempts'][0]['code'], 'REMOTE_IDENTITY_MISMATCH')

    def test_identity_unverifiable_fails_closed_when_no_candidate_matches_ad(self):
        self.remote_mock.return_value = {**REMOTE_OK, 'computer_sid': ''}
        result = self.run_probe()
        self.assertEqual(result['status'], 'NOT_READY')
        self.assertEqual(result['checks']['TARGET_IDENTITY']['code'], 'REMOTE_IDENTITY_UNVERIFIABLE')
        self.assertIsNone(result['target'].get('selected_ip'))

    def test_all_valid_interfaces_of_same_identity_are_safe_and_deterministic(self):
        addresses = ['192.168.100.221', '192.168.100.3']
        self.dns_mock.return_value = {'lab-01.control.local': {
            'dns_status': 'RESOLVED', 'primary_ipv4': addresses[0], 'ipv4_addresses': addresses}}
        result = self.run_probe()
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['target']['selected_ip'], '192.168.100.3')
        self.assertEqual(self.tcp_mock.call_count, 2)
        self.assertEqual(self.remote_mock.call_count, 1)

    @override_settings(REMOTE_INSTALL_WINRM_HTTP_ENABLED=True,
                       REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=['cs-cvel-0289.control.local'])
    def test_http_allowlist_is_exact_and_other_computers_remain_https(self):
        self.assertEqual(preflight._winrm_transport_for('CS-CVEL-0289.CONTROL.LOCAL.'), {
            'scheme': 'https', 'port': 5986, 'message_encryption': 'auto', 'label': 'HTTPS_TLS_VALIDATED'})
        self.assertEqual([item['port'] for item in preflight._winrm_transport_candidates(
            'CS-CVEL-0289.CONTROL.LOCAL.')], [5986, 5985])
        self.assertEqual([item['port'] for item in preflight._winrm_transport_candidates(
            'cs-cvel-02890.control.local')], [5986])
        self.assertEqual(preflight._winrm_transport_for('cs-cvel-02890.control.local')['port'], 5986)
        self.assertEqual(preflight._winrm_transport_for('lab-01.control.local')['scheme'], 'https')

    def test_http_stays_disabled_by_default_even_for_a_pilot_name(self):
        self.assertFalse(settings.REMOTE_INSTALL_WINRM_HTTP_ENABLED)
        self.assertEqual(preflight._winrm_transport_for('cs-cvel-0289.control.local')['port'], 5986)

    @override_settings(REMOTE_INSTALL_WINRM_HTTP_ENABLED=True,
                       REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=['cs-cvel-0289.control.local'])
    def test_non_allowlisted_target_never_probes_http_even_when_https_is_unreachable(self):
        self.tcp_mock.side_effect = ProbeFailure('TCP_5986_TIMEOUT', stage='TCP_5986')
        result = self.run_probe()
        self.assertEqual(result['checks']['REMOTE_TRANSPORT']['code'], 'TCP_5986_TIMEOUT')
        self.tcp_mock.assert_called_once_with('192.168.104.20', 5986,
            timeout=preflight.ENDPOINT_TCP_TIMEOUT_SECONDS)
        self.remote_mock.assert_not_called()

    @override_settings(REMOTE_INSTALL_WINRM_HTTP_ENABLED=True,
                       REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=['not-a-host'])
    def test_invalid_enabled_http_allowlist_fails_closed_before_dns(self):
        result = self.run_probe()
        self.assertEqual(result['checks']['REMOTE_TRANSPORT']['code'], 'WINRM_HTTP_CONFIG_INVALID')
        self.dns_mock.assert_not_called()
        self.tcp_mock.assert_not_called()
        self.remote_mock.assert_not_called()

    @override_settings(REMOTE_INSTALL_WINRM_HTTP_ENABLED=True,
                       REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=['cs-cvel-0289.control.local'])
    def test_http_tcp_failure_is_reported_after_https_connectivity_failure(self):
        self.ad_mock.return_value = [computer(hostname='CS-CVEL-0289', fqdn='cs-cvel-0289.control.local')]
        self.dns_mock.return_value = {'cs-cvel-0289.control.local': {
            'dns_status': 'RESOLVED', 'primary_ipv4': '192.168.102.54', 'ipv4_addresses': ['192.168.102.54']}}
        self.tcp_mock.side_effect = [
            preflight.ProbeFailure('TCP_5986_TIMEOUT', stage='TCP_5986'),
            preflight.ProbeFailure('TCP_5985_REFUSED', stage='TCP_5985')]
        result = preflight.run_remote_install_preflight('cs-cvel-0289.control.local', 'CONTROL\\Admin', SENTINEL)
        self.assertEqual(result['checks']['REMOTE_TRANSPORT']['code'], 'TCP_5985_REFUSED')
        self.assertEqual(result['target']['winrm_transport'], 'HTTP_NTLM_ENCRYPTED')
        self.assertEqual(result['target']['winrm_port'], 5985)
        self.assertEqual([call.args[1] for call in self.tcp_mock.call_args_list], [5986, 5985])
        self.remote_mock.assert_not_called()

    @override_settings(REMOTE_INSTALL_WINRM_HTTP_ENABLED=True,
                       REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=['cs-cvel-0289.control.local'])
    def test_authentication_failure_on_https_never_falls_back_to_http(self):
        self.ad_mock.return_value = [computer(hostname='CS-CVEL-0289', fqdn='cs-cvel-0289.control.local')]
        self.dns_mock.return_value = {'cs-cvel-0289.control.local': {
            'dns_status': 'RESOLVED', 'ipv4_addresses': ['192.168.102.54']}}
        self.remote_mock.side_effect = preflight.ProbeFailure('AUTHENTICATION_FAILED', stage='WINRM_SESSION')
        result = preflight.run_remote_install_preflight('cs-cvel-0289.control.local', 'CONTROL\\Admin', SENTINEL)
        self.assertEqual(result['checks']['AUTHENTICATION']['code'], 'AUTHENTICATION_FAILED')
        self.tcp_mock.assert_called_once_with('192.168.102.54', 5986,
            timeout=preflight.ENDPOINT_TCP_TIMEOUT_SECONDS)
        self.remote_mock.assert_called_once()

    @override_settings(REMOTE_INSTALL_WINRM_HTTP_ENABLED=True,
                       REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=['cs-cvel-0289.control.local'])
    def test_tls_validation_failure_never_falls_back_to_http(self):
        self.ad_mock.return_value = [computer(hostname='CS-CVEL-0289', fqdn='cs-cvel-0289.control.local')]
        self.dns_mock.return_value = {'cs-cvel-0289.control.local': {
            'dns_status': 'RESOLVED', 'ipv4_addresses': ['192.168.102.54']}}
        self.remote_mock.side_effect = preflight.ProbeFailure('TLS_VALIDATION_FAILED', stage='WINRM_SESSION')
        result = preflight.run_remote_install_preflight('cs-cvel-0289.control.local', 'CONTROL\\Admin', SENTINEL)
        self.assertEqual(result['checks']['REMOTE_TRANSPORT']['code'], 'TLS_VALIDATION_FAILED')
        self.tcp_mock.assert_called_once_with('192.168.102.54', 5986,
            timeout=preflight.ENDPOINT_TCP_TIMEOUT_SECONDS)
        self.remote_mock.assert_called_once()

    @override_settings(REMOTE_INSTALL_WINRM_HTTP_ENABLED=True,
                       REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=['cs-cvel-0289.control.local'])
    def test_tls_handshake_connectivity_failure_may_use_authorized_http(self):
        self.ad_mock.return_value = [computer(hostname='CS-CVEL-0289', fqdn='cs-cvel-0289.control.local')]
        self.dns_mock.return_value = {'cs-cvel-0289.control.local': {
            'dns_status': 'RESOLVED', 'ipv4_addresses': ['192.168.102.54']}}
        self.remote_mock.side_effect = [
            ProbeFailure('TLS_HANDSHAKE_FAILED', stage='WINRM_SESSION'), {**REMOTE_OK, 'hostname': 'cs-cvel-0289'}]
        result = preflight.run_remote_install_preflight('cs-cvel-0289.control.local', 'CONTROL\\Admin', SENTINEL)
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['target']['winrm_transport'], 'HTTP_NTLM_ENCRYPTED')
        self.assertEqual([call.kwargs['transport']['label'] for call in self.remote_mock.call_args_list],
                         ['HTTPS_TLS_VALIDATED', 'HTTP_NTLM_ENCRYPTED'])

    @override_settings(REMOTE_INSTALL_WINRM_HTTP_ENABLED=True,
                       REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=['cs-cvel-0289.control.local'])
    def test_invalid_server_ca_configuration_is_not_hidden_by_http_fallback(self):
        self.ad_mock.return_value = [computer(hostname='CS-CVEL-0289', fqdn='cs-cvel-0289.control.local')]
        self.dns_mock.return_value = {'cs-cvel-0289.control.local': {
            'dns_status': 'RESOLVED', 'ipv4_addresses': ['192.168.102.54']}}
        with mock.patch.object(preflight, '_winrm_ca_trust_path',
                side_effect=ProbeFailure('WINRM_CA_TRUST_INVALID', stage='CA_TRUST')):
            result = preflight.run_remote_install_preflight(
                'cs-cvel-0289.control.local', 'CONTROL\\Admin', SENTINEL)
        self.assertEqual(result['checks']['REMOTE_TRANSPORT']['code'], 'WINRM_CA_TRUST_INVALID')
        self.tcp_mock.assert_not_called()
        self.remote_mock.assert_not_called()

    @override_settings(REMOTE_INSTALL_WINRM_HTTP_ENABLED=True,
                       REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=['cs-cvel-0289.control.local'])
    def test_identity_mismatch_on_https_never_falls_back_to_http(self):
        self.ad_mock.return_value = [computer(hostname='CS-CVEL-0289', fqdn='cs-cvel-0289.control.local')]
        self.dns_mock.return_value = {'cs-cvel-0289.control.local': {
            'dns_status': 'RESOLVED', 'ipv4_addresses': ['192.168.102.54']}}
        self.remote_mock.return_value = {**REMOTE_OK, 'hostname': 'other-pc'}
        result = preflight.run_remote_install_preflight('cs-cvel-0289.control.local', 'CONTROL\\Admin', SENTINEL)
        self.assertEqual(result['checks']['TARGET_IDENTITY']['code'], 'REMOTE_IDENTITY_MISMATCH')
        self.tcp_mock.assert_called_once_with('192.168.102.54', 5986,
            timeout=preflight.ENDPOINT_TCP_TIMEOUT_SECONDS)
        self.remote_mock.assert_called_once()

    def test_auth_negotiation_failure_is_reported_in_authentication_check(self):
        self.remote_mock.side_effect = preflight.ProbeFailure('AUTH_NEGOTIATION_FAILED')
        result = self.run_probe()
        self.assertEqual(result['checks']['REMOTE_TRANSPORT']['status'], 'PASS')
        self.assertEqual(result['checks']['AUTHENTICATION'], {
            'status': 'FAIL', 'code': 'AUTH_NEGOTIATION_FAILED'})
        self.assertEqual(result['checks']['REMOTE_TRANSPORT']['status'], 'PASS')
        self.assertEqual(result['status'], 'NOT_READY')

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
                self.tcp_mock.assert_called_with(
                    address, 5986, timeout=preflight.ENDPOINT_TCP_TIMEOUT_SECONDS)
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
                               ({'hostname': 'other'}, 'TARGET_IDENTITY'),
                               ({'nightowl_service_present': True}, 'NIGHTOWL_ABSENCE'),
                               ({'nightowl_directory_present': True}, 'NIGHTOWL_ABSENCE'),
                               ({'nightowl_directory_present': 'false'}, 'NIGHTOWL_ABSENCE')]:
            with self.subTest(changes=changes):
                self.remote_mock.return_value = {**REMOTE_OK, **changes}
                result = self.run_probe()
                self.assertEqual(result['status'], 'NOT_READY')
                self.assertEqual(result['checks'][check]['status'], 'FAIL')

    def test_bootstrap_only_is_not_an_installation_probe_contract(self):
        probe = preflight._READ_ONLY_PROBE
        self.assertIn("$agentInstall = Join-Path $root 'AgentDotNet'", probe)
        self.assertIn("$agentExe = Join-Path $agentInstall 'NightOwl.Agent.Windows.exe'", probe)
        self.assertIn('nightowl_directory_present = [bool](Test-Path -LiteralPath $agentExe -PathType Leaf)', probe)
        self.assertNotIn('Test-Path -LiteralPath $root)', probe)
        self.assertIn("Get-Service -Name 'NightOwlAgentDotNet'", probe)
        # Synthetic paths model the exact Test-Path operand; no Windows target is contacted.
        root = r'C:\ProgramData\NightOwl'
        paths = {root, root + r'\Bootstrap', root + r'\AgentDotNet'}
        self.remote_mock.return_value = {**REMOTE_OK,
            'nightowl_directory_present': root + r'\AgentDotNet\NightOwl.Agent.Windows.exe' in paths}
        self.assertEqual(self.run_probe()['status'], 'READY')

    def test_agent_directory_or_service_blocks_installation(self):
        for field in ('nightowl_directory_present', 'nightowl_service_present'):
            with self.subTest(field=field):
                self.remote_mock.return_value = {**REMOTE_OK, field: True}
                result = self.run_probe()
                self.assertEqual(result['status'], 'NOT_READY')
                self.assertEqual(result['checks']['NIGHTOWL_ABSENCE']['code'],
                                 'NIGHTOWL_INSTALLATION_DETECTED')

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
        writes = [query['sql'] for query in queries if not query['sql'].lstrip().upper().startswith('SELECT')]
        self.assertEqual(len(writes), 2)
        self.assertTrue(all(sql.lstrip().upper().startswith('INSERT INTO "AGENTS_AUDITEVENT"') for sql in writes))
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
        self.assertEqual(kwargs['message_encryption'], 'auto')
        self.assertGreater(kwargs['read_timeout_sec'], kwargs['operation_timeout_sec'])
        command = protocol.run_command.call_args
        self.assertEqual(command.args[1], 'powershell.exe')
        self.assertIn('-EncodedCommand', command.args[2])
        self.assertNotIn(SENTINEL, str(command))
        protocol.cleanup_command.assert_called_once()
        protocol.close_shell.assert_called_once()

    @override_settings(REMOTE_INSTALL_WINRM_HTTP_ENABLED=True,
                       REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=['cs-cvel-0289.control.local'])
    @mock.patch('winrm.protocol.Protocol')
    def test_http_pilot_protocol_requires_ntlm_message_encryption(self, protocol_class):
        preflight._new_winrm_protocol('CS-CVEL-0289.CONTROL.LOCAL.', 'CONTROL\\Admin', SENTINEL,
                                      transport=preflight._HTTP_TRANSPORT)
        kwargs = protocol_class.call_args.kwargs
        self.assertEqual(kwargs['endpoint'], 'http://cs-cvel-0289.control.local:5985/wsman')
        self.assertEqual(kwargs['transport'], 'ntlm')
        self.assertEqual(kwargs['message_encryption'], 'always')
        self.assertIsNone(kwargs['ca_trust_path'])
        self.assertEqual(kwargs['server_cert_validation'], 'validate')

    @override_settings(REMOTE_INSTALL_WINRM_HTTP_ENABLED=True,
                       REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=['some-other-host.control.local'])
    @mock.patch('winrm.protocol.Protocol')
    def test_http_protocol_cannot_be_constructed_for_non_allowlisted_fqdn(self, protocol_class):
        with self.assertRaisesMessage(preflight.ProbeFailure, 'WINRM_PORT_NOT_ALLOWED'):
            preflight._new_winrm_protocol('CS-CVEL-0289.CONTROL.LOCAL.', 'CONTROL\\Admin', SENTINEL,
                                          transport=preflight._HTTP_TRANSPORT)
        protocol_class.assert_not_called()

    @override_settings(REMOTE_INSTALL_WINRM_HTTP_ENABLED=True,
                       REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST=['cs-cvel-0289.control.local'])
    def test_http_auth_crypto_negotiation_and_timeout_fail_closed_without_https_retry(self):
        from winrm.exceptions import AuthenticationError

        failures = (
            (AuthenticationError(SENTINEL), 'AUTHENTICATION_FAILED'),
            (RuntimeError('NTLM message encryption negotiation failed ' + SENTINEL), 'WINRM_PROTOCOL_ERROR'),
            (requests.exceptions.ReadTimeout(SENTINEL), 'WINRM_REQUEST_TIMEOUT'),
        )
        for error, code in failures:
            with self.subTest(code=code), mock.patch('winrm.protocol.Protocol') as protocol_class:
                protocol_class.return_value.open_shell.side_effect = error
                with self.assertRaises(preflight.ProbeFailure) as raised:
                    preflight._winrm_probe('cs-cvel-0289.control.local', 'CONTROL\\Admin', SENTINEL,
                                           transport=preflight._HTTP_TRANSPORT)
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn(SENTINEL, str(raised.exception))
                protocol_class.assert_called_once()
                self.assertEqual(protocol_class.call_args.kwargs['endpoint'],
                                 'http://cs-cvel-0289.control.local:5985/wsman')

    def test_untrusted_certificate_and_hostname_mismatch_fail_closed(self):
        for reason in ('untrusted certificate', 'hostname mismatch'):
            with self.subTest(reason=reason), mock.patch('winrm.protocol.Protocol') as protocol_class:
                protocol_class.return_value.open_shell.side_effect = ssl.SSLError(reason + SENTINEL)
                with self.assertRaises(preflight.ProbeFailure) as raised:
                    preflight._winrm_probe('lab-01.control.local', 'SyntheticAdmin', SENTINEL)
                self.assertEqual(raised.exception.code, 'TLS_VALIDATION_FAILED')
                self.assertNotIn(SENTINEL, str(raised.exception))
                protocol_class.return_value.run_command.assert_not_called()

    @mock.patch('winrm.protocol.Protocol')
    def test_missing_ntlm_challenge_is_reported_as_auth_negotiation_not_transport(self, protocol_class):
        protocol_class.return_value.open_shell.side_effect = PermissionError(
            'Access denied: Server did not respond with NTLM challenge token ' + SENTINEL)
        with self.assertRaises(preflight.ProbeFailure) as raised:
            preflight._winrm_probe('lab-01.control.local', 'SyntheticAdmin', SENTINEL)
        self.assertEqual(raised.exception.code, 'AUTH_NEGOTIATION_FAILED')
        self.assertNotIn(SENTINEL, str(raised.exception))
        protocol_class.return_value.run_command.assert_not_called()

    @mock.patch('winrm.protocol.Protocol')
    def test_unrelated_permission_error_remains_sanitized_transport_failure(self, protocol_class):
        protocol_class.return_value.open_shell.side_effect = PermissionError('local access denied ' + SENTINEL)
        with self.assertRaises(preflight.ProbeFailure) as raised:
            preflight._winrm_probe('lab-01.control.local', 'SyntheticAdmin', SENTINEL)
        self.assertEqual(raised.exception.code, 'WINRM_PROTOCOL_ERROR')
        self.assertNotIn(SENTINEL, str(raised.exception))

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

    def test_no_loadable_ca_fails_closed(self):
        with mock.patch.object(preflight.ssl, 'SSLContext') as context, \
                mock.patch('winrm.protocol.Protocol') as protocol:
            context.return_value.get_ca_certs.return_value = []
            with self.assertRaisesMessage(preflight.ProbeFailure, 'WINRM_CA_TRUST_INVALID'):
                preflight._new_winrm_protocol('lab-01.control.local', 'SyntheticAdmin', SENTINEL)
            protocol.assert_not_called()

    def make_ca(self, name, expired=False):
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                       .public_key(key.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(now - timedelta(days=30))
                       .not_valid_after(now + timedelta(days=-1 if expired else 30))
                       .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                       .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                                   key_encipherment=False, data_encipherment=False,
                                                   key_agreement=False, key_cert_sign=True, crl_sign=True,
                                                   encipher_only=False, decipher_only=False), critical=True)
                       .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                       .sign(key, hashes.SHA256()))
        return key, certificate

    def test_unrelated_expired_roots_are_accepted(self):
        for valid_count, expired_count in ((1, 1), (2, 2)):
            with self.subTest(valid=valid_count, expired=expired_count), TemporaryDirectory() as directory:
                roots = [self.make_ca(f'valid-{i}')[1] for i in range(valid_count)]
                roots += [self.make_ca(f'expired-{i}', expired=True)[1] for i in range(expired_count)]
                bundle = Path(directory) / 'mixed-roots.pem'
                bundle.write_bytes(b''.join(c.public_bytes(serialization.Encoding.PEM) for c in roots))
                with override_settings(WINRM_CA_TRUST_PATH=str(bundle)):
                    self.assertEqual(preflight._winrm_ca_trust_path(), str(bundle.resolve()))
                    context = ssl.create_default_context(cafile=str(bundle))
                    self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                    self.assertTrue(context.check_hostname)

    def test_tls_runtime_validates_server_chain_hostname_and_expiration(self):
        trusted_key, trusted_ca = self.make_ca('trusted-root')
        other_key, other_ca = self.make_ca('untrusted-root')
        expired_root = self.make_ca('unused-expired-root', expired=True)[1]
        cases = (
            ('trusted', trusted_key, trusted_ca, 'lab-01.control.local', False, None),
            ('untrusted', other_key, other_ca, 'lab-01.control.local', False, 20),
            ('hostname', trusted_key, trusted_ca, 'other.control.local', False, 62),
            ('expired', trusted_key, trusted_ca, 'lab-01.control.local', True, 10),
        )
        for label, issuer_key, issuer, hostname, expired, error_code in cases:
            with self.subTest(case=label), TemporaryDirectory() as directory:
                key = ec.generate_private_key(ec.SECP256R1())
                now = datetime.now(timezone.utc)
                leaf = (x509.CertificateBuilder()
                        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
                        .issuer_name(issuer.subject).public_key(key.public_key())
                        .serial_number(x509.random_serial_number())
                        .not_valid_before(now - timedelta(days=2))
                        .not_valid_after(now + timedelta(days=-1 if expired else 1))
                        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False)
                        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
                        .sign(issuer_key, hashes.SHA256()))
                root = Path(directory) / 'ca.pem'
                cert = Path(directory) / 'server.pem'
                private = Path(directory) / 'server-key.pem'
                root.write_bytes(trusted_ca.public_bytes(serialization.Encoding.PEM)
                                 + expired_root.public_bytes(serialization.Encoding.PEM))
                cert.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
                private.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                      serialization.PrivateFormat.PKCS8,
                                                      serialization.NoEncryption()))
                with override_settings(WINRM_CA_TRUST_PATH=str(root)):
                    client_context = ssl.create_default_context(cafile=preflight._winrm_ca_trust_path())
                server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                server_context.load_cert_chain(str(cert), str(private))
                client_socket, server_socket = socket.socketpair()
                client_socket.settimeout(3)
                server_socket.settimeout(3)
                errors = []

                def serve():
                    try:
                        with server_socket, server_context.wrap_socket(server_socket, server_side=True) as secured:
                            if secured.recv(1) == b'1':
                                secured.sendall(b'1')
                    except (ssl.SSLError, ConnectionResetError):
                        pass  # The client rejects the intentionally invalid certificate.
                    except Exception as exc:
                        errors.append(type(exc).__name__)

                thread = threading.Thread(target=serve)
                thread.start()
                try:
                    with client_socket:
                        if error_code is None:
                            with client_context.wrap_socket(client_socket, server_hostname='lab-01.control.local') as secured:
                                secured.sendall(b'1')
                                self.assertEqual(secured.recv(1), b'1')
                        else:
                            with self.assertRaises(ssl.SSLCertVerificationError) as raised:
                                client_context.wrap_socket(client_socket, server_hostname='lab-01.control.local')
                            self.assertEqual(raised.exception.verify_code, error_code)
                finally:
                    thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])

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

    def test_pinned_real_pywinrm_session_keeps_fqdn_and_routes_pool_to_selected_ip(self):
        protocol = preflight._new_winrm_protocol('lab-01.control.local', 'SyntheticAdmin', SENTINEL,
                                                  pinned_address='192.168.100.3')
        session = protocol.transport.session
        self.assertFalse(session.trust_env)
        self.assertIn('https://lab-01.control.local:5986/wsman', protocol.transport.endpoint)
        adapter = session.get_adapter(protocol.transport.endpoint)
        pool = adapter.poolmanager.connection_from_url(protocol.transport.endpoint)
        connection = pool._new_conn()
        self.assertEqual(pool.host, 'lab-01.control.local')
        self.assertEqual(connection._pinned_address, '192.168.100.3')
        self.assertEqual(protocol.transport.server_cert_validation, 'validate')
        self.assertEqual(session.verify, os.path.realpath(requests.certs.where()))
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

    def test_redirect_is_blocked_before_http_encryption_handshake(self):
        adapter = SyntheticWinRMAdapter(302, 'https://attacker.invalid/wsman')

        class EncryptionTransport:
            def build_session(self):
                session = requests.Session()
                session.mount('http://', adapter)
                self.setup_encryption(session)
                return session

            def setup_encryption(self, session):
                session.post('http://cs-cvel-0289.control.local:5985/wsman', data=b'synthetic-negotiate')

        protocol = type('FakeProtocol', (), {'transport': EncryptionTransport()})()
        with self.assertRaises(preflight.ProbeFailure) as raised:
            preflight._build_guarded_winrm_session(protocol)
        self.assertEqual(raised.exception.code, 'WINRM_REDIRECT_BLOCKED')
        self.assertEqual(raised.exception.stage, 'WINRM_SESSION')
        self.assertEqual(len(adapter.requests), 1)
        self.assertEqual(adapter.requests[0].url, 'http://cs-cvel-0289.control.local:5985/wsman')

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
