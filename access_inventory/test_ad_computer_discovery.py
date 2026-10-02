from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from config import ad_ldap
from access_inventory.services.ad_computer_discovery import (
    COMPUTER_FILTER, PAGED_RESULTS_OID, discover_ad_computers,
    normalize_fqdn, normalize_hostname, project_ou,
)


CONFIG = {
    'ENABLED': True,
    'SERVER_URI': 'ldaps://ad.example.test',
    'REQUIRE_TLS': True,
    'COMPUTER_DISCOVERY_ENABLED': True,
    'COMPUTER_SEARCH_BASE': 'OU=Computers,DC=example,DC=test',
    'COMPUTER_DISCOVERY_LIMIT': 10,
    'TIMEOUT': 8,
}


def computer(name='LAB-01', **attributes):
    values = {
        'name': name, 'dNSHostName': f'{name}.example.test',
        'operatingSystem': 'Windows Server', 'operatingSystemVersion': '10.0',
        'userAccountControl': 4096,
    }
    values.update(attributes)
    return SimpleNamespace(
        entry_dn=f'CN={name},OU=Computers,DC=example,DC=test',
        entry_attributes_as_dict=values,
    )


class FakeConnection:
    def __init__(self, pages):
        self.pages = pages
        self.entries = []
        self.result = {}
        self.search_calls = []
        self.unbound = False

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        entries, cookie, code = self.pages.pop(0)
        self.entries = entries
        self.result = {
            'result': code,
            'controls': {PAGED_RESULTS_OID: {'value': {'cookie': cookie}}},
        }

    def unbind(self):
        self.unbound = True


@override_settings(AD_AUTH_CONFIG=CONFIG)
class ADComputerDiscoveryTests(SimpleTestCase):
    def discover(self, pages):
        conn = FakeConnection(pages)
        with mock.patch.object(ad_ldap, 'service_connection', return_value=conn) as bind:
            result = discover_ad_computers()
        bind.assert_called_once_with()
        self.assertTrue(conn.unbound)
        return result, conn

    def test_zero_computers_and_read_only_search_contract(self):
        result, conn = self.discover([([], b'', 0)])
        self.assertEqual(result, [])
        self.assertEqual(conn.search_calls[0]['search_filter'], COMPUTER_FILTER)
        self.assertEqual(conn.search_calls[0]['search_base'], CONFIG['COMPUTER_SEARCH_BASE'])
        self.assertEqual(conn.search_calls[0]['time_limit'], 8)
        self.assertEqual(conn.search_calls[0]['paged_size'], 10)
        self.assertEqual(len(conn.search_calls), 1)

    def test_computer_fields_enabled_ou_and_valid_filetime(self):
        ticks = 132537600000000000
        result, _ = self.discover([([computer(
            name='LAB-01', dNSHostName='LAB-01.EXAMPLE.TEST.',
            lastLogonTimestamp=ticks, objectSid='S-1-5-21-SYNTHETIC',
        )], b'', 0)])
        item = result[0]
        self.assertEqual(item['hostname'], 'lab-01')
        self.assertEqual(item['fqdn'], 'lab-01.example.test')
        self.assertEqual(item['ou_name'], 'Computers')
        self.assertEqual(item['ou_dn'], 'OU=Computers,DC=example,DC=test')
        self.assertEqual(item['operating_system'], 'Windows Server')
        self.assertEqual(item['operating_system_version'], '10.0')
        self.assertTrue(item['enabled'])
        self.assertEqual(item['last_logon_at'], '2020-12-30T00:00:00+00:00')
        self.assertEqual(item['sid'], 'S-1-5-21-SYNTHETIC')
        self.assertNotIn('raw_attributes', item)

    def test_disabled_and_missing_or_invalid_logon(self):
        entries = [computer('DISABLED', userAccountControl=4098, lastLogonTimestamp='bad'),
                   computer('MISSING', userAccountControl=None)]
        result, _ = self.discover([(entries, b'', 0)])
        self.assertFalse(result[0]['enabled'])
        self.assertIsNone(result[0]['last_logon_at'])
        self.assertIsNone(result[1]['enabled'])
        self.assertIsNone(result[1]['last_logon_at'])

    def test_pagination_and_limit(self):
        with override_settings(AD_AUTH_CONFIG={**CONFIG, 'COMPUTER_DISCOVERY_LIMIT': 3}):
            conn = FakeConnection([([computer('ONE')], b'next', 0),
                                   ([computer('TWO'), computer('THREE')], b'more', 0)])
            with mock.patch.object(ad_ldap, 'service_connection', return_value=conn):
                result = discover_ad_computers()
        self.assertEqual([item['hostname'] for item in result], ['one', 'two', 'three'])
        self.assertEqual([call['paged_cookie'] for call in conn.search_calls], [None, b'next'])
        self.assertEqual([call['paged_size'] for call in conn.search_calls], [3, 2])
        self.assertTrue(conn.unbound)

    def test_server_without_confirmed_pagination_fails_closed(self):
        conn = FakeConnection([([computer(f'LAB-{index}') for index in range(100)], b'', 0)])
        with override_settings(AD_AUTH_CONFIG={**CONFIG, 'COMPUTER_DISCOVERY_LIMIT': 101}):
            with mock.patch.object(ad_ldap, 'service_connection', return_value=conn):
                with self.assertRaises(ad_ldap.ActiveDirectoryUnavailable):
                    discover_ad_computers()
        self.assertTrue(conn.unbound)

    def test_ldap_failure_is_sanitized_and_unbinds(self):
        conn = FakeConnection([([], b'', 51)])
        with mock.patch.object(ad_ldap, 'service_connection', return_value=conn):
            with self.assertRaises(ad_ldap.ActiveDirectoryUnavailable) as raised:
                discover_ad_computers()
        self.assertNotIn('synthetic-password', str(raised.exception))
        with mock.patch.object(ad_ldap, 'service_connection', side_effect=RuntimeError('synthetic-password')):
            with self.assertRaises(ad_ldap.ActiveDirectoryUnavailable) as raised:
                discover_ad_computers()
        self.assertNotIn('synthetic-password', str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(conn.unbound)
        conn.search = mock.Mock(side_effect=RuntimeError('synthetic-password'))
        with mock.patch.object(ad_ldap, 'service_connection', return_value=conn):
            with self.assertRaises(ad_ldap.ActiveDirectoryUnavailable) as raised:
                discover_ad_computers()
        self.assertNotIn('synthetic-password', str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        conn.search = mock.Mock(side_effect=TimeoutError('synthetic-password'))
        with mock.patch.object(ad_ldap, 'service_connection', return_value=conn):
            with self.assertRaises(ad_ldap.ActiveDirectoryUnavailable) as raised:
                discover_ad_computers()
        self.assertNotIn('synthetic-password', str(raised.exception))

    def test_repeated_paging_cookie_and_invalid_limit_fail_closed(self):
        conn = FakeConnection([([computer('ONE')], b'same', 0),
                               ([computer('TWO')], b'same', 0)])
        with mock.patch.object(ad_ldap, 'service_connection', return_value=conn):
            with self.assertRaises(ad_ldap.ActiveDirectoryUnavailable):
                discover_ad_computers()
        self.assertTrue(conn.unbound)
        with override_settings(AD_AUTH_CONFIG={**CONFIG, 'COMPUTER_DISCOVERY_LIMIT': 5001}):
            with mock.patch.object(ad_ldap, 'service_connection') as bind:
                with self.assertRaises(ad_ldap.ActiveDirectoryConfigError):
                    discover_ad_computers()
                bind.assert_not_called()

    def test_disabled_missing_base_and_plain_ldap_never_bind(self):
        configurations = [
            {**CONFIG, 'COMPUTER_DISCOVERY_ENABLED': False},
            {**CONFIG, 'COMPUTER_SEARCH_BASE': ''},
            {**CONFIG, 'SERVER_URI': 'ldap://ad.example.test', 'REQUIRE_TLS': False},
        ]
        with mock.patch.object(ad_ldap, 'service_connection') as bind:
            for config in configurations:
                with override_settings(AD_AUTH_CONFIG=config):
                    with self.assertRaises(ad_ldap.ActiveDirectoryConfigError):
                        discover_ad_computers()
        bind.assert_not_called()

    def test_normalization_and_datetime_projection(self):
        self.assertEqual(normalize_hostname('  LAB-01.Example.Test. '), 'lab-01')
        self.assertEqual(normalize_fqdn('  LAB-01.Example.Test. '), 'lab-01.example.test')
        self.assertEqual(project_ou(r'CN=LAB\,01,OU=Field,DC=example,DC=test')[0], 'Field')
        self.assertEqual(project_ou('invalid'), ('', ''))
        result, _ = self.discover([([computer(lastLogonTimestamp=datetime(2026, 1, 1, tzinfo=timezone.utc))], b'', 0)])
        self.assertEqual(result[0]['last_logon_at'], '2026-01-01T00:00:00+00:00')
