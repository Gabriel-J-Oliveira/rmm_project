import os
import subprocess
import uuid
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import LiveServerTestCase
from django.utils import timezone

from .models import AgentMachine, AssetAgentLink, AssetRecord, InventorySnapshot, PhysicalAsset


class AssetRegistryBrowserTests(LiveServerTestCase):
    @skipUnless(os.environ.get('NIGHTOWL_TEST_BROWSER_E2E') == '1', 'Explicit isolated browser gate required')
    def test_endpoint_records_create_and_manual_history_in_real_browser(self):
        operator = get_user_model().objects.create_superuser('asset-records-browser', password='synthetic-only')
        endpoint, _ = AgentMachine.create_with_token(
            hostname='SYNTHETIC-ASSET-RECORDS', machine_id=str(uuid.uuid4()),
            status=AgentMachine.STATUS_OFFLINE, os_name='Windows 11 Pro',
            serial_number='BROWSER-ASSET-01', manufacturer='Example Devices', model='Browser Workstation',
        )
        InventorySnapshot.objects.create(
            machine=endpoint, hostname=endpoint.hostname, collected_at=timezone.now(),
            received_at=timezone.now(), raw_payload={'collections': {'system': {
                'serial_number': 'BROWSER-ASSET-01', 'manufacturer': 'Example Devices',
                'model': 'Browser Workstation',
            }}},
        )
        self.client.force_login(operator)
        result = subprocess.run(
            ['node', str(Path(settings.BASE_DIR) / 'tests/frontend/test_endpoint_asset_records_e2e.cjs')],
            env={**os.environ, 'NIGHTOWL_E2E_SERVER': self.live_server_url,
                 'NIGHTOWL_E2E_SESSION': self.client.cookies['sessionid'].value,
                 'NIGHTOWL_E2E_ENDPOINT': str(endpoint.pk),
                 'NIGHTOWL_E2E_ENDPOINT_NAME': endpoint.hostname},
            capture_output=True, text=True, timeout=100,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(PhysicalAsset.objects.count(), 1)
        asset = PhysicalAsset.objects.get()
        self.assertEqual(asset.serial_normalized, 'browserasset01')
        self.assertEqual(AssetAgentLink.objects.filter(endpoint=endpoint, ended_at__isnull=True).count(), 1)
        self.assertEqual(AssetRecord.objects.filter(asset=asset, record_type=AssetRecord.TYPE_OBSERVATION).count(), 1)
        print(result.stdout.strip())
