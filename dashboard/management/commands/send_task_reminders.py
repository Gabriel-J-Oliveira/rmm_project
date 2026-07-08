from django.core.management.base import BaseCommand

from dashboard.services.task_reminders import send_due_task_reminders


class Command(BaseCommand):
    help = 'Send automatic reminder e-mails for operational tasks with due dates.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Evaluate reminders without sending e-mail or creating logs.',
        )
        parser.add_argument(
            '--window-minutes',
            type=int,
            default=60,
            help='Tolerance window in minutes after the target reminder time.',
        )

    def handle(self, *args, **options):
        summary = send_due_task_reminders(
            dry_run=options['dry_run'],
            window_minutes=max(options['window_minutes'], 1),
        )
        prefix = '[dry-run] ' if summary.get('dry_run') else ''
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Task reminders: sent={summary['sent']} "
                f"skipped={summary['skipped']} failed={summary['failed']}"
            )
        )
