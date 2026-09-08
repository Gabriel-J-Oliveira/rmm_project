import ssl
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from . import ad_ldap
from .ad_ldap import ActiveDirectoryConfigError, ActiveDirectoryUnavailable, parse_ad_server_uri


def ad_settings(server_uri='ldap://ad.example.local', require_tls=False):
    return {
        'ENABLED': True,
        'SERVER_URI': server_uri,
        'DOMAIN': 'example.local',
        'REALM': 'EXAMPLE.LOCAL',
        'BIND_DN': 'service-account',
        'BIND_PASSWORD': 'configured',
        'USER_SEARCH_BASE': 'OU=Users,DC=example,DC=local',
        'GROUP_SEARCH_BASE': '',
        'USER_ATTR': 'sAMAccountName',
        'EMAIL_ATTR': 'mail',
        'FIRST_NAME_ATTR': 'givenName',
        'LAST_NAME_ATTR': 'sn',
        'REQUIRE_TLS': require_tls,
        'ADMIN_GROUP': '',
        'TECH_GROUP': '',
        'TIMEOUT': 8,
    }


class FakeConnection:
    def __init__(self, server, **kwargs):
        self.server = server
        self.user = kwargs.get('user', '')
        self.credential = kwargs.get('pass' + 'word', '')
        self.auto_bind = kwargs.get('auto_bind', False)
        self.receive_timeout = kwargs.get('receive_timeout', 8)
        self.events = []
        self.open_result = True
        self.start_tls_result = True
        self.bind_result = True

    def open(self):
        self.events.append('open')
        return self.open_result

    def start_tls(self):
        self.events.append('start_tls')
        return self.start_tls_result

    def bind(self):
        self.events.append('bind')
        return self.bind_result


class ActiveDirectoryTransportTests(SimpleTestCase):
    def test_parse_ldap_default_port(self):
        endpoint = parse_ad_server_uri('ldap://ad.example.local', require_tls=False)

        self.assertEqual(endpoint.host, 'ad.example.local')
        self.assertEqual(endpoint.port, 389)
        self.assertFalse(endpoint.use_ssl)
        self.assertFalse(endpoint.require_start_tls)
        self.assertFalse(endpoint.secure_transport)

    def test_parse_ldaps_default_port(self):
        endpoint = parse_ad_server_uri('ldaps://ad.example.local', require_tls=False)

        self.assertEqual(endpoint.host, 'ad.example.local')
        self.assertEqual(endpoint.port, 636)
        self.assertTrue(endpoint.use_ssl)
        self.assertFalse(endpoint.require_start_tls)
        self.assertTrue(endpoint.secure_transport)

    def test_parse_explicit_ports_are_respected(self):
        ldap_endpoint = parse_ad_server_uri('ldap://ad.example.local:1389', require_tls=True)
        ldaps_endpoint = parse_ad_server_uri('ldaps://ad.example.local:1636', require_tls=True)

        self.assertEqual(ldap_endpoint.port, 1389)
        self.assertFalse(ldap_endpoint.use_ssl)
        self.assertTrue(ldap_endpoint.require_start_tls)
        self.assertEqual(ldaps_endpoint.port, 1636)
        self.assertTrue(ldaps_endpoint.use_ssl)
        self.assertFalse(ldaps_endpoint.require_start_tls)

    def test_invalid_scheme_is_rejected(self):
        with self.assertRaises(ActiveDirectoryConfigError):
            parse_ad_server_uri('http://ad.example.local', require_tls=True)

    def test_missing_hostname_is_rejected(self):
        with self.assertRaises(ActiveDirectoryConfigError):
            parse_ad_server_uri('ldap://', require_tls=True)

    def test_embedded_credentials_are_rejected(self):
        with self.assertRaises(ActiveDirectoryConfigError):
            parse_ad_server_uri('ldap://user:credential@ad.example.local', require_tls=True)

    def test_query_and_fragment_are_rejected(self):
        with self.assertRaises(ActiveDirectoryConfigError):
            parse_ad_server_uri('ldap://ad.example.local?x=1', require_tls=True)
        with self.assertRaises(ActiveDirectoryConfigError):
            parse_ad_server_uri('ldap://ad.example.local#fragment', require_tls=True)

    def test_unexpected_path_is_rejected(self):
        with self.assertRaises(ActiveDirectoryConfigError):
            parse_ad_server_uri('ldap://ad.example.local/DC=example,DC=local', require_tls=True)
        with self.assertRaises(ActiveDirectoryConfigError):
            parse_ad_server_uri('ldaps://ad.example.local/path', require_tls=True)
        with self.assertRaises(ActiveDirectoryConfigError):
            parse_ad_server_uri('ldap://ad.example.local/', require_tls=True)

    def test_valid_uris_without_path_still_pass(self):
        self.assertEqual(parse_ad_server_uri('ldap://ad.example.local', require_tls=True).port, 389)
        self.assertEqual(parse_ad_server_uri('ldap://ad.example.local:1389', require_tls=True).port, 1389)
        self.assertEqual(parse_ad_server_uri('ldaps://ad.example.local', require_tls=False).port, 636)
        self.assertEqual(parse_ad_server_uri('ldaps://ad.example.local:1636', require_tls=False).port, 1636)

    @override_settings(AD_AUTH_CONFIG=ad_settings('ldaps://ad.example.local', require_tls=False))
    def test_ldaps_server_uses_implicit_tls_with_required_certificate(self):
        with mock.patch('config.ad_ldap.Tls') as tls, mock.patch('config.ad_ldap.Server') as server:
            tls.return_value = 'tls-config'
            ad_ldap._server()

        tls.assert_called_once_with(validate=ssl.CERT_REQUIRED)
        server.assert_called_once_with(
            'ad.example.local',
            port=636,
            use_ssl=True,
            tls='tls-config',
            get_info=ad_ldap.ALL,
            connect_timeout=8,
        )

    @override_settings(AD_AUTH_CONFIG=ad_settings('ldap://ad.example.local', require_tls=True))
    def test_starttls_server_uses_plain_connection_with_required_certificate(self):
        with mock.patch('config.ad_ldap.Tls') as tls, mock.patch('config.ad_ldap.Server') as server:
            tls.return_value = 'tls-config'
            ad_ldap._server()

        tls.assert_called_once_with(validate=ssl.CERT_REQUIRED)
        server.assert_called_once_with(
            'ad.example.local',
            port=389,
            use_ssl=False,
            tls='tls-config',
            get_info=ad_ldap.ALL,
            connect_timeout=8,
        )

    @override_settings(AD_AUTH_CONFIG=ad_settings('ldaps://ad.example.local', require_tls=True))
    def test_ldaps_never_calls_start_tls_before_bind(self):
        fake = FakeConnection(server='server')
        with mock.patch('config.ad_ldap.Connection', return_value=fake), mock.patch('config.ad_ldap.Server'), mock.patch('config.ad_ldap.Tls'):
            conn = ad_ldap.service_connection()

        self.assertIs(conn, fake)
        self.assertEqual(fake.events, ['open', 'bind'])

    @override_settings(AD_AUTH_CONFIG=ad_settings('ldap://ad.example.local', require_tls=True))
    def test_starttls_is_called_before_bind(self):
        fake = FakeConnection(server='server')
        with mock.patch('config.ad_ldap.Connection', return_value=fake), mock.patch('config.ad_ldap.Server'), mock.patch('config.ad_ldap.Tls'):
            conn = ad_ldap.service_connection()

        self.assertIs(conn, fake)
        self.assertEqual(fake.events, ['open', 'start_tls', 'bind'])

    @override_settings(AD_AUTH_CONFIG=ad_settings('ldap://ad.example.local', require_tls=True))
    def test_open_failure_is_fail_closed(self):
        fake = FakeConnection(server='server')
        fake.open_result = False
        with mock.patch('config.ad_ldap.Connection', return_value=fake), mock.patch('config.ad_ldap.Server'), mock.patch('config.ad_ldap.Tls'):
            with self.assertRaises(ActiveDirectoryUnavailable):
                ad_ldap.service_connection()

        self.assertEqual(fake.events, ['open'])

    @override_settings(AD_AUTH_CONFIG=ad_settings('ldap://ad.example.local', require_tls=True))
    def test_starttls_failure_is_fail_closed(self):
        fake = FakeConnection(server='server')
        fake.start_tls_result = False
        with mock.patch('config.ad_ldap.Connection', return_value=fake), mock.patch('config.ad_ldap.Server'), mock.patch('config.ad_ldap.Tls'):
            with self.assertRaises(ActiveDirectoryUnavailable):
                ad_ldap.service_connection()

        self.assertEqual(fake.events, ['open', 'start_tls'])

    @override_settings(AD_AUTH_CONFIG=ad_settings('ldap://ad.example.local', require_tls=True))
    def test_bind_failure_is_fail_closed(self):
        fake = FakeConnection(server='server')
        fake.bind_result = False
        with mock.patch('config.ad_ldap.Connection', return_value=fake), mock.patch('config.ad_ldap.Server'), mock.patch('config.ad_ldap.Tls'):
            with self.assertRaises(ActiveDirectoryUnavailable):
                ad_ldap.service_connection()

        self.assertEqual(fake.events, ['open', 'start_tls', 'bind'])


class TestAdAuthCommandTransportDiagnosticsTests(SimpleTestCase):
    def run_command(self, config):
        output = StringIO()
        fake_conn = mock.Mock()
        with override_settings(AD_AUTH_CONFIG=config):
            with mock.patch('tickets.management.commands.test_ad_auth.service_connection', return_value=fake_conn):
                with mock.patch('tickets.management.commands.test_ad_auth.find_user', return_value=None):
                    call_command('test_ad_auth', '--username', 'sample.user', stdout=output)
        return output.getvalue()

    def test_ldaps_reports_implicit_tls_as_protected(self):
        output = self.run_command(ad_settings('ldaps://ad.example.local', require_tls=False))

        self.assertIn('Transporte LDAP: LDAPS (TLS implicito)', output)
        self.assertIn('Transporte protegido: sim', output)
        self.assertNotIn('TLS requerido: nao', output)

    def test_starttls_reports_protected_transport(self):
        output = self.run_command(ad_settings('ldap://ad.example.local', require_tls=True))

        self.assertIn('Transporte LDAP: StartTLS', output)
        self.assertIn('Transporte protegido: sim', output)

    def test_plaintext_reports_unprotected_transport(self):
        output = self.run_command(ad_settings('ldap://ad.example.local', require_tls=False))

        self.assertIn('Transporte LDAP: LDAP plaintext', output)
        self.assertIn('Transporte protegido: nao', output)
