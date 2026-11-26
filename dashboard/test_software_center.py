import os
import subprocess
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from agents.models import AgentMachine, InventorySnapshot, SoftwarePackageRecord


class SoftwareCenterDataTests(TestCase):
    def setUp(self):
        self.operator = get_user_model().objects.create_superuser('software-center-operator')
        self.client.force_login(self.operator)

    def endpoint(self, hostname, token_digit):
        return AgentMachine.objects.create(
            hostname=hostname, machine_id=str(uuid.uuid4()), agent_token_hash=token_digit * 64,
            status=AgentMachine.STATUS_ONLINE, last_seen_at=timezone.now(),
        )

    def snapshot(self, endpoint, *, name='Acme Client', version='2.1', age=timedelta(minutes=5)):
        collected = timezone.now() - age
        return InventorySnapshot.objects.create(
            machine=endpoint, hostname=endpoint.hostname, collected_at=collected,
            received_at=timezone.now(), installed_software=[{
                'name': name, 'display_name': name, 'publisher': 'Acme Ltd', 'version': version,
            }],
        )

    def test_inventory_api_aggregates_real_latest_snapshots_and_freshness(self):
        current_endpoint = self.endpoint('LAB-SOFT-01', '1')
        stale_endpoint = self.endpoint('LAB-SOFT-02', '2')
        missing_endpoint = self.endpoint('LAB-SOFT-03', '3')
        self.snapshot(current_endpoint, version='2.1')
        self.snapshot(stale_endpoint, version='2.0', age=timedelta(hours=30))

        response = self.client.get(reverse('software-inventory-data'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload['items']), 1)
        item = payload['items'][0]
        self.assertEqual(item['name'], 'Acme Client')
        self.assertEqual(item['publisher'], 'Acme Ltd')
        self.assertEqual(item['versions'], ['2.0', '2.1'])
        self.assertEqual(item['endpoint_count'], 2)
        self.assertEqual((item['current_count'], item['stale_count']), (1, 1))
        self.assertEqual(item['unknown_count'], 0)
        self.assertEqual({entry['id'] for entry in item['endpoints']}, {str(current_endpoint.id), str(stale_endpoint.id)})
        self.assertEqual(payload['summary']['without_inventory_count'], 1)
        self.assertNotIn(str(missing_endpoint.id), {entry['id'] for entry in item['endpoints']})

    def test_empty_inventory_does_not_fall_back_to_demo_data(self):
        self.endpoint('LAB-EMPTY-01', '4')
        response = self.client.get(reverse('software-inventory-data'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['items'], [])
        self.assertEqual(response.json()['summary']['without_inventory_count'], 1)

    def test_future_collection_timestamp_is_reported_as_unknown(self):
        endpoint = self.endpoint('LAB-FUTURE-01', 'b')
        collected = timezone.now() + timedelta(hours=1)
        InventorySnapshot.objects.create(
            machine=endpoint, hostname=endpoint.hostname, collected_at=collected,
            received_at=timezone.now(), installed_software=[{'name': 'Clock App', 'version': '1.0'}],
        )

        payload = self.client.get(reverse('software-inventory-data')).json()

        self.assertEqual(payload['items'][0]['unknown_count'], 1)
        self.assertEqual(payload['summary']['unknown_endpoint_count'], 1)

    def test_inactive_endpoint_inventory_is_not_presented_as_current_inventory(self):
        endpoint = self.endpoint('LAB-REMOVED-01', 'a')
        endpoint.is_active = False
        endpoint.save(update_fields=['is_active'])
        self.snapshot(endpoint)

        payload = self.client.get(reverse('software-inventory-data')).json()

        self.assertEqual(payload['items'], [])
        self.assertEqual(payload['summary']['endpoint_count'], 0)
        self.assertEqual(payload['summary']['endpoints_with_inventory'], 0)

    def test_empty_upload_catalog_is_returned_as_empty_not_sample_data(self):
        response = self.client.get(reverse('software-packages-data'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['items'], [])
        self.assertContains(self.client.get(reverse('software-inventory')), 'data-software-packages-state')

    def test_inventory_search_uses_real_software_publisher_version_and_endpoint(self):
        endpoint = self.endpoint('LAB-SEARCH-01', '5')
        self.snapshot(endpoint, name='Night Owl Support', version='8.0')
        self.assertEqual(self.client.get(reverse('software-inventory-data'), {'q': 'LAB-SEARCH'}).json()['items'][0]['name'], 'Night Owl Support')
        self.assertEqual(self.client.get(reverse('software-inventory-data'), {'publisher': 'Acme Ltd'}).json()['items'][0]['name'], 'Night Owl Support')
        self.assertEqual(self.client.get(reverse('software-inventory-data'), {'q': 'no-such-software'}).json()['items'], [])

    def test_central_page_has_only_inventory_and_upload_sections_without_mock_dependency(self):
        response = self.client.get(reverse('software-inventory'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Softwares coletados')
        self.assertContains(response, 'Uploads')
        self.assertContains(response, 'Adicionar arquivo de instalação')
        self.assertContains(response, 'disabled')
        self.assertNotContains(response, 'MockNightowlApi')
        self.assertNotContains(response, 'Nova implantação')
        self.assertNotContains(response, 'Criar pacote mockado')

    def test_package_metadata_api_is_read_only_and_preserves_approval_audit_fields(self):
        record = SoftwarePackageRecord.objects.create(
            friendly_name='Cliente corporativo', original_filename='client_setup.msi',
            package_type=SoftwarePackageRecord.TYPE_MSI, version='4.2.0', size_bytes=4096,
            sha256='a' * 64, signature_status=SoftwarePackageRecord.SIGNATURE_VALID,
            signer='Acme Ltd', approval_status=SoftwarePackageRecord.STATUS_APPROVED,
            approved_at=timezone.now(), approved_by=self.operator, created_by=self.operator,
        )
        before = SoftwarePackageRecord.objects.count()
        response = self.client.get(reverse('software-packages-data'))
        item = response.json()['items'][0]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(item['id'], str(record.id))
        self.assertEqual(item['friendly_name'], 'Cliente corporativo')
        self.assertEqual(item['original_filename'], 'client_setup.msi')
        self.assertEqual(item['package_type'], 'msi')
        self.assertEqual(item['sha256'], 'a' * 64)
        self.assertEqual(item['approved_by'], self.operator.get_username())
        self.assertGreaterEqual(SoftwarePackageRecord.objects.count(), before)
        self.assertFalse(hasattr(record, 'binary_file'))
        self.assertEqual(self.client.post(reverse('software-packages-data')).status_code, 405)
        self.assertEqual(self.client.post(reverse('software-inventory-data')).status_code, 405)

    def test_package_catalog_rejects_invalid_type_at_model_validation(self):
        record = SoftwarePackageRecord(
            friendly_name='Invalid type', package_type='ps1',
        )
        with self.assertRaises(ValidationError):
            record.full_clean()

    def test_non_technical_operator_cannot_view_software_center_or_apis(self):
        user = get_user_model().objects.create_user('software-viewer')
        client = Client()
        client.force_login(user)
        self.assertIn(client.get(reverse('software-inventory')).status_code, {302, 403})
        self.assertIn(client.get(reverse('software-inventory-data')).status_code, {302, 403})
        self.assertIn(client.get(reverse('software-packages-data')).status_code, {302, 403})


@skipUnless(os.environ.get('NIGHTOWL_TEST_BROWSER_E2E') == '1', 'Explicit isolated browser gate required')
class SoftwareCenterVisualTests(StaticLiveServerTestCase):
    def test_software_center_real_data_drawer_responsive_layout_and_screenshots(self):
        operator = get_user_model().objects.create_superuser('software-center-browser')
        self.client.force_login(operator)
        endpoint = AgentMachine.objects.create(
            hostname='LAB-SOFTWARE-BROWSER', machine_id=str(uuid.uuid4()), agent_token_hash='9' * 64,
            status=AgentMachine.STATUS_ONLINE, last_seen_at=timezone.now(),
        )
        InventorySnapshot.objects.create(
            machine=endpoint, hostname=endpoint.hostname, collected_at=timezone.now(), received_at=timezone.now(),
            installed_software=[{'name': 'Visual App', 'publisher': 'Visual Vendor', 'version': '1.5'}],
        )
        old_endpoint = AgentMachine.objects.create(
            hostname='LAB-SOFTWARE-STALE', machine_id=str(uuid.uuid4()), agent_token_hash='8' * 64,
            status=AgentMachine.STATUS_ONLINE, last_seen_at=timezone.now(),
        )
        InventorySnapshot.objects.create(
            machine=old_endpoint, hostname=old_endpoint.hostname,
            collected_at=timezone.now() - timedelta(hours=30), received_at=timezone.now(),
            installed_software=[
                {'name': 'Visual App', 'publisher': 'Visual Vendor', 'version': '1.4'},
                {'name': 'Old Only App', 'publisher': 'Old Vendor', 'version': '0.8'},
            ],
        )
        SoftwarePackageRecord.objects.create(
            friendly_name='Visual Installer', original_filename='visual-installer.exe',
            package_type=SoftwarePackageRecord.TYPE_EXE, version='1.5', size_bytes=2048,
            sha256='b' * 64, signature_status=SoftwarePackageRecord.SIGNATURE_VALID,
            signer='Visual Vendor', approval_status=SoftwarePackageRecord.STATUS_DRAFT,
            created_by=operator,
        )
        result = subprocess.run(
            ['node', str(Path(settings.BASE_DIR) / 'tests/frontend/test_software_center_visual.cjs')],
            env={**os.environ, 'NIGHTOWL_E2E_SERVER': self.live_server_url,
                 'NIGHTOWL_E2E_SESSION': self.client.cookies['sessionid'].value},
            capture_output=True, encoding='utf-8', timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
