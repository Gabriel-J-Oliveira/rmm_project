from django.core.management.base import BaseCommand

from tickets.models import DeskQueue, DeskSLA, DeskTemplate, Ticket, TicketCategory


class Command(BaseCommand):
    help = 'Seed idempotente das configuracoes reais do Desk para o Backend MVP 2.'

    def handle(self, *args, **options):
        queues = self._seed_queues()
        slas = self._seed_slas(queues)
        categories = self._seed_categories(queues, slas)
        self._seed_templates(categories)
        self._backfill_tickets(categories, slas)
        self.stdout.write(self.style.SUCCESS('Backend MVP 2 seed aplicado com sucesso.'))

    def _seed_queues(self):
        specs = [
            ('N1 - Atendimento', 'Triagem, atendimento inicial e solicitacoes comuns.', 'Gabriel', ['Gabriel', 'Tecnico N1', 'Renan', 'Ana'], 'Comercial', 80, True, True, False),
            ('N2 - Infraestrutura', 'Incidentes de infraestrutura, endpoints e alertas tecnicos.', 'Infraestrutura', ['Renan', 'Infraestrutura', 'Lucas'], 'Comercial', 50, True, True, False),
            ('Segurança', 'Incidentes de seguranca, antivirus e acessos suspeitos.', 'Segurança', ['Ana', 'Seguranca'], 'Comercial', 30, True, True, False),
            ('Sistemas', 'Sistemas internos, ERP e aplicacoes corporativas.', 'Sistemas', ['Lucas', 'Sistemas'], 'Comercial', 40, True, False, False),
            ('CAB / Mudanças', 'Comite de aprovacao de mudancas.', 'Coordenacao', ['Gabriel', 'Coordenacao', 'Infraestrutura', 'Seguranca', 'Sistemas'], 'Sob demanda', 20, False, False, True),
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
            ('Crítica', Ticket.PRIORITY_CRITICAL, 15, 240, DeskSLA.CALENDAR_24X7, False),
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
            ('Segurança', 'Antivirus, acessos suspeitos, risco e resposta a incidente.', 'bi-shield-check', 'red', [c.TYPE_INCIDENT, c.TYPE_RMM_ALERT], Ticket.PRIORITY_HIGH, 'Segurança', 'Crítica', ['Antivirus', 'Acesso suspeito', 'Risco']),
            ('RMM / Alerta', 'Eventos vindos do agente, monitoramento e telemetria.', 'bi-exclamation-triangle', 'amber', [c.TYPE_RMM_ALERT], Ticket.PRIORITY_HIGH, 'N2 - Infraestrutura', 'Alta', ['Endpoint', 'Inventario', 'Alerta']),
            ('E-mail', 'Envio, recebimento, caixas compartilhadas e sincronizacao.', 'bi-envelope', 'cyan', [c.TYPE_INCIDENT, c.TYPE_REQUEST], Ticket.PRIORITY_NORMAL, 'N1 - Atendimento', 'Normal', ['Conta', 'Caixa compartilhada', 'Sincronizacao']),
            ('Impressora', 'Fila de impressao, drivers, spooler e mapeamento.', 'bi-printer', 'gray', [c.TYPE_INCIDENT, c.TYPE_REQUEST], Ticket.PRIORITY_NORMAL, 'N1 - Atendimento', 'Normal', ['Driver', 'Fila', 'Mapeamento']),
            ('GMUD / Mudança', 'Registros de mudanca, aprovacao, janela e rollback.', 'bi-diagram-3', 'purple', [c.TYPE_GMUD], Ticket.PRIORITY_NORMAL, 'CAB / Mudanças', None, ['Servidor', 'Firewall', 'Emergencial']),
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

    def _seed_templates_legacy(self, categories):
        specs = [
            ('Confirmacao de chamado criado', DeskTemplate.TYPE_AUTOMATIC_REPLY, 'Automacao: chamado criado', 'Geral', DeskTemplate.CHANNEL_AUTOMATIC, 'Ola {{solicitante}}, registramos o chamado {{titulo}} e estamos analisando o caso.'),
            ('Pedido de mais informacoes', DeskTemplate.TYPE_PUBLIC_REPLY, 'Composer publico', 'Geral', DeskTemplate.CHANNEL_PUBLIC, 'Ola {{solicitante}}, precisamos de mais detalhes para avancar.\n\nPode nos enviar o erro exibido e o horario aproximado?'),
            ('Aguardando solicitante', DeskTemplate.TYPE_AUTOMATIC_REPLY, 'Automacao: aguardando solicitante', 'Geral', DeskTemplate.CHANNEL_AUTOMATIC, 'Ola {{solicitante}}, ficaremos aguardando seu retorno para continuar o atendimento.'),
            ('SLA proximo do vencimento', DeskTemplate.TYPE_AUTOMATIC_REPLY, 'Automacao: SLA proximo', 'Geral', DeskTemplate.CHANNEL_AUTOMATIC, 'Atencao: o chamado {{titulo}} esta proximo do SLA {{sla}}.'),
            ('Encerramento ao solicitante', DeskTemplate.TYPE_PUBLIC_REPLY, 'Resolver chamado', 'Geral', DeskTemplate.CHANNEL_PUBLIC, 'Ola {{solicitante}}, o chamado {{titulo}} foi resolvido. Caso o problema retorne, responda este atendimento.'),
            ('Comentario interno de triagem', DeskTemplate.TYPE_INTERNAL_COMMENT, 'Composer interno', 'Geral', DeskTemplate.CHANNEL_INTERNAL, 'Triagem inicial:\n- Solicitante: {{solicitante}}\n- Categoria: {{categoria}}\n- Endpoint: {{endpoint}}\n- Proxima acao:'),
            ('Resolucao de acesso', DeskTemplate.TYPE_RESOLUTION, 'Resolver chamado', 'Acesso', DeskTemplate.CHANNEL_PUBLIC, 'Causa raiz: ajuste de permissao/acesso.\nSolucao aplicada:\nValidacao com solicitante:'),
            ('GMUD enviada para aprovacao', DeskTemplate.TYPE_GMUD, 'GMUD: aprovacao', 'GMUD / Mudança', DeskTemplate.CHANNEL_APPROVAL, 'Mudanca {{titulo}} enviada para aprovacao.\nPlano: {{categoria}}\nResponsavel: {{tecnico}}'),
        ]
        variables = ['{{solicitante}}', '{{titulo}}', '{{categoria}}', '{{prioridade}}', '{{endpoint}}', '{{tecnico}}', '{{fila}}', '{{data}}', '{{sla}}']
        for name, template_type, application, category_name, channel, content in specs:
            category = categories.get(category_name)
            DeskTemplate.objects.update_or_create(
                name=name,
                defaults={
                    'description': f'Template MVP 2 para {application}.',
                    'template_type': template_type,
                    'application': application,
                    'category': category,
                    'channel': channel,
                    'trigger': application if application.startswith('Automacao') else '',
                    'content': content,
                    'variables': variables,
                    'is_active': True,
                },
            )

    def _seed_templates(self, categories):
        specs = [
            ('Confirmação de chamado criado', DeskTemplate.TYPE_AUTOMATIC_REPLY, DeskTemplate.APP_TICKET_CREATED, DeskTemplate.CHANNEL_AUTOMATIC, '{{ticket_code}} - chamado recebido', 'Olá {{solicitante}}, o chamado {{ticket_code}} - {{titulo}} foi criado em {{data}} e direcionado para {{fila}}.'),
            ('Chamado assumido', DeskTemplate.TYPE_PUBLIC_REPLY, DeskTemplate.APP_COMPOSER_PUBLIC, DeskTemplate.CHANNEL_PUBLIC, '{{ticket_code}} - atendimento iniciado', 'Ola {{solicitante}}, sou {{tecnico}} e assumi o atendimento do chamado {{ticket_code}} sobre {{titulo}}.'),
            ('Aguardando solicitante', DeskTemplate.TYPE_AUTOMATIC_REPLY, DeskTemplate.APP_WAITING_REQUESTER, DeskTemplate.CHANNEL_AUTOMATIC, '{{ticket_code}} - aguardando seu retorno', 'Ola {{solicitante}}, aguardamos seu retorno no chamado {{ticket_code}} para continuar o atendimento.'),
            ('Chamado resolvido', DeskTemplate.TYPE_RESOLUTION, DeskTemplate.APP_RESOLVE_TICKET, DeskTemplate.CHANNEL_PUBLIC, '{{ticket_code}} - chamado resolvido', 'Ola {{solicitante}}, o chamado {{ticket_code}} foi resolvido por {{tecnico}} em {{data}}.\n\nSolucao: {{solucao}}'),
            ('Chamado reaberto por contestação', DeskTemplate.TYPE_AUTOMATIC_REPLY, DeskTemplate.APP_TICKET_REOPENED, DeskTemplate.CHANNEL_AUTOMATIC, '{{ticket_code}} - chamado reaberto', 'O chamado {{ticket_code}} - {{titulo}} foi reaberto após contestação de {{solicitante}}.'),
            ('Comentário público padrão', DeskTemplate.TYPE_PUBLIC_REPLY, DeskTemplate.APP_COMPOSER_PUBLIC, DeskTemplate.CHANNEL_PUBLIC, '', 'Olá {{solicitante}}, estamos tratando o chamado {{ticket_code}} na fila {{fila}}. Retornaremos com uma atualização.'),
            ('Comentário interno de triagem', DeskTemplate.TYPE_INTERNAL_COMMENT, DeskTemplate.APP_COMPOSER_INTERNAL, DeskTemplate.CHANNEL_INTERNAL, '', 'Triagem do chamado {{ticket_code}}:\n- Categoria: {{categoria}}\n- Prioridade: {{prioridade}}\n- Endpoint: {{endpoint}}\n- Técnico: {{tecnico}}\n- Próxima ação:'),
            ('Escalação padrão', DeskTemplate.TYPE_ESCALATION, DeskTemplate.APP_ESCALATE_TICKET, DeskTemplate.CHANNEL_INTERNAL, '', 'Escalar {{ticket_code}} - {{titulo}}.\nFila atual: {{fila}}\nCategoria: {{categoria}}\nEndpoint: {{endpoint}}\nMotivo técnico:'),
        ]
        variables = [
            '{{ticket_code}}', '{{titulo}}', '{{solicitante}}', '{{tecnico}}',
            '{{categoria}}', '{{prioridade}}', '{{fila}}', '{{endpoint}}',
            '{{solucao}}', '{{data}}',
        ]
        legacy_names = {
            'Confirmação de chamado criado': 'Confirmacao de chamado criado',
            'Chamado reaberto por contestação': 'Chamado reaberto por contestacao',
            'Comentário público padrão': 'Comentario publico padrao',
            'Comentário interno de triagem': 'Comentario interno de triagem',
            'Escalação padrão': 'Escalacao padrao',
        }
        for name, template_type, application, channel, subject, content in specs:
            legacy_name = legacy_names.get(name)
            if legacy_name and not DeskTemplate.objects.filter(name=name).exists():
                DeskTemplate.objects.filter(name=legacy_name).update(name=name)
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
