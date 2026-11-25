import asyncio
from django.test import tag
import json
import os
import subprocess
import uuid
from pathlib import Path
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.test import LiveServerTestCase
from django.urls import reverse
from django.utils import timezone
from access_inventory.models import ADUser

from . import terminal_sessions as sessions, terminal_transport as transport
from .models import (AgentMachine, AssetAgentLink, AssetRecord, AuditEvent, InventorySnapshot,
                     PhysicalAsset, TerminalAccessGrant, AdminTerminalAccessGrant, TerminalSession)
from .terminal_test_harness import LoopbackASGI, WSClient
from .test_terminal_transport import fixture
from .admin_terminal_grants import change_admin_terminal_grant


def enable_admin(machine, operator):
    AdminTerminalAccessGrant.objects.update_or_create(operator=operator, endpoint=machine, defaults={
        'enabled': True, 'expires_at': timezone.now() + timedelta(minutes=30), 'created_by': operator,
        'reason': 'Synthetic admin-auth test', 'approval_ref': 'TEST-ADMIN-AUTH'})
    operator.user_permissions.add(Permission.objects.get(
        content_type__app_label='agents', content_type__model='terminalsession',
        codename='open_admin_terminalsession'))
    snapshot = InventorySnapshot.objects.get(machine=machine)
    snapshot.raw_payload['agent']['capabilities'].append('remote_terminal_admin_auth:1')
    snapshot.save(update_fields=['raw_payload'])


@override_settings(NIGHTOWL_TERMINAL_ENABLED=True, NIGHTOWL_TERMINAL_SINGLE_PROCESS=True, NIGHTOWL_TERMINAL_ADMIN_AUTH_ENABLED=True)
class AdminAuthControlTests(TestCase):
    def test_admin_grant_lifecycle_is_audited_independent_and_closes_session(self):
        machine, _, operator = fixture('admin-grant-lifecycle')
        enable_admin(machine, operator)
        operator.user_permissions.add(Permission.objects.get(
            content_type__app_label='agents', content_type__model='terminalsession',
            codename='manage_admin_terminal_grant'))
        info = change_admin_terminal_grant(actor=operator, endpoint_id=machine.pk, machine_id=machine.machine_id,
            action='grant', ttl_minutes=20, reason='Human pilot validation', approval_ref='TEST-RC49-1')
        self.assertTrue(AdminTerminalAccessGrant.objects.get(pk=info['grant_id']).enabled)
        conpty = TerminalAccessGrant.objects.get(endpoint=machine, operator=operator)
        conpty.enabled, conpty.allow_conpty = True, True
        conpty.save(update_fields=['enabled', 'allow_conpty'])
        session = sessions.create_session(operator, machine.pk, 'admin_auth')
        revoked = change_admin_terminal_grant(actor=operator, endpoint_id=machine.pk, machine_id=machine.machine_id,
            action='revoke', reason='Pilot complete', approval_ref='TEST-RC49-2')
        self.assertFalse(AdminTerminalAccessGrant.objects.get(pk=info['grant_id']).enabled)
        conpty.refresh_from_db()
        self.assertTrue(conpty.allow_conpty, 'admin revoke must preserve the independent ConPTY grant')
        self.assertEqual(TerminalSession.objects.get(pk=session['session_id']).status, 'cancelled')
        audits = set(AuditEvent.objects.filter(endpoint=machine).values_list('event_type', flat=True))
        self.assertTrue({'terminal.admin_grant_granted', 'terminal.admin_grant_revoked'} <= audits)

    def test_admin_grant_requires_manager_and_bounded_ttl(self):
        machine, _, operator = fixture('admin-grant-permission')
        for ttl in (0, 61, True):
            with self.assertRaises(Exception):
                change_admin_terminal_grant(actor=operator, endpoint_id=machine.pk, machine_id=machine.machine_id,
                    action='grant', ttl_minutes=ttl, reason='Test', approval_ref='TEST')
        operator.user_permissions.add(Permission.objects.get(
            content_type__app_label='agents', content_type__model='terminalsession',
            codename='manage_admin_terminal_grant'))
        with self.assertRaises(Exception):
            change_admin_terminal_grant(actor=operator, endpoint_id=machine.pk, machine_id='wrong',
                action='grant', ttl_minutes=10, reason='Test', approval_ref='TEST')

    def test_conpty_grant_does_not_authorize_admin_auth_or_dashboard_gate(self):
        machine, _, operator = fixture('admin-auth-separated-grants')
        grant = TerminalAccessGrant.objects.get(endpoint=machine, operator=operator)
        grant.allow_conpty = True
        grant.save(update_fields=['allow_conpty'])
        snapshot = InventorySnapshot.objects.get(machine=machine)
        snapshot.raw_payload['agent']['capabilities'].append('remote_terminal_admin_auth:1')
        snapshot.save(update_fields=['raw_payload'])

        self.client.force_login(operator)
        response = self.client.get(reverse('endpoint-detail', args=[machine.pk]))
        self.assertFalse(response.context['terminal_admin_auth_available'])
        with self.assertRaises(PermissionError):
            sessions.create_session(operator, machine.pk, 'admin_auth')

        enable_admin(machine, operator)
        operator.user_permissions.add(Permission.objects.get(
            content_type__app_label='agents', content_type__model='terminalsession',
            codename='open_admin_terminalsession'))
        response = self.client.get(reverse('endpoint-detail', args=[machine.pk]))
        self.assertTrue(response.context['terminal_admin_auth_available'])
        sessions.close_session(
            sessions.create_session(operator, machine.pk, 'admin_auth')['session_id'],
            'synthetic_test_closed')

    def test_admin_permission_is_explicit_and_scope_bound(self):
        machine, _, operator = fixture('admin-auth-permission-required')
        grant = TerminalAccessGrant.objects.get(endpoint=machine, operator=operator)
        enable_admin(machine, operator)
        snapshot = InventorySnapshot.objects.get(machine=machine)
        snapshot.raw_payload['agent']['capabilities'].append('remote_terminal_admin_auth:1')
        snapshot.save(update_fields=['raw_payload'])
        operator.user_permissions.remove(Permission.objects.get(
            content_type__app_label='agents', content_type__model='terminalsession',
            codename='open_admin_terminalsession'))

        with self.assertRaises(PermissionError):
            sessions.create_session(operator, machine.pk, 'admin_auth')

        admin = get_user_model().objects.create_superuser('admin-no-explicit-permission', password='synthetic-only')
        AdminTerminalAccessGrant.objects.create(operator=admin, endpoint=machine, created_by=admin,
            reason='Synthetic', approval_ref='TEST', expires_at=timezone.now() + timedelta(minutes=30))
        with self.assertRaises(PermissionError):
            sessions.create_session(admin, machine.pk, 'admin_auth')

        operator.user_permissions.add(Permission.objects.get(
            content_type__app_label='agents', content_type__model='terminalsession',
            codename='open_admin_terminalsession'))
        info = sessions.create_session(operator, machine.pk, 'admin_auth')
        sessions.close_session(info['session_id'], 'synthetic_test_closed')

        other_machine, _, other_operator = fixture('admin-auth-wrong-scope')
        enable_admin(other_machine, other_operator)
        other_operator.user_permissions.add(Permission.objects.get(
            content_type__app_label='agents', content_type__model='terminalsession',
            codename='open_admin_terminalsession'))
        other_snapshot = InventorySnapshot.objects.get(machine=other_machine)
        other_snapshot.raw_payload['agent']['capabilities'].append('remote_terminal_admin_auth:1')
        other_snapshot.save(update_fields=['raw_payload'])
        with self.assertRaises(PermissionError):
            sessions.create_session(operator, other_machine.pk, 'admin_auth')

        wrong_operator = get_user_model().objects.create_user('wrong-admin-operator', is_staff=True,
            password='synthetic-only')
        with self.assertRaises(PermissionError):
            sessions.create_session(wrong_operator, machine.pk, 'admin_auth')

    def test_admin_mode_rejects_missing_capability_and_preserves_conpty(self):
        machine, _, operator = fixture('admin-auth-capability-required')
        enable_admin(machine, operator)
        snapshot = InventorySnapshot.objects.get(machine=machine)
        snapshot.raw_payload['agent']['capabilities'].remove('remote_terminal_admin_auth:1')
        snapshot.save(update_fields=['raw_payload'])
        with self.assertRaises(PermissionError):
            sessions.create_session(operator, machine.pk, 'admin_auth')

        grant = TerminalAccessGrant.objects.get(endpoint=machine, operator=operator)
        grant.allow_conpty = True
        grant.save(update_fields=['allow_conpty'])
        snapshot.raw_payload['agent']['capabilities'].append('remote_terminal_conpty:1')
        snapshot.save(update_fields=['raw_payload'])
        with override_settings(NIGHTOWL_TERMINAL_CONPTY_ENABLED=True):
            self.assertIsNone(sessions.validate_mode(operator, machine, 'conpty'))

    def test_admin_requires_own_grant_capability_flag_and_valid_identity(self):
        machine, _, operator = fixture('admin-auth-revalidation')
        enable_admin(machine, operator)
        info = sessions.create_session(operator, machine.pk, 'admin_auth')
        grant = TerminalAccessGrant.objects.get(endpoint=machine, operator=operator)

        admin_grant = AdminTerminalAccessGrant.objects.get(endpoint=machine, operator=operator)
        admin_grant.enabled = False
        admin_grant.save(update_fields=['enabled'])
        with self.assertRaises(PermissionError):
            sessions.check_live(info['session_id'])
        admin_grant.enabled = True
        admin_grant.expires_at = timezone.now() - timedelta(seconds=1)
        admin_grant.save(update_fields=['enabled', 'expires_at'])
        with self.assertRaises(PermissionError):
            sessions.check_live(info['session_id'])

        admin_grant.expires_at = timezone.now() + timedelta(minutes=30)
        admin_grant.save(update_fields=['expires_at'])
        machine.machine_id = str(uuid.uuid4())
        machine.save(update_fields=['machine_id'])
        with self.assertRaises(PermissionError):
            sessions.check_live(info['session_id'])

    def test_admin_grant_does_not_authorize_conpty_without_conpty_grant(self):
        machine, _, operator = fixture('admin-grant-not-conpty')
        enable_admin(machine, operator)
        with self.assertRaises(PermissionError):
            sessions.create_session(operator, machine.pk, 'conpty')

    def test_admin_auth_rejects_missing_or_ambiguous_endpoint_identity(self):
        machine, _, operator = fixture('admin-auth-identity-required')
        enable_admin(machine, operator)
        machine.machine_id = ''
        machine.save(update_fields=['machine_id'])
        with self.assertRaises(PermissionError):
            sessions.create_session(operator, machine.pk, 'admin_auth')

        machine.machine_id = str(uuid.uuid4())
        machine.save(update_fields=['machine_id'])
        AgentMachine.create_with_token(hostname='SYNTHETIC-admin-auth-duplicate', machine_id=machine.machine_id)
        with self.assertRaises(PermissionError):
            sessions.create_session(operator, machine.pk, 'admin_auth')
        self.assertFalse(TerminalSession.objects.exists())

    def test_endpoint_button_respects_status_and_transport_capability(self):
        machine, _, operator = fixture('admin-auth-button')
        enable_admin(machine, operator)
        machine.status = 'online'
        machine.last_logged_user = r'CONTROL\last.user'
        machine.save(update_fields=['status', 'last_logged_user'])
        self.client.force_login(operator)
        url = reverse('endpoint-detail', args=[machine.pk])
        response = self.client.get(url)
        self.assertContains(response, 'data-terminal-open')
        self.assertNotContains(response, 'data-terminal-open disabled')
        self.assertContains(response, 'data-auth-modal')
        self.assertContains(response, 'data-windows-account')
        self.assertContains(response, 'data-windows-password')
        self.assertNotContains(response, 'name="account"')
        self.assertNotContains(response, 'name="password"')
        self.assertContains(response, 'data-auth-principal')
        self.assertContains(response, 'data-terminal-registered-user>Não identificado')
        self.assertContains(response, 'data-endpoint-uuid=')
        self.assertNotContains(response, 'data-drawer')
        self.assertNotContains(response, 'data-accounts')
        machine.status = 'offline'
        machine.save(update_fields=['status'])
        self.assertContains(self.client.get(url), 'data-terminal-open disabled')
        machine.status = 'online'
        machine.save(update_fields=['status'])
        snapshot = InventorySnapshot.objects.get(machine=machine)
        snapshot.raw_payload['agent']['capabilities'].remove('remote_terminal_transport:1')
        snapshot.save(update_fields=['raw_payload'])
        response = self.client.get(url)
        self.assertContains(response, 'data-terminal-open disabled')
        self.assertContains(response, 'Terminal indispon')

    @override_settings(NIGHTOWL_TERMINAL_BROWSER_ENABLED=True, NIGHTOWL_TERMINAL_CONPTY_ENABLED=True)
    def test_both_modes_render_without_sharing_consent_or_loading_xterm_twice(self):
        machine, _, operator = fixture('admin-and-assisted')
        enable_admin(machine, operator)
        machine.status = 'online'
        machine.save(update_fields=['status'])
        TerminalAccessGrant.objects.filter(endpoint=machine, operator=operator).update(allow_conpty=True)
        snapshot = InventorySnapshot.objects.get(machine=machine)
        snapshot.raw_payload['agent']['capabilities'].append('remote_terminal_conpty:1')
        snapshot.save(update_fields=['raw_payload'])
        self.client.force_login(operator)
        response = self.client.get(reverse('endpoint-detail', args=[machine.pk]))
        self.assertContains(response, 'data-admin-auth')
        self.assertContains(response, 'data-terminal-conpty-panel')
        self.assertContains(response, '/vendor/xterm/xterm.js', count=1)
        admin = self.client.post(reverse('terminal-session-request', args=[machine.pk]),
            {'type': 'request', 'protocol': 1, 'mode': 'admin_auth'}, content_type='application/json', secure=True)
        self.assertEqual(admin.status_code, 201)
        self.assertIsNone(TerminalSession.objects.get(pk=admin.json()['session_id']).consent_deadline)
        sessions.close_session(admin.json()['session_id'], 'test_closed')
        assisted = self.client.post(reverse('terminal-session-request', args=[machine.pk]),
            {'type': 'request', 'protocol': 1, 'mode': 'conpty'}, content_type='application/json', secure=True)
        self.assertEqual(assisted.status_code, 201)
        self.assertEqual(assisted.json()['status'], 'awaiting_consent')
        self.assertNotIn('credential', assisted.json())

    def test_registered_responsible_requires_permission_confirmed_link_and_current_record(self):
        machine, _, operator = fixture('admin-registered-user')
        enable_admin(machine, operator)
        asset = PhysicalAsset.objects.create(identity_status=PhysicalAsset.IDENTITY_CONFIRMED)
        link = AssetAgentLink.objects.create(asset=asset, endpoint=machine, endpoint_uuid_snapshot=machine.pk,
            machine_id_snapshot=machine.machine_id)
        now = timezone.now()
        for kind, when, name in (
            (AssetRecord.TYPE_RESPONSIBILITY_CHANGE, now - timedelta(days=1), 'Responsavel cadastrado'),
            (AssetRecord.TYPE_RESPONSIBILITY_CHANGE, now + timedelta(days=1), 'Mudanca futura'),
            (AssetRecord.TYPE_OBSERVATION, now, 'Observacao nao atribui responsabilidade'),
        ):
            AssetRecord.objects.create(asset=asset, record_type=kind, occurred_at=when, new_responsible=name)
        self.client.force_login(operator)
        url = reverse('endpoint-detail', args=[machine.pk])
        self.assertContains(self.client.get(url), 'data-terminal-registered-user>Não identificado')
        operator.user_permissions.add(*Permission.objects.filter(content_type__app_label='agents',
            codename__in=('view_agentmachine', 'view_physicalasset')))
        self.assertContains(self.client.get(url), 'data-terminal-registered-user>Responsavel cadastrado')
        link.machine_id_snapshot = str(uuid.uuid4())
        link.save(update_fields=['machine_id_snapshot'])
        self.assertContains(self.client.get(url), 'data-terminal-registered-user>Não identificado')

    def test_logout_closes_session_and_prevents_ticket_reuse(self):
        machine, _, operator = fixture('admin-auth-logout')
        enable_admin(machine, operator)
        info = sessions.create_session(operator, machine.pk, 'admin_auth')
        self.client.force_login(operator)
        self.client.get(reverse('logout'), secure=True)
        session = TerminalSession.objects.get(pk=info['session_id'])
        self.assertEqual(session.status, 'cancelled')
        self.assertEqual(session.reason, 'operator_logout')
        with self.assertRaises(PermissionError): sessions.consume_ticket(session.pk, 'operator', info['credential'], transport.OWNER)

    def test_ready_rejects_system_and_non_elevated_process(self):
        machine, _, operator = fixture('admin-auth-invalid-process')
        enable_admin(machine, operator)
        info = sessions.create_session(operator, machine.pk, 'admin_auth')
        identity = {'sid':'S-1-5-21-1-2-3-1001','windows_session_id':0,'integrity':12288,'elevated':True}
        for invalid in ({**identity, 'sid':'S-1-5-18'}, {**identity, 'elevated':False}, {**identity, 'integrity':8192}):
            with self.assertRaises(PermissionError): sessions.mark_ready(info['session_id'], invalid)
        ready_identity = sessions.mark_ready(info['session_id'], identity)
        self.assertEqual(ready_identity, {**identity, 'account_name': '', 'display_name': ''})

    def test_ready_uses_sid_matched_ad_display_name_and_token_account(self):
        machine, _, operator = fixture('admin-auth-display-name')
        enable_admin(machine, operator)
        info = sessions.create_session(operator, machine.pk, 'admin_auth')
        sid = 'S-1-5-21-1-2-3-1001'
        ADUser.objects.create(sid=sid, sam_account_name='admin', display_name='Windows Display Name',
            distinguished_name='CN=Admin,OU=Users,DC=control,DC=local')

        result = sessions.mark_ready(info['session_id'], {
            'sid': sid, 'windows_session_id': 3, 'integrity': 12288, 'elevated': True,
            'account_name': r'CONTROL\admin',
        })

        self.assertEqual(result['display_name'], 'Windows Display Name')
        self.assertEqual(result['account_name'], r'CONTROL\admin')
        self.assertEqual(result['sid'], sid)
        self.assertEqual(result['windows_session_id'], 3)

    def test_ready_falls_back_to_token_account_and_handles_missing_name(self):
        for index, account_name in enumerate((r'CONTROL\admin', '')):
            with self.subTest(account_name=account_name):
                machine, _, operator = fixture(f'admin-auth-account-fallback-{index}')
                enable_admin(machine, operator)
                info = sessions.create_session(operator, machine.pk, 'admin_auth')
                result = sessions.mark_ready(info['session_id'], {
                    'sid': f'S-1-5-21-1-2-3-{1100 + index}', 'windows_session_id': 3,
                    'integrity': 12288, 'elevated': True, 'account_name': account_name,
                })
                self.assertEqual(result['account_name'], account_name)
                self.assertEqual(result['display_name'], '')

    def test_ready_rejects_agent_supplied_display_name(self):
        machine, _, operator = fixture('admin-auth-untrusted-display-name')
        enable_admin(machine, operator)
        info = sessions.create_session(operator, machine.pk, 'admin_auth')
        with self.assertRaises(PermissionError):
            sessions.mark_ready(info['session_id'], {
                'sid': 'S-1-5-21-1-2-3-1001', 'windows_session_id': 3,
                'integrity': 12288, 'elevated': True, 'display_name': 'Untrusted form value',
            })

    def test_http_rejects_unauthorized_operator(self):
        machine, _, operator = fixture('admin-auth-owner')
        enable_admin(machine, operator)
        _, _, other = fixture('admin-auth-other')
        self.client.force_login(other)
        response = self.client.post(reverse('terminal-session-request', args=[machine.pk]),
            {'type':'request','protocol':1,'mode':'admin_auth'}, content_type='application/json', secure=True)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(TerminalSession.objects.exists())

    def test_grant_flag_capability_and_one_use_tickets(self):
        machine, _, operator = fixture('admin-auth-control')
        with self.assertRaises(PermissionError): sessions.create_session(operator, machine.pk, 'admin_auth')
        enable_admin(machine, operator)
        with override_settings(NIGHTOWL_TERMINAL_ADMIN_AUTH_ENABLED=False):
            with self.assertRaises(PermissionError): sessions.create_session(operator, machine.pk, 'admin_auth')
        info = sessions.create_session(operator, machine.pk, 'admin_auth')
        self.assertTrue(info['execution'])
        sessions.consume_ticket(info['session_id'], 'operator', info['credential'], transport.OWNER)
        with self.assertRaises(PermissionError): sessions.consume_ticket(info['session_id'], 'operator', info['credential'], transport.OWNER)
        TerminalSession.objects.filter(pk=info['session_id']).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(PermissionError): sessions.check_live(info['session_id'])


@override_settings(NIGHTOWL_TERMINAL_ENABLED=True, NIGHTOWL_TERMINAL_SINGLE_PROCESS=True,
    NIGHTOWL_TERMINAL_ADMIN_AUTH_ENABLED=True, NIGHTOWL_TERMINAL_TEST_LOOPBACK=True, ALLOWED_HOSTS=['testserver', '127.0.0.1'])
class AdminAuthIntegrationTests(TransactionTestCase):
    def test_auth_limits_count_windows_utf16_and_reject_invalid_surrogates(self):
        event = {'type': 'windows_auth', 'account': 'a' * 256, 'domain': 'd' * 256, 'password': chr(0x1f680) * 256}
        self.assertTrue(transport.valid_windows_auth(event))
        self.assertLessEqual(len(json.dumps(event).encode()), transport.MAX_WINDOWS_AUTH_BYTES)
        self.assertFalse(transport.valid_windows_auth({**event, 'password': event['password'] + 'a'}))
        self.assertFalse(transport.valid_windows_auth({**event, 'password': chr(0xd800)}))
        self.assertFalse(transport.valid_windows_auth({**event, 'password': 'a' + chr(0)}))

    async def test_logout_disconnects_both_peers(self):
        machine, token, operator = await sync_to_async(fixture)('admin-auth-logout-live')
        await sync_to_async(enable_admin)(machine, operator)
        info = await sync_to_async(sessions.create_session)(operator, machine.pk, 'admin_auth')
        agent_info = (await sync_to_async(Client(HTTP_AUTHORIZATION='Bearer ' + token).post)(reverse('terminal-agent-poll'),
            {'machine_id':machine.machine_id}, content_type='application/json', secure=True)).json()['session']
        client = Client()
        await sync_to_async(client.force_login)(operator)
        transport.HUB = transport.TerminalHub()
        async with LoopbackASGI() as server:
            browser = await WSClient.connect(server, info['websocket_path'], info['credential'])
            agent = await WSClient.connect(server, agent_info['websocket_path'], agent_info['credential'], origin=False)
            await browser.until('transport_ready'); await agent.until('transport_ready')
            await sync_to_async(client.get)(reverse('logout'), secure=True)
            await browser.until('closed'); await agent.until('closed')
            await browser.close(); await agent.close()

    async def test_simulated_process_identity_and_io_after_authentication(self):
        machine, token, operator = await sync_to_async(fixture)('admin-auth-ready')
        await sync_to_async(enable_admin)(machine, operator)
        info = await sync_to_async(sessions.create_session)(operator, machine.pk, 'admin_auth')
        response = await sync_to_async(Client(HTTP_AUTHORIZATION='Bearer ' + token).post)(reverse('terminal-agent-poll'),
            {'machine_id': machine.machine_id}, content_type='application/json', secure=True)
        agent_info = response.json()['session']
        transport.HUB = transport.TerminalHub()
        async with LoopbackASGI() as server:
            browser = await WSClient.connect(server, info['websocket_path'], info['credential'])
            agent = await WSClient.connect(server, agent_info['websocket_path'], agent_info['credential'], origin=False)
            await browser.until('transport_ready'); await agent.until('transport_ready')
            await browser.control({'type':'windows_auth','account':'mock','domain':'.','password':'synthetic'})
            await agent.until('windows_auth')
            identity = {'sid':'S-1-5-21-1-2-3-1001','windows_session_id':0,'integrity':12288,'elevated':True}
            await agent.control({'type':'process_ready','mode':'admin_auth','execution':True,'non_system':True,'identity':identity})
            ready_identity = (await browser.until('ready'))['identity']
            self.assertEqual(ready_identity, {**identity, 'account_name': '', 'display_name': ''})
            await agent.until('ready')
            await browser.binary(0, 1, b'synthetic-input')
            self.assertEqual((await agent.read())[9:], b'synthetic-input')
            await agent.binary(1, 1, b'synthetic-output')
            self.assertEqual((await browser.read())[9:], b'synthetic-output')
            await browser.close(); await agent.close()

    @tag('windows_e2e')
    async def test_simulated_browser_real_backend_agent_and_windows_rejection(self):
        machine, token, operator = await sync_to_async(fixture)('admin-auth-integration')
        await sync_to_async(enable_admin)(machine, operator)
        operator_client = Client()
        await sync_to_async(operator_client.force_login)(operator)
        request = await sync_to_async(operator_client.post)(reverse('terminal-session-request', args=[machine.pk]),
            {'type':'request','protocol':1,'mode':'admin_auth'}, content_type='application/json', secure=True)
        self.assertEqual(request.status_code, 201)
        info = request.json()
        client = Client(HTTP_AUTHORIZATION='Bearer ' + token)
        response = await sync_to_async(client.post)(reverse('terminal-agent-poll'), {'machine_id':machine.machine_id}, content_type='application/json', secure=True)
        agent_info = response.json()['session']
        transport.HUB = transport.TerminalHub()
        secret = ('\u00e9\U0001f680' * 150) + uuid.uuid4().hex
        root = Path(__file__).resolve().parent.parent
        dll = root / 'NightOwl.Agent.Windows.Tests' / 'bin' / 'Release' / 'net8.0-windows' / 'NightOwl.Agent.Windows.Tests.dll'
        async with LoopbackASGI() as server:
            browser = await WSClient.connect(server, info['websocket_path'], info['credential'])
            await browser.until('connected')
            process = await asyncio.create_subprocess_exec('dotnet', str(dll), '--terminal-admin-auth-agent', stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, 'NIGHTOWL_TEST_SERVER':server.origin, 'NIGHTOWL_TEST_MACHINE':machine.machine_id})
            try:
                process.stdin.write(json.dumps(agent_info).encode()); await process.stdin.drain(); process.stdin.close(); process.stdin = None
                await browser.until('transport_ready')
                self.assertGreater(len(json.dumps({'password': secret})), 1024)
                await browser.control({'type':'windows_auth','account':'nonexistent-' + uuid.uuid4().hex,'domain':'.','password':secret})
                result = await browser.until('windows_auth_result')
                self.assertEqual(result, {'type':'windows_auth_result','result':'rejected','reason':'credentials_rejected'})
                await browser.control({'type':'windows_auth','account':'x','domain':'.','password':'synthetic-replay'})
                self.assertEqual((await browser.until('closed'))['code'], 'unsupported_control')
                await browser.close()
                output, error = await asyncio.wait_for(process.communicate(), 10)
                self.assertEqual(process.returncode, 0, error.decode(errors='replace'))
                self.assertNotIn(secret.encode(), output + error)
            finally:
                if process.returncode is None: process.kill(); await process.wait()
        rows = await sync_to_async(lambda: json.dumps(list(TerminalSession.objects.values()) + list(AuditEvent.objects.values()), default=str))()
        self.assertNotIn(secret, rows)
        self.assertNotIn(json.dumps(secret)[1:-1], rows)


@override_settings(NIGHTOWL_TERMINAL_ENABLED=True, NIGHTOWL_TERMINAL_SINGLE_PROCESS=True,
    NIGHTOWL_TERMINAL_ADMIN_AUTH_ENABLED=True, NIGHTOWL_TERMINAL_TEST_LOOPBACK=True, ALLOWED_HOSTS=['testserver', '127.0.0.1', 'localhost'])
class AdminAuthVisualBrowserTests(LiveServerTestCase):
    def test_admin_terminal_responsive_visual(self):
        if os.environ.get('NIGHTOWL_TEST_BROWSER_E2E') != '1':
            self.skipTest('Explicit isolated browser gate required')
        machine, _, operator = fixture('admin-auth-visual')
        enable_admin(machine, operator)
        machine.status = AgentMachine.STATUS_ONLINE
        machine.save(update_fields=['status'])
        self.client.force_login(operator)
        endpoint_page = self.client.get(reverse('endpoint-detail', args=[machine.pk]))
        self.assertContains(endpoint_page, 'data-terminal-open')
        self.assertNotContains(endpoint_page, 'data-terminal-open disabled')
        completed = subprocess.run(
            ['node', str(Path(__file__).resolve().parent.parent / 'tests/frontend/test_terminal_admin_visual.cjs')],
            env={**os.environ, 'NIGHTOWL_E2E_SERVER': self.live_server_url,
                 'NIGHTOWL_E2E_SESSION': self.client.cookies['sessionid'].value,
                 'NIGHTOWL_E2E_ENDPOINT': str(machine.pk)}, capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertFalse(TerminalSession.objects.exists(), 'Visual preview must not create a terminal session.')
        print(completed.stdout.strip())
