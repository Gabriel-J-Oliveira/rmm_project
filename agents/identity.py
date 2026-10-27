"""Endpoint identity checks. Serial numbers are auxiliary evidence, never a lookup key."""

import re

from django.db import connection
from django.db.models.functions import Lower, Trim

from .models import AgentMachine


class IdentityConflict(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def lock_identity_registry():
    # Shared by enrollment and authenticated identity changes, including empty-row races.
    if not connection.in_atomic_block:
        raise RuntimeError('Identity changes require a transaction.')
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [734625190283])


def normalized_machine_id(value):
    value = str(value or '').strip()
    if value.casefold() in {'', 'hostname', 'machine_id', 'unknown', 'none', 'null',
                             '00000000-0000-0000-0000-000000000000'}:
        return ''
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}', value):
        raise IdentityConflict('machine_id_invalid')
    return value


def normalized_domain(value):
    return str(value or '').strip().rstrip('.').casefold()


def normalized_hostname(value, domain=''):
    value = str(value or '').strip().rstrip('.').casefold()
    domain = normalized_domain(domain)
    if domain and value.endswith('.' + domain):
        value = value[:-(len(domain) + 1)]
    return value


def meaningful_serial(value):
    value = re.sub(r'[^a-z0-9]', '', str(value or '').casefold())
    if value in {'', 'defaultstring', 'tobefilledbyoem', 'systemserialnumber', 'serialnumber',
                 'unknown', 'none', 'notapplicable', 'notavailable', 'oem', 'na', 'null'}:
        return ''
    if len(set(value)) == 1:
        return ''
    return value


def preserve_serial_evidence(stored, reported):
    if meaningful_serial(stored) and not meaningful_serial(reported):
        return stored
    return reported or stored


def machine_id_matches(value):
    return AgentMachine.objects.annotate(identity_key=Lower(Trim('machine_id'))).filter(
        identity_key=value.casefold())


def machine_name_matches(hostname, domain):
    domain = normalized_domain(domain)
    hostname = normalized_hostname(hostname, domain)
    names = {hostname, hostname + '.'}
    if domain:
        names.update({hostname + '.' + domain, hostname + '.' + domain + '.'})
    return AgentMachine.objects.annotate(
        identity_hostname=Lower(Trim('hostname')), identity_domain=Lower(Trim('domain')),
    ).filter(identity_hostname__in=names, identity_domain__in={domain, domain + '.'})


def find_enrollment_machine(machine_id, hostname, domain):
    by_id = list(machine_id_matches(machine_id).select_for_update()[:2]) if machine_id else []
    by_name = list(machine_name_matches(hostname, domain).select_for_update()[:2])
    if len(by_id) > 1 or len(by_name) > 1:
        raise IdentityConflict('identity_ambiguous')
    if by_id and by_name and by_id[0].pk != by_name[0].pk:
        raise IdentityConflict('identity_keys_conflict')
    if by_id:
        return by_id[0], False, 'machine_id'
    if by_name:
        return by_name[0], False, 'hostname_domain'
    return None, True, 'new'


def validate_runtime_identity(machine, payload):
    """Validate before writes; missing legacy fields never clear established identity.

    A rename requires the current bearer plus the same established machine_id.
    Domain or meaningful serial changes need a separate operator-reviewed recovery.
    """
    system = payload.get('system') if isinstance(payload.get('system'), dict) else {}
    hardware = payload.get('hardware') if isinstance(payload.get('hardware'), dict) else {}
    bios = hardware.get('bios') if isinstance(hardware.get('bios'), dict) else {}
    identifiers = [normalized_machine_id(value) for section in (payload, system)
                   for key in ('machine_id', 'agent_id') if (value := section.get(key))]
    identifiers = [value for value in identifiers if value]
    if len({value.casefold() for value in identifiers}) > 1:
        raise IdentityConflict('machine_id_fields_conflict')
    reported_id = identifiers[0] if identifiers else ''
    stored_id = normalized_machine_id(machine.machine_id)
    if stored_id and reported_id and stored_id.casefold() != reported_id.casefold():
        raise IdentityConflict('machine_id_mismatch')
    if not stored_id and reported_id and machine_id_matches(reported_id).exclude(pk=machine.pk).exists():
        raise IdentityConflict('identity_ambiguous')

    domains = [normalized_domain(section.get('domain')) for section in (payload, system) if section.get('domain')]
    if len(set(domains)) > 1:
        raise IdentityConflict('domain_fields_conflict')
    stored_domain = normalized_domain(machine.domain)
    domain = domains[0] if domains else stored_domain
    if stored_domain and domain != stored_domain:
        raise IdentityConflict('identity_domain_change_requires_review')
    hostnames = [normalized_hostname(section.get('hostname'), domain) for section in (payload, system) if section.get('hostname')]
    if len(set(hostnames)) > 1:
        raise IdentityConflict('hostname_fields_conflict')
    stored_hostname = normalized_hostname(machine.hostname, stored_domain)
    hostname = hostnames[0] if hostnames else stored_hostname
    expected_fqdn = hostname + ('.' + domain if domain else '')
    for section in (payload, system):
        if section.get('fqdn') and normalized_domain(section['fqdn']) != expected_fqdn:
            raise IdentityConflict('fqdn_mismatch')
    if hostname != stored_hostname:
        if not stored_id or not reported_id:
            raise IdentityConflict('identity_rename_requires_machine_id')
        if machine_id_matches(reported_id).exclude(pk=machine.pk).exists():
            raise IdentityConflict('identity_ambiguous')
    if (hostname != stored_hostname or domain != stored_domain) and machine_name_matches(
            hostname, domain).exclude(pk=machine.pk).exists():
        raise IdentityConflict('identity_hostname_conflict')
    serials = [meaningful_serial(section.get('serial_number')) for section in (payload, system, hardware, bios)
               if section.get('serial_number')]
    serials = [value for value in serials if value]
    if len(set(serials)) > 1:
        raise IdentityConflict('serial_fields_conflict')
    stored_serial = meaningful_serial(machine.serial_number)
    if stored_serial and serials and stored_serial != serials[0]:
        raise IdentityConflict('identity_hardware_change_requires_review')
    return {'machine_id': reported_id, 'hostname': hostname.upper(), 'domain': domain,
            'renamed': hostname != stored_hostname}
