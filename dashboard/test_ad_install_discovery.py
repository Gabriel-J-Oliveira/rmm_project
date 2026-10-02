import socket
import threading
import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from access_inventory.models import ADUser
from agents.models import AgentMachine
from dashboard import ad_install_discovery as discovery


def computer(hostname='lab-01', fqdn='lab-01.control.local', **changes):
    item = {
        'hostname': hostname, 'fqdn': fqdn, 'distinguished_name': f'CN={hostname},OU=Computers,DC=control,DC=local',
        'ou_name': 'Computers', 'ou_dn': 'OU=Computers,DC=control,DC=local',
        'operating_system': 'Windows 11 Pro', 'operating_system_version': '10.0',
        'enabled': True, 'last_logon_at': '2020-01-01T00:00:00+00:00', 'sid': 'SYNTHETIC-SID',
    }
    item.update(changes)
    return item


class InstallDiscoveryTests(TestCase):
    def machine(self, hostname='lab-01', domain='control.local', fqdn='lab-01.control.local', **changes):
        token = f'{AgentMachine.objects.count() + 1:064x}'
        return AgentMachine.objects.create(
            hostname=hostname, domain=domain, fqdn=fqdn, agent_token_hash=token,
            status=AgentMachine.STATUS_ONLINE, agent_version='0.1.1.0-rc44',
            last_seen_at=timezone.now(), **changes,
        )

    def project(self, items, dns=None):
        with mock.patch.object(discovery, 'discover_ad_computers', return_value=items), \
                mock.patch.object(discovery, 'resolve_computer_dns', return_value=dns or {}):
            return discovery.build_install_discovery()

    def test_fqdn_match_managed_status_and_truncated_ad_name(self):
        machine = self.machine(hostname='cs-cvel-ubuntu-02', fqdn='cs-cvel-ubuntu-02.control.local')
        row = self.project([computer(hostname='cs-cvel-ubuntu-', fqdn='cs-cvel-ubuntu-02.control.local')])['computers'][0]
        self.assertEqual(row['hostname'], 'cs-cvel-ubuntu-02')
        self.assertEqual(row['ad_name'], 'cs-cvel-ubuntu-')
        self.assertEqual(row['endpoint_id'], str(machine.id))
        self.assertEqual(row['correlation_method'], 'FQDN')
        self.assertEqual(row['nightowl_status'], AgentMachine.STATUS_ONLINE)
        self.assertFalse(row['selectable'])

    def test_hostname_fallback_requires_domain_and_unique_match(self):
        machine = self.machine(fqdn='')
        with override_settings(AD_AUTH_CONFIG={'DOMAIN': 'control.local'}):
            row = self.project([computer(fqdn='')])['computers'][0]
        self.assertEqual(row['endpoint_id'], str(machine.id))
        self.assertEqual(row['correlation_method'], 'HOSTNAME_DOMAIN')
        with override_settings(AD_AUTH_CONFIG={'DOMAIN': 'else.test'}):
            self.assertEqual(self.project([computer(fqdn='')])['summary']['unmanaged'], 1)

    def test_fallback_never_overrides_conflicting_explicit_machine_fqdn(self):
        self.machine(fqdn='different.control.local')
        row = self.project([computer()])['computers'][0]
        self.assertEqual(row['correlation_status'], 'UNMANAGED')
        self.assertIsNone(row['endpoint_id'])

    def test_conflicting_machines_are_not_associated(self):
        self.machine()
        self.machine()
        row = self.project([computer()])['computers'][0]
        self.assertEqual(row['correlation_status'], 'CONFLICT')
        self.assertIsNone(row['endpoint_id'])
        self.assertFalse(row['selectable'])

    def test_fqdn_domain_compatibility(self):
        machine = self.machine(domain=' CONTROL.LOCAL. ', fqdn='LAB-01.CONTROL.LOCAL.')
        row = self.project([computer()])['computers'][0]
        self.assertEqual(row['correlation_status'], 'MANAGED')
        self.assertEqual(row['endpoint_id'], str(machine.id))
        machine.domain = ''
        machine.save(update_fields=['domain'])
        self.assertEqual(self.project([computer()])['computers'][0]['correlation_status'], 'MANAGED')

    def test_contradictory_domain_fails_closed(self):
        self.machine(domain='other.local')
        row = self.project([computer()])['computers'][0]
        self.assertEqual(row['correlation_status'], 'CONFLICT')
        self.assertEqual(row['correlation_method'], 'DOMAIN_MISMATCH')
        self.assertIsNone(row['endpoint_id'])
        self.assertFalse(row['selectable'])

    def test_normalized_duplicate_fqdns_conflict(self):
        self.machine()
        self.machine(fqdn=' LAB-01.CONTROL.LOCAL. ')
        row = self.project([computer()])['computers'][0]
        self.assertEqual(row['correlation_status'], 'CONFLICT')
        self.assertIsNone(row['endpoint_id'])

    def test_terminal_lifecycle_does_not_block_reinstall(self):
        for lifecycle in ('uninstalled', 'purged'):
            with self.subTest(lifecycle=lifecycle):
                AgentMachine.objects.all().delete()
                self.machine(agent_lifecycle_status=lifecycle)
                row = self.project([computer()])['computers'][0]
                self.assertEqual(row['correlation_status'], 'UNMANAGED')
                self.assertTrue(row['selectable'])
                self.assertIsNone(row['endpoint_id'])

    def test_terminal_and_active_match_only_active(self):
        self.machine(agent_lifecycle_status='purged')
        active = self.machine()
        row = self.project([computer()])['computers'][0]
        self.assertEqual(row['correlation_status'], 'MANAGED')
        self.assertEqual(row['endpoint_id'], str(active.id))

    def test_unmanaged_disabled_and_dns_failure_remain_visible(self):
        payload = self.project([computer(enabled=False), computer(hostname='other', fqdn='other.control.local')])
        self.assertEqual(payload['summary']['unmanaged'], 2)
        self.assertEqual(payload['summary']['disabled'], 1)
        self.assertEqual(payload['summary']['dns_unresolved'], 2)
        self.assertFalse(payload['computers'][0]['selectable'])
        self.assertTrue(payload['computers'][1]['selectable'])

    def test_user_enrichment_uses_exact_reported_identity_only(self):
        self.machine(last_logged_user=r'CONTROL\alice')
        user = ADUser.objects.create(sid='SYNTHETIC-USER', sam_account_name='alice', display_name='Alice Example', email='alice@example.test')
        row = self.project([computer()])['computers'][0]
        self.assertEqual(row['ad_user']['id'], str(user.id))
        self.assertEqual(row['reported_user_source'], 'AgentMachine.last_logged_user')
        self.assertEqual(row['ad_activity_age'], '>365d')
        self.assertEqual(self.project([computer(hostname='unmanaged', fqdn='unmanaged.control.local')])['computers'][0]['reported_username'], None)

    def test_upn_exact_match_and_unknown_user(self):
        self.machine(last_logged_user='alice@control.local')
        ADUser.objects.create(sid='SYNTHETIC-UPN', sam_account_name='alice', user_principal_name='alice@control.local', display_name='Alice')
        self.assertEqual(self.project([computer()])['computers'][0]['ad_user']['display_name'], 'Alice')
        AgentMachine.objects.update(last_logged_user='unknown@control.local')
        self.assertIsNone(self.project([computer()])['computers'][0]['ad_user'])

    def test_projection_uses_batch_selects_without_writes(self):
        self.machine()
        with mock.patch.object(discovery, 'discover_ad_computers', return_value=[computer()] * 30), \
                mock.patch.object(discovery, 'resolve_computer_dns', return_value={}):
            with CaptureQueriesContext(connection) as queries:
                payload = discovery.build_install_discovery()
        self.assertEqual(len(payload['computers']), 30)
        self.assertEqual(len(queries), 2)
        self.assertTrue(all(query['sql'].lstrip().upper().startswith('SELECT') for query in queries))

    @mock.patch.object(discovery.socket, 'getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.8', 0))])
    def test_dns_success(self, lookup):
        self.assertEqual(discovery._lookup_ipv4('lab-01.control.local')['primary_ipv4'], '192.0.2.8')
        lookup.assert_called_once()

    @mock.patch.object(discovery.socket, 'getaddrinfo', side_effect=socket.gaierror())
    def test_dns_failure_is_best_effort(self, _lookup):
        self.assertEqual(discovery._lookup_ipv4('missing.control.local')['dns_status'], 'UNRESOLVED')

    def test_dns_lookups_are_bounded_and_concurrent(self):
        barrier = threading.Barrier(2, timeout=3)

        def lookup(name):
            barrier.wait()
            return {'dns_status': 'RESOLVED', 'primary_ipv4': '192.0.2.8', 'ipv4_addresses': ['192.0.2.8']}

        with override_settings(AD_AUTH_CONFIG={'DOMAIN': 'control.local'}), mock.patch.object(discovery, '_lookup_ipv4', side_effect=lookup):
            result = discovery.resolve_computer_dns([computer(), computer('lab-02', 'lab-02.control.local')])
        self.assertEqual(len(result), 2)
        self.assertTrue(all(item['dns_status'] == 'RESOLVED' for item in result.values()))

    def test_dns_never_queries_names_outside_configured_domain(self):
        with override_settings(AD_AUTH_CONFIG={'DOMAIN': 'control.local'}), mock.patch.object(discovery, '_lookup_ipv4', return_value={'dns_status': 'RESOLVED'}) as lookup:
            result = discovery.resolve_computer_dns([computer('external', 'external.other.test'), computer()])
        self.assertEqual(list(result), ['lab-01.control.local'])
        lookup.assert_called_once_with('lab-01.control.local')

    def test_dns_deadline_cancels_queued_work_without_growing_threads(self):
        release = threading.Event()
        started = threading.Event()
        names = [computer(f'lab-{index:03}', f'lab-{index:03}.control.local') for index in range(30)]
        futures = []
        real_submit = discovery.DNS_EXECUTOR.submit

        def submit(*args, **kwargs):
            future = real_submit(*args, **kwargs)
            futures.append(future)
            return future

        def blocked(_name):
            started.set()
            release.wait(3)
            return {'dns_status': 'UNRESOLVED', 'primary_ipv4': None, 'ipv4_addresses': []}

        try:
            with override_settings(AD_AUTH_CONFIG={'DOMAIN': 'control.local'}), \
                    mock.patch.object(discovery, '_lookup_ipv4', side_effect=blocked), \
                    mock.patch.object(discovery.DNS_EXECUTOR, 'submit', side_effect=submit), \
                    mock.patch.object(discovery, 'DNS_DEADLINE_SECONDS', 0.05):
                start = time.monotonic()
                first = discovery.resolve_computer_dns(names)
                self.assertTrue(started.is_set())
                second = discovery.resolve_computer_dns(names)
                self.assertLess(time.monotonic() - start, 1)
            self.assertEqual(len(first), 30)
            self.assertEqual(len(second), 30)
            self.assertTrue(all(item['dns_status'] == 'ERROR' for item in first.values()))
            self.assertTrue(any(future.cancelled() for future in futures))
            self.assertLessEqual(len(discovery.DNS_EXECUTOR._threads), discovery.DNS_WORKERS)
            self.assertEqual(len({thread.name for thread in discovery.DNS_EXECUTOR._threads}), len(discovery.DNS_EXECUTOR._threads))
        finally:
            release.set()

    def test_scan_route_requires_auth_post_and_csrf(self):
        url = reverse('agent-install-ad-scan')
        anonymous = Client(enforce_csrf_checks=True)
        self.assertEqual(anonymous.post(url).status_code, 302)
        user = get_user_model().objects.create_user('technical-test', password='synthetic-password', is_staff=True)
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        self.assertEqual(client.get(url).status_code, 405)
        self.assertEqual(client.post(url).status_code, 403)
        response = client.get(reverse('agent-install'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Computadores do dom')
        with mock.patch('dashboard.ad_install_views.build_install_discovery', return_value={'summary': {}, 'computers': []}) as build:
            result = client.post(url, HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        self.assertEqual(result.status_code, 200)
        build.assert_called_once_with()

    def test_scan_error_is_sanitized(self):
        user = get_user_model().objects.create_user('technical-test', password='synthetic-password', is_staff=True)
        self.client.force_login(user)
        with mock.patch('dashboard.ad_install_views.build_install_discovery', side_effect=RuntimeError('synthetic-secret')):
            result = self.client.post(reverse('agent-install-ad-scan'))
        self.assertEqual(result.status_code, 503)
        self.assertNotIn('synthetic-secret', result.content.decode())
