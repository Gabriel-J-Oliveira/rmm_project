from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from dashboard.models import OperationalTask, TaskReminderLog


REMINDER_DEFINITIONS = (
    ('7d', timedelta(days=7), '7 dias'),
    ('3d', timedelta(days=3), '3 dias'),
    ('8h', timedelta(hours=8), '8 horas'),
)


@dataclass
class TaskReminderSummary:
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    dry_run: bool = False

    def as_dict(self):
        return {
            'sent': self.sent,
            'skipped': self.skipped,
            'failed': self.failed,
            'dry_run': self.dry_run,
        }


def get_task_reminder_recipients(task=None):
    configured = getattr(settings, 'TASK_REMINDER_RECIPIENTS', None)
    if configured:
        if isinstance(configured, str):
            return [email.strip() for email in configured.split(',') if email.strip()]
        return [email for email in configured if email]

    # TODO: buscar usuarios do grupo de tecnicos quando o RBAC/AD estiver consolidado.
    return ['gabriel.oliveira@controlsul.com.br']


def task_reminder_url(task):
    public_url = str(getattr(settings, 'NIGHTOWL_PUBLIC_URL', '') or '').strip().rstrip('/')
    if not public_url:
        return ''
    return f"{public_url}{reverse('jobs-list')}"


def reminder_subject(task, reminder_type):
    labels = {
        '7d': '7 dias',
        '3d': '3 dias',
        '8h': '8 horas',
    }
    return f"[NightOwl] Tarefa vence em {labels.get(reminder_type, reminder_type)}: {task.title}"


def reminder_context(task, reminder_type, remaining_label):
    done, total = task.checklist_progress()
    return {
        'task': task,
        'reminder_type': reminder_type,
        'remaining_label': remaining_label,
        'due_at': timezone.localtime(task.due_at) if task.due_at else None,
        'checklist_done': done,
        'checklist_total': total,
        'checklist_label': f'{done}/{total}' if total else 'Sem checklist',
        'linked_ticket_label': task.linked_ticket_ref or (f'#{task.linked_ticket.number}' if task.linked_ticket else ''),
        'linked_endpoint_label': task.linked_endpoint_name or (str(task.linked_endpoint) if task.linked_endpoint else ''),
        'task_url': task_reminder_url(task),
    }


def render_task_reminder(task, reminder_type, remaining_label):
    context = reminder_context(task, reminder_type, remaining_label)
    subject = reminder_subject(task, reminder_type)
    body_text = render_to_string('emails/task_reminder.txt', context).strip()
    body_html = render_to_string('emails/task_reminder.html', {**context, 'subject': subject}).strip()
    return subject, body_text, body_html


def is_reminder_due(task, reminder_delta, *, now=None, window_minutes=60, valid_until_delta=None):
    now = now or timezone.now()
    if not task.due_at or task.due_at <= now:
        return False

    target_at = task.due_at - reminder_delta
    valid_until = task.due_at - valid_until_delta if valid_until_delta else task.due_at
    if now >= valid_until:
        return False

    window_end = target_at + timedelta(minutes=window_minutes)
    if target_at <= now <= window_end:
        return True

    # Se o agendador rodar atrasado, ainda envia enquanto o vencimento nao chegou.
    return target_at < now < valid_until


def active_task_queryset():
    return (
        OperationalTask.objects
        .filter(due_at__isnull=False, status__in=OperationalTask.ACTIVE_STATUSES)
        .select_related('linked_ticket', 'linked_endpoint')
        .order_by('due_at')
    )


def _log_existing(task, reminder_type, recipient):
    return TaskReminderLog.objects.filter(
        task=task,
        reminder_type=reminder_type,
        sent_to=recipient,
    ).first()


def _record_task_timeline(task, text, event_type):
    try:
        task.add_timeline_event(text, event_type=event_type)
    except Exception:
        # Timeline nao deve quebrar o envio de lembrete.
        pass


def send_single_task_reminder(task, reminder_type, remaining_label, recipient, *, dry_run=False):
    existing = _log_existing(task, reminder_type, recipient)
    if existing:
        return 'skipped'

    if dry_run:
        return 'sent'

    try:
        subject, body_text, body_html = render_task_reminder(task, reminder_type, remaining_label)
        message = EmailMultiAlternatives(
            subject=subject,
            body=body_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
        )
        message.attach_alternative(body_html, 'text/html')
        message.send()
    except Exception as exc:
        error = str(exc)[:1000]
        TaskReminderLog.objects.create(
            task=task,
            reminder_type=reminder_type,
            sent_to=recipient,
            status=TaskReminderLog.STATUS_FAILED,
            error_message=error,
        )
        _record_task_timeline(
            task,
            f'Reminder {reminder_type} falhou para {recipient}: {error}',
            'reminder.failed',
        )
        return 'failed'

    sent_at = timezone.now()
    TaskReminderLog.objects.create(
        task=task,
        reminder_type=reminder_type,
        sent_to=recipient,
        sent_at=sent_at,
        status=TaskReminderLog.STATUS_SENT,
    )
    _record_task_timeline(
        task,
        f'Reminder {reminder_type} enviado para {recipient}',
        'reminder.sent',
    )
    return 'sent'


def send_due_task_reminders(*, dry_run=False, window_minutes=60, now=None):
    now = now or timezone.now()
    summary = TaskReminderSummary(dry_run=dry_run)
    tasks = active_task_queryset()

    for task in tasks:
        sent_any_for_task = False
        for index, (reminder_type, reminder_delta, remaining_label) in enumerate(REMINDER_DEFINITIONS):
            next_definition = REMINDER_DEFINITIONS[index + 1] if index + 1 < len(REMINDER_DEFINITIONS) else None
            valid_until_delta = next_definition[1] if next_definition else None
            if not is_reminder_due(
                task,
                reminder_delta,
                now=now,
                window_minutes=window_minutes,
                valid_until_delta=valid_until_delta,
            ):
                summary.skipped += 1
                continue
            for recipient in get_task_reminder_recipients(task):
                result = send_single_task_reminder(
                    task,
                    reminder_type,
                    remaining_label,
                    recipient,
                    dry_run=dry_run,
                )
                if result == 'sent':
                    summary.sent += 1
                    sent_any_for_task = True
                elif result == 'failed':
                    summary.failed += 1
                else:
                    summary.skipped += 1
        if not sent_any_for_task and task.due_at:
            continue

    return summary.as_dict()
