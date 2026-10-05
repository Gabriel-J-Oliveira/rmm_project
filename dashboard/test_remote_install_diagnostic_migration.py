from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class InstallDiagnosticMigrationTests(TransactionTestCase):
    def test_additive_diagnostics_preserves_legacy_facts(self):
        before = [('dashboard', '0002_remoteinstalljob')]
        after = [('dashboard', '0003_remoteinstalljob_diagnostics')]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        try:
            apps = executor.loader.project_state(before).apps
            User = apps.get_model('auth', 'User')
            user = User.objects.create(username='migration-synthetic')
            Job = apps.get_model('dashboard', 'RemoteInstallJob')
            job = Job.objects.create(target_hostname='synthetic', target_fqdn='synthetic.example.test',
                target_ad_dn='CN=synthetic,DC=example,DC=test', requested_by_id=user.pk,
                active_slot='global', status='OUTCOME_UNKNOWN', stage='OUTCOME_UNKNOWN',
                error_code='INSTALLER_FAILED')
            facts = Job.objects.values().get(pk=job.pk)
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            NewJob = executor.loader.project_state(after).apps.get_model('dashboard', 'RemoteInstallJob')
            migrated = NewJob.objects.values().get(pk=job.pk)
            self.assertEqual(migrated.pop('diagnostics'), {})
            self.assertEqual(migrated, facts)
        finally:
            MigrationExecutor(connection).migrate(after)
