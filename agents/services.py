from django.db import transaction
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from .audit import create_audit_event
from .models import AuditEvent
from .models import InventorySnapshot


def build_fqdn(hostname: str, domain: str) -> str:
    if hostname and domain:
        return f'{hostname}.{domain}'
    return hostname


def first_ip(ips: list[str]) -> str | None:
    return ips[0] if ips else None


def _heartbeat_ips(payload: dict) -> list[str]:
    ips = payload.get('ips') or []
    if ips:
        return ips
    ip_address = payload.get('ip_address')
    return [ip_address] if ip_address else []


def _heartbeat_os(payload: dict) -> dict:
    os_data = payload.get('os') or {}
    if os_data:
        return os_data
    return {
        'name': payload.get('os_name') or '',
        'version': payload.get('os_version') or '',
        'build': payload.get('windows_build') or '',
    }


def _heartbeat_agent(payload: dict) -> dict:
    agent_data = payload.get('agent') or {}
    if not any((
        payload.get('agent_version'),
        payload.get('tray_version'),
        payload.get('updater_version'),
        payload.get('agent_mode'),
        payload.get('install_mode'),
    )):
        return agent_data
    merged = dict(agent_data)
    merged.setdefault('version', payload.get('agent_version') or '')
    merged.setdefault('tray_version', payload.get('tray_version') or '')
    merged.setdefault('updater_version', payload.get('updater_version') or '')
    merged.setdefault('mode', payload.get('agent_mode') or '')
    merged.setdefault('install_mode', payload.get('install_mode') or '')
    return merged


def _parse_agent_datetime(value):
    if value is None:
        return timezone.now()
    if hasattr(value, 'tzinfo'):
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value
    parsed = parse_datetime(str(value))
    if parsed is None:
        return timezone.now()
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _normalize_disk_rows(disks):
    rows = []
    for disk in disks or []:
        if not isinstance(disk, dict):
            continue
        size = disk.get('size_bytes') or disk.get('total_bytes') or 0
        free = disk.get('free_bytes') or 0
        used = disk.get('used_bytes')
        if used is None:
            try:
                used = int(size or 0) - int(free or 0)
            except (TypeError, ValueError):
                used = 0
        rows.append({
            'name': disk.get('name') or disk.get('letter') or disk.get('device_id') or '-',
            'letter': disk.get('letter') or disk.get('name') or '',
            'label': disk.get('label') or disk.get('volume_name') or '',
            'size_bytes': size,
            'total_bytes': disk.get('total_bytes') or size,
            'free_bytes': free,
            'used_bytes': used,
            'filesystem': disk.get('filesystem') or '',
            'drive_type': disk.get('drive_type'),
            'volume_name': disk.get('volume_name') or '',
            'used_percent': disk.get('used_percent'),
            'is_system_drive': disk.get('is_system_drive'),
            'bitlocker_status': disk.get('bitlocker_status') or '',
            'health_status': disk.get('health_status') or '',
            'collected_at': disk.get('collected_at'),
        })
    return rows


def _defender_status_from_security(security_payload):
    security = security_payload or {}
    defender = security.get('defender') or {}
    if not defender:
        return {}
    enabled = (
        defender.get('antivirus_enabled')
        if defender.get('antivirus_enabled') is not None
        else defender.get('defender_enabled')
    )
    realtime = (
        defender.get('real_time_protection_enabled')
        if defender.get('real_time_protection_enabled') is not None
        else defender.get('realtime_protection_enabled')
    )
    return {
        'enabled': enabled,
        'real_time_protection_enabled': realtime,
        'engine_version': defender.get('engine_version') or '',
        'product_version': defender.get('product_version') or '',
        'signatures_age_days': defender.get('signatures_age_days'),
        'raw': defender,
    }


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _as_list(value):
    return value if isinstance(value, list) else []


def _normalize_machine_id(value):
    candidate = str(value or '').strip()
    if not candidate:
        return ''
    if candidate.upper() in {'HOSTNAME', 'MACHINE_ID'}:
        return ''
    return candidate


def _sync_machine_identity(machine, payload_machine_id, source):
    machine_id = _normalize_machine_id(payload_machine_id)
    if not machine_id:
        return []
    if machine.machine_id and machine.machine_id != machine_id:
        create_audit_event(
            event_type='endpoint.identity_conflict',
            title='Conflito de identidade do endpoint',
            description=f'{machine.hostname} reportou machine_id diferente do cadastrado.',
            severity=AuditEvent.SEVERITY_WARNING,
            actor_type=AuditEvent.ACTOR_AGENT,
            actor_name='NightOwlAgent',
            endpoint=machine,
            metadata={
                'source': source,
                'stored_machine_id': machine.machine_id,
                'reported_machine_id': machine_id,
            },
        )
        return []
    if not machine.machine_id:
        machine.machine_id = machine_id
        return ['machine_id']
    return []


def _section(payload, collection_type, section_name, aliases=()):
    if collection_type == section_name or collection_type in aliases:
        return payload
    for key in (section_name, *aliases):
        value = payload.get(key)
        if value is not None:
            return value
    return {}


def _software_rows(software_payload):
    if isinstance(software_payload, list):
        return software_payload
    if isinstance(software_payload, dict):
        return (
            software_payload.get('installed_software')
            or software_payload.get('items')
            or software_payload.get('software')
            or software_payload.get('rows')
            or []
        )
    return []


def _disk_rows(disk_payload):
    if isinstance(disk_payload, list):
        return disk_payload
    if isinstance(disk_payload, dict):
        return disk_payload.get('disks') or disk_payload.get('items') or []
    return []


def _network_ips(network_payload):
    network = _as_dict(network_payload)
    ips = network.get('ips') or []
    if ips:
        return ips
    primary_ip = network.get('primary_ip')
    if primary_ip:
        return [primary_ip]
    for adapter in _as_list(network.get('adapters') or network.get('interfaces')):
        if not isinstance(adapter, dict):
            continue
        adapter_ips = adapter.get('ipv4_addresses') or adapter.get('ips') or []
        if adapter_ips:
            return adapter_ips
    return []


def _snapshot_defaults(machine, latest=None):
    latest = latest or None
    return {
        'hostname': getattr(latest, 'hostname', None) or machine.hostname,
        'domain': getattr(latest, 'domain', None) or machine.domain or '',
        'logged_user': getattr(latest, 'logged_user', None) or machine.last_logged_user or '',
        'ips': getattr(latest, 'ips', None) or ([str(machine.last_ip)] if machine.last_ip else []),
        'os_name': getattr(latest, 'os_name', None) or machine.os_name or '',
        'os_version': getattr(latest, 'os_version', None) or machine.os_version or '',
        'windows_build': getattr(latest, 'windows_build', None) or machine.windows_build or '',
        'cpu': getattr(latest, 'cpu', None) or '',
        'memory_total_bytes': getattr(latest, 'memory_total_bytes', None),
        'disks': getattr(latest, 'disks', None) or [],
        'manufacturer': getattr(latest, 'manufacturer', None) or machine.manufacturer or '',
        'model': getattr(latest, 'model', None) or machine.model or '',
        'serial_number': getattr(latest, 'serial_number', None) or machine.serial_number or '',
        'uptime_seconds': getattr(latest, 'uptime_seconds', None),
        'installed_software': getattr(latest, 'installed_software', None) or [],
        'defender_status': getattr(latest, 'defender_status', None) or {},
        'raw_payload': getattr(latest, 'raw_payload', None) or {},
    }


def _raw_payload_with_previous_collections(machine, raw_payload):
    payload = dict(raw_payload or {})
    if payload.get('collections'):
        return payload
    for latest in machine.inventory_snapshots.order_by('-received_at')[:30]:
        latest_raw = getattr(latest, 'raw_payload', None) or {}
        latest_collections = latest_raw.get('collections') if isinstance(latest_raw, dict) else None
        if latest_collections:
            payload['collections'] = latest_collections
            payload['latest_collection_type'] = latest_raw.get('latest_collection_type')
            payload['latest_collection_received_at'] = latest_raw.get('latest_collection_received_at')
            break
    return payload


@transaction.atomic
def record_heartbeat(machine, payload: dict, raw_payload: dict) -> InventorySnapshot:
    received_at = timezone.now()
    os_data = _heartbeat_os(payload)
    hardware = payload.get('hardware') or {}
    hostname = payload['hostname']
    domain = payload.get('domain', '')
    ips = _heartbeat_ips(payload)
    logged_user = payload.get('logged_user') or payload.get('username') or ''
    agent_data = _heartbeat_agent(payload)
    heartbeat_at = payload.get('heartbeat_at') or payload.get('timestamp') or received_at
    old_agent_version = machine.agent_version
    identity_update_fields = _sync_machine_identity(machine, payload.get('machine_id') or payload.get('agent_id'), 'heartbeat')

    machine.hostname = hostname
    machine.domain = domain
    machine.fqdn = payload.get('fqdn') or build_fqdn(hostname, domain)
    machine.last_ip = first_ip(ips)
    machine.last_logged_user = logged_user
    machine.os_name = os_data.get('name', '')
    machine.os_version = os_data.get('version', '')
    machine.windows_build = os_data.get('build', '')
    machine.manufacturer = hardware.get('manufacturer', '')
    machine.model = hardware.get('model', '')
    machine.serial_number = hardware.get('serial_number', '')
    agent_update_fields = []
    if agent_data:
        machine.agent_version = agent_data.get('version', '')
        machine.agent_mode = agent_data.get('mode', '')
        machine.agent_install_path = agent_data.get('install_path', '')
        machine.agent_task_name = agent_data.get('task_name', '')
        machine.agent_runtime = agent_data.get('runtime', '')
        machine.agent_runtime_version = agent_data.get('runtime_version', '')
        machine.agent_update_source = agent_data.get('update_source', '')
        machine.agent_reported_at = received_at
        agent_update_fields = [
            'agent_version',
            'agent_mode',
            'agent_install_path',
            'agent_task_name',
            'agent_runtime',
            'agent_runtime_version',
            'agent_update_source',
            'agent_reported_at',
        ]
    machine.mark_seen(received_at)
    machine.save(
        update_fields=[
            'hostname',
            'domain',
            'fqdn',
            'first_seen_at',
            'last_seen_at',
            'last_ip',
            'last_logged_user',
            'os_name',
            'os_version',
            'windows_build',
            'manufacturer',
            'model',
            'serial_number',
            *identity_update_fields,
            *agent_update_fields,
            'updated_at',
        ],
    )

    if agent_data and old_agent_version != machine.agent_version:
        create_audit_event(
            event_type='agent.version_changed',
            title='Versao do agente alterada',
            description=f'Versao do agente mudou de {old_agent_version or "-"} para {machine.agent_version or "-"}.',
            severity=AuditEvent.SEVERITY_INFO,
            actor_type=AuditEvent.ACTOR_AGENT,
            actor_name='RmmAgent',
            endpoint=machine,
            metadata={
                'old_version': old_agent_version,
                'new_version': machine.agent_version,
            },
        )

    snapshot_raw_payload = _raw_payload_with_previous_collections(machine, raw_payload)

    return InventorySnapshot.objects.create(
        machine=machine,
        collected_at=heartbeat_at,
        received_at=received_at,
        hostname=hostname,
        domain=domain,
        logged_user=logged_user,
        ips=ips,
        os_name=os_data.get('name', ''),
        os_version=os_data.get('version', ''),
        windows_build=os_data.get('build', ''),
        cpu=hardware.get('cpu', ''),
        memory_total_bytes=hardware.get('memory_total_bytes'),
        disks=payload.get('disks', []),
        manufacturer=hardware.get('manufacturer', ''),
        model=hardware.get('model', ''),
        serial_number=hardware.get('serial_number', ''),
        uptime_seconds=payload.get('uptime_seconds'),
        installed_software=payload.get('installed_software', []),
        defender_status=payload.get('defender_status', {}),
        raw_payload=snapshot_raw_payload,
    )


@transaction.atomic
def record_collection(machine, collection_type: str, payload: dict) -> InventorySnapshot:
    payload = _as_dict(payload)
    latest = machine.inventory_snapshots.order_by('-received_at').first()
    data = _snapshot_defaults(machine, latest)
    identity_update_fields = _sync_machine_identity(machine, payload.get('machine_id') or payload.get('agent_id'), 'collection')

    system = _as_dict(_section(payload, collection_type, 'system'))
    network = _as_dict(_section(payload, collection_type, 'network'))
    hardware = _as_dict(_section(payload, collection_type, 'hardware'))
    disk = _section(payload, collection_type, 'disk', aliases=('disks',))
    software = _section(payload, collection_type, 'software')
    security = _as_dict(_section(payload, collection_type, 'security'))
    patches = _as_dict(_section(payload, collection_type, 'patches', aliases=('patch',)))

    os_data = _as_dict(system.get('os'))
    cpu_data = hardware.get('cpu') or {}
    bios_data = _as_dict(hardware.get('bios'))
    cpu_name = cpu_data.get('name') if isinstance(cpu_data, dict) else str(cpu_data or '')

    data['hostname'] = payload.get('hostname') or data['hostname']
    data['domain'] = system.get('domain') or data['domain']
    data['logged_user'] = system.get('logged_user') or data['logged_user']
    data['ips'] = _network_ips(network) or data['ips']
    data['os_name'] = os_data.get('name') or data['os_name']
    data['os_version'] = os_data.get('version') or data['os_version']
    data['windows_build'] = os_data.get('build') or system.get('os_build') or data['windows_build']
    data['manufacturer'] = hardware.get('manufacturer') or system.get('manufacturer') or data['manufacturer']
    data['model'] = hardware.get('model') or system.get('model') or data['model']
    data['serial_number'] = hardware.get('serial_number') or system.get('serial_number') or bios_data.get('serial_number') or data['serial_number']
    data['uptime_seconds'] = system.get('uptime_seconds') or data['uptime_seconds']
    data['cpu'] = cpu_name or data['cpu']
    data['memory_total_bytes'] = hardware.get('memory_total_bytes') or data['memory_total_bytes']

    disk_items = _disk_rows(disk)
    if disk_items:
        data['disks'] = _normalize_disk_rows(disk_items)
    software_items = _software_rows(software)
    if software_items:
        data['installed_software'] = software_items
    if security:
        data['defender_status'] = _defender_status_from_security(security) or data['defender_status']

    raw_payload = dict(data['raw_payload'] or {})
    collections = dict(raw_payload.get('collections') or {})
    collections[collection_type] = payload
    collected_at = payload.get('collected_at')
    if collection_type == 'full_inventory':
        if system:
            collections['system'] = {**system, 'collected_at': system.get('collected_at') or collected_at}
        if hardware:
            collections['hardware'] = {**hardware, 'collected_at': hardware.get('collected_at') or collected_at}
        if network:
            collections['network'] = {**network, 'collected_at': network.get('collected_at') or collected_at}
        if disk_items:
            collections['disk'] = {
                'disks': disk_items,
                'collected_at': collected_at,
            }
        if software_items:
            collections['software'] = {
                'installed_software': software_items,
                'collected_at': collected_at,
            }
        if security:
            collections['security'] = {**security, 'collected_at': security.get('collected_at') or collected_at}
    if patches:
        collections['patches'] = patches
    raw_payload['collections'] = collections
    raw_payload['latest_collection_type'] = collection_type
    raw_payload['latest_collection_received_at'] = timezone.now().isoformat()
    data['raw_payload'] = raw_payload

    machine.hostname = data['hostname']
    machine.domain = data['domain']
    machine.fqdn = build_fqdn(machine.hostname, machine.domain)
    machine.last_ip = first_ip(data['ips'])
    machine.last_logged_user = data['logged_user']
    machine.os_name = data['os_name']
    machine.os_version = data['os_version']
    machine.windows_build = data['windows_build']
    machine.manufacturer = data['manufacturer']
    machine.model = data['model']
    machine.serial_number = data['serial_number']
    if payload.get('agent_version'):
        machine.agent_version = payload.get('agent_version') or machine.agent_version
    if payload.get('agent_mode'):
        machine.agent_mode = payload.get('agent_mode') or machine.agent_mode
    elif payload.get('agent_version') and not machine.agent_mode:
        machine.agent_mode = 'dotnet-service'
    machine.mark_seen()
    machine.save(update_fields=[
        'hostname',
        'domain',
        'fqdn',
        'first_seen_at',
        'last_seen_at',
        'last_ip',
        'last_logged_user',
        'os_name',
        'os_version',
        'windows_build',
        'manufacturer',
        'model',
        'serial_number',
        *identity_update_fields,
        'agent_version',
        'agent_mode',
        'updated_at',
    ])

    snapshot = InventorySnapshot.objects.create(
        machine=machine,
        collected_at=_parse_agent_datetime(payload.get('collected_at')),
        hostname=data['hostname'],
        domain=data['domain'],
        logged_user=data['logged_user'],
        ips=data['ips'],
        os_name=data['os_name'],
        os_version=data['os_version'],
        windows_build=data['windows_build'],
        cpu=data['cpu'],
        memory_total_bytes=data['memory_total_bytes'],
        disks=data['disks'],
        manufacturer=data['manufacturer'],
        model=data['model'],
        serial_number=data['serial_number'],
        uptime_seconds=data['uptime_seconds'],
        installed_software=data['installed_software'],
        defender_status=data['defender_status'],
        raw_payload=data['raw_payload'],
    )

    event_type = {
        'full_inventory': 'agent.inventory_received',
        'system': 'agent.system_inventory_received',
        'hardware': 'agent.hardware_inventory_received',
        'network': 'agent.network_inventory_received',
        'disk': 'agent.disk_inventory_received',
        'disks': 'agent.disk_inventory_received',
        'security': 'agent.security_inventory_received',
        'software': 'agent.software_inventory_received',
        'patches': 'agent.patch_status_received',
        'patch': 'agent.patch_status_received',
    }.get(collection_type, f'agent.{collection_type}_received')
    create_audit_event(
        event_type=event_type,
        title='Coleta do agente recebida',
        description=f'Coleta {collection_type} recebida de {machine.hostname}.',
        severity=AuditEvent.SEVERITY_INFO,
        actor_type=AuditEvent.ACTOR_AGENT,
        actor_name='NightOwlAgent',
        endpoint=machine,
        metadata={
            'collection_type': collection_type,
            'collection_status': payload.get('status') or 'ok',
            'snapshot_id': str(snapshot.id),
        },
    )
    if collection_type == 'full_inventory':
        section_events = [
            ('system', system, 'agent.system_inventory_received'),
            ('hardware', hardware, 'agent.hardware_inventory_received'),
            ('network', network, 'agent.network_inventory_received'),
            ('software', software, 'agent.software_inventory_received'),
            ('security', security, 'agent.security_inventory_received'),
            ('disks', disk, 'agent.disk_inventory_received'),
            ('patches', patches, 'agent.patch_status_received'),
        ]
        for section_name, section_payload, section_event_type in section_events:
            has_payload = bool(_as_list(section_payload) or _as_dict(section_payload))
            if not has_payload:
                continue
            create_audit_event(
                event_type=section_event_type,
                title='Secao de coleta do agente recebida',
                description=f'Secao {section_name} recebida de {machine.hostname}.',
                severity=AuditEvent.SEVERITY_INFO,
                actor_type=AuditEvent.ACTOR_AGENT,
                actor_name='NightOwlAgent',
                endpoint=machine,
                metadata={
                    'collection_type': collection_type,
                    'section': section_name,
                    'snapshot_id': str(snapshot.id),
                },
            )
    return snapshot
