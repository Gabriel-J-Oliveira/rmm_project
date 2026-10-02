"""Read-only AD discovery projection for the agent installation screen."""

import ipaddress
import socket
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone

from django.urls import reverse

from access_inventory.models import ADUser
from access_inventory.services.ad_computer_discovery import discover_ad_computers, normalize_fqdn, normalize_hostname
from agents.models import AgentMachine
from config import ad_ldap


DNS_WORKERS = 12
DNS_DEADLINE_SECONDS = 20


def _domain_for_computer(computer):
    fqdn = normalize_fqdn(computer.get('fqdn'))
    return fqdn.split('.', 1)[1] if '.' in fqdn else normalize_fqdn(ad_ldap.ad_config().get('DOMAIN'))


def _machine_domain(machine):
    domain = normalize_fqdn(machine.domain)
    fqdn = normalize_fqdn(machine.fqdn)
    return domain or (fqdn.split('.', 1)[1] if '.' in fqdn else '')


def _machine_fqdn(machine):
    fqdn = normalize_fqdn(machine.fqdn)
    if '.' in fqdn:
        return fqdn
    domain = _machine_domain(machine)
    hostname = normalize_hostname(machine.hostname)
    return f'{hostname}.{domain}' if hostname and domain else ''


def _username_key(username):
    username = str(username or '').strip().casefold()
    if '\\' in username:
        username = username.rsplit('\\', 1)[-1]
    return username


def _ad_user_maps():
    sam = defaultdict(list)
    upn = defaultdict(list)
    for user in ADUser.objects.values('id', 'sam_account_name', 'user_principal_name', 'display_name', 'email', 'ou__name'):
        if user['sam_account_name']:
            sam[user['sam_account_name'].casefold()].append(user)
        if user['user_principal_name']:
            upn[user['user_principal_name'].casefold()].append(user)
    return sam, upn


def _enrich_user(username, maps):
    key = _username_key(username)
    if not key:
        return None
    sam, upn = maps
    matches = upn.get(key, []) if '@' in key else sam.get(key, [])
    if len(matches) != 1:
        return None
    user = matches[0]
    return {
        'id': str(user['id']), 'sam_account_name': user['sam_account_name'],
        'display_name': user['display_name'], 'email': user['email'],
        'ou': user['ou__name'] or '',
    }


def _machine_maps():
    fqdn = defaultdict(list)
    hostname_domain = defaultdict(list)
    fields = ('id', 'hostname', 'domain', 'fqdn', 'status', 'agent_version', 'last_seen_at', 'last_logged_user')
    for machine in AgentMachine.objects.only(*fields):
        name = _machine_fqdn(machine)
        if name:
            fqdn[name].append(machine)
        domain = _machine_domain(machine)
        hostname = normalize_hostname(machine.hostname)
        if hostname and domain:
            hostname_domain[(hostname, domain)].append(machine)
    return fqdn, hostname_domain


def _match_machine(computer, maps):
    by_fqdn, by_hostname_domain = maps
    fqdn = normalize_fqdn(computer.get('fqdn'))
    display_hostname = normalize_hostname(fqdn) if '.' in fqdn else normalize_hostname(computer.get('hostname'))
    if '.' in fqdn and fqdn in by_fqdn:
        matches = by_fqdn[fqdn]
        return (matches[0], 'MANAGED', 'FQDN') if len(matches) == 1 else (None, 'CONFLICT', 'FQDN')
    domain = _domain_for_computer(computer)
    candidates = by_hostname_domain.get((display_hostname, domain), []) if domain else []
    matches = [machine for machine in candidates if not fqdn or not machine.fqdn or normalize_fqdn(machine.fqdn) == fqdn]
    if len(matches) > 1:
        return None, 'CONFLICT', 'HOSTNAME_DOMAIN'
    if matches:
        return matches[0], 'MANAGED', 'HOSTNAME_DOMAIN'
    return None, 'UNMANAGED', 'NONE'


def _lookup_ipv4(fqdn):
    try:
        addresses = sorted({str(ipaddress.IPv4Address(item[4][0])) for item in socket.getaddrinfo(fqdn, None, socket.AF_INET, socket.SOCK_STREAM)})
        return {'dns_status': 'RESOLVED' if addresses else 'UNRESOLVED',
                'primary_ipv4': addresses[0] if addresses else None, 'ipv4_addresses': addresses}
    except socket.gaierror:
        return {'dns_status': 'UNRESOLVED', 'primary_ipv4': None, 'ipv4_addresses': []}
    except (OSError, ValueError):
        return {'dns_status': 'ERROR', 'primary_ipv4': None, 'ipv4_addresses': []}


def resolve_computer_dns(computers):
    domain = normalize_fqdn(ad_ldap.ad_config().get('DOMAIN'))
    if not domain:
        return {}
    names = {normalize_fqdn(item.get('fqdn')) for item in computers}
    names = {name for name in names if name.endswith(f'.{domain}')}
    if not names:
        return {}
    results = {}
    pool = ThreadPoolExecutor(max_workers=DNS_WORKERS)
    try:
        futures = {pool.submit(_lookup_ipv4, name): name for name in names}
        done, pending = wait(futures, timeout=DNS_DEADLINE_SECONDS)
        for future in done:
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception:
                results[name] = {'dns_status': 'ERROR', 'primary_ipv4': None, 'ipv4_addresses': []}
        for future in pending:
            future.cancel()
            results[futures[future]] = {'dns_status': 'ERROR', 'primary_ipv4': None, 'ipv4_addresses': []}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return results


def _activity_age(last_logon, now):
    if not last_logon:
        return 'unknown'
    try:
        moment = datetime.fromisoformat(last_logon)
        if moment.tzinfo is None:
            return 'unknown'
        days = (now - moment.astimezone(timezone.utc)).total_seconds() / 86400
    except (TypeError, ValueError):
        return 'unknown'
    if days <= 30:
        return '<=30d'
    if days <= 90:
        return '31-90d'
    if days <= 180:
        return '91-180d'
    if days <= 365:
        return '181-365d'
    return '>365d'


def build_install_discovery():
    computers = discover_ad_computers()
    machines = _machine_maps()
    users = _ad_user_maps()
    dns = resolve_computer_dns(computers)
    now = datetime.now(timezone.utc)
    rows = []
    for computer in computers:
        fqdn = normalize_fqdn(computer.get('fqdn'))
        display_hostname = normalize_hostname(fqdn) if '.' in fqdn else normalize_hostname(computer.get('hostname'))
        machine, correlation, method = _match_machine(computer, machines)
        username = machine.last_logged_user if machine else ''
        address = dns.get(fqdn, {'dns_status': 'UNRESOLVED', 'primary_ipv4': None, 'ipv4_addresses': []})
        rows.append({
            'hostname': display_hostname, 'ad_name': computer.get('hostname') or '',
            'fqdn': fqdn, 'distinguished_name': computer.get('distinguished_name') or '',
            'ou': computer.get('ou_name') or '', 'ou_dn': computer.get('ou_dn') or '',
            'operating_system': computer.get('operating_system') or '',
            'operating_system_version': computer.get('operating_system_version') or '',
            'enabled': computer.get('enabled'), 'last_logon_at': computer.get('last_logon_at'),
            'ad_activity_age': _activity_age(computer.get('last_logon_at'), now),
            **address, 'managed': machine is not None, 'correlation_status': correlation,
            'correlation_method': method,
            'endpoint_id': str(machine.id) if machine else None,
            'endpoint_url': reverse('endpoint-detail', args=[machine.id]) if machine else None,
            'agent_version': machine.agent_version if machine else None,
            'last_seen': machine.last_seen_at.isoformat() if machine and machine.last_seen_at else None,
            'nightowl_status': machine.status if machine else None,
            'reported_username': username or None,
            'reported_user_source': 'AgentMachine.last_logged_user' if username else None,
            'ad_user': _enrich_user(username, users),
            'selectable': computer.get('enabled') is True and correlation == 'UNMANAGED',
        })
    counts = Counter(row['correlation_status'] for row in rows)
    summary = {
        'total': len(rows), 'enabled': sum(row['enabled'] is True for row in rows),
        'disabled': sum(row['enabled'] is False for row in rows),
        'managed': counts['MANAGED'], 'unmanaged': counts['UNMANAGED'],
        'conflicts': counts['CONFLICT'],
        'dns_resolved': sum(row['dns_status'] == 'RESOLVED' for row in rows),
        'dns_unresolved': sum(row['dns_status'] != 'RESOLVED' for row in rows),
        'old_ad_activity': sum(row['ad_activity_age'] == '>365d' for row in rows),
        'with_reported_user': sum(bool(row['reported_username']) for row in rows),
    }
    return {'summary': summary, 'computers': rows}
