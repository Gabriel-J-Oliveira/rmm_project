from .ticket_alert_mapping import alert_mapping_summary


def _rmm_rule_from_mapping():
    mapping = alert_mapping_summary()
    severities = ', '.join([
        severity for severity, priority in mapping['priority_rules'].items()
        if priority in {'critical', 'high'}
    ])
    return {
        'icon': 'activity',
        'name': 'Criar chamados para alertas RMM relevantes',
        'enabled': True,
        'trigger_count': 14,
        'when': f'Alerta RMM com severidade {severities}',
        'then': 'Criar chamado automaticamente usando o mapeamento de alerta para categoria e prioridade',
        'source': 'Mapeamento compartilhado',
    }


def build_automation_rules_context():
    rules = [
        _rmm_rule_from_mapping(),
        {
            'icon': 'user-check',
            'name': 'Atribuir acessos ao time de suporte',
            'enabled': True,
            'trigger_count': 8,
            'when': 'Categoria e Acesso e prioridade Normal ou Alta',
            'then': 'Atribuir ao tecnico com menor carga aberta',
            'source': 'Regra operacional',
        },
        {
            'icon': 'badge-alert',
            'name': 'Elevar chamados de socios',
            'enabled': True,
            'trigger_count': 5,
            'when': 'Solicitante marcado como socio/VIP',
            'then': 'Mudar prioridade para Critica e notificar supervisor',
            'source': 'Regra de atendimento',
        },
        {
            'icon': 'clock-alert',
            'name': 'Avisar chamados sem resposta',
            'enabled': False,
            'trigger_count': 0,
            'when': 'Tempo sem primeira resposta maior que 2h',
            'then': 'Notificar responsavel e supervisor',
            'source': 'Rascunho',
        },
    ]
    return {
        'automation_rules': rules,
        'automation_fields': ['Categoria', 'Setor', 'Prioridade', 'Severidade do alerta RMM', 'Tempo sem resposta'],
        'automation_operators': ['igual a', 'diferente de', 'maior que', 'contem'],
        'automation_actions': [
            'Atribuir a tecnico/equipe',
            'Mudar prioridade',
            'Notificar usuario/supervisor',
            'Criar chamado automaticamente',
        ],
        'alert_mapping': alert_mapping_summary(),
    }
