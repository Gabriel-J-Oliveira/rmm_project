"""Ephemeral, read-only preflight for one AD computer."""

import base64
import ipaddress
import json
import os
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from django.conf import settings

from access_inventory.services.ad_computer_discovery import discover_ad_computers, normalize_fqdn
from config import ad_ldap
from dashboard.ad_install_discovery import _machine_maps, _match_machine, resolve_computer_dns


CHECKS = ('TARGET', 'DNS', 'REMOTE_TRANSPORT', 'AUTHENTICATION', 'ADMIN_PRIVILEGE',
          'WINDOWS_COMPATIBILITY', 'NIGHTOWL_ABSENCE')
WINRM_PORT = 5986
SOCKET_TIMEOUT_SECONDS = 3
WINRM_DEADLINE_SECONDS = 25
MAX_OUTPUT_BYTES = 8192
_DNS_LABEL = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
_CORPORATE_NETWORKS = tuple(ipaddress.IPv4Network(network) for network in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
))

_READ_ONLY_PROBE = r'''
$ErrorActionPreference = 'Stop'
$os = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$principal = [Security.Principal.WindowsPrincipal]([Security.Principal.WindowsIdentity]::GetCurrent())
$root = Join-Path $env:ProgramData 'NightOwl'
[pscustomobject]@{
    hostname = [string]$env:COMPUTERNAME
    windows_name = [string]$os.ProductName
    build = [int]$os.CurrentBuildNumber
    architecture = $(if ([Environment]::Is64BitOperatingSystem) { 'x64' } else { 'x86' })
    powershell_major = [int]$PSVersionTable.PSVersion.Major
    admin = [bool]$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    nightowl_service_present = [bool](Get-Service -Name 'NightOwlAgentDotNet' -ErrorAction SilentlyContinue)
    nightowl_directory_present = [bool](Test-Path -LiteralPath $root)
} | ConvertTo-Json -Compress
'''


class ProbeFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _result(checks, target=None, windows=None):
    return {
        'status': 'READY' if all(checks[key]['status'] == 'PASS' for key in CHECKS) else 'NOT_READY',
        'checks': checks, 'target': target, 'windows': windows,
    }


def _fail(checks, key, code, target=None):
    checks[key] = {'status': 'FAIL', 'code': code}
    return _result(checks, target=target)


def _valid_fqdn(value):
    fqdn = normalize_fqdn(value)
    labels = fqdn.split('.')
    return fqdn if len(fqdn) <= 253 and len(labels) >= 3 and all(_DNS_LABEL.fullmatch(label) for label in labels) else ''


def _ad_target(value):
    fqdn = _valid_fqdn(value)
    domain = normalize_fqdn(ad_ldap.ad_config().get('DOMAIN'))
    if not fqdn or not domain or not fqdn.endswith(f'.{domain}'):
        raise ProbeFailure('INVALID_TARGET')
    try:
        matches = [item for item in discover_ad_computers() if normalize_fqdn(item.get('fqdn')) == fqdn]
    except Exception:
        raise ProbeFailure('AD_DISCOVERY_UNAVAILABLE') from None
    if len(matches) != 1 or not _valid_fqdn(matches[0].get('fqdn')):
        raise ProbeFailure('TARGET_NOT_UNIQUE_OR_MISSING')
    computer = matches[0]
    if computer.get('enabled') is not True:
        raise ProbeFailure('AD_COMPUTER_DISABLED')
    try:
        _, correlation, _ = _match_machine(computer, _machine_maps())
    except Exception:
        raise ProbeFailure('CORRELATION_UNAVAILABLE') from None
    if correlation != 'UNMANAGED':
        raise ProbeFailure('TARGET_MANAGED_OR_CONFLICT')
    return computer


def _tcp_available(address):
    try:
        with socket.create_connection((address, WINRM_PORT), timeout=SOCKET_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


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


def _block_redirects(session):
    send = session.send

    def send_without_redirects(request, **kwargs):
        kwargs['allow_redirects'] = False
        response = send(request, **kwargs)
        if 300 <= response.status_code < 400:
            response.close()
            raise ProbeFailure('WINRM_REDIRECT_BLOCKED')
        return response

    session.send = send_without_redirects


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
            now_utc = datetime.now(timezone.utc)
            for pem in certificates:
                certificate = x509.load_pem_x509_certificate(pem)
                constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
                if not constraints.ca or not certificate.not_valid_before_utc <= now_utc < certificate.not_valid_after_utc:
                    raise ValueError()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=str(path))
        now = time.time()
        if not any(ssl.cert_time_to_seconds(ca['notBefore']) <= now < ssl.cert_time_to_seconds(ca['notAfter'])
                   for ca in context.get_ca_certs()):
            raise ValueError()
    except (OSError, ssl.SSLError, ValueError, x509.ExtensionNotFound):
        raise ProbeFailure('WINRM_CA_TRUST_INVALID') from None
    return os.path.realpath(path)


def _new_winrm_protocol(fqdn, username, password):
    ca_trust_path = _winrm_ca_trust_path()
    from winrm.protocol import Protocol

    protocol = Protocol(
        endpoint=f'https://{fqdn}:{WINRM_PORT}/wsman', transport='ntlm',
        username=username, password=password, server_cert_validation='validate',
        ca_trust_path=ca_trust_path,
        proxy=None, operation_timeout_sec=3, read_timeout_sec=5,
    )
    _block_redirects(protocol.transport.build_session())
    return protocol


def _winrm_probe(fqdn, username, password):
    try:
        from winrm.exceptions import AuthenticationError, WinRMOperationTimeoutError, WinRMTransportError
    except ImportError:
        raise ProbeFailure('WINRM_CLIENT_UNAVAILABLE') from None

    protocol = None
    shell_id = None
    command_id = None
    try:
        protocol = _new_winrm_protocol(fqdn, username, password)
        deadline = time.monotonic() + WINRM_DEADLINE_SECONDS
        shell_id = protocol.open_shell()
        encoded = base64.b64encode(_READ_ONLY_PROBE.encode('utf-16-le')).decode('ascii')
        command_id = protocol.run_command(shell_id, 'powershell.exe',
                                          ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded])
        stdout = bytearray()
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
                return data
        raise ProbeFailure('REMOTE_TIMEOUT')
    except ProbeFailure:
        raise
    except AuthenticationError:
        raise ProbeFailure('AUTHENTICATION_FAILED') from None
    except WinRMTransportError as exc:
        raise ProbeFailure('AUTHENTICATION_FAILED' if exc.code in (401, 403) else 'WINRM_UNAVAILABLE') from None
    except Exception:
        raise ProbeFailure('WINRM_UNAVAILABLE') from None
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


def run_remote_install_preflight(fqdn, username, password):
    checks = {name: {'status': 'SKIPPED'} for name in CHECKS}
    try:
        computer = _ad_target(fqdn)
    except ProbeFailure as exc:
        return _fail(checks, 'TARGET', exc.code)
    checks['TARGET'] = {'status': 'PASS'}
    target = {'hostname': str(computer.get('hostname') or '')[:120],
              'fqdn': normalize_fqdn(computer['fqdn']),
              'operating_system': str(computer.get('operating_system') or '')[:120]}
    dns = resolve_computer_dns([computer]).get(target['fqdn'], {})
    address = dns.get('primary_ipv4')
    if dns.get('dns_status') != 'RESOLVED' or not address:
        return _fail(checks, 'DNS', 'DNS_UNRESOLVED', target)
    addresses = dns.get('ipv4_addresses', [])
    if not isinstance(addresses, (list, tuple)) or not all(_safe_ipv4(item) for item in [address, *addresses]):
        return _fail(checks, 'DNS', 'UNSAFE_TARGET_ADDRESS', target)
    target['ip'] = address
    checks['DNS'] = {'status': 'PASS'}
    try:
        _winrm_ca_trust_path()
    except ProbeFailure as exc:
        return _fail(checks, 'REMOTE_TRANSPORT', exc.code, target)
    if not _tcp_available(address):
        return _fail(checks, 'REMOTE_TRANSPORT', 'WINRM_UNAVAILABLE', target)
    checks['REMOTE_TRANSPORT'] = {'status': 'PASS'}
    if not isinstance(username, str) or not username.strip() or len(username) > 256 or not username.isprintable() or \
            not isinstance(password, str) or not password or len(password) > 512 or not password.isprintable():
        return _fail(checks, 'AUTHENTICATION', 'CREDENTIAL_REQUIRED', target)
    try:
        # WinRM resolves the FQDN again; TLS hostname validation remains required, but DNS is not pinned in this MVP.
        remote = _winrm_probe(target['fqdn'], username.strip(), password)
    except ProbeFailure as exc:
        key = 'AUTHENTICATION' if exc.code in ('AUTHENTICATION_FAILED', 'CREDENTIAL_REQUIRED') else 'REMOTE_TRANSPORT'
        return _fail(checks, key, exc.code, target)
    checks['AUTHENTICATION'] = {'status': 'PASS'}
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
    return _result(checks, target=target, windows=windows)
