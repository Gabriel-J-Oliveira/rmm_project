import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from agents.models import AgentMachine, EndpointAlert, EndpointPerformanceSample, InventorySnapshot, hash_agent_token
from .capacity_views import CAPACITY_PERMISSIONS


class HardwareInventoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'hardware-reviewer', password='synthetic-password', is_staff=True,
        )
        self.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='agents', codename__in=[name.split('.')[1] for name in CAPACITY_PERMISSIONS],
        ))
        self.client.force_login(self.user)
        self.machine = AgentMachine.objects.create(
            hostname='LAB-HARDWARE-01', fqdn='lab-hardware-01.example.test', status=AgentMachine.STATUS_ONLINE,
            machine_id=str(uuid.uuid4()), agent_token_hash=hash_agent_token('synthetic-hardware-secret'),
            last_logged_user='LAB\\operator',
        )

    def add_snapshot(self):
        return InventorySnapshot.objects.create(
            machine=self.machine, hostname=self.machine.hostname, collected_at=timezone.now(),
            os_name='Windows 11', os_version='24H2', manufacturer='Example Maker', model='Example Model',
            memory_total_bytes=16 * 1024 ** 3,
            disks=[{'name': 'C:', 'size_bytes': 1000, 'free_bytes': 250}],
            raw_payload={'collections': {'hardware': {
                'cpu': {'name': 'Example CPU', 'physical_cores': 8, 'logical_processors': 16, 'max_clock_mhz': 4200},
                'memory_total_bytes': 16 * 1024 ** 3,
                'memory': {'total_bytes': 16 * 1024 ** 3, 'slots_total': 2, 'slots_used': 1,
                           'slots_free': 1, 'modules': [{'manufacturer': 'Memory Co', 'part_number': 'PART-1',
                                                       'capacity_bytes': 16 * 1024 ** 3, 'speed_mhz': 3200}]},
                'motherboard': {'product': 'Board 1'},
                'battery': {'present': False, 'health_percent': None},
                'physical_disks': [{'model': 'SSD', 'size_bytes': 1000, 'media_type': 'SSD',
                                    'association_confidence': 'high', 'drive_letters': ['C:']}],
            }}, 'inventory_observations': {'hardware': {'status': 'available'}}},
        )

    def test_page_alias_and_legacy_url_render_inventory(self):
        for name in ('hardware-inventory', 'capacity-page'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Inventário de Hardware')
            self.assertContains(response, reverse('api-hardware-inventory-overview'))

    def test_overview_is_inventory_only_and_does_not_query_performance_samples(self):
        snapshot = self.add_snapshot()
        EndpointPerformanceSample.objects.create(endpoint=self.machine, sample_id=uuid.uuid4(),
                                                 collected_at=timezone.now(), cpu_percent=99,
                                                 collection_duration_ms=1)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('api-hardware-inventory-overview'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(any('agents_endpointperformancesample' in item['sql'].lower() for item in queries))
        row = response.json()['endpoints'][0]
        self.assertEqual(row['inventory_at'], snapshot.collected_at.isoformat())
        self.assertEqual(row['cpu']['physical_cores'], 8)
        self.assertEqual(row['memory']['slots_used'], 1)
        self.assertEqual(row['max_disk_used_percent'], 75)
        self.assertNotIn('cpu_p95', row)
        self.assertNotIn('processes', row)

    def test_missing_inventory_remains_missing_and_alerts_are_read_only(self):
        before_jobs = EndpointPerformanceSample.objects.count()
        response = self.client.get(reverse('api-hardware-inventory-overview'))
        row = response.json()['endpoints'][0]
        self.assertIsNone(row['inventory_at'])
        self.assertIsNone(row['memory']['total_bytes'])
        self.assertEqual(response.json()['counts']['inventory_missing'], 1)
        self.assertEqual(EndpointPerformanceSample.objects.count(), before_jobs)

        alert = EndpointAlert.objects.create(endpoint=self.machine, alert_type='hardware.test',
            severity=EndpointAlert.SEVERITY_CRITICAL, title='Synthetic alert', description='Synthetic')
        detail = self.client.get(reverse('api-hardware-inventory-detail', args=[self.machine.pk])).json()
        self.assertEqual(detail['alerts'][0]['title'], alert.title)
        self.assertEqual(detail['endpoint']['alerts_critical'], 1)

    def test_detail_does_not_expose_raw_payload_and_unknown_endpoint_is_404(self):
        self.add_snapshot()
        response = self.client.get(reverse('api-hardware-inventory-detail', args=[self.machine.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('hardware_details', response.json())
        self.assertEqual(self.client.get(reverse('api-hardware-inventory-detail', args=[uuid.uuid4()])).status_code, 404)

    def test_inventory_routes_keep_authentication_and_permission_gate(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('api-hardware-inventory-overview')).status_code, 302)
        self.client.force_login(self.user)
        self.user.user_permissions.clear()
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('api-hardware-inventory-overview')).status_code, 403)
