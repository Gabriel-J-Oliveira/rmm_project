from datetime import timedelta
import json
from time import perf_counter
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from agents.models import AgentMachine, InventorySnapshot
from dashboard.endpoint_list_data import load_endpoint_list_data
from dashboard import views


class EndpointListPerformanceTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.operator = get_user_model().objects.create_superuser('list-performance', password='synthetic')
        self.client.force_login(self.operator)

    def machine(self, index=0, **kwargs):
        machine, _ = AgentMachine.create_with_token(
            hostname=f'PC-{index:03}', machine_id=f'synthetic-{index}',
            agent_version='0.1.1.0-rc45', **kwargs,
        )
        return machine

    def snapshot(self, machine, age=0, **kwargs):
        return InventorySnapshot.objects.create(
            machine=machine, hostname=machine.hostname, collected_at=self.now - timedelta(minutes=age),
            received_at=self.now - timedelta(minutes=age), **kwargs,
        )

    def test_latest_usable_snapshot_and_empty_fallback_match_detail_contract(self):
        machine = self.machine()
        expected = self.snapshot(machine, 2, disks=[{'name': 'C:', 'size_bytes': 100, 'free_bytes': 50}])
        self.snapshot(machine, 1)
        self.snapshot(machine, 0, raw_payload={'collections': {}})
        empty = self.machine(1)
        latest_empty = self.snapshot(empty)
        absent = self.machine(2)
        with self.assertNumQueries(2):
            endpoints, snapshots = load_endpoint_list_data()
        self.assertEqual(len(endpoints), 3)
        self.assertEqual(snapshots[machine.pk].pk, expected.pk)
        self.assertEqual(snapshots[empty.pk].pk, latest_empty.pk)
        self.assertNotIn(absent.pk, snapshots)
        self.assertEqual(snapshots[machine.pk].pk, views.get_endpoint_detail_snapshot(machine).pk)

    def test_only_latest_thirty_are_considered_and_ties_are_stable(self):
        machine = self.machine()
        self.snapshot(machine, 40, defender_status={'enabled': True})
        for age in range(31):
            self.snapshot(machine, age)
        _, snapshots = load_endpoint_list_data()
        self.assertEqual(snapshots[machine.pk].received_at, self.now)
        self.snapshot(machine)
        _, snapshots = load_endpoint_list_data()
        newest = machine.inventory_snapshots.order_by('-received_at', '-pk').first()
        self.assertEqual(snapshots[machine.pk].pk, newest.pk)

    def test_collections_require_nonempty_object_and_system_projection_preserves_classification(self):
        for index, raw in enumerate([
            {'collections': {'system': {'product_type': 1, 'machine_type': 'notebook'}}},
            {'collections': {'full_inventory': {'system': {'product_type': 3}}}},
            {'collections': {'system': None, 'full_inventory': {'system': {'product_type': 3}}}},
            {'collections': 'invalid'}, {'collections': []}, {'collections': None}, {},
        ]):
            with self.subTest(raw=raw):
                machine = self.machine(index, os_name='Windows 11 Pro')
                self.snapshot(machine, 2, raw_payload={'collections': {'system': {'product_type': 1}}})
                self.snapshot(machine, 0, raw_payload=raw)
                expected = views.get_endpoint_detail_snapshot(machine)
                _, snapshots = load_endpoint_list_data()
                actual = snapshots[machine.pk]
                self.assertEqual(actual.pk, expected.pk)
                self.assertEqual(views.infer_endpoint_type(machine.hostname, machine.os_name, actual),
                                 views.infer_endpoint_type(machine.hostname, machine.os_name, expected))

    def test_query_count_does_not_grow_with_fleet_or_history(self):
        for index in range(40):
            machine = self.machine(index, os_name='Windows 11 Pro', status='online')
            self.snapshot(machine, 2, raw_payload={'collections': {'system': {'product_type': 1}}})
            self.snapshot(machine, 0)
        with patch('dashboard.views.latest_agent_version', return_value='0.1.1.0-rc46') as version:
            with self.assertNumQueries(2):
                endpoints, snapshots = load_endpoint_list_data()
                rows = [views.build_endpoint_row(endpoint, snapshot=snapshots.get(endpoint.pk),
                        recommended_version='0.1.1.0-rc46') for endpoint in endpoints]
        version.assert_not_called()
        self.assertEqual(len(rows), 40)
        self.assertTrue(all(row['agent_version_state'] == 'outdated' for row in rows))

    def test_real_view_resolves_version_once_and_filters_remain_equivalent(self):
        for index in range(6):
            machine = self.machine(index, status='online' if index % 2 else 'offline',
                                   domain='control.local', last_logged_user='CONTROL\\synthetic')
            self.snapshot(machine, os_name='Windows 11 Pro',
                          raw_payload={'collections': {'system': {'product_type': 1}}},
                          defender_status={'enabled': True, 'real_time_protection_enabled': True})
        parameters = {'q': 'PC-', 'status': 'online', 'domain': 'control.local', 'type': 'workstation'}
        with patch('dashboard.views.latest_agent_version', return_value='0.1.1.0-rc46') as version:
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(reverse('endpoint-list'), parameters)
        self.assertEqual(response.status_code, 200)
        version.assert_called_once_with()
        snapshot_queries = [q['sql'] for q in queries if 'FROM "agents_inventorysnapshot"' in q['sql']]
        self.assertEqual(len(snapshot_queries), 2)
        self.assertNotRegex(snapshot_queries[-1], r'(?:SELECT |, )"agents_inventorysnapshot"\."raw_payload"(?:,| FROM)')
        self.assertNotRegex(snapshot_queries[-1], r'(?:SELECT |, )"agents_inventorysnapshot"\."installed_software"(?:,| FROM)')
        self.assertEqual(response.context['total_endpoints'], 3)
        self.assertEqual(len(response.context['rows']), 3)
        self.assertIn('Windows 11 Pro', response.context['os_options'])
        with patch('dashboard.views.latest_agent_version', return_value='0.1.1.0-rc46'):
            old_rows = [views.build_endpoint_row(endpoint) for endpoint in AgentMachine.objects.order_by('hostname', 'domain')]
        filters = views.endpoint_filters_from_request(response.wsgi_request)
        expected = views.filter_endpoint_rows(old_rows, filters)
        self.assertEqual([row['endpoint'].pk for row in response.context['rows']],
                         [row['endpoint'].pk for row in expected])
        self.assertEqual(views.endpoint_summary_counts(expected)['total_endpoints'], response.context['total_endpoints'])

    def test_security_error_and_absent_inventory_are_not_promoted_to_protected(self):
        machine = self.machine(status='online')
        self.snapshot(machine, defender_status={'enabled': True, 'real_time_protection_enabled': True,
                                              'raw': {'error': 'synthetic-error'}})
        absent = self.machine(1)
        endpoints, snapshots = load_endpoint_list_data()
        rows = {endpoint.pk: views.build_endpoint_row(endpoint, snapshot=snapshots.get(endpoint.pk),
                recommended_version='') for endpoint in endpoints}
        self.assertEqual(rows[machine.pk]['defender_key'], 'unknown')
        self.assertEqual(rows[absent.pk]['defender_key'], 'unknown')

    def test_other_callers_still_use_the_existing_snapshot_reader(self):
        machine = self.machine()
        with patch('dashboard.views.get_endpoint_detail_snapshot', return_value=None) as reader:
            with patch('dashboard.views.latest_agent_version', return_value=''):
                views.build_endpoint_row(machine)
        reader.assert_called_once_with(machine)

    def test_software_count_is_preserved_without_transferring_the_software_payload(self):
        for index, software in enumerate([[], [{'name': 'Synthetic'}], {'name': 'Legacy'}, 'Legacy']):
            machine = self.machine(index)
            expected = self.snapshot(machine, installed_software=software)
            _, snapshots = load_endpoint_list_data()
            with self.assertNumQueries(0):
                row = views.build_endpoint_row(machine, snapshot=snapshots[machine.pk], recommended_version='')
            self.assertEqual(row['software_count'], len(views.ensure_list(expected.installed_software)))

    def test_empty_filters_and_early_rejection_keep_all_filter_options(self):
        a = self.machine(0, status='online', os_name='Windows 11 Pro', domain='control.local')
        b = self.machine(1, status='offline', os_name='Windows Server 2019', domain='legacy.local')
        self.snapshot(a)
        self.snapshot(b)
        for parameters, expected_calls in [({'status': 'online'}, 1), ({'domain': 'absent.local'}, 0),
                                            ({'os': 'Windows Server 2019'}, 1)]:
            with self.subTest(parameters=parameters):
                with patch('dashboard.views.build_endpoint_row', wraps=views.build_endpoint_row) as build:
                    with patch('dashboard.views.latest_agent_version', return_value=''):
                        response = self.client.get(reverse('endpoint-list'), parameters)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(build.call_count, expected_calls)
                self.assertEqual(response.context['domain_options'], ['control.local', 'legacy.local'])
                self.assertEqual(response.context['os_options'], ['Windows 11 Pro', 'Windows Server 2019'])
                self.assertEqual(response.context['total_endpoints'], expected_calls)

    def test_request_has_no_cross_request_snapshot_or_version_cache(self):
        machine = self.machine(status='online')
        self.snapshot(machine, 1, os_name='Windows 10 Pro')
        with patch('dashboard.views.latest_agent_version', return_value='0.1.1.0-rc45'):
            first = self.client.get(reverse('endpoint-list'))
        self.snapshot(machine, os_name='Windows 11 Pro', disks=[{'name': 'C:', 'size_bytes': 100, 'free_bytes': 20}])
        with patch('dashboard.views.latest_agent_version', return_value='0.1.1.0-rc46'):
            second = self.client.get(reverse('endpoint-list'))
        self.assertEqual(first.context['rows'][0]['os_name'], 'Windows 10 Pro')
        self.assertEqual(second.context['rows'][0]['os_name'], 'Windows 11 Pro')
        self.assertEqual(second.context['rows'][0]['agent_version_state'], 'outdated')

    def test_all_filter_families_and_totals_match_the_original_rows(self):
        for index in range(4):
            machine = self.machine(index, status='online' if index % 2 else 'offline',
                                   domain='control.local', last_logged_user='CONTROL\\synthetic',
                                   last_seen_at=self.now)
            self.snapshot(machine, os_name='Windows 11 Pro',
                          raw_payload={'collections': {'system': {'product_type': 1,
                                       'machine_type': 'notebook' if index == 1 else 'workstation'}}},
                          disks=[{'name': 'C:', 'size_bytes': 100, 'free_bytes': 2 if index == 1 else 50}],
                          defender_status={'enabled': True, 'real_time_protection_enabled': True} if index % 2 else {})
        with patch('dashboard.views.latest_agent_version', return_value='0.1.1.0-rc46'):
            original = [views.build_endpoint_row(endpoint) for endpoint in AgentMachine.objects.order_by('hostname', 'domain')]
            cases = [{}, {'q': 'missing'}, {'q': 'synthetic'}, {'defender': 'unknown'},
                     {'defender': 'ok'}, {'disk': 'critical'}, {'type': 'notebook'},
                     {'agent': 'outdated'}, {'attention': '1'}, {'sector': 'Usuario final'},
                     {'status': 'online', 'type': 'notebook', 'disk': 'critical'}]
            cases += [{'quick': quick} for quick in ['critical', 'offline', 'servers', 'workstations',
                       'agent_outdated', 'security', 'disk_full']]
            for criteria in cases:
                with self.subTest(criteria=criteria):
                    response = self.client.get(reverse('endpoint-list'), criteria)
                    filters = views.endpoint_filters_from_request(response.wsgi_request)
                    expected = views.filter_endpoint_rows(original, filters)
                    self.assertEqual([row['endpoint'].pk for row in response.context['rows']],
                                     [row['endpoint'].pk for row in expected])
                    for key, value in views.endpoint_summary_counts(expected).items():
                        self.assertEqual(response.context[key], value, key)
                    for key, value in views.endpoint_filter_options(original).items():
                        self.assertEqual(response.context[key], value, key)

    def test_synthetic_94_host_comparison(self):
        inventory = []
        for index in range(94):
            machine = self.machine(index, status='online', os_name='Windows 11 Pro')
            for age in range(30):
                inventory.append(InventorySnapshot(
                    machine=machine, hostname=machine.hostname, collected_at=self.now - timedelta(minutes=age),
                    received_at=self.now - timedelta(minutes=age),
                    installed_software=[{'name': 'Synthetic Office', 'metadata': 'x' * 2000}],
                    raw_payload={'collections': {'system': {'product_type': 1}}, 'unused': 'x' * 2000},
                ))
        InventorySnapshot.objects.bulk_create(inventory)
        start = perf_counter()
        with CaptureQueriesContext(connection) as before:
            old_rows = [views.build_endpoint_row(endpoint) for endpoint in AgentMachine.objects.order_by('hostname', 'domain')]
        before_ms = (perf_counter() - start) * 1000
        start = perf_counter()
        with CaptureQueriesContext(connection) as after:
            endpoints, snapshots = load_endpoint_list_data()
            version = views.latest_agent_version()
            new_rows = [views.build_endpoint_row(endpoint, snapshot=snapshots.get(endpoint.pk),
                        recommended_version=version) for endpoint in endpoints]
        after_ms = (perf_counter() - start) * 1000
        self.assertEqual(len(after), 3)
        self.assertGreater(len(before), len(after))
        for old, new in zip(old_rows, new_rows):
            for key in old.keys() - {'endpoint', 'snapshot'}:
                self.assertEqual(old[key], new[key], key)
        print(json.dumps({'fixture': 'synthetic_94_hosts_30_snapshots', 'database': connection.vendor,
                          'before_queries': len(before), 'after_queries': len(after),
                          'before_ms': round(before_ms, 1), 'after_ms': round(after_ms, 1)}))
