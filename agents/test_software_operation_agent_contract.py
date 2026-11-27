import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AgentJob, AgentMachine, SoftwarePackageOperation, SoftwarePackageRecord
from .internal_jobs import validate_internal_payload
from .software_execution_profiles import execution_profile_digest
from .software_operation_contract import create_software_operation_job


class SoftwareOperationAgentProfileTests(TestCase):
    def setUp(self):
        self.machine, token = AgentMachine.create_with_token(hostname='SYNTHETIC-SOFTWARE', machine_id=str(uuid.uuid4()))
        self.agent = self.client_class(HTTP_AUTHORIZATION='Bearer ' + token)
        self.other, other_token = AgentMachine.create_with_token(hostname='SYNTHETIC-OTHER', machine_id=str(uuid.uuid4()))
        self.other_agent = self.client_class(HTTP_AUTHORIZATION='Bearer ' + other_token)
        self.product = str(uuid.uuid4()).upper()
        self.package = SoftwarePackageRecord.objects.create(
            friendly_name='Synthetic MSI', package_type=SoftwarePackageRecord.TYPE_MSI,
            approval_status=SoftwarePackageRecord.STATUS_APPROVED, artifact_key='synthetic/key',
            sha256='a' * 64, size_bytes=42,
        )
        self.profile = {
            'schema_version': 1,
            'install': {'method': 'msi', 'arguments': [], 'product_code': self.product},
            'update': {'method': 'msi_upgrade', 'arguments': []},
            'uninstall': {'method': 'msi_product_code', 'product_code': self.product},
            'detection': {'method': 'msi_product_code', 'product_code': self.product},
            'expected_version': '2.3.4', 'reboot_behavior': 'report_only',
        }
        self.job_payload = {
            'schema_version': 2, 'operation_id': str(uuid.uuid4()), 'operation': 'install',
            'request_id': str(uuid.uuid4()), 'package_uuid': str(self.package.pk),
            'artifact_sha256': 'a' * 64, 'artifact_revision': 1, 'profile_revision': 1,
            'profile_sha256': 'b' * 64,
        }
        self.job = AgentJob.objects.create(endpoint=self.machine, job_type=AgentJob.TYPE_INSTALL_SOFTWARE,
                                           payload=self.job_payload, status=AgentJob.STATUS_SENT)
        self.operation = SoftwarePackageOperation.objects.create(
            id=self.job_payload['operation_id'], request_id=self.job_payload['request_id'], job=self.job, endpoint=self.machine,
            endpoint_uuid_snapshot=self.machine.pk, machine_id_snapshot=self.machine.machine_id,
            package=self.package, operation='install', artifact_sha256='a' * 64, artifact_revision=1,
            profile_revision=1, profile_sha256='b' * 64, authorization='agents.execute_softwarepackageoperation',
            snapshot={'package': {'profile': self.profile, 'size_bytes': 42}},
        )

    def test_profile_is_authenticated_exact_job_scoped_and_redacted(self):
        url = reverse('agent-software-operation-profile', args=[self.job.pk])
        with patch('agents.software_operation_contract.validate_software_operation_dispatch', return_value=self.operation) as revalidate:
            response = self.agent.get(url)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['operation_id'], str(self.operation.pk))
        self.assertEqual(body['machine_id'], self.machine.machine_id)
        self.assertEqual(body['size_bytes'], 42)
        self.assertEqual(body['product_code'], self.product)
        self.assertEqual(body['expected_version'], '2.3.4')
        self.assertEqual(body['profile'], self.profile)
        self.assertNotIn('artifact_key', body)
        self.assertNotIn('operator', body)
        self.assertNotIn('url', body)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        revalidate.assert_called_once_with(self.job, endpoint=self.machine)

    def test_profile_rejects_other_endpoint_queued_job_and_query_parameters(self):
        url = reverse('agent-software-operation-profile', args=[self.job.pk])
        with patch('agents.software_operation_contract.validate_software_operation_dispatch', return_value=self.operation):
            self.assertEqual(self.other_agent.get(url).status_code, 404)
            self.assertEqual(self.agent.get(url + '?token=synthetic').status_code, 400)
            self.job.status = AgentJob.STATUS_QUEUED
            self.job.save(update_fields=['status'])
            self.assertEqual(self.agent.get(url).status_code, 404)

    def test_v2_internal_payload_is_strict_and_legacy_v1_stays_unchanged(self):
        self.assertEqual(validate_internal_payload('install_software', self.job_payload), self.job_payload)
        with self.assertRaises(ValueError):
            validate_internal_payload('install_software', {**self.job_payload, 'package_id': 'extra'})
        with self.assertRaises(ValueError):
            validate_internal_payload('install_software', {'package_id': 'not-approved'})


class SoftwareOperationSerializedAgentAckTests(TestCase):
    """Exercise the JSON shape emitted by JobExecutionResult/LimitResult through the ACK API."""

    def setUp(self):
        self.operator = get_user_model().objects.create_superuser('software-ack-admin')
        self.machine, token = AgentMachine.create_with_token(
            hostname='SYNTHETIC-SOFTWARE-ACK', machine_id=str(uuid.uuid4()),
        )
        self.machine.status = AgentMachine.STATUS_ONLINE
        self.machine.last_seen_at = timezone.now()
        self.machine.save(update_fields=['status', 'last_seen_at'])
        self.agent = self.client_class(HTTP_AUTHORIZATION='Bearer ' + token)
        now = timezone.now()
        self.product_code = '{11111111-2222-3333-4444-555555555555}'
        self.profile = {
            'schema_version': 1,
            'install': {'method': 'msi', 'arguments': [], 'product_code': self.product_code},
            'update': {'method': 'msi_upgrade', 'arguments': []},
            'uninstall': {'method': 'msi_product_code', 'product_code': self.product_code},
            'detection': {'method': 'msi_product_code', 'product_code': self.product_code},
            'expected_version': '2.3.4',
            'reboot_behavior': 'report_only',
        }
        self.package = SoftwarePackageRecord.objects.create(
            friendly_name='Synthetic MSI ACK', package_type=SoftwarePackageRecord.TYPE_MSI,
            artifact_key='synthetic/ack.msi', sha256='a' * 64, size_bytes=42,
            approval_status=SoftwarePackageRecord.STATUS_APPROVED, approved_at=now, approved_by=self.operator,
            execution_profile=self.profile, execution_profile_revision=1,
            execution_profile_sha256=execution_profile_digest(self.profile),
            execution_profile_artifact_sha256='a' * 64, execution_profile_artifact_revision=1,
            execution_profile_status='approved', execution_profile_approved_at=now,
            execution_profile_approved_by=self.operator,
        )

    def _create_dispatched_job(self, operation):
        with patch('agents.software_operation_contract.internal_capability', return_value=True), \
                patch('agents.software_operation_contract._artifact_matches', return_value=True):
            record, created = create_software_operation_job(
                self.operator, self.machine.pk, self.package.pk, operation, str(uuid.uuid4()),
            )
        self.assertTrue(created)
        job = record.job
        job.status = AgentJob.STATUS_SENT
        job.dispatched_at = timezone.now()
        job.save(update_fields=['status', 'dispatched_at'])
        return job

    def _serialized_agent_result(self, job, status, *, exit_code=0, reboot_pending=False, error_code=''):
        snapshot = job.payload
        if status == AgentJob.STATUS_COMPLETED:
            output = {
                'schema_version': 2,
                'operation_id': snapshot['operation_id'], 'operation': snapshot['operation'],
                'request_id': snapshot['request_id'], 'package_uuid': snapshot['package_uuid'],
                'artifact_sha256': snapshot['artifact_sha256'], 'artifact_revision': snapshot['artifact_revision'],
                'profile_revision': snapshot['profile_revision'], 'profile_sha256': snapshot['profile_sha256'],
                'phase': 'verified', 'verified': True, 'success': True, 'exit_code': exit_code,
                'reboot_pending': reboot_pending,
                'evidence': {
                    'method': 'msi_product_code', 'product_code': self.product_code,
                    'version': '' if snapshot['operation'] == 'uninstall' else '2.3.4',
                    'installed': snapshot['operation'] != 'uninstall',
                },
            }
        else:
            output = {
                'schema_version': 2,
                'operation_id': snapshot['operation_id'], 'operation': snapshot['operation'],
                'request_id': snapshot['request_id'], 'package_uuid': snapshot['package_uuid'],
                'artifact_sha256': snapshot['artifact_sha256'], 'artifact_revision': snapshot['artifact_revision'],
                'profile_revision': snapshot['profile_revision'], 'profile_sha256': snapshot['profile_sha256'],
                'phase': 'msiexec', 'verified': False, 'success': False, 'error_code': error_code,
                'exit_code': exit_code, 'reboot_pending': False,
                'evidence': {'method': 'msi_product_code', 'product_code': '', 'version': '', 'installed': False},
            }
        # This is the serialized result wrapper produced by JobExecutor.LimitResult.
        return {
            'job_id': str(job.pk), 'status': status,
            'started_at': timezone.now().isoformat(), 'finished_at': timezone.now().isoformat(),
            'duration_seconds': 1.25, 'exit_code': exit_code if status == AgentJob.STATUS_COMPLETED else 1,
            'stdout': '', 'stderr': '', 'error_message': '',
            'result': {
                'output': output, 'output_truncated': False,
                'agent_version': '0.1.1.0-rc48', 'machine_id': self.machine.machine_id,
            },
        }

    def _post_result(self, body, result_id):
        return self.agent.post(
            reverse('agent-jobs-result'), data=json.dumps(body), content_type='application/json',
            HTTP_IDEMPOTENCY_KEY=result_id,
        )

    def test_serialized_agent_envelope_accepts_install_update_uninstall_and_reboot_pending(self):
        for operation, exit_code, reboot in (
            ('install', 3010, True), ('update', 0, False), ('uninstall', 0, False),
        ):
            with self.subTest(operation=operation):
                job = self._create_dispatched_job(operation)
                result_id = f'job-{job.pk}-completed'
                response = self._post_result(self._serialized_agent_result(
                    job, AgentJob.STATUS_COMPLETED, exit_code=exit_code, reboot_pending=reboot,
                ), result_id)
                self.assertEqual(response.status_code, 200, response.content)
                job.refresh_from_db()
                self.assertEqual(job.status, AgentJob.STATUS_COMPLETED)
                self.assertEqual(job.result['agent_version'], '0.1.1.0-rc48')
                self.assertEqual(job.error_code, '')

    def test_serialized_agent_failure_propagates_error_code_and_rejects_invalid_code(self):
        job = self._create_dispatched_job('install')
        result_id = f'job-{job.pk}-failed'
        body = self._serialized_agent_result(
            job, AgentJob.STATUS_FAILED, exit_code=1603, error_code='SOFTWARE_MSI_EXIT_FAILED',
        )
        invalid = json.loads(json.dumps(body))
        invalid['result']['output']['error_code'] = 'bad code'
        rejected = self._post_result(invalid, result_id + '-invalid')
        self.assertEqual(rejected.status_code, 400, rejected.content)
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_SENT)

        accepted = self._post_result(body, result_id)
        self.assertEqual(accepted.status_code, 200, accepted.content)
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_FAILED)
        self.assertEqual(job.error_code, 'SOFTWARE_MSI_EXIT_FAILED')

    def test_serialized_agent_ack_replay_is_idempotent(self):
        job = self._create_dispatched_job('install')
        result_id = f'job-{job.pk}-completed'
        body = self._serialized_agent_result(job, AgentJob.STATUS_COMPLETED)
        first = self._post_result(body, result_id)
        self.assertEqual(first.status_code, 200, first.content)
        replay = self._post_result(body, result_id)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertTrue(replay.json().get('duplicate'))
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_COMPLETED)
