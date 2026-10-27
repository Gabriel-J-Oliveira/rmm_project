import json
import uuid
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import connection, connections, IntegrityError
from django.test import Client, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .identity import IdentityConflict, lock_identity_registry, validate_runtime_identity
from .models import (hash_agent_token, AgentEnrollmentLog, AgentEnrollmentToken, AgentJob,
                     AgentJobResultReceipt, AgentMachine, AuditEvent, InventorySnapshot)
from .services import record_collection, record_heartbeat


class IdentityTestSupport:
    def setUp(self):
        self.authorization, self.token = AgentEnrollmentToken.create_with_token(
            name='test', allowed_domain='control.local', max_uses=100)

    def machine(self, hostname='CS-CVEL-0262', serial='Default string', **kwargs):
        instance = AgentMachine(hostname=hostname, domain='control.local',
                                machine_id=kwargs.pop('machine_id', str(uuid.uuid4())),
                                serial_number=serial, **kwargs)
        token = 'rmm_live_test_' + hostname
        if AgentMachine.objects.filter(hostname=hostname).exists():
            token += str(uuid.uuid4())
        instance.set_agent_token(token)
        instance.save()
        return instance

    def enroll(self, hostname, machine_id, serial='', bearer='', **extra):
        client = Client(HTTP_AUTHORIZATION=f'Bearer {bearer}') if bearer else Client()
        return client.post(reverse('agent-enroll'), data={
            'hostname': hostname, 'domain': 'control.local', 'machine_id': machine_id,
            'serial_number': serial, 'enrollment_token': self.token, **extra,
        }, content_type='application/json')

    def unchanged(self, instance, before, count=0):
        instance.refresh_from_db()
        self.assertEqual(AgentMachine.objects.filter(pk=instance.pk).values().get(), before)
        self.assertEqual(InventorySnapshot.objects.filter(machine=instance).count(), count)


class EnrollmentIdentityTests(IdentityTestSupport, TestCase):
    def test_both_real_collisions_create_separate_endpoints_without_rotation(self):
        cases = [
            ('CS-CVEL-0262', 'CS-CVEL-0059', 'Default string', '610fb016-5485-4aa2-a7d0-10892c92bdbd'),
            ('CS-CVEL-0133', 'CS-CVEL-0029', 'To Be Filled By O.E.M.', 'ab9352a5-6e6f-4652-90e5-b3dee5a6f4e3'),
        ]
        for first, second, serial, endpoint_id in cases:
            with self.subTest(first=first):
                existing = self.machine(first, serial, id=endpoint_id)
                before = AgentMachine.objects.filter(pk=existing.pk).values().get()
                response = self.enroll(second, str(uuid.uuid4()), serial)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertNotEqual(response.json()['endpoint_id'], endpoint_id)
                self.unchanged(existing, before)
                log = AgentEnrollmentLog.objects.latest('created_at')
                self.assertEqual(log.metadata['identity_source'], 'new')

    def test_even_unique_plausible_serial_is_not_identity(self):
        existing = self.machine(serial='ABC-123-ZYX')
        response = self.enroll('CS-NEW', 'new-mid', existing.serial_number)
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json()['endpoint_id'], str(existing.pk))

    def test_existing_id_or_name_cannot_rotate_without_bearer(self):
        existing = self.machine()
        before = AgentMachine.objects.filter(pk=existing.pk).values().get()
        for mid, hostname in [(existing.machine_id, existing.hostname),
                              ('different-id', existing.hostname),
                              (existing.machine_id, 'CS-CVEL-0059')]:
            response = self.enroll(hostname, mid)
            self.assertEqual(response.status_code, 409, response.content)
            self.unchanged(existing, before)
        self.authorization.refresh_from_db()
        self.assertEqual(self.authorization.used_count, 0)
        self.assertEqual(AgentEnrollmentLog.objects.filter(status='denied').count(), 3)
        self.assertEqual(AuditEvent.objects.filter(event_type='endpoint.identity_conflict').count(), 3)

    def test_authenticated_reenrollment_preserves_current_credential(self):
        existing = self.machine()
        bearer = 'rmm_live_test_' + existing.hostname
        response = self.enroll(existing.hostname, existing.machine_id, bearer=bearer)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['agent_token'], bearer)
        existing.refresh_from_db()
        self.assertEqual(existing.agent_token_hash, hash_agent_token(bearer))
        self.assertEqual(AgentEnrollmentLog.objects.latest('created_at').metadata['created_or_existing'], 'existing_preserved')

    def test_other_endpoint_bearer_rejected_even_for_new_identity(self):
        existing = self.machine()
        response = self.enroll('CS-NEW', 'new-mid', bearer='rmm_live_test_' + existing.hostname)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(AgentMachine.objects.count(), 1)
        self.authorization.refresh_from_db()
        self.assertEqual(self.authorization.used_count, 0)

    def test_mismatched_machine_id_with_valid_bearer_rejected(self):
        existing = self.machine()
        response = self.enroll(existing.hostname, 'wrong-id', bearer='rmm_live_test_' + existing.hostname)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'machine_id_mismatch')

    def test_ambiguous_machine_ids_and_hostnames_never_choose_first(self):
        existing = self.machine()
        self.machine('CS-OTHER', machine_id=existing.machine_id.upper())
        response = self.enroll(existing.hostname, existing.machine_id)
        self.assertEqual(response.json()['error'], 'identity_ambiguous')
        self.machine(existing.hostname)
        response = self.enroll(existing.hostname, 'another-id')
        self.assertEqual(response.json()['error'], 'identity_ambiguous')

    def test_crossed_machine_id_and_hostname_rejected(self):
        first = self.machine()
        second = self.machine('CS-OTHER')
        response = self.enroll(second.hostname, first.machine_id)
        self.assertEqual(response.json()['error'], 'identity_keys_conflict')

    def test_new_machine_requires_non_placeholder_identifier(self):
        for value in ['', 'machine_id', '00000000-0000-0000-0000-000000000000', 'bad id']:
            response = self.enroll('CS-NEW', value)
            self.assertEqual(response.status_code, 409)
        self.assertFalse(AgentMachine.objects.exists())
        self.authorization.refresh_from_db()
        self.assertEqual(self.authorization.used_count, 0)

    def test_gpo_tokens_are_explicitly_bound_and_not_domain_pool(self):
        scoped, secret = AgentEnrollmentToken.create_with_token(
            name='NightOwlGpo-CS-CVEL-0262', allowed_domain='control.local')
        rejected = self.enroll('CS-CVEL-0059', 'new-id', enrollment_token=secret)
        self.assertEqual(rejected.json()['error'], 'enrollment_target_mismatch')
        scoped.refresh_from_db()
        self.assertEqual(scoped.used_count, 0)
        accepted = self.enroll('cs-cvel-0262', 'new-id', enrollment_token=secret)
        self.assertEqual(accepted.status_code, 200, accepted.content)
        self.authorization.is_active = False
        self.authorization.save()
        rejected = self.enroll('CS-NEW', 'another-id', enrollment_token='')
        self.assertEqual(rejected.status_code, 403)

    def test_mid_name_case_and_domain_trailing_dot(self):
        existing = self.machine()
        response = self.enroll(existing.hostname.lower(), existing.machine_id.upper(),
                               domain='CONTROL.LOCAL.', bearer='rmm_live_test_' + existing.hostname)
        self.assertEqual(response.status_code, 200, response.content)

    def test_failure_after_save_rolls_back_endpoint_and_token(self):
        with mock.patch('agents.views.AgentEnrollView._log_enrollment', side_effect=[RuntimeError('SECRET'), None]):
            response = self.enroll('CS-NEW', 'new-id')
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('SECRET', response.content.decode())
        self.assertFalse(AgentMachine.objects.exists())
        self.authorization.refresh_from_db()
        self.assertEqual(self.authorization.used_count, 0)

    def test_database_failure_rolls_back_savepoint_without_partial_consumption(self):
        with mock.patch('agents.models.AgentEnrollmentToken.mark_used', side_effect=IntegrityError('synthetic')):
            response = self.enroll('CS-NEW', 'new-id')
        self.assertEqual(response.status_code, 500)
        self.assertFalse(AgentMachine.objects.exists())
        self.assertEqual(AgentEnrollmentLog.objects.get().status, AgentEnrollmentLog.STATUS_ERROR)
        self.authorization.refresh_from_db()
        self.assertEqual(self.authorization.used_count, 0)

    def test_missing_success_audit_does_not_announce_committed_enrollment(self):
        with mock.patch('agents.views.create_audit_event', return_value=None):
            response = self.enroll('CS-NEW', 'new-id')
        self.assertEqual(response.status_code, 500)
        self.assertFalse(AgentMachine.objects.exists())
        self.authorization.refresh_from_db()
        self.assertEqual(self.authorization.used_count, 0)


class RuntimeIdentityTests(IdentityTestSupport, TestCase):
    def test_heartbeat_mismatch_rejected_before_any_state_change(self):
        instance = self.machine()
        before = AgentMachine.objects.filter(pk=instance.pk).values().get()
        response = Client(HTTP_AUTHORIZATION='Bearer rmm_live_test_' + instance.hostname).post(
            reverse('agent-heartbeat'), data={'hostname': 'CS-CVEL-0059', 'machine_id': 'wrong-id'},
            content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.unchanged(instance, before)

    def test_inventory_hardware_domain_and_fqdn_conflicts_rollback(self):
        instance = self.machine(serial='DELL-123')
        before = AgentMachine.objects.filter(pk=instance.pk).values().get()
        for extra in [{'hardware': {'serial_number': 'DELL-999'}},
                      {'system': {'domain': 'other.local'}},
                      {'fqdn': 'another.control.local'},
                      {'system': {'machine_id': 'other-id'}}]:
            with self.subTest(extra=extra), self.assertRaises(IdentityConflict):
                record_collection(instance, 'full_inventory', extra)
            self.unchanged(instance, before)

    def test_controlled_rename_requires_same_id_and_is_audited(self):
        instance = self.machine()
        payload = {'hostname': 'CS-RENAMED', 'domain': instance.domain, 'machine_id': instance.machine_id}
        record_heartbeat(instance, payload, payload)
        instance.refresh_from_db()
        self.assertEqual(instance.hostname, 'CS-RENAMED')
        self.assertEqual(instance.fqdn, 'CS-RENAMED.control.local')
        self.assertTrue(AuditEvent.objects.filter(event_type='endpoint.identity_renamed').exists())
        with self.assertRaises(IdentityConflict):
            record_heartbeat(instance, {'hostname': 'CS-NEXT'}, {'hostname': 'CS-NEXT'})

    def test_legacy_missing_id_unchanged_identity_stays_operational(self):
        instance = self.machine()
        payload = {'hostname': instance.hostname}
        record_heartbeat(instance, payload, payload)
        instance.refresh_from_db()
        self.assertEqual(instance.serial_number, 'Default string')
        self.assertEqual(instance.domain, 'control.local')
        self.assertIsNotNone(instance.last_seen_at)

    def test_placeholder_cannot_erase_meaningful_serial_before_hardware_change(self):
        instance = self.machine(serial='DELL-123')
        record_collection(instance, 'hardware', {'serial_number': 'Default string'})
        instance.refresh_from_db()
        self.assertEqual(instance.serial_number, 'DELL-123')
        with self.assertRaises(IdentityConflict):
            record_collection(instance, 'hardware', {'serial_number': 'DELL-999'})
        with self.assertRaises(IdentityConflict):
            record_collection(instance, 'hardware', {'bios': {'serial_number': 'DELL-999'}})

    def test_old_snapshot_cannot_replace_current_identity_without_new_evidence(self):
        instance = self.machine(serial='DELL-123')
        InventorySnapshot.objects.create(machine=instance, hostname='CS-OLD', domain='old.local',
            serial_number='DELL-999', collected_at=timezone.now())
        record_collection(instance, 'software', {'installed_software': []})
        instance.refresh_from_db()
        self.assertEqual((instance.hostname, instance.domain, instance.serial_number),
                         ('CS-CVEL-0262', 'control.local', 'DELL-123'))

    def test_blank_stored_id_can_bind_once_but_cannot_steal_existing_id(self):
        existing = self.machine()
        legacy = self.machine('CS-LEGACY', machine_id='')
        with self.assertRaises(IdentityConflict):
            record_collection(legacy, 'system', {'machine_id': existing.machine_id})
        record_collection(legacy, 'system', {'machine_id': 'legacy-new-id'})
        legacy.refresh_from_db()
        self.assertEqual(legacy.machine_id, 'legacy-new-id')

    def test_legacy_first_binding_api_returns_the_bound_id_not_endpoint_uuid(self):
        for path in ['/api/agent/heartbeat/', '/api/agent/inventory/']:
            hostname = 'CS-HEARTBEAT' if 'heartbeat' in path else 'CS-INVENTORY'
            instance = self.machine(hostname, machine_id='')
            response = Client(HTTP_AUTHORIZATION='Bearer rmm_live_test_' + hostname).post(
                path, data={'hostname': hostname, 'machine_id': hostname + '-bound-id'},
                content_type='application/json')
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()['machine_id'], hostname + '-bound-id')

    def test_conflicting_inventory_job_does_not_commit_receipt_or_job(self):
        instance = self.machine()
        job = AgentJob.objects.create(endpoint=instance, job_type=AgentJob.TYPE_FORCE_INVENTORY)
        response = Client(HTTP_AUTHORIZATION='Bearer rmm_live_test_' + instance.hostname).post(
            '/api/agent/jobs/result/', data={'job_id': str(job.pk), 'status': 'completed',
                'result': {'output': {'machine_id': 'other-id', 'system': {'hostname': 'CS-OTHER'}}}},
            content_type='application/json', HTTP_IDEMPOTENCY_KEY='test-conflict')
        self.assertEqual(response.status_code, 409)
        job.refresh_from_db()
        self.assertEqual(job.status, AgentJob.STATUS_QUEUED)
        self.assertFalse(AgentJobResultReceipt.objects.exists())
        self.assertFalse(InventorySnapshot.objects.exists())

    def test_contradictory_raw_and_diagnostic_heartbeat_identity_rejected(self):
        instance = self.machine()
        for raw in [{'hostname': instance.hostname, 'system': {'machine_id': 'wrong'}},
                    {'hostname': instance.hostname, 'diagnostics': {'machine_id': 'wrong'}}]:
            with self.assertRaises(IdentityConflict):
                record_heartbeat(instance, {'hostname': instance.hostname}, raw)
        self.assertFalse(InventorySnapshot.objects.exists())

    def test_first_binding_with_conflicting_embedded_status_changes_nothing(self):
        instance = self.machine(machine_id='')
        before = AgentMachine.objects.filter(pk=instance.pk).values().get()
        response = Client(HTTP_AUTHORIZATION='Bearer rmm_live_test_' + instance.hostname).post(
            '/api/agent/heartbeat/', data={'hostname': instance.hostname, 'machine_id': 'first-id',
                'diagnostics': {'machine_id': 'other-id'}}, content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.unchanged(instance, before)

    def test_operational_status_failure_rolls_back_whole_heartbeat(self):
        instance = self.machine()
        before = AgentMachine.objects.filter(pk=instance.pk).values().get()
        with mock.patch('agents.views.record_agent_operational_status', side_effect=IdentityConflict('synthetic_conflict')):
            response = Client(HTTP_AUTHORIZATION='Bearer rmm_live_test_' + instance.hostname).post(
                '/api/agent/heartbeat/', data={'hostname': instance.hostname,
                    'machine_id': instance.machine_id, 'diagnostics': {'service_status': 'running'}},
                content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.unchanged(instance, before)

    def test_rename_cannot_collide_with_another_hostname(self):
        first = self.machine()
        second = self.machine('CS-OTHER')
        with self.assertRaises(IdentityConflict):
            validate_runtime_identity(first, {'machine_id': first.machine_id, 'hostname': second.hostname})

    def test_postgresql_lock_uses_transaction_scoped_advisory_lock(self):
        with mock.patch.object(connection, 'vendor', 'postgresql'), mock.patch.object(connection, 'cursor') as cursor:
            lock_identity_registry()
            cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
                'SELECT pg_advisory_xact_lock(%s)', [734625190283])


class IdentityAuditTests(IdentityTestSupport, TransactionTestCase):
    def test_audit_is_read_only_and_never_dumps_credentials_or_raw_payloads(self):
        instance = self.machine()
        secret = 'SENTINEL_PRIVATE_CREDENTIAL'
        AgentEnrollmentLog.objects.create(endpoint=instance, hostname=instance.hostname,
            status='success', message=secret, metadata={'token': secret, 'identity_source': 'serial_number'})
        InventorySnapshot.objects.create(machine=instance, collected_at=timezone.now(),
            hostname='CS-CVEL-0059', raw_payload={'token': secret})
        AuditEvent.objects.create(endpoint=instance, event_type='agent.enrolled', title=secret, metadata={'token': secret})
        output = StringIO()
        with CaptureQueriesContext(connection) as queries:
            call_command('audit_agent_identity', endpoint=[str(instance.pk)], stdout=output)
        text = output.getvalue()
        self.assertNotIn(secret, text)
        self.assertNotIn(instance.agent_token_hash, text)
        self.assertNotIn(self.token, text)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'ALTER', 'CREATE')) for q in queries))
        report = json.loads(text)
        self.assertEqual(report['total_endpoints'], 1)
        self.assertEqual(report['endpoints'][0]['snapshot_identities'][0]['hostname'], 'CS-CVEL-0059')
        self.assertTrue(report['endpoints'][0]['serial_generic_or_missing'])


@unittest.skipUnless(connection.vendor == 'postgresql', 'Requires an isolated PostgreSQL test database')
class PostgreSQLEnrollmentIdentityTests(TransactionTestCase):
    def test_simultaneous_first_enrollment_cannot_duplicate_or_rotate_identity(self):
        authorization, secret = AgentEnrollmentToken.create_with_token(
            name='concurrency-test', allowed_domain='control.local', max_uses=10)
        barrier = threading.Barrier(2)

        def enroll():
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                barrier.wait(timeout=10)
                response = Client().post('/api/agent/enroll/', data={
                    'hostname': 'LAB-IDENTITY', 'domain': 'control.local',
                    'machine_id': 'concurrent-identity', 'enrollment_token': secret,
                }, content_type='application/json')
                return response.status_code, response.json()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(enroll), pool.submit(enroll)
            responses = [first.result(timeout=20), second.result(timeout=20)]
        self.assertEqual(sorted(code for code, _ in responses), [200, 409])
        instance = AgentMachine.objects.get(machine_id='concurrent-identity')
        returned_token = next(body['agent_token'] for code, body in responses if code == 200)
        self.assertEqual(instance.agent_token_hash, hash_agent_token(returned_token))
        authorization.refresh_from_db()
        self.assertEqual(authorization.used_count, 1)
