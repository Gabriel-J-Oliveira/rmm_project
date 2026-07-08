from django.core.management.base import BaseCommand

from tickets.models import DeskQueue, DeskSLA, DeskTemplate, Ticket, TicketCategory


class Command(BaseCommand):
    help = 'Seed idempotente das configuracoes reais do Desk para o Backend MVP 2.'

    def handle(self, *args, **options):
        self._normalize_legacy_names()
        queues = self._seed_queues()
        slas = self._seed_slas(queues)
        categories = self._seed_categories(queues, slas)
        self._seed_templates()
        self._backfill_tickets(categories, slas)
        self.stdout.write(self.style.SUCCESS('Backend MVP 2 seed aplicado com sucesso.'))

    def _normalize_legacy_names(self):
        aliases = [
            (DeskQueue, 'Seguranca', ['Seguranca', 'Seguran\u00e7a', 'Seguran\u00c3\u00a7a']),
            (DeskQueue, 'CAB / Mudancas', ['CAB / Mudancas', 'CAB / Mudan\u00e7as', 'CAB / Mudan\u00c3\u00a7as']),
            (DeskSLA, 'Critica', ['Critica', 'Cr\u00edtica', 'Cr\u00c3\u00adtica']),
            (TicketCategory, 'Seguranca', ['Seguranca', 'Seguran\u00e7a', 'Seguran\u00c3\u00a7a']),
            (TicketCategory, 'GMUD / Mudanca', ['GMUD / Mudanca', 'GMUD / Mudan\u00e7a', 'GMUD / Mudan\u00c3\u00a7a']),
        ]
        for model, canonical, names in aliases:
            if not model.objects.filter(name=canonical).exists():
                model.objects.filter(name__in=[name for name in names if name != canonical]).update(name=canonical)

    def _seed_queues(self):
        specs = [
            ('N1 - Atendimento', 'Triagem, atendimento inicial e solicitacoes comuns.', 'Gabriel', ['Gabriel', 'Tecnico N1', 'Renan', 'Ana'], 'Comercial', 80, True, True, False),
            ('N2 - Infraestrutura', 'Incidentes de infraestrutura, endpoints e alertas tecnicos.', 'Infraestrutura', ['Renan', 'Infraestrutura', 'Lucas'], 'Comercial', 50, True, True, False),
            ('Seguranca', 'Incidentes de seguranca, antivirus e acessos suspeitos.', 'Seguranca', ['Ana', 'Seguranca'], 'Comercial', 30, True, True, False),
            ('Sistemas', 'Sistemas internos, ERP e aplicacoes corporativas.', 'Sistemas', ['Lucas', 'Sistemas'], 'Comercial', 40, True, False, False),
            ('CAB / Mudancas', 'Comite de aprovacao de mudancas.', 'Coordenacao', ['Gabriel', 'Coordenacao', 'Infraestrutura', 'Seguranca', 'Sistemas'], 'Sob demanda', 20, False, False, True),
        ]
        queues = {}
        for name, description, responsible, members, hours, capacity, tickets, rmm, gmud in specs:
            queue, _ = DeskQueue.objects.update_or_create(
                name=name,
                defaults={
                    'description': description,
                    'responsible': responsible,
                    'members': members,
                    'business_hours': hours,
                    'capacity': capacity,
                    'receives_tickets': tickets,
                    'receives_rmm': rmm,
                    'receives_gmud': gmud,
                    'is_active': True,
                },
            )
            queues[name] = queue
        return queues

    def _seed_slas(self, queues):
        specs = [
            ('Baixa', Ticket.PRIORITY_LOW, 480, 4320, DeskSLA.CALENDAR_BUSINESS, True),
            ('Normal', Ticket.PRIORITY_NORMAL, 240, 1440, DeskSLA.CALENDAR_BUSINESS, True),
            ('Alta', Ticket.PRIORITY_HIGH, 60, 480, DeskSLA.CALENDAR_BUSINESS, True),
            ('Critica', Ticket.PRIORITY_CRITICAL, 15, 240, DeskSLA.CALENDAR_24X7, False),
        ]
        slas = {}
        for name, priority, first_response, resolution, calendar, pause in specs:
            sla, _ = DeskSLA.objects.update_or_create(
                name=name,
                defaults={
                    'description': f'Regra de SLA {name.lower()} para atendimento Night Owl Desk.',
                    'priority': priority,
                    'first_response_minutes': first_response,
                    'resolution_minutes': resolution,
                    'calendar_type': calendar,
                    'pause_on_waiting_requester': pause,
                    'pause_on_waiting_supplier': pause,
                    'pause_on_waiting_approval': False,
                    'is_active': True,
                },
            )
            sla.queues.set([queue for queue in queues.values() if queue.receives_tickets or queue.receives_rmm])
            slas[name] = sla
        return slas

    def _seed_categories(self, queues, slas):
        c = TicketCategory
        specs = [
            ('Acesso', 'Credenciais, permissoes, MFA, VPN e sistemas internos.', 'bi-key', 'blue', [c.TYPE_INCIDENT, c.TYPE_REQUEST], Ticket.PRIORITY_NORMAL, 'N1 - Atendimento', 'Normal', ['MFA', 'ERP', 'Permissoes']),
            ('VPN', 'Acesso remoto corporativo, cliente VPN, token e conectividade externa.', 'bi-lock', 'blue', [c.TYPE_INCIDENT], Ticket.PRIORITY_HIGH, 'N2 - Infraestrutura', 'Alta', ['FortiClient', 'Acesso externo', 'Token MFA']),
            ('Hardware', 'Notebooks, desktops, perifericos e componentes fisicos.', 'bi-pc-display', 'cyan', [c.TYPE_INCIDENT], Ticket.PRIORITY_HIGH, 'N2 - Infraestrutura', 'Alta', ['Notebook', 'Desktop', 'Periferico']),
            ('Software', 'Instalacoes, atualizacoes, falhas de aplicativo e licencas.', 'bi-window', 'purple', [c.TYPE_INCIDENT, c.TYPE_REQUEST], Ticket.PRIORITY_NORMAL, 'N1 - Atendimento', 'Normal', ['Instalacao', 'Licenca', 'Atualizacao']),
            ('Rede', 'Conectividade, VPN, Wi-Fi, DNS, firewall e links.', 'bi-hdd-network', 'blue', [c.TYPE_INCIDENT], Ticket.PRIORITY_HIGH, 'N2 - Infraestrutura', 'Alta', ['Wi-Fi', 'DNS', 'Link']),
            ('Seguranca', 'Antivirus, acessos suspeitos, risco e resposta a incidente.', 'bi-shield-check', 'red', [c.TYPE_INCIDENT, c.TYPE_RMM_ALERT], Ticket.PRIORITY_HIGH, 'Seguranca', 'Critica', ['Antivirus', 'Acesso suspeito', 'Risco']),
            ('RMM / Alerta', 'Eventos vindos do agente, monitoramento e telemetria.', 'bi-exclamation-triangle', 'amber', [c.TYPE_RMM_ALERT], Ticket.PRIORITY_HIGH, 'N2 - Infraestrutura', 'Alta', ['Endpoint', 'Inventario', 'Alerta']),
            ('E-mail', 'Envio, recebimento, caixas compartilhadas e sincronizacao.', 'bi-envelope', 'cyan', [c.TYPE_INCIDENT, c.TYPE_REQUEST], Ticket.PRIORITY_NORMAL, 'N1 - Atendimento', 'Normal', ['Conta', 'Caixa compartilhada', 'Sincronizacao']),
            ('Impressora', 'Fila de impressao, drivers, spooler e mapeamento.', 'bi-printer', 'gray', [c.TYPE_INCIDENT, c.TYPE_REQUEST], Ticket.PRIORITY_NORMAL, 'N1 - Atendimento', 'Normal', ['Driver', 'Fila', 'Mapeamento']),
            ('GMUD / Mudanca', 'Registros de mudanca, aprovacao, janela e rollback.', 'bi-diagram-3', 'purple', [c.TYPE_GMUD], Ticket.PRIORITY_NORMAL, 'CAB / Mudancas', None, ['Servidor', 'Firewall', 'Emergencial']),
        ]
        categories = {}
        for name, description, icon, color, allowed_types, priority, queue_name, sla_name, subcategories in specs:
            category, _ = TicketCategory.objects.update_or_create(
                name=name,
                defaults={
                    'description': description,
                    'icon': icon,
                    'color': color,
                    'allowed_types': allowed_types,
                    'default_priority': priority,
                    'default_queue': queues.get(queue_name),
                    'default_sla': slas.get(sla_name) if sla_name else None,
                    'subcategories': subcategories,
                    'is_active': True,
                },
            )
            categories[name] = category
        return categories

    def _seed_templates(self):
        specs = [
            ('Confirmacao de chamado criado', DeskTemplate.TYPE_AUTOMATIC_REPLY, DeskTemplate.APP_TICKET_CREATED, DeskTemplate.CHANNEL_AUTOMATIC, 'Chamado {{ticket_code}} recebido', 'Ola {{solicitante}},\n\nRecebemos sua solicitacao e ela esta aguardando triagem pela equipe.\n\nChamado: {{ticket_code}}\nTitulo: {{titulo}}\nCategoria: {{categoria}}\nStatus atual: {{status}}\nAberto em: {{data_abertura}}\n\nResumo:\n{{resumo}}\n\nAcompanhe pelo NightOwl Desk:\n{{link_acompanhamento}}'),
            ('Chamado assumido', DeskTemplate.TYPE_PUBLIC_REPLY, DeskTemplate.APP_COMPOSER_PUBLIC, DeskTemplate.CHANNEL_PUBLIC, 'Chamado {{ticket_code}} em atendimento', 'Ola {{solicitante}},\n\nSua solicitacao foi assumida pela equipe e esta em atendimento.\n\nChamado: {{ticket_code}}\nTitulo: {{titulo}}\nResponsavel: {{responsavel}}\nEquipe/Fila: {{fila}}\nStatus atual: {{status}}\n\nAcompanhe pelo NightOwl Desk:\n{{link_acompanhamento}}'),
            ('Aguardando solicitante', DeskTemplate.TYPE_AUTOMATIC_REPLY, DeskTemplate.APP_WAITING_REQUESTER, DeskTemplate.CHANNEL_AUTOMATIC, 'Precisamos da sua resposta no chamado {{ticket_code}}', 'Ola {{solicitante}},\n\nA equipe precisa de mais informacoes para continuar o atendimento.\n\nChamado: {{ticket_code}}\nTitulo: {{titulo}}\nStatus atual: {{status}}\n\nMensagem da equipe:\n{{mensagem}}\n\nAcesse o chamado e envie sua resposta:\n{{link_acompanhamento}}'),
            ('Chamado resolvido', DeskTemplate.TYPE_RESOLUTION, DeskTemplate.APP_RESOLVE_TICKET, DeskTemplate.CHANNEL_PUBLIC, 'Chamado {{ticket_code}} resolvido', 'Ola {{solicitante}},\n\nSeu chamado foi resolvido pela equipe. Confira abaixo a solucao aplicada.\n\nChamado: {{ticket_code}}\nTitulo: {{titulo}}\nResponsavel: {{responsavel}}\nResolvido em: {{data_resolucao}}\n\nSolucao aplicada:\n{{solucao}}\n\nCaso o problema continue ou a solucao nao atenda sua solicitacao, responda este e-mail ou acesse o portal para reabrir o chamado:\n{{link_acompanhamento}}'),
            ('Chamado reaberto por contestacao', DeskTemplate.TYPE_AUTOMATIC_REPLY, DeskTemplate.APP_TICKET_REOPENED, DeskTemplate.CHANNEL_AUTOMATIC, 'Chamado {{ticket_code}} reaberto', 'Ola {{solicitante}},\n\nO chamado foi reaberto e voltou para atendimento.\n\nChamado: {{ticket_code}}\nTitulo: {{titulo}}\nStatus atual: {{status}}\nMotivo: {{motivo}}\n\nAcompanhe pelo NightOwl Desk:\n{{link_acompanhamento}}'),
            ('Resposta publica do chamado', DeskTemplate.TYPE_PUBLIC_REPLY, DeskTemplate.APP_COMPOSER_PUBLIC, DeskTemplate.CHANNEL_PUBLIC, 'Nova resposta no chamado {{ticket_code}}', 'Ola {{solicitante}},\n\nA equipe enviou uma nova resposta publica no seu chamado.\n\nChamado: {{ticket_code}}\nTitulo: {{titulo}}\nStatus atual: {{status}}\nTecnico: {{tecnico}}\n\nMensagem:\n{{mensagem}}\n\nAcompanhe pelo NightOwl Desk:\n{{link_acompanhamento}}'),
            ('Comentario publico padrao', DeskTemplate.TYPE_PUBLIC_REPLY, DeskTemplate.APP_COMPOSER_PUBLIC, DeskTemplate.CHANNEL_PUBLIC, '', 'Ola {{solicitante}}, estamos tratando o chamado {{ticket_code}} na fila {{fila}}. Retornaremos com uma atualizacao.'),
            ('Comentario interno de triagem', DeskTemplate.TYPE_INTERNAL_COMMENT, DeskTemplate.APP_COMPOSER_INTERNAL, DeskTemplate.CHANNEL_INTERNAL, '', 'Triagem do chamado {{ticket_code}}:\n- Categoria: {{categoria}}\n- Prioridade: {{prioridade}}\n- Endpoint: {{endpoint}}\n- Tecnico: {{tecnico}}\n- Proxima acao:'),
            ('Escalacao padrao', DeskTemplate.TYPE_ESCALATION, DeskTemplate.APP_ESCALATE_TICKET, DeskTemplate.CHANNEL_INTERNAL, '', 'Escalar {{ticket_code}} - {{titulo}}.\nFila atual: {{fila}}\nCategoria: {{categoria}}\nEndpoint: {{endpoint}}\nMotivo tecnico:'),
        ]
        variables = [
            '{{ticket_code}}', '{{titulo}}', '{{solicitante}}', '{{tecnico}}',
            '{{categoria}}', '{{prioridade}}', '{{fila}}', '{{endpoint}}',
            '{{solucao}}', '{{data}}', '{{data_abertura}}', '{{data_resolucao}}',
            '{{status}}', '{{responsavel}}', '{{mensagem}}', '{{motivo}}',
            '{{resumo}}', '{{link_acompanhamento}}',
        ]
        legacy_names = {
            'Confirmacao de chamado criado': ['ConfirmaÃ§Ã£o de chamado criado', 'Confirmacao de chamado criado'],
            'Chamado reaberto por contestacao': ['Chamado reaberto por contestaÃ§Ã£o', 'Chamado reaberto por contestacao'],
            'Comentario publico padrao': ['ComentÃ¡rio pÃºblico padrÃ£o', 'Comentario publico padrao'],
            'Comentario interno de triagem': ['ComentÃ¡rio interno de triagem', 'Comentario interno de triagem'],
            'Escalacao padrao': ['EscalaÃ§Ã£o padrÃ£o', 'Escalacao padrao'],
        }
        for name, aliases in legacy_names.items():
            if not DeskTemplate.objects.filter(name=name).exists():
                DeskTemplate.objects.filter(name__in=[alias for alias in aliases if alias != name]).update(name=name)
        for name, template_type, application, channel, subject, content in specs:
            DeskTemplate.objects.update_or_create(
                name=name,
                defaults={
                    'description': f'Template essencial para {application}.',
                    'template_type': template_type,
                    'application': application,
                    'category': None,
                    'channel': channel,
                    'subject': subject,
                    'trigger': application if application.startswith('automacao_') else '',
                    'content': content,
                    'variables': variables,
                    'is_active': True,
                },
            )

    def _backfill_tickets(self, categories, slas):
        for ticket in Ticket.objects.select_related('category', 'sla').filter(sla__isnull=True):
            category = ticket.category or categories.get(ticket.category.name if ticket.category else '')
            ticket.sla = (category.default_sla if category else None) or next(
                (sla for sla in slas.values() if sla.priority == ticket.priority),
                slas.get('Normal'),
            )
            if category and not ticket.queue:
                ticket.queue = category.default_queue.name if category.default_queue else 'N1 - Atendimento'
            ticket.save(update_fields=['sla', 'due_at', 'queue', 'updated_at'])
