import asyncio
import hashlib
import json
import os
import statistics
import time
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AgentMachine, InventorySnapshot, AuditEvent, TerminalAccessGrant, TerminalSession
from . import terminal_sessions as sessions
from . import terminal_transport as transport
from .terminal_test_harness import LoopbackASGI, WSClient


def fixture(name='terminal'):
    machine, token = AgentMachine.create_with_token(hostname='SYNTHETIC-' + name, machine_id=str(uuid.uuid4()))
    operator = get_user_model().objects.create_user(name, is_staff=True, password='synthetic-only')
    operator.user_permissions.add(Permission.objects.get(content_type__app_label='agents', codename='open_terminalsession'))
    TerminalAccessGrant.objects.create(operator=operator, endpoint=machine, expires_at=timezone.now() + timedelta(hours=1))
    InventorySnapshot.objects.create(machine=machine, hostname=machine.hostname, collected_at=timezone.now(),
        raw_payload={'snapshot_source': 'heartbeat', 'machine_id': machine.machine_id, 'agent_version': machine.agent_version,
                     'agent': {'capabilities': ['remote_terminal_transport:1']}})
    return machine, token, operator


@override_settings(NIGHTOWL_TERMINAL_ENABLED=True, NIGHTOWL_TERMINAL_SINGLE_PROCESS=True)
class TerminalControlTests(TestCase):
    def setUp(self):
        self.machine, token, self.operator = fixture()
        self.agent = Client(HTTP_AUTHORIZATION='Bearer ' + token)
        self.client.force_login(self.operator)
        self.url = reverse('terminal-session-request', args=[self.machine.pk])

    def request(self):
        return self.client.post(self.url, {'type': 'request', 'protocol': 1}, content_type='application/json', secure=True)

    def poll(self, machine_id=None, client=None):
        return (client or self.agent).post(reverse('terminal-agent-poll'), {'machine_id': machine_id or self.machine.machine_id}, content_type='application/json', secure=True)

    def test_owner_change_between_read_and_ticket_update_is_rejected(self):
        info = self.request().json()
        digest = hashlib.sha256(info['credential'].encode()).hexdigest()
        compare = sessions.hmac.compare_digest
        def race(left, right):
            result = compare(left, right)
            if left == digest and right == digest:
                TerminalSession.objects.filter(pk=info['session_id']).update(transport_owner='other-worker')
            return result
        with patch.object(sessions.hmac, 'compare_digest', side_effect=race):
            with self.assertRaises(PermissionError):
                sessions.consume_ticket(info['session_id'], 'operator', info['credential'], transport.OWNER)

    def test_feature_default_disabled_single_process_and_tls_required(self):
        for config in ({'NIGHTOWL_TERMINAL_ENABLED': False}, {'NIGHTOWL_TERMINAL_SINGLE_PROCESS': False}):
            with override_settings(**config):
                self.assertEqual(self.request().status_code, 409)
        self.assertEqual(self.client.post(self.url, {'type': 'request', 'protocol': 1}, content_type='application/json').status_code, 403)
        self.assertFalse(TerminalSession.objects.exists())

    def test_auth_csrf_permission_and_endpoint_scope(self):
        self.assertEqual(Client().post(self.url, {}, secure=True).status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.operator)
        self.assertEqual(csrf.post(self.url, {}, secure=True).status_code, 403)
        self.assertEqual(self.agent.post(self.url, {}, secure=True).status_code, 403)
        other, _, other_user = fixture('other')
        self.client.force_login(other_user)
        self.assertEqual(self.request().status_code, 403)
        admin = get_user_model().objects.create_superuser('no-grant', password='synthetic-only')
        self.client.force_login(admin)
        self.assertEqual(self.request().status_code, 403)
        self.client.force_login(self.operator)
        self.operator.user_permissions.clear()
        self.assertEqual(self.request().status_code, 403)

    def test_ticket_hash_binding_expiry_replay_and_poll_scope(self):
        response = self.request()
        self.assertEqual(response.status_code, 201)
        self.assertIn('no-store', response['Cache-Control'])
        info = response.json()
        session = TerminalSession.objects.get()
        self.assertNotEqual(session.operator_ticket_hash, info['credential'])
        self.assertEqual(session.operator_ticket_hash, hashlib.sha256(info['credential'].encode()).hexdigest())
        for role, ticket in [('operator', 'bad' * 15), ('agent', info['credential'])]:
            with self.assertRaises(PermissionError):
                sessions.consume_ticket(session.pk, role, ticket, transport.OWNER)
        self.assertEqual(self.poll(str(uuid.uuid4())).status_code, 409)
        _, other_token, _ = fixture('another')
        self.assertIsNone(self.poll(client=Client(HTTP_AUTHORIZATION='Bearer ' + other_token), machine_id=AgentMachine.objects.get(hostname='SYNTHETIC-another').machine_id).json()['session'])
        agent_info = self.poll().json()['session']
        self.assertIsNone(self.poll().json()['session'])
        sessions.consume_ticket(session.pk, 'operator', info['credential'], transport.OWNER)
        with self.assertRaises(PermissionError):
            sessions.consume_ticket(session.pk, 'operator', info['credential'], transport.OWNER)
        with self.assertRaises(PermissionError):
            sessions.consume_ticket(session.pk, 'agent', agent_info['credential'], 'different-worker')
        TerminalSession.objects.filter(pk=session.pk).update(agent_ticket_expires=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(PermissionError):
            sessions.consume_ticket(session.pk, 'agent', agent_info['credential'], transport.OWNER)

    def test_revoked_grant_identity_expiry_and_cancel(self):
        info = self.request().json()
        session = TerminalSession.objects.get()
        TerminalAccessGrant.objects.update(enabled=False)
        with self.assertRaises(PermissionError):
            sessions.consume_ticket(session.pk, 'operator', info['credential'], transport.OWNER)
        TerminalAccessGrant.objects.update(enabled=True)
        self.machine.set_agent_token('rotated-synthetic-only')
        self.machine.save()
        with self.assertRaises(PermissionError):
            sessions.check_live(session.pk)
        TerminalSession.objects.filter(pk=session.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(PermissionError):
            sessions.check_live(session.pk)
        _, _, other = fixture('cancel-other')
        self.client.force_login(other)
        url = reverse('terminal-session-cancel', args=[session.pk])
        self.assertEqual(self.client.post(url, {'type': 'cancel'}, content_type='application/json', secure=True).status_code, 404)
        self.client.force_login(self.operator)
        self.assertEqual(self.client.post(url, {'type': 'cancel'}, content_type='application/json', secure=True).status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.status, 'cancelled')

    def test_admission_limits_and_audit_without_secrets(self):
        info = self.request().json()
        self.assertEqual(self.request().status_code, 409)
        for index in range(3):
            machine, _, operator = fixture(f'quota-{index}')
            sessions.create_session(operator, machine.pk)
        machine, _, operator = fixture('over-quota')
        with self.assertRaises(ValueError):
            sessions.create_session(operator, machine.pk)
        self.assertEqual(TerminalSession.objects.count(), 4)
        metadata = json.dumps(list(AuditEvent.objects.filter(event_type__startswith='terminal.').values('metadata', 'description')))
        self.assertNotIn(info['credential'], metadata)
        self.assertNotIn(self.machine.agent_token_hash, metadata)
        self.assertIn('terminal.authorized', list(AuditEvent.objects.values_list('event_type', flat=True)))

    def test_invalid_contract_and_capability_rejected(self):
        for data in ({'type': 'request', 'protocol': True}, {'type': 'request', 'protocol': 1, 'command': 'whoami'}):
            self.assertEqual(self.client.post(self.url, data, content_type='application/json', secure=True).status_code, 409)
        InventorySnapshot.objects.all().delete()
        self.assertEqual(self.request().status_code, 409)

    def test_credential_is_bound_to_session_and_endpoint_limit(self):
        info = self.request().json()
        other, _, operator = fixture('scope-other')
        other_info = sessions.create_session(operator, other.pk)
        with self.assertRaises(PermissionError):
            sessions.consume_ticket(other_info['session_id'], 'operator', info['credential'], transport.OWNER)
        sessions.close_session(other_info['session_id'], 'synthetic_test_closed')
        TerminalAccessGrant.objects.create(operator=operator, endpoint=self.machine, expires_at=timezone.now() + timedelta(hours=1))
        with self.assertRaises(IntegrityError):
            # DB unique constraint is the final admission barrier, including direct callers.
            sessions.create_session(operator, self.machine.pk)


@override_settings(NIGHTOWL_TERMINAL_ENABLED=True, NIGHTOWL_TERMINAL_SINGLE_PROCESS=True,
    NIGHTOWL_TERMINAL_TEST_LOOPBACK=True, ALLOWED_HOSTS=['testserver', '127.0.0.1'])
class TerminalTransportTests(TransactionTestCase):
    def setUp(self):
        self.machine, token, self.operator = fixture()
        self.info = sessions.create_session(self.operator, self.machine.pk)
        self.session_id = self.info['session_id']
        self.agent_info = Client(HTTP_AUTHORIZATION='Bearer ' + token).post(reverse('terminal-agent-poll'),
            {'machine_id': self.machine.machine_id}, content_type='application/json', secure=True).json()['session']
        transport.HUB = transport.TerminalHub()

    async def pair(self, server):
        operator = await WSClient.connect(server, self.info['websocket_path'], self.info['credential'])
        await operator.until('connected')
        agent = await WSClient.connect(server, self.agent_info['websocket_path'], self.agent_info['credential'], origin=False)
        await agent.until('ready')
        await operator.until('ready')
        return operator, agent

    async def test_loopback_bidirectional_binary_resize_close_and_no_content_audit(self):
        async with LoopbackASGI() as server:
            operator, agent = await self.pair(server)
            await operator.binary(0, 1, b'synthetic-secret-stdin')
            self.assertEqual((await agent.read())[9:], b'synthetic-secret-stdin')
            await agent.binary(1, 1, b'synthetic-secret-stdout')
            self.assertEqual((await operator.read())[9:], b'synthetic-secret-stdout')
            await agent.binary(2, 2, b'synthetic-stderr')
            self.assertEqual((await operator.read())[0], 2)
            await operator.control({'type': 'resize', 'cols': 120, 'rows': 40})
            self.assertEqual((await agent.read())['cols'], 120)
            await agent.control({'type': 'resized', 'cols': 120, 'rows': 40})
            self.assertEqual((await operator.read())['type'], 'resized')
            await operator.control({'type': 'close'})
            self.assertEqual((await agent.until('closed'))['code'], 'peer_closed')
            await operator.close(); await agent.close()
            await asyncio.sleep(.05)
        self.assertEqual(transport.HUB.connections, 0)
        self.assertFalse(transport.HUB.links)
        session = await sync_to_async(TerminalSession.objects.get)(pk=self.session_id)
        self.assertEqual(session.status, 'closed')
        metadata = await sync_to_async(lambda: json.dumps(list(AuditEvent.objects.filter(event_type__startswith='terminal.').values('metadata', 'description'))))()
        self.assertNotIn('synthetic-secret', metadata)

    async def test_invalid_expired_replayed_and_cross_role_credentials(self):
        async with LoopbackASGI() as server:
            wrong_origin = await WSClient.connect(server, self.info['websocket_path'], origin='https://evil.invalid')
            self.assertFalse(wrong_origin.accepted)
            await wrong_origin.close()
            for path, ticket in [(self.info['websocket_path'], 'x' * 43), (self.agent_info['websocket_path'], self.info['credential'])]:
                peer = await WSClient.connect(server, path, ticket)
                self.assertEqual((await peer.read())['type'], 'error')
                await peer.close()
            operator = await WSClient.connect(server, self.info['websocket_path'], self.info['credential'])
            await operator.until('connected')
            replay = await WSClient.connect(server, self.info['websocket_path'], self.info['credential'])
            self.assertEqual((await replay.read())['type'], 'error')
            await replay.close()
            await sync_to_async(TerminalSession.objects.filter(pk=self.session_id).update)(agent_ticket_expires=timezone.now() - timedelta(seconds=1))
            expired = await WSClient.connect(server, self.agent_info['websocket_path'], self.agent_info['credential'], origin=False)
            self.assertEqual((await expired.read())['type'], 'error')
            await expired.close(); await operator.close()

    async def test_peer_disconnect_closes_other_peer(self):
        async with LoopbackASGI() as server:
            operator, agent = await self.pair(server)
            await agent.close()
            self.assertEqual((await operator.until('closed'))['code'], 'disconnected')
            await operator.close()

    async def test_expiry_and_cancel_close_connected_peers(self):
        async with LoopbackASGI() as server:
            operator, agent = await self.pair(server)
            await sync_to_async(sessions.close_session)(self.session_id, 'operator_cancelled', 'cancelled')
            await operator.until('closed'); await agent.until('closed')
            await operator.close(); await agent.close()

    async def test_join_timeout(self):
        with patch.object(transport, 'JOIN_SECONDS', .01):
            async with LoopbackASGI() as server:
                operator = await WSClient.connect(server, self.info['websocket_path'], self.info['credential'])
                self.assertEqual((await operator.until('closed'))['code'], 'join_timeout')
                await operator.close()

    async def test_keepalive_timeout_and_ping_pong(self):
        with patch.object(transport, 'KEEPALIVE_SECONDS', .01), patch.object(transport, 'IDLE_SECONDS', 1.5):
            async with LoopbackASGI() as server:
                operator, agent = await self.pair(server)
                ping = await operator.until('ping')
                await operator.control({'type': 'pong', 'nonce': ping['nonce']})
                self.assertEqual((await operator.until('closed'))['code'], 'keepalive_timeout')
                await operator.close(); await agent.close()

    async def test_sequence_channel_and_frame_limits(self):
        async with LoopbackASGI() as server:
            operator, agent = await self.pair(server)
            await operator.binary(0, 2, b'synthetic')
            self.assertEqual((await operator.until('closed'))['code'], 'invalid_channel_or_sequence')
            await operator.close(); await agent.close()

    async def test_largest_frame_then_oversized_frame_closes_pair(self):
        async with LoopbackASGI() as server:
            operator, agent = await self.pair(server)
            await operator.binary(0, 1, b'x' * transport.MAX_DATA)
            self.assertEqual(len(await agent.read()), transport.MAX_FRAME)
            await operator.binary(0, 2, b'x' * (transport.MAX_DATA + 1))
            self.assertEqual((await agent.until('closed'))['code'], 'invalid_binary_frame')
            await operator.close(); await agent.close()

    async def test_wrong_binary_channel_closes_pair(self):
        async with LoopbackASGI() as server:
            operator, agent = await self.pair(server)
            await operator.binary(1, 1, b'wrong-direction')
            self.assertEqual((await agent.until('closed'))['code'], 'invalid_channel_or_sequence')
            await operator.close(); await agent.close()

    async def test_expiration_closes_live_session(self):
        async with LoopbackASGI() as server:
            operator, agent = await self.pair(server)
            await sync_to_async(TerminalSession.objects.filter(pk=self.session_id).update)(expires_at=timezone.now() - timedelta(seconds=1))
            self.assertEqual((await operator.until('closed'))['code'], 'session_expired_or_revoked')
            await operator.close(); await agent.close()

    async def test_revoked_endpoint_grant_closes_live_session(self):
        async with LoopbackASGI() as server:
            operator, agent = await self.pair(server)
            await sync_to_async(TerminalAccessGrant.objects.filter(operator=self.operator, endpoint=self.machine).update)(enabled=False)
            self.assertEqual((await operator.until('closed'))['code'], 'session_expired_or_revoked')
            await operator.close(); await agent.close()

    async def test_concurrent_replay_only_one_peer_is_accepted(self):
        async with LoopbackASGI() as server:
            peers = await asyncio.gather(*[WSClient.connect(server, self.info['websocket_path'], self.info['credential']) for _ in range(2)])
            events = await asyncio.gather(*(peer.read() for peer in peers))
            self.assertEqual(sorted(event['type'] for event in events), ['connected', 'error'])
            for peer in peers: await peer.close()

    async def test_authentication_timeout_and_tls_origin_policy(self):
        scope = {'path': self.info['websocket_path'], 'scheme': 'ws', 'client': ('127.0.0.1', 1),
            'headers': [(b'host', b'testserver'), (b'origin', b'http://testserver')], 'subprotocols': ['nightowl-terminal.v1']}
        with override_settings(NIGHTOWL_TERMINAL_TEST_LOOPBACK=False):
            self.assertFalse(transport.secure_scope(scope))
            scope['scheme'] = 'wss'
            scope['headers'][1] = (b'origin', b'https://testserver')
            self.assertTrue(transport.secure_scope(scope))
            scope['query_string'] = b'credential=forbidden'
            self.assertFalse(transport.secure_scope(scope))
        with patch.object(transport, 'AUTH_SECONDS', .05):
            async with LoopbackASGI() as server:
                peer = await WSClient.connect(server, self.info['websocket_path'])
                self.assertEqual((await peer.read())['type'], 'error')
                await peer.close()

    async def test_bounded_buffers_rate_concurrency_and_send_timeout(self):
        peer = transport.Peer('agent')
        with self.assertRaises(transport.ProtocolError):
            for _ in range(transport.MAX_MESSAGES + 1):
                peer.enqueue(event={'type': 'resize', 'rows': 1, 'cols': 1})
        peer = transport.Peer('agent')
        with self.assertRaises(transport.ProtocolError):
            for _ in range(20):
                peer.enqueue(data=b'x' * transport.MAX_FRAME)
        self.assertLessEqual(peer.buffered, transport.MAX_BUFFER)
        peer = transport.Peer('agent')
        with self.assertRaises(transport.ProtocolError):
            for _ in range(transport.MAX_RATE + 1): peer.received()
        hub = transport.TerminalHub()
        for _ in range(8): hub.reserve()
        with self.assertRaises(transport.ProtocolError): hub.reserve()
        hub = transport.TerminalHub()
        hub.opens = 16
        with self.assertRaises(transport.ProtocolError): hub.reserve()
        hub = transport.TerminalHub()
        for index in range(4): hub.attach(str(index), transport.Peer('operator'))
        with self.assertRaises(transport.ProtocolError): hub.attach('over', transport.Peer('operator'))
        peer = transport.Peer('agent')
        peer.enqueue(event={'type': 'ready'})
        async def blocked_send(message): await asyncio.Event().wait()
        with patch.object(transport, 'SEND_SECONDS', .01), self.assertRaises(transport.ProtocolError):
            await transport.writer(peer, blocked_send)

    async def test_control_db_timeout_closes_pair(self):
        async with LoopbackASGI() as server:
            operator, agent = await self.pair(server)
            async def blocked_control(*args): await asyncio.Event().wait()
            with patch.object(transport, 'CONTROL_SECONDS', .01), patch.object(transport, 'sync_to_async', return_value=blocked_control):
                self.assertEqual((await operator.until('closed'))['code'], 'control_timeout')
            await operator.close(); await agent.close()

    async def test_native_dotnet_loopback_synthetic_echo(self):
        dll = Path(settings.BASE_DIR) / 'NightOwl.Agent.Windows.Tests/bin/Release/net8.0-windows/NightOwl.Agent.Windows.Tests.dll'
        self.assertTrue(dll.exists(), 'Build affected .NET tests in Release first')
        async with LoopbackASGI() as server:
            process = await asyncio.create_subprocess_exec('dotnet', str(dll), '--terminal-transport-e2e',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, 'NIGHTOWL_TEST_SERVER': server.origin, 'NIGHTOWL_TEST_TERMINAL_INFO': json.dumps(self.agent_info)})
            operator = await WSClient.connect(server, self.info['websocket_path'], self.info['credential'])
            try:
                ready = await operator.until('ready')
                self.assertEqual(ready['mode'], 'synthetic')
                values = []
                for sequence in range(1, 21):
                    started = time.perf_counter()
                    await operator.binary(0, sequence, b'SYNTHETIC-ONLY-' + bytes([sequence]))
                    output = await operator.read()
                    values.append((time.perf_counter() - started) * 1000)
                    self.assertEqual(output[0], 1)
                    self.assertEqual(output[9:], b'SYNTHETIC-ONLY-' + bytes([sequence]))
                await operator.control({'type': 'resize', 'rows': 24, 'cols': 80})
                self.assertEqual((await operator.until('resized'))['rows'], 24)
                await operator.control({'type': 'close'})
                stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
                self.assertEqual(process.returncode, 0, 'Synthetic .NET client failed; output withheld')
                print('TERMINAL_SYNTHETIC_LOOPBACK=' + json.dumps({'messages': len(values), 'p50_ms': round(statistics.median(values), 3),
                    'p95_ms': round(sorted(values)[18], 3), 'kind': 'stdlib_test_adapter_dotnet_echo_no_process_no_tls'}))
            finally:
                await operator.close()
                if process.returncode is None:
                    process.kill()
                    await process.wait()
