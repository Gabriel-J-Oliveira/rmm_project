import asyncio
from django.test import tag
import codecs
import json
import os
import statistics
import time
from pathlib import Path

from asgiref.sync import sync_to_async
from django.conf import settings
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .models import InventorySnapshot, TerminalAccessGrant, TerminalSession
from .terminal_sessions import create_session
from .terminal_test_harness import LoopbackASGI, WSClient
from .test_terminal_transport import fixture
from . import terminal_transport as transport


def enable_conpty(machine, operator):
    TerminalAccessGrant.objects.filter(endpoint=machine, operator=operator).update(allow_conpty=True)
    heartbeat = InventorySnapshot.objects.filter(machine=machine).latest('received_at')
    heartbeat.raw_payload['agent']['capabilities'].append('remote_terminal_conpty:1')
    heartbeat.save(update_fields=['raw_payload'])


@override_settings(NIGHTOWL_TERMINAL_ENABLED=True, NIGHTOWL_TERMINAL_SINGLE_PROCESS=True, NIGHTOWL_TERMINAL_CONPTY_ENABLED=True)
class ConPtyControlTests(TestCase):
    def setUp(self):
        self.machine, self.token, self.operator = fixture('conpty')
        self.client.force_login(self.operator)
        self.url = reverse('terminal-session-request', args=[self.machine.pk])

    def request(self):
        return self.client.post(self.url, {'type': 'request', 'protocol': 1, 'mode': 'conpty'}, content_type='application/json', secure=True)

    def test_requires_real_flag_scoped_grant_and_capability(self):
        self.assertEqual(self.request().status_code, 403)
        enable_conpty(self.machine, self.operator)
        with override_settings(NIGHTOWL_TERMINAL_CONPTY_ENABLED=False):
            self.assertEqual(self.request().status_code, 403)
        heartbeat = InventorySnapshot.objects.get(machine=self.machine)
        heartbeat.raw_payload['agent']['capabilities'].remove('remote_terminal_conpty:1')
        heartbeat.save()
        self.assertEqual(self.request().status_code, 403)
        heartbeat.raw_payload['agent']['capabilities'].append('remote_terminal_conpty:1')
        heartbeat.save()
        response = self.request()
        self.assertEqual(response.status_code, 201)
        info = response.json()
        self.assertTrue(info['execution'])
        self.assertEqual(info['machine_id'], self.machine.machine_id)
        self.assertEqual(info['operator_id'], str(self.operator.pk))
        self.assertEqual(TerminalSession.objects.get().mode, 'conpty')

    def test_unknown_mode_and_synthetic_compatibility(self):
        response = self.client.post(self.url, {'type': 'request', 'protocol': 1, 'mode': 'arbitrary'}, content_type='application/json', secure=True)
        self.assertEqual(response.status_code, 409)
        session = create_session(self.operator, self.machine.pk)
        self.assertEqual(session['mode'], 'synthetic')
        self.assertFalse(session['execution'])


@override_settings(NIGHTOWL_TERMINAL_ENABLED=True, NIGHTOWL_TERMINAL_SINGLE_PROCESS=True,
    NIGHTOWL_TERMINAL_CONPTY_ENABLED=True, NIGHTOWL_TERMINAL_TEST_LOOPBACK=True, ALLOWED_HOSTS=['testserver', '127.0.0.1'])
class ConPtyTransportTests(TransactionTestCase):
    def setUp(self):
        self.machine, token, self.operator = fixture('conpty-network')
        enable_conpty(self.machine, self.operator)
        self.info = create_session(self.operator, self.machine.pk, 'conpty')
        self.agent_info = Client(HTTP_AUTHORIZATION='Bearer ' + token).post(reverse('terminal-agent-poll'),
            {'machine_id': self.machine.machine_id}, content_type='application/json', secure=True).json()['session']
        transport.HUB = transport.TerminalHub()

    async def pair(self, server):
        operator = await WSClient.connect(server, self.info['websocket_path'], self.info['credential'])
        await operator.until('connected')
        agent = await WSClient.connect(server, self.agent_info['websocket_path'], self.agent_info['credential'], origin=False)
        await operator.until('transport_ready'); await agent.until('transport_ready')
        return operator, agent

    async def test_process_confirmation_required_and_revalidated(self):
        async with LoopbackASGI() as server:
            operator, agent = await self.pair(server)
            self.assertEqual((await sync_to_async(TerminalSession.objects.get)(pk=self.info['session_id'])).status, 'connecting')
            await agent.control({'type': 'process_ready', 'mode': 'conpty', 'execution': True, 'non_system': True})
            self.assertEqual((await operator.until('ready'))['mode'], 'conpty')
            await agent.until('ready')
            await sync_to_async(TerminalAccessGrant.objects.filter(operator=self.operator).update)(allow_conpty=False)
            self.assertEqual((await operator.until('closed'))['code'], 'session_expired_or_revoked')
            await operator.close(); await agent.close()

    async def test_no_input_before_process_ready(self):
        async with LoopbackASGI() as server:
            operator, agent = await self.pair(server)
            await operator.binary(0, 1, b'not-authorized-to-execute-yet')
            self.assertEqual((await operator.until('closed'))['code'], 'invalid_binary_frame')
            await operator.close(); await agent.close()

    async def test_system_or_nonboolean_confirmation_rejected(self):
        async with LoopbackASGI() as server:
            operator, agent = await self.pair(server)
            await agent.control({'type': 'process_ready', 'mode': 'conpty', 'execution': True, 'non_system': False})
            self.assertEqual((await operator.until('closed'))['code'], 'invalid_process_confirmation')
            await operator.close(); await agent.close()

    async def test_missing_identity_or_consent_closes_both_peers(self):
        async with LoopbackASGI() as server:
            operator, agent = await self.pair(server)
            await agent.control({'type': 'error', 'code': 'identity_or_consent_required'})
            self.assertEqual((await operator.until('closed'))['code'], 'identity_or_consent_required')
            await operator.close(); await agent.close()

    async def test_operator_cannot_spoof_process_confirmation(self):
        async with LoopbackASGI() as server:
            operator, agent = await self.pair(server)
            await operator.control({'type': 'process_ready', 'mode': 'conpty', 'execution': True, 'non_system': True})
            self.assertEqual((await operator.until('closed'))['code'], 'unsupported_control')
            await operator.close(); await agent.close()

    async def test_numeric_confirmation_is_not_boolean_authorization(self):
        async with LoopbackASGI() as server:
            operator, agent = await self.pair(server)
            await agent.control({'type': 'process_ready', 'mode': 'conpty', 'execution': 1, 'non_system': 1})
            self.assertEqual((await operator.until('closed'))['code'], 'invalid_process_confirmation')
            await operator.close(); await agent.close()

    @tag('windows_e2e')
    async def test_real_powershell_stream_over_websocket_and_disconnect(self):
        dll = Path(settings.BASE_DIR) / 'NightOwl.Agent.Windows.Tests/bin/Release/net8.0-windows/NightOwl.Agent.Windows.Tests.dll'
        self.assertTrue(dll.exists())
        async with LoopbackASGI() as server:
            process = await asyncio.create_subprocess_exec('dotnet', str(dll), '--conpty-e2e', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, 'NIGHTOWL_TEST_SERVER': server.origin, 'NIGHTOWL_TEST_TERMINAL_INFO': json.dumps(self.agent_info),
                     'NIGHTOWL_E2E_SECRET_SENTINEL': 'synthetic-must-not-be-inherited'})
            operator = await WSClient.connect(server, self.info['websocket_path'], self.info['credential'])
            sequence = 0
            decoder = codecs.getincrementaldecoder('utf-8')()
            text = ''
            async def write(value):
                nonlocal sequence
                sequence += 1
                await operator.binary(0, sequence, value.encode())
            async def until_marker(marker):
                nonlocal text
                for _ in range(100):
                    try:
                        message = await operator.read()
                    except TimeoutError:
                        stage = marker if marker.startswith('NIGHTOWL_CHECK_') else 'interactive_io'
                        self.fail('Terminal output timed out at stage: ' + stage)
                    if isinstance(message, bytes):
                        part = decoder.decode(message[9:]); text = (text + part)[-65536:]
                        if '\x1b[6n' in part: await write('\x1b[1;1R')
                        for check in ('WHOAMI', 'HOSTNAME', 'IPCONFIG', 'DATE', 'ENV'):
                            if 'NIGHTOWL_CHECK_' + check + '_FAIL' in text:
                                self.fail('Native ConPTY check failed: ' + check)
                        if marker in text: return time.perf_counter()
                    elif message.get('type') == 'ping': await operator.control({'type': 'pong', 'nonce': message['nonce']})
                    else: self.assertNotEqual(message.get('type'), 'closed', 'Native session closed unexpectedly')
                self.fail('Marker not received; terminal content withheld')
            try:
                opened = time.perf_counter()
                await operator.until('ready')
                ready_ms = (time.perf_counter() - opened) * 1000
                # Exercise actual command resolution in the closed ConPTY environment.
                # Build the marker at runtime so terminal input echo cannot satisfy it.
                for command, check in (('whoami', 'WHOAMI'), ('hostname', 'HOSTNAME'), ('ipconfig', 'IPCONFIG')):
                    await write(f"& {command} | Out-Null; $ok=$? -and ($LASTEXITCODE -eq 0); if ($ok) {{$r='OK'}} else {{$r='FAIL'}}; [Console]::WriteLine('NIGHTOWL_CHECK_'+ '{check}_' + $r)\r")
                    await until_marker('NIGHTOWL_CHECK_' + check + '_OK')
                await write("[Console]::WriteLine('NIGHTOWL_CHECK_'+'DATE_START'); try {Get-Date -ErrorAction Stop | Out-Null; $r='OK'} catch {$r='FAIL'}; [Console]::WriteLine('NIGHTOWL_CHECK_'+'DATE_'+$r)\r")
                await until_marker('NIGHTOWL_CHECK_DATE_OK')
                await write("if (Test-Path Env:NIGHTOWL_E2E_SECRET_SENTINEL) {$r='FAIL'} else {$r='OK'}; [Console]::WriteLine('NIGHTOWL_CHECK_'+'ENV_'+$r)\r")
                await until_marker('NIGHTOWL_CHECK_ENV_OK')
                values = []
                echoes = []
                for index in range(100):
                    marker = f'ECHO_TRANSPORT_{index:03d}'
                    started = time.perf_counter()
                    await write(marker)
                    echoes.append(((await until_marker(marker)) - started) * 1000)
                    await write('\b' * len(marker) + '\r')
                for index in range(100):
                    marker = f'WS_READ_{index:02d}_DONE'
                    started = time.perf_counter()
                    await write(f"[Console]::WriteLine('WS_READ_'+'{index:02d}_DONE')\r")
                    values.append(((await until_marker(marker)) - started) * 1000)
                await operator.control({'type': 'resize', 'cols': 120, 'rows': 40})
                # Read output can be interleaved with controls; until() skips data.
                await operator.until('resized')
                await operator.close()  # Abrupt operator disconnect must tear down the owned native process.
                stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
                self.assertEqual(process.returncode, 0, 'Real ConPTY integration failed; output withheld')
                self.assertIn(b'PASS real ConPTY WebSocket cleanup', stdout)
                self.assertNotIn(b'WS_READ_', stdout)  # Child stdout must never inherit the service's log pipe.
                print('CONPTY_WS_LOOPBACK=' + json.dumps({'commands': len(values), 'p50_ms': round(statistics.median(values), 3),
                    'p95_ms': round(sorted(values)[94], 3), 'echo_samples': len(echoes),
                    'echo_p50_ms': round(statistics.median(echoes), 3), 'echo_p95_ms': round(sorted(echoes)[94], 3),
                    'transport_to_process_ready_ms': round(ready_ms, 3),
                    'kind': 'real_powershell_marker_with_loopback_stdlib_asgi_adapter_no_tls_no_frontend'}))
            finally:
                await operator.close()
                if process.returncode is None:
                    process.kill(); await process.wait()
                if process.returncode:
                    _, error = await process.communicate()
                    category = 'identity_rejected' if b'terminal_identity_not_authorized' in error else 'native_agent_failed'
                    print('CONPTY_E2E_FAILURE=' + json.dumps({'stage': 'agent', 'category': category, 'exit_code': process.returncode}))
