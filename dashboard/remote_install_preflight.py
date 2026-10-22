"""Ephemeral, read-only preflight for one AD computer."""

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress
import json
import os
import re
import socket
import ssl
import time
from pathlib import Path

import requests
from cryptography import x509
from django.conf import settings
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3 import poolmanager as urllib3_poolmanager
from urllib3.poolmanager import PoolManager
from urllib3.util.connection import create_connection

from access_inventory.services.ad_computer_discovery import discover_ad_computers, normalize_fqdn
from config import ad_ldap
from dashboard.ad_install_discovery import _machine_maps, _match_machine, resolve_computer_dns
from dashboard.remote_install_audit import exception_type


CHECKS = ('TARGET', 'DNS', 'REMOTE_TRANSPORT', 'AUTHENTICATION', 'TARGET_IDENTITY', 'ADMIN_PRIVILEGE',
          'WINDOWS_COMPATIBILITY', 'NIGHTOWL_ABSENCE')
WINRM_HTTPS_PORT = 5986
WINRM_HTTP_PORT = 5985
WINRM_PORT = WINRM_HTTPS_PORT
SOCKET_TIMEOUT_SECONDS = 3
ENDPOINT_TCP_TIMEOUT_SECONDS = 1.5
ENDPOINT_TCP_WORKERS = 4
MAX_DNS_IPV4_CANDIDATES = 16
WINRM_DEADLINE_SECONDS = 25
IDENTITY_VALIDATION_TIMEOUT_SECONDS = 10
MAX_OUTPUT_BYTES = 8192
_DNS_LABEL = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
_CORPORATE_NETWORKS = tuple(ipaddress.IPv4Network(network) for network in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
))
_NTLM_CHALLENGE_MISSING = 'Access denied: Server did not respond with NTLM challenge token'

_READ_ONLY_PROBE = r'''
$ErrorActionPreference = 'Stop'
$os = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$principal = [Security.Principal.WindowsPrincipal]([Security.Principal.WindowsIdentity]::GetCurrent())
$root = Join-Path $env:ProgramData 'NightOwl'
$agentInstall = Join-Path $root 'AgentDotNet'
$agentExe = Join-Path $agentInstall 'NightOwl.Agent.Windows.exe'
$cs = Get-CimInstance -ClassName Win32_ComputerSystem
$dnsDomain = [string](Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters').Domain
$computerSid = $null
try {
    $account = [Security.Principal.NTAccount]::new([string]$cs.Domain, ([string]$env:COMPUTERNAME + '$'))
    $computerSid = $account.Translate([Security.Principal.SecurityIdentifier]).Value
} catch { }
$identityDomain = if ($dnsDomain) { $dnsDomain } else { [string]$cs.Domain }
[pscustomobject]@{
    hostname = [string]$env:COMPUTERNAME
    windows_name = [string]$os.ProductName
    build = [int]$os.CurrentBuildNumber
    architecture = $(if ([Environment]::Is64BitOperatingSystem) { 'x64' } else { 'x86' })
    powershell_major = [int]$PSVersionTable.PSVersion.Major
    admin = [bool]$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    nightowl_service_present = [bool](Get-Service -Name 'NightOwlAgentDotNet' -ErrorAction SilentlyContinue)
    # This field represents installed executable evidence, not a partial directory.
    nightowl_directory_present = [bool](Test-Path -LiteralPath $agentExe -PathType Leaf)
    part_of_domain = [bool]$cs.PartOfDomain
    domain = [string]$identityDomain
    computer_sid = [string]$computerSid
} | ConvertTo-Json -Compress
'''


class ProbeFailure(Exception):
    def __init__(self, code, *, stage='', exception=''):
        self.code = code
        self.stage = stage
        self.exception_type = exception
        super().__init__(code)


class _PinnedAddressMixin:
    """Connect to a selected address while retaining the URL host for Host/SNI/verification."""

    def _new_conn(self):
        address = getattr(self, '_pinned_address', None)
        if not address:
            return super()._new_conn()
        return create_connection((address, self.port), self.timeout,
                                 source_address=self.source_address, socket_options=self.socket_options)


class _PinnedHTTPConnection(_PinnedAddressMixin, HTTPConnection):
    pass


class _PinnedHTTPSConnection(_PinnedAddressMixin, HTTPSConnection):
    pass


class _PinnedHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _PinnedHTTPConnection

    def _new_conn(self):
        connection = super()._new_conn()
        connection._pinned_address = self._pinned_address
        return connection


class _PinnedHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _PinnedHTTPSConnection

    def _new_conn(self):
        connection = super()._new_conn()
        connection._pinned_address = self._pinned_address
        return connection


class _PinnedPoolManager(PoolManager):
    def __init__(self, *args, pinned_address, **kwargs):
        self.pinned_address = pinned_address
        super().__init__(*args, **kwargs)
        # urllib3 initializes this instance attribute from its global mapping,
        # overriding any class-level mapping supplied by a subclass.
        self.pool_classes_by_scheme = dict(urllib3_poolmanager.pool_classes_by_scheme)
        self.pool_classes_by_scheme.update(http=_PinnedHTTPConnectionPool, https=_PinnedHTTPSConnectionPool)

    def _new_pool(self, scheme, host, port, request_context=None):
        pool = super()._new_pool(scheme, host, port, request_context)
        pool._pinned_address = self.pinned_address
        return pool


class _PinnedAddressAdapter(requests.adapters.HTTPAdapter):
    def __init__(self, address):
        self.address = address
        super().__init__()

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        self._pool_connections = connections
        self._pool_maxsize = maxsize
        self._pool_block = block
        self.poolmanager = _PinnedPoolManager(
            num_pools=connections, maxsize=maxsize, block=block,
            pinned_address=self.address, **pool_kwargs,
        )


def _result(checks, target=None, windows=None):
    return {
        'status': 'READY' if all(checks[key]['status'] == 'PASS' for key in CHECKS) else 'NOT_READY',
        'checks': checks, 'target': target, 'windows': windows,
    }


def _fail(checks, key, code, target=None, *, failure=None):
    checks[key] = {'status': 'FAIL', 'code': code}
    if failure and (failure.stage or failure.exception_type):
        checks[key].update(stage=failure.stage or key, exception_type=failure.exception_type)
    return _result(checks, target=target)


def _valid_fqdn(value):
    fqdn = normalize_fqdn(value)
    labels = fqdn.split('.')
    return fqdn if len(fqdn) <= 253 and len(labels) >= 3 and all(_DNS_LABEL.fullmatch(label) for label in labels) else ''


def _valid_sid(value):
    return value.upper() if isinstance(value, str) and len(value) <= 184 and re.fullmatch(r'S-1-(?:\d+-)+\d+', value, re.I) else ''


def _ad_identity_matches(computer, fqdn, dn, sid='', *, require_sid=False):
    if normalize_fqdn(computer.get('fqdn')) != normalize_fqdn(fqdn) or computer.get('distinguished_name') != dn:
        return False
    if not (require_sid or sid):
        return True
    expected = _valid_sid(sid)
    return bool(expected) and _valid_sid(computer.get('sid')) == expected


def _ad_target(value):
    fqdn = _valid_fqdn(value)
    domain = normalize_fqdn(ad_ldap.ad_config().get('DOMAIN'))
    if not fqdn or not domain or not fqdn.endswith(f'.{domain}'):
        raise ProbeFailure('INVALID_TARGET')
    try:
        matches = [item for item in discover_ad_computers() if normalize_fqdn(item.get('fqdn')) == fqdn]
    except Exception as exc:
        raise ProbeFailure('AD_DISCOVERY_UNAVAILABLE', stage='TARGET', exception=exception_type(exc)) from None
    if len(matches) != 1 or not _valid_fqdn(matches[0].get('fqdn')):
        raise ProbeFailure('TARGET_NOT_UNIQUE_OR_MISSING')
    computer = matches[0]
    if computer.get('enabled') is not True:
        raise ProbeFailure('AD_COMPUTER_DISABLED')
    try:
        _, correlation, _ = _match_machine(computer, _machine_maps())
    except Exception as exc:
        raise ProbeFailure('CORRELATION_UNAVAILABLE', stage='TARGET', exception=exception_type(exc)) from None
    if correlation != 'UNMANAGED':
        raise ProbeFailure('TARGET_MANAGED_OR_CONFLICT')
    return computer


_HTTPS_TRANSPORT = {'scheme': 'https', 'port': WINRM_HTTPS_PORT,
                    'message_encryption': 'auto', 'label': 'HTTPS_TLS_VALIDATED'}
_HTTP_TRANSPORT = {'scheme': 'http', 'port': WINRM_HTTP_PORT,
                   'message_encryption': 'always', 'label': 'HTTP_NTLM_ENCRYPTED'}
_CONNECTIVITY_FAILURES = {'WINRM_CONNECTION_FAILED', 'WINRM_REQUEST_TIMEOUT', 'TLS_HANDSHAKE_FAILED'}


def _winrm_http_authorized(fqdn):
    """Return exact FQDN authorization; malformed enabled policy fails closed."""
    if getattr(settings, 'REMOTE_INSTALL_WINRM_HTTP_ENABLED', False) is not True:
        return False
    configured = getattr(settings, 'REMOTE_INSTALL_WINRM_HTTP_ALLOWLIST', [])
    if isinstance(configured, str):
        configured = configured.split(',')
    if not isinstance(configured, (list, tuple, set, frozenset)):
        raise ProbeFailure('WINRM_HTTP_CONFIG_INVALID', stage='REMOTE_TRANSPORT')
    allowed = set()
    for candidate in configured:
        if not isinstance(candidate, str) or not candidate.strip():
            raise ProbeFailure('WINRM_HTTP_CONFIG_INVALID', stage='REMOTE_TRANSPORT')
        normalized = _valid_fqdn(candidate)
        if not normalized:
            raise ProbeFailure('WINRM_HTTP_CONFIG_INVALID', stage='REMOTE_TRANSPORT')
        allowed.add(normalized)
    return normalize_fqdn(fqdn) in allowed


def _winrm_transport_for(fqdn):
    """Return the secure default; HTTP can only be selected after an explicit fallback decision."""
    _winrm_http_authorized(fqdn)  # Validate enabled policy even when HTTPS is expected to work.
    return dict(_HTTPS_TRANSPORT)


def _winrm_transport_candidates(fqdn):
    candidates = [dict(_HTTPS_TRANSPORT)]
    if _winrm_http_authorized(fqdn):
        candidates.append(dict(_HTTP_TRANSPORT))
    return candidates


def _transport_selection(fqdn, label, port):
    """Revalidate the preflight's server-side selection before reusing it for installation."""
    for transport in _winrm_transport_candidates(fqdn):
        if transport['label'] == label and transport['port'] == port:
            return transport
    raise ProbeFailure('WINRM_PORT_NOT_ALLOWED', stage='REMOTE_TRANSPORT')


def _tcp_available(address, port=WINRM_HTTPS_PORT, *, timeout=SOCKET_TIMEOUT_SECONDS):
    if port not in (WINRM_HTTP_PORT, WINRM_HTTPS_PORT):
        raise ProbeFailure('WINRM_PORT_NOT_ALLOWED', stage='REMOTE_TRANSPORT')
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except OSError as exc:
        prefix = f'TCP_{port}'
        code = (f'{prefix}_TIMEOUT' if isinstance(exc, TimeoutError) else
                f'{prefix}_REFUSED' if isinstance(exc, ConnectionRefusedError) else f'{prefix}_UNAVAILABLE')
        raise ProbeFailure(code, stage=prefix, exception=exception_type(exc)) from None


def _safe_ipv4(value):
    if not isinstance(value, str):
        return False
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return False
    if (address.is_loopback or address.is_unspecified or address.is_link_local
            or address.is_multicast or address.is_reserved or address == ipaddress.IPv4Address('255.255.255.255')):
        return False
    return address.is_global or any(address in network for network in _CORPORATE_NETWORKS)


def _tcp_candidate_results(addresses, port):
    """Probe every bounded A-record candidate concurrently and return deterministic results."""
    results = {}
    if not addresses:
        return results

    def probe(address):
        try:
            available = _tcp_available(address, port, timeout=ENDPOINT_TCP_TIMEOUT_SECONDS)
            if available:
                return address, {'tcp_status': 'OPEN', 'code': None}
            return address, {'tcp_status': 'FAIL', 'code': f'TCP_{port}_UNAVAILABLE'}
        except ProbeFailure as exc:
            return address, {'tcp_status': 'FAIL', 'code': exc.code,
                             'exception_type': exc.exception_type}
        except Exception as exc:
            return address, {'tcp_status': 'FAIL', 'code': f'TCP_{port}_UNAVAILABLE',
                             'exception_type': exception_type(exc)}

    with ThreadPoolExecutor(max_workers=min(ENDPOINT_TCP_WORKERS, len(addresses))) as executor:
        futures = [executor.submit(probe, address) for address in addresses]
        for future in as_completed(futures):
            address, result = future.result()
            results[address] = result
    return {address: results[address] for address in addresses}


def _remote_identity_failure(remote, computer):
    expected_fqdn = normalize_fqdn(computer.get('fqdn'))
    expected_hostname = str(computer.get('hostname') or expected_fqdn.split('.', 1)[0]).strip().casefold()
    expected_domain = expected_fqdn.partition('.')[2]
    expected_sid = _valid_sid(computer.get('sid'))
    if not expected_fqdn or not expected_sid:
        return 'TARGET_IDENTITY_UNVERIFIABLE'
    hostname = str(remote.get('hostname') or '').strip().casefold()
    domain = normalize_fqdn(remote.get('domain'))
    remote_sid = _valid_sid(remote.get('computer_sid'))
    if (remote.get('part_of_domain') is not True or hostname != expected_hostname or
            domain != expected_domain):
        return 'REMOTE_IDENTITY_MISMATCH'
    if not remote_sid:
        return 'REMOTE_IDENTITY_UNVERIFIABLE'
    return '' if remote_sid == expected_sid else 'REMOTE_IDENTITY_MISMATCH'


def _block_redirects(session):
    send = session.send

    def send_without_redirects(request, **kwargs):
        kwargs['allow_redirects'] = False
        response = send(request, **kwargs)
        if 300 <= response.status_code < 400:
            response.close()
            raise ProbeFailure('WINRM_REDIRECT_BLOCKED', stage='WINRM_SESSION')
        return response

    session.send = send_without_redirects


def _build_guarded_winrm_session(protocol, pinned_address=None):
    transport = protocol.transport
    setup_encryption = transport.setup_encryption
    build_session = transport.build_session

    def build_session_with_pin():
        session = build_session()
        if pinned_address:
            # Keep the FQDN in the request URL for Host, TLS SNI and certificate
            # hostname validation; only the socket destination is pinned.
            session.trust_env = False
            adapter = _PinnedAddressAdapter(pinned_address)
            session.mount('http://', adapter)
            session.mount('https://', adapter)
        return session

    def setup_encryption_without_redirects(session):
        _block_redirects(session)
        return setup_encryption(session)

    # pywinrm may perform an NTLM exchange inside build_session() when HTTP
    # message encryption is forced. Install the redirect guard before that call.
    transport.setup_encryption = setup_encryption_without_redirects
    transport.build_session = build_session_with_pin
    try:
        session = transport.build_session()
    finally:
        del transport.setup_encryption
        del transport.build_session
    _block_redirects(session)
    return session


def _winrm_ca_trust_path():
    configured = getattr(settings, 'WINRM_CA_TRUST_PATH', '')
    if not isinstance(configured, str) or not configured.strip():
        raise ProbeFailure('WINRM_CA_TRUST_INVALID')
    path = Path(configured.strip())
    try:
        if not path.is_absolute() or not path.is_file():
            raise OSError()
        with path.open('rb') as bundle:
            contents = bundle.read()
            certificates = re.findall(rb'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', contents, re.S)
            remainder = re.sub(rb'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', b'', contents, flags=re.S)
            remainder = re.sub(rb'(?m)^[ \t]*#[^\r\n]*', b'', remainder)
            if not certificates or remainder.strip():
                raise OSError()
            for pem in certificates:
                certificate = x509.load_pem_x509_certificate(pem)
                constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
                if not constraints.ca:
                    raise ValueError()
        # Unused expired roots do not invalidate a trust store. TLS validates
        # the validity of the actual server certificate and its chosen chain.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=str(path))
        if not context.get_ca_certs():
            raise ValueError()
    except (OSError, ssl.SSLError, ValueError, x509.ExtensionNotFound) as exc:
        raise ProbeFailure('WINRM_CA_TRUST_INVALID', stage='CA_TRUST', exception=exception_type(exc)) from None
    return os.path.realpath(path)


def _new_winrm_protocol(fqdn, username, password, *, pinned_address=None, transport=None):
    normalized_fqdn = _valid_fqdn(fqdn)
    if not normalized_fqdn:
        raise ProbeFailure('INVALID_TARGET')
    if pinned_address is not None and not _safe_ipv4(pinned_address):
        raise ProbeFailure('UNSAFE_TARGET_ADDRESS', stage='DNS')
    if transport is None:
        transport = _winrm_transport_for(normalized_fqdn)
    if transport not in _winrm_transport_candidates(normalized_fqdn):
        raise ProbeFailure('WINRM_PORT_NOT_ALLOWED', stage='REMOTE_TRANSPORT')
    ca_trust_path = _winrm_ca_trust_path() if transport['scheme'] == 'https' else None
    from winrm.protocol import Protocol

    protocol = Protocol(
        endpoint=f"{transport['scheme']}://{normalized_fqdn}:{transport['port']}/wsman", transport='ntlm',
        username=username, password=password,
        message_encryption=transport['message_encryption'],
        server_cert_validation='validate',
        ca_trust_path=ca_trust_path,
        proxy=None, operation_timeout_sec=3, read_timeout_sec=5,
    )
    _build_guarded_winrm_session(protocol, pinned_address)
    return protocol


def _winrm_failure(exc, stage):
    from winrm.exceptions import AuthenticationError, WinRMTransportError
    if isinstance(exc, ProbeFailure):
        exc.stage = exc.stage or stage
        return exc
    if isinstance(exc, AuthenticationError):
        code = 'AUTHENTICATION_FAILED'
    elif isinstance(exc, WinRMTransportError):
        code = ('AUTHENTICATION_FAILED' if exc.code == 401 else
                'WINRM_AUTHORIZATION_DENIED' if exc.code == 403 else 'WINRM_HTTP_ERROR')
    elif isinstance(exc, (requests.exceptions.SSLError, ssl.SSLError)):
        text = str(exc).casefold()
        verification = isinstance(exc, ssl.SSLCertVerificationError) or any(value in text for value in (
            'certificate_verify_failed', 'certificate verify failed', 'hostname mismatch', 'untrusted certificate'))
        code = 'TLS_VALIDATION_FAILED' if verification else 'TLS_HANDSHAKE_FAILED'
    elif isinstance(exc, (requests.exceptions.Timeout, TimeoutError)):
        code = 'WINRM_REQUEST_TIMEOUT'
    elif isinstance(exc, requests.exceptions.ConnectionError):
        code = 'WINRM_CONNECTION_FAILED'
    elif isinstance(exc, PermissionError) and str(exc).startswith(_NTLM_CHALLENGE_MISSING):
        code = 'AUTH_NEGOTIATION_FAILED'
    else:
        code = 'WINRM_PROTOCOL_ERROR'
    return ProbeFailure(code, stage=stage, exception=exception_type(exc))


def _winrm_probe(fqdn, username, password, *, pinned_address=None, expected_computer=None,
                 deadline=None, transport=None):
    try:
        from winrm.exceptions import WinRMOperationTimeoutError
    except ImportError:
        raise ProbeFailure('WINRM_CLIENT_UNAVAILABLE') from None

    protocol = None
    shell_id = None
    command_id = None
    stage = 'WINRM_SESSION'
    try:
        protocol = _new_winrm_protocol(fqdn, username, password, pinned_address=pinned_address,
                                       transport=transport)
        deadline = min(deadline, time.monotonic() + WINRM_DEADLINE_SECONDS) if deadline else time.monotonic() + WINRM_DEADLINE_SECONDS
        stage = 'WINRM_SESSION'
        shell_id = protocol.open_shell()
        encoded = base64.b64encode(_READ_ONLY_PROBE.encode('utf-16-le')).decode('ascii')
        stage = 'REMOTE_EXECUTION'
        command_id = protocol.run_command(shell_id, 'powershell.exe',
                                          ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded])
        stdout = bytearray()
        stage = 'REMOTE_OUTPUT'
        while time.monotonic() < deadline:
            try:
                out, _err, exit_code, done = protocol.get_command_output_raw(shell_id, command_id)
            except WinRMOperationTimeoutError:
                continue
            if len(stdout) + len(out) > MAX_OUTPUT_BYTES:
                raise ProbeFailure('REMOTE_OUTPUT_INVALID')
            stdout.extend(out)
            if done:
                if exit_code != 0:
                    raise ProbeFailure('REMOTE_PROBE_FAILED')
                try:
                    data = json.loads(stdout.decode('utf-8-sig'))
                except (UnicodeError, ValueError):
                    raise ProbeFailure('REMOTE_OUTPUT_INVALID') from None
                if not isinstance(data, dict):
                    raise ProbeFailure('REMOTE_OUTPUT_INVALID')
                if expected_computer is not None:
                    stage = 'IDENTITY_VALIDATION'
                    identity_failure = _remote_identity_failure(data, expected_computer)
                    if identity_failure:
                        raise ProbeFailure(identity_failure, stage=stage)
                return data
        raise ProbeFailure('REMOTE_TIMEOUT')
    except Exception as exc:
        raise _winrm_failure(exc, stage) from None
    finally:
        if protocol and shell_id:
            if command_id:
                try:
                    protocol.cleanup_command(shell_id, command_id)
                except Exception:
                    pass
            try:
                protocol.close_shell(shell_id)
            except Exception:
                pass


def _verify_remote_identity_session(protocol, shell_id, computer):
    """Reconfirm AD identity on the exact pinned WinRM session before dispatch."""
    try:
        from winrm.exceptions import WinRMOperationTimeoutError
    except ImportError:
        raise ProbeFailure('WINRM_CLIENT_UNAVAILABLE', stage='IDENTITY_VALIDATION') from None
    command_id = None
    try:
        encoded = base64.b64encode(_READ_ONLY_PROBE.encode('utf-16-le')).decode('ascii')
        command_id = protocol.run_command(shell_id, 'powershell.exe',
            ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded])
        if not command_id:
            raise ProbeFailure('REMOTE_PROBE_FAILED', stage='IDENTITY_VALIDATION')
        deadline = time.monotonic() + IDENTITY_VALIDATION_TIMEOUT_SECONDS
        output = bytearray()
        while time.monotonic() < deadline:
            try:
                chunk, _stderr, exit_code, done = protocol.get_command_output_raw(shell_id, command_id)
            except WinRMOperationTimeoutError:
                continue
            if len(output) + len(chunk) > MAX_OUTPUT_BYTES:
                raise ProbeFailure('REMOTE_OUTPUT_INVALID', stage='IDENTITY_VALIDATION')
            output.extend(chunk)
            if done:
                if exit_code != 0:
                    raise ProbeFailure('REMOTE_PROBE_FAILED', stage='IDENTITY_VALIDATION')
                try:
                    remote = json.loads(output.decode('utf-8-sig'))
                except (UnicodeError, ValueError):
                    raise ProbeFailure('REMOTE_OUTPUT_INVALID', stage='IDENTITY_VALIDATION') from None
                if not isinstance(remote, dict):
                    raise ProbeFailure('REMOTE_OUTPUT_INVALID', stage='IDENTITY_VALIDATION')
                code = _remote_identity_failure(remote, computer)
                if code:
                    raise ProbeFailure(code, stage='IDENTITY_VALIDATION')
                return remote
        raise ProbeFailure('REMOTE_TIMEOUT', stage='IDENTITY_VALIDATION')
    except ProbeFailure:
        raise
    except Exception as exc:
        raise _winrm_failure(exc, 'IDENTITY_VALIDATION') from None
    finally:
        if command_id:
            try:
                protocol.cleanup_command(shell_id, command_id)
            except Exception:
                pass


def run_remote_install_preflight(fqdn, username, password):
    checks = {name: {'status': 'SKIPPED'} for name in CHECKS}
    try:
        computer = _ad_target(fqdn)
    except ProbeFailure as exc:
        return _fail(checks, 'TARGET', exc.code, failure=exc)
    checks['TARGET'] = {'status': 'PASS'}
    target = {'hostname': str(computer.get('hostname') or '')[:120],
              'fqdn': normalize_fqdn(computer['fqdn']),
              'operating_system': str(computer.get('operating_system') or '')[:120]}
    try:
        transports = _winrm_transport_candidates(target['fqdn'])
    except ProbeFailure as exc:
        return _fail(checks, 'REMOTE_TRANSPORT', exc.code, target, failure=exc)
    target['winrm_transport'] = transports[0]['label']
    target['winrm_port'] = transports[0]['port']
    if not _valid_sid(computer.get('sid')):
        target['endpoint_attempts'] = []
        return _fail(checks, 'TARGET_IDENTITY', 'TARGET_IDENTITY_UNVERIFIABLE', target)
    try:
        dns = resolve_computer_dns([computer]).get(target['fqdn'], {})
    except Exception as exc:
        failure = ProbeFailure('DNS_UNRESOLVED', stage='DNS', exception=exception_type(exc))
        return _fail(checks, 'DNS', failure.code, target, failure=failure)
    addresses = dns.get('ipv4_addresses', [])
    if dns.get('dns_status') != 'RESOLVED' or not isinstance(addresses, (list, tuple)) or not addresses:
        return _fail(checks, 'DNS', 'DNS_UNRESOLVED', target)
    if len(addresses) > MAX_DNS_IPV4_CANDIDATES:
        return _fail(checks, 'DNS', 'DNS_ANSWER_LIMIT', target)
    if not all(_safe_ipv4(item) for item in addresses):
        return _fail(checks, 'DNS', 'UNSAFE_TARGET_ADDRESS', target)
    addresses = sorted(set(addresses), key=ipaddress.IPv4Address)
    target['ipv4_addresses'] = addresses
    target['endpoint_attempts'] = []
    target['transport_attempts'] = []
    checks['DNS'] = {'status': 'PASS'}
    if transports[0]['scheme'] == 'https':
        try:
            _winrm_ca_trust_path()
        except ProbeFailure as exc:
            return _fail(checks, 'REMOTE_TRANSPORT', exc.code, target, failure=exc)
    if not isinstance(username, str) or not username.strip() or len(username) > 256 or not username.isprintable() or \
            not isinstance(password, str) or not password or len(password) > 512 or not password.isprintable():
        return _fail(checks, 'AUTHENTICATION', 'CREDENTIAL_REQUIRED', target)
    deadline = time.monotonic() + WINRM_DEADLINE_SECONDS
    remote = None
    last_failure = None
    selected_transport = None
    for transport_index, transport in enumerate(transports):
        if time.monotonic() >= deadline:
            break
        target['winrm_transport'] = transport['label']
        target['winrm_port'] = transport['port']
        tcp_results = _tcp_candidate_results(addresses, transport['port'])
        transport_endpoints = [
            {'ip': address, **tcp_results[address], 'transport': transport['label'],
             'port': transport['port'],
             'status': 'TCP_OPEN' if tcp_results[address]['tcp_status'] == 'OPEN' else 'TCP_FAILED'}
            for address in addresses
        ]
        target['transport_attempts'].append({'transport': transport['label'], 'port': transport['port'],
                                             'endpoint_attempts': transport_endpoints})
        candidates = [entry['ip'] for entry in transport_endpoints if entry['tcp_status'] == 'OPEN']
        if not candidates:
            target['endpoint_attempts'] = transport_endpoints
            codes = [entry.get('code') for entry in transport_endpoints if entry.get('code')]
            last_code = next((candidate for candidate in (
                f'TCP_{transport["port"]}_TIMEOUT', f'TCP_{transport["port"]}_REFUSED',
                f'TCP_{transport["port"]}_UNAVAILABLE') if candidate in codes),
                f'TCP_{transport["port"]}_UNAVAILABLE')
            last_failure = ProbeFailure(last_code, stage=f'TCP_{transport["port"]}')
            # HTTP is considered only after every HTTPS address failed at TCP.
            continue

        target['endpoint_attempts'] = transport_endpoints
        failures = []
        for address in candidates:
            try:
                candidate_remote = _winrm_probe(target['fqdn'], username.strip(), password,
                    pinned_address=address, expected_computer=computer, deadline=deadline,
                    transport=transport)
                identity_failure = _remote_identity_failure(candidate_remote, computer)
                if identity_failure:
                    raise ProbeFailure(identity_failure, stage='IDENTITY_VALIDATION')
                remote = candidate_remote
                selected_transport = transport
                target['selected_ip'] = address
                target['ip'] = address
                target['endpoint_attempts'][addresses.index(address)]['status'] = 'SELECTED'
                break
            except ProbeFailure as exc:
                last_failure = exc
                failures.append(exc)
                entry = target['endpoint_attempts'][addresses.index(address)]
                entry.update(status='IDENTITY_MISMATCH' if exc.code == 'REMOTE_IDENTITY_MISMATCH' else
                             'IDENTITY_UNVERIFIABLE' if exc.code in ('REMOTE_IDENTITY_UNVERIFIABLE',
                                                                     'TARGET_IDENTITY_UNVERIFIABLE') else 'WINRM_FAILED',
                             code=exc.code, stage=exc.stage, exception_type=exc.exception_type)
                if exc.code in ('AUTHENTICATION_FAILED', 'AUTH_NEGOTIATION_FAILED',
                                 'WINRM_AUTHORIZATION_DENIED', 'CREDENTIAL_REQUIRED'):
                    break
                if time.monotonic() >= deadline:
                    break
            if remote is not None:
                break
        if remote is not None:
            break

        identity_or_auth_failure = any(failure.code in (
            'REMOTE_IDENTITY_MISMATCH', 'REMOTE_IDENTITY_UNVERIFIABLE', 'TARGET_IDENTITY_UNVERIFIABLE',
            'AUTHENTICATION_FAILED', 'AUTH_NEGOTIATION_FAILED', 'WINRM_AUTHORIZATION_DENIED',
            'CREDENTIAL_REQUIRED', 'TLS_VALIDATION_FAILED') for failure in failures)
        connectivity_only = bool(failures) and all(
            failure.code in _CONNECTIVITY_FAILURES for failure in failures)
        if transport_index + 1 >= len(transports) or identity_or_auth_failure or not connectivity_only:
            break

    if selected_transport:
        target['winrm_transport'] = selected_transport['label']
        target['winrm_port'] = selected_transport['port']
    if remote is None:
        observed_identity_mismatch = any(entry.get('status') == 'IDENTITY_MISMATCH'
                                         for entry in target['endpoint_attempts'])
        observed_identity_failure = observed_identity_mismatch or any(
            entry.get('status') == 'IDENTITY_UNVERIFIABLE' for entry in target['endpoint_attempts'])
        if observed_identity_failure:
            checks['REMOTE_TRANSPORT'] = {'status': 'PASS'}
            checks['AUTHENTICATION'] = {'status': 'PASS'}
            identity_code = ('REMOTE_IDENTITY_MISMATCH' if observed_identity_mismatch
                             else 'REMOTE_IDENTITY_UNVERIFIABLE')
            return _fail(checks, 'TARGET_IDENTITY', identity_code, target, failure=last_failure)
        if last_failure:
            if last_failure.code in ('AUTHENTICATION_FAILED', 'AUTH_NEGOTIATION_FAILED',
                                     'WINRM_AUTHORIZATION_DENIED'):
                checks['REMOTE_TRANSPORT'] = {'status': 'PASS'}
            key = 'AUTHENTICATION' if last_failure.code in (
                'AUTHENTICATION_FAILED', 'AUTH_NEGOTIATION_FAILED', 'CREDENTIAL_REQUIRED') else 'REMOTE_TRANSPORT'
            if last_failure.code == 'WINRM_AUTHORIZATION_DENIED':
                key = 'ADMIN_PRIVILEGE'
            return _fail(checks, key, last_failure.code, target, failure=last_failure)
        return _fail(checks, 'REMOTE_TRANSPORT',
                     last_failure.code if last_failure else 'WINRM_UNAVAILABLE', target,
                     failure=last_failure)
    checks['REMOTE_TRANSPORT'] = {'status': 'PASS'}
    checks['AUTHENTICATION'] = {'status': 'PASS'}
    checks['TARGET_IDENTITY'] = {'status': 'PASS'}
    if type(remote.get('admin')) is not bool or not remote['admin']:
        checks['ADMIN_PRIVILEGE'] = {'status': 'FAIL', 'code': 'ADMIN_REQUIRED'}
    else:
        checks['ADMIN_PRIVILEGE'] = {'status': 'PASS'}
    try:
        build = int(remote.get('build'))
        ps_major = int(remote.get('powershell_major'))
    except (TypeError, ValueError):
        build = ps_major = 0
    name = str(remote.get('windows_name') or '')[:120]
    architecture = remote.get('architecture')
    hostname = str(remote.get('hostname') or '').strip().casefold()
    compatible = (hostname == target['fqdn'].split('.', 1)[0] and 'windows' in name.casefold()
                  and build >= 10240 and architecture == 'x64' and ps_major >= 5)
    checks['WINDOWS_COMPATIBILITY'] = {'status': 'PASS'} if compatible else {
        'status': 'FAIL', 'code': 'WINDOWS_INCOMPATIBLE_OR_IDENTITY_MISMATCH'}
    service_present = remote.get('nightowl_service_present')
    directory_present = remote.get('nightowl_directory_present')
    if type(service_present) is not bool or type(directory_present) is not bool:
        checks['NIGHTOWL_ABSENCE'] = {'status': 'FAIL', 'code': 'NIGHTOWL_STATE_UNKNOWN'}
    elif service_present or directory_present:
        checks['NIGHTOWL_ABSENCE'] = {'status': 'FAIL', 'code': 'NIGHTOWL_INSTALLATION_DETECTED'}
    else:
        checks['NIGHTOWL_ABSENCE'] = {'status': 'PASS'}
    try:
        _, correlation, _ = _match_machine(computer, _machine_maps())
    except Exception:
        correlation = 'UNKNOWN'
    if correlation != 'UNMANAGED':
        checks['NIGHTOWL_ABSENCE'] = {'status': 'FAIL', 'code': 'TARGET_MANAGED_OR_CONFLICT'}
    windows = {'hostname': hostname[:120], 'name': name, 'build': build,
               'architecture': architecture if architecture in ('x64', 'x86') else 'unknown',
               'powershell_major': ps_major}
    result = _result(checks, target=target, windows=windows)
    result['nightowl_absence'] = {
        'service_present': service_present if type(service_present) is bool else None,
        'directory_present': directory_present if type(directory_present) is bool else None,
        'correlation': correlation,
    }
    return result
