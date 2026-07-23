import uuid
import json
import tempfile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AgentJob, AgentJobResultReceipt, AgentMachine, AgentOperationalStatus, AgentRelease, AgentReleaseGroup
from .services import deterministic_rollout_bucket, evaluate_agent_update_policy
from .versioning import compare_versions, parse_semver


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

    def release(self, version='0.1.0.7', channel=AgentRelease.CHANNEL_STABLE, rollout=100, **kwargs):
        defaults = {
            'status': AgentRelease.STATUS_AVAILABLE,
            'package_url': f'https://nightowl.controlsul.com.br/downloads/nightowl-agent/{version}/NightOwl.Agent.Windows.zip',
            'checksum_url': 'https://nightowl.controlsul.com.br/downloads/nightowl-agent/checksums.json',
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
        self.machine.save(update_fields=['update_channel'])
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
            machine = AgentMachine(hostname=f'CS-ROLL-{index:03d}', machine_id=f'rollout-{index}', agent_version='0.1.0.6', agent_token_hash=f'hash-{index}')
            machine.save()
            bucket = deterministic_rollout_bucket(machine, release)
            if bucket < 10 or 10 <= bucket < 25:
                endpoints.append((machine, bucket))
            if any(bucket < 10 for _, bucket in endpoints) and any(10 <= bucket < 25 for _, bucket in endpoints):
                break

        release.rollout_percentage = 10
        release.save(update_fields=['rollout_percentage'])
        initially_selected = {machine.id for machine, _ in endpoints if evaluate_agent_update_policy(machine, manual=True).eligible}
        release.rollout_percentage = 25
        release.save(update_fields=['rollout_percentage'])
        expanded_selected = {machine.id for machine, _ in endpoints if evaluate_agent_update_policy(machine, manual=True).eligible}

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
        self.release(rollout=100)

        first = portal.post(reverse('api-endpoint-job-create', kwargs={'pk': str(self.machine.id)}), {'action': 'update_agent'})
        second = portal.post(reverse('api-endpoint-job-create', kwargs={'pk': str(self.machine.id)}), {'action': 'update_agent'})

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(AgentJob.objects.filter(endpoint=self.machine, job_type=AgentJob.TYPE_UPDATE_AGENT).count(), 1)

    def test_semver_prerelease_is_supported(self):
        self.assertIsNotNone(parse_semver('0.1.1.0-rc1'))
        self.assertEqual(compare_versions('0.1.1.0-rc1', '0.1.1.0'), -1)
        self.assertEqual(compare_versions('0.1.1.0-rc2', '0.1.1.0-rc1'), 1)
        self.assertEqual(compare_versions('0.1.1.0-rc1', '0.1.0.9'), 1)

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
