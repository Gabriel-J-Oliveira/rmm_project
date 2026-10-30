from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from .models import AgentMachine
from .services import record_collection, record_heartbeat


class HardwareCadenceTests(TestCase):
    def setUp(self):
        self.machine, _ = AgentMachine.create_with_token(hostname='SYNTH-DESKTOP', machine_id='synthetic-hardware')
        self.old = (timezone.now() - timedelta(days=2)).isoformat()
        self.now = timezone.now().isoformat()
        self.hardware = {
            'cpu': {'name': 'Synthetic CPU', 'max_clock_mhz': 3600, 'max_clock_ghz': 3.6},
            'memory_total_bytes': 24 * 1024**3,
            'baseboard': {'product': 'Synthetic Board', 'version': 'A1'},
            'memory': {'status': 'available', 'total_bytes': 24 * 1024**3, 'modules': [
                {'device_locator': 'DIMM1', 'serial_number': 'SYNTH-01', 'part_number': 'SYNTH-PART', 'capacity_bytes': 8 * 1024**3},
                {'device_locator': 'DIMM2', 'serial_number': 'SYNTH-02', 'capacity_bytes': 16 * 1024**3}]},
            'battery': {'present': True, 'collection_status': 'available', 'health_percent': 80, 'part_number': None},
            'detailed_collected_at': self.old,
        }
        self.original = self.collect(hardware=self.hardware, network={'adapters': [
            {'name': 'Ethernet', 'dns_servers': ['192.0.2.53'], 'ipv6_addresses': ['2001:db8::1']}],
            'ips': ['192.0.2.10']}, at=self.old)

    def collect(self, *, hardware=None, network=None, states=None, at=None, disks=None):
        payload = {'machine_id': self.machine.machine_id, 'collected_at': at or self.now,
                   'collection_scope': 'scheduled', 'hardware': hardware or {}, 'network': network or {},
                   'section_states': states or {}, 'disks': disks or []}
        return record_collection(self.machine, 'full_inventory', payload)

    def test_hourly_volumes_preserve_hardware_and_network_dates(self):
        current = self.collect(states={'disk': 'available'}, disks=[{'name': 'C:', 'size_bytes': 100, 'free_bytes': 10}])
        self.assertEqual(current.raw_payload['collections']['hardware']['collected_at'], self.old)
        self.assertEqual(current.raw_payload['collections']['network']['collected_at'], self.old)
        self.assertEqual(current.cpu, 'Synthetic CPU')
        self.assertEqual(current.memory_total_bytes, 24 * 1024**3)
        self.assertEqual(current.raw_payload['latest_collection_type'], 'scheduled_inventory')
        self.assertNotIn('full_inventory', current.raw_payload['collections'])

    def test_basic_does_not_erase_details_or_make_them_fresh(self):
        current = self.collect(hardware={'model': 'Synthetic Model', 'memory_total_bytes': 24 * 1024**3}, states={'hardware': 'available'})
        hardware = current.raw_payload['collections']['hardware']
        self.assertEqual(hardware['memory'], self.original.raw_payload['collections']['hardware']['memory'])
        self.assertEqual(hardware['detailed_collected_at'], self.old)
        self.assertEqual(hardware['cpu']['name'], 'Synthetic CPU')

    def test_failed_sections_retain_last_success_and_record_attempt(self):
        current = self.collect(hardware={'memory': {'status': 'failed', 'modules': []}, 'battery': {'collection_status': 'failed', 'present': None}},
                               states={'hardware': 'available', 'memory': 'failed', 'battery': 'failed'})
        hardware = current.raw_payload['collections']['hardware']
        self.assertEqual(hardware['memory']['modules'][0]['part_number'], 'SYNTH-PART')
        self.assertEqual(hardware['battery']['health_percent'], 80)
        self.assertEqual(current.raw_payload['inventory_observations']['battery']['status'], 'failed')
        self.assertEqual(hardware['battery'].get('collected_at'), self.original.raw_payload['collections']['hardware']['battery'].get('collected_at'))
        failed = self.collect(states={'hardware': 'failed', 'network': 'failed'})
        self.assertEqual(failed.raw_payload['collections']['network']['collected_at'], self.old)

    def test_confirmed_battery_absence_is_not_unknown(self):
        current = self.collect(hardware={'battery': {'collection_status': 'not_present', 'present': False, 'health_percent': None}}, states={'hardware': 'available', 'battery': 'not_present'})
        battery = current.raw_payload['collections']['hardware']['battery']
        self.assertIs(battery['present'], False)
        self.assertIsNone(battery['health_percent'])

    def test_legacy_direct_hardware_partial_preserves_previous_details(self):
        current = record_collection(self.machine, 'hardware', {
            'machine_id': self.machine.machine_id, 'collected_at': self.now,
            'memory': {'modules': []}, 'baseboard': {'product': None},
        })
        hardware = current.raw_payload['collections']['hardware']
        self.assertEqual(hardware['baseboard']['product'], 'Synthetic Board')
        self.assertEqual(len(hardware['memory']['modules']), 2)
        failed = record_collection(self.machine, 'hardware', {
            'machine_id': self.machine.machine_id, 'collected_at': self.now,
            'status': 'failed', 'memory': {'modules': []},
        })
        self.assertEqual(failed.raw_payload['collections']['hardware'], hardware)
        self.assertEqual(failed.raw_payload['inventory_observations']['hardware']['status'], 'failed')

    def test_module_missing_oem_preserved_only_for_same_serial_and_locator(self):
        current = self.collect(hardware={'memory': {'modules': [{'device_locator': 'DIMM1', 'serial_number': 'SYNTH-01', 'part_number': None}]}}, states={'hardware': 'available'})
        self.assertEqual(current.raw_payload['collections']['hardware']['memory']['modules'][0]['part_number'], 'SYNTH-PART')
        replaced = self.collect(hardware={'memory': {'modules': [{'device_locator': 'DIMM1', 'serial_number': 'SYNTH-NEW', 'part_number': None}]}}, states={'hardware': 'available'})
        self.assertIsNone(replaced.raw_payload['collections']['hardware']['memory']['modules'][0]['part_number'])

    def test_unknown_vm_topology_preserves_previous_evidence(self):
        current = self.collect(hardware={'memory': {'status': 'unknown', 'slots_total': None, 'modules': []}}, states={'hardware': 'available', 'memory': 'unknown'})
        self.assertEqual(len(current.raw_payload['collections']['hardware']['memory']['modules']), 2)
        self.assertEqual(current.raw_payload['inventory_observations']['memory']['status'], 'unknown')

    def test_detail_payload_exposes_real_oem_and_adapter_dns(self):
        from dashboard.views import build_endpoint_detail_payload
        with patch('dashboard.views.evaluate_agent_update_policy') as policy:
            policy.return_value.target_version = ''
            result = build_endpoint_detail_payload(self.machine, self.original, {'score': 100}, [], '', 'workstation', [], [], [])
        inventory = result['inventory']
        self.assertEqual(inventory['cpuDetails']['max_clock_mhz'], 3600)
        self.assertEqual(inventory['baseboardDetails']['product'], 'Synthetic Board')
        self.assertEqual(inventory['memoryDetails']['modules'][0]['part_number'], 'SYNTH-PART')
        self.assertEqual(inventory['adapters'][0]['dns_servers'], ['192.0.2.53'])

    def test_heartbeat_does_not_rejuvenate_detail(self):
        record_heartbeat(self.machine, {'machine_id': self.machine.machine_id, 'hostname': self.machine.hostname}, {})
        latest = self.machine.inventory_snapshots.order_by('-received_at').first()
        self.assertEqual(latest.raw_payload['collections']['hardware']['detailed_collected_at'], self.old)
        from dashboard.capacity_views import _inventory_map, _inventory_provenance, _hardware
        capacity = _inventory_map([self.machine.pk])[self.machine.pk]
        self.assertEqual(_inventory_provenance(capacity)['collected_at'], self.old)
        self.assertEqual(_hardware(capacity)['cpu']['max_clock_mhz'], 3600)
        self.original.refresh_from_db()
        self.assertEqual(self.original.raw_payload['collections']['hardware']['collected_at'], self.old)
