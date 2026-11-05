import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .identity import IdentityConflict
from .models import AgentMachine, InventorySnapshot
from .services import record_collection, record_heartbeat


class LightweightHeartbeatTests(TestCase):
    def setUp(self):
        self.machine, self.token = AgentMachine.create_with_token(
            hostname='CS-LIGHT', domain='control.local', machine_id='lightweight-machine', status='online')
        self.at = timezone.now() - timedelta(hours=2)
        self.collection = {
            'machine_id': self.machine.machine_id, 'agent_version': '0.1.1.0-rc45',
            'collected_at': self.at.isoformat(),
            'system': {'hostname': self.machine.hostname, 'domain': 'control.local',
                       'os': {'name': 'Windows 11 Pro', 'version': '10.0', 'build': '26100'},
                       'logged_user': 'CONTROL\\synthetic.user'},
            'hardware': {'cpu': {'name': 'Synthetic CPU'}, 'memory_total_bytes': 16 * 1024**3,
                         'manufacturer': 'Synthetic vendor', 'model': 'Synthetic model',
                         'serial_number': 'SYNTHETIC-123'},
            'network': {'ips': ['192.168.100.20']},
            'disks': [{'name': 'C:', 'size_bytes': 100, 'free_bytes': 50}],
            'software': [{'name': 'Synthetic software', 'version': '1'}],
            'security': {'defender_enabled': True},
        }

    def heartbeat(self, **changes):
        payload = {'hostname': 'CS-LIGHT', 'machine_id': self.machine.machine_id,
                   'domain': 'control.local', 'fqdn': 'CS-LIGHT.control.local',
                   'agent_version': '0.1.1.0-rc46', 'agent_mode': 'dotnet-service',
                   'heartbeat_at': timezone.now(), 'uptime_seconds': 0}
        payload.update(changes)
        return record_heartbeat(self.machine, payload, json.loads(json.dumps(payload, default=str)))

    def test_light_heartbeat_preserves_machine_and_snapshot_inventory_with_origin(self):
        original = record_collection(self.machine, 'full_inventory', self.collection)
        for _ in range(35):
            heartbeat = self.heartbeat()
        self.machine.refresh_from_db()
        for field in ('os_name', 'os_version', 'windows_build', 'manufacturer', 'model', 'serial_number'):
            self.assertEqual(getattr(self.machine, field), getattr(original, field))
        for field in ('cpu', 'memory_total_bytes', 'disks', 'installed_software', 'defender_status'):
            self.assertEqual(getattr(heartbeat, field), getattr(original, field))
        self.assertEqual(heartbeat.logged_user, 'CONTROL\\synthetic.user')
        self.assertEqual(heartbeat.ips, ['192.168.100.20'])
        self.assertEqual(heartbeat.uptime_seconds, 0)
        self.assertEqual(heartbeat.raw_payload['snapshot_source'], 'heartbeat')
        self.assertEqual(heartbeat.raw_payload['collections'], original.raw_payload['collections'])
        origin = heartbeat.raw_payload['heartbeat_inventory']['inherited']['memory_total_bytes']
        self.assertEqual(origin['collected_at'], self.at.isoformat())
        self.assertNotIn('memory_total_bytes', heartbeat.raw_payload['heartbeat_inventory']['observed_fields'])
        original.refresh_from_db()
        self.assertNotIn('heartbeat_inventory', original.raw_payload)
        from dashboard.capacity_views import _inventory_map, _inventory_provenance
        capacity_snapshot = _inventory_map([self.machine.pk])[self.machine.pk]
        self.assertEqual(capacity_snapshot.pk, original.pk)
        self.assertEqual(_inventory_provenance(capacity_snapshot)['collected_at'], self.at.isoformat())
        self.assertEqual(self.machine.agent_version, '0.1.1.0-rc46')
        self.assertEqual(self.machine.status, 'online')
        self.assertGreater(self.machine.last_seen_at, self.at)

    def test_empty_unknown_fields_do_not_clear_valid_inventory_or_component_metadata(self):
        record_collection(self.machine, 'full_inventory', self.collection)
        self.heartbeat(agent={'version': '0.1.1.0-rc46', 'install_path': 'synthetic-path'})
        heartbeat = self.heartbeat(os={}, os_name='', os_version='', windows_build='',
                                   hardware={'manufacturer': '', 'model': '', 'serial_number': 'Default string',
                                             'memory_total_bytes': 0}, logged_user='', ips=[],
                                   installed_software=[], defender_status={}, agent={'install_path': ''})
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.os_name, 'Windows 11 Pro')
        self.assertEqual(self.machine.agent_install_path, 'synthetic-path')
        self.assertEqual(heartbeat.serial_number, 'SYNTHETIC-123')
        self.assertEqual(heartbeat.memory_total_bytes, 16 * 1024**3)
        self.assertTrue(heartbeat.installed_software)
        self.assertIn('os_name', heartbeat.raw_payload['heartbeat_inventory']['unknown_fields'])

    def test_never_collected_inventory_is_unknown_not_fabricated(self):
        heartbeat = self.heartbeat()
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.os_name, '')
        self.assertEqual(heartbeat.cpu, '')
        self.assertIsNone(heartbeat.memory_total_bytes)
        self.assertEqual(heartbeat.installed_software, [])
        self.assertNotIn('hardware', heartbeat.raw_payload)

    def test_legacy_complete_heartbeat_can_still_update_inventory(self):
        heartbeat = self.heartbeat(os={'name': 'Windows 10 Pro', 'version': '10.0', 'build': '19045'},
                                   hardware={'cpu': 'Legacy CPU', 'memory_total_bytes': 8 * 1024**3,
                                             'manufacturer': 'Legacy vendor', 'model': 'Legacy model',
                                             'serial_number': 'LEGACY-123'}, logged_user='CONTROL\\legacy.user',
                                   ips=['192.168.100.21'])
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.os_name, 'Windows 10 Pro')
        self.assertEqual(heartbeat.cpu, 'Legacy CPU')
        self.assertEqual(heartbeat.raw_payload['heartbeat_inventory']['inherited'], {})

    def test_explicit_unknown_nulls_are_accepted_without_erasing_inventory(self):
        record_collection(self.machine, 'full_inventory', self.collection)
        response = self.client.post('/api/agent/heartbeat/', data=json.dumps({
            'hostname': self.machine.hostname, 'machine_id': self.machine.machine_id,
            'os_name': None, 'os_version': None, 'windows_build': None, 'os': None,
            'hardware': {'cpu': None, 'manufacturer': None, 'model': None, 'serial_number': None,
                         'memory_total_bytes': None},
        }), content_type='application/json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(response.status_code, 200, response.content)
        self.machine.refresh_from_db()
        heartbeat = self.machine.inventory_snapshots.latest('received_at')
        self.assertEqual(self.machine.manufacturer, 'Synthetic vendor')
        self.assertEqual(heartbeat.cpu, 'Synthetic CPU')
        self.assertIn('os_name', heartbeat.raw_payload['heartbeat_inventory']['unknown_fields'])

    def test_legacy_heartbeat_source_date_survives_later_light_heartbeats(self):
        original = self.heartbeat(hardware={'cpu': 'Legacy CPU', 'memory_total_bytes': 8 * 1024**3})
        self.heartbeat()
        current = self.heartbeat()
        origin = current.raw_payload['heartbeat_inventory']['inherited']['cpu']
        self.assertEqual(origin['collected_at'], original.collected_at.isoformat())
        self.assertEqual(current.cpu, 'Legacy CPU')

    def test_fresh_inventory_and_legacy_heartbeat_replace_only_their_own_provenance(self):
        record_collection(self.machine, 'full_inventory', self.collection)
        reported = self.heartbeat(hardware={'cpu': 'New heartbeat CPU'})
        current = self.heartbeat()
        self.assertEqual(current.raw_payload['heartbeat_inventory']['inherited']['cpu']['collected_at'],
                         reported.collected_at.isoformat())
        record_collection(self.machine, 'software', {
            'collected_at': timezone.now().isoformat(), 'installed_software': [{'name': 'New application'}]})
        current = self.heartbeat()
        self.assertEqual(current.cpu, 'New heartbeat CPU')
        self.assertEqual(current.raw_payload['heartbeat_inventory']['inherited']['cpu']['collected_at'],
                         reported.collected_at.isoformat())
        fresh = {**self.collection, 'collected_at': timezone.now().isoformat()}
        record_collection(self.machine, 'full_inventory', fresh)
        current = self.heartbeat()
        self.assertEqual(current.cpu, 'Synthetic CPU')
        self.assertEqual(current.raw_payload['heartbeat_inventory']['inherited']['cpu']['collected_at'],
                         fresh['collected_at'])

    def test_bearer_light_heartbeat_accepted_and_wrong_machine_id_fails_closed(self):
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + self.token}
        response = self.client.post('/api/agent/heartbeat/', data=json.dumps({
            'hostname': 'CS-LIGHT', 'machine_id': self.machine.machine_id,
            'agent_version': '0.1.1.0-rc46'}), content_type='application/json', **headers)
        self.assertEqual(response.status_code, 200, response.content)
        count = InventorySnapshot.objects.count()
        response = self.client.post('/api/agent/heartbeat/', data=json.dumps({
            'hostname': 'CS-LIGHT', 'machine_id': 'other-machine'}), content_type='application/json', **headers)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(InventorySnapshot.objects.count(), count)

    def test_controlled_rename_and_hardware_conflict_rules_unchanged(self):
        record_collection(self.machine, 'full_inventory', self.collection)
        heartbeat = self.heartbeat(hostname='CS-RENAMED', fqdn='CS-RENAMED.control.local')
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.hostname, 'CS-RENAMED')
        self.assertEqual(heartbeat.serial_number, 'SYNTHETIC-123')
        count = InventorySnapshot.objects.count()
        with self.assertRaises(IdentityConflict):
            self.heartbeat(hostname='CS-RENAMED', fqdn='CS-RENAMED.control.local',
                           hardware={'serial_number': 'OTHER-HARDWARE'})
        self.assertEqual(InventorySnapshot.objects.count(), count)

    def test_only_valid_explicit_fields_update_the_projection(self):
        record_collection(self.machine, 'full_inventory', self.collection)
        heartbeat = self.heartbeat(os={'build': '26200'}, ips=['192.168.100.22'])
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.os_name, 'Windows 11 Pro')
        self.assertEqual(self.machine.windows_build, '26200')
        self.assertEqual(heartbeat.memory_total_bytes, 16 * 1024**3)
        self.assertEqual(heartbeat.ips, ['192.168.100.22'])
