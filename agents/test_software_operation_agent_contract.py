import uuid
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from .models import AgentJob, AgentMachine, SoftwarePackageOperation, SoftwarePackageRecord
from .internal_jobs import validate_internal_payload


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
