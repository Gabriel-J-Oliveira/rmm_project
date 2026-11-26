import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from agents.models import AgentJob, AgentMachine, AlertEvent, EndpointAlert
from tickets.models import Ticket, TicketAuditEvent


class NocOperationsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('operator')
        self.client.force_login(self.user)
        self.endpoint = AgentMachine.objects.create(
            hostname='noc-pilot', machine_id=str(uuid.uuid4()), agent_token_hash='f' * 64,
            status=AgentMachine.STATUS_ONLINE, last_seen_at=timezone.now(),
        )
        self.alert = self.create_alert('Disco cheio')

    def create_alert(self, title, **fields):
        data = dict(alert_type='disk_low', severity='critical', title=title, description='Capacidade de disco')
        return self.endpoint.alerts.create(**(data | fields))

    def action(self, action, payload=None):
        return self.client.post(reverse('noc-alert-action', args=(self.alert.pk, action)), payload or {})

    def test_latest_fifteen_preserve_historical_incidents_in_card(self):
        now = timezone.now()
        self.alert.first_seen_at = now - timedelta(days=10)
        self.alert.save(update_fields=['first_seen_at'])
        for index in range(16):
            self.create_alert(f'Resolvido {index:02}', status=EndpointAlert.STATUS_RESOLVED,
                              resolved_at=now, first_seen_at=now - timedelta(minutes=20-index))
        response = self.client.get(reverse('noc'), {'fragment': '1'})
        self.assertEqual(response.status_code, 200)
        html = response.json()['html']
        recent, thematic = html.split('aria-label="Alertas por tema"')
        self.assertEqual(recent.count('class="noc-alert-row noc-card-actionable noc-occurrence"'), 15)
        self.assertIn('Resolvido 15', recent)
        self.assertNotIn('Resolvido 00', recent)
        self.assertIn('data-noc-end=', recent)
        self.assertIn('Disco cheio', thematic)

    def test_acknowledgement_and_mute_keep_occurrence_open(self):
        self.assertEqual(self.action('acknowledge').status_code, 200)
        self.assertEqual(self.action('mute', {'duration': '1h'}).status_code, 200)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, EndpointAlert.STATUS_ACKNOWLEDGED)
        self.assertTrue(self.alert.is_muted)
        self.assertIsNone(self.alert.resolved_at)
        self.assertEqual(self.alert.events.count(), 2)
        self.assertEqual(self.action('mute', {'duration': 'forever'}).status_code, 400)

    def test_creates_and_reuses_ticket_for_occurrence(self):
        self.assertEqual(self.action('ticket').status_code, 201)
        self.assertEqual(self.action('ticket').status_code, 200)
        ticket = Ticket.objects.get(related_alert_id=str(self.alert.pk))
        self.assertEqual(ticket.endpoint, self.endpoint)
        self.assertEqual(ticket.source, Ticket.SOURCE_RMM_ALERT)
        self.assertEqual(TicketAuditEvent.objects.filter(ticket=ticket).count(), 1)

    def test_verification_uses_fixed_job_and_refuses_offline(self):
        response = self.action('verify', {'job_type': 'uninstall_agent'})
        self.assertEqual(response.status_code, 201)
        job = self.endpoint.jobs.get()
        self.assertEqual(job.job_type, AgentJob.TYPE_COLLECT_DISKS)
        self.assertEqual(job.status, AgentJob.STATUS_QUEUED)
        self.assertEqual(self.action('verify').status_code, 200)
        self.assertEqual(self.endpoint.jobs.count(), 1)
        self.endpoint.status = AgentMachine.STATUS_OFFLINE
        self.endpoint.save(update_fields=['status'])
        self.assertEqual(self.action('verify').status_code, 409)

    def test_read_only_operator_cannot_mutate_and_csrf_is_required(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(reverse('noc-alert-action', args=(self.alert.pk, 'ticket'))).status_code, 403)
        viewer = get_user_model().objects.create_user('viewer', is_staff=True)
        viewer.user_permissions.add(*Permission.objects.filter(codename__in=('view_agentmachine', 'view_endpointalert')))
        self.client.force_login(viewer)
        for action in ('ticket', 'acknowledge', 'mute', 'verify'):
            self.assertEqual(self.action(action).status_code, 403)
        self.assertFalse(Ticket.objects.exists())
        self.assertFalse(self.endpoint.jobs.exists())
        self.assertFalse(AlertEvent.objects.exists())
from os import environ
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(environ.get('NIGHTOWL_TEST_BROWSER_E2E') == '1', 'Explicit isolated browser gate required')
class NocVisualTests(StaticLiveServerTestCase):
    def test_real_noc_panel_and_artifacts(self):
        operator = get_user_model().objects.create_superuser('noc-browser-review')
        self.client.force_login(operator)
        now = timezone.now()
        fixtures = (
            ('LAB-CAPACIDADE', 'disk_low', 'critical', 'Disco C: em 97%', AgentMachine.STATUS_ONLINE),
            ('LAB-OPERACAO', 'endpoint_offline', 'warning', 'Sem comunicação há 28 horas', AgentMachine.STATUS_OFFLINE),
            ('LAB-SEGURANCA', 'security_antivirus', 'security', 'Proteção requer investigação', AgentMachine.STATUS_ONLINE),
            ('LAB-DESEMPENHO', 'cpu_pressure', 'warning', 'CPU sustentada em 91%', AgentMachine.STATUS_ONLINE),
        )
        for index, (hostname, kind, severity, title, state) in enumerate(fixtures):
            endpoint = AgentMachine.objects.create(
                hostname=hostname, machine_id=str(uuid.uuid4()), agent_token_hash=str(index) * 64,
                status=state, last_seen_at=now - timedelta(hours=28) if state == 'offline' else now,
            )
            endpoint.alerts.create(
                alert_type=kind, severity=severity, title=title, description='Ocorrência sintética de homologação.',
                first_seen_at=now - timedelta(minutes=10 + 28 * index),
                metadata={'free_percent': 3, 'used_percent': 97, 'disk_name': 'C:'} if kind == 'disk_low' else {},
            )
        result = subprocess.run(
            ['node', str(Path(settings.BASE_DIR) / 'tests/frontend/test_noc_operations_visual.cjs')],
            env={**environ, 'NIGHTOWL_E2E_SERVER': self.live_server_url,
                 'NIGHTOWL_E2E_SESSION': self.client.cookies['sessionid'].value},
            capture_output=True, encoding='utf-8', timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(Ticket.objects.count(), 1)
        self.assertEqual(AgentJob.objects.filter(job_type=AgentJob.TYPE_COLLECT_DISKS).count(), 1)
        self.assertEqual(AlertEvent.objects.filter(event_type=AlertEvent.TYPE_ACKNOWLEDGED).count(), 1)
