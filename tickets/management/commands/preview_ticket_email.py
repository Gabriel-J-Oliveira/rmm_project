from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from tickets.models import NotificationOutbox, Ticket, TicketCategory
from tickets.services.automation_outbox import SUPPORTED_WORKFLOW_EVENTS, render_ticket_notification
from tickets.services.email_outbox import queue_email, send_email_outbox_item


class MockTicket(SimpleNamespace):
    PRIORITY_HIGH = Ticket.PRIORITY_HIGH
    PRIORITY_CRITICAL = Ticket.PRIORITY_CRITICAL

    def get_status_display(self):
        return dict(Ticket.STATUS_CHOICES).get(self.status, self.status)

    def get_priority_display(self):
        return dict(Ticket.PRIORITY_CHOICES).get(self.priority, self.priority)


class Command(BaseCommand):
    help = 'Previsualiza templates de e-mail do workflow de chamados sem enviar por padrao.'

    def add_arguments(self, parser):
        parser.add_argument('--ticket', help='Numero ou UUID do chamado real.')
        parser.add_argument('--event', choices=[*SUPPORTED_WORKFLOW_EVENTS, 'ticket_closed'], default='ticket_resolved')
        parser.add_argument('--all-events', action='store_true', help='Renderiza todos os eventos suportados.')
        parser.add_argument('--mock', action='store_true', help='Usa dados mockados, sem depender de chamado real.')
        parser.add_argument('--to', help='Destinatario para envio do preview.')
        parser.add_argument('--send', action='store_true', help='Enfileira e envia o preview pelo EmailOutbox.')

    def handle(self, *args, **options):
        events = list(SUPPORTED_WORKFLOW_EVENTS) if options['all_events'] else [options['event']]
        ticket = self._mock_ticket(options.get('to')) if options['mock'] else self._get_ticket(options.get('ticket'))
        if options['send'] and not options.get('to'):
            raise CommandError('Use --to quando --send estiver ativo.')

        sent = []
        for event_type in events:
            if event_type == 'ticket_closed':
                self.stdout.write(self.style.WARNING('ticket_closed permanece skipped: sem template ativo nesta fase.'))
                continue
            extra_context = self._extra_context(event_type)
            rendered = render_ticket_notification(ticket, event_type, user='Equipe NightOwl', extra_context=extra_context)
            if not rendered:
                raise CommandError(f'Template ativo nao encontrado para {event_type}.')
            recipient = options.get('to') or rendered['recipient_email']
            self.stdout.write(self.style.MIGRATE_HEADING(f'\n[{event_type}]'))
            self.stdout.write(f"Recipient: {recipient}")
            self.stdout.write(f"Action URL: {rendered['action_url'] or 'sem URL publica configurada'}")
            self.stdout.write(f"Subject: {rendered['subject']}")
            self.stdout.write('Body text:')
            self.stdout.write(rendered['body_text'])
            self.stdout.write(f"Body HTML chars: {len(rendered['body_html'])}")
            if options['send']:
                item = queue_email(
                    source_app=NotificationOutbox.SOURCE_DESK,
                    source_model='tickets.WorkflowEmailPreview',
                    source_id=f'preview-{event_type}',
                    ticket=None,
                    template=rendered['template'],
                    event_type=event_type,
                    recipient_name=ticket.requester_name,
                    recipient_email=recipient,
                    subject=f"[Preview] {rendered['subject']}",
                    body_text=rendered['body_text'],
                    body_html=rendered['body_html'],
                    priority=NotificationOutbox.PRIORITY_NORMAL,
                    metadata={
                        **rendered['metadata'],
                        'preview': True,
                        'mock': bool(options['mock']),
                        'source_ticket_number': getattr(ticket, 'number', ''),
                    },
                    actor='preview_ticket_email',
                )
                result = send_email_outbox_item(item.pk, actor='preview_ticket_email')
                sent.append((event_type, str(result.pk), result.status, result.last_error))
        if sent:
            for event_type, item_id, status, error in sent:
                suffix = f' | erro: {error}' if error else ''
                self.stdout.write(f'{event_type}: outbox={item_id} status={status}{suffix}')

    def _get_ticket(self, value):
        if not value:
            raise CommandError('Informe --ticket ou use --mock.')
        queryset = Ticket.objects.select_related('category', 'endpoint')
        if str(value).isdigit():
            return queryset.get(number=int(value))
        return queryset.get(pk=value)

    def _mock_ticket(self, recipient=''):
        category = TicketCategory(name='Acesso')
        now = timezone.now()
        return MockTicket(
            pk='preview-ticket',
            number=1042,
            title='Acesso ao ERP indisponivel',
            description='Usuario nao consegue acessar o ERP desde o inicio do expediente.',
            status=Ticket.STATUS_IN_PROGRESS,
            priority=Ticket.PRIORITY_HIGH,
            category=category,
            requester_name='Gabriel Oliveira',
            requester_email=recipient or 'gabriel.oliveira@controlsul.com.br',
            queue='N1 - Atendimento',
            assigned_to='Equipe NightOwl',
            endpoint=None,
            endpoint_name='FIN-012',
            created_at=now,
            resolved_at=now,
        )

    def _extra_context(self, event_type):
        if event_type == 'waiting_requester':
            return {'mensagem': 'Precisamos de um print da mensagem exibida e do horario aproximado da falha.'}
        if event_type == 'ticket_resolved':
            return {'solucao': 'A permissao do usuario foi ajustada no ERP e o acesso foi validado com sucesso.'}
        if event_type == 'ticket_reopened':
            return {'motivo': 'O erro voltou a ocorrer apos nova tentativa de acesso.'}
        if event_type == 'ticket_public_reply':
            return {'mensagem': 'Fizemos uma validacao inicial e precisamos que voce teste o acesso novamente.'}
        return {}
