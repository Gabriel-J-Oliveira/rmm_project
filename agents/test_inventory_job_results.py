import uuid

from django.test import Client, TestCase
from django.utils import timezone

from .models import AgentJob, AgentJobResultReceipt, AgentMachine, InventorySnapshot


class InventoryJobResultTests(TestCase):
    def setUp(self):
        self.token = 'synthetic-inventory-test-token'
        self.machine = AgentMachine(machine_id=str(uuid.uuid4()), hostname='LAB-INVENTORY')
        self.machine.set_agent_token(self.token)
        self.machine.save()
        self.client = Client(HTTP_AUTHORIZATION=f'Bearer {self.token}')

    def full_inventory(self):
        physical_disk = {
            'model': 'Synthetic SSD', 'serial_number': None, 'firmware_version': '1.0',
            'size_bytes': 107374182400, 'media_type': 'ssd', 'bus_type': 'sas',
            'health_status': 'healthy', 'is_system_disk': None, 'drive_letters': [],
            'association_source': 'none', 'association_confidence': 'none',
        }
        return {
            'machine_id': self.machine.machine_id,
            'agent_version': '0.1.1.0-rc44',
            'collected_at': timezone.now().isoformat(),
            'system': {'hostname': 'LAB-INVENTORY', 'os': {'name': 'Windows Server'}},
            'hardware': {
                'manufacturer': 'Synthetic Vendor', 'model': 'Synthetic Model',
                'cpu': {'name': 'Synthetic CPU', 'physical_cores': 8, 'logical_processors': 16},
                'memory_total_bytes': 17179869184,
                'memory': {
                    'total_bytes': 17179869184, 'slots_total': 2, 'slots_used': 1,
                    'slots_free': 1, 'modules': [{'capacity_bytes': 17179869184, 'speed_mhz': 3200}],
                },
                'physical_disks': [physical_disk],
                'battery': {'present': False, 'status': 'not_present', 'health_percent': None},
            },
            'network': {'ips': ['192.0.2.10']},
            'disks': [
                {'letter': 'C:', 'size_bytes': 1000, 'free_bytes': 400},
                {'letter': 'D:', 'size_bytes': 2000, 'free_bytes': 800},
                {'letter': 'E:', 'size_bytes': 3000, 'free_bytes': 1200},
            ],
            'software': [{'name': 'Synthetic App'}],
            'security': {'defender': {'antivirus_enabled': True}},
            'patches': {'updates_available': 0},
        }

    def post_result(self, job_type, result, *, result_id=None):
        job = AgentJob.objects.create(endpoint=self.machine, job_type=job_type)
        result_id = result_id or str(uuid.uuid4())
        response = self.client.post(
            '/api/agent/jobs/result/',
            data={'job_id': str(job.id), 'status': 'completed', 'exit_code': 0, 'result': result},
            content_type='application/json', HTTP_IDEMPOTENCY_KEY=result_id,
        )
        job.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(job.status, AgentJob.STATUS_COMPLETED)
        self.assertEqual(job.result, result)
        self.assertTrue(AgentJobResultReceipt.objects.filter(job=job, result_id=result_id).exists())
        return job, response

    def test_wrapped_full_inventory_materializes_real_sections_and_legacy_volumes(self):
        inventory = self.full_inventory()
        job, _ = self.post_result(AgentJob.TYPE_FORCE_INVENTORY, {
            'agent_version': inventory['agent_version'],
            'machine_id': inventory['machine_id'],
            'output': inventory,
            'output_truncated': False,
        })
        snapshot = InventorySnapshot.objects.get(machine=self.machine)
        sections = snapshot.raw_payload['collections']
        self.assertEqual(sections['full_inventory'], inventory)
        for name in ('hardware', 'system', 'network', 'disk', 'software', 'security'):
            self.assertIn(name, sections)
        hardware = sections['hardware']
        for name in ('memory', 'cpu', 'physical_disks', 'battery'):
            self.assertEqual(hardware[name], inventory['hardware'][name])
        self.assertEqual(hardware['physical_disks'][0]['association_source'], 'none')
        self.assertEqual(hardware['physical_disks'][0]['association_confidence'], 'none')
        self.assertIsNone(hardware['physical_disks'][0]['is_system_disk'])
        self.assertEqual(hardware['physical_disks'][0]['drive_letters'], [])
        self.assertEqual(hardware['battery'], {'present': False, 'status': 'not_present', 'health_percent': None})
        self.assertEqual(sections['disk']['disks'], inventory['disks'])
        self.assertEqual(len(snapshot.disks), 3)
        self.assertEqual([row['letter'] for row in snapshot.disks], ['C:', 'D:', 'E:'])
        self.assertEqual(snapshot.manufacturer, 'Synthetic Vendor')
        self.assertEqual(snapshot.model, 'Synthetic Model')
        self.assertEqual(snapshot.cpu, 'Synthetic CPU')
        self.assertEqual(snapshot.memory_total_bytes, 17179869184)
        self.assertNotEqual(snapshot.disks, hardware['physical_disks'])
        self.assertEqual(job.result['output'], inventory)

    def test_direct_full_inventory_remains_supported(self):
        inventory = self.full_inventory()
        self.post_result(AgentJob.TYPE_FORCE_INVENTORY, inventory)
        snapshot = InventorySnapshot.objects.get(machine=self.machine)
        self.assertEqual(snapshot.raw_payload['collections']['full_inventory'], inventory)
        self.assertEqual(snapshot.manufacturer, 'Synthetic Vendor')
        self.assertEqual(len(snapshot.disks), 3)

    def test_wrapped_collection_jobs_materialize_their_own_sections(self):
        cases = (
            (AgentJob.TYPE_COLLECT_DISKS, 'disk', {'disks': [{'letter': 'C:', 'size_bytes': 1000}]}),
            (AgentJob.TYPE_COLLECT_SECURITY, 'security', {'defender': {'antivirus_enabled': True}}),
            (AgentJob.TYPE_COLLECT_SOFTWARE, 'software', {'installed_software': [{'name': 'Synthetic App'}]}),
            (AgentJob.TYPE_WINDOWS_UPDATE_SCAN, 'patches', {'updates_available': 0}),
        )
        for job_type, section, output in cases:
            with self.subTest(job_type=job_type):
                before = InventorySnapshot.objects.filter(machine=self.machine).count()
                self.post_result(job_type, {
                    'agent_version': '0.1.1.0-rc44', 'machine_id': self.machine.machine_id,
                    'output': output, 'output_truncated': False,
                })
                self.assertEqual(InventorySnapshot.objects.filter(machine=self.machine).count(), before + 1)
                snapshot = InventorySnapshot.objects.filter(machine=self.machine).latest('created_at')
                for key, value in output.items():
                    self.assertEqual(snapshot.raw_payload['collections'][section][key], value)
                if job_type == AgentJob.TYPE_COLLECT_DISKS:
                    self.assertEqual(snapshot.disks[0]['letter'], 'C:')
                if job_type == AgentJob.TYPE_COLLECT_SOFTWARE:
                    self.assertEqual(snapshot.installed_software, output['installed_software'])

    def test_direct_collection_job_remains_supported(self):
        self.post_result(AgentJob.TYPE_COLLECT_DISKS, {'disks': [{'letter': 'C:', 'size_bytes': 1000}]})
        snapshot = InventorySnapshot.objects.get(machine=self.machine)
        self.assertEqual(snapshot.disks[0]['letter'], 'C:')

    def test_truncated_output_keeps_job_and_receipt_without_snapshot(self):
        with self.assertLogs('agents.views', level='WARNING') as captured:
            self.post_result(AgentJob.TYPE_FORCE_INVENTORY, {
                'machine_id': self.machine.machine_id, 'agent_version': '0.1.1.0-rc44',
                'output': {'type': 'force_inventory', 'preview': 'synthetic incomplete data'},
                'output_truncated': True,
            })
        self.assertFalse(InventorySnapshot.objects.filter(machine=self.machine).exists())
        self.assertIn('reason=output_truncated', '\n'.join(captured.output))

    def test_top_level_truncation_does_not_create_snapshot(self):
        job = AgentJob.objects.create(endpoint=self.machine, job_type=AgentJob.TYPE_FORCE_INVENTORY)
        with self.assertLogs('agents.views', level='WARNING'):
            response = self.client.post('/api/agent/jobs/result/', data={
                'job_id': str(job.id), 'status': 'completed', 'output_truncated': True,
                'result': {'output': self.full_inventory(), 'output_truncated': False},
            }, content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        job.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(job.output_truncated)
        self.assertEqual(job.status, AgentJob.STATUS_COMPLETED)
        self.assertFalse(InventorySnapshot.objects.filter(machine=self.machine).exists())
        self.assertEqual(AgentJobResultReceipt.objects.filter(job=job).count(), 1)

    def test_missing_and_invalid_output_do_not_create_snapshots(self):
        cases = (
            {'machine_id': self.machine.machine_id, 'agent_version': '0.1.1.0-rc44'},
            {'output': None},
            {'output': 'not valid JSON'},
            {'output': []},
            {'output': {'hardware': {'manufacturer': 'Synthetic Vendor'}}},
        )
        for result in cases:
            with self.subTest(result=result), self.assertLogs('agents.views', level='WARNING'):
                self.post_result(AgentJob.TYPE_FORCE_INVENTORY, result)
        self.assertFalse(InventorySnapshot.objects.filter(machine=self.machine).exists())

    def test_conflicting_envelope_identity_is_not_silently_overwritten(self):
        inventory = self.full_inventory()
        with self.assertLogs('agents.views', level='WARNING') as captured:
            self.post_result(AgentJob.TYPE_FORCE_INVENTORY, {
                'machine_id': str(uuid.uuid4()), 'output': inventory, 'output_truncated': False,
            })
        self.assertFalse(InventorySnapshot.objects.filter(machine=self.machine).exists())
        self.assertIn('reason=machine_id_conflict', '\n'.join(captured.output))

    def test_envelope_metadata_fills_missing_inner_fields(self):
        inventory = self.full_inventory()
        inventory.pop('machine_id')
        inventory.pop('agent_version')
        self.post_result(AgentJob.TYPE_FORCE_INVENTORY, {
            'machine_id': self.machine.machine_id,
            'agent_version': '0.1.1.0-rc44',
            'output': inventory,
            'output_truncated': False,
        })
        full = InventorySnapshot.objects.get(machine=self.machine).raw_payload['collections']['full_inventory']
        self.assertEqual(full['machine_id'], self.machine.machine_id)
        self.assertEqual(full['agent_version'], '0.1.1.0-rc44')

    def test_result_replay_does_not_materialize_a_second_snapshot(self):
        job = AgentJob.objects.create(endpoint=self.machine, job_type=AgentJob.TYPE_FORCE_INVENTORY)
        result_id = str(uuid.uuid4())
        payload = {
            'job_id': str(job.id), 'status': 'completed',
            'result': {'output': self.full_inventory(), 'output_truncated': False},
        }
        first = self.client.post('/api/agent/jobs/result/', data=payload, content_type='application/json',
                                 HTTP_IDEMPOTENCY_KEY=result_id)
        second = self.client.post('/api/agent/jobs/result/', data=payload, content_type='application/json',
                                  HTTP_IDEMPOTENCY_KEY=result_id)
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertTrue(second.json()['duplicate'])
        self.assertEqual(InventorySnapshot.objects.filter(machine=self.machine).count(), 1)
        self.assertEqual(AgentJobResultReceipt.objects.filter(job=job).count(), 1)

    def test_direct_inventory_api_remains_unchanged(self):
        response = self.client.post('/api/agent/inventory/hardware/', data={
            'machine_id': self.machine.machine_id, 'manufacturer': 'Synthetic Vendor',
            'model': 'Synthetic Model', 'cpu': {'name': 'Synthetic CPU'},
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(InventorySnapshot.objects.get(machine=self.machine).manufacturer, 'Synthetic Vendor')
