import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Case, F, IntegerField, Value, When
from django.utils import timezone

from agents.audit import create_audit_event as create_global_audit_event

from ..models import NotificationOutbox, TicketAuditEvent


logger = logging.getLogger(__name__)
SMTP_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'


def _address_list(value):
    if not value:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(',') if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _valid_email(value):
    try:
        validate_email(value)
        return True
    except ValidationError:
        return False


def smtp_configuration_status():
    if getattr(settings, 'EMAIL_USE_TLS', False) and getattr(settings, 'EMAIL_USE_SSL', False):
        return {
            'code': 'conflict',
            'label': 'TLS/SSL conflitante',
            'detail': 'Ative apenas EMAIL_USE_TLS ou EMAIL_USE_SSL.',
            'configured': False,
        }

    missing = []
    if getattr(settings, 'EMAIL_BACKEND', '') != SMTP_BACKEND:
        missing.append('backend SMTP')
    if not getattr(settings, 'EMAIL_HOST', ''):
        missing.append('servidor')
    if not getattr(settings, 'EMAIL_PORT', None):
        missing.append('porta')
    if not getattr(settings, 'EMAIL_HOST_USER', ''):
        missing.append('usuario')
    if not getattr(settings, 'EMAIL_HOST_PASSWORD', ''):
        missing.append('credencial')
    if not getattr(settings, 'DEFAULT_FROM_EMAIL', ''):
        missing.append('remetente')
    if missing:
        return {
            'code': 'incomplete',
            'label': 'SMTP incompleto',
            'detail': f"Configuracao ausente: {', '.join(missing)}.",
            'configured': False,
        }
    return {
        'code': 'configured',
        'label': 'SMTP configurado',
        'detail': f"{settings.EMAIL_HOST}:{settings.EMAIL_PORT} com {'TLS' if settings.EMAIL_USE_TLS else 'SSL' if settings.EMAIL_USE_SSL else 'conexao sem criptografia explicita'}.",
        'configured': True,
    }


def summarize_email_error(exc):
    message = f'{exc.__class__.__name__}: {str(exc)}'
    password = str(getattr(settings, 'EMAIL_HOST_PASSWORD', '') or '')
    if password:
        message = message.replace(password, '[redacted]')
    return ' '.join(message.split())[:4000]


def _actor_name(actor):
    if not actor:
        return 'Night Owl'
    if isinstance(actor, str):
        return actor
    if getattr(actor, 'is_authenticated', False):
        return actor.get_full_name() or actor.get_username()
    return str(actor)


def _audit_email(item, event_type, action, actor='Night Owl', error=''):
    metadata = {
        'email_id': str(item.pk),
        'source_app': item.source_app,
        'event_type': item.event_type,
        'recipient_email': item.recipient_email,
        'status': item.status,
        'attempts': item.attempts,
        'template': item.template.name if item.template else '',
    }
    if error:
        metadata['error'] = error[:500]
    if item.ticket_id:
        try:
            TicketAuditEvent.objects.create(
                ticket=item.ticket,
                actor=_actor_name(actor),
                event_type=event_type,
                action=action,
                field_name='email',
                new_value=item.subject,
                metadata=metadata,
            )
        except Exception:
            logger.exception('Failed to audit email event on ticket: email=%s event=%s', item.pk, event_type)
    create_global_audit_event(
        event_type=f'email.{event_type.removeprefix("email_")}',
        title=action,
        description=f'{item.event_type}: {item.subject}',
        severity='warning' if event_type == 'email_failed' else 'info',
        actor_type='system',
        actor_name=_actor_name(actor),
        endpoint=item.ticket.endpoint if item.ticket_id and item.ticket.endpoint_id else None,
        metadata=metadata,
    )


def queue_email(
    *,
    source_app,
    event_type,
    recipient_email,
    subject,
    body_text,
    recipient_name='',
    cc=None,
    bcc=None,
    body_html='',
    priority=NotificationOutbox.PRIORITY_NORMAL,
    max_attempts=3,
    source_model='',
    source_id='',
    ticket=None,
    template=None,
    metadata=None,
    actor='Night Owl',
):
    status = NotificationOutbox.STATUS_PENDING
    validation_errors = []
    if not _valid_email(recipient_email):
        validation_errors.append('Destinatario ausente ou e-mail invalido.')
    if not str(subject or '').strip():
        validation_errors.append('Assunto vazio.')
    if not str(body_text or '').strip() and not str(body_html or '').strip():
        validation_errors.append('Corpo do e-mail vazio.')
    if validation_errors:
        status = NotificationOutbox.STATUS_SKIPPED
    last_error = ' '.join(validation_errors)

    item = NotificationOutbox.objects.create(
        source_app=source_app,
        source_model=source_model,
        source_id=str(source_id or ''),
        ticket=ticket,
        template=template,
        event_type=event_type,
        channel=NotificationOutbox.CHANNEL_EMAIL,
        recipient_name=recipient_name,
        recipient_email=recipient_email or '',
        cc=_address_list(cc),
        bcc=_address_list(bcc),
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        status=status,
        priority=priority,
        max_attempts=max(1, int(max_attempts or 3)),
        last_error=last_error,
        metadata=metadata or {},
    )
    _audit_email(
        item,
        'email_queued',
        'E-mail adicionado a fila' if status == NotificationOutbox.STATUS_PENDING else 'E-mail ignorado pela fila',
        actor=actor,
        error=last_error,
    )
    logger.info('Email queued: id=%s source=%s event=%s status=%s', item.pk, source_app, event_type, status)
    return item


def _validate_smtp_settings():
    status = smtp_configuration_status()
    if not status['configured']:
        raise ImproperlyConfigured(status['detail'])


def send_email_outbox_item(email_id, actor='Night Owl'):
    validation_error = ''
    with transaction.atomic():
        item = NotificationOutbox.objects.select_for_update().get(pk=email_id)
        if item.status in {
            NotificationOutbox.STATUS_SENT,
            NotificationOutbox.STATUS_SKIPPED,
            NotificationOutbox.STATUS_CANCELLED,
            NotificationOutbox.STATUS_SENDING,
        }:
            return item
        if item.attempts >= item.max_attempts:
            return item
        validation_errors = []
        if not _valid_email(item.recipient_email):
            validation_errors.append('Destinatario ausente ou e-mail invalido.')
        if not item.subject.strip():
            validation_errors.append('Assunto vazio.')
        if not item.body_text.strip() and not item.body_html.strip():
            validation_errors.append('Corpo do e-mail vazio.')
        if validation_errors:
            validation_error = ' '.join(validation_errors)
            item.status = NotificationOutbox.STATUS_SKIPPED
            item.last_error = validation_error
            item.save(update_fields=['status', 'last_error', 'updated_at'])
        else:
            item.status = NotificationOutbox.STATUS_SENDING
            item.attempts += 1
            item.last_attempt_at = timezone.now()
            item.save(update_fields=['status', 'attempts', 'last_attempt_at', 'updated_at'])
    item = NotificationOutbox.objects.select_related('ticket', 'ticket__endpoint', 'template').get(pk=email_id)
    if validation_error:
        _audit_email(item, 'email_failed', 'E-mail ignorado por validacao', actor=actor, error=validation_error)
        return item

    try:
        _validate_smtp_settings()
        message = EmailMultiAlternatives(
            subject=item.subject,
            body=item.body_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[item.recipient_email],
            cc=item.cc,
            bcc=item.bcc,
            headers=(item.metadata or {}).get('headers') if isinstance(item.metadata, dict) else None,
        )
        if item.body_html:
            message.attach_alternative(item.body_html, 'text/html')
        sent_count = message.send(fail_silently=False)
        if sent_count != 1:
            raise RuntimeError('O backend de e-mail nao confirmou o envio.')
    except Exception as exc:
        error = summarize_email_error(exc)
        item.status = NotificationOutbox.STATUS_FAILED
        item.last_error = error
        item.save(update_fields=['status', 'last_error', 'updated_at'])
        _audit_email(item, 'email_failed', 'Falha no envio de e-mail', actor=actor, error=error)
        logger.warning('Email send failed: id=%s error=%s', item.pk, error)
        return item

    item.status = NotificationOutbox.STATUS_SENT
    item.sent_at = timezone.now()
    item.last_error = ''
    item.save(update_fields=['status', 'sent_at', 'last_error', 'updated_at'])
    _audit_email(item, 'email_sent', 'E-mail enviado', actor=actor)
    logger.info('Email sent: id=%s', item.pk)
    return item


def process_pending_emails(limit=50, actor='Night Owl'):
    priority_order = Case(
        When(priority=NotificationOutbox.PRIORITY_HIGH, then=Value(0)),
        When(priority=NotificationOutbox.PRIORITY_NORMAL, then=Value(1)),
        default=Value(2),
        output_field=IntegerField(),
    )
    ids = list(
        NotificationOutbox.objects.filter(
            status=NotificationOutbox.STATUS_PENDING,
            attempts__lt=F('max_attempts'),
        )
        .order_by(priority_order, 'created_at')
        .values_list('pk', flat=True)[:max(1, int(limit or 50))]
    )
    results = {'processed': 0, 'sent': 0, 'failed': 0}
    for email_id in ids:
        item = send_email_outbox_item(email_id, actor=actor)
        results['processed'] += 1
        if item.status == NotificationOutbox.STATUS_SENT:
            results['sent'] += 1
        elif item.status == NotificationOutbox.STATUS_FAILED:
            results['failed'] += 1
    return results


def retry_failed_email(email_id, actor='Night Owl', reset_attempts=False):
    with transaction.atomic():
        item = NotificationOutbox.objects.select_for_update().get(pk=email_id)
        if item.status != NotificationOutbox.STATUS_FAILED:
            return item
        if item.attempts >= item.max_attempts and not reset_attempts:
            return item
        if reset_attempts:
            item.attempts = 0
        item.status = NotificationOutbox.STATUS_PENDING
        item.last_error = ''
        item.save(update_fields=['status', 'attempts', 'last_error', 'updated_at'])
    item = NotificationOutbox.objects.select_related('ticket', 'ticket__endpoint', 'template').get(pk=email_id)
    _audit_email(item, 'email_retried', 'E-mail preparado para nova tentativa', actor=actor)
    return item


def retry_all_failed(actor='Night Owl', send_now=True):
    ids = list(
        NotificationOutbox.objects.filter(
            status=NotificationOutbox.STATUS_FAILED,
            attempts__lt=F('max_attempts'),
        ).values_list('pk', flat=True)
    )
    result = {'retried': len(ids), 'sent': 0, 'failed': 0}
    for email_id in ids:
        retry_failed_email(email_id, actor=actor)
        if send_now:
            item = send_email_outbox_item(email_id, actor=actor)
            result['sent'] += item.status == NotificationOutbox.STATUS_SENT
            result['failed'] += item.status == NotificationOutbox.STATUS_FAILED
    return result


def cancel_email(email_id, actor='Night Owl'):
    item = NotificationOutbox.objects.select_related('ticket', 'ticket__endpoint', 'template').get(pk=email_id)
    if item.status not in {NotificationOutbox.STATUS_SENT, NotificationOutbox.STATUS_CANCELLED}:
        item.status = NotificationOutbox.STATUS_CANCELLED
        item.save(update_fields=['status', 'updated_at'])
        _audit_email(item, 'email_cancelled', 'E-mail cancelado', actor=actor)
    return item


def mark_email_pending(email_id, actor='Night Owl', reset_attempts=False):
    item = NotificationOutbox.objects.select_related('ticket', 'ticket__endpoint', 'template').get(pk=email_id)
    if item.status != NotificationOutbox.STATUS_SENT:
        item.status = NotificationOutbox.STATUS_PENDING
        item.last_error = ''
        if reset_attempts:
            item.attempts = 0
        item.save(update_fields=['status', 'last_error', 'attempts', 'updated_at'])
        _audit_email(item, 'email_retried', 'E-mail marcado como pendente', actor=actor)
    return item
