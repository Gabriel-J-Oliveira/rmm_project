from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from agents.models import AgentMachine, EndpointAlert, InventorySnapshot


GB = 1024 ** 3


class Command(BaseCommand):
    help = 'Seed demo endpoints, snapshots, and alerts for the Night Owl NOC screen.'

    def handle(self, *args, **options):
        self._clear_demo_data()
        now = timezone.now()

        endpoint_specs = [
            ('DEMO-FIN-001', AgentMachine.STATUS_ONLINE, 3, 'ana.financeiro', '10.20.1.11', 16, 38),
            ('DEMO-RH-002', AgentMachine.STATUS_ONLINE, 6, 'bruna.rh', '10.20.2.14', 8, 82),
            ('DEMO-TI-003', AgentMachine.STATUS_ONLINE, 2, 'lucas.ti', '10.20.3.21', 32, 44),
            ('DEMO-ADM-004', AgentMachine.STATUS_OFFLINE, 26 * 60, 'marta.adm', '10.20.4.17', 8, 61),
            ('DEMO-NOTE-005', AgentMachine.STATUS_OFFLINE, 5 * 60, 'carlos.vendas', '10.20.5.19', 4, 72),
            ('DEMO-COM-006', AgentMachine.STATUS_ONLINE, 4, 'paula.compras', '10.20.6.31', 16, 93),
            ('DEMO-OPS-007', AgentMachine.STATUS_UNKNOWN, 0, '', '10.20.7.8', 8, 53),
            ('DEMO-JUR-008', AgentMachine.STATUS_ONLINE, 8, 'renata.juridico', '10.20.8.12', 16, 58),
            ('DEMO-FIN-009', AgentMachine.STATUS_ONLINE, 7, 'marcos.financeiro', '10.20.1.29', 8, 88),
            ('DEMO-RH-010', AgentMachine.STATUS_OFFLINE, 90, 'sofia.rh', '10.20.2.33', 16, 49),
            ('DEMO-TI-011', AgentMachine.STATUS_ONLINE, 1, 'diego.ti', '10.20.3.41', 32, 35),
            ('DEMO-ADM-012', AgentMachine.STATUS_UNKNOWN, 0, '', '10.20.4.43', 8, 64),
            ('DEMO-NOTE-013', AgentMachine.STATUS_ONLINE, 12, 'helena.vendas', '10.20.5.51', 4, 57),
            ('DEMO-COM-014', AgentMachine.STATUS_ONLINE, 9, 'rafael.compras', '10.20.6.55', 16, 76),
            ('DEMO-OPS-015', AgentMachine.STATUS_ONLINE, 5, 'nina.ops', '10.20.7.62', 8, 91),
            ('DEMO-SRV-016', AgentMachine.STATUS_OFFLINE, 32 * 60, 'system', '10.20.9.10', 64, 47),
        ]

        endpoints = {}
        for index, spec in enumerate(endpoint_specs, start=1):
            hostname, status, minutes_ago, user, ip, ram_gb, disk_used = spec
            endpoint = AgentMachine(
                hostname=hostname,
                domain='demo.local',
                fqdn=f'{hostname}.demo.local',
                is_active=True,
                status=status,
                first_seen_at=now - timedelta(days=12, minutes=index),
                last_seen_at=None if status == AgentMachine.STATUS_UNKNOWN else now - timedelta(minutes=minutes_ago),
                last_ip=ip,
                last_logged_user=user,
                os_name='Microsoft Windows 11 Pro',
                os_version='10.0.22631',
                windows_build='22631',
                manufacturer='Dell Inc.',
                model='Latitude 5440' if 'NOTE' in hostname else 'OptiPlex 7010',
                serial_number=f'DEMO-SN-{index:04d}',
                agent_version='0.0.9' if index % 4 == 0 else '0.1.0',
                agent_mode='scheduled_task',
                agent_install_path='C:\\RMM',
                agent_task_name='RMM-Agent-Heartbeat',
                agent_runtime='powershell',
                agent_runtime_version='5.1.22621.2506',
                agent_reported_at=now - timedelta(minutes=minutes_ago or index),
            )
            endpoint.set_agent_token(AgentMachine.generate_token())
            endpoint.save()
            endpoints[hostname] = endpoint
            self._create_snapshot(endpoint, now - timedelta(hours=4), user, ip, ram_gb, max(disk_used - 24, 35), index, previous=True)
            self._create_snapshot(endpoint, now, user, ip, ram_gb, disk_used, index)

        self._create_alerts(endpoints, now)

        self.stdout.write(self.style.SUCCESS(
            f'Demo NOC seeded: {len(endpoints)} endpoints, '
            f'{InventorySnapshot.objects.filter(machine__domain="demo.local", machine__hostname__startswith="DEMO-").count()} snapshots, '
            f'{EndpointAlert.objects.filter(metadata__demo=True).count()} alerts.'
        ))

    def _clear_demo_data(self):
        EndpointAlert.objects.filter(metadata__demo=True).delete()
        AgentMachine.objects.filter(hostname__startswith='DEMO-', domain='demo.local').delete()

    def _create_snapshot(self, endpoint, now, user, ip, ram_gb, disk_used, index, previous=False):
        disk_size = 256 * GB if ram_gb <= 8 else 512 * GB
        free_bytes = int(disk_size * ((100 - disk_used) / 100))
        snapshot_ip = ip
        snapshot_user = user
        windows_build = endpoint.windows_build
        if previous:
            parts = ip.split('.')
            if len(parts) == 4:
                parts[-1] = str(max(int(parts[-1]) - 1, 1))
                snapshot_ip = '.'.join(parts)
            snapshot_user = f'prev.{user}' if user and user != 'system' else user
            windows_build = '22621'
        software = [
            {'name': 'Microsoft 365 Apps for enterprise', 'version': '16.0', 'publisher': 'Microsoft Corporation'},
            {'name': 'Bitdefender Endpoint Security Tools', 'version': '7.9', 'publisher': 'Bitdefender'},
        ]
        if endpoint.hostname in {'DEMO-TI-003', 'DEMO-TI-011'} and not previous:
            software.append({'name': 'WinSCP', 'version': '6.3', 'publisher': 'Martin Prikryl'})
        if endpoint.hostname in {'DEMO-FIN-009', 'DEMO-COM-014'} and not previous:
            software.append({'name': 'AnyDesk', 'version': '8.0', 'publisher': 'AnyDesk Software GmbH'})
        if endpoint.hostname == 'DEMO-COM-006' and not previous:
            software = [{'name': 'Microsoft 365 Apps for enterprise', 'version': '16.0', 'publisher': 'Microsoft Corporation'}]

        InventorySnapshot.objects.create(
            machine=endpoint,
            collected_at=now - timedelta(minutes=index),
            received_at=now - timedelta(minutes=index),
            hostname=endpoint.hostname,
            domain=endpoint.domain,
            logged_user=snapshot_user,
            ips=[snapshot_ip],
            os_name=endpoint.os_name,
            os_version=endpoint.os_version,
            windows_build=windows_build,
            cpu='Intel Core i5-1345U' if ram_gb < 32 else 'Intel Core i7-13700',
            memory_total_bytes=ram_gb * GB,
            disks=[{
                'name': 'C:',
                'size_bytes': disk_size,
                'free_bytes': free_bytes,
            }],
            manufacturer=endpoint.manufacturer,
            model=endpoint.model,
            serial_number=endpoint.serial_number,
            uptime_seconds=(index + 4) * 86400,
            installed_software=software,
            defender_status={'enabled': True, 'real_time_protection_enabled': True} if previous or endpoint.hostname != 'DEMO-COM-006' else {},
            raw_payload={'demo': True, 'source': 'seed_demo_noc'},
        )

    def _create_alerts(self, endpoints, now):
        self._alert(endpoints['DEMO-COM-006'], 'disk_low', EndpointAlert.SEVERITY_CRITICAL, 'Disco C: critico', 'Disco C: possui apenas 7% livre.', now - timedelta(minutes=4), {
            'dedupe_key': 'disk_low:C:',
            'disk_name': 'C:',
            'free_percent': 7,
            'used_percent': 93,
            'free_bytes': 18 * GB,
            'size_bytes': 256 * GB,
        })
        self._alert(endpoints['DEMO-OPS-015'], 'disk_low', EndpointAlert.SEVERITY_WARNING, 'Disco C: com pouco espaco', 'Disco C: possui apenas 9% livre.', now - timedelta(minutes=12), {
            'dedupe_key': 'disk_low:C:',
            'disk_name': 'C:',
            'free_percent': 9,
            'used_percent': 91,
            'free_bytes': 46 * GB,
            'size_bytes': 512 * GB,
        })
        self._alert(endpoints['DEMO-ADM-004'], 'endpoint_offline', EndpointAlert.SEVERITY_CRITICAL, 'Endpoint offline ha mais de 24h', 'Endpoint sem comunicacao ha 26 horas.', now - timedelta(minutes=19), {
            'dedupe_key': 'endpoint_offline',
            'offline_for_seconds': 26 * 3600,
            'threshold': '24h',
        })
        self._alert(endpoints['DEMO-NOTE-005'], 'endpoint_offline', EndpointAlert.SEVERITY_WARNING, 'Endpoint offline', 'Endpoint sem comunicacao ha 5 horas.', now - timedelta(minutes=33), {
            'dedupe_key': 'endpoint_offline',
            'offline_for_seconds': 5 * 3600,
            'threshold': '1h',
        })
        self._alert(endpoints['DEMO-COM-006'], 'security_antivirus', EndpointAlert.SEVERITY_CRITICAL, 'Nenhuma protecao antivirus identificada', 'Defender ausente e nenhum antivirus alternativo foi identificado.', now - timedelta(minutes=9), {
            'dedupe_key': 'security_antivirus',
            'security_state': 'missing',
        })
        self._alert(endpoints['DEMO-FIN-009'], 'remote_access_software', EndpointAlert.SEVERITY_SECURITY, 'Software de acesso remoto detectado', 'Ferramenta de acesso remoto detectada: AnyDesk.', now - timedelta(minutes=16), {
            'dedupe_key': 'remote_access_software:anydesk',
            'software_name': 'AnyDesk',
            'version': '8.0',
            'publisher': 'AnyDesk Software GmbH',
            'category': 'remote_access',
        })
        self._alert(endpoints['DEMO-TI-003'], 'admin_network_tool', EndpointAlert.SEVERITY_SECURITY, 'Ferramenta administrativa detectada', 'Ferramenta administrativa/rede detectada: WinSCP.', now - timedelta(minutes=42), {
            'dedupe_key': 'admin_network_tool:winscp',
            'software_name': 'WinSCP',
            'version': '6.3',
            'publisher': 'Martin Prikryl',
            'category': 'admin_network',
        })
        self._alert(endpoints['DEMO-NOTE-013'], 'low_memory', EndpointAlert.SEVERITY_WARNING, 'Memoria baixa', 'Endpoint possui apenas 4 GB de RAM.', now - timedelta(minutes=54), {
            'dedupe_key': 'low_memory',
            'memory_total_bytes': 4 * GB,
            'memory_total_gb': 4,
        })
        self._alert(endpoints['DEMO-RH-002'], 'high_uptime', EndpointAlert.SEVERITY_WARNING, 'Reinicializacao recomendada', 'Endpoint ligado ha 18 dias. Reinicializacao recomendada.', now - timedelta(hours=2), {
            'dedupe_key': 'high_uptime',
            'uptime_seconds': 18 * 86400,
            'uptime_days': 18,
        })
        self._alert(endpoints['DEMO-OPS-007'], 'stale_inventory', EndpointAlert.SEVERITY_WARNING, 'Inventario desatualizado', 'Ultimo inventario recebido ha mais de 24 horas.', now - timedelta(hours=3), {
            'dedupe_key': 'stale_inventory',
            'age_seconds': 30 * 3600,
        })
        self._alert(endpoints['DEMO-FIN-001'], 'disk_low', EndpointAlert.SEVERITY_WARNING, 'Disco C: voltou ao normal', 'Disco C: voltou ao estado normal em DEMO-FIN-001.', now - timedelta(minutes=6), {
            'dedupe_key': 'disk_low:C:',
            'disk_name': 'C:',
            'free_percent': 28,
            'used_percent': 72,
        }, status=EndpointAlert.STATUS_RESOLVED, resolved_at=now - timedelta(minutes=5))

    def _alert(self, endpoint, alert_type, severity, title, description, seen_at, metadata, status=EndpointAlert.STATUS_OPEN, resolved_at=None):
        payload = {'demo': True, **metadata}
        EndpointAlert.objects.create(
            endpoint=endpoint,
            alert_type=alert_type,
            severity=severity,
            title=title,
            description=description,
            status=status,
            first_seen_at=seen_at - timedelta(minutes=10),
            last_seen_at=seen_at,
            resolved_at=resolved_at,
            metadata=payload,
        )
