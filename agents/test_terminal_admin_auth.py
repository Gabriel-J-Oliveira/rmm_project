import asyncio
from django.test import tag
import json
import os
import uuid
from pathlib import Path
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import terminal_sessions as sessions, terminal_transport as transport
from .models import AuditEvent, InventorySnapshot, TerminalAccessGrant, TerminalSession
from .terminal_test_harness import LoopbackASGI, WSClient
from .test_terminal_transport import fixture


def enable_admin(machine, operator):
    TerminalAccessGrant.objects.filter(endpoint=machine, operator=operator).update(allow_conpty=True)
    snapshot = InventorySnapshot.objects.get(machine=machine)
    snapshot.raw_payload['agent']['capabilities'].append('remote_terminal_admin_auth:1')
    snapshot.save(update_fields=['raw_payload'])


@override_settings(NIGHTOWL_TERMINAL_ENABLED=True, NIGHTOWL_TERMINAL_SINGLE_PROCESS=True, NIGHTOWL_TERMINAL_ADMIN_AUTH_ENABLED=True)
class AdminAuthControlTests(TestCase):
    def test_endpoint_button_respects_status_and_transport_capability(self):
        machine, _, operator = fixture('admin-auth-button')
        enable_admin(machine, operator)
        machine.status = 'online'
        machine.save(update_fields=['status'])
        self.client.force_login(operator)
        url = reverse('endpoint-detail', args=[machine.pk])
        response = self.client.get(url)
        self.assertContains(response, 'data-terminal-open')
        self.assertNotContains(response, 'data-terminal-open disabled')
        machine.status = 'offline'
        machine.save(update_fields=['status'])
        self.assertContains(self.client.get(url), 'data-terminal-open disabled')
        snapshot = InventorySnapshot.objects.get(machine=machine)
        snapshot.raw_payload['agent']['capabilities'].remove('remote_terminal_transport:1')
        snapshot.save(update_fields=['raw_payload'])
        self.assertNotContains(self.client.get(url), 'data-terminal-open')

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
        self.assertEqual(sessions.mark_ready(info['session_id'], identity), identity)

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
            self.assertEqual((await browser.until('ready'))['identity'], identity)
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
        secret = 'synthetic-only-' + uuid.uuid4().hex
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
                await browser.control({'type':'windows_auth','account':'nonexistent-' + uuid.uuid4().hex,'domain':'.','password':secret})
                result = await browser.until('windows_auth_result')
                self.assertEqual(result, {'type':'windows_auth_result','result':'rejected'})
                await browser.control({'type':'windows_auth','account':'x','domain':'.','password':secret})
                self.assertEqual((await browser.until('closed'))['code'], 'unsupported_control')
                await browser.close()
                output, error = await asyncio.wait_for(process.communicate(), 10)
                self.assertEqual(process.returncode, 0, error.decode(errors='replace'))
                self.assertNotIn(secret.encode(), output + error)
            finally:
                if process.returncode is None: process.kill(); await process.wait()
        rows = await sync_to_async(lambda: json.dumps(list(TerminalSession.objects.values()) + list(AuditEvent.objects.values()), default=str))()
        self.assertNotIn(secret, rows)
