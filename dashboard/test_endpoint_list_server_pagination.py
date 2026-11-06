from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from agents.models import AgentMachine, InventorySnapshot


class EndpointListServerPaginationTests(TestCase):
    def setUp(self):
        operator = get_user_model().objects.create_superuser('server-page-test', password='synthetic')
        self.client.force_login(operator)

    def machine(self, index, *, status='online', domain='control.local'):
        machine, _ = AgentMachine.create_with_token(
            hostname=f'PAGE-{index:03}', machine_id=f'page-{index}', status=status,
            domain=domain, last_logged_user=f'CONTROL\\user{index}', os_name='Windows 11 Pro',
            last_seen_at=timezone.now(), agent_version='0.1.0',
        )
        InventorySnapshot.objects.create(
            machine=machine, hostname=machine.hostname, collected_at=timezone.now(),
            received_at=timezone.now(), os_name='Windows 11 Pro',
            disks=[{'name': 'C:', 'size_bytes': 100, 'free_bytes': 50}],
            defender_status={'enabled': True, 'real_time_protection_enabled': True},
            raw_payload={'collections': {'system': {'product_type': 1}}},
        )
        return machine

    def test_initial_response_contains_only_requested_page_but_counts_full_filtered_set(self):
        machines = [self.machine(index) for index in range(23)]
        response = self.client.get(reverse('endpoint-list'), {'page': 2, 'page_size': 10})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['result_count'], 23)
        self.assertEqual(response.context['total_endpoints'], 23)
        self.assertEqual(response.context['page_number'], 2)
        self.assertEqual(len(response.context['rows']), 10)
        self.assertEqual(response.context['rows'][0]['endpoint'].pk, machines[10].pk)
        self.assertNotContains(response, 'PAGE-000')
        self.assertContains(response, 'PAGE-010')
        self.assertContains(response, 'data-endpoint-loaded="false"')

    def test_filter_is_applied_before_page_and_counts_are_not_page_local(self):
        for index in range(14):
            self.machine(index, status='online' if index < 12 else 'offline')
        response = self.client.get(reverse('endpoint-list'), {'status': 'online', 'page': 2, 'page_size': 10})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['result_count'], 12)
        self.assertEqual(response.context['online_count'], 12)
        self.assertEqual(response.context['page_number'], 2)
        self.assertEqual(len(response.context['rows']), 2)
        self.assertTrue(all(row['endpoint'].status == AgentMachine.STATUS_ONLINE for row in response.context['rows']))

    def test_fragment_response_is_page_scoped_and_supports_lazy_views(self):
        machines = [self.machine(index) for index in range(21)]
        response = self.client.get(
            reverse('endpoint-list'), {'page': 3, 'page_size': 10, 'view': 'cards'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['view'], 'cards')
        self.assertEqual(payload['result_count'], 21)
        self.assertEqual(payload['page'], 3)
        self.assertEqual(payload['endpoint_ids'], [str(machines[20].pk)])
        self.assertIn('PAGE-020', payload['html'])
        self.assertNotIn('PAGE-000', payload['html'])

    def test_invalid_page_size_falls_back_and_out_of_range_page_is_clamped(self):
        machines = [self.machine(index) for index in range(12)]
        response = self.client.get(reverse('endpoint-list'), {'page': 99, 'page_size': 100})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_size'], 10)
        self.assertEqual(response.context['page_number'], 2)
        self.assertEqual(response.context['rows'][0]['endpoint'].pk, machines[10].pk)

    def test_page_size_25_and_50_are_server_enforced(self):
        for index in range(51):
            self.machine(index)
        page_25 = self.client.get(reverse('endpoint-list'), {'page_size': 25, 'page': 1})
        page_50 = self.client.get(reverse('endpoint-list'), {'page_size': 50, 'page': 1})
        last_page = self.client.get(reverse('endpoint-list'), {'page_size': 50, 'page': 2})

        self.assertEqual(len(page_25.context['rows']), 25)
        self.assertEqual(len(page_50.context['rows']), 50)
        self.assertEqual(len(last_page.context['rows']), 1)
        self.assertEqual(last_page.context['result_count'], 51)

    def test_fragment_keeps_endpoint_text_escaped(self):
        machine = self.machine(0)
        machine.hostname = '<img src=x onerror=alert(1)>'
        machine.save(update_fields=['hostname'])
        response = self.client.get(
            reverse('endpoint-list'), {'view': 'table'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('&lt;img', response.json()['html'])
        self.assertNotIn('<img', response.json()['html'])
