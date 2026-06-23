from .settings_rmm_context import get_settings_rmm_context
from .ticket_alert_mapping import alert_mapping_summary


def build_ticket_settings_context(request):
    section = request.GET.get('section', 'general')
    sections = [
        {'key': 'general', 'label': 'Geral', 'icon': 'settings'},
        {'key': 'sla', 'label': 'SLA', 'icon': 'timer'},
        {'key': 'notifications', 'label': 'Notificacoes', 'icon': 'bell'},
        {'key': 'integrations', 'label': 'Integracoes', 'icon': 'plug-zap'},
        {'key': 'permissions', 'label': 'Permissoes', 'icon': 'shield-check'},
        {'key': 'audit', 'label': 'Auditoria', 'icon': 'history'},
    ]
    if section not in {item['key'] for item in sections}:
        section = 'general'
    return {
        'settings_section': section,
        'settings_sections': sections,
        'rmm_status': get_settings_rmm_context(),
        'alert_mapping': alert_mapping_summary(),
        'sla_rows': [
            {'priority': 'Critica', 'first_response': '15 min', 'resolution': '4h'},
            {'priority': 'Alta', 'first_response': '30 min', 'resolution': '8h'},
            {'priority': 'Normal', 'first_response': '1h', 'resolution': '1d'},
            {'priority': 'Baixa', 'first_response': '4h', 'resolution': '3d'},
        ],
        'permission_actions': ['Fechar chamado', 'Editar regras de automacao', 'Ver dashboard', 'Editar categorias', 'Forcar sincronizacao RMM'],
        'permission_roles': ['Tecnico', 'Supervisor', 'Gerente'],
        'audit_rows': [
            {'when': 'Hoje, 08:40', 'actor': 'Night Owl', 'event': 'Regra RMM avaliada', 'target': 'Automacoes'},
            {'when': 'Ontem, 17:12', 'actor': 'Gabriel', 'event': 'Categoria Seguranca revisada', 'target': 'Categorias'},
            {'when': 'Ontem, 15:08', 'actor': 'Sistema', 'event': 'SLA recalculado', 'target': 'Dashboard'},
        ],
    }
