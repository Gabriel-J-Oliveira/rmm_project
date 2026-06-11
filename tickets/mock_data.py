from dataclasses import dataclass, field


STATUS_LABELS = {
    'new': 'Novo',
    'in_progress': 'Em atendimento',
    'waiting_user': 'Aguardando usuario',
    'waiting_third_party': 'Aguardando terceiro',
    'resolved': 'Resolvido',
    'closed': 'Fechado',
    'canceled': 'Cancelado',
}

PRIORITY_LABELS = {
    'low': 'Baixa',
    'normal': 'Normal',
    'high': 'Alta',
    'critical': 'Critica',
}

CATEGORIES = [
    {'name': 'Acesso', 'description': 'Contas, permissoes e liberacoes.', 'color': '#22C55E', 'active': True},
    {'name': 'Hardware', 'description': 'Equipamentos, perifericos e pecas.', 'color': '#38BDF8', 'active': True},
    {'name': 'Software', 'description': 'Aplicativos, licencas e instalacoes.', 'color': '#34D399', 'active': True},
    {'name': 'Rede', 'description': 'Conectividade, Wi-Fi, VPN e links.', 'color': '#10B981', 'active': True},
    {'name': 'Impressora', 'description': 'Impressoras, filas e suprimentos.', 'color': '#F59E0B', 'active': True},
    {'name': 'Servidor', 'description': 'Servidores, servicos internos e backups.', 'color': '#38BDF8', 'active': True},
    {'name': 'Seguranca', 'description': 'Antivirus, alertas e acesso remoto.', 'color': '#EF4444', 'active': True},
    {'name': 'RMM / Alerta', 'description': 'Sinais vindos do monitoramento Night Owl.', 'color': '#86EFAC', 'active': True},
    {'name': 'Solicitacao', 'description': 'Demandas gerais da TI.', 'color': '#94A3B8', 'active': True},
]


@dataclass
class MockEndpoint:
    hostname: str
    status: str
    domain: str
    last_user: str
    last_heartbeat: str
    url: str = '/endpoints/'


@dataclass
class MockComment:
    author: str
    body: str
    when: str
    visibility: str = 'Interno'


@dataclass
class MockTicket:
    number: int
    title: str
    requester: str
    sector: str
    role: str
    partner: bool
    priority: str
    status: str
    category: str
    assigned_to: str
    endpoint: MockEndpoint | None
    opened_for: str
    updated_for: str
    description: str
    comments: list[MockComment] = field(default_factory=list)
    created_at: str = 'Hoje, 08:40'
    first_response_at: str = '--'
    assigned_at: str = '--'
    resolved_at: str = '--'

    @property
    def priority_label(self):
        return PRIORITY_LABELS.get(self.priority, self.priority)

    @property
    def status_label(self):
        return STATUS_LABELS.get(self.status, self.status)


MOCK_TICKETS = [
    MockTicket(
        1049,
        'Sistema de vendas lento na filial',
        'Daniel Ribeiro',
        'Comercial',
        'Supervisor comercial',
        False,
        'high',
        'in_progress',
        'Software',
        'Gabriel',
        MockEndpoint('COM-014', 'online', 'control.local', 'daniel.ribeiro', 'ha 2 min'),
        '5h 10min',
        '2h 40min',
        'Equipe comercial relata lentidao no sistema de vendas durante emissao de pedidos.',
    ),
    MockTicket(
        1048,
        'Socio sem acesso ao e-mail',
        'Henrique Valente',
        'Diretoria',
        'Socio',
        True,
        'critical',
        'new',
        'Acesso',
        '',
        MockEndpoint('DIR-NOTE-011', 'online', 'control.local', 'henrique.valente', 'ha 3 min'),
        '2h 44min',
        '2h 44min',
        'Solicitante VIP sem acesso ao e-mail corporativo antes de reuniao com cliente estrategico.',
        [MockComment('Henrique Valente', 'Preciso resolver antes das 14h.', 'ha 2h 40min')],
    ),
    MockTicket(
        1047,
        'Notebook da diretoria sem VPN',
        'Claudia Ferraz',
        'Diretoria',
        'Diretora administrativa',
        True,
        'high',
        'new',
        'Rede',
        'Renan',
        MockEndpoint('DIR-NOTE-009', 'online', 'control.local', 'claudia.ferraz', 'ha 8 min'),
        '1h 58min',
        '1h 58min',
        'VPN conecta mas nao acessa recursos internos. Viagem programada para hoje.',
    ),
    MockTicket(
        1046,
        'ERP indisponivel no setor Juridico',
        'Renata Lima',
        'Juridico',
        'Assistente juridico',
        False,
        'high',
        'waiting_user',
        'Acesso',
        'Renan',
        None,
        '4h 20min',
        '2h 15min',
        'Equipe juridica nao consegue autenticar no ERP para consultar contratos.',
    ),
    MockTicket(
        1045,
        'Instalar certificado digital',
        'Rafael Costa',
        'Fiscal',
        'Analista fiscal',
        False,
        'high',
        'in_progress',
        'Software',
        'Renan',
        MockEndpoint('FIS-006', 'online', 'control.local', 'rafael.costa', 'ha 5 min'),
        '3h 05min',
        '35 min',
        'Instalar certificado A1 e validar acesso ao portal fiscal.',
    ),
    MockTicket(
        1044,
        'Troca de mouse e teclado',
        'Paulo Mendes',
        'Comercial',
        'Executivo comercial',
        False,
        'low',
        'new',
        'Hardware',
        'Ana',
        None,
        '50 min',
        '50 min',
        'Solicitacao de troca de perifericos com falha intermitente.',
    ),
    MockTicket(
        1043,
        'Falha de impressao no Financeiro',
        'Mariana Souza',
        'Financeiro',
        'Analista financeiro',
        False,
        'normal',
        'in_progress',
        'Impressora',
        'Gabriel',
        MockEndpoint('FIN-012', 'online', 'control.local', 'mariana.souza', 'ha 4 min'),
        '1h 15min',
        '22 min',
        'Impressao de boletos falha na impressora compartilhada do setor.',
    ),
    MockTicket(
        1042,
        'Bitdefender ausente em FIN-012',
        'Mariana Souza',
        'Financeiro',
        'Analista financeiro',
        False,
        'critical',
        'new',
        'Seguranca',
        '',
        MockEndpoint('FIN-012', 'online', 'control.local', 'mariana.souza', 'ha 4 min'),
        '18 min',
        '4 min',
        'O endpoint FIN-012 apareceu sem protecao identificada no painel de monitoramento. Solicito verificacao antes do fechamento do mes.',
        [
            MockComment('Night Owl', 'Alerta de seguranca relacionado ao endpoint FIN-012.', 'ha 18 min'),
            MockComment('Gabriel', 'Vou validar inventario e console do antivirus.', 'ha 7 min'),
        ],
        first_response_at='ha 7 min',
    ),
    MockTicket(
        1041,
        'Impressora do Juridico nao imprime',
        'Renata Lima',
        'Juridico',
        'Assistente juridico',
        False,
        'normal',
        'waiting_user',
        'Impressora',
        'Gabriel',
        None,
        '42 min',
        '12 min',
        'Documentos ficam presos na fila da impressora compartilhada do Juridico.',
        [MockComment('Gabriel', 'Fila reiniciada. Aguardando confirmacao do setor.', 'ha 12 min')],
        assigned_at='ha 38 min',
        first_response_at='ha 36 min',
    ),
    MockTicket(
        1040,
        'Computador lento na recepcao',
        'Patricia Alves',
        'Recepcao',
        'Recepcionista',
        False,
        'high',
        'in_progress',
        'Hardware',
        'Lucas',
        MockEndpoint('REC-004', 'online', 'control.local', 'patricia.alves', 'ha 2 min'),
        '1h 10min',
        '9 min',
        'Maquina demora para abrir navegador e sistema de atendimento.',
    ),
    MockTicket(
        1039,
        'Solicitacao de acesso ao ERP',
        'Bruno Martins',
        'Compras',
        'Comprador',
        False,
        'normal',
        'new',
        'Acesso',
        '',
        None,
        '1h 35min',
        '1h 20min',
        'Liberar perfil de consulta de pedidos no ERP para novo colaborador.',
    ),
    MockTicket(
        1038,
        'Socio sem acesso ao e-mail',
        'Eduardo Campos',
        'Diretoria',
        'Socio',
        True,
        'high',
        'in_progress',
        'Acesso',
        'Gabriel',
        MockEndpoint('DIR-NOTE-002', 'online', 'control.local', 'eduardo.campos', 'ha 1 min'),
        '2h 5min',
        '3 min',
        'Conta de e-mail nao sincroniza no notebook da diretoria. Impacta comunicacao com cliente externo.',
        [
            MockComment('Eduardo Campos', 'Preciso acessar ainda pela manha.', 'ha 2h'),
            MockComment('Gabriel', 'Prioridade elevada por solicitante socio. Verificando credenciais e MFA.', 'ha 1h 50min'),
            MockComment('Gabriel', 'Perfil reconfigurado. Validando envio e recebimento.', 'ha 3 min'),
        ],
        first_response_at='ha 1h 50min',
        assigned_at='ha 1h 51min',
    ),
    MockTicket(
        1037,
        'Instalar software no notebook da diretoria',
        'Camila Rocha',
        'Diretoria',
        'Diretora comercial',
        True,
        'high',
        'waiting_third_party',
        'Software',
        'Ana',
        MockEndpoint('DIR-NOTE-005', 'offline', 'control.local', 'camila.rocha', 'ha 2h'),
        '3h 12min',
        '26 min',
        'Instalar ferramenta de apresentacao comercial no notebook antes de reuniao externa.',
    ),
    MockTicket(1036, 'VPN instavel para usuario remoto', 'Igor Nunes', 'Comercial', 'Executivo de contas', False, 'high', 'in_progress', 'Rede', 'Lucas', None, '4h', '40 min', 'Conexao VPN cai durante acesso ao CRM.'),
    MockTicket(1035, 'Atualizar pacote Office', 'Juliana Moraes', 'RH', 'Analista RH', False, 'normal', 'resolved', 'Software', 'Ana', MockEndpoint('RH-009', 'online', 'control.local', 'juliana.moraes', 'ha 6 min'), '1d', '2h', 'Atualizacao solicitada para compatibilidade de planilhas.', resolved_at='Hoje, 09:10'),
    MockTicket(1034, 'Wi-Fi fraco na sala de reuniao 2', 'Fernando Dias', 'Operacoes', 'Coordenador', False, 'normal', 'waiting_third_party', 'Rede', 'Lucas', None, '1d 3h', '3h', 'Sinal oscila durante reunioes com videoconferencia.'),
    MockTicket(1033, 'Criar usuario para novo colaborador', 'Lais Pereira', 'RH', 'Business partner', False, 'normal', 'waiting_third_party', 'Acesso', 'Gabriel', None, '2d', '1d', 'Criacao de login para admissao.', assigned_at='Ontem, 15:40'),
    MockTicket(1032, 'Servidor de arquivos com alerta de disco', 'Night Owl RMM', 'TI', 'Monitoramento', False, 'critical', 'new', 'RMM / Alerta', '', MockEndpoint('SRV-FILES-01', 'online', 'control.local', 'system', 'ha 1 min'), '11 min', '1 min', 'Alerta de disco critico detectado no volume D:.'),
    MockTicket(1031, 'Scanner do fiscal nao reconhece driver', 'Rafael Costa', 'Fiscal', 'Analista fiscal', False, 'normal', 'waiting_user', 'Hardware', 'Ana', None, '3d', '2d', 'Aguardando confirmacao do setor apos troca de equipamento.'),
]


def get_ticket(number):
    try:
        number = int(number)
    except (TypeError, ValueError):
        return None
    return next((ticket for ticket in MOCK_TICKETS if ticket.number == number), None)


def filter_tickets(params, tickets=None, assigned_to=None):
    items = list(tickets or MOCK_TICKETS)
    query = (params.get('q') or '').strip().lower()
    status = (params.get('status') or '').strip()
    priority = (params.get('priority') or '').strip()
    category = (params.get('category') or '').strip()
    owner = (params.get('assigned_to') or '').strip().lower()

    if assigned_to:
        items = [ticket for ticket in items if ticket.assigned_to.lower() == assigned_to.lower()]
    if query:
        items = [
            ticket for ticket in items
            if query in ticket.title.lower()
            or query in ticket.requester.lower()
            or query in ticket.sector.lower()
            or (ticket.endpoint and query in ticket.endpoint.hostname.lower())
        ]
    if status:
        items = [ticket for ticket in items if ticket.status == status]
    if priority:
        items = [ticket for ticket in items if ticket.priority == priority]
    if category:
        items = [ticket for ticket in items if ticket.category == category]
    if owner:
        items = [ticket for ticket in items if owner in ticket.assigned_to.lower()]
    if params.get('critical') == '1':
        items = [ticket for ticket in items if ticket.priority == 'critical']
    if params.get('unassigned') == '1':
        items = [ticket for ticket in items if not ticket.assigned_to]
    return items


def summary_for(tickets=None):
    items = list(tickets or MOCK_TICKETS)
    return {
        'new': len([ticket for ticket in items if ticket.status == 'new']),
        'in_progress': len([ticket for ticket in items if ticket.status == 'in_progress']),
        'waiting_user': len([ticket for ticket in items if ticket.status == 'waiting_user']),
        'critical': len([ticket for ticket in items if ticket.priority == 'critical' and ticket.status not in {'closed', 'canceled'}]),
        'resolved_today': 3,
        'avg_first_response': '18 min',
        'open': len([ticket for ticket in items if ticket.status not in {'resolved', 'closed', 'canceled'}]),
    }
