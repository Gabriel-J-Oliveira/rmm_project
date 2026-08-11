from datetime import datetime, time, timedelta

import csv
import json
import logging
import uuid
from types import SimpleNamespace

from django.contrib import messages
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone
from django.views.decorators.http import require_POST

from agents.models import (
    AgentEnrollmentLog,
    AgentEnrollmentToken,
    AgentJob,
    AgentMachine,
    AgentManualValidationToken,
    AgentOperationalStatus,
    AgentRelease,
    AgentReleaseAudit,
    AgentReleaseGroup,
    AgentReleaseTrustBundle,
    AlertEvent,
    AuditEvent,
    EndpointAlert,
    MaintenanceRun,
    SoftwarePolicy,
    SoftwarePolicyException,
    SoftwarePolicyTargetEndpoint,
    SoftwarePolicyViolation,
)
from agents.job_progress import (
    job_expected_timeout_at,
    job_installed_version,
    job_previous_version,
    job_progress_message,
    job_progress_percentage,
    job_stage,
    job_stale_info,
    job_target_version,
    public_job_status,
    sanitize_job_value,
)
from config.authz import is_nightowl_technical_user
from agents.audit import create_audit_event
from agents.services import (
    AGENT_RELEASE_AVAILABLE_STATUSES,
    build_update_agent_job_payload,
    change_agent_release_rollout,
    evaluate_agent_update_policy,
    publish_agent_release,
    promote_agent_release,
    revoke_agent_release,
    supersede_agent_release,
)
from agents.software_catalog import (
    ADMIN_NETWORK_SOFTWARE,
    CATEGORY_LABELS,
    REMOTE_ACCESS_SOFTWARE,
    RISK_LABELS,
    SECURITY_SOFTWARE,
    classify_software as classify_software_catalog,
    normalize_key,
)
from agents.versioning import agent_version_state, compare_versions, parse_semver, sort_releases_by_version
from tickets.models import NotificationOutbox
from tickets.services.email_outbox import (
    cancel_email,
    mark_email_pending,
    process_pending_emails,
    retry_all_failed,
    retry_failed_email,
    send_email_outbox_item,
    smtp_configuration_status,
)


logger = logging.getLogger(__name__)

REMOTE_ACCESS_TERMS = REMOTE_ACCESS_SOFTWARE
ADMIN_TOOL_TERMS = ADMIN_NETWORK_SOFTWARE
SECURITY_TERMS = SECURITY_SOFTWARE

ALERT_TYPE_OPTIONS = [
    ('disk_low', 'Disco baixo'),
    ('endpoint_offline', 'Endpoint offline'),
    ('security_antivirus', 'Antivirus'),
    ('security_alternative_av', 'AV alternativo'),
    ('high_uptime', 'Uptime alto'),
    ('remote_access_software', 'Acesso remoto'),
    ('admin_network_tool', 'Admin/Rede'),
    ('stale_inventory', 'Inventario desatualizado'),
    ('low_memory', 'Memoria baixa'),
    ('agent_outdated', 'Agente desatualizado'),
    ('software_policy_violation', 'Politica de Software'),
]

PERIOD_OPTIONS = [
    ('all', 'Todos'),
    ('today', 'Hoje'),
    ('24h', '24h'),
    ('7d', '7d'),
]

EVENT_PERIOD_OPTIONS = [
    ('24h', '24h'),
    ('7d', '7d'),
    ('30d', '30d'),
    ('all', 'Todos'),
]

EVENT_CATEGORY_OPTIONS = [
    ('all', 'Todos'),
    ('agent', 'Agente'),
    ('system', 'Sistema'),
    ('alerts', 'Alertas'),
    ('jobs', 'Jobs'),
    ('security', 'Segurança'),
    ('inventory', 'Inventário'),
    ('maintenance', 'Manutenção'),
]

EVENT_CATEGORY_PREFIXES = {
    'agent': ['agent.'],
    'system': ['system.', 'endpoint.status_changed'],
    'alerts': ['alert.'],
    'jobs': ['job.', 'maintenance.task_'],
    'security': ['security.', 'policy.', 'software_policy.'],
    'inventory': ['inventory.', 'software.', 'network.', 'disk.', 'os.', 'user.'],
    'maintenance': ['maintenance.'],
}


def latest_agent_version():
    releases = list(AgentRelease.objects.filter(
        channel=AgentRelease.CHANNEL_STABLE,
        status=AgentRelease.STATUS_AVAILABLE,
        revoked=False,
    ))
    ordered = sort_releases_by_version(releases, reverse=True)
    release = ordered[0] if ordered else None
    if release:
        return release.version
    candidates = [
        getattr(settings, 'NIGHTOWL_AGENT_VERSION_MANIFEST', ''),
        settings.BASE_DIR / 'downloads' / 'agent' / 'windows' / 'version.json',
        settings.BASE_DIR / 'NightOwl.Agent.Windows' / 'publish' / 'downloads' / 'agent' / 'windows' / 'version.json',
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            with open(candidate, encoding='utf-8') as handle:
                version = (json.load(handle).get('version') or '').strip()
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if version:
            return version
    return getattr(settings, 'NIGHTOWL_RECOMMENDED_AGENT_VERSION', '').strip()

SOFTWARE_CATEGORY_OPTIONS = [('all', 'Todas'), *CATEGORY_LABELS.items()]
SOFTWARE_RISK_OPTIONS = [('all', 'Todos'), *RISK_LABELS.items()]

MUTE_DURATIONS = {
    '1h': ('1 hora', timedelta(hours=1)),
    '4h': ('4 horas', timedelta(hours=4)),
    '24h': ('24 horas', timedelta(hours=24)),
    '7d': ('7 dias', timedelta(days=7)),
}


class MockList(list):
    def all(self):
        return self


class MockEndpoint(SimpleNamespace):
    def get_status_display(self):
        return {
            AgentMachine.STATUS_ONLINE: 'Online',
            AgentMachine.STATUS_OFFLINE: 'Offline',
            AgentMachine.STATUS_UNKNOWN: 'Unknown',
        }.get(self.status, self.status or 'Unknown')


class MockAlert(SimpleNamespace):
    def get_severity_display(self):
        return dict(EndpointAlert.SEVERITY_CHOICES).get(self.severity, self.severity.title())

    def get_status_display(self):
        return dict(EndpointAlert.STATUS_CHOICES).get(self.status, self.status.title())

    @property
    def is_muted(self):
        return bool(getattr(self, 'muted_until', None) and self.muted_until > timezone.now())

    @property
    def is_temporary(self):
        return bool(getattr(self, 'expires_at', None))


class MockEvent(SimpleNamespace):
    def get_severity_display(self):
        return dict(AuditEvent.SEVERITY_CHOICES).get(self.severity, self.severity.title())

    def get_actor_type_display(self):
        return dict(AuditEvent.ACTOR_CHOICES).get(self.actor_type, self.actor_type.title())


def _mock_endpoint(pk, hostname, status, user, ip, os_name, domain='CONTROL', minutes=8, agent='0.9.8'):
    seen_at = timezone.now() - timedelta(minutes=minutes)
    return MockEndpoint(
        id=uuid.UUID(pk),
        pk=uuid.UUID(pk),
        hostname=hostname,
        status=status,
        domain=domain,
        last_logged_user=user,
        last_ip=ip,
        os_name=os_name,
        last_seen_at=seen_at,
        agent_version=agent,
    )


def mock_rmm_endpoints():
    return [
        _mock_endpoint('00000000-0000-4000-8000-000000000101', 'FIN-012', AgentMachine.STATUS_ONLINE, 'mariana.souza', '192.168.104.42', 'Windows 11 Pro 23H2', minutes=3, agent='1.4.2'),
        _mock_endpoint('00000000-0000-4000-8000-000000000102', 'JUR-PRINT-01', AgentMachine.STATUS_OFFLINE, 'juridico', '192.168.104.66', 'Windows Server 2019', minutes=185, agent='1.3.9'),
        _mock_endpoint('00000000-0000-4000-8000-000000000103', 'REC-004', AgentMachine.STATUS_ONLINE, 'recepcao', '192.168.104.23', 'Windows 10 Pro 22H2', minutes=9, agent='1.4.2'),
        _mock_endpoint('00000000-0000-4000-8000-000000000104', 'DIR-NB-03', AgentMachine.STATUS_ONLINE, 'claudia.ferraz', '192.168.104.88', 'Windows 11 Pro 24H2', minutes=14, agent='1.4.1'),
        _mock_endpoint('00000000-0000-4000-8000-000000000105', 'SRV-ERP-01', AgentMachine.STATUS_ONLINE, 'svc-erp', '192.168.104.10', 'Windows Server 2022', minutes=2, agent='1.4.2'),
        _mock_endpoint('00000000-0000-4000-8000-000000000106', 'COM-017', AgentMachine.STATUS_UNKNOWN, 'daniel.ribeiro', '192.168.104.77', 'Windows 11 Pro 23H2', minutes=54, agent=''),
        _mock_endpoint('00000000-0000-4000-8000-000000000107', 'FIN-DC-02', AgentMachine.STATUS_OFFLINE, 'svc-backup', '192.168.104.12', 'Windows Server 2019', minutes=420, agent='1.2.7'),
        _mock_endpoint('00000000-0000-4000-8000-000000000108', 'TI-NOC-01', AgentMachine.STATUS_ONLINE, 'gabriel.oliveira', '192.168.104.5', 'Windows 11 Enterprise', minutes=1, agent='1.4.2'),
    ]


def _mock_alert(pk, endpoint, title, description, severity, alert_type, minutes=12, status=None, metadata=None, expires=False):
    now = timezone.now()
    return MockAlert(
        id=uuid.UUID(pk),
        pk=uuid.UUID(pk),
        endpoint=endpoint,
        endpoint_id=endpoint.id,
        title=title,
        description=description,
        severity=severity,
        alert_type=alert_type,
        status=status or EndpointAlert.STATUS_OPEN,
        first_seen_at=now - timedelta(hours=2, minutes=minutes),
        last_seen_at=now - timedelta(minutes=minutes),
        updated_at=now - timedelta(minutes=max(1, minutes // 2)),
        resolved_at=now - timedelta(minutes=minutes) if status == EndpointAlert.STATUS_RESOLVED else None,
        muted_until=None,
        expires_at=now + timedelta(hours=2) if expires else None,
        metadata=metadata or {},
        events=MockList([
            SimpleNamespace(get_event_type_display=lambda: 'Criado', created_at=now - timedelta(minutes=minutes)),
            SimpleNamespace(get_event_type_display=lambda: 'Reavaliado', created_at=now - timedelta(minutes=max(1, minutes // 2))),
        ]),
    )


def mock_rmm_alerts(endpoints=None):
    endpoints = endpoints or mock_rmm_endpoints()
    by_name = {endpoint.hostname: endpoint for endpoint in endpoints}
    return [
        _mock_alert('00000000-0000-4000-8000-000000000201', by_name['FIN-012'], 'Bitdefender ausente em FIN-012', 'O agente detectou ausencia do Bitdefender na maquina financeira FIN-012.', EndpointAlert.SEVERITY_CRITICAL, 'security_antivirus', minutes=6),
        _mock_alert('00000000-0000-4000-8000-000000000202', by_name['SRV-ERP-01'], 'Disco C: acima de 90%', 'Volume principal do servidor ERP esta com pouco espaco livre.', EndpointAlert.SEVERITY_WARNING, 'disk_low', minutes=18, metadata={'disk_name': 'C:', 'free_percent': 7, 'used_percent': 93}),
        _mock_alert('00000000-0000-4000-8000-000000000203', by_name['JUR-PRINT-01'], 'Endpoint offline ha mais de 3h', 'Impressora/servidor de impressao do Juridico parou de comunicar.', EndpointAlert.SEVERITY_CRITICAL, 'endpoint_offline', minutes=185),
        _mock_alert('00000000-0000-4000-8000-000000000204', by_name['DIR-NB-03'], 'AnyDesk detectado em notebook da diretoria', 'Software de acesso remoto identificado em endpoint sensivel.', EndpointAlert.SEVERITY_SECURITY, 'remote_access_software', minutes=27),
        _mock_alert('00000000-0000-4000-8000-000000000205', by_name['COM-017'], 'Inventario desatualizado', 'Endpoint sem snapshot completo nas ultimas 24h.', EndpointAlert.SEVERITY_INFO, 'stale_inventory', minutes=54),
        _mock_alert('00000000-0000-4000-8000-000000000206', by_name['REC-004'], 'Memoria baixa recorrente', 'Uso de memoria acima de 88% em tres coletas consecutivas.', EndpointAlert.SEVERITY_WARNING, 'low_memory', minutes=34),
        _mock_alert('00000000-0000-4000-8000-000000000207', by_name['FIN-DC-02'], 'Controlador financeiro offline', 'Servidor de dominio financeiro sem heartbeat recente.', EndpointAlert.SEVERITY_CRITICAL, 'endpoint_offline', minutes=420),
        _mock_alert('00000000-0000-4000-8000-000000000208', by_name['TI-NOC-01'], 'Alerta de politica resolvido', 'Violacao de software normalizada apos remocao.', EndpointAlert.SEVERITY_SECURITY, 'software_policy_violation', minutes=11, status=EndpointAlert.STATUS_RESOLVED),
    ]


def mock_rmm_events(endpoints=None, alerts=None):
    endpoints = endpoints or mock_rmm_endpoints()
    alerts = alerts or mock_rmm_alerts(endpoints)
    specs = [
        ('agent.heartbeat_received', 'Heartbeat recebido', 'FIN-012 enviou heartbeat e métricas básicas.', AuditEvent.SEVERITY_SUCCESS, AuditEvent.ACTOR_AGENT, endpoints[0], None, 2),
        ('alert.created', 'Alerta critico criado', 'Bitdefender ausente detectado no FIN-012.', AuditEvent.SEVERITY_CRITICAL, AuditEvent.ACTOR_SYSTEM, endpoints[0], alerts[0], 5),
        ('maintenance.run_completed', 'Rotina de manutenção concluída', 'Rotina mark_offline_agents executada com sucesso.', AuditEvent.SEVERITY_SUCCESS, AuditEvent.ACTOR_SCHEDULER, endpoints[1], None, 9),
        ('job.completed', 'Job de inventário concluído', 'Coleta de software concluída em 47 pacotes.', AuditEvent.SEVERITY_SUCCESS, AuditEvent.ACTOR_SCHEDULER, endpoints[2], None, 12),
        ('endpoint.status_changed', 'Endpoint ficou offline', 'JUR-PRINT-01 deixou de comunicar com o RMM.', AuditEvent.SEVERITY_WARNING, AuditEvent.ACTOR_SYSTEM, endpoints[1], alerts[2], 16),
        ('inventory.software_changed', 'Inventário de software alterado', 'Google Chrome atualizado de 125 para 126.', AuditEvent.SEVERITY_INFO, AuditEvent.ACTOR_AGENT, endpoints[2], None, 21),
        ('software_policy.violation', 'Politica violada', 'AnyDesk detectado em endpoint da diretoria.', AuditEvent.SEVERITY_SECURITY, AuditEvent.ACTOR_SYSTEM, endpoints[3], alerts[3], 25),
        ('alert.acknowledged', 'Alerta reconhecido', 'Tecnico Gabriel reconheceu alerta de disco.', AuditEvent.SEVERITY_INFO, AuditEvent.ACTOR_USER, endpoints[4], alerts[1], 31),
        ('policy.violation', 'Regra de segurança violada', 'Ferramenta de acesso remoto fora da política permitida.', AuditEvent.SEVERITY_SECURITY, AuditEvent.ACTOR_SYSTEM, endpoints[3], alerts[3], 36),
        ('security.defender_changed', 'Defender alterado', 'Estado de antivirus mudou para atencao.', AuditEvent.SEVERITY_SECURITY, AuditEvent.ACTOR_SYSTEM, endpoints[0], alerts[0], 42),
        ('software.installed', 'Software instalado', 'Novo software detectado no endpoint REC-004.', AuditEvent.SEVERITY_INFO, AuditEvent.ACTOR_SYSTEM, endpoints[2], None, 58),
        ('alert.resolved_auto', 'Alerta resolvido automaticamente', 'Violacao de politica deixou de existir.', AuditEvent.SEVERITY_SUCCESS, AuditEvent.ACTOR_SCHEDULER, endpoints[7], alerts[7], 72),
    ]
    now = timezone.now()
    return [
        MockEvent(
            id=uuid.uuid4(),
            event_type=event_type,
            title=title,
            description=description,
            severity=severity,
            actor_type=actor_type,
            actor_name='NightOwl' if actor_type != AuditEvent.ACTOR_USER else 'gabriel.oliveira',
            endpoint=endpoint,
            alert=alert,
            created_at=now - timedelta(minutes=minutes),
        )
        for event_type, title, description, severity, actor_type, endpoint, alert, minutes in specs
    ]


def mock_endpoint_rows(endpoints=None):
    endpoints = endpoints or mock_rmm_endpoints()
    disk_levels = ['normal', 'warning', 'normal', 'normal', 'critical', 'warning', 'critical', 'normal']
    defender_keys = ['attention', 'ok', 'ok', 'ok', 'ok', 'unknown', 'attention', 'ok']
    software_counts = [47, 18, 39, 52, 31, 44, 23, 68]
    rows = []
    for index, endpoint in enumerate(endpoints):
        level = disk_levels[index % len(disk_levels)]
        used = 93 if level == 'critical' else 82 if level == 'warning' else 51
        defender_key = defender_keys[index % len(defender_keys)]
        primary_disk = {
            'has_data': True,
            'level': level,
            'summary': f'{used}% usado',
            'used_percent': used,
        }
        defender = {'key': 'ok' if defender_key == 'ok' else 'attention' if defender_key == 'attention' else 'unknown'}
        health = calculate_health(endpoint, primary_disk, defender)
        endpoint_type = infer_endpoint_type(endpoint.hostname, endpoint.os_name)
        sector = infer_endpoint_sector(endpoint.hostname, endpoint.domain, endpoint.last_logged_user)
        rows.append({
            'endpoint': endpoint,
            'hostname': endpoint.hostname,
            'domain': endpoint.domain,
            'logged_user': endpoint.last_logged_user,
            'sector': sector,
            'tag': sector,
            'endpoint_type': endpoint_type,
            'endpoint_type_label': endpoint_type_label(endpoint_type),
            'primary_ip': endpoint.last_ip,
            'os_name': endpoint.os_name,
            'last_seen_at': endpoint.last_seen_at,
            'primary_disk': primary_disk,
            'defender_key': defender_key,
            'software_count': software_counts[index % len(software_counts)],
            'has_attention': level in {'warning', 'critical'} or defender_key != 'ok' or endpoint.status == AgentMachine.STATUS_OFFLINE,
            'health': health,
            'attention': endpoint_attention_summary(endpoint, primary_disk, defender_key, health),
            'agent_version': endpoint.agent_version,
            'agent_version_state': agent_version_state(endpoint.agent_version, latest_agent_version()),
        })
    return rows


def mock_software_inventory_rows(endpoints=None):
    endpoints = endpoints or mock_rmm_endpoints()
    def example(*indexes):
        return [{'endpoint': endpoints[index]} for index in indexes]
    specs = [
        {'name': 'Bitdefender Endpoint Security Tools', 'publisher': 'Bitdefender', 'category': 'security', 'category_label': 'Seguranca', 'risk_level': 'low', 'risk_label': 'Baixo', 'endpoint_count': 6, 'versions': ['7.9.12'], 'versions_display': '7.9.12', 'latest_seen_at': timezone.now() - timedelta(minutes=8), 'example_endpoints': example(1, 2, 4), 'is_sensitive': False},
        {'name': 'AnyDesk', 'publisher': 'AnyDesk Software GmbH', 'category': 'remote_access', 'category_label': 'Acesso remoto', 'risk_level': 'high', 'risk_label': 'Alto', 'endpoint_count': 1, 'versions': ['8.0.9'], 'versions_display': '8.0.9', 'latest_seen_at': timezone.now() - timedelta(minutes=27), 'example_endpoints': example(3), 'is_sensitive': True},
        {'name': 'Microsoft 365 Apps', 'publisher': 'Microsoft Corporation', 'category': 'office', 'category_label': 'Produtividade', 'risk_level': 'low', 'risk_label': 'Baixo', 'endpoint_count': 7, 'versions': ['2406', '2407'], 'versions_display': '2406, 2407', 'latest_seen_at': timezone.now() - timedelta(minutes=12), 'example_endpoints': example(0, 2, 7), 'is_sensitive': False},
        {'name': 'Advanced IP Scanner', 'publisher': 'Famatech', 'category': 'admin_network', 'category_label': 'Admin/Rede', 'risk_level': 'medium', 'risk_label': 'Medio', 'endpoint_count': 2, 'versions': ['2.5.4594'], 'versions_display': '2.5.4594', 'latest_seen_at': timezone.now() - timedelta(hours=1), 'example_endpoints': example(7, 5), 'is_sensitive': True},
        {'name': 'Google Chrome', 'publisher': 'Google LLC', 'category': 'browser', 'category_label': 'Navegador', 'risk_level': 'low', 'risk_label': 'Baixo', 'endpoint_count': 8, 'versions': ['126.0.6478'], 'versions_display': '126.0.6478', 'latest_seen_at': timezone.now() - timedelta(minutes=6), 'example_endpoints': example(0, 3, 6), 'is_sensitive': False},
        {'name': 'Python 3.12', 'publisher': 'Python Software Foundation', 'category': 'development', 'category_label': 'Desenvolvimento', 'risk_level': 'medium', 'risk_label': 'Medio', 'endpoint_count': 1, 'versions': ['3.12.4'], 'versions_display': '3.12.4', 'latest_seen_at': timezone.now() - timedelta(hours=3), 'example_endpoints': example(7), 'is_sensitive': False},
    ]
    rows = []
    for row in specs:
        row['endpoints'] = row['example_endpoints']
        rows.append(row)
    summary = {
        'unique_count': len(rows),
        'install_count': sum(row['endpoint_count'] for row in rows),
        'remote_access_count': sum(row['category'] == 'remote_access' for row in rows),
        'admin_network_count': sum(row['category'] == 'admin_network' for row in rows),
        'security_count': sum(row['category'] == 'security' for row in rows),
        'unknown_count': 2,
    }
    return rows, summary


def mock_policy_context(endpoints=None):
    endpoints = endpoints or mock_rmm_endpoints()
    policies = [
        {
            'id': '00000000-0000-4000-8000-000000000301',
            'name': 'Bloquear acesso remoto nao homologado',
            'description': 'Detecta AnyDesk, TeamViewer e ferramentas similares fora da equipe de TI.',
            'type': 'prohibited',
            'type_label': 'Proibido',
            'software': 'AnyDesk',
            'match_type': 'Contem',
            'match_type_value': 'contains',
            'publisher': 'AnyDesk Software GmbH',
            'version': 'Qualquer',
            'scope': 'Todos os endpoints',
            'scope_type': 'all',
            'scope_value': '',
            'target_endpoints': [],
            'target_endpoint_ids': [],
            'severity': 'security',
            'severity_label': 'Security',
            'status': 'active',
            'status_label': 'Ativa',
            'is_active': True,
            'monitor_only': False,
            'create_alert': True,
            'show_in_noc': True,
            'create_audit_event': True,
            'violations_open': 1,
            'exceptions_active': 1,
            'updated_at': '07/07 09:42',
            'behavior': ['Gerar alerta', 'Mostrar no NOC', 'Criar auditoria'],
        },
        {
            'id': '00000000-0000-4000-8000-000000000302',
            'name': 'Bitdefender obrigatorio',
            'description': 'Todos os endpoints Windows devem possuir Bitdefender ativo.',
            'type': 'required',
            'type_label': 'Obrigatorio',
            'software': 'Bitdefender Endpoint Security Tools',
            'match_type': 'Igual',
            'match_type_value': 'equals',
            'publisher': 'Bitdefender',
            'version': 'Qualquer',
            'scope': 'Todos os endpoints',
            'scope_type': 'all',
            'scope_value': '',
            'target_endpoints': [],
            'target_endpoint_ids': [],
            'severity': 'critical',
            'severity_label': 'Critical',
            'status': 'active',
            'status_label': 'Ativa',
            'is_active': True,
            'monitor_only': False,
            'create_alert': True,
            'show_in_noc': True,
            'create_audit_event': True,
            'violations_open': 1,
            'exceptions_active': 0,
            'updated_at': '07/07 10:05',
            'behavior': ['Gerar alerta', 'Mostrar no NOC', 'Criar auditoria'],
        },
        {
            'id': '00000000-0000-4000-8000-000000000303',
            'name': 'Ferramentas admin somente TI',
            'description': 'Monitora scanners e consoles administrativos em setores nao tecnicos.',
            'type': 'restricted',
            'type_label': 'Restrito',
            'software': 'Advanced IP Scanner',
            'match_type': 'Contem',
            'match_type_value': 'contains',
            'publisher': 'Famatech',
            'version': 'Qualquer',
            'scope': 'Departamento: TI',
            'scope_type': 'department',
            'scope_value': 'TI',
            'target_endpoints': [],
            'target_endpoint_ids': [],
            'severity': 'warning',
            'severity_label': 'Warning',
            'status': 'monitor_only',
            'status_label': 'Monitoramento',
            'is_active': True,
            'monitor_only': True,
            'create_alert': False,
            'show_in_noc': False,
            'create_audit_event': True,
            'violations_open': 0,
            'exceptions_active': 0,
            'updated_at': '06/07 17:31',
            'behavior': ['Somente monitoramento', 'Criar auditoria'],
        },
    ]
    violations = [
        {'id': '00000000-0000-4000-8000-000000000401', 'policy_id': policies[0]['id'], 'endpoint_id': str(endpoints[3].id), 'endpoint': endpoints[3].hostname, 'endpoint_url': f'/endpoints/{endpoints[3].id}/', 'software_name': 'AnyDesk', 'software_version': '8.0.9', 'publisher': 'AnyDesk Software GmbH', 'severity': 'security', 'severity_label': 'Security', 'status': 'open', 'status_label': 'Aberta', 'first_seen_at': '07/07/2026 09:21', 'last_seen_at': '07/07/2026 10:02', 'alert_id': '', 'alert_label': 'Sem alerta', 'resolution_reason': ''},
        {'id': '00000000-0000-4000-8000-000000000402', 'policy_id': policies[1]['id'], 'endpoint_id': str(endpoints[0].id), 'endpoint': endpoints[0].hostname, 'endpoint_url': f'/endpoints/{endpoints[0].id}/', 'software_name': 'Bitdefender Endpoint Security Tools', 'software_version': 'Ausente', 'publisher': 'Bitdefender', 'severity': 'critical', 'severity_label': 'Critical', 'status': 'open', 'status_label': 'Aberta', 'first_seen_at': '07/07/2026 08:51', 'last_seen_at': '07/07/2026 10:01', 'alert_id': '', 'alert_label': 'Sem alerta', 'resolution_reason': ''},
    ]
    exceptions = [
        {'id': '00000000-0000-4000-8000-000000000501', 'policy_id': policies[0]['id'], 'policy': policies[0]['name'], 'endpoint_id': str(endpoints[7].id), 'endpoint': endpoints[7].hostname, 'endpoint_label': f'{endpoints[7].hostname} - {endpoints[7].domain} - {endpoints[7].status}', 'reason': 'Suporte remoto homologado para TI', 'exception_type': 'temporary', 'exception_type_label': 'Temporaria', 'expires_at': '31/07/2026 23:59', 'expires_value': '2026-07-31', 'status': 'active', 'status_label': 'Ativa', 'created_by': 'Night Owl', 'created_at': '07/07 08:30'},
    ]
    logs = {
        policies[0]['id']: [{'time': '07/07 09:42', 'title': 'Politica criada', 'description': 'Regra de bloqueio cadastrada no preview.', 'severity': 'info', 'event_type': 'software_policy.created'}],
        policies[1]['id']: [{'time': '07/07 10:05', 'title': 'Violacao detectada', 'description': 'FIN-012 sem Bitdefender.', 'severity': 'critical', 'event_type': 'software_policy.violation'}],
    }
    return policies, violations, exceptions, logs


def redirect_back(request):
    return redirect(request.META.get('HTTP_REFERER') or 'alerts-list')


def actor_label(request):
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        return user.get_username()
    return ''


def create_alert_event(alert, event_type, message, request=None, metadata=None):
    AlertEvent.objects.create(
        alert=alert,
        event_type=event_type,
        message=message,
        metadata=metadata or {},
        actor=actor_label(request) if request else '',
    )


def audit_alert_action(request, alert, event_type, title, description='', severity=AuditEvent.SEVERITY_INFO, metadata=None):
    create_audit_event(
        event_type=event_type,
        title=title,
        description=description,
        severity=severity,
        endpoint=alert.endpoint,
        alert=alert,
        metadata={
            'alert_type': alert.alert_type,
            'alert_id': str(alert.id),
            'severity': alert.severity,
            'status': alert.status,
            **(metadata or {}),
        },
        request=request,
    )


def normalize_agent_server_base(url):
    value = (url or '').strip().rstrip('/')
    if value.endswith('/api/agent/heartbeat'):
        value = value[:-len('/api/agent/heartbeat')]
    elif value.endswith('/api/agent/heartbeat/'):
        value = value[:-len('/api/agent/heartbeat/')]
    return value.rstrip('/')


def agent_heartbeat_url_from_base(server_base):
    value = (server_base or '').strip().rstrip('/')
    if not value:
        return ''
    if value.endswith('/api/agent/heartbeat'):
        return value + '/'
    return f'{value}/api/agent/heartbeat/'


def normalize_powershell_path(path):
    value = str(path or '').strip().strip('"')
    if not value:
        return value
    while value.startswith('\\\\\\\\'):
        value = '\\\\' + value[4:]
    if value.startswith('\\\\'):
        return '\\\\' + value[2:].replace('\\\\', '\\')
    return value.replace('\\\\', '\\')


def build_agent_install_command(enrollment_token='', *, server_url=None, source_path=None, install_as_service=True, run_once=True, run_check=True, keep_scheduled_task_fallback=False, no_gui=False):
    server_base = normalize_agent_server_base(
        server_url
        or getattr(settings, 'NIGHTOWL_AGENT_PUBLIC_SERVER_URL', '')
        or getattr(settings, 'NIGHTOWL_PUBLIC_URL', '')
        or getattr(settings, 'NIGHTOWL_AGENT_HEARTBEAT_URL', 'http://192.168.101.242:8000/api/agent/heartbeat/')
    )
    source_path = source_path or getattr(settings, 'NIGHTOWL_AGENT_INSTALLER_URL', '')
    if not source_path:
        source_path = f'{server_base}/downloads/nightowl-agent/Install-NightOwlAgentDotNet.ps1'
    source_path = normalize_powershell_path(source_path)
    clean_source_path = str(source_path).rstrip('\\/')
    if clean_source_path.lower().endswith('.ps1') or clean_source_path.lower().startswith(('http://', 'https://')):
        installer_path = clean_source_path
    else:
        installer_path = f'{clean_source_path}\\Install-NightOwlAgentDotNet.ps1'
    flags = []
    if install_as_service:
        flags.append('-InstallAsService')
    if run_check:
        flags.append('-RunCheck')
    if keep_scheduled_task_fallback:
        flags.append('-KeepPowerShellAgent:$true')
    if no_gui:
        flags.append('-NoGui')
    token_value = str(enrollment_token or '').strip()
    if token_value and token_value != 'TOKEN_AQUI':
        flags.append(f'-EnrollmentToken "{token_value}"')
    flag_text = ' '.join(flags)
    if installer_path.lower().startswith(('http://', 'https://')):
        return (
            '$dir = "$env:TEMP\\NightOwlAgent"; '
            'New-Item -ItemType Directory -Force -Path $dir | Out-Null; '
            f'Invoke-WebRequest "{installer_path}" -OutFile "$dir\\Install-NightOwlAgentDotNet.ps1" -UseBasicParsing; '
            'powershell.exe -ExecutionPolicy Bypass -File "$dir\\Install-NightOwlAgentDotNet.ps1" '
            f'-ServerUrl "{server_base}" '
            f'{flag_text}'
        )
    return (
        'powershell.exe -ExecutionPolicy Bypass '
        f'-File "{installer_path}" '
        f'-ServerUrl "{server_base}" '
        f'{flag_text}'
    )


def enrollment_token_state(token):
    if not token.is_active:
        return {'key': 'critical', 'label': 'Revogado'}
    if token.is_expired:
        return {'key': 'unknown', 'label': 'Expirado'}
    if token.usage_limit_reached:
        return {'key': 'warning', 'label': 'Esgotado'}
    return {'key': 'online', 'label': 'Ativo'}


def software_display_name(software):
    return str((software or {}).get('name') or '').strip()


def software_publisher(software):
    return str((software or {}).get('publisher') or '').strip()


def software_version_value(software):
    return str((software or {}).get('version') or '').strip()


def latest_snapshot_rows():
    rows = []
    endpoints = AgentMachine.objects.prefetch_related('inventory_snapshots').order_by('hostname', 'domain')
    for endpoint in endpoints:
        snapshot = endpoint.inventory_snapshots.order_by('-received_at').first()
        if snapshot:
            rows.append((endpoint, snapshot))
    return rows


def build_software_inventory():
    inventory = {}
    total_installs = 0
    endpoints_with_inventory = 0

    for endpoint, snapshot in latest_snapshot_rows():
        endpoints_with_inventory += 1
        for software in snapshot.installed_software or []:
            name = software_display_name(software)
            if not name:
                continue
            publisher = software_publisher(software)
            version = software_version_value(software)
            classification = classify_software_catalog(software)
            key = f'{normalize_key(name)}::{normalize_key(publisher)}'
            item = inventory.setdefault(key, {
                'key': key,
                'name': name,
                'publisher': publisher,
                'category': classification['category'],
                'category_label': classification['category_label'],
                'risk_level': classification['risk_level'],
                'risk_label': classification['risk_label'],
                'is_sensitive': classification['is_sensitive'],
                'install_count': 0,
                'endpoint_ids': set(),
                'versions': set(),
                'latest_seen_at': snapshot.received_at,
                'endpoints': [],
            })

            item['install_count'] += 1
            total_installs += 1
            item['endpoint_ids'].add(endpoint.id)
            if version:
                item['versions'].add(version)
            if snapshot.received_at > item['latest_seen_at']:
                item['latest_seen_at'] = snapshot.received_at
            item['endpoints'].append({
                'endpoint': endpoint,
                'version': version,
                'snapshot': snapshot,
            })

    rows = []
    for item in inventory.values():
        endpoint_count = len(item['endpoint_ids'])
        versions = sorted(item['versions'])
        rows.append({
            **item,
            'endpoint_count': endpoint_count,
            'versions': versions,
            'versions_display': ', '.join(versions[:4]) + (' +' + str(len(versions) - 4) if len(versions) > 4 else '') if versions else '—',
            'example_endpoints': sorted(item['endpoints'], key=lambda row: row['endpoint'].hostname)[:4],
        })

    risk_order = {'security': 0, 'warning': 1, 'info': 2, 'ok': 3, 'unknown': 4}
    category_order = {'remote_access': 0, 'admin_network': 1, 'security': 2, 'development': 3, 'browser': 4, 'office': 5, 'utility': 6, 'unknown': 7}
    rows.sort(key=lambda row: (
        risk_order.get(row['risk_level'], 9),
        category_order.get(row['category'], 9),
        -row['endpoint_count'],
        row['name'].lower(),
    ))
    return rows, {
        'unique_count': len(rows),
        'install_count': total_installs,
        'endpoints_with_inventory': endpoints_with_inventory,
        'remote_access_count': sum(1 for row in rows if row['category'] == 'remote_access'),
        'admin_network_count': sum(1 for row in rows if row['category'] == 'admin_network'),
        'security_count': sum(1 for row in rows if row['category'] == 'security'),
        'unknown_count': sum(1 for row in rows if row['category'] == 'unknown'),
    }


def software_row_matches(row, filters):
    q = filters['q'].lower()
    if q:
        haystack = ' '.join([
            row['name'],
            row['publisher'],
            ' '.join(row['versions']),
            ' '.join(item['endpoint'].hostname for item in row['endpoints']),
        ]).lower()
        if q not in haystack:
            return False
    if filters['category'] != 'all' and row['category'] != filters['category']:
        return False
    if filters['risk'] != 'all' and row['risk_level'] != filters['risk']:
        return False
    if filters['publisher'] and row['publisher'] != filters['publisher']:
        return False
    if filters['version'] and filters['version'] not in row['versions']:
        return False
    if filters['endpoint']:
        endpoint_filter = filters['endpoint'].lower()
        if not any(endpoint_filter in item['endpoint'].hostname.lower() for item in row['endpoints']):
            return False
    if filters['sensitive'] == 'yes' and not row['is_sensitive']:
        return False
    if filters['sensitive'] == 'no' and row['is_sensitive']:
        return False
    return True


def format_bytes_gb(value):
    if value in (None, ''):
        return None
    try:
        return round(int(value) / (1024 ** 3), 1)
    except (TypeError, ValueError):
        return None


def format_uptime(seconds):
    if seconds in (None, ''):
        return None
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None

    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    if days:
        return f'{days}d {hours}h'
    if hours:
        return f'{hours}h {minutes}min'
    return f'{minutes}min'


def ensure_dict(value):
    return value if isinstance(value, dict) else {}


def ensure_list(value):
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def safe_get(value, key, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return default


def build_disk_rows(disks):
    rows = []
    for disk in disks or []:
        disk = ensure_dict(disk)
        if not disk:
            continue
        size = disk.get('size_bytes') or disk.get('total_bytes') or 0
        free = disk.get('free_bytes') or 0
        try:
            size = int(size)
            free = int(free)
        except (TypeError, ValueError):
            size = 0
            free = 0

        used_percent = 0
        if size > 0:
            used_percent = round(((size - free) / size) * 100)

        if used_percent >= 90:
            level = 'critical'
        elif used_percent >= 80:
            level = 'warning'
        else:
            level = 'normal'

        rows.append({
            'name': disk.get('name') or disk.get('letter') or disk.get('device_id') or '-',
            'label': disk.get('label') or disk.get('volume_name') or '',
            'filesystem': disk.get('filesystem') or '',
            'drive_type': disk.get('drive_type') or '',
            'size_gb': format_bytes_gb(size),
            'free_gb': format_bytes_gb(free),
            'used_percent': used_percent,
            'level': level,
            'is_system_drive': disk.get('is_system_drive'),
            'bitlocker_status': disk.get('bitlocker_status') or '',
            'health_status': disk.get('health_status') or '',
        })
    return rows


def get_primary_disk(disks):
    rows = build_disk_rows(disks)
    if not rows:
        return {
            'name': '-',
            'summary': '-',
            'used_percent': None,
            'level': 'unknown',
            'has_data': False,
        }

    primary = next((disk for disk in rows if str(disk['name']).upper().startswith('C:')), rows[0])
    total = f"{primary['size_gb']} GB" if primary['size_gb'] is not None else '-'
    free = f"{primary['free_gb']} GB" if primary['free_gb'] is not None else '-'
    return {
        **primary,
        'summary': f"{primary['name']} · {primary['used_percent']}% usado · {free} livre de {total}",
        'has_data': True,
    }


def defender_state(defender_status):
    status = ensure_dict(defender_status)
    enabled = status.get('enabled') is True
    realtime = status.get('real_time_protection_enabled') is True

    if enabled and realtime:
        return {
            'label': 'Protegido',
            'class': 'security-good',
            'detail': 'Defender ativo com prote&ccedil;&atilde;o em tempo real.',
        }
    if status:
        return {
            'label': 'Aten&ccedil;&atilde;o',
            'class': 'security-warning',
            'detail': 'Defender ausente ou prote&ccedil;&atilde;o parcial.',
        }
    return {
        'label': 'Indispon&iacute;vel',
        'class': 'security-critical',
        'detail': 'Status de seguran&ccedil;a n&atilde;o recebido.',
    }


def software_name(software):
    return str(safe_get(software, 'name', '') or '')


def software_text(software):
    item = ensure_dict(software)
    return ' '.join([
        str(item.get('display_name') or item.get('name') or ''),
        str(item.get('publisher') or ''),
        str(item.get('display_version') or item.get('version') or ''),
    ]).lower()


def has_software_match(installed_software, terms):
    for software in installed_software or []:
        text = software_text(software)
        if any(term in text for term in terms):
            return True
    return False


def classify_software(software):
    text = software_text(software)
    if 'microsoft' in text:
        return 'microsoft'
    if any(term in text for term in REMOTE_ACCESS_TERMS):
        return 'remote'
    if any(term in text for term in SECURITY_TERMS):
        return 'security'
    if any(term in text for term in ADMIN_TOOL_TERMS):
        return 'admin'
    return 'other'


def build_software_rows(installed_software):
    rows = []
    for software in installed_software or []:
        software = ensure_dict(software)
        if not software:
            continue
        classification = classify_software_catalog(software)
        chip_category = classification['category']
        if chip_category == 'remote_access':
            chip_category = 'remote'
        elif chip_category == 'admin_network':
            chip_category = 'admin'
        elif 'microsoft' in software_text(software):
            chip_category = 'microsoft'
        rows.append({
            'name': software.get('display_name') or software.get('name') or '',
            'version': software.get('display_version') or software.get('version') or '',
            'publisher': software.get('publisher') or '',
            'category': chip_category,
            'category_label': classification['category_label'],
            'risk_level': classification['risk_level'],
            'risk_label': classification['risk_label'],
            'installed_at': software.get('install_date') or software.get('installed_at') or '',
            'architecture': software.get('architecture') or '',
            'source': software.get('source') or software.get('registry_hive') or '',
            'install_location': software.get('install_location') or '',
            'uninstall_string': software.get('uninstall_string') or '',
        })
    return rows


def detail_defender_state(defender_status, installed_software):
    state = defender_state(defender_status)
    has_security = has_software_match(installed_software, SECURITY_TERMS)
    has_bitdefender = has_software_match(installed_software, ['bitdefender'])

    status = ensure_dict(defender_status)
    defender_ok = status.get('enabled') is True and status.get('real_time_protection_enabled') is True

    if defender_ok:
        return {
            **state,
            'key': 'ok',
        }

    if has_bitdefender:
        return {
            'label': 'Protegido por terceiro',
            'class': 'security-good',
            'detail': 'Defender n&atilde;o identificado, mas Bitdefender foi detectado nos softwares instalados.',
            'key': 'ok',
        }

    if has_security:
        return {
            'label': 'Antiv&iacute;rus detectado',
            'class': 'security-warning',
            'detail': 'Defender n&atilde;o identificado, mas outro software de seguran&ccedil;a foi detectado.',
            'key': 'attention',
        }

    return {
        **state,
        'key': 'attention' if status else 'unknown',
    }


def calculate_health(endpoint, primary_disk, defender):
    if endpoint.last_seen_at is None and not primary_disk.get('has_data') and defender.get('key') == 'unknown':
        return {
            'score': None,
            'label': 'Dados insuficientes',
            'class': 'health-unknown',
        }

    score = 0
    if endpoint.status == AgentMachine.STATUS_ONLINE:
        score += 40
    elif endpoint.status == AgentMachine.STATUS_UNKNOWN:
        score += 20
    else:
        score += 10

    disk_level = primary_disk.get('level')
    if disk_level == 'normal':
        score += 30
    elif disk_level == 'warning':
        score += 15
    elif disk_level == 'critical':
        score += 0
    else:
        score += 12

    defender_key = defender.get('key')
    if defender_key == 'ok':
        score += 30
    elif defender_key == 'attention':
        score += 10
    else:
        score += 14

    if score >= 80:
        label = 'Saud&aacute;vel'
        css_class = 'health-good'
    elif score >= 55:
        label = 'Aten&ccedil;&atilde;o'
        css_class = 'health-warning'
    else:
        label = 'Cr&iacute;tico'
        css_class = 'health-critical'

    return {
        'score': score,
        'label': label,
        'class': css_class,
    }


def build_smart_badges(endpoint, primary_disk, defender, installed_software):
    badges = [
        {
            'label': endpoint.get_status_display() or 'Unknown',
            'class': f'status-{endpoint.status}',
        },
    ]

    if primary_disk.get('has_data'):
        disk_level = primary_disk.get('level')
        badges.append({
            'label': f"Disco {disk_level}",
            'class': f'disk-pill-{disk_level}',
        })

    defender_key = defender.get('key', 'unknown')
    defender_label = 'Defender OK' if defender_key == 'ok' else 'Defender Aten&ccedil;&atilde;o' if defender_key == 'attention' else 'Defender desconhecido'
    badges.append({
        'label': defender_label,
        'class': f'defender-{defender_key}',
    })

    if has_software_match(installed_software, REMOTE_ACCESS_TERMS):
        badges.append({
            'label': 'Acesso remoto detectado',
            'class': 'badge-security',
        })

    if has_software_match(installed_software, ADMIN_TOOL_TERMS):
        badges.append({
            'label': 'Admin/Rede detectado',
            'class': 'badge-admin',
        })

    return badges


def defender_filter_state(defender_status):
    status = ensure_dict(defender_status)
    if not status:
        return 'unknown'
    if status.get('enabled') is True and status.get('real_time_protection_enabled') is True:
        return 'ok'
    return 'attention'


def build_endpoint_row(endpoint):
    snapshot = get_endpoint_detail_snapshot(endpoint)
    primary_disk = get_primary_disk(snapshot.disks if snapshot else [])
    defender_key = defender_filter_state(snapshot.defender_status if snapshot else {})
    software_count = len(ensure_list(snapshot.installed_software if snapshot else [])) if snapshot else None
    os_name = endpoint.os_name or (snapshot.os_name if snapshot else '')
    domain = endpoint.domain or (snapshot.domain if snapshot else '')
    logged_user = endpoint.last_logged_user or (snapshot.logged_user if snapshot else '')
    primary_ip = endpoint.last_ip

    if not primary_ip and snapshot and snapshot.ips:
        primary_ip = snapshot.ips[0]

    endpoint_type = infer_endpoint_type(endpoint.hostname, os_name)
    sector = infer_endpoint_sector(endpoint.hostname, domain, logged_user)
    health_basis = SimpleNamespace(status=endpoint.status, last_seen_at=endpoint.last_seen_at)
    defender = detail_defender_state(snapshot.defender_status if snapshot else {}, snapshot.installed_software if snapshot else [])
    health = calculate_health(health_basis, primary_disk, defender)
    attention = endpoint_attention_summary(endpoint, primary_disk, defender_key, health, snapshot)

    return {
        'endpoint': endpoint,
        'snapshot': snapshot,
        'hostname': endpoint.hostname or '',
        'domain': domain or '',
        'logged_user': logged_user or '',
        'sector': sector,
        'tag': sector,
        'endpoint_type': endpoint_type,
        'endpoint_type_label': endpoint_type_label(endpoint_type),
        'primary_ip': primary_ip or '',
        'os_name': os_name or '',
        'last_seen_at': endpoint.last_seen_at,
        'primary_disk': primary_disk,
        'defender_key': defender_key,
        'software_count': software_count,
        'has_attention': defender_key == 'attention' or primary_disk['level'] in ('warning', 'critical'),
        'health': health,
        'attention': attention,
        'agent_version': endpoint.agent_version,
        'agent_version_state': agent_version_state(endpoint.agent_version, latest_agent_version()),
    }


def row_matches_query(row, query):
    if not query:
        return True

    haystack = ' '.join([
        row['hostname'],
        row['domain'],
        row['logged_user'],
        row.get('sector', ''),
        row.get('tag', ''),
        row.get('endpoint_type_label', ''),
        str(row['primary_ip']),
        row['os_name'],
    ]).lower()
    return query.lower() in haystack


def infer_endpoint_type(hostname, os_name):
    text = f'{hostname or ""} {os_name or ""}'.casefold()
    if any(term in text for term in ['server', 'srv', 'dc-', 'erp', 'print']):
        return 'server'
    if any(term in text for term in ['nb', 'notebook', 'laptop']):
        return 'notebook'
    return 'workstation'


def endpoint_type_label(value):
    return {
        'server': 'Servidor',
        'workstation': 'Workstation',
        'notebook': 'Notebook',
    }.get(value or '', 'Workstation')


def infer_endpoint_sector(hostname, domain, logged_user):
    hostname = (hostname or '').upper()
    prefixes = {
        'FIN': 'Financeiro',
        'JUR': 'Juridico',
        'DIR': 'Diretoria',
        'REC': 'Recepcao',
        'COM': 'Comercial',
        'TI': 'TI',
        'SRV': 'Infraestrutura',
    }
    for prefix, sector in prefixes.items():
        if hostname.startswith(prefix):
            return sector
    if logged_user:
        return 'Usuario final'
    return domain or 'Sem setor'


def endpoint_attention_summary(endpoint, primary_disk, defender_key, health, snapshot=None):
    if endpoint.status == AgentMachine.STATUS_OFFLINE:
        if endpoint.last_seen_at:
            delta = timezone.now() - endpoint.last_seen_at
            hours = max(1, int(delta.total_seconds() // 3600))
            return {
                'label': f'Offline ha {hours}h',
                'level': 'critical',
                'key': 'offline',
            }
        return {'label': 'Offline sem heartbeat', 'level': 'critical', 'key': 'offline'}
    if defender_key == 'attention':
        return {'label': 'Defender critico', 'level': 'critical', 'key': 'security'}
    if primary_disk.get('level') == 'critical':
        return {'label': f"Disco {primary_disk.get('used_percent', 0)}%", 'level': 'critical', 'key': 'disk'}
    if primary_disk.get('level') == 'warning':
        return {'label': f"Disco {primary_disk.get('used_percent', 0)}%", 'level': 'warning', 'key': 'disk'}
    if endpoint.agent_version and agent_version_state(endpoint.agent_version, latest_agent_version()) == 'outdated':
        return {'label': 'Agente desatualizado', 'level': 'warning', 'key': 'agent'}
    if defender_key == 'unknown':
        return {'label': 'Inventario vencido', 'level': 'muted', 'key': 'inventory'}
    score = health.get('score')
    if score is not None and score < 70:
        return {'label': 'Saude degradada', 'level': 'warning', 'key': 'health'}
    return {'label': 'Sem acao imediata', 'level': 'normal', 'key': 'ok'}


def endpoint_filters_from_request(request):
    return {
        'q': request.GET.get('q', '').strip(),
        'status': request.GET.get('status', '').strip(),
        'os': request.GET.get('os', '').strip(),
        'domain': request.GET.get('domain', '').strip(),
        'defender': request.GET.get('defender', '').strip(),
        'disk': request.GET.get('disk', '').strip(),
        'type': request.GET.get('type', '').strip(),
        'sector': request.GET.get('sector', '').strip(),
        'agent': request.GET.get('agent', '').strip(),
        'attention': request.GET.get('attention', '').strip(),
        'quick': request.GET.get('quick', 'all').strip() or 'all',
    }


def endpoint_matches_quick_filter(row, quick_filter):
    if quick_filter in ('', 'all'):
        return True
    if quick_filter == 'critical':
        return row.get('attention', {}).get('level') == 'critical' or (row.get('health', {}).get('score') or 100) < 55
    if quick_filter == 'offline':
        return row['endpoint'].status == AgentMachine.STATUS_OFFLINE
    if quick_filter == 'servers':
        return row.get('endpoint_type') == 'server'
    if quick_filter == 'workstations':
        return row.get('endpoint_type') == 'workstation'
    if quick_filter == 'agent_outdated':
        return row.get('agent_version_state') == 'outdated'
    if quick_filter == 'security':
        return row.get('defender_key') in ('attention', 'unknown')
    if quick_filter == 'disk_full':
        return row.get('primary_disk', {}).get('level') == 'critical'
    return True


def filter_endpoint_rows(rows, filters):
    filtered_rows = []
    for row in rows:
        if not row_matches_query(row, filters['q']):
            continue
        if filters['status'] and row['endpoint'].status != filters['status']:
            continue
        if filters['os'] and row['os_name'] != filters['os']:
            continue
        if filters['domain'] and row['domain'] != filters['domain']:
            continue
        if filters['defender'] and row['defender_key'] != filters['defender']:
            continue
        if filters['disk'] and row['primary_disk']['level'] != filters['disk']:
            continue
        if filters['type'] and row.get('endpoint_type') != filters['type']:
            continue
        if filters['sector'] and row.get('sector') != filters['sector']:
            continue
        if filters['agent'] and row.get('agent_version_state') != filters['agent']:
            continue
        if filters['attention'] == '1' and not row.get('has_attention'):
            continue
        if not endpoint_matches_quick_filter(row, filters['quick']):
            continue
        filtered_rows.append(row)
    return filtered_rows


def endpoint_summary_counts(rows):
    return {
        'total_endpoints': len(rows),
        'online_count': sum(row['endpoint'].status == AgentMachine.STATUS_ONLINE for row in rows),
        'offline_count': sum(row['endpoint'].status == AgentMachine.STATUS_OFFLINE for row in rows),
        'unknown_count': sum(row['endpoint'].status == AgentMachine.STATUS_UNKNOWN for row in rows),
        'attention_count': sum(1 for row in rows if row.get('has_attention')),
        'offline_critical_count': sum(row['endpoint'].status == AgentMachine.STATUS_OFFLINE and row.get('attention', {}).get('level') == 'critical' for row in rows),
        'agent_outdated_count': sum(row.get('agent_version_state') == 'outdated' for row in rows),
        'security_attention_count': sum(row.get('defender_key') in ('attention', 'unknown') for row in rows),
        'disk_critical_count': sum(row.get('primary_disk', {}).get('level') == 'critical' for row in rows),
    }


def endpoint_filter_options(rows):
    return {
        'os_options': sorted({row['os_name'] for row in rows if row['os_name']}),
        'domain_options': sorted({row['domain'] for row in rows if row['domain']}),
        'sector_options': sorted({row.get('sector') for row in rows if row.get('sector')}),
        'type_options': [
            ('server', 'Servidores'),
            ('workstation', 'Workstations'),
            ('notebook', 'Notebooks'),
        ],
        'status_options': AgentMachine.STATUS_CHOICES,
    }


def build_endpoint_health_breakdown(endpoint, primary_disk, defender, endpoint_alerts=None, snapshot=None):
    alerts_count = len(endpoint_alerts or [])
    connectivity_score = 100 if endpoint.status == AgentMachine.STATUS_ONLINE else 55 if endpoint.status == AgentMachine.STATUS_UNKNOWN else 10
    agent_state = agent_version_state(endpoint.agent_version, latest_agent_version())
    agent_score = 100 if agent_state == 'current' else 55 if agent_state == 'outdated' else 25
    security_score = 100 if defender.get('key') == 'ok' else 45 if defender.get('key') == 'attention' else 30
    disk_level = primary_disk.get('level')
    disk_score = 100 if disk_level == 'normal' else 60 if disk_level == 'warning' else 25 if disk_level == 'critical' else 40
    inventory_score = 100 if snapshot and getattr(snapshot, 'received_at', None) else 35
    alerts_score = max(0, 100 - (alerts_count * 22))
    return [
        {'label': 'Conectividade', 'score': connectivity_score, 'level': 'good' if connectivity_score >= 80 else 'critical' if connectivity_score < 40 else 'warning'},
        {'label': 'Agente', 'score': agent_score, 'level': 'good' if agent_score >= 80 else 'critical' if agent_score < 40 else 'warning'},
        {'label': 'Seguranca', 'score': security_score, 'level': 'good' if security_score >= 80 else 'critical' if security_score < 50 else 'warning'},
        {'label': 'Disco', 'score': disk_score, 'level': 'good' if disk_score >= 80 else 'critical' if disk_score < 40 else 'warning'},
        {'label': 'Inventario', 'score': inventory_score, 'level': 'good' if inventory_score >= 80 else 'warning'},
        {'label': 'Alertas ativos', 'score': alerts_score, 'level': 'good' if alerts_score >= 80 else 'critical' if alerts_score < 50 else 'warning'},
    ]


def build_endpoint_patch_rows(endpoint):
    now = timezone.now()
    return [
        {
            'name': 'Windows Update - Qualidade',
            'status': 'Pendente' if endpoint.status != AgentMachine.STATUS_OFFLINE else 'Nao coletado',
            'severity': 'warning' if endpoint.status != AgentMachine.STATUS_OFFLINE else 'muted',
            'installed_at': now - timedelta(days=11),
        },
        {
            'name': 'Microsoft Defender Platform',
            'status': 'Atualizado' if endpoint.status == AgentMachine.STATUS_ONLINE else 'Verificar',
            'severity': 'success' if endpoint.status == AgentMachine.STATUS_ONLINE else 'warning',
            'installed_at': now - timedelta(days=3),
        },
        {
            'name': 'Drivers e firmware',
            'status': 'Mock preview',
            'severity': 'info',
            'installed_at': None,
        },
    ]


def build_endpoint_task_rows(endpoint):
    now = timezone.now()
    return [
        {'name': 'Coleta de inventario', 'status': 'completed', 'started_at': now - timedelta(minutes=18), 'source': 'Agente'},
        {'name': 'Verificacao de disco', 'status': 'queued', 'started_at': now - timedelta(minutes=4), 'source': 'Operador'},
        {'name': 'Verificacao Defender', 'status': 'preview', 'started_at': None, 'source': 'Mock'},
    ]


def _pulse_value(value, fallback=''):
    return value if value not in (None, '') else fallback


def _pulse_sector_from_endpoint(row):
    hostname = (row.get('hostname') or '').upper()
    domain = row.get('domain') or 'CONTROL'
    prefixes = {
        'FIN': 'Financeiro',
        'JUR': 'Juridico',
        'DIR': 'Diretoria',
        'REC': 'Recepcao',
        'COM': 'Comercial',
        'TI': 'TI',
        'SRV': 'Infraestrutura',
    }
    for prefix, sector in prefixes.items():
        if hostname.startswith(prefix):
            return sector
    return domain or 'Operacao'


def _pulse_resource_label(row):
    disk = row.get('primary_disk') or {}
    defender_key = row.get('defender_key')
    if defender_key == 'attention':
        return 'Sem AV', 'critical'
    if disk.get('level') == 'critical':
        return disk.get('summary') or 'Disco critico', 'critical'
    if disk.get('level') == 'warning':
        return disk.get('summary') or 'Disco em atencao', 'warning'
    if defender_key == 'unknown':
        return 'AV desconhecido', 'warning'
    return 'Normal', 'ok'


def _pulse_health(row, alerts):
    status = getattr(row['endpoint'], 'status', AgentMachine.STATUS_UNKNOWN)
    critical_alerts = sum(alert.severity == EndpointAlert.SEVERITY_CRITICAL for alert in alerts)
    warning_alerts = sum(alert.severity in {EndpointAlert.SEVERITY_WARNING, EndpointAlert.SEVERITY_SECURITY} for alert in alerts)
    disk = row.get('primary_disk') or {}
    defender_key = row.get('defender_key')
    score = 94
    if status == AgentMachine.STATUS_OFFLINE:
        score -= 42
    elif status == AgentMachine.STATUS_UNKNOWN:
        score -= 26
    score -= critical_alerts * 24
    score -= warning_alerts * 11
    if disk.get('level') == 'critical':
        score -= 18
    elif disk.get('level') == 'warning':
        score -= 9
    if defender_key == 'attention':
        score -= 20
    elif defender_key == 'unknown':
        score -= 10
    score = max(8, min(100, score))
    if score < 45:
        return score, 'critical', 'Critica'
    if score < 72:
        return score, 'warning', 'Degradada'
    return score, 'ok', 'Saudavel'


def _pulse_recent_label(value):
    if not value:
        return 'Sem contato'
    delta = timezone.now() - value
    minutes = max(1, int(delta.total_seconds() // 60))
    if minutes < 60:
        return f'ha {minutes} min'
    hours = minutes // 60
    if hours < 24:
        return f'ha {hours}h'
    return f'ha {hours // 24}d'


def build_pulse_context(endpoint_rows, alerts, events):
    alerts_by_endpoint = {}
    for alert in alerts:
        alerts_by_endpoint.setdefault(str(alert.endpoint_id), []).append(alert)

    pulse_rows = []
    stale_cutoff = timezone.now() - timedelta(hours=24)
    for index, row in enumerate(endpoint_rows):
        endpoint = row['endpoint']
        endpoint_alerts = alerts_by_endpoint.get(str(endpoint.id), [])
        resource_label, resource_level = _pulse_resource_label(row)
        health_score, health_level, health_label = _pulse_health(row, endpoint_alerts)
        os_name = row.get('os_name') or ''
        hostname = row.get('hostname') or endpoint.hostname or ''
        status = endpoint.status or AgentMachine.STATUS_UNKNOWN
        is_server = 'server' in os_name.lower() or hostname.upper().startswith(('SRV', 'DC'))
        last_seen_at = row.get('last_seen_at')
        is_stale = not last_seen_at or last_seen_at < stale_cutoff or status == AgentMachine.STATUS_UNKNOWN
        disk = row.get('primary_disk') or {}
        disk_used = disk.get('used_percent') or (93 if disk.get('level') == 'critical' else 82 if disk.get('level') == 'warning' else 48)
        no_av = row.get('defender_key') == 'attention'
        cpu = 82 if health_level == 'critical' else 66 if health_level == 'warning' else 34 + (index * 5) % 22
        ram = 88 if resource_level == 'critical' and 'Disco' not in resource_label else 63 + (index * 7) % 18
        pulse_rows.append({
            **row,
            'id': endpoint.id,
            'status': status,
            'status_label': endpoint.get_status_display(),
            'sector': _pulse_sector_from_endpoint(row),
            'is_server': is_server,
            'is_workstation': not is_server,
            'alert_count': len(endpoint_alerts),
            'critical_count': sum(alert.severity == EndpointAlert.SEVERITY_CRITICAL for alert in endpoint_alerts),
            'alert_titles': ' | '.join(alert.title for alert in endpoint_alerts[:3]),
            'health_score': health_score,
            'health_level': health_level,
            'health_label': health_label,
            'resource_label': resource_label,
            'resource_level': resource_level,
            'no_av': no_av,
            'disk_critical': disk.get('level') == 'critical',
            'stale_inventory': is_stale,
            'last_seen_label': _pulse_recent_label(last_seen_at),
            'cpu_percent': min(99, cpu),
            'ram_percent': min(99, ram),
            'disk_percent': min(99, disk_used),
        })

    status_counts = {
        'online': sum(row['status'] == AgentMachine.STATUS_ONLINE for row in pulse_rows),
        'offline': sum(row['status'] == AgentMachine.STATUS_OFFLINE for row in pulse_rows),
        'unknown': sum(row['status'] == AgentMachine.STATUS_UNKNOWN or row['stale_inventory'] for row in pulse_rows),
    }
    critical_rows = [row for row in pulse_rows if row['critical_count'] or row['health_level'] == 'critical']
    open_attention = [alert for alert in alerts if alert.status == EndpointAlert.STATUS_OPEN]
    health_score = round((sum(row['health_score'] for row in pulse_rows) / len(pulse_rows)) if pulse_rows else 100)

    severity_rank = {
        EndpointAlert.SEVERITY_CRITICAL: 0,
        EndpointAlert.SEVERITY_SECURITY: 1,
        EndpointAlert.SEVERITY_WARNING: 2,
        EndpointAlert.SEVERITY_INFO: 3,
    }
    sorted_alerts = sorted(
        open_attention,
        key=lambda alert: (severity_rank.get(alert.severity, 9), alert.last_seen_at or timezone.now()),
    )
    recommended = []
    for alert in sorted_alerts[:6]:
        endpoint = alert.endpoint
        impact = 'Interrompe atendimento ou servico critico' if alert.severity == EndpointAlert.SEVERITY_CRITICAL else 'Pode degradar a operacao se nao tratado'
        if alert.alert_type == 'security_antivirus':
            impact = 'Endpoint exposto sem protecao homologada'
        elif alert.alert_type == 'disk_low':
            impact = 'Risco de parada por falta de espaco'
        elif alert.alert_type == 'endpoint_offline':
            impact = 'Dispositivo fora do monitoramento'
        recommended.append({
            'severity': alert.severity,
            'severity_label': alert.get_severity_display(),
            'endpoint': endpoint,
            'title': alert.title,
            'description': alert.description,
            'impact': impact,
            'age_at': alert.last_seen_at,
            'alert': alert,
        })

    return {
        'pulse_endpoint_rows': pulse_rows,
        'pulse_alerts': sorted_alerts[:10],
        'pulse_events': list(events)[:12],
        'pulse_recommended': recommended,
        'pulse_health': {
            'score': health_score,
            'online': status_counts['online'],
            'offline': status_counts['offline'],
            'critical': len(critical_rows),
            'attention': len(open_attention),
            'unknown': status_counts['unknown'],
        },
    }


def index(request):
    now = timezone.now()
    if request.GET.get('mock') == '1' or not AgentMachine.objects.exists():
        endpoints = mock_rmm_endpoints()
        all_alerts = mock_rmm_alerts(endpoints)
        alerts = [alert for alert in all_alerts if alert.status == EndpointAlert.STATUS_OPEN]
        endpoint_rows = mock_endpoint_rows(endpoints)
        events = mock_rmm_events(endpoints, all_alerts)
        pulse_context = build_pulse_context(endpoint_rows, alerts, events)
        context = {
            'total_endpoints': len(endpoints),
            'online_count': sum(endpoint.status == AgentMachine.STATUS_ONLINE for endpoint in endpoints),
            'offline_count': sum(endpoint.status == AgentMachine.STATUS_OFFLINE for endpoint in endpoints),
            'unknown_count': sum(endpoint.status == AgentMachine.STATUS_UNKNOWN for endpoint in endpoints),
            'endpoints': endpoints,
            'open_alerts_count': len(alerts),
            'critical_alerts_count': sum(alert.severity == EndpointAlert.SEVERITY_CRITICAL for alert in alerts),
            'recent_alerts': sorted(alerts, key=lambda item: item.last_seen_at, reverse=True)[:8],
            'using_mock_rmm_data': True,
            **pulse_context,
        }
        return render(request, 'dashboard/index.html', context)

    status_counts = {
        item['status']: item['count']
        for item in AgentMachine.objects.values('status').annotate(count=Count('id'))
    }
    endpoints = AgentMachine.objects.order_by('hostname', 'domain')
    open_alerts = EndpointAlert.objects.filter(status=EndpointAlert.STATUS_OPEN).filter(
        Q(muted_until__isnull=True) | Q(muted_until__lte=now),
    )
    recent_alerts = open_alerts.select_related('endpoint').order_by('-last_seen_at')[:8]
    endpoint_rows = [build_endpoint_row(endpoint) for endpoint in endpoints]
    events = AuditEvent.objects.select_related('endpoint', 'alert').order_by('-created_at')[:12]
    pulse_context = build_pulse_context(endpoint_rows, list(open_alerts.select_related('endpoint')), events)

    context = {
        'total_endpoints': AgentMachine.objects.count(),
        'online_count': status_counts.get(AgentMachine.STATUS_ONLINE, 0),
        'offline_count': status_counts.get(AgentMachine.STATUS_OFFLINE, 0),
        'unknown_count': status_counts.get(AgentMachine.STATUS_UNKNOWN, 0),
        'endpoints': endpoints,
        'open_alerts_count': open_alerts.count(),
        'critical_alerts_count': open_alerts.filter(severity=EndpointAlert.SEVERITY_CRITICAL).count(),
        'recent_alerts': recent_alerts,
        **pulse_context,
    }
    return render(request, 'dashboard/index.html', context)


def alerts_list(request):
    now = timezone.now()
    if request.GET.get('mock') == '1' or not EndpointAlert.objects.exists():
        endpoints = mock_rmm_endpoints()
        all_alerts = mock_rmm_alerts(endpoints)
        if len(all_alerts) > 5:
            all_alerts[5].muted_until = now + timedelta(hours=4)
        status_filter = request.GET.get('status', EndpointAlert.STATUS_OPEN).strip() or EndpointAlert.STATUS_OPEN
        severity_filter = request.GET.get('severity', 'all').strip() or 'all'
        type_filter = request.GET.get('type', 'all').strip() or 'all'
        period_filter = request.GET.get('period', 'all').strip() or 'all'
        query = request.GET.get('q', '').strip().casefold()
        alerts = all_alerts
        if status_filter == 'muted':
            alerts = [alert for alert in alerts if alert.is_muted]
        elif status_filter != 'all':
            alerts = [alert for alert in alerts if alert.status == status_filter]
        if severity_filter != 'all':
            alerts = [alert for alert in alerts if alert.severity == severity_filter]
        if type_filter != 'all':
            alerts = [alert for alert in alerts if alert.alert_type == type_filter]
        if period_filter == 'today':
            alerts = [alert for alert in alerts if timezone.localtime(alert.last_seen_at).date() == timezone.localdate()]
        elif period_filter == '24h':
            alerts = [alert for alert in alerts if alert.last_seen_at >= now - timedelta(hours=24)]
        elif period_filter == '7d':
            alerts = [alert for alert in alerts if alert.last_seen_at >= now - timedelta(days=7)]
        if query:
            alerts = [
                alert for alert in alerts
                if query in ' '.join([
                    alert.title,
                    alert.description,
                    alert.endpoint.hostname,
                    alert.endpoint.last_logged_user,
                    alert.endpoint.last_ip,
                ]).casefold()
            ]
        unmuted_open = [alert for alert in alerts if alert.status == EndpointAlert.STATUS_OPEN and not alert.is_muted]
        top_map = {}
        for alert in unmuted_open:
            row = top_map.setdefault(alert.endpoint.id, {'endpoint': alert.endpoint, 'count': 0, 'max_severity': alert.severity})
            row['count'] += 1
            if alert.severity == EndpointAlert.SEVERITY_CRITICAL:
                row['max_severity'] = alert.severity
        context = {
            'active_nav': 'alerts',
            'alerts': alerts,
            'filters': {'status': status_filter, 'severity': severity_filter, 'type': type_filter, 'period': period_filter, 'q': request.GET.get('q', '').strip()},
            'alert_type_options': ALERT_TYPE_OPTIONS,
            'period_options': PERIOD_OPTIONS,
            'mute_durations': [(key, label) for key, (label, _delta) in MUTE_DURATIONS.items()],
            'severity_options': EndpointAlert.SEVERITY_CHOICES,
            'status_options': EndpointAlert.STATUS_CHOICES,
            'open_count': len(unmuted_open),
            'critical_count': sum(alert.severity == EndpointAlert.SEVERITY_CRITICAL for alert in unmuted_open),
            'warning_count': sum(alert.severity == EndpointAlert.SEVERITY_WARNING for alert in unmuted_open),
            'security_count': sum(alert.severity == EndpointAlert.SEVERITY_SECURITY for alert in unmuted_open),
            'affected_endpoint_count': len({alert.endpoint.id for alert in unmuted_open}),
            'recent_activity': sorted(all_alerts, key=lambda item: item.updated_at, reverse=True)[:8],
            'resolved_recent': [alert for alert in all_alerts if alert.status == EndpointAlert.STATUS_RESOLVED][:6],
            'toast_resolved_alerts': [alert for alert in all_alerts if alert.status == EndpointAlert.STATUS_RESOLVED][:2],
            'top_endpoints': sorted(top_map.values(), key=lambda item: -item['count'])[:6],
            'last_updated_at': now,
            'using_mock_rmm_data': True,
        }
        return render(request, 'dashboard/alerts.html', context)

    queryset = EndpointAlert.objects.select_related('endpoint').prefetch_related('events')

    status_filter = request.GET.get('status', EndpointAlert.STATUS_OPEN).strip() or EndpointAlert.STATUS_OPEN
    severity_filter = request.GET.get('severity', 'all').strip() or 'all'
    type_filter = request.GET.get('type', 'all').strip() or 'all'
    period_filter = request.GET.get('period', 'all').strip() or 'all'
    query = request.GET.get('q', '').strip()

    if status_filter == 'muted':
        queryset = queryset.filter(muted_until__gt=now)
    elif status_filter != 'all':
        queryset = queryset.filter(status=status_filter)

    if severity_filter != 'all':
        queryset = queryset.filter(severity=severity_filter)

    if type_filter != 'all':
        queryset = queryset.filter(alert_type=type_filter)

    if period_filter == 'today':
        queryset = queryset.filter(last_seen_at__date=timezone.localdate())
    elif period_filter == '24h':
        queryset = queryset.filter(last_seen_at__gte=now - timedelta(hours=24))
    elif period_filter == '7d':
        queryset = queryset.filter(last_seen_at__gte=now - timedelta(days=7))

    if query:
        queryset = queryset.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(endpoint__hostname__icontains=query)
            | Q(endpoint__last_logged_user__icontains=query)
            | Q(endpoint__last_ip__icontains=query)
        )

    summary_queryset = queryset
    unmuted_open_filter = Q(status=EndpointAlert.STATUS_OPEN) & (Q(muted_until__isnull=True) | Q(muted_until__lte=now))
    priority_order = Case(
        When(muted_until__gt=now, then=Value(4)),
        When(status=EndpointAlert.STATUS_OPEN, severity=EndpointAlert.SEVERITY_CRITICAL, then=Value(0)),
        When(status=EndpointAlert.STATUS_OPEN, severity=EndpointAlert.SEVERITY_SECURITY, then=Value(1)),
        When(status=EndpointAlert.STATUS_OPEN, severity=EndpointAlert.SEVERITY_WARNING, then=Value(2)),
        When(status=EndpointAlert.STATUS_OPEN, severity=EndpointAlert.SEVERITY_INFO, then=Value(3)),
        When(status=EndpointAlert.STATUS_ACKNOWLEDGED, then=Value(5)),
        When(status=EndpointAlert.STATUS_RESOLVED, then=Value(6)),
        default=Value(7),
        output_field=IntegerField(),
    )
    queryset = queryset.alias(priority_rank=priority_order).order_by('priority_rank', '-last_seen_at')

    recent_activity = EndpointAlert.objects.select_related('endpoint').order_by('-updated_at')[:8]
    resolved_recent = EndpointAlert.objects.filter(
        status=EndpointAlert.STATUS_RESOLVED,
        resolved_at__isnull=False,
    ).select_related('endpoint').order_by('-resolved_at')[:6]
    toast_resolved_alerts = EndpointAlert.objects.filter(
        status=EndpointAlert.STATUS_RESOLVED,
        resolved_at__gte=now - timedelta(minutes=10),
    ).select_related('endpoint').order_by('-resolved_at')[:4]

    open_alerts_for_top = EndpointAlert.objects.filter(
        status=EndpointAlert.STATUS_OPEN,
    ).filter(
        Q(muted_until__isnull=True) | Q(muted_until__lte=now),
    ).select_related('endpoint').order_by('endpoint__hostname')
    severity_rank = {
        EndpointAlert.SEVERITY_CRITICAL: 0,
        EndpointAlert.SEVERITY_SECURITY: 1,
        EndpointAlert.SEVERITY_WARNING: 2,
        EndpointAlert.SEVERITY_INFO: 3,
    }
    top_endpoint_map = {}
    for alert in open_alerts_for_top:
        current = top_endpoint_map.setdefault(alert.endpoint_id, {
            'endpoint': alert.endpoint,
            'count': 0,
            'max_severity': alert.severity,
        })
        current['count'] += 1
        if severity_rank.get(alert.severity, 9) < severity_rank.get(current['max_severity'], 9):
            current['max_severity'] = alert.severity

    top_endpoints = sorted(
        top_endpoint_map.values(),
        key=lambda item: (-item['count'], severity_rank.get(item['max_severity'], 9), item['endpoint'].hostname or ''),
    )[:6]

    context = {
        'active_nav': 'alerts',
        'alerts': queryset,
        'filters': {
            'status': status_filter,
            'severity': severity_filter,
            'type': type_filter,
            'period': period_filter,
            'q': query,
        },
        'alert_type_options': ALERT_TYPE_OPTIONS,
        'period_options': PERIOD_OPTIONS,
        'mute_durations': [(key, label) for key, (label, _delta) in MUTE_DURATIONS.items()],
        'severity_options': EndpointAlert.SEVERITY_CHOICES,
        'status_options': EndpointAlert.STATUS_CHOICES,
        'open_count': summary_queryset.filter(unmuted_open_filter).count(),
        'critical_count': summary_queryset.filter(unmuted_open_filter, severity=EndpointAlert.SEVERITY_CRITICAL).count(),
        'warning_count': summary_queryset.filter(unmuted_open_filter, severity=EndpointAlert.SEVERITY_WARNING).count(),
        'security_count': summary_queryset.filter(unmuted_open_filter, severity=EndpointAlert.SEVERITY_SECURITY).count(),
        'affected_endpoint_count': summary_queryset.filter(unmuted_open_filter).values('endpoint_id').distinct().count(),
        'recent_activity': recent_activity,
        'resolved_recent': resolved_recent,
        'toast_resolved_alerts': toast_resolved_alerts,
        'top_endpoints': top_endpoints,
        'last_updated_at': now,
    }
    return render(request, 'dashboard/alerts.html', context)


def noc_view(request):
    now = timezone.now()
    if request.GET.get('mock') == '1' or not AgentMachine.objects.exists():
        endpoints = mock_rmm_endpoints()
        alerts = mock_rmm_alerts(endpoints)
        open_alerts = [alert for alert in alerts if alert.status == EndpointAlert.STATUS_OPEN]
        online_count = sum(endpoint.status == AgentMachine.STATUS_ONLINE for endpoint in endpoints)
        critical_count = sum(alert.severity == EndpointAlert.SEVERITY_CRITICAL for alert in open_alerts)
        health_score = max(0, min(100, round((online_count / max(len(endpoints), 1)) * 100) - (critical_count * 8)))
        context = {
            'active_nav': 'noc',
            'last_updated_at': now,
            'last_alert_evaluation': now - timedelta(minutes=3),
            'total_endpoints': len(endpoints),
            'noc_health_score': health_score,
            'online_count': online_count,
            'offline_count': sum(endpoint.status == AgentMachine.STATUS_OFFLINE for endpoint in endpoints),
            'unknown_count': sum(endpoint.status == AgentMachine.STATUS_UNKNOWN for endpoint in endpoints),
            'open_critical_count': critical_count,
            'open_warning_count': sum(alert.severity == EndpointAlert.SEVERITY_WARNING for alert in open_alerts),
            'open_security_count': sum(alert.severity == EndpointAlert.SEVERITY_SECURITY for alert in open_alerts),
            'affected_endpoints_count': len({alert.endpoint.id for alert in open_alerts}),
            'critical_alerts': [alert for alert in open_alerts if alert.severity == EndpointAlert.SEVERITY_CRITICAL][:8],
            'offline_endpoints': [endpoint for endpoint in endpoints if endpoint.status == AgentMachine.STATUS_OFFLINE][:8],
            'security_alerts': [alert for alert in open_alerts if alert.severity == EndpointAlert.SEVERITY_SECURITY][:8],
            'disk_alerts': [alert for alert in open_alerts if alert.alert_type == 'disk_low'][:8],
            'recent_activity': mock_rmm_events(endpoints, alerts)[:10],
            'using_mock_rmm_data': True,
        }
        return render(request, 'dashboard/noc.html', context)

    status_counts = {
        item['status']: item['count']
        for item in AgentMachine.objects.values('status').annotate(count=Count('id'))
    }
    open_alerts = EndpointAlert.objects.filter(status=EndpointAlert.STATUS_OPEN).filter(
        Q(muted_until__isnull=True) | Q(muted_until__lte=now),
    )
    severity_order = Case(
        When(severity=EndpointAlert.SEVERITY_CRITICAL, then=Value(0)),
        When(severity=EndpointAlert.SEVERITY_WARNING, then=Value(1)),
        When(severity=EndpointAlert.SEVERITY_SECURITY, then=Value(2)),
        When(severity=EndpointAlert.SEVERITY_INFO, then=Value(3)),
        default=Value(4),
        output_field=IntegerField(),
    )

    disk_alerts = open_alerts.filter(alert_type='disk_low').select_related('endpoint').alias(
        severity_rank=severity_order,
    ).order_by('severity_rank', '-last_seen_at')[:8]
    offline_endpoints = AgentMachine.objects.filter(
        status=AgentMachine.STATUS_OFFLINE,
    ).order_by('last_seen_at')[:8]
    relevant_event_types = [
        'alert.created',
        'alert.expired',
        'alert.resolved_auto',
        'endpoint.status_changed',
        'alert.reopened',
        'alert.resolved_manual',
        'alert.acknowledged',
        'security.defender_changed',
        'disk.state_changed',
        'os.build_changed',
    ]
    recent_activity = AuditEvent.objects.filter(
        Q(event_type__in=relevant_event_types)
        | Q(event_type='software.installed', severity=AuditEvent.SEVERITY_SECURITY)
    ).select_related('endpoint', 'alert').order_by('-created_at')[:10]
    last_alert_evaluation = EndpointAlert.objects.order_by('-updated_at').values_list('updated_at', flat=True).first()

    total_endpoints = AgentMachine.objects.count()
    online_count = status_counts.get(AgentMachine.STATUS_ONLINE, 0)
    critical_count = open_alerts.filter(severity=EndpointAlert.SEVERITY_CRITICAL).count()
    health_score = max(0, min(100, round((online_count / max(total_endpoints, 1)) * 100) - (critical_count * 8)))

    context = {
        'active_nav': 'noc',
        'last_updated_at': now,
        'last_alert_evaluation': last_alert_evaluation,
        'total_endpoints': total_endpoints,
        'noc_health_score': health_score,
        'online_count': online_count,
        'offline_count': status_counts.get(AgentMachine.STATUS_OFFLINE, 0),
        'unknown_count': status_counts.get(AgentMachine.STATUS_UNKNOWN, 0),
        'open_critical_count': critical_count,
        'open_warning_count': open_alerts.filter(severity=EndpointAlert.SEVERITY_WARNING).count(),
        'open_security_count': open_alerts.filter(severity=EndpointAlert.SEVERITY_SECURITY).count(),
        'affected_endpoints_count': open_alerts.values('endpoint_id').distinct().count(),
        'critical_alerts': open_alerts.filter(
            severity=EndpointAlert.SEVERITY_CRITICAL,
        ).select_related('endpoint').order_by('-last_seen_at')[:8],
        'offline_endpoints': offline_endpoints,
        'security_alerts': open_alerts.filter(
            severity=EndpointAlert.SEVERITY_SECURITY,
        ).select_related('endpoint').order_by('-last_seen_at')[:8],
        'disk_alerts': disk_alerts,
        'recent_activity': recent_activity,
    }
    return render(request, 'dashboard/noc.html', context)


def events_list(request):
    now = timezone.now()
    if request.GET.get('mock') == '1' or not AuditEvent.objects.exists():
        endpoints = mock_rmm_endpoints()
        alerts = mock_rmm_alerts(endpoints)
        events = mock_rmm_events(endpoints, alerts)
        query = request.GET.get('q', '').strip().casefold()
        severity_filter = request.GET.get('severity', 'all').strip() or 'all'
        event_type_filter = request.GET.get('event_type', 'all').strip() or 'all'
        actor_type_filter = request.GET.get('actor_type', 'all').strip() or 'all'
        endpoint_filter = request.GET.get('endpoint', '').strip()
        period_filter = request.GET.get('period', '7d').strip() or '7d'
        category_filter = request.GET.get('category', 'all').strip() or 'all'
        filtered = events
        if period_filter == '24h':
            filtered = [event for event in filtered if event.created_at >= now - timedelta(hours=24)]
        elif period_filter == '7d':
            filtered = [event for event in filtered if event.created_at >= now - timedelta(days=7)]
        elif period_filter == '30d':
            filtered = [event for event in filtered if event.created_at >= now - timedelta(days=30)]
        if severity_filter != 'all':
            filtered = [event for event in filtered if event.severity == severity_filter]
        if event_type_filter != 'all':
            filtered = [event for event in filtered if event.event_type == event_type_filter]
        if category_filter != 'all':
            prefixes = EVENT_CATEGORY_PREFIXES.get(category_filter, [])
            filtered = [event for event in filtered if any(event.event_type.startswith(prefix) for prefix in prefixes)]
        if actor_type_filter != 'all':
            filtered = [event for event in filtered if event.actor_type == actor_type_filter]
        if endpoint_filter:
            filtered = [event for event in filtered if str(event.endpoint.id) == endpoint_filter]
        if query:
            filtered = [
                event for event in filtered
                if query in ' '.join([event.title, event.description, event.event_type, event.endpoint.hostname, event.actor_name]).casefold()
            ]
        context = {
            'active_nav': 'events',
            'events': sorted(filtered, key=lambda item: item.created_at, reverse=True)[:250],
            'filters': {
                'q': request.GET.get('q', '').strip(),
                'severity': severity_filter,
                'event_type': event_type_filter,
                'actor_type': actor_type_filter,
                'endpoint': endpoint_filter,
                'period': period_filter,
                'category': category_filter,
            },
            'severity_options': AuditEvent.SEVERITY_CHOICES,
            'actor_type_options': AuditEvent.ACTOR_CHOICES,
            'event_type_options': sorted({event.event_type for event in events}),
            'period_options': EVENT_PERIOD_OPTIONS,
            'category_options': EVENT_CATEGORY_OPTIONS,
            'endpoint_options': [{'id': endpoint.id, 'hostname': endpoint.hostname} for endpoint in endpoints],
            'events_24h_count': sum(event.created_at >= now - timedelta(hours=24) for event in events),
            'critical_count': sum(event.severity == AuditEvent.SEVERITY_CRITICAL for event in filtered),
            'security_count': sum(event.severity == AuditEvent.SEVERITY_SECURITY for event in filtered),
            'system_count': sum(event.actor_type in [AuditEvent.ACTOR_SYSTEM, AuditEvent.ACTOR_SCHEDULER] for event in filtered),
            'user_count': sum(event.actor_type == AuditEvent.ACTOR_USER for event in filtered),
            'using_mock_rmm_data': True,
        }
        return render(request, 'dashboard/events.html', context)

    queryset = AuditEvent.objects.select_related('endpoint', 'alert')

    query = request.GET.get('q', '').strip()
    severity_filter = request.GET.get('severity', 'all').strip() or 'all'
    event_type_filter = request.GET.get('event_type', 'all').strip() or 'all'
    actor_type_filter = request.GET.get('actor_type', 'all').strip() or 'all'
    endpoint_filter = request.GET.get('endpoint', '').strip()
    period_filter = request.GET.get('period', '7d').strip() or '7d'
    category_filter = request.GET.get('category', 'all').strip() or 'all'

    if period_filter == '24h':
        queryset = queryset.filter(created_at__gte=now - timedelta(hours=24))
    elif period_filter == '7d':
        queryset = queryset.filter(created_at__gte=now - timedelta(days=7))
    elif period_filter == '30d':
        queryset = queryset.filter(created_at__gte=now - timedelta(days=30))

    if severity_filter != 'all':
        queryset = queryset.filter(severity=severity_filter)
    if event_type_filter != 'all':
        queryset = queryset.filter(event_type=event_type_filter)
    if category_filter != 'all':
        category_query = Q()
        for prefix in EVENT_CATEGORY_PREFIXES.get(category_filter, []):
            category_query |= Q(event_type__startswith=prefix)
        if category_query:
            queryset = queryset.filter(category_query)
    if actor_type_filter != 'all':
        queryset = queryset.filter(actor_type=actor_type_filter)
    if endpoint_filter:
        queryset = queryset.filter(endpoint_id=endpoint_filter)
    if query:
        queryset = queryset.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(event_type__icontains=query)
            | Q(endpoint__hostname__icontains=query)
            | Q(actor_name__icontains=query)
        )

    filtered_queryset = queryset
    last_24h = now - timedelta(hours=24)
    context = {
        'active_nav': 'events',
        'events': queryset.order_by('-created_at')[:250],
        'filters': {
            'q': query,
            'severity': severity_filter,
            'event_type': event_type_filter,
            'actor_type': actor_type_filter,
            'endpoint': endpoint_filter,
            'period': period_filter,
            'category': category_filter,
        },
        'severity_options': AuditEvent.SEVERITY_CHOICES,
        'actor_type_options': AuditEvent.ACTOR_CHOICES,
        'event_type_options': AuditEvent.objects.values_list('event_type', flat=True).distinct().order_by('event_type'),
        'period_options': EVENT_PERIOD_OPTIONS,
        'category_options': EVENT_CATEGORY_OPTIONS,
        'endpoint_options': AgentMachine.objects.order_by('hostname').values('id', 'hostname')[:500],
        'events_24h_count': AuditEvent.objects.filter(created_at__gte=last_24h).count(),
        'critical_count': filtered_queryset.filter(severity=AuditEvent.SEVERITY_CRITICAL).count(),
        'security_count': filtered_queryset.filter(severity=AuditEvent.SEVERITY_SECURITY).count(),
        'system_count': filtered_queryset.filter(actor_type__in=[AuditEvent.ACTOR_SYSTEM, AuditEvent.ACTOR_SCHEDULER]).count(),
        'user_count': filtered_queryset.filter(actor_type=AuditEvent.ACTOR_USER).count(),
    }
    return render(request, 'dashboard/events.html', context)


def jobs_list(request):
    context = {
        'active_nav': 'jobs',
        'using_mock_rmm_data': True,
    }
    return render(request, 'dashboard/jobs.html', context)


def maintenance_list(request):
    runs = MaintenanceRun.objects.prefetch_related('task_results').order_by('-started_at')[:25]
    latest_run = runs[0] if runs else None
    recent_failures = MaintenanceRun.objects.filter(
        status__in=[MaintenanceRun.STATUS_PARTIAL, MaintenanceRun.STATUS_FAILED],
    ).order_by('-started_at')[:6]
    status_counts = {
        item['status']: item['count']
        for item in MaintenanceRun.objects.values('status').annotate(count=Count('id'))
    }

    context = {
        'active_nav': 'maintenance',
        'latest_run': latest_run,
        'runs': runs,
        'recent_failures': recent_failures,
        'success_count': status_counts.get(MaintenanceRun.STATUS_SUCCESS, 0),
        'partial_count': status_counts.get(MaintenanceRun.STATUS_PARTIAL, 0),
        'failed_count': status_counts.get(MaintenanceRun.STATUS_FAILED, 0),
        'running_count': status_counts.get(MaintenanceRun.STATUS_RUNNING, 0),
    }
    return render(request, 'dashboard/maintenance.html', context)


def email_outbox_list(request):
    queryset = NotificationOutbox.objects.select_related('ticket', 'template').order_by('-created_at')
    status_filter = (request.GET.get('status') or '').strip()
    source_filter = (request.GET.get('source') or '').strip()
    query = (request.GET.get('q') or '').strip()
    today_only = request.GET.get('today') == '1'

    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if source_filter:
        queryset = queryset.filter(source_app=source_filter)
    if today_only:
        queryset = queryset.filter(created_at__date=timezone.localdate())
    if query:
        search_filter = (
            Q(recipient_name__icontains=query)
            | Q(recipient_email__icontains=query)
            | Q(subject__icontains=query)
            | Q(last_error__icontains=query)
            | Q(source_id__icontains=query)
        )
        if query.lstrip('#').isdigit():
            search_filter |= Q(ticket__number=int(query.lstrip('#')))
        queryset = queryset.filter(search_filter)

    items = list(queryset[:250])
    payload = [
        {
            'id': str(item.pk),
            'status': item.status,
            'status_label': item.get_status_display(),
            'source_app': item.source_app,
            'source_label': item.get_source_app_display(),
            'event_type': item.event_type,
            'recipient_name': item.recipient_name,
            'recipient_email': item.recipient_email,
            'cc': item.cc,
            'bcc': item.bcc,
            'subject': item.subject,
            'body_text': item.body_text,
            'body_html': item.body_html,
            'priority': item.get_priority_display(),
            'attempts': item.attempts,
            'max_attempts': item.max_attempts,
            'last_error': item.last_error,
            'last_attempt_at': timezone.localtime(item.last_attempt_at).strftime('%d/%m/%Y %H:%M:%S') if item.last_attempt_at else '',
            'created_at': timezone.localtime(item.created_at).strftime('%d/%m/%Y %H:%M:%S'),
            'updated_at': timezone.localtime(item.updated_at).strftime('%d/%m/%Y %H:%M:%S'),
            'sent_at': timezone.localtime(item.sent_at).strftime('%d/%m/%Y %H:%M:%S') if item.sent_at else '',
            'metadata': item.metadata,
            'ticket_number': item.ticket.number if item.ticket else '',
            'ticket_url': f'/tickets/{item.ticket.number}/' if item.ticket else '',
            'template': item.template.name if item.template else '',
            'retry_url': f'/maintenance/email-outbox/{item.pk}/retry/',
            'cancel_url': f'/maintenance/email-outbox/{item.pk}/cancel/',
            'pending_url': f'/maintenance/email-outbox/{item.pk}/pending/',
        }
        for item in items
    ]
    today = timezone.localdate()
    context = {
        'active_nav': 'maintenance',
        'maintenance_tab': 'email',
        'emails': items,
        'email_payload': payload,
        'filters': {
            'q': query,
            'status': status_filter,
            'source': source_filter,
            'today': today_only,
        },
        'status_choices': NotificationOutbox.STATUS_CHOICES,
        'source_choices': NotificationOutbox.SOURCE_CHOICES,
        'pending_count': NotificationOutbox.objects.filter(status=NotificationOutbox.STATUS_PENDING).count(),
        'sent_today_count': NotificationOutbox.objects.filter(status=NotificationOutbox.STATUS_SENT, sent_at__date=today).count(),
        'failed_count': NotificationOutbox.objects.filter(status=NotificationOutbox.STATUS_FAILED).count(),
        'inactive_count': NotificationOutbox.objects.filter(
            status__in=[NotificationOutbox.STATUS_SKIPPED, NotificationOutbox.STATUS_CANCELLED],
        ).count(),
        'smtp_status': smtp_configuration_status(),
    }
    return render(request, 'dashboard/email_outbox.html', context)


def _email_action_actor(request):
    if request.user.is_authenticated:
        return request.user.get_full_name() or request.user.get_username()
    return 'Night Owl Web'


@require_POST
def email_outbox_process(request):
    result = process_pending_emails(limit=50, actor=_email_action_actor(request))
    messages.success(
        request,
        f"Fila processada: {result['sent']} enviado(s), {result['failed']} falha(s).",
    )
    return redirect('email-outbox-list')


@require_POST
def email_outbox_retry_all(request):
    result = retry_all_failed(actor=_email_action_actor(request), send_now=True)
    messages.info(
        request,
        f"{result['retried']} reprocessado(s): {result['sent']} enviado(s), {result['failed']} falha(s).",
    )
    return redirect('email-outbox-list')


@require_POST
def email_outbox_retry(request, pk):
    item = get_object_or_404(NotificationOutbox, pk=pk)
    if item.status == NotificationOutbox.STATUS_FAILED:
        retry_failed_email(
            item.pk,
            actor=_email_action_actor(request),
            reset_attempts=item.attempts >= item.max_attempts,
        )
    result = send_email_outbox_item(item.pk, actor=_email_action_actor(request))
    if result.status == NotificationOutbox.STATUS_SENT:
        messages.success(request, 'E-mail enviado.')
    elif result.status == NotificationOutbox.STATUS_FAILED:
        messages.error(request, 'Falha no envio. Consulte os detalhes da fila.')
    else:
        messages.info(request, f'E-mail permanece com status {result.get_status_display()}.')
    return redirect('email-outbox-list')


@require_POST
def email_outbox_cancel(request, pk):
    cancel_email(pk, actor=_email_action_actor(request))
    messages.info(request, 'E-mail cancelado.')
    return redirect('email-outbox-list')


@require_POST
def email_outbox_pending(request, pk):
    mark_email_pending(pk, actor=_email_action_actor(request), reset_attempts=True)
    messages.info(request, 'E-mail marcado como pendente.')
    return redirect('email-outbox-list')


@require_POST
def alert_acknowledge(request, pk):
    alert = get_object_or_404(EndpointAlert, pk=pk)
    if alert.status in (EndpointAlert.STATUS_OPEN, EndpointAlert.STATUS_ACKNOWLEDGED):
        was_open = alert.status == EndpointAlert.STATUS_OPEN
        alert.status = EndpointAlert.STATUS_ACKNOWLEDGED
        if alert.acknowledged_at is None:
            alert.acknowledged_at = timezone.now()
        alert.save(update_fields=['status', 'acknowledged_at', 'updated_at'])
        if was_open:
            create_alert_event(alert, AlertEvent.TYPE_ACKNOWLEDGED, 'Alerta reconhecido.', request)
            audit_alert_action(request, alert, 'alert.acknowledged', 'Alerta reconhecido', alert.title)
        messages.info(request, 'Alerta reconhecido.')
    else:
        messages.warning(request, 'Alerta resolvido nao foi reaberto.')
    return redirect_back(request)


@require_POST
def alert_resolve(request, pk):
    alert = get_object_or_404(EndpointAlert, pk=pk)
    if alert.status != EndpointAlert.STATUS_RESOLVED:
        alert.status = EndpointAlert.STATUS_RESOLVED
        alert.resolved_at = timezone.now()
        alert.resolution_type = EndpointAlert.RESOLUTION_MANUAL
        alert.save(update_fields=['status', 'resolved_at', 'resolution_type', 'updated_at'])
        create_alert_event(
            alert,
            AlertEvent.TYPE_RESOLVED_MANUAL,
            'Alerta resolvido manualmente. Se a condicao ainda existir, ele podera voltar na proxima avaliacao.',
            request,
        )
        audit_alert_action(
            request,
            alert,
            'alert.resolved_manual',
            'Alerta resolvido manualmente',
            'Se a condicao ainda existir, o alerta podera voltar na proxima avaliacao.',
            severity=AuditEvent.SEVERITY_SUCCESS,
            metadata={'resolution_type': EndpointAlert.RESOLUTION_MANUAL},
        )
        messages.success(request, 'Alerta resolvido manualmente.')
    else:
        messages.info(request, 'Alerta ja estava resolvido.')
    return redirect_back(request)


@require_POST
def alert_mute(request, pk):
    alert = get_object_or_404(EndpointAlert, pk=pk)
    duration = request.POST.get('duration', '').strip()
    reason = request.POST.get('reason', '').strip()
    label, delta = MUTE_DURATIONS.get(duration, (None, None))
    if not delta:
        messages.error(request, 'Duracao de silenciamento invalida.')
        return redirect_back(request)

    now = timezone.now()
    alert.muted_at = now
    alert.muted_until = now + delta
    alert.muted_reason = reason
    alert.save(update_fields=['muted_at', 'muted_until', 'muted_reason', 'updated_at'])
    create_alert_event(
        alert,
        AlertEvent.TYPE_MUTED,
        f'Alerta silenciado por {label}.',
        request,
        {'duration': duration, 'muted_until': alert.muted_until.isoformat(), 'reason': reason},
    )
    audit_alert_action(
        request,
        alert,
        'alert.muted',
        'Alerta silenciado',
        f'Alerta silenciado por {label}.',
        severity=AuditEvent.SEVERITY_INFO,
        metadata={'duration': duration, 'muted_until': alert.muted_until.isoformat(), 'muted_reason': reason},
    )
    messages.info(request, f'Alerta silenciado por {label}.')
    return redirect_back(request)


@require_POST
def alert_comment(request, pk):
    alert = get_object_or_404(EndpointAlert, pk=pk)
    message = request.POST.get('message', '').strip()
    if not message:
        messages.warning(request, 'Informe uma observacao antes de salvar.')
        return redirect_back(request)

    create_alert_event(alert, AlertEvent.TYPE_COMMENT, message, request)
    audit_alert_action(
        request,
        alert,
        'alert.comment_added',
        'Observacao adicionada',
        message,
        severity=AuditEvent.SEVERITY_INFO,
    )
    messages.info(request, 'Observacao adicionada.')
    return redirect_back(request)


def endpoint_list(request):
    if request.GET.get('mock') == '1' or not AgentMachine.objects.exists():
        rows = mock_endpoint_rows()
        filters = endpoint_filters_from_request(request)
        filtered_rows = filter_endpoint_rows(rows, filters)
        context = {
            'active_nav': 'endpoints',
            'rows': filtered_rows,
            'filters': filters,
            **endpoint_summary_counts(filtered_rows),
            **endpoint_filter_options(rows),
            'using_mock_rmm_data': True,
        }
        return render(request, 'dashboard/endpoint_list.html', context)

    rows = [build_endpoint_row(endpoint) for endpoint in AgentMachine.objects.order_by('hostname', 'domain')]
    filters = endpoint_filters_from_request(request)
    filtered_rows = filter_endpoint_rows(rows, filters)

    context = {
        'active_nav': 'endpoints',
        'rows': filtered_rows,
        'filters': filters,
        **endpoint_summary_counts(filtered_rows),
        **endpoint_filter_options(rows),
    }
    return render(request, 'dashboard/endpoint_list.html', context)


def build_mock_endpoint_snapshot(endpoint):
    hostname = (endpoint.hostname or '').upper()
    disk_used = 93 if hostname in {'SRV-ERP-01', 'FIN-DC-02'} else 82 if hostname in {'REC-004', 'COM-017'} else 56
    disk_size = 512 * 1024 ** 3
    disk_free = round(disk_size * ((100 - disk_used) / 100))
    has_bitdefender = hostname not in {'FIN-012', 'FIN-DC-02'}
    installed_software = [
        {
            'name': 'Microsoft 365 Apps',
            'publisher': 'Microsoft Corporation',
            'version': '2407',
        },
        {
            'name': 'Google Chrome',
            'publisher': 'Google LLC',
            'version': '126.0.6478',
        },
        {
            'name': 'Bitdefender Endpoint Security Tools' if has_bitdefender else 'Windows Defender',
            'publisher': 'Bitdefender' if has_bitdefender else 'Microsoft Corporation',
            'version': '7.9.12' if has_bitdefender else '4.18.24060',
        },
    ]
    if hostname == 'DIR-NB-03':
        installed_software.append({
            'name': 'AnyDesk',
            'publisher': 'AnyDesk Software GmbH',
            'version': '8.0.9',
        })
    if hostname == 'TI-NOC-01':
        installed_software.append({
            'name': 'Advanced IP Scanner',
            'publisher': 'Famatech',
            'version': '2.5.4594',
        })

    return SimpleNamespace(
        received_at=endpoint.last_seen_at,
        hostname=endpoint.hostname,
        domain=endpoint.domain,
        logged_user=endpoint.last_logged_user,
        os_name=endpoint.os_name,
        os_version='23H2' if '11' in (endpoint.os_name or '') else '22H2',
        windows_build='22631.3880' if '11' in (endpoint.os_name or '') else '19045.4651',
        ips=[endpoint.last_ip] if endpoint.last_ip else [],
        serial_number=f'NO-MOCK-{str(endpoint.id)[-6:]}',
        manufacturer='NightOwl Preview',
        model='Endpoint mockado',
        cpu='Intel Core i5-1240P',
        memory_total_bytes=16 * 1024 ** 3,
        uptime_seconds=420000 if endpoint.status == AgentMachine.STATUS_ONLINE else None,
        disks=[
            {
                'name': 'C:',
                'size_bytes': disk_size,
                'free_bytes': disk_free,
            },
            {
                'name': 'D:',
                'size_bytes': 1024 * 1024 ** 3,
                'free_bytes': 640 * 1024 ** 3,
            },
        ],
        installed_software=installed_software,
        defender_status={
            'enabled': has_bitdefender,
            'real_time_protection_enabled': has_bitdefender,
            'engine_version': '1.1.24060.5' if has_bitdefender else '',
            'antivirus_signature_last_updated': timezone.now().strftime('%d/%m/%Y %H:%M'),
        },
    )


def build_mock_endpoint_detail_context(pk):
    endpoints = mock_rmm_endpoints()
    endpoint = next((item for item in endpoints if item.id == pk), None)
    if endpoint is None:
        raise Http404('Endpoint nao encontrado.')

    endpoint.agent_mode = 'Preview'
    endpoint.agent_install_path = r'C:\RMM'
    endpoint.agent_task_name = 'NightOwl Agent Heartbeat'
    endpoint.agent_runtime = 'PowerShell'
    endpoint.agent_update_source = r'\\192.168.104.120\controlsul\Comum\_Agents'
    endpoint.agent_runtime_version = '5.1'
    endpoint.agent_reported_at = endpoint.last_seen_at

    snapshot = build_mock_endpoint_snapshot(endpoint)
    installed_software = snapshot.installed_software
    disks = build_disk_rows(snapshot.disks)
    software_rows = build_software_rows(installed_software)
    defender = detail_defender_state(snapshot.defender_status, installed_software)
    primary_disk = get_primary_disk(snapshot.disks)
    health = calculate_health(endpoint, primary_disk, defender)
    smart_badges = build_smart_badges(endpoint, primary_disk, defender, installed_software)

    alerts = mock_rmm_alerts(endpoints)
    events = mock_rmm_events(endpoints, alerts)
    endpoint_alerts = MockList([
        alert for alert in alerts
        if alert.endpoint_id == endpoint.id
        and alert.status in [EndpointAlert.STATUS_OPEN, EndpointAlert.STATUS_ACKNOWLEDGED]
    ][:8])
    audit_events = MockList([
        event for event in events
        if getattr(getattr(event, 'endpoint', None), 'id', None) == endpoint.id
    ][:8])
    endpoint_sector = infer_endpoint_sector(endpoint.hostname, endpoint.domain, endpoint.last_logged_user)
    endpoint_type = infer_endpoint_type(endpoint.hostname, endpoint.os_name)
    endpoint_attention = endpoint_attention_summary(endpoint, primary_disk, defender_filter_state(snapshot.defender_status), health, snapshot)

    return {
        'active_nav': 'endpoints',
        'endpoint': endpoint,
        'snapshot': snapshot,
        'using_mock_rmm_data': True,
        'memory_total_gb': format_bytes_gb(snapshot.memory_total_bytes),
        'uptime_display': format_uptime(snapshot.uptime_seconds),
        'disks': disks,
        'installed_software': software_rows,
        'defender': defender,
        'primary_disk': primary_disk,
        'health': health,
        'health_breakdown': build_endpoint_health_breakdown(endpoint, primary_disk, defender, endpoint_alerts, snapshot),
        'endpoint_attention': endpoint_attention,
        'endpoint_sector': endpoint_sector,
        'endpoint_type': endpoint_type,
        'endpoint_type_label': endpoint_type_label(endpoint_type),
        'smart_badges': smart_badges,
        'primary_ip': endpoint.last_ip or (snapshot.ips[0] if snapshot.ips else ''),
        'recommended_agent_version': latest_agent_version(),
        'agent_version_state': agent_version_state(endpoint.agent_version, latest_agent_version()),
        'endpoint_alerts': endpoint_alerts,
        'audit_events': audit_events,
        'related_tickets': [],
        'patch_rows': build_endpoint_patch_rows(endpoint),
        'task_rows': build_endpoint_task_rows(endpoint),
    }


def _iso_or_none(value):
    if not value:
        return None
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)


def serialize_agent_job(job):
    public_status = public_job_status(job)
    stale = job_stale_info(job)
    stage = job_stage(job)
    progress = job_progress_percentage(job)
    progress_message = job_progress_message(job)
    sanitized_payload = sanitize_job_value(job.payload or {})
    sanitized_result = sanitize_job_value(job.result or {})
    target_version = job_target_version(job)
    previous_version = job_previous_version(job)
    installed_version = job_installed_version(job)
    rollback_status = ''
    if job.status == AgentJob.STATUS_ROLLED_BACK or (isinstance(job.result, dict) and job.result.get('rollback_performed')):
        rollback_status = 'rolled_back'
    elif job.status == AgentJob.STATUS_ROLLBACK_FAILED:
        rollback_status = 'rollback_failed'
    return {
        'id': str(job.id),
        'jobId': str(job.id),
        'resultId': job.result_id or '',
        'correlationId': job.correlation_id or '',
        'name': dict(AgentJob.TYPE_CHOICES).get(job.job_type, job.job_type),
        'type': job.job_type,
        'command': job.job_type,
        'status': public_status,
        'rawStatus': job.status,
        'stage': stage,
        'progressPercentage': progress,
        'progress_percentage': progress,
        'progressMessage': progress_message,
        'progress_message': progress_message,
        'attempt': job.attempt,
        'timeoutSeconds': job.timeout_seconds,
        'timeout_seconds': job.timeout_seconds,
        'endpoint': job.endpoint.hostname if job.endpoint_id else '',
        'createdBy': job.created_by or 'Sistema',
        'created_by': job.created_by or 'Sistema',
        'createdAt': _iso_or_none(job.created_at),
        'created_at': _iso_or_none(job.created_at),
        'queuedAt': _iso_or_none(job.queued_at),
        'dispatchedAt': _iso_or_none(job.dispatched_at),
        'dispatched_at': _iso_or_none(job.dispatched_at),
        'startedAt': _iso_or_none(job.started_at),
        'started_at': _iso_or_none(job.started_at),
        'finishedAt': _iso_or_none(job.finished_at),
        'finished_at': _iso_or_none(job.finished_at),
        'lastUpdateAt': _iso_or_none(job.result_received_at or job.updated_at),
        'last_update_at': _iso_or_none(job.result_received_at or job.updated_at),
        'durationMs': int((job.duration_seconds or 0) * 1000) if job.duration_seconds is not None else 0,
        'durationSeconds': job.duration_seconds,
        'duration_seconds': job.duration_seconds,
        'result': job.error_message or progress_message or (job.stdout[:140] if job.stdout else ''),
        'stdout': sanitize_job_value(job.stdout or ''),
        'stderr': sanitize_job_value(job.stderr or ''),
        'exitCode': job.exit_code,
        'exit_code': job.exit_code,
        'errorCode': job.error_code,
        'error_code': job.error_code,
        'outputTruncated': job.output_truncated,
        'output_truncated': job.output_truncated,
        'receivedAt': _iso_or_none(job.result_received_at),
        'payload': sanitized_payload,
        'payloadSanitized': sanitized_payload,
        'release': {
            'id': str(job.agent_release_id) if job.agent_release_id else (job.payload or {}).get('release_id', ''),
            'version': (job.agent_release.version if job.agent_release_id else (job.payload or {}).get('target_version', '')),
            'channel': (job.agent_release.channel if job.agent_release_id else (job.payload or {}).get('source_channel') or (job.payload or {}).get('channel', '')),
        },
        'targetVersion': target_version,
        'target_version': target_version,
        'previousVersion': previous_version,
        'previous_version': previous_version,
        'installedVersion': installed_version,
        'installed_version': installed_version,
        'rollbackPerformed': bool(rollback_status),
        'rollback_performed': bool(rollback_status),
        'rollbackStatus': rollback_status,
        'rollback_status': rollback_status,
        'resultJson': sanitized_result,
        'result_sanitized': sanitized_result,
        'errorMessage': job.error_message,
        'error_message': job.error_message,
        'progress': progress,
        'isStale': stale['is_stale'],
        'is_stale': stale['is_stale'],
        'staleReason': stale['stale_reason'],
        'stale_reason': stale['stale_reason'],
        'staleSince': _iso_or_none(stale['stale_since']),
        'stale_since': _iso_or_none(stale['stale_since']),
        'expectedTimeoutAt': _iso_or_none(stale['expected_timeout_at'] or job_expected_timeout_at(job)),
        'expected_timeout_at': _iso_or_none(stale['expected_timeout_at'] or job_expected_timeout_at(job)),
        'timeline': [
            item for item in [
                'pending' if job.queued_at else '',
                'dispatched' if job.dispatched_at else '',
                'started' if job.started_at else '',
                public_status if job.finished_at else '',
            ]
            if item
        ],
    }


def _raw_collection(snapshot, name):
    if not snapshot or not snapshot.raw_payload:
        return None
    raw = ensure_dict(snapshot.raw_payload)
    collections = ensure_dict(raw.get('collections'))
    if name in collections:
        return collections[name]
    full = ensure_dict(collections.get('full_inventory'))
    aliases = {
        'disk': ['disk', 'disks'],
        'disks': ['disks', 'disk'],
        'software': ['software', 'installed_software'],
        'patches': ['patches', 'patch'],
    }.get(name, [name])
    for alias in aliases:
        if alias in full:
            return full.get(alias)
    return None


def snapshot_has_detail_data(snapshot):
    if not snapshot:
        return False
    raw = ensure_dict(snapshot.raw_payload)
    collections = ensure_dict(raw.get('collections'))
    return bool(collections or snapshot.disks or snapshot.installed_software or snapshot.defender_status)


def get_endpoint_detail_snapshot(endpoint):
    snapshots = list(endpoint.inventory_snapshots.order_by('-received_at')[:30])
    for snapshot in snapshots:
        if snapshot_has_detail_data(snapshot):
            return snapshot
    return snapshots[0] if snapshots else None


def resolve_agent_endpoint(identifier):
    value = str(identifier or '').strip()
    if not value:
        raise AgentMachine.DoesNotExist
    try:
        return AgentMachine.objects.get(pk=uuid.UUID(value))
    except (ValueError, AgentMachine.DoesNotExist):
        pass
    return AgentMachine.objects.filter(
        Q(machine_id__iexact=value) | Q(hostname__iexact=value) | Q(fqdn__iexact=value),
    ).order_by('-last_seen_at', 'hostname').first()


def _normalize_collection_dict(value, list_key=None):
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and list_key:
        return {list_key: value}
    return {}


def _normalize_network_collection(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {'interfaces': value}
    return {}


def _agent_indicator_from_status(endpoint, diagnostic, recommended_version):
    if getattr(endpoint, 'status', '') == AgentMachine.STATUS_OFFLINE:
        return 'offline'
    if diagnostic:
        return diagnostic.health_indicator
    if agent_version_state(endpoint.agent_version, recommended_version) == 'outdated':
        return 'attention'
    return 'healthy'


def _serialize_agent_diagnostic(endpoint, diagnostic, jobs, recommended_version, *, can_view_technical=False, can_view_admin=False):
    installed_version = endpoint.agent_version or (diagnostic.installed_version if diagnostic else '')
    available_version = (diagnostic.available_version if diagnostic else '') or recommended_version
    running_jobs = [job for job in jobs if job.status in {AgentJob.STATUS_QUEUED, AgentJob.STATUS_SENT, AgentJob.STATUS_RUNNING}]
    last_error_message = diagnostic.last_error_message if diagnostic and can_view_admin else (diagnostic.last_error_message[:160] if diagnostic and can_view_technical else '')
    update_error_message = diagnostic.update_error_message if diagnostic and can_view_admin else (diagnostic.update_error_message[:160] if diagnostic and can_view_technical else '')
    rollback_error_message = diagnostic.rollback_error_message if diagnostic and can_view_admin else (diagnostic.rollback_error_message[:160] if diagnostic and can_view_technical else '')
    result_last_send_error = diagnostic.result_last_send_error if diagnostic and can_view_admin else (diagnostic.result_last_send_error[:160] if diagnostic and can_view_technical else '')
    return {
        'visible': bool(can_view_technical),
        'admin': bool(can_view_admin),
        'indicator': _agent_indicator_from_status(endpoint, diagnostic, available_version),
        'summary': {
            'installed_version': installed_version or '',
            'available_version': available_version or '',
            'last_heartbeat_at': _iso_or_none((diagnostic.last_heartbeat_at if diagnostic else None) or endpoint.last_seen_at),
            'last_inventory_at': _iso_or_none(diagnostic.last_inventory_at if diagnostic else None),
            'last_agent_start_at': _iso_or_none(diagnostic.last_agent_start_at if diagnostic else None),
            'agent_uptime_seconds': diagnostic.agent_uptime_seconds if diagnostic else None,
            'service_status': (diagnostic.service_status if diagnostic else '') or ('Running' if endpoint.status == AgentMachine.STATUS_ONLINE else ''),
            'current_user': diagnostic.current_user if diagnostic else endpoint.last_logged_user,
            'current_ip': str((diagnostic.current_ip if diagnostic else None) or endpoint.last_ip or ''),
            'pending_result_count': diagnostic.pending_result_count if diagnostic else 0,
            'running_job_count': diagnostic.running_job_count if diagnostic else len(running_jobs),
        },
        'last_error': {
            'component': diagnostic.last_error_component if diagnostic else '',
            'code': diagnostic.last_error_code if diagnostic else '',
            'message': last_error_message,
            'at': _iso_or_none(diagnostic.last_error_at if diagnostic else None),
        },
        'updater': {
            'update_id': diagnostic.update_id if diagnostic else '',
            'job_id': diagnostic.update_job_id if diagnostic else '',
            'from_version': diagnostic.from_version if diagnostic else '',
            'target_version': diagnostic.target_version if diagnostic else '',
            'current_stage': diagnostic.update_current_stage if diagnostic else '',
            'status': diagnostic.update_status if diagnostic else '',
            'started_at': _iso_or_none(diagnostic.update_started_at if diagnostic else None),
            'completed_at': _iso_or_none(diagnostic.update_completed_at if diagnostic else None),
            'rollback_status': diagnostic.rollback_status if diagnostic else '',
            'rollback_attempt': diagnostic.rollback_attempt if diagnostic else 0,
            'health_check_confirmed': diagnostic.health_check_confirmed if diagnostic else False,
            'error_code': diagnostic.update_error_code if diagnostic else '',
            'error_message': update_error_message,
            'rollback_error_code': diagnostic.rollback_error_code if diagnostic else '',
            'rollback_error_message': rollback_error_message,
            'package_url': diagnostic.package_url_sanitized if diagnostic and can_view_admin else '',
        },
        'queue': {
            'pending_count': diagnostic.result_pending_count if diagnostic else 0,
            'oldest_pending_at': _iso_or_none(diagnostic.result_oldest_pending_at if diagnostic else None),
            'retrying_count': diagnostic.result_retrying_count if diagnostic else 0,
            'quarantined_count': diagnostic.result_quarantined_count if diagnostic else 0,
            'queue_full': diagnostic.result_queue_full if diagnostic else False,
            'last_send_error': result_last_send_error,
        },
    }


def _endpoint_diagnostic(endpoint):
    try:
        return endpoint.operational_status
    except AgentOperationalStatus.DoesNotExist:
        return None


def _manual_update_release_options(endpoint):
    channel = endpoint.update_channel or AgentMachine.UPDATE_CHANNEL_STABLE
    releases = list(AgentRelease.objects.select_related('replacement_release').filter(
        channel=channel,
        status__in=set(AGENT_RELEASE_AVAILABLE_STATUSES) | {AgentRelease.STATUS_PAUSED, AgentRelease.STATUS_SUPERSEDED},
        revoked=False,
    ))
    releases = sort_releases_by_version(releases, reverse=True)[:20]
    rows = []
    for release in releases:
        decision = evaluate_agent_update_policy(endpoint, manual=True, explicit_release=release, record_evaluation=False)
        version_comparison = compare_versions(endpoint.agent_version or '', release.version)
        rows.append({
            'id': str(release.id),
            'version': release.version,
            'channel': release.channel,
            'status': release.status,
            'status_label': release.get_status_display() if hasattr(release, 'get_status_display') else release.status,
            'is_endpoint_channel': release.channel == channel,
            'rollout_percentage': release.rollout_percentage,
            'rollout_paused': release.rollout_paused,
            'mandatory': release.mandatory,
            'revoked': release.revoked,
            'superseded': release.status == AgentRelease.STATUS_SUPERSEDED,
            'replacement_release_id': str(release.replacement_release_id) if release.replacement_release_id else '',
            'replacement_version': release.replacement_release.version if release.replacement_release_id else '',
            'signature_valid': release.signature_valid,
            'signature_key_id': release.signature_key_id,
            'legacy_unsigned': release.legacy_unsigned,
            'manifest_url': release.manifest_url,
            'eligible': decision.eligible,
            'reason_code': decision.reason_code,
            'requires_force': decision.reason_code == 'downgrade_requires_force' or (version_comparison is not None and version_comparison > 0),
            'same_version': version_comparison == 0,
            'metadata_complete': bool(release.package_url and release.sha256 and release.size),
            'package_url_present': bool(release.package_url),
            'sha256_present': bool(release.sha256),
            'size': release.size,
            'released_at': _iso_or_none(release.released_at),
            'release_notes': release.release_notes,
            'minimum_updater_version': release.minimum_updater_version,
        })
    return rows


def _update_policy_message(reason_code):
    messages_by_reason = {
        'eligible': 'Release autorizada para atualizacao.',
        'already_current': 'O endpoint ja esta na versao selecionada.',
        'channel_no_release': 'Nao ha release disponivel para o canal do endpoint.',
        'release_not_found': 'A release selecionada nao foi encontrada.',
        'release_not_available': 'A release selecionada nao esta disponivel.',
        'release_revoked': 'A release selecionada foi revogada.',
        'release_paused': 'A release selecionada esta pausada.',
        'endpoint_paused': 'As atualizacoes deste endpoint estao pausadas.',
        'manual_policy': 'A politica do endpoint exige acionamento manual.',
        'notify_only': 'A politica atual e apenas notificacao.',
        'outside_maintenance_window': 'O endpoint esta fora da janela de manutencao.',
        'minimum_updater_incompatible': 'O updater instalado nao atende a versao minima exigida pela release.',
        'group_not_allowed': 'O endpoint nao pertence aos grupos autorizados para esta release.',
        'rollout_not_selected': 'O endpoint nao foi selecionado pelo rollout automatico.',
        'pinned_release_not_found': 'A versao fixada no endpoint nao foi encontrada.',
        'pinned_release_unavailable': 'A versao fixada no endpoint nao esta disponivel.',
        'invalid_version': 'A versao instalada do endpoint nao pode ser comparada com seguranca.',
        'downgrade_requires_force': 'A release selecionada e anterior a versao instalada. Confirme o downgrade nas opcoes avancadas.',
        'signature_invalid': 'A release selecionada nao possui assinatura valida para distribuicao segura.',
        'key_unknown': 'A chave de assinatura da release nao e confiavel para este backend.',
        'key_revoked': 'A chave de assinatura da release foi revogada.',
    }
    return messages_by_reason.get(reason_code or '', 'Este endpoint nao possui uma release autorizada para atualizacao neste momento.')


def build_endpoint_detail_payload(endpoint, snapshot, health, endpoint_attention, endpoint_sector, endpoint_type, endpoint_alerts, audit_events, related_tickets, *, request=None):
    update_decision = evaluate_agent_update_policy(endpoint, manual=False)
    recommended_version = update_decision.target_version or latest_agent_version()
    can_view_technical = is_nightowl_technical_user(getattr(request, 'user', None)) if request is not None else True
    user = getattr(request, 'user', None)
    can_view_admin = bool(user and getattr(user, 'is_authenticated', False) and (getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False)))
    diagnostic = _endpoint_diagnostic(endpoint)
    raw_payload = ensure_dict(snapshot.raw_payload if snapshot else {})
    raw_agent = ensure_dict(raw_payload.get('agent'))
    system = _normalize_collection_dict(_raw_collection(snapshot, 'system'))
    hardware = _normalize_collection_dict(_raw_collection(snapshot, 'hardware'))
    network = _normalize_network_collection(_raw_collection(snapshot, 'network'))
    disk_collection = _normalize_collection_dict(_raw_collection(snapshot, 'disk'), list_key='disks')
    software_collection = _normalize_collection_dict(_raw_collection(snapshot, 'software'), list_key='installed_software')
    security_collection = _normalize_collection_dict(_raw_collection(snapshot, 'security'))
    patches_collection = _normalize_collection_dict(_raw_collection(snapshot, 'patches'))
    full_inventory = _normalize_collection_dict(_raw_collection(snapshot, 'full_inventory'))

    has_inventory = bool(system or hardware or network)
    disk_items = ensure_list(disk_collection.get('disks') or (snapshot.disks if snapshot else []))
    software_items = ensure_list(software_collection.get('installed_software') or (snapshot.installed_software if snapshot else []))
    has_disks = bool(disk_items)
    has_software = bool(software_items)
    has_security = bool(security_collection)
    has_patches = bool(patches_collection)

    agent_health = {
        'agent_mode': endpoint.agent_mode or raw_agent.get('mode') or '',
        'agent_version': endpoint.agent_version or raw_agent.get('version') or raw_payload.get('agent_version') or '',
        'tray_version': raw_agent.get('tray_version') or raw_payload.get('tray_version') or '',
        'updater_version': raw_agent.get('updater_version') or raw_payload.get('updater_version') or '',
        'install_mode': raw_agent.get('install_mode') or ('service' if (endpoint.agent_mode or '').lower() == 'service' else ''),
        'service_name': raw_agent.get('service_name') or endpoint.agent_task_name or 'NightOwlAgent',
        'service_status': raw_agent.get('service_status') or ('Running' if endpoint.status == AgentMachine.STATUS_ONLINE and (endpoint.agent_mode or '').lower() == 'service' else ''),
        'service_start_type': raw_agent.get('service_start_type') or '',
        'service_account': raw_agent.get('service_account') or '',
        'install_path': endpoint.agent_install_path or raw_agent.get('install_path') or r'C:\ProgramData\NightOwl\Agent',
        'legacy_install_path': raw_agent.get('legacy_install_path') or r'C:\RMM',
        'config_path': raw_agent.get('config_path') or r'C:\ProgramData\NightOwl\Agent\RmmAgent.config.json',
        'log_path': (raw_agent.get('log_path') or r'C:\ProgramData\NightOwl\Logs') if can_view_admin else '',
        'log_file': (raw_agent.get('log_file') or r'C:\ProgramData\NightOwl\Logs\agent-service.jsonl') if can_view_admin else 'agent-service.jsonl',
        'heartbeat_url': raw_agent.get('heartbeat_url') or '',
        'jobs_pull_url': raw_agent.get('jobs_pull_url') or '',
        'jobs_result_url': raw_agent.get('jobs_result_url') or '',
        'collection_endpoints': raw_agent.get('collection_endpoints') or {},
        'last_heartbeat_at': _iso_or_none(endpoint.last_seen_at),
        'last_inventory_at': full_inventory.get('collected_at') or system.get('collected_at') or hardware.get('collected_at') or network.get('collected_at'),
        'last_software_inventory_at': software_collection.get('collected_at') or full_inventory.get('collected_at'),
        'last_security_inventory_at': security_collection.get('collected_at') or full_inventory.get('collected_at'),
        'last_disk_inventory_at': disk_collection.get('collected_at') or full_inventory.get('collected_at'),
        'last_patch_scan_at': patches_collection.get('collected_at') or full_inventory.get('collected_at'),
        'last_job_pull_at': None,
        'last_error': raw_agent.get('last_error') or raw_agent.get('last_status') or '',
    }

    inventory = None
    if has_inventory:
        cpu = hardware.get('cpu') or {}
        cpu_name = cpu.get('name') if isinstance(cpu, dict) else str(cpu or '')
        bios = ensure_dict(hardware.get('bios'))
        os_data = ensure_dict(system.get('os'))
        network_adapters = ensure_list(network.get('adapters') or network.get('interfaces'))
        macs = network.get('mac_addresses') or [
            ensure_dict(adapter).get('mac_address')
            for adapter in network_adapters
            if ensure_dict(adapter).get('mac_address')
        ]
        inventory = {
            'manufacturer': system.get('manufacturer') or endpoint.manufacturer,
            'model': system.get('model') or endpoint.model,
            'serial': system.get('serial_number') or bios.get('serial_number') or endpoint.serial_number,
            'cpu': cpu_name or (snapshot.cpu if snapshot else ''),
            'memoryGb': format_bytes_gb(hardware.get('memory_total_bytes') or (snapshot.memory_total_bytes if snapshot else None)),
            'availableMemoryGb': format_bytes_gb(hardware.get('available_memory_bytes')),
            'osVersion': os_data.get('version') or endpoint.os_version,
            'build': os_data.get('build') or system.get('os_build') or endpoint.windows_build,
            'architecture': os_data.get('architecture') or '',
            'installDate': system.get('install_date') or '',
            'lastBootTime': system.get('last_boot_time') or '',
            'timezone': system.get('timezone') or '',
            'locale': system.get('locale') or system.get('language') or '',
            'machineType': system.get('machine_type') or endpoint_type,
            'bios': bios.get('version') or '',
            'biosReleaseDate': bios.get('release_date') or hardware.get('bios_release_date') or '',
            'motherboard': hardware.get('motherboard') or '',
            'cpuManufacturer': hardware.get('cpu_manufacturer') or '',
            'physicalCores': hardware.get('physical_cores') or '',
            'logicalProcessors': hardware.get('logical_processors') or '',
            'tpmPresent': hardware.get('tpm_present'),
            'tpmEnabled': hardware.get('tpm_enabled'),
            'batteryPresent': hardware.get('battery_present'),
            'batteryStatus': hardware.get('battery_status') or '',
            'primaryIp': network.get('primary_ip') or '',
            'primaryMac': network.get('primary_mac') or '',
            'defaultGateway': network.get('default_gateway') or '',
            'dnsServers': network.get('dns_servers') or [],
            'adapters': network_adapters,
            'macs': macs,
            'uptime': format_uptime(system.get('uptime_seconds') or (snapshot.uptime_seconds if snapshot else None)),
            'lastFullInventory': full_inventory.get('collected_at') or system.get('collected_at') or hardware.get('collected_at'),
            'raw': {
                'system': system,
                'hardware': hardware,
                'network': network,
            },
        }

    disk_source = disk_items if has_disks else []
    disk_rows = []
    for disk in build_disk_rows(disk_source):
        disk_rows.append({
            'name': disk['name'],
            'label': disk.get('label') or '',
            'filesystem': disk.get('filesystem') or '',
            'driveType': disk.get('drive_type') or '',
            'totalGb': disk['size_gb'],
            'freeGb': disk['free_gb'],
            'usedPercent': disk['used_percent'],
            'severity': disk['level'],
            'isSystemDrive': disk.get('is_system_drive'),
            'bitlockerStatus': disk.get('bitlocker_status') or '',
            'healthStatus': disk.get('health_status') or '',
        })

    software_rows = []
    if has_software:
        for row in build_software_rows(software_items):
            software_rows.append({
                'name': row['name'],
                'version': row['version'],
                'publisher': row['publisher'],
                'category': row['category'],
                'risk': row['risk_level'],
                'installedAt': row.get('installed_at') or '',
                'installedAtRaw': row.get('installed_at') or '',
                'architecture': row.get('architecture') or '',
                'source': row.get('source') or '',
                'installLocation': row.get('install_location') or '',
            })

    security = None
    if has_security:
        defender = ensure_dict(security_collection.get('defender'))
        firewall = ensure_dict(security_collection.get('firewall'))
        bitlocker = ensure_dict(security_collection.get('bitlocker') or security_collection.get('bitlocker_summary'))
        av_products = [
            ensure_dict(item)
            for item in ensure_list(security_collection.get('detected_antivirus_products') or security_collection.get('antivirus_products'))
        ]
        remote_tools = []
        for item in ensure_list(security_collection.get('remote_access_tools')):
            if isinstance(item, dict):
                name = item.get('name')
            else:
                name = str(item or '')
            if name:
                remote_tools.append(name)
        local_admins = []
        for item in ensure_list(security_collection.get('local_admins') or security_collection.get('local_administrators')):
            if isinstance(item, dict):
                name = item.get('name')
            else:
                name = str(item or '')
            if name:
                local_admins.append(name)
        security = {
            'status': 'critical' if security_collection.get('overall_status') == 'critical' else 'attention' if security_collection.get('overall_status') == 'warning' else security_collection.get('overall_status') or 'unknown',
            'antivirus': ', '.join([item.get('name', '') for item in av_products if item.get('name')]) or ('Microsoft Defender' if defender else '-'),
            'signature': defender.get('antivirus_signature_version') or defender.get('engine_version') or '',
            'signatureUpdatedAt': defender.get('antivirus_signature_last_updated') or '',
            'lastQuickScan': defender.get('last_quick_scan') or '',
            'lastFullScan': defender.get('last_full_scan') or '',
            'defenderEnabled': defender.get('defender_enabled') if defender.get('defender_enabled') is not None else defender.get('antivirus_enabled'),
            'realtimeEnabled': defender.get('realtime_protection_enabled') if defender.get('realtime_protection_enabled') is not None else defender.get('real_time_protection_enabled'),
            'firewall': 'Dominio: {0} · Privada: {1} · Publica: {2}'.format(
                'on' if firewall.get('domain_enabled') else 'off' if firewall.get('domain_enabled') is False else '?',
                'on' if firewall.get('private_enabled') else 'off' if firewall.get('private_enabled') is False else '?',
                'on' if firewall.get('public_enabled') else 'off' if firewall.get('public_enabled') is False else '?',
            ),
            'bitlocker': bitlocker.get('status') or ('Coletado' if bitlocker else 'Nao coletado'),
            'rdpEnabled': security_collection.get('rdp_enabled'),
            'uacEnabled': security_collection.get('uac_enabled'),
            'remoteTools': remote_tools,
            'localAdmins': local_admins,
            'antivirusProducts': av_products,
            'raw': security_collection,
        }

    events = []
    for event in audit_events:
        events.append({
            'id': str(event.id),
            'eventType': event.event_type,
            'category': 'jobs' if event.event_type.startswith('job.') else 'inventory' if 'inventory' in event.event_type else 'agent',
            'severity': event.severity,
            'title': event.title,
            'description': event.description,
            'timestamp': _iso_or_none(event.created_at),
            'source': event.get_actor_type_display() if hasattr(event, 'get_actor_type_display') else event.actor_type,
            'actor': event.actor_name or '-',
            'endpoint': endpoint.hostname,
            'metadata': event.metadata,
        })

    alerts = []
    for alert in endpoint_alerts:
        if (
            alert.alert_type == 'agent_outdated'
            and agent_version_state(endpoint.agent_version, recommended_version) == 'current'
        ):
            continue
        alerts.append({
            'id': str(alert.id),
            'title': alert.title,
            'description': alert.description,
            'severity': alert.severity,
            'status': alert.status,
            'endpoint': endpoint.hostname,
            'createdAt': _iso_or_none(alert.first_seen_at),
        })

    tickets = []
    for ticket in related_tickets:
        tickets.append({
            'number': f'#{ticket.number}',
            'title': ticket.title,
            'status': ticket.get_status_display() if hasattr(ticket, 'get_status_display') else ticket.status,
            'priority': getattr(ticket, 'priority', ''),
        })

    job_objects = list(endpoint.jobs.order_by('-created_at')[:20])
    jobs = [serialize_agent_job(job) for job in job_objects]
    last_job = endpoint.jobs.filter(finished_at__isnull=False).order_by('-finished_at', '-updated_at').first()
    active_job = endpoint.jobs.filter(
        status__in=[AgentJob.STATUS_QUEUED, AgentJob.STATUS_SENT, AgentJob.STATUS_RUNNING],
    ).order_by('-created_at').first()
    active_update_job = endpoint.jobs.filter(
        job_type=AgentJob.TYPE_UPDATE_AGENT,
        status__in=[AgentJob.STATUS_QUEUED, AgentJob.STATUS_SENT, AgentJob.STATUS_RUNNING],
    ).order_by('-created_at').first()

    return {
        'data_source': 'real',
        'endpoint': {
            'id': str(endpoint.id),
            'machine_id': endpoint.machine_id or str(endpoint.id),
            'fqdn': endpoint.fqdn or '',
            'hostname': endpoint.hostname,
            'status': endpoint.status,
            'ip': str(endpoint.last_ip or ''),
            'user': endpoint.last_logged_user or '',
            'sector': endpoint_sector,
            'os': endpoint.os_name or '',
            'domain': endpoint.domain or '',
            'type': endpoint_type,
            'last_seen_at': _iso_or_none(endpoint.last_seen_at),
            'agent_mode': endpoint.agent_mode or '',
            'agent_version': endpoint.agent_version or '',
            'agent_version_state': agent_version_state(endpoint.agent_version, recommended_version),
            'latest_agent_version': recommended_version,
            'update_channel': endpoint.update_channel or AgentMachine.UPDATE_CHANNEL_STABLE,
            'update_policy': endpoint.update_policy or AgentMachine.UPDATE_POLICY_MANUAL,
            'update_paused': endpoint.update_paused,
            'pinned_agent_version': endpoint.pinned_agent_version or '',
            'identity_source': 'machine_id' if endpoint.machine_id else 'internal_id',
        },
        'latest_agent_version': recommended_version,
        'recommended_agent_version': recommended_version,
        'agent_update_policy': update_decision.as_panel_payload(),
        'agent_update_releases': _manual_update_release_options(endpoint) if can_view_technical else [],
        'active_job': serialize_agent_job(active_job) if active_job else None,
        'active_update_job': serialize_agent_job(active_update_job) if active_update_job else None,
        'agent_health': agent_health,
        'agent_diagnostic': _serialize_agent_diagnostic(
            endpoint,
            diagnostic,
            job_objects,
            recommended_version,
            can_view_technical=can_view_technical,
            can_view_admin=can_view_admin,
        ),
        'inventory': inventory,
        'hardware': hardware or None,
        'network': network or {'interfaces': []},
        'disks': disk_rows,
        'software': software_rows,
        'security': security,
        'patches': patches_collection or None,
        'events': events,
        'jobs': jobs,
        'alerts': alerts,
        'tickets': tickets,
        'health': {
            'score': health.get('score'),
            'class': health.get('class'),
            'label': health.get('label'),
        },
        'attention': endpoint_attention,
        'collection_state': {
            'inventory': has_inventory,
            'disks': has_disks,
            'software': has_software,
            'security': has_security,
            'patches': has_patches,
            'events': bool(events),
            'jobs': bool(jobs),
            'last_job_result_at': _iso_or_none(last_job.finished_at if last_job else None),
        },
    }


def endpoint_detail(request, pk):
    endpoint = resolve_agent_endpoint(pk)
    if endpoint is None:
        context = build_mock_endpoint_detail_context(pk)
        return render(request, 'dashboard/endpoint_detail.html', context)

    snapshot = get_endpoint_detail_snapshot(endpoint)

    disks = build_disk_rows(snapshot.disks if snapshot else [])
    installed_software = snapshot.installed_software if snapshot else []
    software_rows = build_software_rows(installed_software)
    defender = detail_defender_state(snapshot.defender_status if snapshot else {}, installed_software)
    primary_disk = get_primary_disk(snapshot.disks if snapshot else [])
    health = calculate_health(endpoint, primary_disk, defender)
    smart_badges = build_smart_badges(endpoint, primary_disk, defender, installed_software)
    primary_ip = endpoint.last_ip

    if not primary_ip and snapshot and snapshot.ips:
        primary_ip = snapshot.ips[0]
    endpoint_alerts = endpoint.alerts.filter(
        status__in=[EndpointAlert.STATUS_OPEN, EndpointAlert.STATUS_ACKNOWLEDGED],
    ).order_by('-last_seen_at')[:8]
    endpoint_sector = infer_endpoint_sector(endpoint.hostname, endpoint.domain, endpoint.last_logged_user)
    endpoint_type = infer_endpoint_type(endpoint.hostname, endpoint.os_name)
    endpoint_attention = endpoint_attention_summary(endpoint, primary_disk, defender_filter_state(snapshot.defender_status if snapshot else {}), health, snapshot)

    audit_events = endpoint.audit_events.select_related('alert').order_by('-created_at')[:8]
    related_tickets = endpoint.tickets.exclude(
        status__in=['closed', 'canceled'],
    ).order_by('-updated_at')[:5]

    context = {
        'active_nav': 'endpoints',
        'endpoint': endpoint,
        'snapshot': snapshot,
        'endpoint_data_badge': 'Dados reais',
        'memory_total_gb': format_bytes_gb(snapshot.memory_total_bytes) if snapshot else None,
        'uptime_display': format_uptime(snapshot.uptime_seconds) if snapshot else None,
        'disks': disks,
        'installed_software': software_rows,
        'defender': defender,
        'primary_disk': primary_disk,
        'health': health,
        'health_breakdown': build_endpoint_health_breakdown(endpoint, primary_disk, defender, endpoint_alerts, snapshot),
        'endpoint_attention': endpoint_attention,
        'endpoint_sector': endpoint_sector,
        'endpoint_type': endpoint_type,
        'endpoint_type_label': endpoint_type_label(endpoint_type),
        'smart_badges': smart_badges,
        'primary_ip': primary_ip,
        'recommended_agent_version': latest_agent_version(),
        'agent_version_state': agent_version_state(endpoint.agent_version, latest_agent_version()),
        'endpoint_alerts': endpoint_alerts,
        'audit_events': audit_events,
        'related_tickets': related_tickets,
        'patch_rows': build_endpoint_patch_rows(endpoint),
        'task_rows': build_endpoint_task_rows(endpoint),
    }
    context['endpoint_detail_payload'] = build_endpoint_detail_payload(
        endpoint,
        snapshot,
        health,
        endpoint_attention,
        endpoint_sector,
        endpoint_type,
        endpoint_alerts,
        audit_events,
        related_tickets,
        request=request,
    )
    return render(request, 'dashboard/endpoint_detail.html', context)


def endpoint_detail_data(request, pk):
    if not is_nightowl_technical_user(request.user):
        return JsonResponse({'error': 'forbidden'}, status=403)

    endpoint = resolve_agent_endpoint(pk)
    if endpoint is None:
        raise Http404
    snapshot = get_endpoint_detail_snapshot(endpoint)
    installed_software = snapshot.installed_software if snapshot else []
    defender = detail_defender_state(snapshot.defender_status if snapshot else {}, installed_software)
    primary_disk = get_primary_disk(snapshot.disks if snapshot else [])
    health = calculate_health(endpoint, primary_disk, defender)
    endpoint_alerts = endpoint.alerts.filter(
        status__in=[EndpointAlert.STATUS_OPEN, EndpointAlert.STATUS_ACKNOWLEDGED],
    ).order_by('-last_seen_at')[:8]
    endpoint_sector = infer_endpoint_sector(endpoint.hostname, endpoint.domain, endpoint.last_logged_user)
    endpoint_type = infer_endpoint_type(endpoint.hostname, endpoint.os_name)
    endpoint_attention = endpoint_attention_summary(endpoint, primary_disk, defender_filter_state(snapshot.defender_status if snapshot else {}), health, snapshot)
    audit_events = endpoint.audit_events.select_related('alert').order_by('-created_at')[:8]
    related_tickets = endpoint.tickets.exclude(
        status__in=['closed', 'canceled'],
    ).order_by('-updated_at')[:5]
    response = JsonResponse(
        build_endpoint_detail_payload(
            endpoint,
            snapshot,
            health,
            endpoint_attention,
            endpoint_sector,
            endpoint_type,
            endpoint_alerts,
            audit_events,
            related_tickets,
            request=request,
        ),
    )
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    return response


@require_POST
def endpoint_job_create(request, pk):
    if not is_nightowl_technical_user(request.user):
        return JsonResponse({'error': 'forbidden', 'detail': 'Sem permissao para criar jobs tecnicos.'}, status=403)

    endpoint = resolve_agent_endpoint(pk)
    if endpoint is None:
        raise Http404
    action = (request.POST.get('action') or '').strip()
    job_type = (request.POST.get('job_type') or '').strip()
    action_map = {
        'force_inventory': AgentJob.TYPE_FORCE_INVENTORY,
        'collect_inventory': AgentJob.TYPE_FORCE_INVENTORY,
        'check_defender': AgentJob.TYPE_COLLECT_SECURITY,
        'defender_check': AgentJob.TYPE_COLLECT_SECURITY,
        'collect_security': AgentJob.TYPE_COLLECT_SECURITY,
        'check_disk': AgentJob.TYPE_COLLECT_DISKS,
        'collect_disks': AgentJob.TYPE_COLLECT_DISKS,
        'collect_logs': AgentJob.TYPE_COLLECT_LOGS,
        'ping': AgentJob.TYPE_PING,
        'collect_software': AgentJob.TYPE_COLLECT_SOFTWARE,
        'windows_update_scan': AgentJob.TYPE_WINDOWS_UPDATE_SCAN,
        'update_agent': AgentJob.TYPE_UPDATE_AGENT,
        'update_trusted_release_keys': AgentJob.TYPE_UPDATE_TRUSTED_RELEASE_KEYS,
        'restart_agent': AgentJob.TYPE_RESTART_AGENT,
    }
    selected_type = action_map.get(action) or action_map.get(job_type) or job_type
    allowed_types = {choice[0] for choice in AgentJob.TYPE_CHOICES}
    if selected_type not in allowed_types:
        return JsonResponse(
            {
                'error': 'unsupported_job_type',
                'detail': 'Esta acao ainda nao esta habilitada para execucao remota real.',
            },
            status=400,
        )

    payload = {}
    if selected_type == AgentJob.TYPE_PING:
        payload['target'] = request.POST.get('target') or str(endpoint.last_ip or endpoint.hostname)
        payload['count'] = 2
    elif selected_type == AgentJob.TYPE_COLLECT_LOGS:
        payload['lines'] = 120
    elif selected_type == AgentJob.TYPE_UPDATE_AGENT:
        selected_release_id = (request.POST.get('release_id') or request.POST.get('agent_release_id') or '').strip()
        force_update = (request.POST.get('force') or '').strip().lower() in {'1', 'true', 'on', 'yes'}
        selected_release = None
        if selected_release_id:
            selected_release = AgentRelease.objects.filter(pk=selected_release_id).first()
            if selected_release is None:
                create_audit_event(
                    event_type='agent.update_manual_blocked',
                    title='Update manual bloqueado',
                    description=f'Release informada nao foi encontrada para {endpoint.hostname}.',
                    severity=AuditEvent.SEVERITY_WARNING,
                    actor_type=AuditEvent.ACTOR_USER,
                    actor_name=request.user.get_username(),
                    endpoint=endpoint,
                    metadata={'release_id': selected_release_id, 'reason_code': 'release_not_found'},
                    request=request,
                )
                return JsonResponse(
                    {
                        'error': 'endpoint_not_eligible_for_update',
                        'detail': 'A release selecionada nao foi encontrada.',
                        'reason_code': 'release_not_found',
                    },
                    status=409,
                )
        pending_update = endpoint.jobs.filter(
            job_type=AgentJob.TYPE_UPDATE_AGENT,
            status__in=[AgentJob.STATUS_QUEUED, AgentJob.STATUS_SENT, AgentJob.STATUS_RUNNING],
        ).order_by('-created_at').first()
        if pending_update:
            logger.info(
                'update_agent job already pending endpoint_id=%s job_id=%s status=%s',
                endpoint.id,
                pending_update.id,
                pending_update.status,
            )
            return JsonResponse(
                {
                    'error': 'update_job_already_pending',
                    'detail': 'Ja existe um job de atualizacao do agente pendente ou em execucao para este endpoint.',
                    'job': serialize_agent_job(pending_update),
                },
                status=409,
            )
        update_decision = evaluate_agent_update_policy(
            endpoint,
            manual=selected_release is not None,
            explicit_release=selected_release,
            allow_downgrade=force_update,
        )
        create_audit_event(
            event_type='agent.update_policy_evaluated',
            title='Politica de update manual avaliada',
            description=f'Politica de update manual avaliada para {endpoint.hostname}.',
            severity=AuditEvent.SEVERITY_INFO if update_decision.eligible else AuditEvent.SEVERITY_WARNING,
            actor_type=AuditEvent.ACTOR_USER,
            actor_name=request.user.get_username(),
            endpoint=endpoint,
            metadata={
                'manual': True,
                'eligible': update_decision.eligible,
                'reason_code': update_decision.reason_code,
                'release_id': update_decision.selected_release_id or selected_release_id,
                'target_version': update_decision.target_version,
                'channel': update_decision.channel,
                'rollout_bucket': update_decision.rollout_bucket,
                'force': force_update,
                'operator': request.user.get_username(),
            },
            request=request,
        )
        if not update_decision.eligible or not update_decision.release:
            AgentReleaseAudit.objects.create(
                user=request.user,
                action=AgentReleaseAudit.ACTION_UPDATED,
                release=update_decision.release or selected_release,
                endpoint=endpoint,
                version=update_decision.target_version or (selected_release.version if selected_release else ''),
                channel_after=update_decision.channel or (selected_release.channel if selected_release else ''),
                reason='manual_panel_update_blocked',
                metadata={
                    'reason_code': update_decision.reason_code,
                    'selected_release_id': selected_release_id,
                    'operator': request.user.get_username(),
                },
            )
            return JsonResponse(
                {
                    'error': 'endpoint_not_eligible_for_update',
                    'detail': _update_policy_message(update_decision.reason_code),
                    'reason_code': update_decision.reason_code,
                    'policy': update_decision.as_panel_payload(),
                },
                status=409,
            )
        release = update_decision.release
        payload.update(build_update_agent_job_payload(
            endpoint,
            update_decision,
            force=force_update,
            source='manual_panel',
            manual_explicit=selected_release is not None,
        ))
        if 'manifest_url' not in payload and release.channel == AgentRelease.CHANNEL_DEVELOPMENT:
            create_audit_event(
                event_type='agent.update_legacy_bootstrap_payload',
                title='Payload legado usado para bootstrap de update',
                description=f'Update manual de {endpoint.hostname} usara payload legado para permitir bootstrap ate RC6.',
                severity=AuditEvent.SEVERITY_WARNING,
                actor_type=AuditEvent.ACTOR_USER,
                actor_name=request.user.get_username(),
                endpoint=endpoint,
                metadata={
                    'release_id': str(release.id),
                    'target_version': release.version,
                    'current_version': endpoint.agent_version or '',
                    'channel': release.channel,
                    'reason': 'agent_version_before_rc6',
                },
                request=request,
            )
    elif selected_type == AgentJob.TYPE_RESTART_AGENT:
        payload.update({
            'source': 'manual_panel',
            'reason': 'manual_endpoint_action',
        })
    elif selected_type == AgentJob.TYPE_UPDATE_TRUSTED_RELEASE_KEYS:
        active_trust_job = endpoint.jobs.filter(
            job_type=AgentJob.TYPE_UPDATE_TRUSTED_RELEASE_KEYS,
            status__in=[AgentJob.STATUS_QUEUED, AgentJob.STATUS_SENT, AgentJob.STATUS_RUNNING],
        ).order_by('-created_at').first()
        if active_trust_job:
            return JsonResponse(
                {
                    'error': 'trust_bundle_job_already_pending',
                    'detail': 'Ja existe um job de sincronizacao de chaves confiaveis pendente ou em execucao para este endpoint.',
                    'job': serialize_agent_job(active_trust_job),
                },
                status=409,
            )
        trust_bundle_id = (request.POST.get('trust_bundle_id') or request.POST.get('bundle_id') or '').strip()
        trust_bundle = None
        if trust_bundle_id:
            trust_bundle = AgentReleaseTrustBundle.objects.filter(pk=trust_bundle_id).first()
            if trust_bundle is None:
                return JsonResponse(
                    {
                        'error': 'trust_bundle_not_found',
                        'detail': 'O bundle de confianca selecionado nao foi encontrado.',
                        'reason_code': 'trust_bundle_not_found',
                    },
                    status=409,
                )
            if trust_bundle.status != AgentReleaseTrustBundle.STATUS_PUBLISHED:
                return JsonResponse(
                    {
                        'error': 'trust_bundle_not_published',
                        'detail': 'Somente bundles de confianca publicados podem ser enviados ao endpoint.',
                        'reason_code': 'trust_bundle_not_published',
                    },
                    status=409,
                )
            payload.update({
                'metadata_url': trust_bundle.metadata_url,
                'bundle_url': trust_bundle.bundle_url,
                'signature_url': trust_bundle.signature_url,
                'expected_root_key_id': trust_bundle.root_key_id,
                'expected_bundle_version': trust_bundle.bundle_version,
                'expected_sha256': trust_bundle.bundle_sha256,
                'source': 'manual_panel',
                'timeout_seconds': 180,
            })
        else:
            try:
                expected_bundle_version = int(request.POST.get('expected_bundle_version') or 0)
            except (TypeError, ValueError):
                expected_bundle_version = 0
            payload.update({
                'metadata_url': (request.POST.get('metadata_url') or '').strip(),
                'bundle_url': (request.POST.get('bundle_url') or '').strip(),
                'signature_url': (request.POST.get('signature_url') or '').strip(),
                'expected_root_key_id': (request.POST.get('expected_root_key_id') or '').strip(),
                'expected_bundle_version': expected_bundle_version,
                'expected_sha256': (request.POST.get('expected_sha256') or '').strip(),
                'source': 'manual_panel',
                'timeout_seconds': 180,
            })
        missing = [key for key in ('metadata_url', 'bundle_url', 'signature_url', 'expected_root_key_id') if not payload.get(key)]
        if missing:
            return JsonResponse(
                {
                    'error': 'invalid_trust_bundle_payload',
                    'detail': f'Campos obrigatorios ausentes: {", ".join(missing)}.',
                    'reason_code': 'invalid_trust_bundle_payload',
                },
                status=400,
            )
        create_audit_event(
            event_type='trust.sync.job_requested',
            title='Sincronizacao de trust bundle solicitada',
            description=f'Sincronizacao de chaves confiaveis solicitada para {endpoint.hostname}.',
            severity=AuditEvent.SEVERITY_INFO,
            actor_type=AuditEvent.ACTOR_USER,
            actor_name=request.user.get_username(),
            endpoint=endpoint,
            metadata={
                'trust_bundle_id': trust_bundle_id,
                'bundle_version': payload.get('expected_bundle_version'),
                'root_key_id': payload.get('expected_root_key_id'),
                'bundle_sha256': payload.get('expected_sha256'),
            },
            request=request,
        )

    job = AgentJob.objects.create(
        endpoint=endpoint,
        agent_release=payload.get('release_id') and AgentRelease.objects.filter(pk=payload.get('release_id')).first(),
        job_type=selected_type,
        created_by=request.user.get_username(),
        payload=payload,
        correlation_id=str(uuid.uuid4()),
        attempt=1,
        timeout_seconds=payload.get('timeout_seconds') or 300,
        expires_at=timezone.now() + timedelta(minutes=30),
    )
    create_audit_event(
        event_type='job.created',
        title='Job tecnico criado',
        description=f'Job {selected_type} criado para {endpoint.hostname}.',
        severity=AuditEvent.SEVERITY_INFO,
        actor_type=AuditEvent.ACTOR_USER,
        actor_name=request.user.get_username(),
        endpoint=endpoint,
        metadata={'job_id': str(job.id), 'job_type': selected_type, 'payload': payload},
        request=request,
    )
    logger.info(
        'job.created endpoint_id=%s job_id=%s job_type=%s created_by=%s',
        endpoint.id,
        job.id,
        selected_type,
        request.user.get_username(),
    )
    if selected_type == AgentJob.TYPE_UPDATE_AGENT:
        AgentReleaseAudit.objects.create(
            user=request.user,
            action=AgentReleaseAudit.ACTION_UPDATED,
            release=job.agent_release,
            endpoint=endpoint,
            version=payload.get('target_version', ''),
            channel_after=payload.get('source_channel') or payload.get('channel', ''),
            reason='manual_panel_update_job_created',
            metadata={'job_id': str(job.id), 'reason_code': payload.get('policy_reason') or update_decision.reason_code},
        )
        logger.info(
            'update_agent job created endpoint_id=%s job_id=%s release_id=%s target_version=%s created_by=%s',
            endpoint.id,
            job.id,
            payload.get('release_id', ''),
            payload.get('target_version', ''),
            request.user.get_username(),
        )
    return JsonResponse({'status': 'ok', 'job': serialize_agent_job(job)}, status=201)


@require_POST
def endpoint_job_mark_failed(request, pk, job_id):
    if not is_nightowl_technical_user(request.user):
        return JsonResponse({'error': 'forbidden', 'detail': 'Sem permissao para alterar jobs tecnicos.'}, status=403)
    endpoint = resolve_agent_endpoint(pk)
    if endpoint is None:
        raise Http404
    job = get_object_or_404(AgentJob, pk=job_id, endpoint=endpoint)
    if job.status in {
        AgentJob.STATUS_COMPLETED,
        AgentJob.STATUS_FAILED,
        AgentJob.STATUS_CANCELLED,
        AgentJob.STATUS_TIMED_OUT,
        AgentJob.STATUS_ROLLED_BACK,
        AgentJob.STATUS_ROLLBACK_FAILED,
    }:
        return JsonResponse({'status': 'ok', 'job': serialize_agent_job(job), 'updated': False}, status=200)
    reason = (request.POST.get('reason') or 'Marcado manualmente como falha pelo operador.').strip()[:500]
    job.status = AgentJob.STATUS_FAILED
    job.finished_at = timezone.now()
    job.error_code = 'JOB_MANUALLY_MARKED_FAILED'
    job.error_message = reason
    job.result_received_at = timezone.now()
    if job.started_at:
        job.duration_seconds = max(0, (job.finished_at - job.started_at).total_seconds())
    job.save(update_fields=[
        'status',
        'finished_at',
        'error_code',
        'error_message',
        'result_received_at',
        'duration_seconds',
        'updated_at',
    ])
    create_audit_event(
        event_type='job.marked_failed',
        title='Job marcado como falha',
        description=f'Job {job.job_type} marcado manualmente como falha em {endpoint.hostname}.',
        severity=AuditEvent.SEVERITY_WARNING,
        actor_type=AuditEvent.ACTOR_USER,
        actor_name=request.user.get_username(),
        endpoint=endpoint,
        metadata={'job_id': str(job.id), 'job_type': job.job_type, 'reason': reason},
        request=request,
    )
    logger.warning(
        'job.marked_failed endpoint_id=%s job_id=%s job_type=%s marked_by=%s',
        endpoint.id,
        job.id,
        job.job_type,
        request.user.get_username(),
    )
    return JsonResponse({'status': 'ok', 'job': serialize_agent_job(job), 'updated': True}, status=200)


@require_POST
def endpoint_update_policy_update(request, pk):
    if not is_nightowl_technical_user(request.user):
        return JsonResponse({'error': 'forbidden', 'detail': 'Sem permissao para alterar politica de update.'}, status=403)
    endpoint = resolve_agent_endpoint(pk)
    if endpoint is None:
        raise Http404

    channel_before = endpoint.update_channel or AgentMachine.UPDATE_CHANNEL_STABLE
    rollout_before = None
    channel = (request.POST.get('update_channel') or endpoint.update_channel or AgentMachine.UPDATE_CHANNEL_STABLE).strip()
    policy = (request.POST.get('update_policy') or endpoint.update_policy or AgentMachine.UPDATE_POLICY_MANUAL).strip()
    if channel not in {choice[0] for choice in AgentMachine.UPDATE_CHANNEL_CHOICES}:
        return JsonResponse({'error': 'invalid_channel', 'detail': 'Canal invalido.'}, status=400)
    if policy not in {choice[0] for choice in AgentMachine.UPDATE_POLICY_CHOICES}:
        return JsonResponse({'error': 'invalid_policy', 'detail': 'Politica invalida.'}, status=400)

    endpoint.update_channel = channel
    endpoint.update_policy = policy
    endpoint.auto_update_enabled = (request.POST.get('auto_update_enabled') or '').lower() in {'1', 'true', 'on', 'yes'}
    endpoint.update_paused = (request.POST.get('update_paused') or '').lower() in {'1', 'true', 'on', 'yes'}
    endpoint.pinned_agent_version = (request.POST.get('pinned_agent_version') or '').strip()[:50]
    start = (request.POST.get('maintenance_window_start') or '').strip()
    end = (request.POST.get('maintenance_window_end') or '').strip()
    try:
        endpoint.maintenance_window_start = time.fromisoformat(start) if start else None
        endpoint.maintenance_window_end = time.fromisoformat(end) if end else None
    except ValueError:
        return JsonResponse({'error': 'invalid_maintenance_window', 'detail': 'Janela de manutencao invalida.'}, status=400)
    endpoint.save(update_fields=[
        'update_channel',
        'update_policy',
        'auto_update_enabled',
        'update_paused',
        'pinned_agent_version',
        'maintenance_window_start',
        'maintenance_window_end',
        'updated_at',
    ])

    group_ids = request.POST.getlist('rollout_groups')
    if group_ids:
        endpoint.rollout_groups.set(AgentReleaseGroup.objects.filter(id__in=group_ids))

    AgentReleaseAudit.objects.create(
        user=request.user,
        action=AgentReleaseAudit.ACTION_ENDPOINT_POLICY_CHANGED,
        endpoint=endpoint,
        version=endpoint.pinned_agent_version,
        channel_before=channel_before,
        channel_after=endpoint.update_channel,
        rollout_before=rollout_before,
        reason=(request.POST.get('reason') or '').strip(),
        metadata={
            'update_policy': endpoint.update_policy,
            'auto_update_enabled': endpoint.auto_update_enabled,
            'update_paused': endpoint.update_paused,
            'pinned_agent_version': endpoint.pinned_agent_version,
        },
    )
    decision = evaluate_agent_update_policy(endpoint, manual=False)
    return JsonResponse({'status': 'ok', 'policy': decision.as_panel_payload()})


def _release_metrics(release):
    endpoints = AgentMachine.objects.filter(update_channel=release.channel)
    eligible = 0
    updated = 0
    pending = 0
    failed = 0
    rolled_back = 0
    rollback_failed = 0
    for endpoint in endpoints:
        decision = evaluate_agent_update_policy(endpoint, manual=True, record_evaluation=False)
        if decision.release and decision.release.id == release.id and decision.eligible:
            eligible += 1
        if endpoint.agent_version == release.version:
            updated += 1
    jobs = AgentJob.objects.filter(agent_release=release)
    pending = jobs.filter(status__in=[AgentJob.STATUS_QUEUED, AgentJob.STATUS_SENT, AgentJob.STATUS_RUNNING]).count()
    failed = jobs.filter(status=AgentJob.STATUS_FAILED).count()
    rolled_back = jobs.filter(status=AgentJob.STATUS_ROLLED_BACK).count()
    rollback_failed = jobs.filter(status=AgentJob.STATUS_ROLLBACK_FAILED).count()
    return {
        'eligible': eligible,
        'updated': updated,
        'pending': pending,
        'failed': failed,
        'rolled_back': rolled_back,
        'rollback_failed': rollback_failed,
        'success_percentage': round((updated / max(1, endpoints.count())) * 100, 1),
    }


def agent_releases(request):
    if not is_nightowl_technical_user(request.user):
        return JsonResponse({'error': 'forbidden'}, status=403)
    groups = AgentReleaseGroup.objects.all()
    if request.method == 'POST':
        version = (request.POST.get('version') or '').strip()
        channel = (request.POST.get('channel') or AgentRelease.CHANNEL_STABLE).strip()
        release_status = (request.POST.get('status') or AgentRelease.STATUS_DRAFT).strip()
        package_url = (request.POST.get('package_url') or '').strip()
        checksum_url = (request.POST.get('checksum_url') or '').strip()
        manifest_url = (request.POST.get('manifest_url') or '').strip()
        signature_url = (request.POST.get('signature_url') or '').strip()
        sha256 = (request.POST.get('sha256') or '').strip().lower()
        release_notes = (request.POST.get('release_notes') or '').strip()
        try:
            size = int(request.POST.get('size') or 0)
            rollout_percentage = min(100, max(0, int(request.POST.get('rollout_percentage') or 0)))
        except ValueError:
            messages.error(request, 'Tamanho ou percentual invalido.')
            return redirect('agent-releases')
        if channel not in {choice[0] for choice in AgentRelease.CHANNEL_CHOICES}:
            messages.error(request, 'Canal invalido.')
            return redirect('agent-releases')
        if release_status not in {choice[0] for choice in AgentRelease.STATUS_CHOICES}:
            messages.error(request, 'Status invalido.')
            return redirect('agent-releases')
        if AgentRelease.objects.filter(version=version).exists():
            messages.error(request, 'Esta versao ja existe.')
            return redirect('agent-releases')
        if parse_semver(version) is None:
            messages.error(request, 'Versao invalida. Use SemVer, exemplo 0.1.1.0-rc1.')
            return redirect('agent-releases')
        if not version or not package_url or len(sha256) != 64:
            messages.error(request, 'Versao, URL do pacote e SHA-256 sao obrigatorios.')
            return redirect('agent-releases')
        release = AgentRelease.objects.create(
            version=version,
            channel=channel,
            status=release_status,
            package_url=package_url,
            checksum_url=checksum_url,
            sha256=sha256,
            size=size,
            manifest_url=manifest_url,
            manifest_sha256=(request.POST.get('manifest_sha256') or '').strip().lower(),
            signature_url=signature_url,
            signature_sha256=(request.POST.get('signature_sha256') or '').strip().lower(),
            signature_key_id=(request.POST.get('signature_key_id') or '').strip(),
            signature_valid=bool(request.POST.get('signature_valid')),
            legacy_unsigned=not bool(request.POST.get('signature_valid')),
            released_at=timezone.now() if release_status == AgentRelease.STATUS_PUBLISHED else None,
            published_by=request.user if release_status == AgentRelease.STATUS_PUBLISHED else None,
            minimum_updater_version=(request.POST.get('minimum_updater_version') or '').strip(),
            release_notes=release_notes,
            rollout_percentage=rollout_percentage,
            rollout_paused=release_status == AgentRelease.STATUS_PAUSED,
            mandatory=bool(request.POST.get('mandatory')),
            created_by=request.user,
        )
        group_ids = request.POST.getlist('allowed_groups')
        if group_ids:
            release.allowed_groups.set(groups.filter(id__in=group_ids))
        AgentReleaseAudit.objects.create(
            user=request.user,
            action=AgentReleaseAudit.ACTION_CREATED,
            release=release,
            version=release.version,
            channel_after=release.channel,
            rollout_after=release.rollout_percentage,
            reason='release_created_from_panel',
        )
        messages.success(request, f'Release {release.version} criada.')
        return redirect('agent-releases')

    releases = sort_releases_by_version(
        list(AgentRelease.objects.prefetch_related('allowed_groups').select_related('created_by')),
        reverse=True,
    )[:50]
    rows = [{'release': release, 'metrics': _release_metrics(release)} for release in releases]
    return render(
        request,
        'dashboard/agent_releases.html',
        {
            'active_nav': 'agent_releases',
            'releases': rows,
            'groups': groups,
            'channel_choices': AgentRelease.CHANNEL_CHOICES,
            'status_choices': AgentRelease.STATUS_CHOICES,
        },
    )


@require_POST
def agent_release_action(request, pk):
    if not is_nightowl_technical_user(request.user):
        return JsonResponse({'error': 'forbidden'}, status=403)
    release = get_object_or_404(AgentRelease, pk=pk)
    action = (request.POST.get('action') or '').strip()
    reason = (request.POST.get('reason') or '').strip()
    try:
        if action == 'publish':
            publish_agent_release(
                release,
                request.user,
                reason or 'release_published_from_panel',
                rollout_percentage=int(request.POST.get('rollout_percentage') or release.rollout_percentage or 0),
                rollout_paused=bool(request.POST.get('paused')),
            )
        elif action == 'pause':
            change_agent_release_rollout(release, request.user, release.rollout_percentage, paused=True, reason=reason or 'rollout_paused')
        elif action == 'resume':
            change_agent_release_rollout(release, request.user, release.rollout_percentage, paused=False, reason=reason or 'rollout_resumed')
        elif action == 'revoke':
            replacement_id = (request.POST.get('replacement_release') or '').strip()
            replacement = AgentRelease.objects.filter(pk=replacement_id).first() if replacement_id else None
            revoke_agent_release(release, request.user, reason, replacement)
            AgentJob.objects.filter(
                agent_release=release,
                status__in=[AgentJob.STATUS_QUEUED, AgentJob.STATUS_SENT],
            ).update(status=AgentJob.STATUS_CANCELLED, finished_at=timezone.now(), error_code='RELEASE_REVOKED', error_message='Release revogada antes da execucao.')
        elif action == 'promote':
            promote_agent_release(
                release,
                (request.POST.get('channel') or '').strip(),
                request.user,
                rollout_percentage=int(request.POST.get('rollout_percentage') or 0),
                rollout_paused=bool(request.POST.get('paused')),
                approval_reason=reason,
                allow_prerelease_stable=bool(request.POST.get('allow_prerelease_stable')),
            )
        elif action == 'rollout':
            change_agent_release_rollout(
                release,
                request.user,
                int(request.POST.get('rollout_percentage') or 0),
                paused=None,
                reason=reason or 'rollout_percentage_changed',
            )
        elif action == 'mandatory':
            before = bool(release.mandatory)
            release.mandatory = bool(request.POST.get('mandatory'))
            release.save(update_fields=['mandatory', 'updated_at'])
            AgentReleaseAudit.objects.create(
                user=request.user,
                action=AgentReleaseAudit.ACTION_UPDATED,
                release=release,
                version=release.version,
                channel_before=release.channel,
                channel_after=release.channel,
                rollout_before=release.rollout_percentage,
                rollout_after=release.rollout_percentage,
                reason=reason or 'mandatory_changed',
                metadata={'mandatory_before': before, 'mandatory_after': release.mandatory},
            )
        elif action == 'supersede':
            replacement = get_object_or_404(AgentRelease, pk=(request.POST.get('replacement_release') or '').strip())
            supersede_agent_release(release, replacement, request.user, reason)
        else:
            messages.error(request, 'Acao invalida.')
            return redirect('agent-releases')
    except (ValueError, ValidationError) as exc:
        messages.error(request, str(exc))
        return redirect('agent-releases')
    messages.success(request, f'Release {release.version} atualizada.')
    return redirect('agent-releases')


def agent_install(request):
    created_token = None
    created_enrollment = None
    created_manual_token = None
    created_manual_validation = None
    created_command = ''
    now = timezone.now()

    if request.method == 'POST':
        action = request.POST.get('action', 'enrollment').strip()
        name = request.POST.get('name', '').strip()
        allowed_domain = request.POST.get('allowed_domain', '').strip().lower()
        notes = request.POST.get('notes', '').strip()
        try:
            expires_hours = int(request.POST.get('expires_hours') or 168)
        except ValueError:
            expires_hours = 168
        max_uses_raw = request.POST.get('max_uses', '').strip()
        max_uses = None
        if max_uses_raw:
            try:
                max_uses = int(max_uses_raw)
            except ValueError:
                max_uses = None

        if action == 'manual_validation':
            manual_name = name or 'Validacao manual fora do dominio'
            try:
                expires_minutes = int(request.POST.get('expires_minutes') or 30)
            except ValueError:
                expires_minutes = 30
            if expires_minutes <= 0:
                messages.warning(request, 'A validade do token manual deve ser maior que zero.')
            else:
                created_manual_validation, created_manual_token = AgentManualValidationToken.create_with_token(
                    name=manual_name,
                    expires_at=now + timedelta(minutes=expires_minutes),
                    notes=notes or 'Token manual gerado pelo painel de instalacao do agente.',
                )
                create_audit_event(
                    event_type='agent.manual_validation_token_created',
                    title='Token de validacao manual criado',
                    description=f'Token manual criado: {created_manual_validation.name}.',
                    severity=AuditEvent.SEVERITY_INFO,
                    metadata={
                        'manual_validation_token_id': str(created_manual_validation.id),
                        'prefix': created_manual_validation.prefix,
                    },
                    request=request,
                )
                messages.success(request, 'Token de validacao manual criado. Copie agora; ele nao sera exibido novamente.')
        elif not name:
            messages.warning(request, 'Informe um nome para o token.')
        elif expires_hours <= 0:
            messages.warning(request, 'A validade deve ser maior que zero.')
        elif max_uses is not None and max_uses <= 0:
            messages.warning(request, 'O limite de usos deve ser maior que zero.')
        else:
            enrollment, created_token = AgentEnrollmentToken.create_with_token(
                name=name,
                expires_at=now + timedelta(hours=expires_hours),
                max_uses=max_uses,
                allowed_domain=allowed_domain,
                notes=notes,
            )
            created_enrollment = enrollment
            created_command = build_agent_install_command()
            create_audit_event(
                event_type='agent.enrollment_token_created',
                title='Enrollment token criado',
                description=f'Enrollment token criado: {enrollment.name}.',
                severity=AuditEvent.SEVERITY_INFO,
                metadata={
                    'enrollment_token_id': str(enrollment.id),
                    'prefix': enrollment.prefix,
                    'allowed_domain': enrollment.allowed_domain,
                    'max_uses': enrollment.max_uses,
                },
                request=request,
            )
            messages.success(request, 'Enrollment token criado. Copie o token agora; ele nao sera exibido novamente.')

    tokens = AgentEnrollmentToken.objects.order_by('-created_at')
    token_rows = []
    for token in tokens:
        token_rows.append({
            'token': token,
            'state': enrollment_token_state(token),
            'logs': token.logs.select_related('endpoint').order_by('-created_at')[:5],
            'command_without_token': build_agent_install_command(),
        })

    last_24h = now - timedelta(hours=24)
    configured_heartbeat_url = getattr(settings, 'NIGHTOWL_AGENT_HEARTBEAT_URL', '').strip()
    public_url = getattr(settings, 'NIGHTOWL_PUBLIC_URL', '').strip().rstrip('/')
    request_base_url = request.build_absolute_uri('/').rstrip('/')
    default_server_url = public_url or normalize_agent_server_base(configured_heartbeat_url) or request_base_url
    default_heartbeat_url = agent_heartbeat_url_from_base(default_server_url)
    package_source_path = normalize_powershell_path(
        getattr(settings, 'NIGHTOWL_AGENT_INSTALLER_URL', '').strip()
        or f'{default_server_url}/downloads/nightowl-agent/Install-NightOwlAgentDotNet.ps1'
    )
    legacy_share_path = normalize_powershell_path(
        getattr(settings, 'NIGHTOWL_AGENT_SOURCE_PATH', r'\\192.168.104.120\controlsul\Comum\_Agents')
    )
    failure_statuses = [
        AgentEnrollmentLog.STATUS_DENIED,
        AgentEnrollmentLog.STATUS_EXPIRED,
        AgentEnrollmentLog.STATUS_INACTIVE,
        AgentEnrollmentLog.STATUS_USAGE_LIMIT_REACHED,
        AgentEnrollmentLog.STATUS_INVALID_TOKEN,
        AgentEnrollmentLog.STATUS_DOMAIN_DENIED,
        AgentEnrollmentLog.STATUS_INVALID_MANUAL_VALIDATION_TOKEN,
        AgentEnrollmentLog.STATUS_MANUAL_VALIDATION_TOKEN_EXPIRED,
        AgentEnrollmentLog.STATUS_MANUAL_VALIDATION_TOKEN_USED,
        AgentEnrollmentLog.STATUS_ERROR,
    ]
    context = {
        'active_nav': 'agents',
        'rows': token_rows,
        'created_token': created_token,
        'created_enrollment': created_enrollment,
        'created_manual_token': created_manual_token,
        'created_manual_validation': created_manual_validation,
        'created_command': created_command,
        'heartbeat_url': default_heartbeat_url,
        'server_url': default_server_url,
        'source_path': package_source_path,
        'legacy_share_path': legacy_share_path,
        'package_url': f'{default_server_url}/downloads/nightowl-agent/NightOwl.Agent.Windows.zip',
        'download_page_url': f'{default_server_url}/agents/download/',
        'local_package_path': getattr(settings, 'NIGHTOWL_AGENT_LOCAL_PACKAGE_PATH', r'C:\NightOwlAgents'),
        'recommended_agent_version': latest_agent_version(),
        'active_count': sum(1 for row in token_rows if row['state']['key'] == 'online'),
        'expired_count': sum(1 for row in token_rows if row['state']['key'] == 'unknown'),
        'uses_24h_count': AgentEnrollmentLog.objects.filter(created_at__gte=last_24h).count(),
        'success_24h_count': AgentEnrollmentLog.objects.filter(
            created_at__gte=last_24h,
            status=AgentEnrollmentLog.STATUS_SUCCESS,
        ).count(),
        'failure_24h_count': AgentEnrollmentLog.objects.filter(
            created_at__gte=last_24h,
            status__in=failure_statuses,
        ).count(),
        'default_allowed_domain': 'control.local',
    }
    return render(request, 'dashboard/agent_install.html', context)


@require_POST
def agent_enrollment_revoke(request, pk):
    enrollment = get_object_or_404(AgentEnrollmentToken, pk=pk)
    if enrollment.is_active:
        enrollment.is_active = False
        enrollment.save(update_fields=['is_active', 'updated_at'])
        create_audit_event(
            event_type='agent.enrollment_token_revoked',
            title='Enrollment token revogado',
            description=f'Enrollment token revogado: {enrollment.name}.',
            severity=AuditEvent.SEVERITY_WARNING,
            metadata={
                'enrollment_token_id': str(enrollment.id),
                'prefix': enrollment.prefix,
                'allowed_domain': enrollment.allowed_domain,
            },
            request=request,
        )
        messages.warning(request, 'Enrollment token revogado.')
    else:
        messages.info(request, 'Este enrollment token ja estava inativo.')
    return redirect('agent-install')


def software_inventory(request):
    rows, summary = build_software_inventory()
    if request.GET.get('mock') == '1' or not rows:
        rows, summary = mock_software_inventory_rows()
    filters = {
        'q': request.GET.get('q', '').strip(),
        'category': request.GET.get('category', 'all').strip() or 'all',
        'risk': request.GET.get('risk', 'all').strip() or 'all',
        'publisher': request.GET.get('publisher', '').strip(),
        'version': request.GET.get('version', '').strip(),
        'endpoint': request.GET.get('endpoint', '').strip(),
        'sensitive': request.GET.get('sensitive', 'all').strip() or 'all',
    }
    filtered_rows = [row for row in rows if software_row_matches(row, filters)]

    context = {
        'active_nav': 'software',
        'rows': filtered_rows,
        'summary': {
            **summary,
            'filtered_count': len(filtered_rows),
        },
        'filters': filters,
        'category_options': SOFTWARE_CATEGORY_OPTIONS,
        'risk_options': SOFTWARE_RISK_OPTIONS,
        'publisher_options': sorted({row['publisher'] for row in rows if row['publisher']}),
        'version_options': sorted({version for row in rows for version in row['versions']}),
        'updated_at': timezone.now(),
    }
    return render(request, 'dashboard/software_inventory.html', context)


def policy_status(policy):
    if not policy.is_active:
        return 'inactive', 'Inativa'
    if policy.monitor_only:
        return 'monitor_only', 'Monitoramento'
    return 'active', 'Ativa'


def policy_scope_label(policy):
    if policy.scope_type == SoftwarePolicy.SCOPE_ALL:
        return 'Todos os endpoints'
    if policy.scope_type == SoftwarePolicy.SCOPE_SPECIFIC_ENDPOINTS:
        count = policy.target_endpoints.count()
        return f'{count} endpoint{"s" if count != 1 else ""} especifico{"s" if count != 1 else ""}'
    return f'{policy.get_scope_type_display()}: {policy.scope_value}'


def policy_behavior(policy):
    behavior = []
    if policy.create_alert:
        behavior.append('Gerar alerta')
    if policy.show_in_noc:
        behavior.append('Mostrar no NOC')
    if policy.create_audit_event:
        behavior.append('Criar auditoria')
    if policy.monitor_only:
        behavior.append('Somente monitoramento')
    return behavior or ['Nenhuma acao operacional configurada']


def serialize_policy(policy):
    status_key, status_label = policy_status(policy)
    active_exceptions = [item for item in policy.exceptions.all() if item.is_active]
    open_violations = [item for item in policy.violations.all() if item.status == SoftwarePolicyViolation.STATUS_OPEN]
    target_endpoints = [
        {
            'id': str(item.endpoint_id),
            'hostname': item.endpoint.hostname,
            'domain': item.endpoint.domain or 'sem dominio',
            'status': item.endpoint.status,
            'url': f'/endpoints/{item.endpoint_id}/',
            'label': f'{item.endpoint.hostname} Â· {item.endpoint.domain or "sem dominio"} Â· {item.endpoint.status}',
        }
        for item in policy.target_endpoints.all()
    ]
    return {
        'id': str(policy.id),
        'name': policy.name,
        'description': policy.description,
        'type': policy.policy_type,
        'type_label': policy.get_policy_type_display(),
        'software': policy.software_name,
        'match_type': policy.get_match_type_display(),
        'match_type_value': policy.match_type,
        'publisher': policy.publisher,
        'version': policy.version_rule or 'Qualquer',
        'scope': policy_scope_label(policy),
        'scope_type': policy.scope_type,
        'scope_value': policy.scope_value,
        'target_endpoints': target_endpoints,
        'target_endpoint_ids': [item['id'] for item in target_endpoints],
        'severity': policy.severity,
        'severity_label': policy.get_severity_display(),
        'status': status_key,
        'status_label': status_label,
        'is_active': policy.is_active,
        'monitor_only': policy.monitor_only,
        'create_alert': policy.create_alert,
        'show_in_noc': policy.show_in_noc,
        'create_audit_event': policy.create_audit_event,
        'violations_open': len(open_violations),
        'exceptions_active': len(active_exceptions),
        'updated_at': policy.updated_at.strftime('%d/%m %H:%M'),
        'behavior': policy_behavior(policy),
    }


def serialize_policy_violation(violation):
    return {
        'id': str(violation.id),
        'policy_id': str(violation.policy_id),
        'endpoint_id': str(violation.endpoint_id),
        'endpoint': violation.endpoint.hostname,
        'endpoint_url': f'/endpoints/{violation.endpoint_id}/',
        'software_name': violation.software_name or violation.policy.software_name,
        'software_version': violation.software_version or '—',
        'publisher': violation.publisher or '—',
        'severity': violation.severity,
        'severity_label': violation.get_severity_display(),
        'status': violation.status,
        'status_label': violation.get_status_display(),
        'first_seen_at': violation.first_seen_at.strftime('%d/%m/%Y %H:%M') if violation.first_seen_at else '—',
        'last_seen_at': violation.last_seen_at.strftime('%d/%m/%Y %H:%M') if violation.last_seen_at else '—',
        'alert_id': str(violation.alert_id) if violation.alert_id else '',
        'alert_label': 'Alerta vinculado' if violation.alert_id else 'Sem alerta',
        'resolution_reason': violation.resolution_reason or '',
    }


def serialize_policy_exception(exception):
    return {
        'id': str(exception.id),
        'policy_id': str(exception.policy_id),
        'policy': exception.policy.name,
        'endpoint_id': str(exception.endpoint_id),
        'endpoint': exception.endpoint.hostname,
        'endpoint_label': f'{exception.endpoint.hostname} · {exception.endpoint.domain or "sem dominio"} · {exception.endpoint.status}',
        'reason': exception.reason,
        'exception_type': exception.exception_type,
        'exception_type_label': exception.get_exception_type_display(),
        'expires_at': exception.expires_at.strftime('%d/%m/%Y %H:%M') if exception.expires_at else 'Permanente',
        'expires_value': exception.expires_at.strftime('%Y-%m-%d') if exception.expires_at else '',
        'status': exception.status_key,
        'status_label': exception.status_label,
        'created_by': 'Night Owl',
        'created_at': exception.created_at.strftime('%d/%m %H:%M'),
    }


def policy_audit_logs(policy):
    events = AuditEvent.objects.filter(
        event_type__startswith='software_policy',
        metadata__policy_id=str(policy.id),
    ).order_by('-created_at')[:20]
    return [
        {
            'time': event.created_at.strftime('%d/%m %H:%M'),
            'title': event.title,
            'description': event.description,
            'severity': event.severity,
            'event_type': event.event_type,
        }
        for event in events
    ]


def software_policies(request):
    if request.GET.get('mock') == '1' or not SoftwarePolicy.objects.exists():
        endpoints = mock_rmm_endpoints()
        policies, violations, exceptions, logs_by_policy = mock_policy_context(endpoints)
        endpoint_options_data = [
            {
                'id': str(endpoint.id),
                'label': f'{endpoint.hostname} - {endpoint.domain or "sem dominio"} - {endpoint.status}',
            }
            for endpoint in endpoints
        ]
        summary = {
            'active': sum(policy['is_active'] and not policy['monitor_only'] for policy in policies),
            'inactive': sum(not policy['is_active'] for policy in policies),
            'monitoring': sum(policy['monitor_only'] for policy in policies),
            'exceptions': len(exceptions),
            'expired_exceptions': sum(exception['status'] == 'expired' for exception in exceptions),
            'total': len(policies),
        }
        context = {
            'active_nav': 'software_policies',
            'policies': policies,
            'violations': violations,
            'exceptions': exceptions,
            'logs_by_policy': logs_by_policy,
            'summary': summary,
            'endpoint_options': endpoints,
            'endpoint_options_data': endpoint_options_data,
            'policy_type_options': SoftwarePolicy.TYPE_CHOICES,
            'match_type_options': SoftwarePolicy.MATCH_CHOICES,
            'scope_type_options': SoftwarePolicy.SCOPE_CHOICES,
            'severity_options': SoftwarePolicy.SEVERITY_CHOICES,
            'using_mock_rmm_data': True,
        }
        return render(request, 'dashboard/software_policies.html', context)

    policy_queryset = SoftwarePolicy.objects.prefetch_related(
        'exceptions__endpoint',
        'violations__endpoint',
        'target_endpoints__endpoint',
    ).order_by('-updated_at')
    policies = [serialize_policy(policy) for policy in policy_queryset]
    violations_queryset = SoftwarePolicyViolation.objects.select_related('policy', 'endpoint', 'alert').order_by('-last_seen_at')
    violations = [serialize_policy_violation(violation) for violation in violations_queryset]
    exceptions_queryset = SoftwarePolicyException.objects.select_related('policy', 'endpoint').order_by('-created_at')
    exceptions = [serialize_policy_exception(exception) for exception in exceptions_queryset]
    logs_by_policy = {
        str(policy.id): policy_audit_logs(policy)
        for policy in policy_queryset
    }
    endpoint_options = AgentMachine.objects.order_by('hostname', 'domain')
    endpoint_options_data = [
        {
            'id': str(endpoint.id),
            'label': f'{endpoint.hostname} · {endpoint.domain or "sem dominio"} · {endpoint.status}',
        }
        for endpoint in endpoint_options
    ]
    summary = {
        'active': SoftwarePolicy.objects.filter(is_active=True).count(),
        'inactive': SoftwarePolicy.objects.filter(is_active=False).count(),
        'monitoring': SoftwarePolicy.objects.filter(monitor_only=True).count(),
        'exceptions': SoftwarePolicyException.objects.filter(is_active=True).count(),
        'expired_exceptions': sum(
            1 for exception in exceptions_queryset
            if exception.status_key == 'expired'
        ),
        'total': SoftwarePolicy.objects.count(),
    }
    context = {
        'active_nav': 'software_policies',
        'policies': policies,
        'violations': violations,
        'exceptions': exceptions,
        'logs_by_policy': logs_by_policy,
        'summary': summary,
        'endpoint_options': endpoint_options,
        'endpoint_options_data': endpoint_options_data,
        'policy_type_options': SoftwarePolicy.TYPE_CHOICES,
        'match_type_options': SoftwarePolicy.MATCH_CHOICES,
        'scope_type_options': SoftwarePolicy.SCOPE_CHOICES,
        'severity_options': SoftwarePolicy.SEVERITY_CHOICES,
    }
    return render(request, 'dashboard/software_policies.html', context)


def checked(request, name):
    return request.POST.get(name) in {'on', 'true', '1', 'yes'}


def parse_exception_expires(value):
    value = (value or '').strip()
    if not value:
        return None
    parsed_datetime = parse_datetime(value)
    if parsed_datetime:
        if timezone.is_naive(parsed_datetime):
            return timezone.make_aware(parsed_datetime)
        return parsed_datetime
    parsed_date = parse_date(value)
    if parsed_date:
        return timezone.make_aware(datetime.combine(parsed_date, time(hour=23, minute=59, second=59)))
    return None


def apply_policy_post(policy, request):
    policy.name = request.POST.get('name', '').strip()
    policy.description = request.POST.get('description', '').strip()
    policy.policy_type = request.POST.get('policy_type', '').strip()
    policy.software_name = request.POST.get('software_name', '').strip()
    policy.match_type = request.POST.get('match_type', '').strip()
    policy.publisher = request.POST.get('publisher', '').strip()
    policy.version_rule = request.POST.get('version_rule', '').strip()
    policy.scope_type = request.POST.get('scope_type', '').strip()
    policy.scope_value = request.POST.get('scope_value', '').strip()
    policy.severity = request.POST.get('severity', '').strip()
    policy.is_active = checked(request, 'is_active')
    policy.monitor_only = checked(request, 'monitor_only')
    policy.create_alert = checked(request, 'create_alert')
    policy.show_in_noc = checked(request, 'show_in_noc')
    policy.create_audit_event = checked(request, 'create_audit_event')
    if policy.policy_type == SoftwarePolicy.TYPE_OBSERVED:
        policy.monitor_only = True
    policy.full_clean()
    policy.save()
    return policy


def selected_target_endpoint_ids(request):
    endpoint_ids = []
    seen = set()
    for endpoint_id in request.POST.getlist('target_endpoint'):
        endpoint_id = (endpoint_id or '').strip()
        if endpoint_id and endpoint_id not in seen:
            endpoint_ids.append(endpoint_id)
            seen.add(endpoint_id)
    return endpoint_ids


def sync_policy_target_endpoints(request, policy):
    selected_ids = selected_target_endpoint_ids(request)
    if policy.scope_type != SoftwarePolicy.SCOPE_SPECIFIC_ENDPOINTS:
        selected_ids = []
    elif not selected_ids:
        raise ValidationError({'target_endpoints': 'Selecione ao menos um endpoint para o escopo especifico.'})

    endpoints = list(AgentMachine.objects.filter(id__in=selected_ids))
    found_ids = {str(endpoint.id) for endpoint in endpoints}
    missing_ids = [endpoint_id for endpoint_id in selected_ids if endpoint_id not in found_ids]
    if missing_ids:
        raise ValidationError({'target_endpoints': 'Um ou mais endpoints selecionados nao foram encontrados.'})

    previous_ids = set(policy.target_endpoints.values_list('endpoint_id', flat=True))
    desired_ids = {endpoint.id for endpoint in endpoints}
    removed_ids = previous_ids - desired_ids
    added_endpoints = [endpoint for endpoint in endpoints if endpoint.id not in previous_ids]

    if removed_ids:
        SoftwarePolicyTargetEndpoint.objects.filter(policy=policy, endpoint_id__in=removed_ids).delete()
    created = 0
    for endpoint in added_endpoints:
        SoftwarePolicyTargetEndpoint.objects.get_or_create(policy=policy, endpoint=endpoint)
        created += 1

    if added_endpoints or removed_ids:
        create_audit_event(
            event_type='software_policy.target_endpoints_updated',
            title='Endpoints-alvo atualizados',
            description=f'Endpoints-alvo atualizados na politica {policy.name}.',
            severity=AuditEvent.SEVERITY_INFO,
            metadata={
                'policy_id': str(policy.id),
                'policy_name': policy.name,
                'added_count': len(added_endpoints),
                'removed_count': len(removed_ids),
                'target_count': len(desired_ids),
                'added_endpoint_ids': [str(endpoint.id) for endpoint in added_endpoints],
                'removed_endpoint_ids': [str(endpoint_id) for endpoint_id in removed_ids],
            },
            request=request,
        )

    for endpoint in added_endpoints:
        create_audit_event(
            event_type='software_policy.target_endpoint_added',
            title='Endpoint-alvo adicionado',
            description=f'{endpoint.hostname} passou a fazer parte do escopo da politica {policy.name}.',
            severity=AuditEvent.SEVERITY_INFO,
            endpoint=endpoint,
            metadata={
                'policy_id': str(policy.id),
                'policy_name': policy.name,
                'endpoint_id': str(endpoint.id),
                'endpoint_hostname': endpoint.hostname,
            },
            request=request,
        )

    for endpoint_id in removed_ids:
        create_audit_event(
            event_type='software_policy.target_endpoint_removed',
            title='Endpoint-alvo removido',
            description=f'Endpoint removido do escopo da politica {policy.name}.',
            severity=AuditEvent.SEVERITY_INFO,
            metadata={
                'policy_id': str(policy.id),
                'policy_name': policy.name,
                'endpoint_id': str(endpoint_id),
            },
            request=request,
        )

    return {'added': created, 'removed': len(removed_ids), 'total': len(desired_ids)}


def audit_software_policy(request, event_type, title, policy, severity=AuditEvent.SEVERITY_INFO, description='', metadata=None):
    create_audit_event(
        event_type=event_type,
        title=title,
        description=description,
        severity=severity,
        metadata={
            'policy_id': str(policy.id),
            'policy_name': policy.name,
            **(metadata or {}),
        },
        request=request,
    )


def create_policy_exceptions_from_post(request, policy):
    created = 0
    endpoint_ids = request.POST.getlist('exception_endpoint')
    reasons = request.POST.getlist('exception_reason')
    types = request.POST.getlist('exception_type')
    expires_values = request.POST.getlist('exception_expires_at')
    for index, endpoint_id in enumerate(endpoint_ids):
        endpoint_id = (endpoint_id or '').strip()
        if not endpoint_id:
            continue
        endpoint = AgentMachine.objects.filter(id=endpoint_id).first()
        if endpoint is None:
            messages.warning(request, 'Endpoint de excecao nao encontrado; linha ignorada.')
            continue
        exception_type = (types[index] if index < len(types) else SoftwarePolicyException.TYPE_TEMPORARY).strip()
        reason = (reasons[index] if index < len(reasons) else '').strip()
        expires_at = parse_exception_expires(expires_values[index] if index < len(expires_values) else '')
        exception = SoftwarePolicyException(
            policy=policy,
            endpoint=endpoint,
            reason=reason,
            exception_type=exception_type,
            expires_at=expires_at,
        )
        try:
            exception.full_clean()
            exception.save()
        except ValidationError as error:
            messages.warning(request, f'Excecao para {endpoint.hostname} ignorada: {error.messages[0]}')
            continue
        except IntegrityError:
            messages.warning(request, f'Ja existe excecao ativa para {endpoint.hostname} nesta politica.')
            continue
        created += 1
        create_audit_event(
            event_type='software_policy_exception.created',
            title='Excecao de politica criada',
            description=f'Excecao criada para {endpoint.hostname} na politica {policy.name}.',
            severity=AuditEvent.SEVERITY_INFO,
            endpoint=endpoint,
            metadata={
                'policy_id': str(policy.id),
                'policy_name': policy.name,
                'exception_id': str(exception.id),
                'endpoint_id': str(endpoint.id),
            },
            request=request,
        )
    return created


@require_POST
def software_policy_create(request):
    try:
        with transaction.atomic():
            policy = apply_policy_post(SoftwarePolicy(), request)
            target_result = sync_policy_target_endpoints(request, policy)
            created_exceptions = create_policy_exceptions_from_post(request, policy)
            audit_software_policy(
                request,
                'software_policy.created',
                'Politica de software criada',
                policy,
                description=f'Politica criada: {policy.name}.',
                metadata={'exceptions_created': created_exceptions, 'target_endpoints': target_result},
            )
        messages.success(request, 'Politica criada com sucesso.')
    except ValidationError as error:
        messages.error(request, 'Erro ao criar politica: ' + '; '.join(error.messages))
    return redirect('software-policies')


@require_POST
def software_policy_update(request, pk):
    policy = get_object_or_404(SoftwarePolicy, pk=pk)
    try:
        with transaction.atomic():
            apply_policy_post(policy, request)
            target_result = sync_policy_target_endpoints(request, policy)
            audit_software_policy(
                request,
                'software_policy.updated',
                'Politica de software atualizada',
                policy,
                description=f'Politica atualizada: {policy.name}.',
                metadata={'target_endpoints': target_result},
            )
        messages.success(request, 'Politica atualizada com sucesso.')
    except ValidationError as error:
        messages.error(request, 'Erro ao atualizar politica: ' + '; '.join(error.messages))
    return redirect('software-policies')


@require_POST
def software_policy_toggle_active(request, pk):
    policy = get_object_or_404(SoftwarePolicy, pk=pk)
    policy.is_active = not policy.is_active
    policy.save(update_fields=['is_active', 'updated_at'])
    if policy.is_active:
        event_type = 'software_policy.activated'
        title = 'Politica de software reativada'
        severity = AuditEvent.SEVERITY_INFO
        message = 'Politica reativada.'
    else:
        event_type = 'software_policy.deactivated'
        title = 'Politica de software desativada'
        severity = AuditEvent.SEVERITY_WARNING
        message = 'Politica desativada.'
    audit_software_policy(request, event_type, title, policy, severity=severity, description=title)

    is_async = (
        request.headers.get('x-requested-with') == 'XMLHttpRequest'
        or 'application/json' in request.headers.get('Accept', '')
    )
    if is_async:
        status, status_label = policy_status(policy)
        return JsonResponse({
            'status': 'ok',
            'policy_id': str(policy.id),
            'is_active': policy.is_active,
            'policy_status': status,
            'policy_status_label': status_label,
            'button_label': 'Desativar' if policy.is_active else 'Reativar',
            'message': 'Política reativada' if policy.is_active else 'Política desativada',
        })

    if policy.is_active:
        messages.success(request, message)
    else:
        messages.warning(request, message)
    return redirect('software-policies')


@require_POST
def software_policy_delete(request, pk):
    policy = get_object_or_404(SoftwarePolicy, pk=pk)
    policy_name = policy.name
    policy_id = str(policy.id)
    policy.delete()
    create_audit_event(
        event_type='software_policy.deleted',
        title='Politica de software excluida',
        description=f'Politica excluida: {policy_name}.',
        severity=AuditEvent.SEVERITY_WARNING,
        metadata={'policy_id': policy_id, 'policy_name': policy_name},
        request=request,
    )
    messages.warning(request, 'Politica excluida.')
    return redirect('software-policies')


@require_POST
def software_policy_exception_add(request, pk):
    policy = get_object_or_404(SoftwarePolicy, pk=pk)
    created = create_policy_exceptions_from_post(request, policy)
    if created:
        messages.success(request, f'{created} excecao(oes) adicionada(s).')
    else:
        messages.warning(request, 'Nenhuma excecao foi adicionada.')
    return redirect('software-policies')


@require_POST
def software_policy_exception_remove(request, pk):
    exception = get_object_or_404(SoftwarePolicyException.objects.select_related('policy', 'endpoint'), pk=pk)
    if exception.is_active:
        exception.is_active = False
        exception.save(update_fields=['is_active', 'updated_at'])
        create_audit_event(
            event_type='software_policy_exception.removed',
            title='Excecao de politica removida',
            description=f'Excecao removida para {exception.endpoint.hostname} na politica {exception.policy.name}.',
            severity=AuditEvent.SEVERITY_WARNING,
            endpoint=exception.endpoint,
            metadata={
                'policy_id': str(exception.policy_id),
                'policy_name': exception.policy.name,
                'exception_id': str(exception.id),
                'endpoint_id': str(exception.endpoint_id),
            },
            request=request,
        )
        messages.warning(request, 'Excecao removida.')
    else:
        messages.info(request, 'Esta excecao ja estava inativa.')
    return redirect('software-policies')


def software_detail(request):
    name = request.GET.get('name', '').strip()
    publisher = request.GET.get('publisher', '').strip()
    rows, _summary = build_software_inventory()
    selected = None
    name_key = normalize_key(name)
    publisher_key = normalize_key(publisher)
    for row in rows:
        if normalize_key(row['name']) == name_key and (not publisher or normalize_key(row['publisher']) == publisher_key):
            selected = row
            break

    if selected is None:
        return render(request, 'dashboard/software_detail.html', {
            'active_nav': 'software',
            'software': None,
            'endpoint_rows': [],
            'filters': {'q': '', 'version': '', 'status': ''},
        })

    filters = {
        'q': request.GET.get('q', '').strip(),
        'version': request.GET.get('version', '').strip(),
        'status': request.GET.get('status', '').strip(),
    }
    endpoint_rows = sorted(selected['endpoints'], key=lambda item: item['endpoint'].hostname)
    if filters['q']:
        q = filters['q'].lower()
        endpoint_rows = [
            item for item in endpoint_rows
            if q in item['endpoint'].hostname.lower()
            or q in item['endpoint'].domain.lower()
            or q in (item['endpoint'].last_logged_user or '').lower()
        ]
    if filters['version']:
        endpoint_rows = [item for item in endpoint_rows if item['version'] == filters['version']]
    if filters['status']:
        endpoint_rows = [item for item in endpoint_rows if item['endpoint'].status == filters['status']]

    context = {
        'active_nav': 'software',
        'software': selected,
        'endpoint_rows': endpoint_rows,
        'filters': filters,
        'status_options': AgentMachine.STATUS_CHOICES,
    }
    return render(request, 'dashboard/software_detail.html', context)


def software_export(request):
    rows, _summary = build_software_inventory()
    filters = {
        'q': request.GET.get('q', '').strip(),
        'category': request.GET.get('category', 'all').strip() or 'all',
        'risk': request.GET.get('risk', 'all').strip() or 'all',
        'publisher': request.GET.get('publisher', '').strip(),
        'version': request.GET.get('version', '').strip(),
        'endpoint': request.GET.get('endpoint', '').strip(),
        'sensitive': request.GET.get('sensitive', 'all').strip() or 'all',
    }
    filtered_rows = [row for row in rows if software_row_matches(row, filters)]
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="nightowl-softwares.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(['name', 'publisher', 'category', 'risk_level', 'endpoint_count', 'versions', 'latest_seen_at'])
    for row in filtered_rows:
        writer.writerow([
            row['name'],
            row['publisher'],
            row['category'],
            row['risk_level'],
            row['endpoint_count'],
            '; '.join(row['versions']),
            row['latest_seen_at'].isoformat() if row['latest_seen_at'] else '',
        ])
    return response
