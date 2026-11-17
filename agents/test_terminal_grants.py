import asyncio
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
import sys
import unittest

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command, CommandError
from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from asgiref.sync import sync_to_async

from . import terminal_transport as transport
from .models import AgentMachine, AuditEvent, InventorySnapshot, TerminalAccessGrant, TerminalSession
from .terminal_grants import (
    GrantExecutionIdentity, PILOT_ENDPOINT_ID, PILOT_MACHINE_ID,
    PILOT_OPERATOR_USERNAME, change_conpty_grant, inspect_conpty_grant,
    inspect_conpty_grant_for_revocation,
    verify_execution_identity,
)
from .terminal_sessions import create_session
from .terminal_test_harness import LoopbackASGI, WSClient


def pilot_fixture(*, grant=True):
    endpoint, token = AgentMachine.create_with_token(
        id=PILOT_ENDPOINT_ID, hostname='SYNTHETIC-PILOT', machine_id=PILOT_MACHINE_ID,
    )
    User = get_user_model()
    operator = User.objects.create_user(PILOT_OPERATOR_USERNAME, is_staff=True, password='synthetic-only')
    permission = Permission.objects.get(content_type__app_label='agents', codename='open_terminalsession')
    operator.user_permissions.add(permission)
    if grant:
        TerminalAccessGrant.objects.create(operator=operator, endpoint=endpoint,
            enabled=True, allow_conpty=True, allow_admin_auth=False,
            expires_at=timezone.now() + timedelta(minutes=10))
        InventorySnapshot.objects.create(machine=endpoint, hostname=endpoint.hostname,
        collected_at=timezone.now(), raw_payload={
            'snapshot_source': 'heartbeat', 'machine_id': PILOT_MACHINE_ID,
            'agent_version': endpoint.agent_version,
            'agent': {'capabilities': ['remote_terminal_transport:1', 'remote_terminal_conpty:1']},
        })
    return endpoint, token, operator


def actor():
    return GrantExecutionIdentity(username='ops-auditor', uid=1201, effective_uid=0)


@override_settings(NIGHTOWL_TECHNICAL_USERNAMES={'gabriel.oliveira'},
    NIGHTOWL_TERMINAL_GRANT_ADMIN_USERS={'ops-auditor'},
    NIGHTOWL_TERMINAL_ENABLED=True, NIGHTOWL_TERMINAL_SINGLE_PROCESS=True,
    NIGHTOWL_TERMINAL_CONPTY_ENABLED=True)
class TerminalConptyGrantServiceTests(TransactionTestCase):
    def setUp(self):
        self.endpoint, _, self.operator = pilot_fixture()
        self.kwargs = {
            'endpoint_id': str(PILOT_ENDPOINT_ID), 'machine_id': PILOT_MACHINE_ID,
            'operator_username': PILOT_OPERATOR_USERNAME,
        }

    def change(self, action='grant', **kwargs):
        ttl_minutes = kwargs.pop('ttl_minutes', 20 if action == 'grant' else None)
        return change_conpty_grant(action=action, actor=actor(), reason='Homologação aprovada',
            approval_ref='CHG-TEST-1042', ttl_minutes=ttl_minutes,
            **self.kwargs, **kwargs)

    def test_missing_and_expired_grants_are_reported_then_grant_can_be_renewed(self):
        TerminalAccessGrant.objects.all().delete()
        self.assertFalse(inspect_conpty_grant(**self.kwargs)['grant_exists'])
        first = self.change()
        grant = TerminalAccessGrant.objects.get()
        self.assertEqual(first['action'], 'granted')
        self.assertTrue(grant.allow_conpty)
        self.assertFalse(grant.allow_admin_auth)
        grant.expires_at = timezone.now() - timedelta(seconds=1)
        grant.save(update_fields=['expires_at'])
        self.assertFalse(inspect_conpty_grant(**self.kwargs)['grant_current'])
        renewed = self.change()
        grant.refresh_from_db()
        self.assertEqual(renewed['action'], 'renewed')
        self.assertTrue(grant.expires_at > timezone.now())
        self.assertEqual(set(AuditEvent.objects.filter(event_type__startswith='terminal.grant_')
                             .values_list('event_type', flat=True)),
                         {'terminal.grant_granted', 'terminal.grant_renewed'})

    def test_revoke_disables_only_conpty_and_audits_actor_target_and_reason(self):
        outcome = self.change('revoke')
        grant = TerminalAccessGrant.objects.get()
        self.assertEqual(outcome['action'], 'revoked')
        self.assertFalse(grant.enabled)
        self.assertFalse(grant.allow_conpty)
        self.assertFalse(grant.allow_admin_auth)
        event = AuditEvent.objects.get(event_type='terminal.grant_revoked')
        self.assertEqual(event.actor_name, 'ops-auditor')
        self.assertEqual(event.endpoint_id, self.endpoint.pk)
        self.assertEqual(event.description, 'Homologação aprovada')
        self.assertEqual(event.metadata['approval_ref'], 'CHG-TEST-1042')
        self.assertEqual(event.metadata['executor_uid'], 1201)
        self.assertEqual(event.metadata['endpoint_uuid'], str(PILOT_ENDPOINT_ID))
        self.assertNotIn('credential', json.dumps(event.metadata).casefold())

    def test_refuses_admin_auth_and_never_changes_it(self):
        grant = TerminalAccessGrant.objects.get()
        TerminalAccessGrant.objects.filter(pk=grant.pk).update(allow_admin_auth=True)
        with self.assertRaises(PermissionDenied):
            self.change('revoke')
        grant.refresh_from_db()
        self.assertTrue(grant.allow_admin_auth)
        self.assertTrue(grant.enabled)
        self.assertFalse(AuditEvent.objects.filter(event_type__startswith='terminal.grant_').exists())

    def test_service_refuses_unallowlisted_executor_identity(self):
        unauthorized = GrantExecutionIdentity(username='other-operator', uid=1300, effective_uid=0)
        with self.assertRaises(PermissionDenied):
            change_conpty_grant(action='revoke', **self.kwargs, actor=unauthorized,
                reason='Motivo', approval_ref='CHG-TEST-1046')
        self.assertTrue(TerminalAccessGrant.objects.get().enabled)
        self.assertFalse(AuditEvent.objects.filter(event_type__startswith='terminal.grant_').exists())

    def test_audit_failure_rolls_back_grant_change(self):
        grant = TerminalAccessGrant.objects.get()
        before = (grant.enabled, grant.allow_conpty, grant.expires_at)
        with patch('agents.terminal_grants.AuditEvent.objects.create', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.change()
        grant.refresh_from_db()
        self.assertEqual((grant.enabled, grant.allow_conpty, grant.expires_at), before)
        self.assertFalse(AuditEvent.objects.filter(event_type__startswith='terminal.grant_').exists())

    def test_rejects_wrong_endpoint_machine_id_operator_duplicate_machine_id_and_bad_ttl(self):
        with self.assertRaises(PermissionDenied):
            change_conpty_grant(action='grant', endpoint_id=str(uuid.uuid4()),
                machine_id=PILOT_MACHINE_ID, operator_username=PILOT_OPERATOR_USERNAME,
                actor=actor(), reason='Motivo', approval_ref='CHG-1', ttl_minutes=10)
        with self.assertRaises(PermissionDenied):
            change_conpty_grant(action='grant', endpoint_id=str(PILOT_ENDPOINT_ID),
                machine_id='wrong-machine-id', operator_username=PILOT_OPERATOR_USERNAME,
                actor=actor(), reason='Motivo', approval_ref='CHG-1', ttl_minutes=10)
        with self.assertRaises(PermissionDenied):
            change_conpty_grant(action='grant', endpoint_id=str(PILOT_ENDPOINT_ID),
                machine_id=PILOT_MACHINE_ID, operator_username='other-user',
                actor=actor(), reason='Motivo', approval_ref='CHG-1', ttl_minutes=10)
        self.assertTrue(TerminalAccessGrant.objects.get().enabled)
        self.assertFalse(AuditEvent.objects.filter(event_type__startswith='terminal.grant_').exists())
        decoy, _ = AgentMachine.create_with_token(
            hostname=self.endpoint.hostname, machine_id='synthetic-different-machine-id')
        state = inspect_conpty_grant(**self.kwargs)
        self.assertEqual(state['endpoint_id'], str(PILOT_ENDPOINT_ID))
        decoy.delete()
        AgentMachine.create_with_token(hostname=self.endpoint.hostname, machine_id=PILOT_MACHINE_ID)
        with self.assertRaises(PermissionDenied):
            inspect_conpty_grant(**self.kwargs)
        AgentMachine.objects.exclude(pk=PILOT_ENDPOINT_ID).delete()
        self.endpoint.delete()
        with self.assertRaises(ValidationError):
            inspect_conpty_grant(**self.kwargs)
        for ttl in (0, 61, True):
            with self.subTest(ttl=ttl), self.assertRaises(ValidationError):
                self.change(ttl_minutes=ttl)

    def test_inactive_or_unauthorized_operator_and_missing_revoke_are_rejected(self):
        self.operator.user_permissions.clear()
        self.assertFalse(inspect_conpty_grant(**self.kwargs)['operator_has_permission'])
        with self.assertRaises(PermissionDenied):
            self.change()
        permission = Permission.objects.get(content_type__app_label='agents', codename='open_terminalsession')
        self.operator.user_permissions.add(permission)
        self.operator.is_active = False
        self.operator.save(update_fields=['is_active'])
        self.assertFalse(inspect_conpty_grant(**self.kwargs)['operator_active'])
        with self.assertRaises(PermissionDenied):
            self.change()
        self.operator.is_active = True
        self.operator.save(update_fields=['is_active'])
        TerminalAccessGrant.objects.all().delete()
        with self.assertRaises(ValidationError):
            self.change('revoke')

    def test_status_reports_inactive_endpoint_but_mutation_is_blocked(self):
        self.endpoint.is_active = False
        self.endpoint.save(update_fields=['is_active'])
        state = inspect_conpty_grant(**self.kwargs)
        self.assertFalse(state['endpoint_active'])
        self.assertFalse(state['authorization_ready'])
        with self.assertRaises(PermissionDenied):
            self.change()

    def test_revocation_works_when_beneficiary_is_inactive_but_grant_does_not(self):
        self.operator.is_active = False
        self.operator.save(update_fields=['is_active'])
        self.assertFalse(inspect_conpty_grant_for_revocation(**self.kwargs)['operator_active'])
        with self.assertRaises(PermissionDenied):
            self.change('grant')
        outcome = self.change('revoke')
        self.assertEqual(outcome['action'], 'revoked')
        self.assertFalse(TerminalAccessGrant.objects.get().enabled)
        event = AuditEvent.objects.get(event_type='terminal.grant_revoked')
        self.assertFalse(event.metadata['operator_active'])

    def test_revocation_works_after_beneficiary_permission_is_removed(self):
        self.operator.user_permissions.clear()
        self.operator._perm_cache = None
        self.assertFalse(inspect_conpty_grant_for_revocation(**self.kwargs)['operator_has_permission'])
        with self.assertRaises(PermissionDenied):
            self.change('grant')
        self.change('revoke')
        event = AuditEvent.objects.get(event_type='terminal.grant_revoked')
        self.assertFalse(event.metadata['operator_has_permission'])
        self.assertFalse(TerminalAccessGrant.objects.get().allow_admin_auth)

    def test_revocation_works_for_inactive_endpoint_but_grant_does_not(self):
        self.endpoint.is_active = False
        self.endpoint.save(update_fields=['is_active'])
        self.assertFalse(inspect_conpty_grant_for_revocation(**self.kwargs)['endpoint_active'])
        with self.assertRaises(PermissionDenied):
            self.change('grant')
        self.change('revoke')
        event = AuditEvent.objects.get(event_type='terminal.grant_revoked')
        self.assertFalse(event.metadata['endpoint_active'])

    def test_revocation_by_fixed_uuid_audits_changed_machine_id_but_grant_is_blocked(self):
        self.endpoint.machine_id = 'replacement-machine-id'
        self.endpoint.save(update_fields=['machine_id'])
        preview = inspect_conpty_grant_for_revocation(**self.kwargs)
        self.assertTrue(preview['machine_id_diverged'])
        self.assertEqual(preview['machine_id'], 'replacement-machine-id')
        with self.assertRaises(PermissionDenied):
            self.change('grant')
        self.change('revoke')
        event = AuditEvent.objects.get(event_type='terminal.grant_revoked')
        self.assertTrue(event.metadata['machine_id_diverged'])
        self.assertEqual(event.metadata['machine_id'], 'replacement-machine-id')
        self.assertEqual(event.metadata['expected_machine_id'], PILOT_MACHINE_ID)
        self.assertFalse(TerminalAccessGrant.objects.get().allow_admin_auth)

    def test_revocation_with_missing_grant_never_creates_one_even_if_identity_drifted(self):
        TerminalAccessGrant.objects.all().delete()
        self.endpoint.machine_id = 'replacement-machine-id'
        self.endpoint.is_active = False
        self.endpoint.save(update_fields=['machine_id', 'is_active'])
        with self.assertRaises(ValidationError):
            self.change('revoke')
        self.assertFalse(TerminalAccessGrant.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(event_type='terminal.grant_revoked').exists())

    def test_reason_and_approval_are_required_and_secret_like_values_rejected(self):
        for reason, approval in [('', 'CHG-1'), ('password=hunter2', 'CHG-1'), ('reason', '')]:
            with self.subTest(reason=reason), self.assertRaises(ValidationError):
                change_conpty_grant(action='grant', **self.kwargs, actor=actor(), reason=reason,
                    approval_ref=approval, ttl_minutes=10)
        self.assertFalse(AuditEvent.objects.filter(event_type__startswith='terminal.grant_').exists())

    def test_execution_identity_requires_root_sudo_and_allowlisted_matching_uid(self):
        with self.assertRaises(PermissionDenied):
            verify_execution_identity(effective_uid=1000, sudo_user='ops-auditor', sudo_uid='1201',
                                     allowed_users={'ops-auditor'})
        with self.assertRaises(PermissionDenied):
            verify_execution_identity(effective_uid=0, sudo_user='root', sudo_uid='0',
                                     allowed_users={'root'})
        with self.assertRaises(PermissionDenied):
            verify_execution_identity(effective_uid=0, sudo_user='not-allowed', sudo_uid='1201',
                                     allowed_users={'ops-auditor'})
        with patch.dict(sys.modules, {'pwd': SimpleNamespace(
                getpwnam=lambda username: SimpleNamespace(pw_uid=1201))}):
            identity = verify_execution_identity(effective_uid=0, sudo_user='ops-auditor',
                sudo_uid='1201', allowed_users={'OPS-AUDITOR'})
        self.assertEqual((identity.username, identity.uid, identity.effective_uid), ('ops-auditor', 1201, 0))

    @unittest.skipUnless(connection.vendor == 'postgresql', 'Requer locking real do PostgreSQL.')
    def test_create_session_waiting_on_revoke_cannot_use_pre_revocation_grant(self):
        revoke_has_lock = threading.Event()
        allow_revoke_commit = threading.Event()
        create_attempting = threading.Event()

        def hold_audit_until_create_waits(**_kwargs):
            revoke_has_lock.set()
            if not allow_revoke_commit.wait(5):
                raise TimeoutError('test synchronization timed out')

        operator_id = self.operator.pk
        endpoint_id = self.endpoint.pk

        def revoke():
            close_old_connections()
            try:
                return change_conpty_grant(action='revoke', **self.kwargs, actor=actor(),
                    reason='Concorrência de teste', approval_ref='CHG-PG-1045')
            finally:
                close_old_connections()

        def create():
            close_old_connections()
            try:
                create_attempting.set()
                from django.contrib.auth import get_user_model
                operator = get_user_model().objects.get(pk=operator_id)
                return create_session(operator, endpoint_id, 'conpty')
            finally:
                close_old_connections()

        with patch('agents.terminal_grants._audit_grant_change', side_effect=hold_audit_until_create_waits):
            with ThreadPoolExecutor(max_workers=2) as pool:
                revoke_future = pool.submit(revoke)
                self.assertTrue(revoke_has_lock.wait(3))
                create_future = pool.submit(create)
                self.assertTrue(create_attempting.wait(3))
                time.sleep(0.15)
                self.assertFalse(create_future.done())
                allow_revoke_commit.set()
                revoke_future.result(timeout=5)
                with self.assertRaises(PermissionError) as rejected:
                    create_future.result(timeout=5)
                self.assertEqual(str(rejected.exception), 'terminal_forbidden')
        self.assertFalse(TerminalSession.objects.filter(endpoint=self.endpoint,
            status__in=TerminalSession.ACTIVE).exists())


@override_settings(NIGHTOWL_TECHNICAL_USERNAMES={'gabriel.oliveira'},
    NIGHTOWL_TERMINAL_GRANT_ADMIN_USERS={'ops-auditor'},
    NIGHTOWL_TERMINAL_ENABLED=True, NIGHTOWL_TERMINAL_SINGLE_PROCESS=True,
    NIGHTOWL_TERMINAL_CONPTY_ENABLED=True, NIGHTOWL_TERMINAL_TEST_LOOPBACK=True,
    ALLOWED_HOSTS=['testserver', '127.0.0.1'])
class TerminalConptyGrantRevocationTransportTests(TransactionTestCase):
    def setUp(self):
        self.endpoint, token, self.operator = pilot_fixture()
        transport.HUB = transport.TerminalHub()
        self.info = create_session(self.operator, self.endpoint.pk, 'conpty')
        response = Client(
            HTTP_AUTHORIZATION='Bearer ' + token
        ).post(reverse('terminal-agent-poll'), {'machine_id': PILOT_MACHINE_ID},
            content_type='application/json', secure=True)
        self.agent_info = response.json()['session']

    async def test_revoke_transaction_closes_both_wss_peers_and_session(self):
        async with LoopbackASGI() as server:
            operator = await WSClient.connect(server, self.info['websocket_path'], self.info['credential'])
            await operator.until('connected')
            agent = await WSClient.connect(server, self.agent_info['websocket_path'],
                self.agent_info['credential'], origin=False)
            await operator.until('transport_ready')
            await agent.until('transport_ready')

            await sync_to_async(change_conpty_grant)(action='revoke',
                endpoint_id=str(PILOT_ENDPOINT_ID), machine_id=PILOT_MACHINE_ID,
                operator_username=PILOT_OPERATOR_USERNAME, actor=actor(),
                reason='Revogação de teste', approval_ref='CHG-TEST-1043')

            operator_closed, agent_closed = await asyncio.gather(
                operator.until('closed'), agent.until('closed'))
            self.assertEqual(operator_closed['code'], 'session_expired_or_revoked')
            self.assertEqual(agent_closed['code'], 'session_expired_or_revoked')
            await operator.close()
            await agent.close()
            session = await sync_to_async(TerminalSession.objects.get)(pk=self.info['session_id'])
            self.assertEqual(session.status, 'cancelled')
            self.assertEqual(session.reason, 'grant_revoked')
            self.assertIsNotNone(session.closed_at)


@override_settings(NIGHTOWL_TERMINAL_GRANT_ADMIN_USERS={'ops-auditor'})
class TerminalConptyGrantCommandTests(TransactionTestCase):
    def setUp(self):
        self.endpoint, _, self.operator = pilot_fixture(grant=False)
        self.options = [
            '--action', 'grant', '--endpoint-id', str(PILOT_ENDPOINT_ID),
            '--machine-id', PILOT_MACHINE_ID, '--operator', PILOT_OPERATOR_USERNAME,
            '--reason', 'Aprovado para teste', '--approval-ref', 'CHG-TEST-1044',
            '--ttl-minutes', '10',
        ]
        self.execution = patch('agents.management.commands.terminal_conpty_grant.verify_execution_identity',
            return_value=actor()).start()
        self.addCleanup(self.execution.stop)

    def test_default_action_is_preview_and_apply_needs_exact_uuid_confirmation(self):
        output = StringIO()
        call_command('terminal_conpty_grant', *self.options, stdout=output)
        self.assertIn('"no_changes": true', output.getvalue())
        self.assertFalse(TerminalAccessGrant.objects.exists())
        with self.assertRaises(CommandError):
            call_command('terminal_conpty_grant', *self.options, '--apply')
        self.assertFalse(TerminalAccessGrant.objects.exists())

    def test_apply_requires_fixed_target_and_changes_only_after_confirmation(self):
        output = StringIO()
        call_command('terminal_conpty_grant', *self.options, '--apply',
            '--confirm-endpoint', str(PILOT_ENDPOINT_ID), stdout=output)
        grant = TerminalAccessGrant.objects.get()
        self.assertTrue(grant.enabled and grant.allow_conpty)
        self.assertFalse(grant.allow_admin_auth)
