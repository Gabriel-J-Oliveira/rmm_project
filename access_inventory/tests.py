import json
from pathlib import Path

from django.test import TestCase
from django.urls import reverse

from .agent_auth import create_inventory_agent_with_token
from .models import (
    ADGroup,
    ADOrganizationalUnit,
    ADUser,
    AclEntry,
    FileServer,
    Folder,
    InventoryAgent,
    InventoryAgentRun,
    Share,
)


BASE_DIR = Path(__file__).resolve().parent.parent


class InventoryAgentApiTests(TestCase):
    def setUp(self):
        self.agent, self.token = create_inventory_agent_with_token(
            name='FS01 Collector',
            hostname='FS01',
        )

    def auth_headers(self, token=None):
        return {'HTTP_X_NIGHTOWL_AGENT_TOKEN': token or self.token}

    def load_sample(self, name):
        path = BASE_DIR / 'sample_data' / 'access_inventory' / name
        return json.loads(path.read_text(encoding='utf-8'))

    def test_create_inventory_agent_hashes_token(self):
        agent = InventoryAgent.objects.get(pk=self.agent.pk)

        self.assertTrue(agent.token_hash)
        self.assertNotEqual(agent.token_hash, self.token)
        self.assertTrue(agent.enabled)

    def test_rejects_invalid_token(self):
        response = self.client.post(
            reverse('access-inventory-agent-heartbeat'),
            data={'version': '0.1.0'},
            content_type='application/json',
            **self.auth_headers(token='wrong-token'),
        )

        self.assertEqual(response.status_code, 401)

    def test_accepts_valid_token_and_records_heartbeat(self):
        response = self.client.post(
            reverse('access-inventory-agent-heartbeat'),
            data={'version': '0.1.0'},
            content_type='application/json',
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.version, '0.1.0')
        self.assertIsNotNone(self.agent.last_seen_at)
        self.assertTrue(
            InventoryAgentRun.objects.filter(
                agent=self.agent,
                run_type=InventoryAgentRun.RUN_HEARTBEAT,
                status=InventoryAgentRun.STATUS_SUCCESS,
            ).exists()
        )

    def test_file_acl_payload_imports_sample(self):
        payload = self.load_sample('file_acl_sample.json')

        response = self.client.post(
            reverse('access-inventory-agent-file-acl'),
            data=json.dumps(payload),
            content_type='application/json',
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(FileServer.objects.count(), 1)
        self.assertEqual(Share.objects.count(), 1)
        self.assertEqual(Folder.objects.count(), 2)
        self.assertEqual(AclEntry.objects.count(), 2)
        run = InventoryAgentRun.objects.get(run_type=InventoryAgentRun.RUN_FILE_ACL)
        self.assertEqual(run.status, InventoryAgentRun.STATUS_SUCCESS)
        self.assertGreater(run.items_created, 0)

    def test_ad_inventory_payload_imports_sample(self):
        payload = self.load_sample('ad_inventory_sample.json')

        response = self.client.post(
            reverse('access-inventory-agent-ad-inventory'),
            data=json.dumps(payload),
            content_type='application/json',
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ADOrganizationalUnit.objects.count(), 2)
        self.assertEqual(ADUser.objects.count(), 2)
        self.assertEqual(ADGroup.objects.count(), 2)
        run = InventoryAgentRun.objects.get(run_type=InventoryAgentRun.RUN_AD_INVENTORY)
        self.assertEqual(run.status, InventoryAgentRun.STATUS_SUCCESS)
        self.assertGreater(run.items_created, 0)
