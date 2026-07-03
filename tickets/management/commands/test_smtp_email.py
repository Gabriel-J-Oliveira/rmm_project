from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

from tickets.services.email_renderer import render_base_email
from tickets.services.email_outbox import smtp_configuration_status, summarize_email_error


class Command(BaseCommand):
    help = 'Envia um e-mail simples para validar a configuracao SMTP real do Night Owl.'

    def add_arguments(self, parser):
        parser.add_argument('--to', required=True, dest='recipient')

    def handle(self, *args, **options):
        recipient = str(options['recipient'] or '').strip()
        try:
            validate_email(recipient)
        except ValidationError:
            raise CommandError('Destinatario de teste invalido.')

        smtp_status = smtp_configuration_status()
        if not smtp_status['configured']:
            raise CommandError(f"SMTP indisponivel: {smtp_status['detail']}")

        subject = 'Teste SMTP NightOwl'
        body_text = 'Envio de teste realizado pelo NightOwl.'
        body_html = render_base_email(
            email_title=subject,
            email_subtitle='Validacao do canal de envio do Night Owl.',
            email_badge='Teste SMTP',
            email_body=body_text,
            footer_text='Mensagem automatica de teste do Night Owl.',
        )
        message = EmailMultiAlternatives(
            subject=subject,
            body=body_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
        )
        message.attach_alternative(body_html, 'text/html')
        try:
            sent_count = message.send(fail_silently=False)
        except Exception as exc:
            raise CommandError(f'Falha no teste SMTP: {summarize_email_error(exc)}')
        if sent_count != 1:
            raise CommandError('O backend SMTP nao confirmou o envio do teste.')
        self.stdout.write(self.style.SUCCESS(f'E-mail de teste enviado com sucesso para {recipient}.'))
