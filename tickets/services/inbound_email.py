import email
import hashlib
import html
import imaplib
import logging
import re
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.policy import default
from email.utils import getaddresses, parsedate_to_datetime, parseaddr
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import InboundEmailMessage, Ticket, TicketAttachment
from .desk_mvp1 import create_audit_event
from .ticket_workflow import WorkflowError, requester_reply, transition_ticket


logger = logging.getLogger(__name__)

TICKET_SUBJECT_RE = re.compile(r'\[NightOwl\s+#(?P<number>\d+)\]', re.IGNORECASE)
HEADER_LINE_RE = re.compile(
    r'^(De|From|Enviado|Sent|Para|To|Assunto|Subject|Cc|Date):\s',
    re.IGNORECASE,
)
QUOTE_START_RE = re.compile(
    r'^(Em .+ escreveu:|On .+ wrote:|-----Original Message-----|_{5,}|-{5,})',
    re.IGNORECASE,
)
TAG_RE = re.compile(r'<[^>]+>')
DANGEROUS_EXTENSIONS = {
    '.bat', '.cmd', '.com', '.cpl', '.exe', '.js', '.jse', '.msi', '.ps1',
    '.reg', '.scr', '.vbe', '.vbs', '.wsf',
}


@dataclass
class InboundAttachment:
    filename: str
    content_type: str
    payload: bytes
    inline: bool = False


@dataclass
class ParsedInboundEmail:
    message_id: str
    subject: str
    from_name: str
    from_email: str
    received_at: object
    ticket_number: int | None
    body: str
    attachments: list[InboundAttachment]
    headers: dict
    raw_hash: str
    auto_submitted: str


def inbound_configuration_status():
    missing = []
    if not getattr(settings, 'INBOUND_EMAIL_ENABLED', False):
        missing.append('INBOUND_EMAIL_ENABLED')
    if not getattr(settings, 'INBOUND_EMAIL_HOST', ''):
        missing.append('INBOUND_EMAIL_HOST')
    if not getattr(settings, 'INBOUND_EMAIL_USER', ''):
        missing.append('INBOUND_EMAIL_USER')
    if not getattr(settings, 'INBOUND_EMAIL_PASSWORD', ''):
        missing.append('INBOUND_EMAIL_PASSWORD')
    return {
        'configured': not missing,
        'missing': missing,
        'host': getattr(settings, 'INBOUND_EMAIL_HOST', ''),
        'folder': getattr(settings, 'INBOUND_EMAIL_FOLDER', 'INBOX'),
    }


def _decode(value):
    if not value:
        return ''
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value or '')


def _message_id(message, raw_bytes):
    value = _decode(message.get('Message-ID', '')).strip()
    if value:
        return value.strip('<>')
    return f'sha256:{hashlib.sha256(raw_bytes).hexdigest()}'


def _received_at(message):
    try:
        parsed = parsedate_to_datetime(message.get('Date'))
        if parsed and timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed
    except Exception:
        return timezone.now()


def _text_from_html(value):
    value = re.sub(r'(?is)<(script|style).*?>.*?</\1>', '', value or '')
    value = re.sub(r'(?i)<br\s*/?>', '\n', value)
    value = re.sub(r'(?i)</p\s*>', '\n\n', value)
    value = TAG_RE.sub('', value)
    return html.unescape(value)


def _part_text(part):
    payload = part.get_payload(decode=True)
    if payload is None:
        return ''
    charset = part.get_content_charset() or 'utf-8'
    return payload.decode(charset, errors='replace')


def _safe_filename(value):
    name = Path(_decode(value) or 'anexo').name.strip().replace('\x00', '')
    if not name:
        name = 'anexo'
    return name[:180]


def _is_real_attachment(part, filename):
    disposition = (part.get_content_disposition() or '').lower()
    if disposition == 'attachment':
        return True
    if disposition == 'inline':
        return False
    return bool(filename and not str(part.get('Content-ID') or '').strip())


def _extract_payloads(message):
    plain_parts = []
    html_parts = []
    attachments = []

    for part in message.walk() if message.is_multipart() else [message]:
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        filename = part.get_filename()
        if _is_real_attachment(part, filename):
            payload = part.get_payload(decode=True) or b''
            if payload:
                attachments.append(InboundAttachment(
                    filename=_safe_filename(filename),
                    content_type=content_type,
                    payload=payload,
                    inline=False,
                ))
            continue
        if content_type == 'text/plain':
            plain_parts.append(_part_text(part))
        elif content_type == 'text/html':
            html_parts.append(_text_from_html(_part_text(part)))

    body = '\n'.join(part for part in plain_parts if part.strip()).strip()
    if not body:
        body = '\n'.join(part for part in html_parts if part.strip()).strip()
    return body, attachments


def clean_email_body(value):
    lines = []
    previous_blank = False
    for raw_line in str(value or '').replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        line = raw_line.rstrip()
        stripped = line.strip()
        if QUOTE_START_RE.match(stripped):
            break
        if stripped == '--':
            break
        if stripped.startswith('>') or HEADER_LINE_RE.match(stripped):
            continue
        if stripped.lower().startswith(('enviado do meu ', 'sent from my ')):
            continue
        if not stripped:
            if previous_blank:
                continue
            previous_blank = True
            lines.append('')
            continue
        previous_blank = False
        lines.append(line)
    cleaned = '\n'.join(lines).strip()
    return cleaned[:12000]


def _extract_ticket_number(message, subject):
    header_ticket = _decode(message.get('X-NightOwl-Ticket-ID', '')).strip()
    if header_ticket:
        normalized = header_ticket.lstrip('#').strip()
        if normalized.isdigit():
            return int(normalized)
    match = TICKET_SUBJECT_RE.search(subject or '')
    if match:
        return int(match.group('number'))
    return None


def parse_inbound_email(raw_bytes):
    message = email.message_from_bytes(raw_bytes, policy=default)
    subject = _decode(message.get('Subject', ''))
    from_name, from_email = parseaddr(_decode(message.get('From', '')))
    body, attachments = _extract_payloads(message)
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
    return ParsedInboundEmail(
        message_id=_message_id(message, raw_bytes),
        subject=subject,
        from_name=from_name,
        from_email=from_email,
        received_at=_received_at(message),
        ticket_number=_extract_ticket_number(message, subject),
        body=clean_email_body(body),
        attachments=attachments,
        headers={
            'in_reply_to': _decode(message.get('In-Reply-To', '')),
            'references': _decode(message.get('References', ''))[:1000],
            'x_nightowl_ticket_id': _decode(message.get('X-NightOwl-Ticket-ID', '')),
            'x_nightowl_event': _decode(message.get('X-NightOwl-Event', '')),
        },
        raw_hash=raw_hash,
        auto_submitted=_decode(message.get('Auto-Submitted', '')),
    )


def _is_nightowl_sender(parsed):
    outbound = {
        parseaddr(str(getattr(settings, 'DEFAULT_FROM_EMAIL', '') or ''))[1].casefold(),
        str(getattr(settings, 'EMAIL_HOST_USER', '') or '').casefold(),
        str(getattr(settings, 'SERVER_EMAIL', '') or '').casefold(),
    } - {''}
    return parsed.from_email.casefold() in outbound


def _metadata(parsed, source='mailbox'):
    return {
        'source': source,
        'message_id': parsed.message_id,
        'raw_hash': parsed.raw_hash,
        'headers': parsed.headers,
        'auto_submitted': parsed.auto_submitted,
        'attachment_count': len(parsed.attachments),
    }


def _create_record(parsed, status, error='', ticket=None, comment=None, source='mailbox'):
    return InboundEmailMessage.objects.create(
        message_id=parsed.message_id,
        ticket=ticket,
        from_name=parsed.from_name,
        from_email=parsed.from_email,
        subject=parsed.subject[:255],
        received_at=parsed.received_at,
        processed_at=timezone.now(),
        status=status,
        error=error,
        raw_metadata=_metadata(parsed, source=source),
        created_comment=comment,
    )


def _audit(ticket, actor, event_type, action, metadata=None, new_value=''):
    if not ticket:
        return
    create_audit_event(
        ticket,
        actor=actor or 'Sistema',
        event_type=event_type,
        action=action,
        field_name='email_inbound',
        new_value=new_value,
        metadata=metadata or {},
    )


def _save_attachments(ticket, comment, parsed, actor, dry_run=False):
    created = []
    skipped = []
    for attachment in parsed.attachments:
        suffix = Path(attachment.filename).suffix.casefold()
        if suffix in DANGEROUS_EXTENSIONS:
            skipped.append(attachment.filename)
            continue
        if dry_run:
            created.append({'name': attachment.filename, 'size': len(attachment.payload), 'dry_run': True})
            continue
        item = TicketAttachment.objects.create(
            ticket=ticket,
            comment=comment,
            file=ContentFile(attachment.payload, name=attachment.filename),
            original_name=attachment.filename,
            content_type=attachment.content_type,
            size=len(attachment.payload),
            uploaded_by=actor,
            visibility=TicketAttachment.VISIBILITY_PUBLIC,
        )
        created.append(item)
    if skipped:
        _audit(
            ticket,
            'Sistema',
            'email_inbound_attachment_skipped',
            'Anexo de e-mail ignorado por extensao bloqueada',
            metadata={'filenames': skipped, 'message_id': parsed.message_id},
        )
    if created and not dry_run:
        _audit(
            ticket,
            actor,
            'attachment_created',
            'Anexo publico criado por e-mail inbound',
            new_value=', '.join(item.original_name for item in created),
            metadata={'origin': 'email_inbound', 'message_id': parsed.message_id, 'count': len(created)},
        )
    return created, skipped


def process_inbound_email_bytes(raw_bytes, *, dry_run=False, restrict_ticket=None, source='mailbox'):
    parsed = parse_inbound_email(raw_bytes)
    if InboundEmailMessage.objects.filter(message_id=parsed.message_id).exists():
        return {'status': 'deduped', 'message_id': parsed.message_id, 'ticket': parsed.ticket_number}

    ticket = None
    if parsed.ticket_number:
        ticket = Ticket.objects.select_related('category', 'sla', 'endpoint').filter(number=parsed.ticket_number).first()
    if restrict_ticket and parsed.ticket_number != int(restrict_ticket):
        error = f'E-mail referencia chamado #{parsed.ticket_number}, diferente do filtro #{restrict_ticket}.'
        if not dry_run:
            _create_record(parsed, InboundEmailMessage.STATUS_SKIPPED, error=error, source=source)
        return {'status': 'skipped', 'reason': error, 'message_id': parsed.message_id}
    if not parsed.ticket_number:
        error = 'Chamado nao identificado por header X-NightOwl-Ticket-ID ou assunto [NightOwl #ID].'
        if not dry_run:
            _create_record(parsed, InboundEmailMessage.STATUS_SKIPPED, error=error, source=source)
        return {'status': 'skipped', 'reason': error, 'message_id': parsed.message_id}
    if not ticket:
        error = f'Chamado #{parsed.ticket_number} nao encontrado.'
        if not dry_run:
            _create_record(parsed, InboundEmailMessage.STATUS_SKIPPED, error=error, source=source)
        return {'status': 'skipped', 'reason': error, 'message_id': parsed.message_id, 'ticket': parsed.ticket_number}
    if _is_nightowl_sender(parsed):
        error = 'E-mail ignorado para evitar loop: remetente e o proprio NightOwl.'
        if not dry_run:
            record = _create_record(parsed, InboundEmailMessage.STATUS_SKIPPED, error=error, ticket=ticket, source=source)
            _audit(ticket, 'Sistema', 'email_inbound_skipped', error, metadata={'inbound_id': str(record.pk), 'message_id': parsed.message_id})
        return {'status': 'skipped', 'reason': error, 'message_id': parsed.message_id, 'ticket': ticket.number}
    if parsed.auto_submitted and parsed.auto_submitted.casefold() != 'no':
        error = 'E-mail automatico ignorado por header Auto-Submitted.'
        if not dry_run:
            record = _create_record(parsed, InboundEmailMessage.STATUS_SKIPPED, error=error, ticket=ticket, source=source)
            _audit(ticket, 'Sistema', 'email_inbound_skipped', error, metadata={'inbound_id': str(record.pk), 'message_id': parsed.message_id})
        return {'status': 'skipped', 'reason': error, 'message_id': parsed.message_id, 'ticket': ticket.number}
    if ticket.status in {Ticket.STATUS_CLOSED, Ticket.STATUS_CANCELED}:
        error = 'Chamado encerrado/cancelado: resposta por e-mail mantida sem interacao automatica.'
        if not dry_run:
            record = _create_record(parsed, InboundEmailMessage.STATUS_SKIPPED, error=error, ticket=ticket, source=source)
            _audit(ticket, 'Sistema', 'email_inbound_skipped', error, metadata={'inbound_id': str(record.pk), 'message_id': parsed.message_id})
        return {'status': 'skipped', 'reason': error, 'message_id': parsed.message_id, 'ticket': ticket.number}
    if not parsed.body:
        error = 'E-mail sem corpo util apos limpeza.'
        if not dry_run:
            record = _create_record(parsed, InboundEmailMessage.STATUS_SKIPPED, error=error, ticket=ticket, source=source)
            _audit(ticket, 'Sistema', 'email_inbound_skipped', error, metadata={'inbound_id': str(record.pk), 'message_id': parsed.message_id})
        return {'status': 'skipped', 'reason': error, 'message_id': parsed.message_id, 'ticket': ticket.number}

    actor = parsed.from_name or parsed.from_email or 'Solicitante por e-mail'
    if dry_run:
        return {
            'status': 'would_process',
            'message_id': parsed.message_id,
            'ticket': ticket.number,
            'from_email': parsed.from_email,
            'body': parsed.body,
            'attachments': [attachment.filename for attachment in parsed.attachments],
        }

    try:
        with transaction.atomic():
            if ticket.status == Ticket.STATUS_RESOLVED:
                result = transition_ticket(
                    ticket,
                    Ticket.STATUS_IN_PROGRESS,
                    actor=actor,
                    reason=parsed.body,
                    public_message=f'Reabertura solicitada por e-mail:\n\n{parsed.body}',
                    source='email_inbound',
                    notify=False,
                )
                comment = result.public_comment
            else:
                comment = requester_reply(ticket, actor=actor, body=parsed.body, source='email_inbound')
            _save_attachments(ticket, comment, parsed, actor, dry_run=False)
            record = _create_record(parsed, InboundEmailMessage.STATUS_PROCESSED, ticket=ticket, comment=comment, source=source)
            _audit(
                ticket,
                actor,
                'email_inbound_processed',
                'E-mail inbound vinculado ao chamado',
                new_value=parsed.subject,
                metadata={
                    'inbound_id': str(record.pk),
                    'message_id': parsed.message_id,
                    'from_email': parsed.from_email,
                    'comment_id': str(comment.pk) if comment else '',
                    'status_after': ticket.status,
                    'source': source,
                },
            )
    except IntegrityError:
        return {'status': 'deduped', 'message_id': parsed.message_id, 'ticket': ticket.number}
    except WorkflowError as exc:
        record = _create_record(parsed, InboundEmailMessage.STATUS_FAILED, error=str(exc), ticket=ticket, source=source)
        _audit(ticket, 'Sistema', 'email_inbound_failed', 'Falha ao processar e-mail inbound', metadata={'inbound_id': str(record.pk), 'error': str(exc)})
        return {'status': 'failed', 'reason': str(exc), 'message_id': parsed.message_id, 'ticket': ticket.number}

    return {'status': 'processed', 'message_id': parsed.message_id, 'ticket': ticket.number, 'comment_id': str(comment.pk) if comment else ''}


def _connect_imap():
    timeout = int(getattr(settings, 'INBOUND_EMAIL_TIMEOUT', 30) or 30)
    host = getattr(settings, 'INBOUND_EMAIL_HOST', '')
    port = int(getattr(settings, 'INBOUND_EMAIL_PORT', 993) or 993)
    if getattr(settings, 'INBOUND_EMAIL_USE_SSL', True):
        client = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    else:
        client = imaplib.IMAP4(host, port, timeout=timeout)
    client.login(getattr(settings, 'INBOUND_EMAIL_USER', ''), getattr(settings, 'INBOUND_EMAIL_PASSWORD', ''))
    return client


def _ensure_mailbox(client, folder):
    if not folder:
        return
    status, _ = client.create(folder)
    if status not in {'OK', 'NO'}:
        logger.info('Could not ensure inbound folder %s: %s', folder, status)


def _move_message(client, uid, folder):
    if not folder:
        return
    _ensure_mailbox(client, folder)
    client.uid('COPY', uid, folder)
    client.uid('STORE', uid, '+FLAGS', r'(\Deleted)')


def process_inbound_mailbox(*, limit=20, dry_run=False, ticket=None, verbose=False):
    status = inbound_configuration_status()
    if not status['configured']:
        return {'processed': 0, 'skipped': 0, 'failed': 0, 'errors': [f"Configuracao inbound incompleta: {', '.join(status['missing'])}"]}

    client = _connect_imap()
    result = {'processed': 0, 'skipped': 0, 'failed': 0, 'errors': []}
    try:
        folder = getattr(settings, 'INBOUND_EMAIL_FOLDER', 'INBOX')
        client.select(folder)
        search_status, data = client.uid('SEARCH', None, 'UNSEEN')
        if search_status != 'OK':
            return {**result, 'errors': ['Falha ao buscar e-mails nao lidos.']}
        uids = (data[0] or b'').split()[:max(1, int(limit or 20))]
        for uid in uids:
            fetch_status, fetch_data = client.uid('FETCH', uid, '(RFC822)')
            if fetch_status != 'OK' or not fetch_data:
                result['failed'] += 1
                result['errors'].append(f'Falha ao baixar UID {uid.decode(errors="ignore")}.')
                continue
            raw = next((item[1] for item in fetch_data if isinstance(item, tuple) and item[1]), b'')
            processed = process_inbound_email_bytes(raw, dry_run=dry_run, restrict_ticket=ticket, source='imap')
            if verbose:
                logger.info('Inbound UID %s result: %s', uid, processed)
            if processed['status'] in {'processed', 'would_process'}:
                result['processed'] += 1
                if not dry_run:
                    _move_message(client, uid, getattr(settings, 'INBOUND_EMAIL_PROCESSED_FOLDER', ''))
            elif processed['status'] in {'skipped', 'deduped'}:
                result['skipped'] += 1
                if not dry_run:
                    _move_message(client, uid, getattr(settings, 'INBOUND_EMAIL_ERROR_FOLDER', ''))
            else:
                result['failed'] += 1
                if not dry_run:
                    _move_message(client, uid, getattr(settings, 'INBOUND_EMAIL_ERROR_FOLDER', ''))
        if not dry_run:
            client.expunge()
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return result


def process_inbound_email_file(path, *, dry_run=False, ticket=None):
    raw = Path(path).read_bytes()
    return process_inbound_email_bytes(raw, dry_run=dry_run, restrict_ticket=ticket, source='eml_file')
