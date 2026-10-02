from datetime import datetime, timedelta, timezone

from ldap3.utils.dn import parse_dn
from ldap3.core.exceptions import LDAPInvalidDnError

from config import ad_ldap


COMPUTER_FILTER = '(objectCategory=computer)'
PAGED_RESULTS_OID = '1.2.840.113556.1.4.319'
PAGE_SIZE = 100
MAX_LIMIT = 5000
ATTRIBUTES = (
    'name', 'dNSHostName', 'distinguishedName', 'operatingSystem',
    'operatingSystemVersion', 'userAccountControl', 'lastLogonTimestamp',
    'objectSid',
)
FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def _single_ldap_value(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _text(value):
    value = _single_ldap_value(value)
    return str(value).strip() if value is not None else ''


def normalize_fqdn(value):
    return _text(value).rstrip('.').casefold()


def normalize_hostname(value):
    return _text(value).rstrip('.').split('.', 1)[0].casefold()


def project_ou(distinguished_name):
    try:
        components = parse_dn(distinguished_name, escape=True)
    except (TypeError, ValueError, LDAPInvalidDnError):
        return '', ''
    first_ou = next((index for index, (name, _value, _separator) in enumerate(components)
                     if name.casefold() == 'ou'), None)
    if first_ou is None:
        return '', ''
    ou_name = components[first_ou][1]
    ou_dn = ''.join(f'{name}={value}{separator}' for name, value, separator in components[first_ou:])
    return ou_name, ou_dn


def _filetime_iso(value):
    value = _single_ldap_value(value)
    if isinstance(value, datetime):
        moment = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        return moment.isoformat() if moment > FILETIME_EPOCH else None
    raw = _text(value)
    try:
        ticks = int(raw)
        if ticks <= 0:
            return None
        return (FILETIME_EPOCH + timedelta(microseconds=ticks // 10)).isoformat()
    except (ValueError, OverflowError):
        return None


def _computer(entry):
    attrs = entry.entry_attributes_as_dict
    distinguished_name = _text(attrs.get('distinguishedName')) or _text(entry.entry_dn)
    ou_name, ou_dn = project_ou(distinguished_name)
    fqdn = normalize_fqdn(attrs.get('dNSHostName'))
    hostname = normalize_hostname(attrs.get('name') or fqdn)
    try:
        uac = int(_text(attrs.get('userAccountControl')))
    except ValueError:
        uac = None
    return {
        'hostname': hostname, 'fqdn': fqdn, 'distinguished_name': distinguished_name,
        'ou_name': ou_name, 'ou_dn': ou_dn,
        'operating_system': _text(attrs.get('operatingSystem')),
        'operating_system_version': _text(attrs.get('operatingSystemVersion')),
        'enabled': None if uac is None else not bool(uac & 2),
        'last_logon_at': _filetime_iso(attrs.get('lastLogonTimestamp')),
        'sid': _text(attrs.get('objectSid')),
    }


def discover_ad_computers():
    config = ad_ldap.ad_config()
    if not config.get('COMPUTER_DISCOVERY_ENABLED'):
        raise ad_ldap.ActiveDirectoryConfigError('Descoberta de computadores AD desabilitada.')
    base = str(config.get('COMPUTER_SEARCH_BASE') or '').strip()
    if not base:
        raise ad_ldap.ActiveDirectoryConfigError('AD_COMPUTER_SEARCH_BASE nao configurada.')
    try:
        limit = int(config.get('COMPUTER_DISCOVERY_LIMIT', 500))
        timeout = int(config.get('TIMEOUT', 8))
    except (TypeError, ValueError):
        raise ad_ldap.ActiveDirectoryConfigError('Limite ou timeout AD invalido.') from None
    if not 1 <= limit <= MAX_LIMIT or timeout < 1:
        raise ad_ldap.ActiveDirectoryConfigError('Limite ou timeout AD invalido.')
    ad_ldap.require_secure_ad_transport()

    try:
        conn = ad_ldap.service_connection()
    except Exception:
        raise ad_ldap.ActiveDirectoryUnavailable('Conexao para descoberta AD falhou.') from None
    computers = []
    cookie = None
    seen_cookies = set()
    try:
        for _ in range(limit + 1):
            page_size = min(PAGE_SIZE, limit - len(computers))
            conn.search(
                search_base=base, search_filter=COMPUTER_FILTER,
                search_scope=ad_ldap.SUBTREE, attributes=list(ATTRIBUTES),
                paged_size=page_size,
                paged_cookie=cookie, time_limit=timeout,
            )
            result = conn.result or {}
            if result.get('result') != 0:
                raise ad_ldap.ActiveDirectoryUnavailable('Busca de computadores AD falhou.')
            controls = result.get('controls') or {}
            control = controls.get(PAGED_RESULTS_OID) if isinstance(controls, dict) else None
            control_value = control.get('value') if isinstance(control, dict) else None
            if not isinstance(control_value, dict) or 'cookie' not in control_value:
                raise ad_ldap.ActiveDirectoryUnavailable('Controle de paginacao AD ausente.')
            next_cookie = control_value['cookie']
            if not isinstance(next_cookie, (bytes, str)):
                raise ad_ldap.ActiveDirectoryUnavailable('Cookie de paginacao AD invalido.')
            page_entries = conn.entries
            for entry in page_entries:
                computers.append(_computer(entry))
                if len(computers) >= limit:
                    return computers
            if not next_cookie:
                return computers
            if next_cookie in seen_cookies:
                raise ad_ldap.ActiveDirectoryUnavailable('Paginacao AD invalida.')
            seen_cookies.add(next_cookie)
            cookie = next_cookie
        raise ad_ldap.ActiveDirectoryUnavailable('Limite de paginas AD excedido.')
    except Exception:
        raise ad_ldap.ActiveDirectoryUnavailable('Busca de computadores AD falhou.') from None
    finally:
        try:
            conn.unbind()
        except Exception:
            pass
