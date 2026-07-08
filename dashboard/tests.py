from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from dashboard.models import OperationalTask, TaskReminderLog
from dashboard.services.task_reminders import send_due_task_reminders


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='NightOwl <no-reply@example.com>',
    TASK_REMINDER_RECIPIENTS=['gabriel.oliveira@controlsul.com.br'],
)
class TaskReminderServiceTests(TestCase):
    def create_task(self, *, due_delta, status=OperationalTask.STATUS_OPEN):
        return OperationalTask.objects.create(
            title='Validar rotina operacional',
            description='Checar tarefa antes do prazo.',
            status=status,
            priority=OperationalTask.PRIORITY_HIGH,
            category=OperationalTask.CATEGORY_MAINTENANCE,
            responsible='Gabriel Oliveira',
            due_at=timezone.now() + due_delta,
            checklist=[{'title': 'Validar', 'done': True}, {'title': 'Registrar', 'done': False}],
        )

    def test_task_without_due_at_does_not_send(self):
        OperationalTask.objects.create(title='Sem prazo', status=OperationalTask.STATUS_OPEN)

        summary = send_due_task_reminders()

        self.assertEqual(summary['sent'], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_completed_task_does_not_send(self):
        self.create_task(due_delta=timedelta(days=7), status=OperationalTask.STATUS_DONE)

        summary = send_due_task_reminders()

        self.assertEqual(summary['sent'], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_seven_day_reminder_sends_once(self):
        task = self.create_task(due_delta=timedelta(days=7))

        summary = send_due_task_reminders()
        duplicate = send_due_task_reminders()

        self.assertEqual(summary['sent'], 1)
        self.assertEqual(duplicate['sent'], 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(TaskReminderLog.objects.filter(task=task, reminder_type='7d', status='sent').exists())

    def test_three_day_reminder_sends(self):
        task = self.create_task(due_delta=timedelta(days=3))

        summary = send_due_task_reminders()

        self.assertEqual(summary['sent'], 1)
        self.assertTrue(TaskReminderLog.objects.filter(task=task, reminder_type='3d', status='sent').exists())

    def test_eight_hour_reminder_sends(self):
        task = self.create_task(due_delta=timedelta(hours=8))

        summary = send_due_task_reminders()

        self.assertEqual(summary['sent'], 1)
        self.assertTrue(TaskReminderLog.objects.filter(task=task, reminder_type='8h', status='sent').exists())

    def test_failed_send_registers_failed_log(self):
        task = self.create_task(due_delta=timedelta(hours=8))

        with mock.patch('django.core.mail.EmailMultiAlternatives.send', side_effect=RuntimeError('SMTP indisponivel')):
            summary = send_due_task_reminders()

        self.assertEqual(summary['failed'], 1)
        log = TaskReminderLog.objects.get(task=task, reminder_type='8h')
        self.assertEqual(log.status, TaskReminderLog.STATUS_FAILED)
        self.assertIn('SMTP indisponivel', log.error_message)
