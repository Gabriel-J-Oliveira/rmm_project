from django.db import transaction
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


@transaction.atomic
def record_heartbeat(machine, payload: dict, raw_payload: dict) -> InventorySnapshot:
    received_at = timezone.now()
    os_data = payload.get('os') or {}
    hardware = payload.get('hardware') or {}
    hostname = payload['hostname']
    domain = payload.get('domain', '')
    ips = payload.get('ips', [])
    logged_user = payload.get('logged_user', '')
    agent_data = payload.get('agent') or {}
    old_agent_version = machine.agent_version

    machine.hostname = hostname
    machine.domain = domain
    machine.fqdn = build_fqdn(hostname, domain)
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

    return InventorySnapshot.objects.create(
        machine=machine,
        collected_at=payload['heartbeat_at'],
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
        raw_payload=raw_payload,
    )
