from datetime import timedelta
from io import StringIO

from django.core.management import call_command, get_commands
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from agents.audit import create_audit_event
from agents.models import AuditEvent, MaintenanceRun, MaintenanceTaskResult


TASKS = [
    {
        'name': 'mark_offline_agents',
        'dry_run': False,
        'options': {},
    },
    {
        'name': 'evaluate_alerts',
        'dry_run': True,
        'options': {},
    },
    {
        'name': 'detect_changes',
        'dry_run': True,
        'options': {},
    },
    {
        'name': 'expire_temporary_alerts',
        'dry_run': False,
        'options': {},
    },
    {
        'name': 'evaluate_software_policies',
        'dry_run': True,
        'options': {},
    },
]


class Command(BaseCommand):
    help = 'Run Night Owl operational maintenance tasks in a central audited flow.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--only', action='append', default=[], help='Run only the named task. Can be used multiple times.')
        parser.add_argument('--skip', action='append', default=[], help='Skip the named task. Can be used multiple times.')
        parser.add_argument('--verbose', action='store_true')
        parser.add_argument('--continue-on-error', action='store_true', default=True)
        parser.add_argument('--stop-on-error', action='store_true')
        parser.add_argument('--no-software-policies', action='store_true')
        parser.add_argument('--no-change-detection', action='store_true')
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--triggered-by', default='manual')

    def handle(self, *args, **options):
        now = timezone.now()
        force = options['force']
        stale_before = now - timedelta(minutes=30)

        running = MaintenanceRun.objects.filter(status=MaintenanceRun.STATUS_RUNNING).order_by('-started_at').first()
        if running and running.started_at >= stale_before and not force:
            message = 'Ja existe uma manutencao em execucao.'
            self.stderr.write(self.style.WARNING(message))
            raise CommandError(message)

        if running and running.started_at < stale_before:
            running.status = MaintenanceRun.STATUS_FAILED
            running.finished_at = now
            running.duration_seconds = (now - running.started_at).total_seconds()
            running.error = 'Execucao marcada como stale apos mais de 30 minutos em running.'
            running.save(update_fields=['status', 'finished_at', 'duration_seconds', 'error'])
            self.stdout.write(self.style.WARNING(f'Previous running maintenance marked as stale: {running.id}'))

        selected_tasks = self._select_tasks(options)
        run = MaintenanceRun.objects.create(
            started_at=now,
            status=MaintenanceRun.STATUS_RUNNING,
            triggered_by=options['triggered_by'],
            dry_run=options['dry_run'],
            total_tasks=len(selected_tasks),
        )

        stop_on_error = options['stop_on_error']
        had_critical_interrupt = False

        try:
            for task in selected_tasks:
                result_status = self._run_task(run, task, options)
                if result_status == MaintenanceTaskResult.STATUS_FAILED and stop_on_error:
                    had_critical_interrupt = True
                    break
        except Exception as exc:
            had_critical_interrupt = True
            run.error = str(exc)
            self.stderr.write(self.style.ERROR(str(exc)))
        finally:
            self._finish_run(run, had_critical_interrupt=had_critical_interrupt)

        self._print_summary(run)
        self._audit_run(run)

        if run.status == MaintenanceRun.STATUS_FAILED:
            raise CommandError('Maintenance failed.')

    def _select_tasks(self, options):
        only = set(options['only'] or [])
        skip = set(options['skip'] or [])
        if options['no_software_policies']:
            skip.add('evaluate_software_policies')
        if options['no_change_detection']:
            skip.add('detect_changes')

        tasks = []
        for task in TASKS:
            name = task['name']
            if only and name not in only:
                continue
            if name in skip:
                continue
            tasks.append(task)
        known_names = {task['name'] for task in TASKS}
        for missing_name in sorted(only - known_names - skip):
            tasks.append({
                'name': missing_name,
                'dry_run': False,
                'options': {},
            })
        return tasks

    def _run_task(self, run, task, options):
        task_name = task['name']
        started_at = timezone.now()
        result = MaintenanceTaskResult.objects.create(
            run=run,
            task_name=task_name,
            status=MaintenanceTaskResult.STATUS_SKIPPED,
            started_at=started_at,
            metadata={},
        )

        if task_name not in get_commands():
            return self._finish_task(
                result,
                MaintenanceTaskResult.STATUS_SKIPPED,
                output='',
                error='Comando nao encontrado no projeto atual.',
                metadata={'reason': 'missing_command'},
            )

        if options['dry_run'] and not task['dry_run']:
            return self._finish_task(
                result,
                MaintenanceTaskResult.STATUS_SKIPPED,
                output='',
                error='dry-run nao suportado por esta rotina',
                metadata={'reason': 'dry_run_not_supported'},
            )

        stdout = StringIO()
        stderr = StringIO()
        command_options = {'stdout': stdout, 'stderr': stderr}
        command_options.update(task.get('options') or {})
        if options['dry_run'] and task['dry_run']:
            command_options['dry_run'] = True
        if task_name == 'evaluate_software_policies' and options['verbose']:
            command_options['verbose'] = True

        try:
            call_command(task_name, **command_options)
        except TypeError as exc:
            if options['dry_run'] and 'dry_run' in command_options:
                command_options.pop('dry_run', None)
                return self._finish_task(
                    result,
                    MaintenanceTaskResult.STATUS_SKIPPED,
                    output=stdout.getvalue(),
                    error=f'dry-run nao aceito por esta rotina: {exc}',
                    metadata={'reason': 'dry_run_option_rejected'},
                )
            return self._finish_task(
                result,
                MaintenanceTaskResult.STATUS_FAILED,
                output=stdout.getvalue(),
                error=str(exc),
                metadata={'exception': exc.__class__.__name__},
            )
        except Exception as exc:
            return self._finish_task(
                result,
                MaintenanceTaskResult.STATUS_FAILED,
                output=stdout.getvalue(),
                error=(stderr.getvalue() + '\n' + str(exc)).strip(),
                metadata={'exception': exc.__class__.__name__},
            )

        return self._finish_task(
            result,
            MaintenanceTaskResult.STATUS_SUCCESS,
            output=stdout.getvalue(),
            error=stderr.getvalue(),
            metadata={'dry_run': bool(options['dry_run'])},
        )

    def _finish_task(self, result, status, output='', error='', metadata=None):
        finished_at = timezone.now()
        result.status = status
        result.finished_at = finished_at
        result.duration_seconds = (finished_at - result.started_at).total_seconds()
        result.output = output or ''
        result.error = error or ''
        result.metadata = metadata or {}
        result.save(update_fields=['status', 'finished_at', 'duration_seconds', 'output', 'error', 'metadata'])
        return status

    def _finish_run(self, run, had_critical_interrupt=False):
        results = list(run.task_results.all())
        successful = sum(1 for result in results if result.status == MaintenanceTaskResult.STATUS_SUCCESS)
        failed = sum(1 for result in results if result.status == MaintenanceTaskResult.STATUS_FAILED)
        skipped = sum(1 for result in results if result.status == MaintenanceTaskResult.STATUS_SKIPPED)

        if had_critical_interrupt or (results and failed == len(results)):
            status = MaintenanceRun.STATUS_FAILED
        elif failed:
            status = MaintenanceRun.STATUS_PARTIAL
        else:
            status = MaintenanceRun.STATUS_SUCCESS

        finished_at = timezone.now()
        run.finished_at = finished_at
        run.status = status
        run.total_tasks = len(results)
        run.successful_tasks = successful
        run.failed_tasks = failed
        run.skipped_tasks = skipped
        run.duration_seconds = (finished_at - run.started_at).total_seconds()
        run.summary = {
            'tasks': [
                {
                    'name': result.task_name,
                    'status': result.status,
                    'duration_seconds': result.duration_seconds,
                    'error': result.error,
                }
                for result in results
            ],
        }
        run.save(update_fields=[
            'finished_at',
            'status',
            'total_tasks',
            'successful_tasks',
            'failed_tasks',
            'skipped_tasks',
            'duration_seconds',
            'summary',
            'error',
        ])

    def _print_summary(self, run):
        run.refresh_from_db()
        self.stdout.write(self.style.SUCCESS('Maintenance complete.'))
        self.stdout.write(f'Status: {run.status}')
        self.stdout.write('Tasks:')
        for result in run.task_results.all():
            line = f'- {result.task_name}: {result.status}'
            if result.error:
                line += f' ({result.error.splitlines()[0]})'
            self.stdout.write(line)

    def _audit_run(self, run):
        if run.status == MaintenanceRun.STATUS_SUCCESS:
            severity = AuditEvent.SEVERITY_INFO
        elif run.status == MaintenanceRun.STATUS_PARTIAL:
            severity = AuditEvent.SEVERITY_WARNING
        else:
            severity = AuditEvent.SEVERITY_CRITICAL

        create_audit_event(
            event_type='maintenance.run_completed',
            title='Rotina de manutencao executada',
            description=f'Manutencao finalizada com status {run.status}.',
            severity=severity,
            actor_type=AuditEvent.ACTOR_SYSTEM,
            actor_name='run_maintenance_tasks',
            metadata={
                'run_id': str(run.id),
                'status': run.status,
                'dry_run': run.dry_run,
                'total_tasks': run.total_tasks,
                'successful_tasks': run.successful_tasks,
                'failed_tasks': run.failed_tasks,
                'skipped_tasks': run.skipped_tasks,
                'duration_seconds': run.duration_seconds,
            },
        )
