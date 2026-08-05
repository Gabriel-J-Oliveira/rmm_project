import uuid
import json
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AuditEvent, AgentJob, AgentJobResultReceipt, AgentMachine, AgentOperationalStatus, AgentRelease, AgentReleaseAudit, AgentReleaseGroup, AgentReleaseSigningKey
from .job_progress import job_progress_message, job_progress_percentage, job_stale_info, sanitize_job_value
from .services import build_update_agent_job_payload, deterministic_rollout_bucket, evaluate_agent_update_policy
from .services import change_agent_release_rollout, promote_agent_release, publish_agent_release, revoke_agent_release, supersede_agent_release
from .versioning import compare_versions, normalize_agent_version, parse_semver


class AgentOperationalDiagnosticsTests(TestCase):
    def setUp(self):
        self.token = 'rmm_live_test_token'
        self.machine = AgentMachine(
            machine_id='machine-001',
            hostname='CS-TEST-001',
            domain='CONTROL',
            agent_token_hash='',
        )
        self.machine.set_agent_token(self.token)
        self.machine.save()
        self.client = Client(HTTP_AUTHORIZATION=f'Bearer {self.token}')

    def test_legacy_heartbeat_still_accepted(self):
        response = self.client.post(
            '/api/agent/heartbeat/',
            data={
                'machine_id': 'machine-001',
                'hostname': 'CS-TEST-001',
                'agent_version': '0.1.0.8',
                'timestamp': timezone.now().isoformat(),
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.agent_version, '0.1.0.8')

    def test_heartbeat_accepts_prerelease_agent_versions(self):
        response = self.client.post(
            '/api/agent/heartbeat/',
            data={
                'machine_id': 'machine-001',
                'hostname': 'CS-TEST-001',
                'agent_version': '0.1.1.0-rc2',
                'tray_version': '0.1.1.0-rc2',
                'updater_version': '0.1.1.0-rc2',
                'timestamp': timezone.now().isoformat(),
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.agent_version, '0.1.1.0-rc2')

    def test_heartbeat_normalizes_long_product_versions(self):
        product_version = '0.1.1.0-rc2+4cede41a96bc45baa85d3a30a17d44b1.36c72a1e5ed17b7cbfbb4515a6f9b549cfe1b2f8'
        response = self.client.post(
            '/api/agent/heartbeat/',
            data={
                'machine_id': 'machine-001',
                'hostname': 'CS-TEST-001',
                'agent_version': product_version,
                'tray_version': product_version,
                'updater_version': product_version,
                'agent': {
                    'version': product_version,
                    'tray_version': product_version,
                    'updater_version': product_version,
                    'informational_version': product_version,
                    'build_id': '4cede41a96bc45baa85d3a30a17d44b1',
                    'git_commit': '36c72a1e5ed17b7cbfbb4515a6f9b549cfe1b2f8',
                },
                'timestamp': timezone.now().isoformat(),
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.agent_version, '0.1.1.0-rc2')

    def test_invalid_version_shape_does_not_update_agent_version(self):
        response = self.client.post(
            '/api/agent/heartbeat/',
            data={
                'machine_id': 'machine-001',
                'hostname': 'CS-TEST-001',
                'agent_version': 'not-a-version',
                'timestamp': timezone.now().isoformat(),
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.agent_version, '')

    def test_status_endpoint_records_diagnostics(self):
        response = self.client.post(
            '/api/agent/status/',
            data={
                'machine_id': 'machine-001',
                'agent': {
                    'installed_version': '0.1.0.8',
                    'available_version': '0.1.0.9',
                    'service_status': 'Running',
                    'running_job_count': 1,
                },
                'updater': {
                    'update_id': str(uuid.uuid4()),
                    'job_id': str(uuid.uuid4()),
                    'from_version': '0.1.0.7',
                    'target_version': '0.1.0.8',
                    'current_stage': 'completed',
                    'status': 'completed',
                    'health_check_confirmed': True,
                },
                'result_queue': {
                    'pending_count': 2,
                    'retrying_count': 1,
                    'quarantined_count': 0,
                    'queue_full': False,
                },
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        status = AgentOperationalStatus.objects.get(endpoint=self.machine)
        self.assertEqual(status.installed_version, '0.1.0.8')
        self.assertEqual(status.available_version, '0.1.0.9')
        self.assertEqual(status.result_pending_count, 2)
        self.assertEqual(status.health_indicator, AgentOperationalStatus.HEALTH_ATTENTION)

    def test_status_rejects_machine_id_mismatch(self):
        response = self.client.post(
            '/api/agent/status/',
            data={'machine_id': 'other-machine', 'agent': {'installed_version': '0.1.0.8'}},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(AgentOperationalStatus.objects.filter(endpoint=self.machine).exists())

    def test_job_result_idempotency(self):
        job = AgentJob.objects.create(endpoint=self.machine, job_type=AgentJob.TYPE_PING)
        result_id = str(uuid.uuid4())
        payload = {
            'job_id': str(job.id),
            'status': 'completed',
            'result': {'type': 'ping', 'success': True},
            'duration_seconds': 1,
        }

        first = self.client.post('/api/agent/jobs/result/', data=payload, content_type='application/json', HTTP_IDEMPOTENCY_KEY=result_id)
        second = self.client.post('/api/agent/jobs/result/', data=payload, content_type='application/json', HTTP_IDEMPOTENCY_KEY=result_id)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()['duplicate'])
        self.assertEqual(AgentJobResultReceipt.objects.filter(result_id=result_id).count(), 1)
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_COMPLETED)
        self.assertEqual(job.result_id, result_id)

    def test_job_result_idempotency_conflict(self):
        job = AgentJob.objects.create(endpoint=self.machine, job_type=AgentJob.TYPE_PING)
        result_id = str(uuid.uuid4())
        first_payload = {'job_id': str(job.id), 'status': 'completed', 'result': {'type': 'ping', 'success': True}}
        second_payload = {'job_id': str(job.id), 'status': 'failed', 'result': {'type': 'ping', 'success': False}}

        self.client.post('/api/agent/jobs/result/', data=first_payload, content_type='application/json', HTTP_IDEMPOTENCY_KEY=result_id)
        response = self.client.post('/api/agent/jobs/result/', data=second_payload, content_type='application/json', HTTP_IDEMPOTENCY_KEY=result_id)

        self.assertEqual(response.status_code, 409)
        receipt = AgentJobResultReceipt.objects.get(result_id=result_id)
        self.assertEqual(receipt.conflict_count, 1)

    def test_update_job_stage_maps_to_progress_and_message(self):
        job = AgentJob.objects.create(
            endpoint=self.machine,
            job_type=AgentJob.TYPE_UPDATE_AGENT,
            status=AgentJob.STATUS_RUNNING,
            payload={'target_version': '0.1.1.0-rc6'},
            result={'update_status': 'downloading', 'target_version': '0.1.1.0-rc6'},
        )

        self.assertEqual(job_progress_percentage(job), 35)
        self.assertIn('Baixando pacote', job_progress_message(job))
        self.assertIn('0.1.1.0-rc6', job_progress_message(job))

    def test_waiting_health_check_stale_after_five_minutes(self):
        now = timezone.now()
        job = AgentJob.objects.create(
            endpoint=self.machine,
            job_type=AgentJob.TYPE_UPDATE_AGENT,
            status=AgentJob.STATUS_RUNNING,
            started_at=now - timedelta(minutes=10),
            result_received_at=now - timedelta(minutes=6),
            result={'update_status': 'waiting_health_check'},
        )

        stale = job_stale_info(job, now=now)
        self.assertTrue(stale['is_stale'])
        self.assertEqual(stale['stale_reason'], 'waiting_health_check_too_long')

    def test_sanitized_job_details_redact_secrets(self):
        sanitized = sanitize_job_value({
            'agent_token': 'secret-token',
            'nested': {'Authorization': 'Bearer secret-value'},
            'message': 'download failed Authorization: Bearer secret-value',
        })

        self.assertEqual(sanitized['agent_token'], '[REDACTED]')
        self.assertEqual(sanitized['nested']['Authorization'], '[REDACTED]')
        self.assertIn('[REDACTED]', sanitized['message'])
        self.assertNotIn('secret-value', json.dumps(sanitized))

    def test_partial_result_keeps_running_and_final_result_completes_same_result_id(self):
        job = AgentJob.objects.create(endpoint=self.machine, job_type=AgentJob.TYPE_UPDATE_AGENT)
        result_id = str(uuid.uuid4())
        partial = {
            'job_id': str(job.id),
            'status': 'running',
            'result': {'update_status': 'runner_started', 'target_version': '0.1.1.0-rc6'},
        }
        final = {
            'job_id': str(job.id),
            'status': 'completed',
            'result': {'update_status': 'completed', 'target_version': '0.1.1.0-rc6'},
            'duration_seconds': 45,
        }

        first = self.client.post('/api/agent/jobs/result/', data=partial, content_type='application/json', HTTP_IDEMPOTENCY_KEY=result_id)
        second = self.client.post('/api/agent/jobs/result/', data=final, content_type='application/json', HTTP_IDEMPOTENCY_KEY=result_id)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_COMPLETED)
        self.assertEqual(job.duration_seconds, 45)
        self.assertEqual(AgentJobResultReceipt.objects.get(result_id=result_id).conflict_count, 0)

    def test_interrupted_update_can_be_resolved_by_final_completed_result(self):
        job = AgentJob.objects.create(
            endpoint=self.machine,
            job_type=AgentJob.TYPE_UPDATE_AGENT,
            status=AgentJob.STATUS_INTERRUPTED,
            result={'update_status': 'runner_started'},
            payload={'target_version': '0.1.1.0-rc6'},
        )
        response = self.client.post(
            '/api/agent/jobs/result/',
            data={
                'job_id': str(job.id),
                'status': 'completed',
                'result': {
                    'update_status': 'completed',
                    'target_version': '0.1.1.0-rc6',
                    'installed_version': '0.1.1.0-rc6',
                    'health_check_confirmed': True,
                },
            },
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_COMPLETED)

    def test_update_restart_interruption_waits_for_reconciliation_then_completes(self):
        job = AgentJob.objects.create(
            endpoint=self.machine,
            job_type=AgentJob.TYPE_UPDATE_AGENT,
            status=AgentJob.STATUS_RUNNING,
            payload={'target_version': '0.1.1.0-rc6'},
            result={'update_status': 'runner_started', 'target_version': '0.1.1.0-rc6'},
            timeout_seconds=900,
        )

        restart = self.client.post(
            '/api/agent/jobs/result/',
            data={
                'job_id': str(job.id),
                'status': 'failed',
                'error_code': 'JOB_INTERRUPTED',
                'error_message': 'Job was interrupted by agent restart.',
                'result': {'update_status': 'runner_started', 'target_version': '0.1.1.0-rc6'},
            },
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

        self.assertEqual(restart.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_RUNNING)
        self.assertEqual(job.result['update_status'], 'awaiting_reconciliation')
        self.assertEqual(job_progress_message(job), 'Agente reiniciando / aguardando confirmacao.')

        final = self.client.post(
            '/api/agent/jobs/result/',
            data={
                'job_id': str(job.id),
                'status': 'completed',
                'result': {
                    'update_status': 'completed',
                    'target_version': '0.1.1.0-rc6',
                    'installed_version': '0.1.1.0-rc6',
                    'health_check_confirmed': True,
                },
            },
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

        self.assertEqual(final.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_COMPLETED)
        self.assertTrue(AuditEvent.objects.filter(endpoint=self.machine, event_type='update.reconciled').exists())

    def test_update_restart_interruption_can_reconcile_to_rollback(self):
        job = AgentJob.objects.create(
            endpoint=self.machine,
            job_type=AgentJob.TYPE_UPDATE_AGENT,
            status=AgentJob.STATUS_RUNNING,
            payload={'target_version': '0.1.1.0-rc6'},
            result={'update_status': 'runner_started', 'target_version': '0.1.1.0-rc6'},
            timeout_seconds=900,
        )

        self.client.post(
            '/api/agent/jobs/result/',
            data={
                'job_id': str(job.id),
                'status': 'interrupted',
                'error_code': 'JOB_INTERRUPTED',
                'result': {'update_status': 'runner_started', 'target_version': '0.1.1.0-rc6'},
            },
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        rollback = self.client.post(
            '/api/agent/jobs/result/',
            data={
                'job_id': str(job.id),
                'status': 'rolled_back',
                'error_code': 'UPDATE_SERVICE_START_FAILED',
                'result': {
                    'update_status': 'rolled_back',
                    'target_version': '0.1.1.0-rc6',
                    'installed_version': '0.1.1.0-rc5',
                    'rollback_confirmed': True,
                },
            },
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

        self.assertEqual(rollback.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_ROLLED_BACK)
        self.assertTrue(AuditEvent.objects.filter(endpoint=self.machine, event_type='update.reconciled').exists())

    def test_common_job_interrupted_remains_interrupted(self):
        job = AgentJob.objects.create(endpoint=self.machine, job_type=AgentJob.TYPE_PING, status=AgentJob.STATUS_RUNNING)

        response = self.client.post(
            '/api/agent/jobs/result/',
            data={
                'job_id': str(job.id),
                'status': 'failed',
                'error_code': 'JOB_INTERRUPTED',
                'error_message': 'Job was interrupted by agent restart.',
                'result': {'type': 'ping'},
            },
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_FAILED)
        self.assertEqual(job.error_code, 'JOB_INTERRUPTED')

    def test_duplicate_final_update_result_is_idempotent_after_reconciliation(self):
        job = AgentJob.objects.create(
            endpoint=self.machine,
            job_type=AgentJob.TYPE_UPDATE_AGENT,
            status=AgentJob.STATUS_RUNNING,
            payload={'target_version': '0.1.1.0-rc6'},
            result={'update_status': 'awaiting_reconciliation'},
        )
        result_id = str(uuid.uuid4())
        payload = {
            'job_id': str(job.id),
            'status': 'completed',
            'result': {
                'update_status': 'completed',
                'target_version': '0.1.1.0-rc6',
                'installed_version': '0.1.1.0-rc6',
                'health_check_confirmed': True,
            },
        }

        first = self.client.post('/api/agent/jobs/result/', data=payload, content_type='application/json', HTTP_IDEMPOTENCY_KEY=result_id)
        second = self.client.post('/api/agent/jobs/result/', data=payload, content_type='application/json', HTTP_IDEMPOTENCY_KEY=result_id)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()['duplicate'])
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_COMPLETED)

    def test_failed_job_interrupted_update_can_be_corrected_to_completed(self):
        job = AgentJob.objects.create(
            endpoint=self.machine,
            job_type=AgentJob.TYPE_UPDATE_AGENT,
            status=AgentJob.STATUS_FAILED,
            error_code='JOB_INTERRUPTED',
            payload={'target_version': '0.1.1.0-rc6'},
            result={'update_status': 'runner_started'},
        )

        response = self.client.post(
            '/api/agent/jobs/result/',
            data={
                'job_id': str(job.id),
                'status': 'completed',
                'result': {
                    'update_status': 'completed',
                    'target_version': '0.1.1.0-rc6',
                    'installed_version': '0.1.1.0-rc6',
                    'health_check_confirmed': True,
                },
            },
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_COMPLETED)
        self.assertTrue(AuditEvent.objects.filter(endpoint=self.machine, event_type='update.reconciled').exists())

    def test_final_job_ignores_late_running_result(self):
        job = AgentJob.objects.create(endpoint=self.machine, job_type=AgentJob.TYPE_PING, status=AgentJob.STATUS_COMPLETED)

        response = self.client.post(
            '/api/agent/jobs/result/',
            data={'job_id': str(job.id), 'status': 'running', 'result': {'stage': 'late'}},
            content_type='application/json',
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ignored'])
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_COMPLETED)

    def test_mark_job_failed_requires_permission_and_updates_job(self):
        job = AgentJob.objects.create(
            endpoint=self.machine,
            job_type=AgentJob.TYPE_UPDATE_AGENT,
            status=AgentJob.STATUS_RUNNING,
            started_at=timezone.now() - timedelta(minutes=20),
        )
        user_model = get_user_model()
        basic_user = user_model.objects.create_user(username='job-basic', password='pass')
        forbidden_client = Client()
        forbidden_client.force_login(basic_user)
        forbidden = forbidden_client.post(reverse('api-endpoint-job-mark-failed', kwargs={'pk': str(self.machine.id), 'job_id': job.id}))
        self.assertIn(forbidden.status_code, (302, 403))
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_RUNNING)

        user = user_model.objects.create_user(username='job-admin', password='pass', is_staff=True)
        portal = Client()
        portal.force_login(user)
        response = portal.post(
            reverse('api-endpoint-job-mark-failed', kwargs={'pk': str(self.machine.id), 'job_id': job.id}),
            {'reason': 'Sem atualizacao ha muito tempo.'},
        )

        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_FAILED)
        self.assertEqual(job.error_code, 'JOB_MANUALLY_MARKED_FAILED')

    def test_rollback_failed_is_critical(self):
        response = self.client.post(
            '/api/agent/status/',
            data={
                'machine_id': 'machine-001',
                'agent': {'installed_version': '0.1.0.8', 'service_status': 'Running'},
                'updater': {
                    'current_stage': 'rollback_failed',
                    'status': 'rollback_failed',
                    'rollback_status': 'rollback_failed',
                    'rollback_error_code': 'ROLLBACK_FAILED',
                    'rollback_error_message': 'restore failed',
                },
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        status = AgentOperationalStatus.objects.get(endpoint=self.machine)
        self.assertEqual(status.health_indicator, AgentOperationalStatus.HEALTH_CRITICAL)

    def test_endpoint_detail_diagnostic_rbac(self):
        AgentOperationalStatus.objects.create(
            endpoint=self.machine,
            installed_version='0.1.0.8',
            available_version='0.1.0.8',
            last_error_code='RESULT_SEND_FAILED',
            last_error_message='backend unavailable',
        )
        user_model = get_user_model()
        user = user_model.objects.create_user(username='tech', password='pass', is_staff=True)
        portal = Client()
        portal.force_login(user)
        response = portal.get(reverse('api-endpoint-detail', kwargs={'pk': str(self.machine.id)}))

        self.assertEqual(response.status_code, 200)
        diagnostic = response.json()['agent_diagnostic']
        self.assertTrue(diagnostic['visible'])
        self.assertEqual(diagnostic['last_error']['code'], 'RESULT_SEND_FAILED')

# Create your tests here.


class AgentReleasePolicyTests(TestCase):
    def setUp(self):
        self.token = 'rmm_live_release_test_token'
        self.machine = AgentMachine(
            machine_id='release-machine-001',
            hostname='CS-REL-001',
            domain='CONTROL',
            agent_version='0.1.0.6',
            agent_token_hash='',
        )
        self.machine.set_agent_token(self.token)
        self.machine.save()
        self.client = Client(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        AgentReleaseSigningKey.objects.create(
            key_id='nightowl-release-2026-01',
            public_key_xml='<RSAKeyValue><Modulus>test</Modulus><Exponent>AQAB</Exponent></RSAKeyValue>',
            status=AgentReleaseSigningKey.STATUS_ACTIVE,
        )

    def release(self, version='0.1.0.7', channel=AgentRelease.CHANNEL_STABLE, rollout=100, **kwargs):
        defaults = {
            'status': AgentRelease.STATUS_AVAILABLE,
            'package_url': f'https://nightowl.controlsul.com.br/downloads/nightowl-agent/{version}/NightOwl.Agent.Windows.zip',
            'checksum_url': 'https://nightowl.controlsul.com.br/downloads/nightowl-agent/checksums.json',
            'manifest_url': f'https://nightowl.controlsul.com.br/downloads/nightowl-agent/{version}/release-manifest.json',
            'manifest_sha256': 'b' * 64,
            'signature_url': f'https://nightowl.controlsul.com.br/downloads/nightowl-agent/{version}/release-manifest.sig',
            'signature_sha256': 'c' * 64,
            'signature_key_id': 'nightowl-release-2026-01',
            'signature_valid': True,
            'legacy_unsigned': False,
            'sha256': 'a' * 64,
            'size': 1234,
            'rollout_percentage': rollout,
            'released_at': timezone.now(),
        }
        defaults.update(kwargs)
        return AgentRelease.objects.create(version=version, channel=channel, **defaults)

    def test_stable_endpoint_never_receives_development_release(self):
        self.release(version='0.1.0.9', channel=AgentRelease.CHANNEL_DEVELOPMENT)

        decision = evaluate_agent_update_policy(self.machine, manual=True)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason_code, 'channel_no_release')
        self.assertEqual(decision.channel, AgentMachine.UPDATE_CHANNEL_STABLE)

    def test_pilot_endpoint_receives_pilot_release(self):
        self.machine.update_channel = AgentMachine.UPDATE_CHANNEL_PILOT
        self.machine.is_pilot_endpoint = True
        self.machine.save(update_fields=['update_channel', 'is_pilot_endpoint'])
        release = self.release(version='0.1.0.8', channel=AgentRelease.CHANNEL_PILOT)

        decision = evaluate_agent_update_policy(self.machine, manual=True)

        self.assertTrue(decision.eligible)
        self.assertEqual(decision.release, release)

    def test_development_endpoint_receives_development_release(self):
        self.machine.update_channel = AgentMachine.UPDATE_CHANNEL_DEVELOPMENT
        self.machine.save(update_fields=['update_channel'])
        release = self.release(version='0.1.0.8', channel=AgentRelease.CHANNEL_DEVELOPMENT)

        decision = evaluate_agent_update_policy(self.machine, manual=True)

        self.assertTrue(decision.eligible)
        self.assertEqual(decision.release, release)

    def test_rollout_bucket_is_deterministic(self):
        release = self.release(rollout=10)

        first = deterministic_rollout_bucket(self.machine, release)
        second = deterministic_rollout_bucket(self.machine, release)

        self.assertEqual(first, second)
        self.assertGreaterEqual(first, 0)
        self.assertLess(first, 100)

    def test_increasing_rollout_adds_endpoints_without_removing_previous(self):
        release = self.release(rollout=10)
        endpoints = []
        for index in range(250):
            machine = AgentMachine(
                hostname=f'CS-ROLL-{index:03d}',
                machine_id=f'rollout-{index}',
                agent_version='0.1.0.6',
                agent_token_hash=f'hash-{index}',
                update_policy=AgentMachine.UPDATE_POLICY_AUTOMATIC,
                auto_update_enabled=True,
            )
            machine.save()
            bucket = deterministic_rollout_bucket(machine, release)
            if bucket < 10 or 10 <= bucket < 25:
                endpoints.append((machine, bucket))
            if any(bucket < 10 for _, bucket in endpoints) and any(10 <= bucket < 25 for _, bucket in endpoints):
                break

        release.rollout_percentage = 10
        release.save(update_fields=['rollout_percentage'])
        initially_selected = {machine.id for machine, _ in endpoints if evaluate_agent_update_policy(machine, manual=False).eligible}
        release.rollout_percentage = 25
        release.save(update_fields=['rollout_percentage'])
        expanded_selected = {machine.id for machine, _ in endpoints if evaluate_agent_update_policy(machine, manual=False).eligible}

        self.assertTrue(initially_selected)
        self.assertTrue(initially_selected.issubset(expanded_selected))
        self.assertGreater(len(expanded_selected), len(initially_selected))

    def test_rollout_pause_blocks_release(self):
        self.release(rollout=100, rollout_paused=True)

        decision = evaluate_agent_update_policy(self.machine, manual=True)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason_code, 'release_paused')

    def test_endpoint_pause_blocks_release(self):
        self.machine.update_paused = True
        self.machine.save(update_fields=['update_paused'])
        self.release(rollout=100)

        decision = evaluate_agent_update_policy(self.machine, manual=True)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason_code, 'endpoint_paused')

    def test_pinned_version_selects_matching_release(self):
        self.release(version='0.1.0.8', rollout=100)
        pinned = self.release(version='0.1.0.7', rollout=100)
        self.machine.pinned_agent_version = '0.1.0.7'
        self.machine.save(update_fields=['pinned_agent_version'])

        decision = evaluate_agent_update_policy(self.machine, manual=True)

        self.assertTrue(decision.eligible)
        self.assertEqual(decision.release, pinned)

    def test_revoked_release_not_delivered(self):
        self.release(rollout=100, revoked=True)

        decision = evaluate_agent_update_policy(self.machine, manual=True)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason_code, 'channel_no_release')

    def test_minimum_updater_blocks_incompatible_endpoint(self):
        self.release(rollout=100, minimum_updater_version='9.0.0.0')

        decision = evaluate_agent_update_policy(self.machine, manual=True)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason_code, 'minimum_updater_incompatible')

    def test_group_restriction_blocks_endpoint_outside_group(self):
        group = AgentReleaseGroup.objects.get(slug='pilot')
        release = self.release(rollout=100)
        release.allowed_groups.add(group)

        decision = evaluate_agent_update_policy(self.machine, manual=True)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason_code, 'group_not_allowed')

    def test_endpoint_without_channel_uses_stable(self):
        AgentMachine.objects.filter(pk=self.machine.pk).update(update_channel='')
        self.machine.refresh_from_db()
        release = self.release(rollout=100)

        decision = evaluate_agent_update_policy(self.machine, manual=True)

        self.assertTrue(decision.eligible)
        self.assertEqual(decision.release, release)
        self.assertEqual(decision.channel, AgentMachine.UPDATE_CHANNEL_STABLE)

    def test_automatic_policy_creates_update_job_once(self):
        self.machine.update_policy = AgentMachine.UPDATE_POLICY_AUTOMATIC
        self.machine.auto_update_enabled = True
        self.machine.save(update_fields=['update_policy', 'auto_update_enabled'])
        release = self.release(rollout=100)

        first = self.client.get('/api/agent/update-policy/')
        second = self.client.get('/api/agent/update-policy/')

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(AgentJob.objects.filter(endpoint=self.machine, job_type=AgentJob.TYPE_UPDATE_AGENT).count(), 1)
        job = AgentJob.objects.get(endpoint=self.machine, job_type=AgentJob.TYPE_UPDATE_AGENT)
        self.assertEqual(job.agent_release, release)
        self.assertEqual(job.payload['target_version'], release.version)

    def test_manual_panel_update_avoids_duplicate_jobs(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username='tech-release', password='pass', is_staff=True)
        portal = Client()
        portal.force_login(user)
        release = self.release(rollout=100)

        first = portal.post(reverse('api-endpoint-job-create', kwargs={'pk': str(self.machine.id)}), {'action': 'update_agent', 'release_id': str(release.id)})
        second = portal.post(reverse('api-endpoint-job-create', kwargs={'pk': str(self.machine.id)}), {'action': 'update_agent', 'release_id': str(release.id)})

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(AgentJob.objects.filter(endpoint=self.machine, job_type=AgentJob.TYPE_UPDATE_AGENT).count(), 1)

    def test_manual_update_allows_development_release_with_rollout_zero(self):
        self.machine.update_channel = AgentMachine.UPDATE_CHANNEL_DEVELOPMENT
        self.machine.update_policy = AgentMachine.UPDATE_POLICY_MANUAL
        self.machine.auto_update_enabled = False
        self.machine.update_paused = False
        self.machine.agent_version = '0.1.0.7'
        self.machine.save(update_fields=['update_channel', 'update_policy', 'auto_update_enabled', 'update_paused', 'agent_version'])
        release = self.release(version='0.1.1.0-rc1', channel=AgentRelease.CHANNEL_DEVELOPMENT, rollout=0, minimum_updater_version='0.1.0.7')

        decision = evaluate_agent_update_policy(self.machine, manual=True)

        self.assertTrue(decision.eligible)
        self.assertEqual(decision.release, release)

    def test_automatic_rollout_zero_blocks_release(self):
        self.machine.update_channel = AgentMachine.UPDATE_CHANNEL_DEVELOPMENT
        self.machine.update_policy = AgentMachine.UPDATE_POLICY_AUTOMATIC
        self.machine.auto_update_enabled = True
        self.machine.agent_version = '0.1.0.7'
        self.machine.save(update_fields=['update_channel', 'update_policy', 'auto_update_enabled', 'agent_version'])
        self.release(version='0.1.1.0-rc1', channel=AgentRelease.CHANNEL_DEVELOPMENT, rollout=0, minimum_updater_version='0.1.0.7')

        response = self.client.get('/api/agent/update-policy/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['update_available'])
        self.assertEqual(response.json()['reason_code'], 'rollout_not_selected')
        self.assertEqual(AgentJob.objects.filter(endpoint=self.machine, job_type=AgentJob.TYPE_UPDATE_AGENT).count(), 0)

    def test_manual_update_allows_explicit_paused_release(self):
        release = self.release(rollout=0, rollout_paused=True, status=AgentRelease.STATUS_PAUSED)

        decision = evaluate_agent_update_policy(self.machine, manual=True, explicit_release=release)

        self.assertTrue(decision.eligible)
        self.assertEqual(decision.reason_code, 'eligible')
        self.assertEqual(decision.release, release)

    def test_manual_update_blocks_explicit_revoked_release(self):
        release = self.release(rollout=0, revoked=True)

        decision = evaluate_agent_update_policy(self.machine, manual=True, explicit_release=release)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason_code, 'release_revoked')

    def test_manual_panel_update_blocks_paused_endpoint(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username='tech-paused', password='pass', is_staff=True)
        portal = Client()
        portal.force_login(user)
        self.machine.update_paused = True
        self.machine.save(update_fields=['update_paused'])
        release = self.release(rollout=0)

        response = portal.post(reverse('api-endpoint-job-create', kwargs={'pk': str(self.machine.id)}), {'action': 'update_agent', 'release_id': str(release.id)})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason_code'], 'endpoint_paused')
        self.assertEqual(AgentJob.objects.filter(endpoint=self.machine, job_type=AgentJob.TYPE_UPDATE_AGENT).count(), 0)

    def test_manual_update_without_explicit_release_keeps_endpoint_channel(self):
        self.release(version='0.1.1.0-rc1', channel=AgentRelease.CHANNEL_DEVELOPMENT, rollout=0)

        decision = evaluate_agent_update_policy(self.machine, manual=True)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason_code, 'channel_no_release')

    def test_manual_panel_update_without_release_id_does_not_use_paused_release(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username='tech-no-release-id', password='pass', is_staff=True)
        portal = Client()
        portal.force_login(user)
        self.machine.update_channel = AgentMachine.UPDATE_CHANNEL_DEVELOPMENT
        self.machine.agent_version = '0.1.1.0-rc2'
        self.machine.save(update_fields=['update_channel', 'agent_version'])
        self.release(
            version='0.1.1.0-rc3',
            channel=AgentRelease.CHANNEL_DEVELOPMENT,
            rollout=0,
            rollout_paused=True,
            status=AgentRelease.STATUS_PAUSED,
            minimum_updater_version='0.1.0.7',
        )

        response = portal.post(reverse('api-endpoint-job-create', kwargs={'pk': str(self.machine.id)}), {'action': 'update_agent'})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason_code'], 'channel_no_release')
        self.assertEqual(AgentJob.objects.filter(endpoint=self.machine, job_type=AgentJob.TYPE_UPDATE_AGENT).count(), 0)

    def test_manual_panel_update_uses_explicit_release_and_persists_metadata(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username='tech-explicit', password='pass', is_staff=True)
        portal = Client()
        portal.force_login(user)
        self.machine.update_channel = AgentMachine.UPDATE_CHANNEL_DEVELOPMENT
        self.machine.agent_version = '0.1.1.0-rc6'
        self.machine.save(update_fields=['update_channel', 'agent_version'])
        release = self.release(
            version='0.1.1.0-rc7',
            channel=AgentRelease.CHANNEL_DEVELOPMENT,
            rollout=0,
            rollout_paused=True,
            status=AgentRelease.STATUS_PAUSED,
            minimum_updater_version='0.1.0.7',
            manifest_url='https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/0.1.1.0-rc7/release-manifest.json',
            manifest_sha256='b' * 64,
            signature_url='https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/0.1.1.0-rc7/release-manifest.sig',
            signature_sha256='c' * 64,
            signature_key_id='nightowl-release-2026-01',
            signature_valid=True,
            legacy_unsigned=False,
        )

        response = portal.post(reverse('api-endpoint-job-create', kwargs={'pk': str(self.machine.id)}), {'action': 'update_agent', 'release_id': str(release.id)})

        self.assertEqual(response.status_code, 201)
        job = AgentJob.objects.get(endpoint=self.machine, job_type=AgentJob.TYPE_UPDATE_AGENT)
        self.assertEqual(job.agent_release, release)
        self.assertEqual(job.payload['target_version'], '0.1.1.0-rc7')
        self.assertEqual(job.payload['release_id'], str(release.id))
        self.assertEqual(job.payload['package_url'], release.package_url)
        self.assertEqual(job.payload['checksum_url'], release.checksum_url)
        self.assertEqual(job.payload['sha256'], release.sha256)
        self.assertEqual(job.payload['size'], release.size)
        self.assertEqual(job.payload['minimum_updater_version'], release.minimum_updater_version)
        self.assertEqual(job.payload['channel'], release.channel)
        self.assertEqual(job.payload['source'], 'manual_panel')
        self.assertEqual(job.payload['policy_reason'], 'eligible')
        self.assertEqual(job.payload['manifest_url'], release.manifest_url)
        self.assertEqual(job.payload['manifest_sha256'], release.manifest_sha256)
        self.assertEqual(job.payload['signature_url'], release.signature_url)
        self.assertEqual(job.payload['signature_sha256'], release.signature_sha256)
        self.assertEqual(job.payload['signature_key_id'], release.signature_key_id)
        self.assertTrue(job.payload['signature_valid'])
        self.assertFalse(job.payload['legacy_unsigned'])
        self.assertEqual(job.timeout_seconds, 900)
        self.assertIsNotNone(job.expires_at)

    def test_manual_panel_update_rc5_to_rc6_uses_legacy_bootstrap_payload(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username='tech-rc5-bootstrap', password='pass', is_staff=True)
        portal = Client()
        portal.force_login(user)
        self.machine.update_channel = AgentMachine.UPDATE_CHANNEL_DEVELOPMENT
        self.machine.agent_version = '0.1.1.0-rc5'
        self.machine.save(update_fields=['update_channel', 'agent_version'])
        release = self.release(
            version='0.1.1.0-rc6',
            channel=AgentRelease.CHANNEL_DEVELOPMENT,
            rollout=0,
            rollout_paused=True,
            status=AgentRelease.STATUS_PAUSED,
            minimum_updater_version='0.1.0.7',
            manifest_url='https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/0.1.1.0-rc6/release-manifest.json',
            manifest_sha256='b' * 64,
            signature_url='https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/0.1.1.0-rc6/release-manifest.sig',
            signature_sha256='c' * 64,
            signature_key_id='nightowl-release-2026-01',
            signature_valid=True,
            legacy_unsigned=False,
        )

        response = portal.post(reverse('api-endpoint-job-create', kwargs={'pk': str(self.machine.id)}), {'action': 'update_agent', 'release_id': str(release.id)})

        self.assertEqual(response.status_code, 201)
        job = AgentJob.objects.get(endpoint=self.machine, job_type=AgentJob.TYPE_UPDATE_AGENT)
        self.assertEqual(set(job.payload.keys()), {
            'release_id',
            'target_version',
            'channel',
            'package_url',
            'checksum_url',
            'sha256',
            'size',
            'minimum_updater_version',
            'force',
            'mandatory',
            'timeout_seconds',
            'source',
        })
        self.assertEqual(job.payload['target_version'], '0.1.1.0-rc6')
        self.assertNotIn('manifest_url', job.payload)
        self.assertNotIn('signature_url', job.payload)
        self.assertNotIn('signature_key_id', job.payload)
        self.assertNotIn('source_channel', job.payload)
        self.assertTrue(AuditEvent.objects.filter(endpoint=self.machine, event_type='agent.update_legacy_bootstrap_payload').exists())

    def test_update_payload_rc6_to_rc7_contains_signature_fields(self):
        self.machine.update_channel = AgentMachine.UPDATE_CHANNEL_DEVELOPMENT
        self.machine.agent_version = '0.1.1.0-rc6'
        self.machine.save(update_fields=['update_channel', 'agent_version'])
        release = self.release(
            version='0.1.1.0-rc7',
            channel=AgentRelease.CHANNEL_DEVELOPMENT,
            rollout=0,
            rollout_paused=True,
            status=AgentRelease.STATUS_PAUSED,
            minimum_updater_version='0.1.0.7',
            manifest_url='https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/0.1.1.0-rc7/release-manifest.json',
            manifest_sha256='d' * 64,
            signature_url='https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/0.1.1.0-rc7/release-manifest.sig',
            signature_sha256='e' * 64,
            signature_key_id='nightowl-release-2026-01',
            signature_valid=True,
            legacy_unsigned=False,
        )
        decision = evaluate_agent_update_policy(self.machine, manual=True, explicit_release=release)

        payload = build_update_agent_job_payload(self.machine, decision, source='manual_panel', manual_explicit=True)

        self.assertEqual(payload['target_version'], '0.1.1.0-rc7')
        self.assertEqual(payload['manifest_url'], release.manifest_url)
        self.assertEqual(payload['manifest_sha256'], release.manifest_sha256)
        self.assertEqual(payload['signature_url'], release.signature_url)
        self.assertEqual(payload['signature_sha256'], release.signature_sha256)
        self.assertEqual(payload['signature_key_id'], release.signature_key_id)
        self.assertTrue(payload['signature_valid'])
        self.assertFalse(payload['legacy_unsigned'])

    def test_legacy_bootstrap_payload_is_not_used_for_pilot_channel(self):
        self.machine.update_channel = AgentMachine.UPDATE_CHANNEL_PILOT
        self.machine.is_pilot_endpoint = True
        self.machine.agent_version = '0.1.1.0-rc5'
        self.machine.save(update_fields=['update_channel', 'is_pilot_endpoint', 'agent_version'])
        release = self.release(
            version='0.1.1.0-rc6',
            channel=AgentRelease.CHANNEL_PILOT,
            rollout=0,
            rollout_paused=True,
            status=AgentRelease.STATUS_PAUSED,
            minimum_updater_version='0.1.0.7',
            manifest_url='https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/0.1.1.0-rc6/release-manifest.json',
            manifest_sha256='d' * 64,
            signature_url='https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/0.1.1.0-rc6/release-manifest.sig',
            signature_sha256='e' * 64,
            signature_key_id='nightowl-release-2026-01',
            signature_valid=True,
            legacy_unsigned=False,
        )
        decision = evaluate_agent_update_policy(self.machine, manual=True, explicit_release=release)

        payload = build_update_agent_job_payload(self.machine, decision, source='manual_panel', manual_explicit=True)

        self.assertIn('manifest_url', payload)
        self.assertIn('signature_key_id', payload)

    def test_manual_panel_update_rejects_same_version(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username='tech-same-version', password='pass', is_staff=True)
        portal = Client()
        portal.force_login(user)
        self.machine.agent_version = '0.1.1.0-rc5'
        self.machine.save(update_fields=['agent_version'])
        release = self.release(version='0.1.1.0-rc5', minimum_updater_version='0.1.0.7')

        response = portal.post(
            reverse('api-endpoint-job-create', kwargs={'pk': str(self.machine.id)}),
            {'action': 'update_agent', 'release_id': str(release.id)},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason_code'], 'already_current')
        self.assertEqual(AgentJob.objects.filter(endpoint=self.machine, job_type=AgentJob.TYPE_UPDATE_AGENT).count(), 0)

    def test_manual_panel_update_rejects_downgrade_without_force(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username='tech-downgrade-block', password='pass', is_staff=True)
        portal = Client()
        portal.force_login(user)
        self.machine.agent_version = '0.1.1.0-rc5'
        self.machine.save(update_fields=['agent_version'])
        release = self.release(version='0.1.1.0-rc4', minimum_updater_version='0.1.0.7')

        response = portal.post(
            reverse('api-endpoint-job-create', kwargs={'pk': str(self.machine.id)}),
            {'action': 'update_agent', 'release_id': str(release.id), 'force': 'false'},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason_code'], 'downgrade_requires_force')
        self.assertEqual(AgentJob.objects.filter(endpoint=self.machine, job_type=AgentJob.TYPE_UPDATE_AGENT).count(), 0)

    def test_manual_panel_update_allows_downgrade_with_explicit_force(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username='tech-downgrade-force', password='pass', is_staff=True)
        portal = Client()
        portal.force_login(user)
        self.machine.agent_version = '0.1.1.0-rc5'
        self.machine.save(update_fields=['agent_version'])
        release = self.release(version='0.1.1.0-rc4', minimum_updater_version='0.1.0.7')

        response = portal.post(
            reverse('api-endpoint-job-create', kwargs={'pk': str(self.machine.id)}),
            {'action': 'update_agent', 'release_id': str(release.id), 'force': 'true'},
        )

        self.assertEqual(response.status_code, 201)
        job = AgentJob.objects.get(endpoint=self.machine, job_type=AgentJob.TYPE_UPDATE_AGENT)
        self.assertEqual(job.agent_release, release)
        self.assertTrue(job.payload['force'])

    def test_manual_panel_update_rejects_invalid_release_id(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username='tech-invalid-release', password='pass', is_staff=True)
        portal = Client()
        portal.force_login(user)

        response = portal.post(
            reverse('api-endpoint-job-create', kwargs={'pk': str(self.machine.id)}),
            {'action': 'update_agent', 'release_id': str(uuid.uuid4())},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason_code'], 'release_not_found')

    def test_manual_panel_update_rejects_incompatible_release_channel(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username='tech-channel-release', password='pass', is_staff=True)
        portal = Client()
        portal.force_login(user)
        self.machine.update_channel = AgentMachine.UPDATE_CHANNEL_STABLE
        self.machine.agent_version = '0.1.0.7'
        self.machine.save(update_fields=['update_channel', 'agent_version'])
        release = self.release(
            version='0.1.1.0-rc1',
            channel=AgentRelease.CHANNEL_DEVELOPMENT,
            rollout=0,
            status=AgentRelease.STATUS_PAUSED,
            minimum_updater_version='0.1.0.7',
        )

        response = portal.post(
            reverse('api-endpoint-job-create', kwargs={'pk': str(self.machine.id)}),
            {'action': 'update_agent', 'release_id': str(release.id)},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason_code'], 'channel_no_release')

    def test_manual_panel_update_rejects_revoked_explicit_release(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username='tech-revoked-release', password='pass', is_staff=True)
        portal = Client()
        portal.force_login(user)
        release = self.release(rollout=0, revoked=True)

        response = portal.post(
            reverse('api-endpoint-job-create', kwargs={'pk': str(self.machine.id)}),
            {'action': 'update_agent', 'release_id': str(release.id)},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason_code'], 'release_revoked')

    def test_endpoint_detail_exposes_manual_paused_release_option(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username='tech-release-options', password='pass', is_staff=True)
        portal = Client()
        portal.force_login(user)
        self.machine.update_channel = AgentMachine.UPDATE_CHANNEL_DEVELOPMENT
        self.machine.agent_version = '0.1.1.0-rc2'
        self.machine.save(update_fields=['update_channel', 'agent_version'])
        release = self.release(
            version='0.1.1.0-rc3',
            channel=AgentRelease.CHANNEL_DEVELOPMENT,
            rollout=0,
            rollout_paused=True,
            status=AgentRelease.STATUS_PAUSED,
            minimum_updater_version='0.1.0.7',
        )

        response = portal.get(reverse('api-endpoint-detail', kwargs={'pk': str(self.machine.id)}))

        self.assertEqual(response.status_code, 200)
        options = response.json()['agent_update_releases']
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]['id'], str(release.id))
        self.assertEqual(options[0]['version'], '0.1.1.0-rc3')
        self.assertTrue(options[0]['eligible'])
        self.assertEqual(options[0]['status'], AgentRelease.STATUS_PAUSED)
        self.assertTrue(options[0]['rollout_paused'])
        self.assertIn('released_at', options[0])
        self.assertIn('release_notes', options[0])
        self.assertTrue(options[0]['metadata_complete'])

    def test_endpoint_detail_marks_downgrade_release_as_requires_force(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username='tech-release-downgrade-option', password='pass', is_staff=True)
        portal = Client()
        portal.force_login(user)
        self.machine.agent_version = '0.1.1.0-rc5'
        self.machine.save(update_fields=['agent_version'])
        release = self.release(version='0.1.1.0-rc4', minimum_updater_version='0.1.0.7')

        response = portal.get(reverse('api-endpoint-detail', kwargs={'pk': str(self.machine.id)}))

        self.assertEqual(response.status_code, 200)
        options = response.json()['agent_update_releases']
        selected = next(item for item in options if item['id'] == str(release.id))
        self.assertFalse(selected['eligible'])
        self.assertTrue(selected['requires_force'])
        self.assertEqual(selected['reason_code'], 'downgrade_requires_force')

    def test_endpoint_detail_reports_active_update_job(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username='tech-active-update', password='pass', is_staff=True)
        portal = Client()
        portal.force_login(user)
        job = AgentJob.objects.create(
            endpoint=self.machine,
            job_type=AgentJob.TYPE_UPDATE_AGENT,
            status=AgentJob.STATUS_RUNNING,
            payload={'target_version': '0.1.1.0-rc5'},
        )

        response = portal.get(reverse('api-endpoint-detail', kwargs={'pk': str(self.machine.id)}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['active_update_job']['id'], str(job.id))

    def test_semver_prerelease_is_supported(self):
        self.assertIsNotNone(parse_semver('0.1.1.0-rc1'))
        self.assertEqual(compare_versions('0.1.1.0-rc1', '0.1.1.0'), -1)
        self.assertEqual(compare_versions('0.1.1.0-rc2', '0.1.1.0-rc1'), 1)
        self.assertEqual(compare_versions('0.1.1.0-rc1', '0.1.0.9'), 1)
        self.assertEqual(
            normalize_agent_version('0.1.1.0-rc2+4cede41a96bc45baa85d3a30a17d44b1.36c72a1e5ed17b7cbfbb4515a6f9b549cfe1b2f8'),
            '0.1.1.0-rc2',
        )
        self.assertEqual(compare_versions('0.1.1.0-rc2+build.metadata', '0.1.1.0-rc1'), 1)

    def test_import_agent_release_creates_paused_development_release(self):
        manifest = {
            'version': '0.1.1.0-rc1',
            'packageUrl': 'https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/0.1.1.0-rc1/NightOwl.Agent.Windows.zip',
            'checksumUrl': 'https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/0.1.1.0-rc1/checksums.json',
            'sha256': 'b' * 64,
            'size': 123456,
            'minimum_updater_version': '0.1.0.7',
        }
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as handle:
            json.dump(manifest, handle)
            path = handle.name

        call_command('import_agent_release', '--agent-version', '0.1.1.0-rc1', '--channel', 'development', '--version-json', path)

        release = AgentRelease.objects.get(version='0.1.1.0-rc1')
        self.assertEqual(release.channel, AgentRelease.CHANNEL_DEVELOPMENT)
        self.assertEqual(release.status, AgentRelease.STATUS_PAUSED)
        self.assertTrue(release.rollout_paused)
        self.assertEqual(release.rollout_percentage, 0)
        self.assertEqual(AgentJob.objects.filter(job_type=AgentJob.TYPE_UPDATE_AGENT).count(), 0)


class AgentReleaseGovernanceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(username='release-admin', password='pass', is_staff=True, is_superuser=True)
        self.machine = AgentMachine.objects.create(
            machine_id='gov-machine-001',
            hostname='CS-GOV-001',
            agent_token_hash='hash',
            agent_version='0.1.1.0-rc5',
            update_channel=AgentMachine.UPDATE_CHANNEL_DEVELOPMENT,
        )
        AgentReleaseSigningKey.objects.create(
            key_id='nightowl-release-2026-01',
            public_key_xml='<RSAKeyValue><Modulus>test</Modulus><Exponent>AQAB</Exponent></RSAKeyValue>',
            status=AgentReleaseSigningKey.STATUS_ACTIVE,
        )

    def release(self, version='0.1.1.0-rc6', channel=AgentRelease.CHANNEL_DEVELOPMENT, status=AgentRelease.STATUS_PUBLISHED, **kwargs):
        defaults = {
            'package_url': f'https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/{version}/NightOwl.Agent.Windows.zip',
            'checksum_url': f'https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/{version}/checksums.json',
            'manifest_url': f'https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/{version}/release-manifest.json',
            'signature_url': f'https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/{version}/release-manifest.sig',
            'sha256': 'c' * 64,
            'manifest_sha256': 'd' * 64,
            'signature_sha256': 'e' * 64,
            'signature_key_id': 'nightowl-release-2026-01',
            'signature_valid': True,
            'legacy_unsigned': False,
            'size': 1234,
            'minimum_updater_version': '0.1.0.7',
            'rollout_percentage': 0,
            'rollout_paused': True,
            'released_at': timezone.now(),
            'created_by': self.admin,
        }
        defaults.update(kwargs)
        return AgentRelease.objects.create(version=version, channel=channel, status=status, **defaults)

    def test_published_release_is_immutable_for_artifact_fields(self):
        release = self.release()
        release.sha256 = 'f' * 64
        with self.assertRaises(ValidationError):
            release.save()

    def test_same_hash_import_is_idempotent(self):
        release = self.release()
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as handle:
            json.dump({
                'version': release.version,
                'packageUrl': release.package_url,
                'checksumUrl': release.checksum_url,
                'manifestUrl': release.manifest_url,
                'signatureUrl': release.signature_url,
                'sha256': release.sha256,
                'manifest_sha256': release.manifest_sha256,
                'signature_sha256': release.signature_sha256,
                'key_id': release.signature_key_id,
                'size': release.size,
                'minimum_updater_version': release.minimum_updater_version,
            }, handle)
            path = handle.name

        call_command('import_agent_release', '--agent-version', release.version, '--channel', release.channel, '--version-json', path)
        self.assertEqual(AgentRelease.objects.filter(version=release.version).count(), 1)

    def test_verify_agent_release_uses_agent_version_argument(self):
        release = self.release(version='0.1.1.0-rc6')
        call_command('verify_agent_release', '--agent-version', release.version, '--skip-remote')

    def test_different_hash_import_is_blocked(self):
        release = self.release()
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as handle:
            json.dump({
                'version': release.version,
                'packageUrl': release.package_url,
                'checksumUrl': release.checksum_url,
                'sha256': 'a' * 64,
                'size': release.size,
            }, handle)
            path = handle.name

        with self.assertRaises(Exception) as ctx:
            call_command('import_agent_release', '--agent-version', release.version, '--channel', release.channel, '--version-json', path)
        self.assertIn('RELEASE_IMMUTABILITY_VIOLATION', str(ctx.exception))

    def test_promote_development_to_pilot_and_pilot_to_stable(self):
        release = self.release()
        promote_agent_release(release, AgentRelease.CHANNEL_PILOT, self.admin, rollout_percentage=20, rollout_paused=True, approval_reason='Piloto inicial')
        release.refresh_from_db()
        self.assertEqual(release.channel, AgentRelease.CHANNEL_PILOT)
        self.assertEqual(release.rollout_percentage, 20)
        promote_agent_release(
            release,
            AgentRelease.CHANNEL_STABLE,
            self.admin,
            rollout_percentage=5,
            rollout_paused=True,
            approval_reason='Aprovado para stable',
            allow_prerelease_stable=True,
        )
        release.refresh_from_db()
        self.assertEqual(release.channel, AgentRelease.CHANNEL_STABLE)
        self.assertEqual(release.stable_approval_reason, 'Aprovado para stable')

    def test_prerelease_stable_requires_explicit_confirmation(self):
        release = self.release()
        promote_agent_release(release, AgentRelease.CHANNEL_PILOT, self.admin, rollout_percentage=20, rollout_paused=True, approval_reason='Piloto inicial')

        with self.assertRaises(ValidationError):
            promote_agent_release(release, AgentRelease.CHANNEL_STABLE, self.admin, rollout_percentage=5, rollout_paused=True, approval_reason='Aprovado para stable')

    def test_legacy_unsigned_cannot_be_promoted(self):
        release = self.release(version='0.1.1.0-legacy', signature_valid=False, legacy_unsigned=True, signature_key_id='', signature_url='', signature_sha256='')
        with self.assertRaises(ValidationError):
            promote_agent_release(release, AgentRelease.CHANNEL_PILOT, self.admin, rollout_percentage=10, rollout_paused=True, approval_reason='legacy pilot')

    def test_publish_requires_signature_policy_for_signed_release(self):
        release = self.release(status=AgentRelease.STATUS_DRAFT, signature_valid=False)
        with self.assertRaises(ValidationError):
            publish_agent_release(release, self.admin, 'publicacao sem assinatura')

    def test_publish_signed_draft_is_audited_and_idempotent(self):
        release = self.release(status=AgentRelease.STATUS_DRAFT, released_at=None)

        publish_agent_release(release, self.admin, 'publicacao rc6', rollout_percentage=0, rollout_paused=True)
        release.refresh_from_db()

        self.assertEqual(release.status, AgentRelease.STATUS_PAUSED)
        self.assertTrue(AgentReleaseAudit.objects.filter(release=release, action=AgentReleaseAudit.ACTION_PUBLISHED).exists())
        self.assertTrue(AuditEvent.objects.filter(event_type='release.published', metadata__release_id=str(release.id)).exists())

        publish_agent_release(release, self.admin, 'publicacao rc6 repetida', rollout_percentage=0, rollout_paused=True)
        self.assertEqual(AgentReleaseAudit.objects.filter(release=release, action=AgentReleaseAudit.ACTION_PUBLISHED).count(), 1)

    def test_revoked_signing_key_blocks_policy(self):
        AgentReleaseSigningKey.objects.filter(key_id='nightowl-release-2026-01').update(
            status=AgentReleaseSigningKey.STATUS_REVOKED,
            revoked_at=timezone.now(),
            revocation_reason='rotacao comprometida',
        )
        release = self.release()

        decision = evaluate_agent_update_policy(self.machine, manual=True, explicit_release=release)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason_code, 'key_revoked')

    def test_unknown_signing_key_blocks_policy(self):
        release = self.release(signature_key_id='nightowl-unknown-key')

        decision = evaluate_agent_update_policy(self.machine, manual=True, explicit_release=release)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason_code, 'key_unknown')

    def test_supersede_requires_reason_and_replacement(self):
        old = self.release(version='0.1.1.0-rc6')
        new = self.release(version='0.1.1.0-rc7')

        with self.assertRaises(ValidationError):
            supersede_agent_release(old, new, self.admin, '')

    def test_revoke_blocks_manual_update(self):
        release = self.release()
        revoke_agent_release(release, self.admin, 'Falha critica')
        release.refresh_from_db()
        decision = evaluate_agent_update_policy(self.machine, manual=True, explicit_release=release)
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason_code, 'release_revoked')

    def test_revoke_cancels_not_started_jobs_for_release(self):
        release = self.release()
        job = AgentJob.objects.create(
            endpoint=self.machine,
            agent_release=release,
            job_type=AgentJob.TYPE_UPDATE_AGENT,
            status=AgentJob.STATUS_QUEUED,
            payload={'target_version': release.version},
        )

        revoke_agent_release(release, self.admin, 'Falha critica')
        job.refresh_from_db()

        self.assertEqual(job.status, AgentJob.STATUS_CANCELLED)
        self.assertEqual(job.error_code, 'RELEASE_REVOKED')

    def test_superseded_not_selected_automatically(self):
        old = self.release(version='0.1.1.0-rc6')
        new = self.release(version='0.1.1.0-rc7')
        supersede_agent_release(old, new, self.admin, 'RC7 substitui RC6')
        decision = evaluate_agent_update_policy(self.machine, manual=False)
        self.assertEqual(decision.release, new)

    def test_pilot_release_requires_pilot_endpoint(self):
        release = self.release(channel=AgentRelease.CHANNEL_PILOT)
        self.machine.update_channel = AgentMachine.UPDATE_CHANNEL_PILOT
        self.machine.is_pilot_endpoint = False
        self.machine.save(update_fields=['update_channel', 'is_pilot_endpoint'])
        blocked = evaluate_agent_update_policy(self.machine, manual=False, explicit_release=release)
        self.assertFalse(blocked.eligible)
        self.machine.is_pilot_endpoint = True
        self.machine.save(update_fields=['is_pilot_endpoint'])
        allowed = evaluate_agent_update_policy(self.machine, manual=True, explicit_release=release)
        self.assertNotEqual(allowed.reason_code, 'group_not_allowed')

    def test_rollout_bucket_is_deterministic(self):
        release = self.release()
        first = deterministic_rollout_bucket(self.machine, release)
        second = deterministic_rollout_bucket(self.machine, release)
        self.assertEqual(first, second)
        self.assertGreaterEqual(first, 0)
        self.assertLess(first, 100)

    def test_rollout_change_is_audited(self):
        release = self.release()

        change_agent_release_rollout(release, self.admin, 25, paused=False, reason='piloto ampliado')

        release.refresh_from_db()
        self.assertEqual(release.rollout_percentage, 25)
        self.assertEqual(release.status, AgentRelease.STATUS_PUBLISHED)
        self.assertTrue(AgentReleaseAudit.objects.filter(release=release, action=AgentReleaseAudit.ACTION_RESUMED).exists())
        self.assertTrue(AuditEvent.objects.filter(event_type='release.resumed', metadata__release_id=str(release.id)).exists())
