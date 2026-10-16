from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class BatchIdentityMigrationTests(TransactionTestCase):
    def test_legacy_history_is_preserved_without_inventing_sid(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = [('dashboard', '0004_remoteinstallbatch_remoteinstallbatchitem')]
        after = [('dashboard', '0005_remoteinstallbatchitem_target_ad_sid')]
        try:
            executor.migrate(before)
            apps = executor.loader.project_state(before).apps
            user = apps.get_model('auth', 'User').objects.create(username='identity-migration')
            batch = apps.get_model('dashboard', 'RemoteInstallBatch').objects.create(
                requested_by_id=user.pk, status='COMPLETED_WITH_ERRORS', total_count=1)
            Item = apps.get_model('dashboard', 'RemoteInstallBatchItem')
            item = Item.objects.create(batch_id=batch.pk, position=1, target_hostname='old',
                target_fqdn='old.control.local', target_ad_dn='CN=old,DC=control,DC=local',
                status='REVIEW_REQUIRED', error_code='INSTALLER_RESULT_UNKNOWN')
            original = Item.objects.values().get(pk=item.pk)
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            Item = executor.loader.project_state(after).apps.get_model('dashboard', 'RemoteInstallBatchItem')
            updated = Item.objects.values().get(pk=item.pk)
            self.assertEqual(updated.pop('target_ad_sid'), '')
            self.assertEqual(updated, original)
        finally:
            MigrationExecutor(connection).migrate(latest)
