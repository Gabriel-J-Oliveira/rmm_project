import io
import json
import uuid
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .lifecycle_reconciliation import evaluate_legacy_lifecycle_reconciliation
from .models import AgentJob, AgentMachine, AuditEvent, InventorySnapshot
from .services import record_heartbeat


class LegacyLifecycleReconciliationTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.machine = AgentMachine.objects.create(
            hostname='LEGACY-LAB', machine_id=str(uuid.uuid4()), agent_token_hash='synthetic-lifecycle-hash',
            status='online', last_seen_at=self.now, agent_version='0.1.1.0-rc17',
            agent_mode='dotnet-service', agent_install_path='C:\\ProgramData\\NightOwl\\AgentDotNet',
        )
        self.snapshot = InventorySnapshot.objects.create(
            machine=self.machine, hostname=self.machine.hostname, collected_at=self.now,
            received_at=self.now, raw_payload={
                'heartbeat_at': self.now.isoformat(), 'hostname': self.machine.hostname,
                'machine_id': self.machine.machine_id, 'agent_version': self.machine.agent_version,
            },
        )

    def decision(self):
        self.machine.refresh_from_db()
        return evaluate_legacy_lifecycle_reconciliation(self.machine, now=self.now)

    def command(self, *args):
        output = io.StringIO()
        call_command('reconcile_agent_lifecycle', '--endpoint', str(self.machine.pk), *args, stdout=output)
        return json.loads(output.getvalue())

    def test_fresh_legacy_heartbeat_is_reconcilable_without_automatic_write(self):
        result = self.decision()
        self.assertTrue(result['eligible_for_reconciliation'])
        self.assertEqual(result['proposed_lifecycle'], 'installed')
        self.assertEqual(self.machine.agent_lifecycle_status, '')

    def test_stale_offline_invalid_identity_ambiguous_identity_and_version_block(self):
        self.machine.last_seen_at = self.now - timedelta(minutes=16)
        self.machine.save(update_fields=['last_seen_at'])
        self.assertIn('last_seen_fresh', self.decision()['blockers'])
        self.machine.last_seen_at = self.now
        self.machine.is_active = False
        self.machine.save(update_fields=['last_seen_at', 'is_active'])
        self.assertIn('active', self.decision()['blockers'])
        self.machine.is_active = True
        self.machine.status = 'offline'
        self.machine.save(update_fields=['is_active', 'status'])
        self.assertIn('online', self.decision()['blockers'])
        self.machine.status = 'online'
        self.machine.machine_id = 'not-a-uuid'
        self.machine.save(update_fields=['status', 'machine_id'])
        self.assertIn('identity_valid', self.decision()['blockers'])
        self.machine.machine_id = self.snapshot.raw_payload['machine_id']
        self.machine.save(update_fields=['machine_id'])
        AgentMachine.objects.create(hostname='DUPLICATE', machine_id=self.machine.machine_id,
                                    agent_token_hash='synthetic-duplicate')
        self.assertIn('identity_unique', self.decision()['blockers'])
        AgentMachine.objects.filter(hostname='DUPLICATE').delete()
        self.machine.agent_version = ''
        self.machine.save(update_fields=['agent_version'])
        self.assertIn('agent_version_valid', self.decision()['blockers'])
        self.machine.agent_version = 'invalid-version'
        self.machine.save(update_fields=['agent_version'])
        self.assertIn('agent_version_valid', self.decision()['blockers'])

    def test_stale_heartbeat_snapshot_blocks_even_with_fresh_endpoint(self):
        self.snapshot.received_at = self.now - timedelta(minutes=16)
        self.snapshot.save(update_fields=['received_at'])
        self.assertIn('authenticated_heartbeat_fresh', self.decision()['blockers'])

    def test_recent_collection_cannot_refresh_copied_old_heartbeat(self):
        self.snapshot.raw_payload['heartbeat_at'] = (self.now - timedelta(minutes=16)).isoformat()
        self.snapshot.save(update_fields=['raw_payload'])
        self.assertIn('authenticated_heartbeat_fresh', self.decision()['blockers'])

    def test_heartbeat_evidence_and_active_lifecycle_job_are_required(self):
        self.snapshot.raw_payload['machine_id'] = str(uuid.uuid4())
        self.snapshot.save(update_fields=['raw_payload'])
        self.assertIn('heartbeat_identity_matches', self.decision()['blockers'])
        self.snapshot.raw_payload['machine_id'] = self.machine.machine_id
        self.snapshot.save(update_fields=['raw_payload'])
        AgentJob.objects.create(endpoint=self.machine, job_type='update_agent', status='sent')
        self.assertIn('active_lifecycle_jobs', self.decision()['blockers'])

    def test_dry_run_does_not_write_and_apply_updates_one_endpoint_with_audit(self):
        before = list(AgentMachine.objects.values())
        result = self.command()
        self.assertEqual(result['mode'], 'dry_run')
        self.assertTrue(result['eligible_for_reconciliation'])
        self.assertEqual(before, list(AgentMachine.objects.values()))
        self.assertFalse(AuditEvent.objects.exists())
        result = self.command('--apply', '--reason', 'Synthetic legacy recovery')
        self.assertTrue(result['applied'])
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.agent_lifecycle_status, 'installed')
        self.assertEqual(self.machine.last_installed_agent_version, '')
        audit = AuditEvent.objects.get(event_type='agent.lifecycle_legacy_reconciled')
        self.assertEqual(audit.endpoint_id, self.machine.pk)
        again = self.command('--apply', '--reason', 'Synthetic legacy recovery')
        self.assertEqual(again['blockers'], ['already_installed'])
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_terminal_lifecycle_survives_heartbeat_and_apply(self):
        for lifecycle in ('uninstalled', 'purged'):
            with self.subTest(lifecycle=lifecycle):
                self.machine.agent_lifecycle_status = lifecycle
                self.machine.save(update_fields=['agent_lifecycle_status'])
                record_heartbeat(self.machine, {
                    'hostname': self.machine.hostname, 'machine_id': self.machine.machine_id,
                    'agent_version': self.machine.agent_version,
                    'agent_mode': self.machine.agent_mode,
                    'agent': {'install_path': self.machine.agent_install_path},
                }, {'hostname': self.machine.hostname, 'machine_id': self.machine.machine_id,
                    'agent_version': self.machine.agent_version, 'heartbeat_at': self.now.isoformat()})
                self.machine.refresh_from_db()
                self.assertEqual(self.machine.agent_lifecycle_status, lifecycle)
                result = self.command('--apply', '--reason', 'Synthetic terminal check')
                self.assertIn('terminal_lifecycle', result['blockers'])
                self.machine.refresh_from_db()
                self.assertEqual(self.machine.agent_lifecycle_status, lifecycle)
        self.assertFalse(AuditEvent.objects.filter(event_type='agent.lifecycle_legacy_reconciled').exists())
