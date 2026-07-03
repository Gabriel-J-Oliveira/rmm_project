from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from tickets.models import Ticket, TicketAuditEvent, TicketCategory, TicketComment


class Command(BaseCommand):
    help = 'Cria dados persistidos e idempotentes para o Backend MVP 1 do NightOwl Desk.'

    @transaction.atomic
    def handle(self, *args, **options):
        category_rows = {
            'Acesso': ('Contas, permissoes, MFA e VPN.', '#22C55E'),
            'Hardware': ('Equipamentos, perifericos e componentes.', '#38BDF8'),
            'Software': ('Aplicativos, licencas e instalacoes.', '#34D399'),
            'Rede': ('Conectividade, firewall, DNS e links.', '#10B981'),
            'Seguranca': ('Antivirus, risco e resposta a incidente.', '#EF4444'),
            'RMM / Alerta': ('Eventos vindos do monitoramento.', '#86EFAC'),
            'E-mail': ('Envio, recebimento e sincronizacao.', '#38BDF8'),
            'Impressora': ('Filas, drivers e equipamentos de impressao.', '#F59E0B'),
            'Sistemas internos': ('ERP e aplicacoes corporativas.', '#A78BFA'),
        }
        categories = {}
        for name, (description, color) in category_rows.items():
            category, _ = TicketCategory.objects.get_or_create(
                name=name,
                defaults={'description': description, 'color': color, 'is_active': True},
            )
            categories[name] = category

        now = timezone.now()
        rows = [
            (1049, 'Sistema de vendas lento na filial', 'Equipe comercial relata lentidao durante emissao de pedidos.', 'Daniel Ribeiro', 'daniel@nalen.local', 'Comercial', 'Software', 'high', 'in_progress', 'Gabriel', 'N1 - Atendimento', 'manual', 'COM-014', 5),
            (1048, 'Socio sem acesso ao e-mail', 'Solicitante VIP sem acesso ao e-mail antes de reuniao estrategica.', 'Henrique Valente', 'henrique@nalen.local', 'Diretoria', 'Acesso', 'critical', 'new', '', 'N1 - Atendimento', 'manual', 'DIR-NOTE-011', 3),
            (1047, 'Notebook da diretoria sem VPN', 'VPN conecta, mas nao acessa recursos internos.', 'Claudia Ferraz', 'claudia@nalen.local', 'Diretoria', 'Rede', 'high', 'new', 'Renan', 'N2 - Infraestrutura', 'manual', 'DIR-NOTE-009', 2),
            (1046, 'ERP indisponivel no setor Juridico', 'Equipe juridica nao consegue autenticar no ERP.', 'Renata Lima', 'renata@nalen.local', 'Juridico', 'Acesso', 'high', 'waiting_user', 'Renan', 'N1 - Atendimento', 'email', '', 4),
            (1045, 'Instalar certificado digital', 'Instalar certificado A1 e validar portal fiscal.', 'Rafael Costa', 'rafael@nalen.local', 'Fiscal', 'Software', 'high', 'in_progress', 'Renan', 'N1 - Atendimento', 'portal', 'FIS-006', 3),
            (1044, 'Troca de mouse e teclado', 'Perifericos apresentam falha intermitente.', 'Paulo Mendes', 'paulo@nalen.local', 'Comercial', 'Hardware', 'low', 'new', 'Ana', 'N1 - Atendimento', 'manual', '', 1),
            (1043, 'Bitdefender ausente em FIN-012', 'RMM nao identificou protecao ativa no endpoint.', 'Mariana Souza', 'mariana@nalen.local', 'Financeiro', 'RMM / Alerta', 'critical', 'in_progress', 'Gabriel', 'N2 - Infraestrutura', 'rmm_alert', 'FIN-012', 2),
            (1042, 'Impressora do Financeiro offline', 'Fila de impressao indisponivel para o setor.', 'Mariana Souza', 'mariana@nalen.local', 'Financeiro', 'Impressora', 'normal', 'resolved', 'Gabriel', 'N1 - Atendimento', 'phone', 'FIN-012', 26),
            (1041, 'Disco do servidor acima de 90%', 'Monitoramento detectou baixo espaco livre no servidor.', 'Sistema', 'rmm@nalen.local', 'TI', 'RMM / Alerta', 'critical', 'new', '', 'N2 - Infraestrutura', 'monitoring', 'SRV-FILES-01', 1),
            (1040, 'Liberacao de acesso ao ERP', 'Novo colaborador precisa acessar o modulo financeiro.', 'Ana Ribeiro', 'ana@nalen.local', 'Financeiro', 'Acesso', 'normal', 'waiting_third_party', 'Gabriel', 'N1 - Atendimento', 'portal', '', 20),
        ]

        created = 0
        for number, title, description, requester, email, department, category_name, priority, status, assignee, queue, source, endpoint_name, age_hours in rows:
            ticket, was_created = Ticket.objects.get_or_create(
                number=number,
                defaults={
                    'title': title,
                    'description': description,
                    'requester_name': requester,
                    'requester_email': email,
                    'requester_department': department,
                    'requester_role': 'Socio' if department == 'Diretoria' and number == 1048 else '',
                    'requester_is_partner': number == 1048,
                    'category': categories[category_name],
                    'priority': priority,
                    'status': status,
                    'assigned_to': assignee,
                    'queue': queue,
                    'source': source,
                    'endpoint_name': endpoint_name,
                    'resolved_at': now - timedelta(hours=18) if status == Ticket.STATUS_RESOLVED else None,
                },
            )
            if was_created:
                Ticket.objects.filter(pk=ticket.pk).update(
                    created_at=now - timedelta(hours=age_hours),
                    updated_at=now - timedelta(minutes=max(3, age_hours * 7)),
                )
                ticket.refresh_from_db()
            created += int(was_created)

            comment_rows = []
            if number in {1048, 1043, 1042}:
                comment_rows.append(('Gabriel', 'Triagem iniciada e contexto validado.', TicketComment.VISIBILITY_INTERNAL))
            if number in {1048, 1046}:
                comment_rows.append((requester, 'Poderiam informar uma previsao de retorno?', TicketComment.VISIBILITY_PUBLIC))
            for author, body, visibility in comment_rows:
                TicketComment.objects.get_or_create(
                    ticket=ticket,
                    body=body,
                    defaults={'author_name': author, 'visibility': visibility},
                )

            TicketAuditEvent.objects.get_or_create(
                ticket=ticket,
                event_type='ticket_created',
                action='Criou chamado',
                new_value=f'#{ticket.number}',
                defaults={
                    'actor': requester or 'Sistema',
                    'field_name': 'ticket',
                    'metadata': {'source': 'seed_desk_mvp1', 'origin': 'Seed'},
                },
            )
            if assignee:
                TicketAuditEvent.objects.get_or_create(
                    ticket=ticket,
                    event_type='field_changed',
                    action='Alterou responsavel',
                    field_name='assigned_to',
                    new_value=assignee,
                    defaults={
                        'actor': 'Sistema',
                        'old_value': 'Sem responsavel',
                        'metadata': {'source': 'seed_desk_mvp1', 'origin': 'Seed'},
                    },
                )
            for comment in ticket.comments.all():
                TicketAuditEvent.objects.get_or_create(
                    ticket=ticket,
                    event_type='comment_created',
                    action='Criou comentario publico' if comment.visibility == 'public' else 'Criou comentario interno',
                    field_name='comments',
                    new_value=comment.body,
                    defaults={
                        'actor': comment.author_name,
                        'metadata': {'visibility': comment.visibility, 'source': 'seed_desk_mvp1', 'origin': 'Seed'},
                    },
                )

        self.stdout.write(self.style.SUCCESS(
            f'Backend MVP 1 pronto: {len(categories)} categorias, {len(rows)} chamados ({created} novos).'
        ))
