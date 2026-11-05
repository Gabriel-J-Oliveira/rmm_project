"""Read-only, bounded connectivity check to a correlated managed AD endpoint."""
import xml.etree.ElementTree as ET
from datetime import timedelta
import time

import requests
from django.db import transaction
from django.utils import timezone

from access_inventory.services.ad_computer_discovery import discover_ad_computers, normalize_fqdn
from config import ad_ldap
from dashboard.ad_install_discovery import resolve_computer_dns
from dashboard.models import RemoteInstallJob
from dashboard.remote_install import _matching_endpoint
from dashboard.remote_install_preflight import (_PinnedAddressAdapter, _safe_ipv4, _tcp_available,
    _valid_fqdn, _valid_sid, _winrm_ca_trust_path, ProbeFailure)

IDENTIFY = b'''<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:i="http://schemas.dmtf.org/wbem/wsman/identity/1/wsmanidentity.xsd"><s:Header/><s:Body><i:Identify/></s:Body></s:Envelope>'''
IDENTITY_NS = 'http://schemas.dmtf.org/wbem/wsman/identity/1/wsmanidentity.xsd'


def check_connectivity(machine, actor):
    result = {'tcp': 'not_checked', 'wsman': 'not_checked', 'authentication': 'not_checked', 'port': 5986}
    from agents.models import AgentMachine
    with transaction.atomic():
        machine = AgentMachine.objects.select_for_update().get(pk=machine.pk)
        if RemoteInstallJob.objects.filter(endpoint=machine, diagnostics__operation='winrm_connectivity',
                created_at__gte=timezone.now() - timedelta(seconds=30)).exists():
            raise ProbeFailure('CHECK_RATE_LIMITED')
        job = RemoteInstallJob.objects.create(target_hostname=machine.hostname, target_fqdn=machine.fqdn,
            target_ad_dn='', requested_by=actor, endpoint=machine, status='RUNNING', stage='CHECKING',
            started_at=timezone.now(), diagnostics={'operation': 'winrm_connectivity'})
    try:
        fqdn = _valid_fqdn(machine.fqdn)
        domain = normalize_fqdn(ad_ldap.ad_config().get('DOMAIN'))
        if not machine.is_active or not fqdn or not domain or not fqdn.endswith('.' + domain):
            raise ProbeFailure('TARGET_NOT_ALLOWED')
        matches = [item for item in discover_ad_computers() if normalize_fqdn(item.get('fqdn')) == fqdn]
        if len(matches) != 1 or matches[0].get('enabled') is not True or not _valid_sid(matches[0].get('sid')) or _matching_endpoint(matches[0]) != machine:
            raise ProbeFailure('TARGET_NOT_ALLOWED')
        dns = resolve_computer_dns(matches).get(fqdn, {})
        addresses = dns.get('ipv4_addresses', [])
        address = str(machine.last_ip or '')
        if (dns.get('dns_status') != 'RESOLVED' or not isinstance(addresses, (list, tuple)) or
            not addresses or len(addresses) > 16 or not all(_safe_ipv4(ip) for ip in addresses) or
            not _safe_ipv4(address) or address not in addresses):
            raise ProbeFailure('TARGET_ADDRESS_UNVERIFIED')
        try:
            if not _tcp_available(address, 5986, timeout=2):
                raise ProbeFailure('TCP_5986_UNAVAILABLE')
            result['tcp'] = 'reachable'
        except ProbeFailure as exc:
            result.update(tcp='unavailable', error=exc.code)
            return result
        try:
            ca = _winrm_ca_trust_path()
            with requests.Session() as session:
                session.trust_env = False
                session.mount('https://', _PinnedAddressAdapter(address))
                with session.post(f'https://{fqdn}:5986/wsman', data=IDENTIFY,
                    headers={'Content-Type': 'application/soap+xml;charset=UTF-8', 'Accept-Encoding': 'identity'},
                    verify=ca, timeout=(2, 3), allow_redirects=False, stream=True) as response:
                    if response.status_code in (401, 403):
                        result.update(wsman='not_verified', error='HTTP_AUTH_CHALLENGE')
                    elif response.status_code == 200:
                        body = bytearray()
                        deadline = time.monotonic() + 6
                        while len(body) <= 16384:
                            if time.monotonic() >= deadline:
                                raise ValueError('response deadline')
                            chunk = response.raw.read1(min(4096, 16385 - len(body)), decode_content=False)
                            if not chunk:
                                break
                            body.extend(chunk)
                        body = bytes(body)
                        if len(body) > 16384:
                            raise ValueError('invalid response')
                        xml = body.decode('utf-8-sig')
                        if '\x00' in xml or '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
                            raise ValueError('invalid response')
                        root = ET.fromstring(xml)
                        identity_tag = '{' + IDENTITY_NS + '}IdentifyResponse'
                        result['wsman'] = 'reachable' if root.tag == identity_tag or root.find('.//' + identity_tag) is not None else 'unavailable'
                    else:
                        result['wsman'] = 'unavailable'
        except Exception:
            result.update(wsman='unavailable', error='WSMAN_UNAVAILABLE')
        return result
    except ProbeFailure as exc:
        result['error'] = exc.code
        return result
    except Exception:
        result['error'] = 'CHECK_UNAVAILABLE'
        return result
    finally:
        result['checked_at'] = timezone.now().isoformat()
        job.status = 'COMPLETED'; job.stage = 'COMPLETED'; job.finished_at = timezone.now()
        job.diagnostics = {'operation': 'winrm_connectivity', 'result': result}
        job.save(update_fields=['status', 'stage', 'finished_at', 'diagnostics', 'updated_at'])
