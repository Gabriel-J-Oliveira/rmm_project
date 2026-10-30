"""Bounded ASGI WebSocket relay, deliberately transport-only (no process API)."""
import asyncio
import json
import re
import secrets
import time
import uuid
from urllib.parse import urlsplit

from asgiref.sync import sync_to_async
from django.conf import settings
from django.http.request import split_domain_port, validate_host

from . import terminal_sessions as control

OWNER = str(uuid.uuid4())
MAX_DATA = 16 * 1024
MAX_FRAME = MAX_DATA + 9
MAX_BUFFER = 256 * 1024
MAX_MESSAGES = 32
MAX_RATE = 100
AUTH_SECONDS = 5
JOIN_SECONDS = 15
KEEPALIVE_SECONDS = 10
IDLE_SECONDS = 25
SEND_SECONDS = 2
CONTROL_SECONDS = 2
PATH = re.compile(r'/ws/terminal/([0-9a-f-]{36})/(operator|agent)/\Z')


class ProtocolError(Exception):
    pass


class Peer:
    def __init__(self, role):
        self.role = role
        self.outgoing = asyncio.Queue(MAX_MESSAGES)
        self.buffered = 0
        self.last_received = self.last_ping = time.monotonic()
        self.ping = None
        self.sequence = 0
        self.rate_start, self.rate = time.monotonic(), 0

    def enqueue(self, *, data=None, event=None):
        message = {'type': 'websocket.send', 'bytes': data} if data is not None else {
            'type': 'websocket.send', 'text': json.dumps(event, separators=(',', ':'))}
        size = len(data) if data is not None else len(message['text'].encode())
        if self.buffered + size > MAX_BUFFER or self.outgoing.full():
            raise ProtocolError('backpressure')
        self.outgoing.put_nowait((message, size))
        self.buffered += size

    def received(self):
        now = time.monotonic()
        self.last_received = now
        if now - self.rate_start >= 1:
            self.rate_start, self.rate = now, 0
        self.rate += 1
        if self.rate > MAX_RATE:
            raise ProtocolError('message_rate_limit')


class Link:
    def __init__(self, mode='synthetic'):
        self.mode = mode
        self.peers = {}
        self.ready = asyncio.Event()
        self.transport_ready = asyncio.Event()
        self.ended = asyncio.Event()
        self.reason = 'disconnected'
        self.created = time.monotonic()

    def end(self, reason):
        if not self.ended.is_set():
            self.reason = reason
            self.ended.set()


class TerminalHub:
    def __init__(self):
        self.links = {}
        self.connections = 0
        self.loop = None
        self.open_window, self.opens = time.monotonic(), 0

    def reserve(self):
        loop = asyncio.get_running_loop()
        now = time.monotonic()
        if now - self.open_window >= 1:
            self.open_window, self.opens = now, 0
        self.opens += 1
        if self.opens > 16:
            raise ProtocolError('connection_rate_limit')
        if self.connections and self.loop is not loop:
            raise ProtocolError('single_event_loop_required')
        if self.connections >= control.MAX_SESSIONS * 2:
            raise ProtocolError('connection_limit')
        self.loop = loop
        self.connections += 1

    def attach(self, session_id, peer, mode='synthetic'):
        if session_id not in self.links:
            if len(self.links) >= control.MAX_SESSIONS:
                raise ProtocolError('session_limit')
            self.links[session_id] = Link(mode)
        link = self.links[session_id]
        if peer.role in link.peers or link.ended.is_set():
            raise ProtocolError('peer_already_connected')
        link.peers[peer.role] = peer
        return link


HUB = TerminalHub()


def secure_scope(scope):
    if scope.get('query_string'):
        return False
    headers = dict(scope.get('headers', []))
    host = headers.get(b'host', b'').decode('ascii', errors='replace')
    domain, _ = split_domain_port(host)
    if not domain or not validate_host(domain, settings.ALLOWED_HOSTS):
        return False
    if scope.get('scheme') != 'wss' and not control.loopback_allowed(scope.get('client', ('', 0))[0]):
        return False
    origin = headers.get(b'origin')
    if scope['path'].endswith('/operator/') and not origin:
        return False
    if origin:
        parsed = urlsplit(origin.decode('ascii', errors='replace'))
        if (parsed.netloc != host or parsed.scheme != ('https' if scope.get('scheme') == 'wss' else 'http') or
                parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            return False
    return 'nightowl-terminal.v1' in scope.get('subprotocols', [])


async def writer(peer, send):
    while True:
        message, size = await peer.outgoing.get()
        peer.buffered -= size
        try:
            await asyncio.wait_for(send(message), SEND_SECONDS)
        except asyncio.TimeoutError:
            raise ProtocolError('send_timeout') from None


async def reader(peer, link, receive, session_id=None):
    while True:
        message = await receive()
        if message['type'] == 'websocket.disconnect':
            raise ProtocolError('disconnected')
        if message['type'] != 'websocket.receive':
            raise ProtocolError('invalid_frame')
        peer.received()
        data = message.get('bytes')
        other = link.peers.get('agent' if peer.role == 'operator' else 'operator')
        if data is not None:
            if not link.ready.is_set() or not 9 < len(data) <= MAX_FRAME:
                raise ProtocolError('invalid_binary_frame')
            channel, sequence = data[0], int.from_bytes(data[1:9], 'big')
            if channel not in ((0,) if peer.role == 'operator' else (1, 2)) or sequence != peer.sequence + 1:
                raise ProtocolError('invalid_channel_or_sequence')
            peer.sequence = sequence
            other.enqueue(data=data)
            continue
        text = message.get('text', '')
        if len(text.encode()) > 1024:
            raise ProtocolError('control_frame_limit')
        try:
            event = json.loads(text)
        except (ValueError, TypeError):
            raise ProtocolError('invalid_control') from None
        if not isinstance(event, dict):
            raise ProtocolError('invalid_control')
        kind = event.get('type')
        if kind == 'pong' and set(event) == {'type', 'nonce'} and peer.ping and event['nonce'] == peer.ping:
            peer.ping = None
        elif kind == 'close' and set(event) == {'type'}:
            raise ProtocolError('peer_closed')
        elif kind == 'process_ready' and peer.role == 'agent' and link.mode == 'conpty' and link.transport_ready.is_set() and not link.ready.is_set():
            if (event != {'type': 'process_ready', 'mode': 'conpty', 'execution': True, 'non_system': True} or
                    event.get('execution') is not True or event.get('non_system') is not True):
                raise ProtocolError('invalid_process_confirmation')
            await asyncio.wait_for(sync_to_async(control.mark_ready)(session_id), CONTROL_SECONDS)
            link.ready.set()
            for member in link.peers.values():
                member.enqueue(event={'type': 'ready', 'mode': 'conpty', 'execution': True})
        elif kind == 'error' and peer.role == 'agent' and set(event) == {'type', 'code'} and event['code'] in (
                'identity_or_consent_required', 'conpty_start_failed', 'terminal_output_limit', 'terminal_process_exited'):
            raise ProtocolError(event['code'])
        elif (kind, peer.role) in (('resize', 'operator'), ('resized', 'agent')) and link.ready.is_set():
            if (set(event) != {'type', 'cols', 'rows'} or
                    any(type(event[k]) is not int or not 1 <= event[k] <= 500 for k in ('cols', 'rows'))):
                raise ProtocolError('invalid_resize')
            other.enqueue(event=event)
        else:
            raise ProtocolError('unsupported_control')


async def monitor(session_id, peer, link):
    while True:
        await asyncio.sleep(1)
        try:
            await asyncio.wait_for(sync_to_async(control.check_live)(session_id), CONTROL_SECONDS)
        except (PermissionError, control.TerminalSession.DoesNotExist):
            raise ProtocolError('session_expired_or_revoked') from None
        except asyncio.TimeoutError:
            raise ProtocolError('control_timeout') from None
        now = time.monotonic()
        if not link.ready.is_set() and now - link.created > JOIN_SECONDS:
            raise ProtocolError('join_timeout')
        if now - peer.last_received > IDLE_SECONDS or peer.ping and now - peer.last_ping > IDLE_SECONDS:
            raise ProtocolError('keepalive_timeout')
        if not peer.ping and now - peer.last_ping >= KEEPALIVE_SECONDS:
            peer.ping, peer.last_ping = secrets.token_hex(8), now
            peer.enqueue(event={'type': 'ping', 'nonce': peer.ping})


async def terminal_application(scope, receive, send):
    match = PATH.fullmatch(scope.get('path', ''))
    reserved = authenticated = False
    link = peer = None
    tasks = []
    session_id = role = None
    try:
        if not match or not control.enabled() or not secure_scope(scope):
            await send({'type': 'websocket.close', 'code': 4403})
            return
        session_id, role = match.groups()
        session_id = str(uuid.UUID(session_id))
        HUB.reserve()
        reserved = True
        connect = await asyncio.wait_for(receive(), AUTH_SECONDS)
        if connect['type'] != 'websocket.connect':
            raise ProtocolError('invalid_handshake')
        await asyncio.wait_for(send({'type': 'websocket.accept', 'subprotocol': 'nightowl-terminal.v1'}), SEND_SECONDS)
        message = await asyncio.wait_for(receive(), AUTH_SECONDS)
        text = message.get('text', '')
        if message['type'] != 'websocket.receive' or len(text) > 512:
            raise ProtocolError('invalid_authentication')
        auth = json.loads(text)
        if (not isinstance(auth, dict) or set(auth) != {'type', 'credential'} or auth['type'] != 'authenticate' or
                not isinstance(auth['credential'], str) or not 32 <= len(auth['credential']) <= 128):
            raise ProtocolError('invalid_authentication')
        mode = await asyncio.wait_for(sync_to_async(control.consume_ticket)(session_id, role, auth['credential'], OWNER), CONTROL_SECONDS)
        authenticated = True
        peer = Peer(role)
        link = HUB.attach(session_id, peer, mode)
        peer.enqueue(event={'type': 'connected', 'session_id': session_id, 'role': role, 'protocol': 1})
        if len(link.peers) == 2:
            link.transport_ready.set()
            if mode == 'synthetic':
                await asyncio.wait_for(sync_to_async(control.mark_ready)(session_id), CONTROL_SECONDS)
                link.ready.set()
            for member in link.peers.values():
                member.enqueue(event={'type': 'ready' if mode == 'synthetic' else 'transport_ready', 'mode': mode, 'execution': mode == 'conpty'})
        tasks = [asyncio.create_task(writer(peer, send)), asyncio.create_task(reader(peer, link, receive, session_id)),
                 asyncio.create_task(monitor(session_id, peer, link)), asyncio.create_task(link.ended.wait())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if not link.ended.is_set():
            link.end('disconnected')
    except asyncio.CancelledError:
        if link:
            link.end('server_shutdown')
        raise
    except Exception as exc:
        # Exceptions and protocol contents are intentionally never logged or echoed.
        reason = str(exc) if isinstance(exc, ProtocolError) else 'authentication_or_transport_failed'
        if link:
            link.end(reason)
        elif reserved and not authenticated:
            try:
                await asyncio.wait_for(sync_to_async(control.record_rejection)(session_id), CONTROL_SECONDS)
            except Exception:
                pass
        if not reserved:
            await send({'type': 'websocket.close', 'code': 4403})
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if authenticated:
            reason = link.reason if link else 'transport_unavailable'
            try:
                await asyncio.wait_for(sync_to_async(control.close_session)(session_id, reason,
                    'expired' if 'timeout' in reason or 'expired' in reason else 'closed'), CONTROL_SECONDS)
            except Exception:
                pass  # Socket still closes if DB is unavailable; durable expiry is unchanged.
        if link:
            link.peers.pop(role, None)
            if not link.peers:
                HUB.links.pop(session_id, None)
        if reserved:
            HUB.connections -= 1
        if reserved:
            try:
                await asyncio.wait_for(send({'type': 'websocket.send', 'text': json.dumps({
                    'type': 'closed' if authenticated else 'error',
                    'code': link.reason if link else 'connection_rejected'})}), SEND_SECONDS)
                await asyncio.wait_for(send({'type': 'websocket.close', 'code': 1000 if authenticated else 4403}), SEND_SECONDS)
            except (Exception, asyncio.CancelledError):
                pass
