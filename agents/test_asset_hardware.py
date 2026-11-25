import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .asset_hardware import observe_hardware
from .models import AgentJob, AgentMachine, AssetRecord, InventorySnapshot, PhysicalAsset


@override_settings(NIGHTOWL_AUTOMATIC_ASSETS_ENABLED=True)
class HardwareIdentityTests(TestCase):
    def setUp(self):
        self.key = uuid.uuid4()
        self.endpoint = self.machine()

    def machine(self):
        return AgentMachine.create_with_token(
            hostname='WORKSTATION', machine_id=str(uuid.uuid4()), status=AgentMachine.STATUS_ONLINE,
        )[0]

    def observe(self, endpoint, *, key=None, serial='ABC-123', hypervisor=False):
        snapshot = InventorySnapshot.objects.create(machine=endpoint, hostname=endpoint.hostname,
                                                     collected_at=timezone.now())
        return observe_hardware(endpoint.pk, snapshot, {
            'manufacturer': 'Vendor', 'model': 'Model', 'serial_number': serial,
            'oem_product': {'smbios_uuid': str(self.key if key is None else key), 'hypervisor_present': hypervisor},
        })

    def test_automatic_link_is_idempotent_and_recovers_after_removal(self):
        self.assertEqual(self.observe(self.endpoint).state, 'linked')
        self.assertEqual(self.observe(self.endpoint).state, 'linked')
        asset = PhysicalAsset.objects.get()
        AssetRecord.objects.create(asset=asset, record_type=AssetRecord.TYPE_FORMATTED,
                                   occurred_at=timezone.now(), description='Formatação')
        self.endpoint.delete()
        replacement = self.machine()
        self.assertEqual(self.observe(replacement, serial='').state, 'linked')
        self.assertEqual(PhysicalAsset.objects.get().pk, asset.pk)
        self.assertEqual(asset.records.count(), 1)
        self.assertEqual(asset.agent_links.count(), 2)
        self.assertEqual(asset.agent_links.get(ended_at__isnull=True).endpoint, replacement)

    def test_vm_invalid_duplicate_and_divergent_identities_do_not_reassign(self):
        self.assertEqual(self.observe(self.endpoint, key=uuid.UUID(int=0)).state, 'uuid_invalid')
        self.assertEqual(self.observe(self.endpoint, hypervisor=True).state, 'physical_unconfirmed')
        self.assertFalse(PhysicalAsset.objects.exists())
        self.assertEqual(self.observe(self.endpoint).state, 'linked')
        self.assertEqual(self.observe(self.machine()).state, 'uuid_duplicate')
        self.assertEqual(self.observe(self.endpoint, key=uuid.uuid4()).state, 'asset_identity_conflict')
        self.assertEqual(PhysicalAsset.objects.get().smbios_uuid, self.key)

    @override_settings(NIGHTOWL_AUTOMATIC_ASSETS_ENABLED=False)
    def test_flag_disables_creation_without_losing_evidence(self):
        self.assertEqual(self.observe(self.endpoint).state, 'eligible')
        self.assertFalse(PhysicalAsset.objects.exists())

    def test_validate_endpoint_creates_only_one_existing_inventory_job(self):
        operator = get_user_model().objects.create_superuser('operator')
        self.client.force_login(operator)
        url = reverse('api-endpoint-asset-records', args=[self.endpoint.pk])
        data = {'action': 'validate_link', 'machine_id': self.endpoint.machine_id}
        first = self.client.post(url, data, content_type='application/json')
        second = self.client.post(url, data, content_type='application/json')
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(AgentJob.objects.get().job_type, AgentJob.TYPE_FORCE_INVENTORY)
        self.assertFalse(PhysicalAsset.objects.exists())
