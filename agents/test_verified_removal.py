from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    AgentEnrollmentToken, AgentJob, AgentMachine, AgentRelease, AgentRemovalAudit, AgentRolloutCampaign, AgentRolloutTarget,
    AssetAgentLink, AssetRecord, AutomationDefinition, AutomationOccurrence, InventorySnapshot, PhysicalAsset,
)


class VerifiedRemovalTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.endpoint, token = AgentMachine.create_with_token(
            hostname='REMOVE-PILOT', machine_id=str(uuid4()), agent_version='0.1.1.0-rc49',
            status=AgentMachine.STATUS_OFFLINE,
        )
        self.other, _ = AgentMachine.create_with_token(hostname='OTHER', machine_id=str(uuid4()))
        self.agent = Client(HTTP_AUTHORIZATION=f'Bearer {token}', HTTP_X_NIGHTOWL_JOB_PROTOCOL='2')
        self.operator = get_user_model().objects.create_superuser('removal-operator', password='synthetic-password')
        self.client.force_login(self.operator)
        self.request_url = reverse('api-endpoint-uninstall', args=[self.endpoint.pk])
        self.confirmation = {'mode': 'remove', 'machine_id': self.endpoint.machine_id,
                             'hostname_confirmation': self.endpoint.hostname,
                             'username': self.operator.username, 'password': 'synthetic-password'}

    def heartbeat(self):
        return InventorySnapshot.objects.create(
            machine=self.endpoint, hostname=self.endpoint.hostname, collected_at=self.now, logged_user='DOMAIN\\observed',
            raw_payload={'snapshot_source': 'heartbeat', 'machine_id': self.endpoint.machine_id,
                         'agent_version': self.endpoint.agent_version,
                         'agent': {'capabilities': ['verified_agent_removal:1']}},
        )

    def enqueue_and_pull(self):
        self.heartbeat()
        response = self.client.post(self.request_url, self.confirmation)
        self.assertEqual(response.status_code, 201, response.content)
        job = self.endpoint.jobs.get()
        self.assertEqual(job.status, 'queued')
        self.assertNotIn('removal_receipt_token', job.payload)
        self.assertNotContains(response, 'removal_receipt_token', status_code=201)
        outdated = Client(HTTP_AUTHORIZATION=self.agent.defaults['HTTP_AUTHORIZATION']).get('/api/agent/jobs/pull/')
        self.assertEqual(outdated.json()['jobs'], [])
        pull = self.agent.get('/api/agent/jobs/pull/')
        self.assertEqual(pull.status_code, 200)
        wire = pull.json()['jobs'][0]
        self.assertEqual(wire['id'], str(job.pk))
        return job, wire['payload']['removal_receipt_token']

    def receipt(self, job, token, *, complete=True, verified=True, **fields):
        return Client().post('/api/agent/jobs/result/', {
            'job_id': str(job.pk), 'status': 'completed' if complete else 'failed',
            'exit_code': 0 if complete else 1, 'result': {
                'mode': 'purge', 'machine_id': self.endpoint.machine_id, 'cleanup_version': 1,
                'cleanup_verified': verified, 'binary_removed': verified, 'persistent_data_preserved': False, **fields,
            },
        }, content_type='application/json', HTTP_IDEMPOTENCY_KEY=f'uninstall-{job.pk}',
           HTTP_X_NIGHTOWL_REMOVAL_RECEIPT=token)

    def test_requires_supported_agent_credentials_and_current_identity(self):
        self.assertEqual(self.client.post(self.request_url, self.confirmation).json()['error'],
                         'verified_removal_unsupported')
        self.heartbeat()
        self.assertEqual(self.client.post(self.request_url, {**self.confirmation, 'machine_id': 'stale'}).status_code, 409)
        self.assertEqual(self.client.post(self.request_url, {**self.confirmation, 'password': 'invalid'}).status_code, 403)
        self.client.force_login(get_user_model().objects.create_user('other-operator', is_staff=True))
        self.assertEqual(self.client.post(self.request_url, self.confirmation).status_code, 403)
        self.assertFalse(AgentJob.objects.exists())

    def test_partial_attempt_and_invalid_receipt_cannot_delete_retry_can_finish(self):
        job, token = self.enqueue_and_pull()
        self.assertEqual(self.receipt(job, '0' * 64).status_code, 403)
        self.assertEqual(self.receipt(job, token, verified=False).status_code, 409)
        self.assertEqual(self.receipt(job, token, machine_id=self.other.machine_id).status_code, 409)
        self.assertEqual(self.receipt(job, token, complete=False, verified=False).json()['retryable'], True)
        self.assertTrue(AgentMachine.objects.filter(pk=self.endpoint.pk).exists())
        self.assertEqual(AgentRemovalAudit.objects.get(job_id=job.pk).outcome, 'cleanup_unverified')
        self.assertTrue(self.receipt(job, token).json()['removed'])
        self.assertTrue(self.receipt(job, token).json()['duplicate'])
        self.assertEqual(self.receipt(job, token, verified=False).status_code, 409)
        self.assertFalse(AgentMachine.objects.filter(pk=self.endpoint.pk).exists())
        self.assertIn(self.agent.get('/api/agent/jobs/pull/').status_code, (401, 403))

    def test_old_receipt_cannot_delete_an_endpoint_after_agent_credentials_rotate(self):
        job, token = self.enqueue_and_pull()
        self.endpoint.set_agent_token('new-synthetic-token')
        self.endpoint.save(update_fields=['agent_token_hash'])
        self.assertEqual(self.receipt(job, token).json()['error'], 'removal_identity_changed')
        self.assertTrue(AgentMachine.objects.filter(pk=self.endpoint.pk).exists())

    @override_settings(NIGHTOWL_AUTOMATIC_ASSETS_ENABLED=True)
    def test_reinstall_enrolls_new_endpoint_and_recovers_the_same_asset(self):
        from .services import record_collection

        hardware_id = uuid4()
        hardware = {'manufacturer': 'Vendor', 'model': 'Model', 'serial_number': 'PHYS-001',
                    'oem_product': {'smbios_uuid': str(hardware_id), 'hypervisor_present': False}}
        record_collection(self.endpoint, 'full_inventory', {
            'hostname': self.endpoint.hostname, 'machine_id': self.endpoint.machine_id, 'hardware': hardware,
        })
        original = PhysicalAsset.objects.get()
        job, token = self.enqueue_and_pull()
        self.assertTrue(self.receipt(job, token).json()['removed'])
        _, enrollment = AgentEnrollmentToken.create_with_token(name='reinstall', max_uses=1)
        machine_id = str(uuid4())
        response = Client().post(reverse('agent-enroll'), {
            'hostname': self.endpoint.hostname, 'machine_id': machine_id,
            'serial_number': 'PHYS-001', 'enrollment_token': enrollment,
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        replacement = AgentMachine.objects.get(pk=response.json()['endpoint_id'])
        self.assertNotEqual(replacement.pk, self.endpoint.pk)
        record_collection(replacement, 'full_inventory', {
            'hostname': replacement.hostname, 'machine_id': machine_id, 'hardware': hardware,
        })
        self.assertEqual(AgentMachine.objects.filter(hostname=replacement.hostname).count(), 1)
        self.assertEqual(PhysicalAsset.objects.get().pk, original.pk)
        self.assertEqual(original.agent_links.get(ended_at__isnull=True).endpoint, replacement)

    def test_deletes_only_selected_operational_rows_and_preserves_asset_and_audit(self):
        asset = PhysicalAsset.objects.create(serial_number='PHYS-001')
        link = AssetAgentLink.objects.create(asset=asset, endpoint=self.endpoint, endpoint_uuid_snapshot=self.endpoint.pk,
                                             machine_id_snapshot=self.endpoint.machine_id)
        AssetRecord.objects.create(asset=asset, record_type='observation', description='Original', occurred_at=self.now)
        definition = AutomationDefinition.objects.create(name='Shared', job_type='ping', starts_at=self.now,
                                                         timezone_name='UTC')
        release = AgentRelease.objects.create(version='0.1.1.0-rc90', channel='development',
                                              package_url='https://example.invalid/rc90.zip', sha256='a' * 64)
        job, token = self.enqueue_and_pull()
        campaign = AgentRolloutCampaign.objects.create(
            release=release, channel_snapshot='development', state='draft', cohort_schema=1, cohort_hash='',
            preview_generated_at=self.now, freshness_seconds=60, total_candidates=2, eligible_count=0, excluded_count=2,
            concurrency_limit=1, minimum_observation_seconds=0, created_by=self.operator,
        )
        for endpoint in (self.endpoint, self.other):
            AutomationOccurrence.objects.create(definition=definition, endpoint=endpoint, revision=1, status='skipped',
                                                scheduled_for=self.now, expires_at=self.now + timedelta(minutes=5))
            AgentRolloutTarget.objects.create(campaign=campaign, endpoint=endpoint, update_channel_snapshot='development',
                update_policy_snapshot='manual', rollout_bucket=1, eligibility_at_selection=False,
                state='excluded', selected_at=self.now)
        campaign.state = 'ready'
        campaign.save(update_fields=['state'])
        campaign.state = 'running'
        campaign.save(update_fields=['state'])
        self.assertEqual(self.receipt(job, token).json()['error'], 'removal_dependencies_changed')
        campaign.state = 'completed'
        campaign.save(update_fields=['state'])
        self.assertTrue(self.receipt(job, token).json()['removed'])
        link.refresh_from_db()
        self.assertIsNone(link.endpoint_id)
        self.assertIsNotNone(link.ended_at)
        self.assertEqual(asset.records.count(), 1)
        self.assertEqual(definition.occurrences.get().endpoint, self.other)
        self.assertEqual(campaign.targets.get().endpoint, self.other)
        with self.assertRaises(IntegrityError), transaction.atomic():
            campaign.targets.filter(endpoint=self.other).delete()
        self.assertFalse(InventorySnapshot.objects.filter(machine_id=self.endpoint.pk).exists())
        audit = AgentRemovalAudit.objects.get(job_id=job.pk)
        self.assertEqual(audit.observed_user, 'DOMAIN\\observed')
        self.assertEqual(audit.observed_user_at, self.now)
        self.assertEqual(audit.asset_id, asset.pk)
        self.assertEqual(audit.authorized_by, self.operator.username)
        self.assertEqual(self.client.get(reverse('api-endpoint-removal-status', args=[self.endpoint.pk])).json()['outcome'],
                         'removed')
