import uuid

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import AgentMachine, AssetAgentLink, PhysicalAsset
from .asset_registry import endpoint_asset_payload
from dashboard.views import row_matches_query


class EndpointAssetCodeSearchTests(TestCase):
    def setUp(self):
        operator = get_user_model().objects.create_superuser('asset-search-operator', password='synthetic-only')
        self.client.force_login(operator)

    def endpoint(self, index):
        endpoint, _ = AgentMachine.create_with_token(
            hostname=f'ASSET-SEARCH-{index:02}', machine_id=str(uuid.uuid4()),
            status=AgentMachine.STATUS_OFFLINE, os_name='Windows 11 Pro',
        )
        return endpoint

    def test_row_search_is_case_insensitive_for_provisional_asset_code(self):
        self.assertTrue(row_matches_query({
            'hostname': 'PC-1', 'domain': '', 'logged_user': '', 'sector': '', 'tag': '',
            'endpoint_type_label': 'Workstation', 'primary_ip': '', 'os_name': '',
            'asset_code': 'PROV-AABBCCDD',
        }, 'prov-aabb'))

    def test_new_agent_with_same_meaningful_serial_only_receives_a_suggestion(self):
        original = self.endpoint(1)
        asset = PhysicalAsset.objects.create(serial_number='SAME-PHYSICAL-01')
        AssetAgentLink.objects.create(
            asset=asset, endpoint=original, endpoint_uuid_snapshot=original.pk,
            machine_id_snapshot=original.machine_id, hostname_snapshot=original.hostname,
        )
        replacement, _ = AgentMachine.create_with_token(
            hostname='RENAMED-AFTER-REINSTALL', machine_id=str(uuid.uuid4()),
            serial_number='same physical 01',
        )
        payload = endpoint_asset_payload(replacement)
        self.assertEqual(payload['state'], 'unlinked')
        self.assertEqual(payload['suggestion']['id'], str(asset.pk))
        self.assertEqual(AssetAgentLink.objects.filter(asset=asset, ended_at__isnull=True).count(), 1)

    def test_q_matches_provisional_code_before_pagination_and_in_ajax_views(self):
        for index in range(10):
            self.endpoint(index)
        target = self.endpoint(10)
        asset = PhysicalAsset.objects.create(serial_number='SEARCH-ONLY-01', manufacturer='Example')
        AssetAgentLink.objects.create(
            asset=asset, endpoint=target, endpoint_uuid_snapshot=target.pk,
            machine_id_snapshot=target.machine_id, hostname_snapshot=target.hostname,
        )
        url = reverse('endpoint-list')

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url, {'q': asset.asset_code.lower(), 'page': 1, 'page_size': 10})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['result_count'], 1)
        self.assertEqual(response.context['rows'][0]['endpoint'].pk, target.pk)
        self.assertContains(response, asset.asset_code)
        link_reads = [query for query in queries.captured_queries if 'agents_assetagentlink' in query['sql'].lower()]
        self.assertEqual(len(link_reads), 1, 'active asset links are mapped in one fleet query, not per endpoint')

        for view in ('table', 'cards', 'health'):
            with self.subTest(view=view):
                response = self.client.get(
                    url, {'q': asset.asset_code.lower(), 'page': 1, 'page_size': 10, 'view': view},
                    HTTP_X_REQUESTED_WITH='XMLHttpRequest',
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload['view'], view)
                self.assertEqual(payload['result_count'], 1)
                self.assertEqual(payload['endpoint_ids'], [str(target.pk)])
                self.assertIn(asset.asset_code, payload['html'])

    def test_stale_machine_snapshot_is_not_searchable_in_any_endpoint_view(self):
        endpoint = self.endpoint(1)
        asset = PhysicalAsset.objects.create()
        link = AssetAgentLink.objects.create(
            asset=asset, endpoint=endpoint, endpoint_uuid_snapshot=endpoint.pk,
            machine_id_snapshot=str(uuid.uuid4()),
        )
        for view in ('table', 'cards', 'health'):
            with self.subTest(view=view):
                response = self.client.get(reverse('endpoint-list'), {'q': asset.asset_code, 'view': view},
                                           HTTP_X_REQUESTED_WITH='XMLHttpRequest')
                self.assertEqual(response.json()['result_count'], 0)
                self.assertNotIn(asset.asset_code, response.json()['html'])
        link.machine_id_snapshot = f' {endpoint.machine_id.upper()} '
        link.save(update_fields=['machine_id_snapshot'])
        response = self.client.get(reverse('endpoint-list'), {'q': asset.asset_code})
        self.assertEqual(response.context['result_count'], 1)
