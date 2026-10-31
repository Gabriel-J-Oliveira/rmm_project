import asyncio
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
        self.assertFalse(info['execution'])
        sessions.consume_ticket(info['session_id'], 'operator', info['credential'], transport.OWNER)
        with self.assertRaises(PermissionError): sessions.consume_ticket(info['session_id'], 'operator', info['credential'], transport.OWNER)
        TerminalSession.objects.filter(pk=info['session_id']).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(PermissionError): sessions.check_live(info['session_id'])


@override_settings(NIGHTOWL_TERMINAL_ENABLED=True, NIGHTOWL_TERMINAL_SINGLE_PROCESS=True,
    NIGHTOWL_TERMINAL_ADMIN_AUTH_ENABLED=True, NIGHTOWL_TERMINAL_TEST_LOOPBACK=True, ALLOWED_HOSTS=['testserver', '127.0.0.1'])
class AdminAuthIntegrationTests(TransactionTestCase):
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
