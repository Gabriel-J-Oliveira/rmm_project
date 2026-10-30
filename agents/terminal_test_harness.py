"""Test-only stdlib RFC6455 loopback adapter; NOT a deployment server.

Drives the actual ASGI application and .NET ClientWebSocket without installing
infrastructure. Intentionally supports only the frames needed by these tests.
"""
import asyncio
import base64
import hashlib
import json
import secrets
import struct

from config.asgi import application

GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


async def read_frame(reader, masked):
    first, second = await reader.readexactly(2)
    if first & 0x70 or not first & 0x80 or bool(second & 0x80) != masked:
        raise ValueError('unsupported_test_frame')
    length = second & 127
    if length == 126:
        length = struct.unpack('!H', await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack('!Q', await reader.readexactly(8))[0]
    if length > 18 * 1024:
        raise ValueError('test_frame_limit')
    key = await reader.readexactly(4) if masked else None
    data = await reader.readexactly(length)
    if key:
        data = bytes(b ^ key[i % 4] for i, b in enumerate(data))
    return first & 15, data


def frame(opcode, data, masked=False):
    header = bytes([0x80 | opcode])
    flag = 0x80 if masked else 0
    header += bytes([flag | len(data)]) if len(data) < 126 else bytes([flag | 126]) + struct.pack('!H', len(data))
    if masked:
        key = secrets.token_bytes(4)
        return header + key + bytes(b ^ key[i % 4] for i, b in enumerate(data))
    return header + data


class LoopbackASGI:
    async def __aenter__(self):
        self.tasks = set()
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.origin = f'http://127.0.0.1:{self.port}'
        return self

    async def __aexit__(self, *args):
        self.server.close()
        await self.server.wait_closed()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        accepted = False
        try:
            request = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
            lines = request.split(b'\r\n')
            path = lines[0].split()[1].decode()
            headers = [tuple(part.strip() for part in line.split(b':', 1)) for line in lines[1:] if b':' in line]
            headers = [(key.lower(), value) for key, value in headers]
            header_dict = dict(headers)
            scope = {'type': 'websocket', 'asgi': {'version': '3.0'}, 'path': path.split('?', 1)[0],
                'query_string': path.split('?', 1)[1].encode() if '?' in path else b'', 'scheme': 'ws',
                'headers': headers, 'client': ('127.0.0.1', 1), 'server': ('127.0.0.1', self.port),
                'subprotocols': header_dict.get(b'sec-websocket-protocol', b'').decode().split(', ')}
            first = True

            async def receive():
                nonlocal first
                if first:
                    first = False
                    return {'type': 'websocket.connect'}
                try:
                    while True:
                        opcode, data = await read_frame(reader, True)
                        if opcode == 8:
                            return {'type': 'websocket.disconnect', 'code': 1000}
                        if opcode in (9, 10):
                            if opcode == 9:
                                writer.write(frame(10, data))
                                await writer.drain()
                            continue
                        return {'type': 'websocket.receive', **({'text': data.decode()} if opcode == 1 else {'bytes': data})}
                except (asyncio.IncompleteReadError, ConnectionError):
                    return {'type': 'websocket.disconnect', 'code': 1006}

            async def send(message):
                nonlocal accepted
                if message['type'] == 'websocket.accept':
                    key = base64.b64encode(hashlib.sha1(header_dict[b'sec-websocket-key'] + GUID.encode()).digest())
                    writer.write(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + key +
                        b'\r\nSec-WebSocket-Protocol: nightowl-terminal.v1\r\n\r\n')
                    accepted = True
                elif message['type'] == 'websocket.send':
                    writer.write(frame(2, message['bytes']) if 'bytes' in message else frame(1, message['text'].encode()))
                elif message['type'] == 'websocket.close':
                    writer.write(frame(8, struct.pack('!H', message.get('code', 1000))) if accepted else b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
                await writer.drain()

            await application(scope, receive, send)
        except (ConnectionError, asyncio.IncompleteReadError, ValueError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.tasks.discard(task)


class WSClient:
    @classmethod
    async def connect(cls, server, path, credential=None, origin=True):
        self = cls()
        self.reader, self.writer = await asyncio.open_connection('127.0.0.1', server.port)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        request = f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: nightowl-terminal.v1\r\n'
        if origin:
            request += f'Origin: {server.origin if origin is True else origin}\r\n'
        self.writer.write((request + '\r\n').encode())
        await self.writer.drain()
        response = await asyncio.wait_for(self.reader.readuntil(b'\r\n\r\n'), 3)
        self.accepted = response.startswith(b'HTTP/1.1 101')
        if self.accepted and credential is not None:
            await self.control({'type': 'authenticate', 'credential': credential})
        return self

    async def control(self, value):
        self.writer.write(frame(1, json.dumps(value).encode(), True))
        await self.writer.drain()

    async def binary(self, channel, sequence, data):
        self.writer.write(frame(2, bytes([channel]) + sequence.to_bytes(8, 'big') + data, True))
        await self.writer.drain()

    async def read(self):
        opcode, data = await asyncio.wait_for(read_frame(self.reader, False), 5)
        return data if opcode == 2 else (json.loads(data) if opcode == 1 else {'type': 'socket_closed'})

    async def until(self, kind):
        for _ in range(10):
            event = await self.read()
            if isinstance(event, dict) and event.get('type') == kind:
                return event
            if isinstance(event, dict) and event.get('type') == 'ping':
                await self.control({'type': 'pong', 'nonce': event['nonce']})
        raise AssertionError('Expected transport event not received')

    async def close(self):
        self.writer.close()
        await self.writer.wait_closed()
